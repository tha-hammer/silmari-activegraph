"""Tool-side errors. CONTRACT v0.7 #6, + v1.0 PR-D format migration.

Three surface types:

- :class:`ToolError` — structured failure from a tool body. Carries a
  ``reason`` code that the runtime merges into
  ``tool.responded.payload.error`` and into the wrapping behavior's
  ``behavior.failed`` event.

- :class:`MissingToolError` — raised at runtime startup when an
  ``@llm_behavior`` declares a tool name the runtime cannot find.
  Stays a plain RuntimeError subclass through PR-D; PR-E re-parents.

- :class:`UnknownToolError` — raised when an LLM response asks for a
  tool the behavior did not declare. Caught by the runtime and
  surfaced as ``behavior.failed reason="tool.unknown_tool"``.

PR-D migrates ``ToolError`` and ``UnknownToolError`` to
:class:`activegraph.errors.ExecutionError`. The
``(reason, message, payload_extras)`` constructor on ``ToolError`` is
preserved; per-reason prose lives in ``_TOOL_REASON_PROSE``.
"""

from __future__ import annotations

from typing import Any, Optional

from activegraph.errors import ExecutionError, RegistrationError


def _tool_prose_timeout(message: str) -> tuple[str, str, str]:
    return (
        f"A tool invocation exceeded its declared `timeout_seconds`:\n  {message}",
        "Tools declare a per-call timeout at the decorator. The runtime "
        "enforces it so a slow or hung tool can't stall the whole behavior "
        "loop. A timed-out call returns a structured failure to the calling "
        "behavior; the behavior decides whether to retry, fall back, or "
        "fail.",
        "If the timeout is too aggressive for the expected work, raise the "
        "tool's `timeout_seconds`. If the timeout is hitting because the "
        "endpoint is slow under contention, the right answer is usually a "
        "narrower retry policy in the calling behavior rather than a higher "
        "ceiling.",
    )


def _tool_prose_network_error(message: str) -> tuple[str, str, str]:
    return (
        f"A tool call failed with a network error:\n  {message}",
        "Tools that reach the network can fail for many reasons (DNS, TLS, "
        "connection drop, mid-transfer error). The framework treats these as "
        "structured tool failures rather than untyped exceptions so the "
        "calling behavior can read `reason='tool.network_error'` from the "
        "tool.responded event payload and decide how to proceed.",
        "Inspect the tool.responded event for the full underlying error. "
        "Common recoveries: re-run after the network stabilizes, switch to "
        "RecordedTool for offline replay, or add explicit retry-on-network "
        "logic in the calling behavior.",
    )


def _tool_prose_invalid_input(message: str) -> tuple[str, str, str]:
    return (
        f"A tool was invoked with arguments that didn't match its input schema:\n  {message}",
        "Tools declare typed input via Pydantic models. The framework "
        "validates arguments before invoking the body so a malformed call "
        "fails at the boundary with a clear error instead of producing a "
        "stack trace inside the tool. This is the same Pydantic invariant "
        "the LLM output_schema enforces — typed input is the contract.",
        "Check the tool's declared input schema (in the @tool decorator) "
        "against the arguments the LLM produced. If the LLM is producing "
        "consistently malformed args, the prompt may need an explicit "
        "example of correct invocation; if the tool's schema is too "
        "strict, relax the relevant field.",
    )


def _tool_prose_invalid_output(message: str) -> tuple[str, str, str]:
    return (
        f"A tool returned a value that didn't match its output schema:\n  {message}",
        "Tools declare typed output via Pydantic models. The framework "
        "validates the return value before merging it into the "
        "tool.responded event so downstream behaviors can rely on the "
        "shape. A schema-violating return is a bug in the tool body — "
        "the audit trail would lie if the framework silently coerced it.",
        "Fix the tool body to return data matching the declared schema, "
        "or relax the schema if the actual return shape is correct. The "
        "underlying value is in the tool.responded payload for inspection."
    )


def _tool_prose_execution_error(message: str) -> tuple[str, str, str]:
    return (
        f"A tool body raised an exception:\n  {message}",
        "When a tool body raises, the framework catches it and surfaces a "
        "structured failure so the calling behavior can read "
        "`reason='tool.execution_error'` from tool.responded and decide "
        "whether to retry or fail. The raw exception is preserved in "
        "payload_extras for diagnosis without leaking it past the tool "
        "boundary.",
        "Inspect tool.responded.payload_extras for the original exception "
        "type and traceback. If the failure is intrinsic to the tool's "
        "inputs (bad data), tighten the input validation. If it's "
        "intermittent, add retry-on-execution-error logic to the calling "
        "behavior.",
    )


def _tool_prose_fixture_missing(message: str) -> tuple[str, str, str]:
    return (
        f"A RecordedTool has no fixture for this argument combination:\n  {message}",
        "RecordedTool replays a directory of recorded tool responses keyed "
        "by tool name + argument hash. A missing fixture means the live "
        "arguments don't match any recorded invocation — either the tool's "
        "arguments changed since recording (a behavior edit, an upstream "
        "data shift), or this is a new invocation that was never recorded.",
        "Re-record the fixture from a live run with the current arguments:\n"
        "    1. Switch the tool to its live implementation\n"
        "    2. Run the goal once to produce live responses\n"
        "    3. The recorder writes new fixtures alongside the existing ones\n"
        "    4. Subsequent runs against RecordedTool replay them\n"
        "\n"
        "Or diff the args against the recorded hash to find the drift.",
    )


_TOOL_REASON_PROSE: dict[str, Any] = {
    "tool.timeout": _tool_prose_timeout,
    "tool.network_error": _tool_prose_network_error,
    "tool.invalid_input": _tool_prose_invalid_input,
    "tool.invalid_output": _tool_prose_invalid_output,
    "tool.execution_error": _tool_prose_execution_error,
    "tool.fixture_missing": _tool_prose_fixture_missing,
}


def _tool_fallback_prose(reason: str, message: str) -> tuple[str, str, str]:
    return (
        f"A tool invocation failed with reason {reason!r}:\n  {message}",
        f"The runtime catches structured failures from tool bodies and merges "
        f"them into the emitted tool.responded event, where the calling "
        f"behavior can read `reason={reason!r}` and decide how to proceed. "
        f"The exception you're seeing is the underlying carrier.",
        f"Inspect the tool.responded event in the trace:\n"
        f"    activegraph inspect <store> --tail 50\n"
        f"\n"
        f"The full message is preserved verbatim above; check the tool's "
        f"documentation for reason {reason!r}.",
    )


class MissingToolError(RegistrationError, RuntimeError):
    """An ``@llm_behavior`` declares a tool name the runtime cannot find
    in its tool registry at startup.

    Fires at construction time, not at LLM-call time — the runtime
    validates the declared tools once when the behavior registers.
    Multi-inherits :class:`RuntimeError` for back-compat.
    """

    _doc_slug = "missing-tool-error"

    def __init__(
        self,
        tool_name: str,
        *,
        behavior_name: Optional[str] = None,
        registered: Optional[tuple[str, ...]] = None,
    ) -> None:
        self.tool_name = tool_name
        self.behavior_name = behavior_name
        self.registered = registered or ()
        ctx: dict[str, Any] = {"tool_name": tool_name}
        if behavior_name:
            ctx["behavior_name"] = behavior_name
        if self.registered:
            ctx["registered"] = list(self.registered)
        sample = ""
        if self.registered:
            preview = ", ".join(repr(n) for n in list(self.registered)[:6])
            extra = f" (+{len(self.registered) - 6} more)" if len(self.registered) > 6 else ""
            sample = f"\n  registered tools: {preview}{extra}"
        on_behavior = (
            f" on @llm_behavior {behavior_name!r}" if behavior_name else ""
        )
        RegistrationError.__init__(
            self,
            f"no tool named {tool_name!r} is registered",
            what_failed=(
                f"@llm_behavior declares the tool {tool_name!r}{on_behavior}, "
                f"but the Runtime's tool registry has no tool by that name.{sample}"
            ),
            why=(
                "@llm_behavior validates its declared tools at startup so a "
                "misconfiguration fails before any LLM call burns budget. "
                "A missing tool at LLM-call time would either produce "
                "UnknownToolError on every invocation (cost without "
                "progress) or silently drop the call (which would corrupt "
                "the audit trail). Validation at registration prevents both."
            ),
            how_to_fix=(
                "Either register the tool with the runtime:\n"
                "    rt = Runtime(graph, tools=[my_tool, ...])\n"
                "or, if the tool comes from a pack, load the pack:\n"
                "    rt.load_pack(my_pack)\n"
                "\n"
                "For pack-scoped tools, use the canonical name "
                "`'pack_name.tool_name'` in the @llm_behavior's "
                "`tools=[...]` argument."
            ),
            context=ctx,
        )


class UnknownToolError(ExecutionError, RuntimeError):
    """Raised when an LLM response calls a tool the behavior didn't declare.

    The runtime catches it during the LLM tool-loop and surfaces it as
    ``behavior.failed reason="tool.unknown_tool"``. Multi-inherits
    RuntimeError so user code that catches RuntimeError around runtime
    operations continues to work.
    """

    _doc_slug = "unknown-tool-error"

    def __init__(
        self,
        message: str,
        *,
        tool_name: Optional[str] = None,
        behavior_name: Optional[str] = None,
        declared_tools: Optional[tuple[str, ...]] = None,
    ) -> None:
        self.tool_name = tool_name
        self.behavior_name = behavior_name
        self.declared_tools = declared_tools or ()
        ctx: dict[str, Any] = {"message": message}
        if tool_name is not None:
            ctx["tool_name"] = tool_name
        if behavior_name is not None:
            ctx["behavior_name"] = behavior_name
        if self.declared_tools:
            ctx["declared_tools"] = list(self.declared_tools)
        declared_list = (
            ", ".join(repr(t) for t in self.declared_tools)
            if self.declared_tools
            else "(none declared)"
        )
        ExecutionError.__init__(
            self,
            message,
            what_failed=(
                f"An LLM response asked to invoke a tool that the calling "
                f"behavior did not declare.\n"
                f"  tool requested: {tool_name!r}\n"
                f"  declared on behavior {behavior_name!r}: {declared_list}"
            ),
            why=(
                "@llm_behavior declares the exact set of tools the wrapped "
                "behavior is allowed to invoke. The runtime refuses any other "
                "tool call rather than silently execute it — an undeclared "
                "tool could perform side effects the behavior's audit trail "
                "doesn't account for, which would break replay determinism."
            ),
            how_to_fix=(
                f"Either add {tool_name!r} to the @llm_behavior's `tools=[...]` "
                f"list (and confirm the tool is registered with @tool), or "
                f"adjust the prompt so the model stops asking for it. If the "
                f"model is consistently asking for an undeclared tool, the "
                f"prompt may be implying capabilities the behavior doesn't "
                f"have — be explicit about which tools are available."
            ),
            context=ctx,
        )


class ToolError(ExecutionError, Exception):
    """Structured failure from inside a tool invocation.

    ``reason`` must be one of the v0.7 codes:

      tool.timeout, tool.network_error, tool.invalid_input,
      tool.invalid_output, tool.execution_error,
      tool.unknown_tool, tool.fixture_missing,
      tool.max_turns_exhausted, tool.unrecorded_external_io,
      budget.tool_calls_exhausted, budget.cost_exhausted.

    Constructor signature ``(reason, message, *, payload_extras=)`` is
    preserved from v0.7 so the internal raise sites in tool bodies do
    not change. The structured-format fields are auto-derived from
    ``reason`` via ``_TOOL_REASON_PROSE``.
    """

    _doc_slug = "tool-error"

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        payload_extras: Optional[dict[str, Any]] = None,
    ) -> None:
        self.reason = reason
        self.payload_extras = dict(payload_extras or {})
        prose_fn = _TOOL_REASON_PROSE.get(reason)
        if prose_fn is None:
            what_failed, why, how_to_fix = _tool_fallback_prose(reason, message)
        else:
            what_failed, why, how_to_fix = prose_fn(message)
        ExecutionError.__init__(
            self,
            f"{reason}: {message}",
            what_failed=what_failed,
            why=why,
            how_to_fix=how_to_fix,
            context={
                "reason": reason,
                "message": message,
                "payload_extras": self.payload_extras,
            },
        )
