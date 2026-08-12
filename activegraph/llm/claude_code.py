"""`ClaudeCodeProvider` — Claude Agent SDK-backed `LLMProvider`.

CONTRACT v1.11 #1. Bills LLM calls against the caller's Claude
Max/Pro/Team/Enterprise **subscription** (via the `claude` CLI, driven
as an async subprocess by the `claude-agent-sdk` package, exactly
`0.2.135` with its bundled CLI `2.1.227` — see "Exact SDK
compatibility" below) instead of `ANTHROPIC_API_KEY` metered billing.
Install with `pip install "activegraph[claude-code]"`. The SDK is
resolved lazily, inside `_load_sdk_bindings()`, so the rest of
`activegraph.llm` (and `import activegraph.llm` itself) works without
the extra installed.

**Supported use.** Intended for a caller's own local/ordinary
subscription use. It must not be advertised or used to route
Free/Pro/Max credentials on behalf of third-party users, or as
authentication for a hosted multi-tenant product — those deployments
belong on an Anthropic API, Bedrock, Vertex, or Foundry credential
through an appropriate provider instead.

**Auth is best-effort, not proof.** The provider prefers subscription
authentication by rejecting the environment routes known to select
metered API/cloud credentials (`reject_metered_env_auth=True`, the
default). The `claude` CLI itself remains the authentication
authority — managed policy, a global `~/.claude.json`, and saved
credential state cannot be suppressed or identified conclusively by
the public Agent SDK. `model_usage[*]["provider"] == "firstParty"` is
evidence the request did not use a *named cloud backend*; it is *not*
proof of subscription billing. `LLMResponse.cost_usd` is standard
list-rate accounting, never an actual charge, credit deduction, or
remaining-credit meter.

**Capability-limited — read `LLMProviderCapabilities` before wiring
this up.** Unlike `AnthropicProvider`/`OpenAIProvider`, this provider
does not claim full `LLMProvider` parity:

  * `max_tokens`/`temperature`/`top_p` are accepted (Protocol-required
    keywords) but never forwarded — `ClaudeAgentOptions` has no
    output-length ceiling or sampling-control fields at all. Because of
    this, `Runtime` refuses to bind an `@llm_behavior(deterministic=True)`
    to this provider at all (capability-binding validation, before any
    subprocess or network call) — there is no `deterministic=` keyword
    on `complete()` to guard per-call; the locked `LLMProvider.complete()`
    signature never gains one.
  * A hard `budget={"max_cost_usd": ...}` is likewise rejected at
    binding time — this provider's `count_tokens()` is a local
    heuristic (`llm_capabilities.input_token_count == "estimate"`), not
    an official pre-call count, so the budget gate's promise can't
    hold.
  * `requires_generation_control_acknowledgement=True`: construct with
    `allow_unenforced_generation_controls=True` or `Runtime` refuses to
    bind this provider to any `@llm_behavior` at all.
  * Tool-call cardinality is 0-or-1 per `complete()`, never a batch —
    the SDK's `DeferredToolUse` is singular by construction.

See `docs/reference/llm-providers.md` for the full user-facing record.

**Isolation.** Every call constructs a fresh `ClaudeAgentOptions` and a
stateless `query()` — no session reuse (`continue_conversation=False`,
`resume=None`, `fork_session=False`, `session_id=None`), no persistence
(`enable_file_checkpointing=False`, `session_store=None`,
`extra_args={"no-session-persistence": None}`), no ambient settings
(`setting_sources=[]`; this does **not** suppress managed policy or
global configuration — a stated limitation, not a promise), no skills/
plugins/subagents (`skills=[]`, `plugins=[]`, `agents=None`), a private
empty temp `cwd` alive only through the call, and a hard-coded
`permission_mode="dontAsk"` (deny anything not pre-approved by the
allow-list, never prompt). `reject_metered_env_auth=True` additionally
rejects (terminal `llm.auth_error`) any of the documented higher-
precedence credential/routing env vars; nested-session/isolation env
vars are *always* rejected (terminal `llm.request_error`) regardless of
that flag. `CLAUDE_CODE_OAUTH_TOKEN` is explicitly never rejected —
official precedence defines it as subscription OAuth ahead of saved
`/login` credentials.

**Multi-turn tool continuation.** The SDK takes a single `prompt: str`,
not a `messages[]` array. Each live `complete()` call flattens the
entire `messages` history into one canonical JSON transcript
(`activegraph-transcript-json-v1`, `sort_keys=True` so cache-identical
`messages` always produce byte-identical prompts) sent as a single new
turn to a fresh, non-resumed `query()` call — the only mechanism
verified (via live SDK testing) to always produce exactly one clean
response for a real multi-turn conversation. This is lossy (the model
reads prose describing prior turns rather than native content blocks)
but deliberate.

**Exact SDK compatibility.** `_load_sdk_bindings()` checks
`importlib.metadata.version("claude-agent-sdk") == "0.2.135"` and
resolves+verifies the SDK's own bundled `claude` binary (never a
caller override, never a system-PATH fallback) before ever
constructing options. A missing/wrong-version SDK or missing bundled
binary is a terminal `llm.request_error` naming the install command.
Upgrading the pinned SDK version is a deliberate compatibility change,
gated by this provider's full fake and live test suites — not a silent
version-range widening.
"""

from __future__ import annotations

import functools
import json
import os
import platform
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

from activegraph.llm._claude_shared import (
    DEFAULT_MODEL as _DEFAULT_MODEL,
    DEFAULT_PRICING as _DEFAULT_PRICING,
    NATIVE_STRUCTURED_OUTPUT_PREFIXES as _NATIVE_STRUCTURED_OUTPUT_PREFIXES,
)
from activegraph.llm.errors import LLMBehaviorError
from activegraph.llm.parsing import parse_structured_response as _parse_structured
from activegraph.llm.provider import LLMProvider, LLMProviderCapabilities
from activegraph.llm.types import LLMMessage, LLMResponse, ToolCall
from activegraph.llm.wire import (
    build_tool_name_map,
    classify_provider_status,
    restore_tool_name,
    sanitize_tool_name,
)


_SUPPORTED_SDK_VERSION = "0.2.135"
_MCP_SERVER_KEY = "activegraph"
_TRANSCRIPT_FORMAT_VERSION = "activegraph-transcript-json-v1"
_TEARDOWN_GRACE_SECONDS = 25.0
_ESTIMATED_UTF8_BYTES_PER_TOKEN = 4

# Env vars that select metered API/cloud credentials, outranking
# subscription OAuth per the CLI's documented auth precedence. Rejected
# (terminal llm.auth_error) when reject_metered_env_auth=True (the
# default); left untouched when False. Re-confirm exact names against
# live docs at upgrade time.
_CREDENTIAL_ROUTING_VARS: tuple[str, ...] = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AWS_BASE_URL",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
    "ANTHROPIC_FOUNDRY_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "CLAUDE_CONFIG_DIR",
)

# Nested-session/behavior-override env vars — always rejected (terminal
# llm.request_error) regardless of reject_metered_env_auth, since an
# active one changes SDK/CLI behavior in ways this provider's isolation
# contract can't reconcile (MCP-no-prefix invalidates every qualified
# MCP name; forced checkpointing can override the disabled option;
# Simple mode changes SDK/OAuth/customization behavior).
_NESTED_SESSION_VARS: tuple[str, ...] = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_PID",
    "CLAUDE_AGENT_SDK_MCP_NO_PREFIX",
    "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING",
    "CLAUDE_CODE_SIMPLE",
)

# ResultMessage.subtype values that are deterministic local config/limit
# failures, not transient ones — classified as llm.request_error
# unconditionally.
_LOCAL_TERMINAL_LIMIT_SUBTYPES = frozenset(
    {"error_max_turns", "error_max_budget_usd", "error_permission_denied"}
)

# AssistantMessage.error pre-result signals, consulted only when the
# stream ends without ever yielding a terminal ResultMessage.
_ASSISTANT_ERROR_REASON: dict[str, str] = {
    "authentication_failed": "llm.auth_error",
    "billing_error": "llm.auth_error",
    "rate_limit": "llm.rate_limited",
    "invalid_request": "llm.request_error",
    "server_error": "llm.network_error",
    "unknown": "llm.network_error",
}

CLAUDE_CODE_CAPABILITIES = LLMProviderCapabilities(
    enforces_max_tokens=False,
    supports_sampling_controls=False,
    input_token_count="estimate",
    max_tool_calls_per_completion=1,
    requires_generation_control_acknowledgement=True,
)


# ---------------------------------------------------------------------------
# SDK bindings loader — the single, complete SDK test seam. Injectable via
# ClaudeCodeProvider(_sdk_loader=...); the earlier query_fn=-only seam was
# removed because injecting just `query` left every other SDK symbol
# (ClaudeAgentOptions, message/exception classes, HookMatcher,
# create_sdk_mcp_server/@tool) eagerly imported at call time regardless.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SDKBindings:
    query: Callable[..., Any]
    ClaudeAgentOptions: type
    ClaudeSDKError: type
    CLIConnectionError: type
    CLINotFoundError: type
    CLIJSONDecodeError: type
    MessageParseError: type
    ProcessError: type
    AssistantMessage: type
    TextBlock: type
    ResultMessage: type
    DeferredToolUse: type
    RateLimitEvent: type
    HookMatcher: type
    create_sdk_mcp_server: Callable[..., Any]
    tool: Callable[..., Any]
    cli_path: str


def _load_sdk_bindings() -> _SDKBindings:
    """Resolve every SDK symbol this provider needs, verify the exact
    supported SDK version, and resolve+verify the SDK's own bundled
    `claude` CLI binary — never a caller override, never a system-PATH
    fallback (that's what pinning to a resolved, verified binary path
    on `ClaudeAgentOptions.cli_path` prevents).
    """
    import importlib.metadata

    try:
        installed_version = importlib.metadata.version("claude-agent-sdk")
    except importlib.metadata.PackageNotFoundError as e:
        raise LLMBehaviorError(
            "llm.request_error",
            "ClaudeCodeProvider requires the `claude-agent-sdk` package "
            f"(exactly {_SUPPORTED_SDK_VERSION}). Install with "
            "`pip install activegraph[claude-code]`.",
            payload_extras={"phase": "setup"},
        ) from e
    if installed_version != _SUPPORTED_SDK_VERSION:
        raise LLMBehaviorError(
            "llm.request_error",
            f"claude-agent-sdk=={installed_version} is installed; "
            f"ClaudeCodeProvider supports exactly {_SUPPORTED_SDK_VERSION}. "
            f'Run `pip install "claude-agent-sdk=={_SUPPORTED_SDK_VERSION}"`.',
            payload_extras={"phase": "setup", "installed_version": installed_version},
        )
    try:
        import claude_agent_sdk as _sdk
        from claude_agent_sdk._errors import MessageParseError
    except ImportError as e:
        raise LLMBehaviorError(
            "llm.request_error",
            "ClaudeCodeProvider requires the `claude-agent-sdk` package. "
            "Install with `pip install activegraph[claude-code]`.",
            payload_extras={"phase": "setup"},
        ) from e

    cli_name = "claude.exe" if platform.system() == "Windows" else "claude"
    bundled_path = Path(_sdk.__file__).parent / "_bundled" / cli_name
    if not bundled_path.is_file() or not os.access(bundled_path, os.X_OK):
        raise LLMBehaviorError(
            "llm.request_error",
            f"claude-agent-sdk's bundled CLI binary was not found or is "
            f"not executable at {bundled_path}. Reinstall with "
            "`pip install --force-reinstall activegraph[claude-code]`.",
            payload_extras={"phase": "setup"},
        )

    return _SDKBindings(
        query=_sdk.query,
        ClaudeAgentOptions=_sdk.ClaudeAgentOptions,
        ClaudeSDKError=_sdk.ClaudeSDKError,
        CLIConnectionError=_sdk.CLIConnectionError,
        CLINotFoundError=_sdk.CLINotFoundError,
        CLIJSONDecodeError=_sdk.CLIJSONDecodeError,
        MessageParseError=MessageParseError,
        ProcessError=_sdk.ProcessError,
        AssistantMessage=_sdk.AssistantMessage,
        TextBlock=_sdk.TextBlock,
        ResultMessage=_sdk.ResultMessage,
        DeferredToolUse=_sdk.DeferredToolUse,
        RateLimitEvent=_sdk.RateLimitEvent,
        HookMatcher=_sdk.HookMatcher,
        create_sdk_mcp_server=_sdk.create_sdk_mcp_server,
        tool=_sdk.tool,
        cli_path=str(bundled_path),
    )


# ---------------------------------------------------------------------------
# Canonical transcript (Seam C equivalent)
# ---------------------------------------------------------------------------


def _canonical_json(payload: Any) -> str:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except ValueError as e:
        raise LLMBehaviorError(
            "llm.request_error",
            f"message content is not JSON-safe (a non-finite float?): {e}",
            payload_extras={"phase": "transcript"},
        ) from e


def _flatten_messages_to_prompt(messages: list[LLMMessage]) -> str:
    """One versioned canonical-JSON transcript, used both for the SDK
    prompt and (via `count_tokens`) the token-count estimate. See the
    module docstring's "Multi-turn tool continuation" section for why
    this is the sole supported representation — don't reintroduce role-
    dispatch prose delimiters or a raw `AsyncIterable[dict]` history
    replay without re-verifying both against a current CLI/SDK build.
    """
    transcript = [m.to_dict() for m in messages]
    body = _canonical_json(transcript)
    return f"{_TRANSCRIPT_FORMAT_VERSION}\n\n{body}"


# ---------------------------------------------------------------------------
# Tool contract (Seam E equivalent)
# ---------------------------------------------------------------------------


def _qualify_mcp_name(wire_name: str) -> str:
    return f"mcp__{_MCP_SERVER_KEY}__{wire_name}"


def _strip_mcp_prefix(qualified_name: str) -> str:
    prefix = f"mcp__{_MCP_SERVER_KEY}__"
    if not qualified_name.startswith(prefix):
        raise LLMBehaviorError(
            "llm.request_error",
            f"Deferred tool name {qualified_name!r} did not have the "
            f"expected {prefix!r} prefix — a built-in tool may have "
            f"fired despite tools=[].",
            payload_extras={"phase": "tool"},
        )
    return qualified_name[len(prefix) :]


def _exact_hook_matcher(qualified: str) -> str:
    """An anchored regex matching exactly one qualified tool name.
    Qualified names contain only the fixed `mcp__activegraph__` prefix
    plus the shared wire alphabet (`[a-zA-Z0-9_-]`), so hyphen is the
    only surviving regex-active character and needs escaping.
    """
    return "^(?:" + qualified.replace("-", r"\-") + ")$"


async def _defer_tool_callback(
    allowed: frozenset[str], input_data: Any, tool_use_id: Any, context: Any
) -> dict[str, Any]:
    """Top-level PreToolUse hook callback, bound to its call's allow-list
    via `functools.partial` (unit-testable, pickling/import friendly —
    CONTRACT v1.11 #1). Each `HookMatcher`'s own anchored regex already
    scopes which tool name routes here; this re-checks the exact
    allow-list as defense in depth (review CC2) and returns no defer
    decision for a miss, rather than assuming the matcher alone is
    sufficient.
    """
    tool_name = input_data.get("tool_name") if isinstance(input_data, dict) else None
    if tool_name not in allowed:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "defer",
            "permissionDecisionReason": "ActiveGraph Runtime owns tool execution",
        }
    }


def _build_tool_bindings(
    tools: list[dict[str, Any]], bindings: _SDKBindings
) -> tuple[Any, list[str], dict[str, list[Any]]]:
    """Register each offered tool as an in-process SDK-MCP tool whose
    handler is provably unreachable, alongside one exactly-anchored
    `PreToolUse` hook matcher per tool (never an unconditional/catch-all
    matcher — CC2). Returns `(server, allowed_tools, hooks)`.
    """
    sdk_tools: list[Any] = []
    allowed_names: list[str] = []
    for t in tools:
        wire_name = sanitize_tool_name(str(t.get("name", "")))
        qualified = _qualify_mcp_name(wire_name)
        input_schema = t.get("input_schema") or {"type": "object", "properties": {}}
        description = str(t.get("description", ""))

        async def _unreachable_handler(
            args: dict[str, Any], _qualified: str = qualified
        ) -> dict[str, Any]:
            raise AssertionError(
                f"unreachable: PreToolUse always defers {_qualified!r} "
                "before dispatch"
            )

        sdk_tools.append(
            bindings.tool(wire_name, description, input_schema)(_unreachable_handler)
        )
        allowed_names.append(qualified)

    allowed_set = frozenset(allowed_names)
    callback = functools.partial(_defer_tool_callback, allowed_set)
    hook_matchers = [
        bindings.HookMatcher(matcher=_exact_hook_matcher(q), hooks=[callback])
        for q in allowed_names
    ]

    server = bindings.create_sdk_mcp_server(name=_MCP_SERVER_KEY, tools=sdk_tools)
    return server, allowed_names, {"PreToolUse": hook_matchers}


def _tool_calls_from_result(
    result_message: Any, name_map: Optional[dict[str, str]]
) -> list[ToolCall]:
    """0-or-1 `ToolCall`s from `ResultMessage.deferred_tool_use` — never
    more; the SDK's `DeferredToolUse` is singular by construction.
    """
    deferred = getattr(result_message, "deferred_tool_use", None)
    if deferred is None:
        return []
    wire_name = _strip_mcp_prefix(deferred.name)
    canonical = restore_tool_name(wire_name, name_map)
    calls = [ToolCall(id=deferred.id, name=canonical, args=dict(deferred.input or {}))]
    assert len(calls) <= 1  # invariant: never a batch (review P4)
    return calls


# ---------------------------------------------------------------------------
# Async bridge + collector (Seam D equivalent)
# ---------------------------------------------------------------------------


@dataclass
class _QueryOutcome:
    """One primary state (a result, a work error, a timeout, or a clean
    zero-result stream) plus orthogonal cleanup state, resolved by
    `_finish`'s precedence rules.
    """

    assistant_text_parts: list[str]
    assistant_error_codes: list[str]
    rate_limit_events: list[dict[str, Any]]
    result_message: Any
    work_error: Optional[BaseException]
    timed_out: bool
    cleanup_error: Optional[BaseException]
    cleanup_timed_out: bool


def _rate_limit_event_to_dict(event: Any) -> dict[str, Any]:
    info = event.rate_limit_info
    return {
        "uuid": event.uuid,
        "session_id": event.session_id,
        "status": info.status,
        "resets_at": info.resets_at,
        "rate_limit_type": info.rate_limit_type,
        "utilization": info.utilization,
        "overage_status": info.overage_status,
    }


async def _run_single_query(
    bindings: _SDKBindings,
    prompt: str,
    options: Any,
    timeout_seconds: float,
    teardown_grace_seconds: float = _TEARDOWN_GRACE_SECONDS,
) -> _QueryOutcome:
    """Drive one `query()` call to its first terminal `ResultMessage`,
    then close the generator. Three deliberate choices (CONTRACT v1.11
    #1, reviews P1/C5/CC3):

    - `anyio.fail_after`, not raw `asyncio.wait_for` — applied only to
      the query work, never to cleanup.
    - Stop at the *first* terminal `ResultMessage` and never read past
      it — a later process-error frame in the same stream must not
      overwrite a useful classification.
    - `aclose()` runs in `finally`, under a **shielded** cancel scope so
      the expired work deadline can't also cancel cleanup, bounded by
      its own `teardown_grace_seconds` (default 25s, covering the
      pinned SDK's documented ~20s terminate/kill escalation) — so a
      retry cannot begin until close completes or the teardown bound is
      reached. Cleanup failure/timeout is reported back to the caller
      as orthogonal state, never silently swallowed and never allowed
      to override a *different*, already-classified primary failure.
    """
    import anyio

    assistant_text_parts: list[str] = []
    assistant_error_codes: list[str] = []
    rate_limit_events: list[dict[str, Any]] = []
    result_message: Any = None
    work_error: Optional[BaseException] = None
    timed_out = False
    cleanup_error: Optional[BaseException] = None
    cleanup_timed_out = False

    gen = bindings.query(prompt=prompt, options=options)
    try:
        with anyio.fail_after(timeout_seconds):
            async for msg in gen:
                if isinstance(msg, bindings.AssistantMessage):
                    for block in msg.content or []:
                        text = getattr(block, "text", None)
                        if isinstance(text, str):
                            assistant_text_parts.append(text)
                    error_code = getattr(msg, "error", None)
                    if error_code is not None:
                        assistant_error_codes.append(error_code)
                elif isinstance(msg, bindings.RateLimitEvent):
                    rate_limit_events.append(_rate_limit_event_to_dict(msg))
                elif isinstance(msg, bindings.ResultMessage):
                    result_message = msg
                    break
    except TimeoutError:
        timed_out = True
    except BaseException as e:  # classified by the caller, never here
        work_error = e
    finally:
        with anyio.CancelScope(shield=True):
            try:
                with anyio.fail_after(teardown_grace_seconds):
                    await gen.aclose()
            except TimeoutError:
                cleanup_timed_out = True
            except BaseException as e:
                cleanup_error = e

    return _QueryOutcome(
        assistant_text_parts=assistant_text_parts,
        assistant_error_codes=assistant_error_codes,
        rate_limit_events=rate_limit_events,
        result_message=result_message,
        work_error=work_error,
        timed_out=timed_out,
        cleanup_error=cleanup_error,
        cleanup_timed_out=cleanup_timed_out,
    )


# ---------------------------------------------------------------------------
# Result classification (Seam F equivalent)
# ---------------------------------------------------------------------------


def _classify_exception(bindings: _SDKBindings, exc: BaseException) -> str:
    if isinstance(exc, bindings.CLINotFoundError):
        return "llm.request_error"
    if isinstance(
        exc,
        (
            bindings.CLIConnectionError,
            bindings.ProcessError,
            bindings.CLIJSONDecodeError,
            bindings.MessageParseError,
        ),
    ):
        return "llm.network_error"
    if isinstance(exc, bindings.ClaudeSDKError):
        return "llm.network_error"
    return "llm.network_error"  # unknown raised exception, no status — retryable fallback


def _work_error_extras(exc: BaseException, *, phase: str) -> dict[str, Any]:
    # Sanitized: type/status/phase only — never a raw frame that could
    # carry prompt or credential material.
    extras: dict[str, Any] = {"phase": phase, "exception_type": type(exc).__name__}
    exit_code = getattr(exc, "exit_code", None)
    if exit_code is not None:
        extras["exit_code"] = exit_code
    return extras


def _classify_result_status(status: Optional[int]) -> str:
    reason = classify_provider_status(status)
    if reason == "llm.network_error" and (status is None or status < 500):
        # An observed terminal ResultMessage(is_error=True) with no
        # retryable status is itself the terminal signal — default to
        # request_error, not the raised-exception network fallback
        # (this deliberately differs from _classify_exception's default).
        return "llm.request_error"
    return reason


def _classify_result_error(result_message: Any) -> tuple[str, dict[str, Any], str]:
    terminal_reason = getattr(result_message, "terminal_reason", None)
    subtype = getattr(result_message, "subtype", None)
    status = getattr(result_message, "api_error_status", None)
    permission_denials = getattr(result_message, "permission_denials", None)
    errors = getattr(result_message, "errors", None)
    extras: dict[str, Any] = {
        "phase": "result",
        "subtype": subtype,
        "api_error_status": status,
        "terminal_reason": terminal_reason,
        "permission_denials": list(permission_denials) if permission_denials else None,
        "errors": list(errors) if errors else None,
        "uuid": getattr(result_message, "uuid", None),
    }
    message = (
        (errors[0] if errors else None)
        or getattr(result_message, "result", None)
        or subtype
        or "unknown error"
    )

    if terminal_reason == "aborted_streaming":
        return "llm.network_error", extras, message
    if terminal_reason in ("max_turns", "aborted_tools"):
        return "llm.request_error", extras, message
    if subtype in _LOCAL_TERMINAL_LIMIT_SUBTYPES:
        return "llm.request_error", extras, message
    if permission_denials:
        return "llm.request_error", extras, message
    return _classify_result_status(status), extras, message


def _classify_zero_result(assistant_error_codes: list[str]) -> str:
    for code in assistant_error_codes:
        reason = _ASSISTANT_ERROR_REASON.get(code)
        if reason is not None:
            return reason
    return "llm.network_error"


def _cleanup_extras(outcome: _QueryOutcome) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    if outcome.cleanup_timed_out:
        extras["cleanup_timed_out"] = True
    if outcome.cleanup_error is not None:
        extras["cleanup_error_type"] = type(outcome.cleanup_error).__name__
    return extras


# ---------------------------------------------------------------------------
# Cost / token accounting
# ---------------------------------------------------------------------------


def _pricing_for(model: str, pricing: dict[str, dict[str, str]]) -> tuple[Decimal, Decimal]:
    best_key: Optional[str] = None
    for key in pricing:
        if model.startswith(key) and (best_key is None or len(key) > len(best_key)):
            best_key = key
    if best_key is None:
        best_key = "claude-sonnet-4"
    entry = pricing[best_key]
    return Decimal(str(entry["input"])), Decimal(str(entry["output"]))


def _local_estimate_cost(input_tokens: int, output_tokens: int, model: str) -> Decimal:
    in_price, out_price = _pricing_for(model, _DEFAULT_PRICING)
    million = Decimal("1000000")
    return (Decimal(input_tokens) * in_price / million) + (
        Decimal(output_tokens) * out_price / million
    )


def _aggregate_tokens(result_message: Any) -> tuple[int, int]:
    """Sums `inputTokens + cacheReadInputTokens + cacheCreationInputTokens`
    and `outputTokens` across every `model_usage` entry — representing
    every billed/list-rate input token class, not only uncached input.
    Falls back to the equivalent snake_case `usage` fields when
    `model_usage` is absent.
    """
    model_usage = getattr(result_message, "model_usage", None)
    if model_usage:
        in_tok = sum(
            int(u.get("inputTokens", 0) or 0)
            + int(u.get("cacheReadInputTokens", 0) or 0)
            + int(u.get("cacheCreationInputTokens", 0) or 0)
            for u in model_usage.values()
        )
        out_tok = sum(int(u.get("outputTokens", 0) or 0) for u in model_usage.values())
        return in_tok, out_tok
    usage = getattr(result_message, "usage", None) or {}
    in_tok = (
        int(usage.get("input_tokens", 0) or 0)
        + int(usage.get("cache_read_input_tokens", 0) or 0)
        + int(usage.get("cache_creation_input_tokens", 0) or 0)
    )
    out_tok = int(usage.get("output_tokens", 0) or 0)
    return in_tok, out_tok


def _resolve_cost(result_message: Any, in_tok: int, out_tok: int, model: str) -> tuple[Decimal, str]:
    """Three-tier fallback, list-rate first: the SDK's own
    `total_cost_usd` already accounts for cache-read/cache-creation
    tokens a local ordinary-token-only estimate would silently ignore.
    """
    total_cost_usd = getattr(result_message, "total_cost_usd", None)
    if total_cost_usd is not None:
        return Decimal(str(total_cost_usd)), "total_cost_usd"
    model_usage = getattr(result_message, "model_usage", None) or {}
    summed = sum(
        (Decimal(str(u.get("costUSD", 0))) for u in model_usage.values()), Decimal("0")
    )
    if summed > 0:
        return summed, "model_usage"
    return _local_estimate_cost(in_tok, out_tok, model), "local_estimate"


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


def _provider_meta(result_message: Any, outcome: _QueryOutcome, cost_source: str) -> dict[str, Any]:
    model_usage = getattr(result_message, "model_usage", None)
    return {
        "subtype": result_message.subtype,
        "duration_ms": result_message.duration_ms,
        "duration_api_ms": result_message.duration_api_ms,
        "num_turns": result_message.num_turns,
        "session_id": result_message.session_id,
        "stop_reason": result_message.stop_reason,
        "terminal_reason": result_message.terminal_reason,
        "total_cost_usd": result_message.total_cost_usd,
        "usage": _jsonable(result_message.usage),
        "model_usage": _jsonable(dict(model_usage)) if model_usage else {},
        "permission_denials": _jsonable(result_message.permission_denials),
        "errors": list(result_message.errors) if result_message.errors else None,
        "api_error_status": result_message.api_error_status,
        "uuid": result_message.uuid,
        "rate_limit_events": outcome.rate_limit_events,
        "assistant_error_codes": list(outcome.assistant_error_codes),
        "cost_source": cost_source,
        # Behavior 0's capability decision: these are always accepted
        # but never enforced by this provider, for every call.
        "requested_controls_unenforced": {
            "max_tokens": True,
            "temperature": True,
            "top_p": True,
        },
    }


def _map_result(
    outcome: _QueryOutcome,
    *,
    model: str,
    structured_output_mode: str,
    output_schema: Optional[type],
    name_map: Optional[dict[str, str]],
    latency: float,
) -> LLMResponse:
    result_message = outcome.result_message
    if result_message.is_error:
        reason, extras, message = _classify_result_error(result_message)
        raise LLMBehaviorError(reason, message, payload_extras={**extras, "model": model})

    # Mapping order (CONTRACT v1.11 #1): error -> deferred tool ->
    # native final -> prompt-mode final -> unstructured.
    tool_calls = _tool_calls_from_result(result_message, name_map)
    parsed: Any = None
    if tool_calls:
        text = "".join(outcome.assistant_text_parts)
    else:
        native_mode = structured_output_mode == "native" and output_schema is not None
        if native_mode:
            native = result_message.structured_output
            if native is None:
                raise LLMBehaviorError(
                    "llm.parse_error",
                    "native structured output missing from ResultMessage",
                    payload_extras={"model": model},
                )
            try:
                parsed = output_schema.model_validate(native)  # type: ignore[union-attr]
            except Exception as e:
                raise LLMBehaviorError(
                    "llm.schema_violation", str(e), payload_extras={"model": model}
                ) from e
            text = _canonical_json(native)
        else:
            text = "".join(outcome.assistant_text_parts) or (result_message.result or "")
            if output_schema is not None:
                parsed = _parse_structured(text, output_schema)

    in_tok, out_tok = _aggregate_tokens(result_message)
    cost_usd, cost_source = _resolve_cost(result_message, in_tok, out_tok, model)

    return LLMResponse(
        raw_text=text,
        parsed=parsed,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost_usd,
        latency_seconds=latency,
        model=model,
        finish_reason=(result_message.stop_reason or result_message.terminal_reason or "completed"),
        seed=None,
        cache_hit=False,
        provider_meta=_provider_meta(result_message, outcome, cost_source),
        tool_calls=tool_calls or None,
    )


def _finish(
    outcome: _QueryOutcome,
    bindings: _SDKBindings,
    *,
    model: str,
    structured_output_mode: str,
    output_schema: Optional[type],
    name_map: Optional[dict[str, str]],
    timeout_seconds: float,
    latency: float,
) -> LLMResponse:
    """Error precedence (CONTRACT v1.11 #1): a primary work error or
    timeout remains primary and carries cleanup diagnostics as extras;
    a successful terminal result is not returned unless cleanup also
    succeeded (cleanup failure/timeout after success maps to
    `llm.network_error`); a zero-result clean stream is
    `llm.network_error` (or an `AssistantMessage.error`-derived reason
    when one was observed).
    """
    cleanup_extras = _cleanup_extras(outcome)

    if outcome.work_error is not None:
        reason = _classify_exception(bindings, outcome.work_error)
        extras = {**_work_error_extras(outcome.work_error, phase="stream"), **cleanup_extras, "model": model}
        raise LLMBehaviorError(reason, str(outcome.work_error), payload_extras=extras) from outcome.work_error

    if outcome.timed_out:
        extras = {"phase": "timeout", "timeout_seconds": timeout_seconds, "model": model, **cleanup_extras}
        raise LLMBehaviorError(
            "llm.network_error",
            f"ClaudeCodeProvider: query timed out after {timeout_seconds}s",
            payload_extras=extras,
        )

    if outcome.result_message is not None:
        if outcome.cleanup_error is not None or outcome.cleanup_timed_out:
            extras = {"phase": "cleanup", "model": model, **cleanup_extras}
            raise LLMBehaviorError(
                "llm.network_error",
                "ClaudeCodeProvider: query succeeded but cleanup failed",
                payload_extras=extras,
            )
        return _map_result(
            outcome,
            model=model,
            structured_output_mode=structured_output_mode,
            output_schema=output_schema,
            name_map=name_map,
            latency=latency,
        )

    reason = _classify_zero_result(outcome.assistant_error_codes)
    extras = {
        "phase": "stream",
        "model": model,
        "assistant_error_codes": list(outcome.assistant_error_codes),
        **cleanup_extras,
    }
    raise LLMBehaviorError(
        reason,
        "ClaudeCodeProvider: the query stream ended without a terminal ResultMessage",
        payload_extras=extras,
    )


class ClaudeCodeProvider(LLMProvider):
    """See the module docstring for the full capability-limited-
    provider record. `default_model` and pricing are sourced from
    `activegraph.llm._claude_shared` so they can never drift from
    `AnthropicProvider`'s for the same model family.
    """

    default_model: str = _DEFAULT_MODEL
    llm_capabilities: LLMProviderCapabilities = CLAUDE_CODE_CAPABILITIES

    def __init__(
        self,
        *,
        allow_unenforced_generation_controls: bool = False,
        reject_metered_env_auth: bool = True,
        _sdk_loader: Optional[Callable[[], _SDKBindings]] = None,
    ) -> None:
        # MUST do zero I/O — no os.environ read, no claude_agent_sdk
        # import, no subprocess — so _live.py's bare cls() probing and
        # Runtime's capability-binding validation (which constructs
        # nothing) stay safe.
        self.allow_unenforced_generation_controls = bool(allow_unenforced_generation_controls)
        self._reject_metered_env_auth = bool(reject_metered_env_auth)
        self._sdk_loader = _sdk_loader
        self._bindings_cached: Optional[_SDKBindings] = None

    # ---- SDK lazy-load ----

    def _resolve_bindings(self) -> _SDKBindings:
        if self._bindings_cached is not None:
            return self._bindings_cached
        loader = self._sdk_loader or _load_sdk_bindings
        bindings = loader()
        self._bindings_cached = bindings
        return bindings

    def _check_isolation(self) -> None:
        if self._reject_metered_env_auth:
            present = [v for v in _CREDENTIAL_ROUTING_VARS if os.environ.get(v)]
            if present:
                raise LLMBehaviorError(
                    "llm.auth_error",
                    "ClaudeCodeProvider prefers Claude subscription auth "
                    "(reject_metered_env_auth=True, the default), but "
                    f"{', '.join(present)} is set in the environment and "
                    "takes precedence per the CLI's documented auth "
                    "precedence. Unset it to bill against your "
                    "subscription, or pass reject_metered_env_auth=False "
                    "to allow it.",
                    payload_extras={"phase": "setup", "conflicting_vars": present},
                )
        present_isolation = [v for v in _NESTED_SESSION_VARS if os.environ.get(v)]
        if present_isolation:
            raise LLMBehaviorError(
                "llm.request_error",
                "ClaudeCodeProvider requires an isolated, non-nested "
                f"session, but {', '.join(present_isolation)} is set in "
                "the environment.",
                payload_extras={"phase": "setup", "conflicting_vars": present_isolation},
            )

    def _child_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        if self._reject_metered_env_auth:
            for v in _CREDENTIAL_ROUTING_VARS:
                env[v] = ""
        for v in _NESTED_SESSION_VARS:
            env[v] = ""
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
        env["CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS"] = "1"
        return env

    # ---- LLMProvider methods ----

    def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        output_schema: Optional[type],
        timeout_seconds: float,
        tools: Optional[list[dict[str, Any]]] = None,
        structured_output_mode: str = "prompt",
    ) -> LLMResponse:
        # NOTE: max_tokens/temperature/top_p are intentionally never
        # forwarded — Behavior 0's capability decision. There is no
        # deterministic= parameter here: Runtime's capability-binding
        # validation refuses to bind a deterministic @llm_behavior to
        # this provider before complete() is ever reachable.

        # Resolve the SDK bindings first, so a genuinely missing
        # `claude-code` extra (which sniffio is also part of) always
        # surfaces as the same clean install-hint LLMBehaviorError, not
        # a raw ImportError from the sniffio check below.
        bindings = self._resolve_bindings()

        try:
            import sniffio
        except ImportError as e:
            raise LLMBehaviorError(
                "llm.request_error",
                "ClaudeCodeProvider requires the `sniffio` package. "
                "Install with `pip install activegraph[claude-code]`.",
                payload_extras={"phase": "setup"},
            ) from e

        try:
            sniffio.current_async_library()
        except sniffio.AsyncLibraryNotFoundError:
            pass  # not in a running async context — the supported case
        else:
            raise LLMBehaviorError(
                "llm.request_error",
                "ClaudeCodeProvider.complete() cannot be called from "
                "inside a running asyncio/Trio context; call it from a "
                "synchronous context or a worker thread.",
                payload_extras={"phase": "setup"},
            )

        self._check_isolation()

        prompt = _flatten_messages_to_prompt(messages)
        name_map: Optional[dict[str, str]] = None

        with tempfile.TemporaryDirectory(prefix="activegraph-claude-code-") as cwd:
            options_kwargs: dict[str, Any] = {
                "model": model,
                "tools": [],  # strips built-ins even with no caller tools=
                "mcp_servers": {},
                "allowed_tools": [],
                "strict_mcp_config": True,
                "permission_mode": "dontAsk",
                "setting_sources": [],
                "skills": [],
                "plugins": [],
                "agents": None,
                "max_turns": 1,
                "continue_conversation": False,
                "resume": None,
                "fork_session": False,
                "session_id": None,
                "enable_file_checkpointing": False,
                "session_store": None,
                "extra_args": {"no-session-persistence": None},
                "env": self._child_env(),
                "cwd": cwd,
                "cli_path": bindings.cli_path,
            }
            if system:
                options_kwargs["system_prompt"] = system
            if tools:
                name_map = build_tool_name_map(list(tools))
                server, allowed, hooks = _build_tool_bindings(tools, bindings)
                options_kwargs["mcp_servers"] = {_MCP_SERVER_KEY: server}
                options_kwargs["allowed_tools"] = allowed
                options_kwargs["hooks"] = hooks
            if structured_output_mode == "native" and output_schema is not None:
                from activegraph.llm.native import inject_additional_properties_false
                from activegraph.llm.prompt import schema_to_json

                schema_json = schema_to_json(output_schema)
                assert schema_json is not None  # output_schema is not None
                options_kwargs["output_format"] = {
                    "type": "json_schema",
                    "schema": inject_additional_properties_false(schema_json),
                }
            options = bindings.ClaudeAgentOptions(**options_kwargs)

            import anyio

            t0 = time.monotonic()
            outcome = anyio.run(_run_single_query, bindings, prompt, options, timeout_seconds)
            latency = time.monotonic() - t0

        return _finish(
            outcome,
            bindings,
            model=model,
            structured_output_mode=structured_output_mode,
            output_schema=output_schema,
            name_map=name_map,
            timeout_seconds=timeout_seconds,
            latency=latency,
        )

    def estimate_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> Decimal:
        return _local_estimate_cost(input_tokens, output_tokens, model)

    def count_tokens(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
    ) -> int:
        """Heuristic-only (`llm_capabilities.input_token_count ==
        "estimate"`) — this SDK exposes no token-counting capability at
        all. `ESTIMATED_UTF8_BYTES_PER_TOKEN` UTF-8-byte-length-based
        ceiling division over `system` plus the exact canonical
        transcript `complete()` would send — not offered tool schemas,
        native output schema, CLI system context, or managed settings,
        none of which this Protocol method has access to. Never touches
        `_resolve_bindings()`/the SDK/the network.
        """
        transcript = _flatten_messages_to_prompt(messages)
        total_bytes = len(system.encode("utf-8")) + len(transcript.encode("utf-8"))
        return -(-total_bytes // _ESTIMATED_UTF8_BYTES_PER_TOKEN)  # ceiling division

    def recognizes_model(self, name: str) -> bool:
        """True for the ``claude-*`` model family — same family
        `AnthropicProvider` claims. That's expected: the cross-provider
        mismatch check in `runtime/_live.py` is about catching a
        *mismatched* provider, not enforcing single ownership of a
        model family (Behavior 12).
        """
        return name.startswith("claude-")

    def supports_native_structured_output(self, model: str) -> bool:
        return model.startswith(_NATIVE_STRUCTURED_OUTPUT_PREFIXES)
