# Standalone OpenRouter LLM Provider TDD Implementation Plan

## Overview

Add `OpenRouterProvider` as ActiveGraph's **fourth** concrete shipped
`LLMProvider`, after `ClaudeCodeProvider`. The provider uses OpenRouter's
OpenAI-compatible non-streaming Chat Completions endpoint and reuses
`OpenAIProvider` through narrow, characterized protected hooks. It remains
directly injectable through `Runtime(..., llm_provider=...)`; this work does not
add a registry or change the public `LLMProvider` Protocol.

This revision incorporates every critical and warning finding from
`2026-08-11-12-48-AF-ysq-tdd-openrouter-llm-provider-REVIEW.md`. It is rebased
on the live tree, where `ClaudeCodeProvider`, provider capabilities, hard-budget
binding validation, and all-match model-owner diagnostics already exist. All
implementation edits must merge with and preserve those concurrent Claude Code
and BAML changes.

Tracked implementation issue: `AF-ysq`. Plan-revision issue: `AF-e0n`.

Source research:
`thoughts/searchable/shared/research/2026-08-11-12-25-AF-s36-openrouter-llm-client-pattern.md`.

## Readiness and Review Traceability

The implementation may start only from this revised contract. The live pre-edit
baseline is:

```text
.venv/bin/python -m pytest -q \
  tests/test_llm_openai.py \
  tests/test_llm_default_model.py \
  tests/test_llm_provider.py

52 passed
```

The review's nine blockers are closed in the plan as follows:

| Review blocker | Locked resolution |
| --- | --- |
| Stale provider topology | Treat OpenRouter as fourth; candidate order is Anthropic, OpenAI, Claude Code, OpenRouter; preserve list-of-all-matches semantics. |
| False hard-cost-budget path | Declare estimated input counts; reject every hard `max_cost_usd` binding before tokenization or SDK I/O; retain `Infinity` only for direct unknown-model estimates. |
| Parameters may be ignored | Send `provider.require_parameters=true` on every OpenRouter request, including tools and native schemas. |
| Wrong in-band error path | Inspect `choices[0].error` first, then defensive top-level `error`, before any content/tool/schema/cost extraction. |
| Hidden SDK retries | Internally constructed clients use `max_retries=0`; injected-client retry behavior remains caller-owned. |
| Fake SDK compatibility proof | Exercise literal HTTP responses through real `openai==1.55.3` models over `httpx.MockTransport` in an exact-minimum CI lane. |
| Constructor/pricing ambiguity | Pin the full keyword-only constructor; `None` and `{}` both mean an empty OpenRouter-owned price table; deep-copy and validate inputs. |
| Unsafe/inexact monetary claims | Require finite, non-negative realized `usage.cost`; record provenance; exact accounting applies only to valid completed responses. |
| Duplicate status ladder | Use one bounded typed-error table, then shared `classify_provider_status`; classify 408 as transient in the shared classifier. |

Cross-cutting warnings are also made testable below: exact model grammar,
pricing units and boundaries, hook signatures and order, bounded payload schemas,
`max_completion_tokens`, forward-compatible extension access, timeout and
cancellation limits, client ownership/lifecycle/concurrency, `max_llm_calls`
semantics, exact closure causality, one-source example selection, minimum-SDK
CI, generated Ring-1 API coverage, and preservation of Claude/BAML surfaces.

## Current State Analysis

- `activegraph/llm/provider.py:64-210` defines the frozen capability record and
  the complete keyword-only provider Protocol. No Protocol change is required.
- `activegraph/llm/openai.py:85-528` is the synchronous, lazy, injectable
  OpenAI adapter. Request policy, client construction, response validation, and
  cost provenance are currently embedded in `complete()` or `_client()` and
  need narrow hooks; copying `complete()` is forbidden.
- `activegraph/llm/wire.py:113-159` owns shared numeric status classification.
  Its current generic 4xx branch makes 408 terminal, contrary to timeout
  semantics; the shared classifier and its exhaustive matrix must change once.
- `activegraph/runtime/_live.py:198-314` returns every shipped provider that
  claims a model. Its live candidate order is Anthropic, OpenAI, Claude Code.
- Runtime validates provider capabilities at construction, registry setup, and
  late registration. A provider with estimated input counts cannot bind to a
  hard `max_cost_usd` budget.
- Runtime owns retries, tool execution, tool-loop continuation, cache writes,
  budget events, response events, and handler provenance. `max_llm_calls` is
  consumed once per behavior invocation, not once per provider turn or HTTP
  request.
- `activegraph/llm/__init__.py`, `pyproject.toml`, README/reference docs, tests,
  and the architecture spec already contain uncommitted Claude Code/BAML work.
  OpenRouter changes are additive semantic merges, never restoration from
  `HEAD` or wholesale regeneration from the stale original plan.
- The declared `openai>=1.0` floor is not sufficient for the current typed wire.
  `max_completion_tokens` first appears in the probed SDK at 1.45.0, while
  versions through 1.55.2 fail with the admitted `httpx==0.28.1`. Version
  1.55.3 is the first verified practical floor that supports the required
  request arguments, constructs with HTTPX 0.28.1, and preserves OpenRouter's
  extra response fields in the normal non-strict HTTP parse path.

## Locked Product and External Contracts

### Public construction and ownership

The exact public constructor is:

```python
def __init__(
    self,
    *,
    api_key_env: str = "OPENROUTER_API_KEY",
    client: Any = None,
    pricing: Optional[Mapping[str, Mapping[str, str]]] = None,
    native_structured_output_models: Optional[tuple[str, ...]] = None,
    base_url: str = "https://openrouter.ai/api/v1",
    app_url: Optional[str] = None,
    app_name: Optional[str] = None,
) -> None: ...
```

Construction performs no environment read, SDK import, network I/O, or client
creation. `pricing=None` and `pricing={}` both install an empty local table;
OpenAI's GPT defaults must never leak into this subclass. Nested pricing maps
and the native prefix tuple are copied. The reasoning-model prefix tuple is
fixed empty. Empty attribution strings are omitted. ActiveGraph passes
`base_url` exactly as supplied and does not silently normalize slashes.
Non-empty `app_url` maps exactly to the `HTTP-Referer` default header and
non-empty `app_name` maps exactly to `X-OpenRouter-Title`; values are not read
from any additional environment variable.

An injected client bypasses SDK import, key lookup, header construction,
`max_retries` configuration, and client caching; its retries, closing, and
thread-safety are caller-owned. An internal client is lazily created once and
cached for ordinary serial/synchronous Runtime use. The provider Protocol has
no `close`, so ActiveGraph does not promise deterministic cleanup; the SDK
client lives with the provider and may be reclaimed/closed by SDK lifecycle
behavior. The cache is not promised as a lock-protected concurrent singleton.

`timeout_seconds` is passed as the SDK call's per-HTTP-attempt `timeout`. It is
not an end-to-end Runtime deadline and supplies no cancellation token or
in-flight interruption. OpenRouter's server-side provider fallback occurs
inside one SDK request and is distinct from Runtime-owned retry attempts.

### Identity and model grammar

- Public import: `from activegraph.llm import OpenRouterProvider` only; no
  top-level `activegraph.OpenRouterProvider` export.
- Default model: `openrouter/free`.
- Project recognition regex:

  ```regex
  ^~?[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[a-z0-9]+(?:[._-][a-z0-9]+)*)?$
  ```

  EBNF: `model = ["~"], segment, "/", segment, [":", segment]`, where
  `segment = atom, {("." | "_" | "-"), atom}` and `atom` is one or more
  lowercase ASCII letters or digits.
- Accept examples: `openrouter/free`, `openai/gpt-4o-mini`,
  `anthropic/claude-3.5-sonnet:beta`, `~openai/gpt-latest`.
- Reject plain IDs, uppercase, whitespace, empty/trailing/consecutive
  punctuation groups, empty owner/slug/variant, multiple `:` variants, and
  extra `/` segments.
- This is ActiveGraph's bounded ownership policy, not a promise to recognize
  every future OpenRouter catalog identifier. A future form outside the regex is
  unclaimed by OpenRouter for ownership diagnostics; under the live Runtime's
  permissive rule it is still forwarded when no other shipped provider claims
  it. Recognition policy can be deliberately extended later without turning
  this feature into a model allowlist.

### Capabilities and hard budgets

The exact descriptor is:

```python
llm_capabilities = LLMProviderCapabilities(
    enforces_max_tokens=True,
    supports_sampling_controls=True,
    input_token_count="estimate",
    max_tool_calls_per_completion=None,
    requires_generation_control_acknowledgement=False,
)
```

The max-token and sampling claims depend on `require_parameters=true` being on
every request. Token counts remain local estimates. Therefore every
`LLMBehavior` binding between this provider and a Runtime with hard
`max_cost_usd` is rejected—whether the behavior exists at Runtime construction,
appears during registry initialization, or is registered late—before
`count_tokens`, `estimate_cost`, or SDK access. An empty Runtime has no behavior
to validate and may construct; non-cost budgets remain bindable.

### Request wire

Every OpenRouter call sends `model`, shared messages, per-attempt `timeout`,
`max_completion_tokens=int(max_tokens)`, `temperature=float(temperature)`, and
`extra_body={"provider": {"require_parameters": True}}`; it never sends the
deprecated `max_tokens`. `top_p` is sent only when `< 1.0`, matching existing
OpenAI behavior. Sanitized tools and native `response_format` are merged after
the request-policy hook without removing the required provider policy.

Native structured output is prompt mode by default. It is enabled only for
caller-supplied `native_structured_output_models` prefixes, and
`require_parameters=true` makes unsupported endpoints fail instead of silently
ignoring the schema. No OpenRouter-only reasoning parameter is added to the
shared Protocol.

### Pricing and realized cost

Constructor `pricing` is an ActiveGraph-owned mapping of model-family keys to
`{"input": <decimal string>, "output": <decimal string>}` in **USD per one
million tokens**. It is not OpenRouter Models API per-token
`prompt`/`completion` data. At construction, reject missing keys, booleans,
malformed values, negative values, NaN, and positive/negative infinity.

For estimation, remove one optional leading `~`, then choose the longest price
key for which the normalized model equals the key or the next character is one
of `-`, `.`, `_`, or `:`. Thus `openai/gpt-4` matches
`openai/gpt-4-mini` but not `openai/gpt-4o`; an exact longer key wins. The
normalized `openrouter/free` model and every recognized model ending `:free`
estimate to zero. Every unknown paid model estimates to `Decimal("Infinity")`,
including a zero-token request.

A completed response has no estimate fallback: `usage.cost` must exist and
convert via `Decimal(str(value))` to a finite, non-negative, non-boolean value.
Missing, null, boolean, malformed, negative, NaN, or infinite cost raises a
terminal `LLMBehaviorError(reason="llm.request_error")` so Runtime does not
retry an already completed, potentially billed response. Its bounded extras
are exactly `model`, `field="usage.cost"`, `value_type`, and optional
`finish_reason`. `value_type: str` is one of the fixed values `missing`, `null`,
`bool`, `int`, `float`, `str`, `decimal`, or `other`; `finish_reason`, when
present, is a scalar string truncated to 64 characters. No value or response
object is copied. Valid responses set
`provider_meta={"cost_source": "openrouter_usage"}`.

Because `llm.request_error` currently describes only rejected outbound 4xx
requests, this behavior also broadens that reason's shared prose to cover a
terminal unusable completed response envelope. The operator guidance must
distinguish request configuration from missing/invalid provider accounting, and
tests/docs must preserve both meanings; no eighth reason is invented solely for
OpenRouter.

Exact cost accounting is limited to completed responses with valid returned
usage. A failed, timed-out, cancelled, or transport-lost request may still be
billed without returning usage; Runtime records failed attempts as zero and
cannot enforce a hard monetary ceiling across such work. This is documented as
a limitation rather than disguised as an estimate.

### Success and error response compatibility

Ordinary success parsing continues to use the OpenAI SDK's canonical attribute
model shape. Only OpenRouter extension reads—choice/top-level error and
`usage.cost`—must accept both SDK attributes and mapping-shaped test/forward
compatibility inputs. This does not silently broaden every inherited success
field into a second response protocol.

Response validation order is canonical `choices[0].error`, then defensive
top-level `error`; choice-level wins if both exist. Each hop accepts mapping or
attribute form. A choice with `finish_reason="error"` but no error object is a
transient malformed-provider response. Validation runs immediately after the
SDK call and outside the broad SDK-exception catch, before text, partial
content, tools, schema parsing, usage, or cost extraction.

The validation helper is a flat detect → normalize → classify flow. It selects
at most one effective error, constructs one bounded payload, and raises once at
the end of the error branch; the no-error path returns directly without nested
policy ladders or mutation hidden in a control expression.

One module-level immutable `_OPENROUTER_ERROR_TYPE_REASONS` map classifies
recognized `error.metadata.error_type` values first. Its complete initial
contents are locked here and asserted by a table-driven test:

| ActiveGraph reason | Exact OpenRouter `error_type` strings |
| --- | --- |
| `llm.auth_error` | `authentication`, `permission_denied` |
| `llm.rate_limited` | `rate_limit_exceeded` |
| `llm.network_error` | `provider_overloaded`, `provider_unavailable`, `server`, `timeout`, `unmapped` |
| `llm.request_error` | `payment_required`, `context_length_exceeded`, `max_tokens_exceeded`, `token_limit_exceeded`, `string_too_long`, `invalid_request`, `invalid_prompt`, `not_found`, `precondition_failed`, `payload_too_large`, `unprocessable`, `content_policy_violation`, `refusal`, `invalid_image`, `image_too_large`, `image_too_small`, `unsupported_image_format`, `image_not_found`, `image_download_failed` |

Unknown types do not grow payloads and fall through to numeric status.

Numeric fallback calls `classify_provider_status` and contains no local
401/403/408/429/4xx/5xx ladder. The shared classifier is deliberately amended
so status 408 maps to `llm.network_error`; its exhaustive wire matrix proves
408, 409, auth, 429, other 4xx, 5xx, and missing-status behavior.
This same shared change handles SDK-raised 408 consistently. Unknown future
error types, union variants, fields, and status codes use the bounded typed-then-
numeric fallback and never crash while copying an arbitrary response.

In-band error `payload_extras` has exactly these keys and types:

- `model: str` (always present);
- `status_code: int | None` (always present after normalization);
- `error_type: str` when scalar and at most 128 characters;
- `provider_code: str` when `metadata.provider_code` is scalar, otherwise when
  `error.code` is scalar but cannot be losslessly normalized to an integer;
  the value is normalized and truncated to 128 characters;
- `finish_reason: str` when scalar and at most 64 characters.

No raw response, partial output, provider message, error mapping, metadata
mapping, headers, or exception object is retained. Error messages are stable
ActiveGraph prose and do not depend on volatile provider text. This schema is
specific to OpenRouter's in-band error body; the inherited SDK-exception
payload remains the separately characterized OpenAI wire contract.

## Protected Reuse Boundary

`OpenAIProvider` gains only these fixed, keyworded protected seams, each pinned
by direct base-characterization tests:

```python
def _sdk_client_kwargs(self, *, api_key: str) -> dict[str, Any]: ...

def _request_policy_kwargs(
    self,
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
) -> dict[str, Any]: ...

def _validate_response(self, raw: Any, *, model: str) -> None: ...

def _response_cost(
    self,
    raw: Any,
    *,
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> Decimal: ...

def _response_provider_meta(
    self,
    raw: Any,
    *,
    model: str,
) -> dict[str, Any]: ...
```

Base behavior is byte-compatible: empty SDK kwargs, current reasoning/non-
reasoning request policy, no-op validation, static estimated response cost, and
empty provider metadata. Provider/install labels may become protected class
attributes only to reuse the existing one-time tokenizer fallback without
copying its calculation or state.

The locked `complete()` order is:

1. Resolve the injected or lazy client.
2. Translate messages and build `model`, `messages`, and `timeout` kwargs.
3. Merge `_request_policy_kwargs`.
4. Merge sanitized tools.
5. Merge native response format when opted in.
6. Perform the SDK call inside the existing broad exception catch.
7. Stop latency accounting.
8. Call `_validate_response` outside the catch.
9. Extract text/tool calls and parse a non-tool structured result using current
   OpenAI behavior.
10. Extract usage and call `_response_cost` and `_response_provider_meta`.
11. Extract selected model/finish reason and return `LLMResponse`.

`_client()` retains injected-client, cache, lazy import, and environment order,
but constructs `OpenAI(**self._sdk_client_kwargs(api_key=api_key))`. OpenAI's
base empty mapping preserves its current SDK/environment behavior. OpenRouter's
override rejects an empty/whitespace `api_key` before SDK construction, then
supplies the exact key, exact `base_url`, `max_retries=0`, and only configured
`default_headers`. Base OpenAI retains its existing `None`-only environment
check. No subclass overrides `_client`, `complete`, or `_heuristic_count`.

## Testing Strategy

Each behavior follows RED → GREEN → REFACTOR. A RED test must fail because the
named production seam is absent or wrong, not because of an import typo, skipped
optional dependency, synthetic event injection, or test-only implementation.
Unit fakes remain useful for exhaustive mapping and boundary matrices, but real
SDK transport tests are the compatibility authority.

Use the existing registry-isolation fixture. Do not introduce sleeps, external
network calls, import-time credential reads, or blanket `importorskip`. Normal
CI installs `.[dev,openrouter]`; a distinct minimum lane pins
`openai==1.55.3` exactly.

## TDD Behavior 1 — Offline Identity, Constructor, Grammar, and Capabilities

### RED

Add `tests/test_llm_openrouter.py` cases that import from `activegraph.llm`,
construct with the exact signature while SDK import and environment access are
guarded, and assert no I/O. Table-test accepted/rejected model IDs, default
model, `~` normalization, native-prefix copying, empty reasoning defaults,
optional-header omission, pricing deep-copy, `None`/`{}` empty semantics, and
all invalid monetary scalars.

In `tests/test_llm_provider.py`, assert the complete capability descriptor and
`isinstance(OpenRouterProvider(), LLMProvider)`. In budget tests, bind with a
hard `max_cost_usd` at each live binding path, arranging for an LLM behavior to
be present at that path, and assert
`InvalidRuntimeConfiguration` before tokenizer, estimator, client, or SDK call;
show an empty Runtime alone does not fail and a non-cost budget still binds.

### GREEN

Create `activegraph/llm/openrouter.py` with the no-I/O constructor, constants,
regex, recognition/default/native methods, exact capability record, and owned
pricing validation. Export it only from `activegraph.llm`.

### REFACTOR

Keep grammar and monetary parsing helpers private, pure, bounded, and table-
driven. Do not modify the Protocol or add a provider registry.

## TDD Behavior 2 — Lazy Client, Retry, and Lifecycle Policy

### RED

Characterize every new base hook in `tests/test_llm_openai.py`. Prove current
OpenAI request kwargs, static cost, metadata, lazy env/import behavior, and
messages remain unchanged.

For OpenRouter, capture internal SDK constructor kwargs and assert exact key,
base URL, `max_retries=0`, conditional headers, one cached construction, empty-
key failure, and provider-specific missing-extra/tokenizer diagnostics. Assert
an injected client performs no import/env/configuration and is never closed or
mutated.

Use the genuine SDK with `httpx.MockTransport` returning retryable 429/408/5xx
responses. The internal provider path must make exactly one HTTP request per
`complete()` call. Scope that cardinality assertion away from arbitrary injected
clients.

### GREEN

Add `_sdk_client_kwargs` plus provider/install labels to `OpenAIProvider` and
route existing `_client()` through them. Implement OpenRouter's constructor
kwargs. Preserve the existing lazy/cached control flow.

### REFACTOR

Keep ownership and retry policy in one override; do not duplicate `_client()`.
Document serial cache, non-deterministic internal cleanup, caller-owned injected
clients, per-attempt timeout, and lack of cancellation.

## TDD Behavior 3 — Request Mapping and Real-SDK Successful Completion

### RED

Characterize the base request-policy hook for ordinary and reasoning OpenAI
models. For OpenRouter fake-client cases, assert every prompt, tools, and native
request uses `max_completion_tokens`, never `max_tokens`, always includes
`provider.require_parameters=true`, and forwards temperature/conditional
`top_p`, timeout, messages, tools, and response format exactly.

Add an unskipped real `openai.OpenAI` client over `httpx.MockTransport`. Feed a
literal HTTP 200 Chat Completion containing ordinary SDK fields plus
`usage.cost="0.001230"`. Drive the public provider and assert one request, URL,
JSON request policy, preserved SDK `usage.cost`, concrete selected model,
tokens, text/parsed output, finish reason, exact Decimal cost, and
`provider_meta={"cost_source": "openrouter_usage"}`.

### GREEN

Extract `_request_policy_kwargs`, `_validate_response`, `_response_cost`, and
`_response_provider_meta` with the locked order. Implement only the OpenRouter
deltas. Preserve inherited message, tool-name, schema, usage, and response
translation.

### REFACTOR

Share tiny mapping/attribute extension accessors between error and realized-
cost handling. Do not introduce a second full success parser or copy
`complete()`.

## TDD Behavior 4 — Estimates, Realized-Cost Failures, and Budget Semantics

### RED

Table-test zero-cost routing, recognized `:free`, boundary-aware longest-price
matching, the `gpt-4`/`gpt-4o` non-match, exact Decimal per-million arithmetic,
leading-`~` normalization, zero-token unknown paid models, and direct unknown
paid `Infinity`.

For realized responses, table-test missing, null, bool, malformed, negative,
NaN, and infinite `usage.cost`. Assert terminal `llm.request_error`, the exact
bounded accounting payload, no retry implication, and no false
`cost_source`. Reassert Runtime hard-cost binding rejection and zero calls to
`count_tokens`, `estimate_cost`, and SDK. Assert `max_llm_calls=1` permits the
single behavior invocation even when that invocation makes two tool-loop LLM
turns.

### GREEN

Implement normalized boundary matching, per-million estimates, strict realized
cost conversion, and provenance. Broaden and test the shared terminal
request-error prose for invalid completed envelopes without changing retry
classification. Do not add failed-attempt cost estimation to Runtime.

### REFACTOR

Use `Decimal` end-to-end and centralize finite/non-negative/non-bool validation.
Keep direct estimation, binding compatibility, realized accounting, and
unobservable failed charges as four explicitly distinct concepts.

## TDD Behavior 5 — Structured Output, Tools, and In-Band/SDK Errors

### RED

Retain inherited tests for prompt/native parsing, dotted tool-name restoration,
and no provider-side tool execution. Add tables for mapping and attribute error
shapes, choice-first precedence, defensive top-level fallback, finish-error
without an object, every typed category, unknown typed fallback, numeric status
fallback, malformed fields, and exact payload keys/types/length bounds.

Through the real 1.55.3 SDK transport, return HTTP 200 with partial content,
`finish_reason="error"`, and `choices[0].error`. Assert the SDK preserves extra
fields; provider validation wins before partial/schema/cost parsing; typed error
wins over numeric status; exactly one request occurs. Add a real top-level error
fallback response. For SDK-raised and in-band 408, assert transient
`llm.network_error`.

Extend `tests/test_llm_wire.py`'s exhaustive shared matrix with 408 while
retaining 409/auth/429/other-4xx/5xx/`None` cases. Mapping/attribute errors with
string, boolean, or otherwise malformed status values are normalized to `None`
and tested in OpenRouter before calling the shared `Optional[int]` classifier;
provider tests sample but do not copy the valid numeric matrix.

### GREEN

Implement choice-first `_validate_response`, the immutable typed map, bounded
normalizers, and one call to shared numeric classification. Amend the shared
classifier for 408 once. Raise typed `LLMBehaviorError` outside the SDK catch so
it is not rewrapped.

### REFACTOR

Keep messages stable, helpers private, tables exhaustive, and forward fallbacks
bounded. No raw in-band provider prose or response objects enter event payloads.

## TDD Behavior 6 — Four-Provider Ownership and Blocking Runtime Closure

### Classification

This is a **blocking** closure because the change crosses public export,
provider implementation, shipped-owner diagnostics, Runtime dispatch, tool
execution, event causality, and handler graph effects.

| Closure element | Locked choice |
| --- | --- |
| Source | Registered seed/tool/LLM behaviors, a fresh public `Graph`, and only an injected fake at the external HTTP/SDK boundary. |
| Trigger | One synchronous public `Runtime.run_goal` call. |
| Driver | None; the synchronous Runtime call drives provider turns, tool execution, events, and handler completion. |
| Observation | Public graph reads for the handler effect plus `Graph.events` for the filtered connector trace. |
| Forbidden shortcuts | Direct provider/private Runtime/handler/emitter calls, mocked providers or event sinks, seeded result events, sleeps, polling, or fabricated graph state. |

### RED

Extend the shipped-provider candidate test to exact order
`[AnthropicProvider, OpenAIProvider, ClaudeCodeProvider, OpenRouterProvider]`.
Retain all claimants rather than first match, preserve the existing
Anthropic/Claude overlap case, and add OpenRouter recognized/mismatch/plural
diagnostic cases. Bare candidate construction must remain no-I/O and exceptions
from one candidate must not hide later matches.

Build the closure from registered seed, tool, and LLM behaviors plus a fresh
public `Graph`; the only fake is the injected external Chat Completions client.
Trigger only `Runtime.run_goal`. Do not call the provider, private Runtime
methods, handler, event sink, or emitter directly; do not seed outcome events,
mock a provider, sleep, or poll.

The first RED failure must be at a named production seam—normally the missing
public provider/export or shipped-owner candidate—not in test scaffolding.

The fake returns a tool call then a final result. Observe handler-created graph
state via public graph reads and filter `Graph.events` to the four connector
types. Destructure exactly:

```text
llm.requested(turn_index=0)
llm.responded(turn_index=0)
tool.requested
tool.responded
llm.requested(turn_index=1)
llm.responded(turn_index=1)
```

Assert exactly two external `create` calls; exactly two request/response events;
exactly one tool pair; request/response turn indices `[0, 1]`; no `error` value
on successful responses; `req0.caused_by == triggering_event.id`;
`resp0.caused_by == req0.id`;
`tool_req.caused_by == req0.id`; `tool_resp.caused_by == tool_req.id`;
`req1.caused_by == req0.id`; and `resp1.caused_by == req1.id`. Assert request 2
contains the assistant tool-call echo and a matching tool-result id. Assert the
handler-created object's provenance names `req1.id` as the successful LLM
request and retains contributing tool request provenance. With
`max_llm_calls=1`, the two provider turns still complete because the budget
counts one behavior invocation.

### GREEN

Lazily append OpenRouter to `_which_shipped_provider_claims` after Claude Code,
preserving its list return, all-match loop, plural diagnostics, and singular
compatibility keys. Wire the public export and run through the real Runtime
chain.

### REFACTOR

Keep candidate import lazy and closure assertions about this connector only;
do not assert unrelated lifecycle/idle event order.

## TDD Behavior 7 — Packaging, Example, Docs, Spec, and Generated Coverage

### RED

Add TOML assertions for exactly:

```text
openrouter = ["openai>=1.55.3", "tiktoken>=0.7", "pydantic>=2"]
```

Raise every existing OpenAI floor in `[openai]`, `[llm]`, and `[all]` to
`openai>=1.55.3`; do not duplicate distribution names in aggregate extras.
Assert the BAML optional-dependency block/value remains unchanged and preserve
Claude's exact SDK pin, `anyio`/`sniffio` bounds, test dependencies, and
`claude_code_live` marker. Correct the stale TOML comment that says Claude is
outside aggregates; do not mutate the arrays to match that obsolete comment.

In `examples/babyagi.py`, assert one bounded `PROVIDER_SPECS` mapping supplies
factory and env name for `anthropic`, `openai`, and `openrouter`. Derive direct
validation, construction, CLI choices, and env lookup from it; an unknown direct
argument raises clearly rather than falling through to OpenAI. Do not add Claude
Code to this deterministic API-key example.

Add semantic docs/spec assertions where stable and require current-state prose
to say four providers while historical release prose keeps its chronology.
Require generated coverage to contain `activegraph.llm.openrouter`,
`OpenRouterProvider`, and the concurrent Claude/capability/status symbols.

### GREEN

- Update `pyproject.toml` and `.github/workflows/tests.yml`; normal CI installs
  `.[dev,openrouter]`. Add an exact-minimum lane that installs
  `.[dev,openrouter]` plus `openai==1.55.3`, then runs real-SDK tests unskipped.
- Add OpenRouter install, key, default, grammar, request-enforcement, cost,
  timeout/retry/lifecycle, and hard-budget limitations to `README.md` and
  `docs/reference/llm-providers.md` while preserving Claude's authentication and
  capability differences.
- Correct `docs/reference/errors/missing-optional-dependency.md`, the stale
  provider description in `mkdocs.yml`, and BabyAGI script/README.
- Semantically merge four-provider/all-match ownership, Claude capability
  exception, and OpenRouter wire/cost/error policy into `specs/08-llm.md`.
- Add current OpenRouter entries to `CHANGELOG.md` and `CONTRACT.md` as additive
  edits; never rewrite historical one/two/three-provider entries or concurrent
  Claude records.
- Run `scripts/audit_docstrings.py`; do not hand-edit
  `docs/reference/api/COVERAGE_REPORT.md`. OpenRouter is Ring 1 because it is in
  `activegraph.llm.__all__`, not top-level `activegraph.__all__`.

### REFACTOR

Remove repeated provider branches/enumerations in the example only. Do not add
a global provider registry, pull BAML implementation into scope, expose a
top-level provider, or promise all four providers are compatible with BabyAGI's
deterministic defaults.

## File-Level Change Inventory

| File | Planned change |
| --- | --- |
| `activegraph/llm/openrouter.py` | New provider, constructor/identity/capabilities, request/client deltas, pricing, validation, cost provenance. |
| `activegraph/llm/openai.py` | Narrow characterized protected hooks and provider/install labels; preserve base behavior. |
| `activegraph/llm/wire.py` | Shared 408 timeout classification only. |
| `activegraph/llm/errors.py` | Broaden terminal request-error prose to cover an unusable completed provider envelope as well as invalid outbound requests. |
| `activegraph/llm/__init__.py` | Add fourth-provider import/export/doc prose; preserve Claude/capability exports. |
| `activegraph/runtime/_live.py` | Lazy fourth candidate and current capability remediation prose; preserve all matches. |
| `tests/test_llm_openrouter.py` | Full unit, real-SDK, budgeting, and blocking closure suite. |
| `tests/test_llm_openai.py` | Base-hook characterization and regression. |
| `tests/test_llm_provider.py` | Exact OpenRouter capability/Protocol assertions. |
| `tests/test_llm_default_model.py` | Fourth-provider order, grammar, overlap, and plural diagnostics. |
| `tests/test_llm_budget.py` | Three binding-path hard-cost rejection and non-cost compatibility. |
| `tests/test_llm_wire.py` | Exhaustive shared classifier matrix including 408. |
| `tests/test_llm_failure.py` | Characterize both outbound-request and invalid-completed-envelope request-error prose. |
| `pyproject.toml` | New exact extra, consistent SDK floor, corrected stale comment; preserve Claude/BAML blocks. |
| `.github/workflows/tests.yml` | Normal OpenRouter install and exact-minimum real-SDK lane. |
| `examples/babyagi.py`, `examples/babyagi/README.md` | Single three-entry API-key provider spec and usage. |
| `README.md`, `docs/reference/llm-providers.md` | Four-provider setup and complete limitations. |
| `docs/reference/errors/llm-behavior-error.md` | Document the broadened terminal request-error contract without dropping concurrent edits. |
| `docs/reference/errors/missing-optional-dependency.md`, `mkdocs.yml` | Accurate extra/current-provider prose. |
| `specs/08-llm.md` | Semantic four-provider architecture merge; preserve Claude/BAML material. |
| `CHANGELOG.md`, `CONTRACT.md` | Additive current OpenRouter records; preserve historical chronology and concurrent Claude records semantically. |
| `docs/reference/api/COVERAGE_REPORT.md` | Generated Ring-1 output only. |

`activegraph/llm/provider.py` receives the stale current-provider/public-surface
docstring correction while preserving its capability prose; its Protocol and
capability types do not change. No other file is implicitly authorized.

## Verification Gates

Run the focused gates after each behavior, then the complete sequence:

```bash
.venv/bin/python -m pytest -q \
  tests/test_llm_openrouter.py \
  tests/test_llm_openai.py \
  tests/test_llm_default_model.py \
  tests/test_llm_provider.py \
  tests/test_llm_budget.py \
  tests/test_llm_wire.py \
  tests/test_llm_native_structured_output.py \
  tests/test_llm_behavior.py \
  tests/test_llm_failure.py

# In an isolated environment/CI job; no importorskip:
python -m pip install -e ".[dev,openrouter]" "openai==1.55.3"
python -m pytest -q tests/test_llm_openrouter.py -k real_sdk

.venv/bin/python scripts/audit_docstrings.py
rg -n 'activegraph\.llm\.(claude_code|openrouter)|ClaudeCodeProvider|OpenRouterProvider|LLMProviderCapabilities|classify_provider_status' \
  docs/reference/api/COVERAGE_REPORT.md
.venv/bin/python scripts/gate_docstrings.py
.venv/bin/python examples/babyagi.py --help
.venv/bin/python -m build
.venv/bin/mkdocs build --strict
.venv/bin/python -m pytest -m 'not slow' -q
git diff --check
```

The exact-minimum lane is a release gate, not an optional local convenience.
No test may silently skip because OpenAI/tiktoken is absent. The generated API
report must contain both OpenRouter and the already-live Claude/capability/status
symbols, because module import failures can otherwise make the audit silently
omit a Ring-1 module.

## Implementation Status — 2026-08-11

- [x] Behavior 1 — offline identity, constructor, grammar, and capabilities.
- [x] Behavior 2 — lazy client, retry, and lifecycle policy.
- [x] Behavior 3 — request mapping and real-SDK successful completion.
- [x] Behavior 4 — estimates, realized-cost failures, and invocation-budget semantics.
- [x] Behavior 5 — structured output, tools, and in-band/SDK errors.
- [x] Behavior 6 — four-provider ownership and blocking Runtime closure.
- [x] Behavior 7 — packaging, example, docs, spec, and generated coverage.

Verification evidence: the focused provider/runtime matrix passes 274 tests;
the literal-HTTP OpenAI SDK 1.55.3 lane passes 6 tests; the complete non-slow
suite passes 1,290 tests with 41 skips and 18 deselections; configured mypy,
package build, strict MkDocs build, BabyAGI help, compileall, and
`git diff --check` pass. Generated coverage contains OpenRouter, Claude Code,
the capability descriptor, and shared status classifier. The repository-wide
docstring gate separately reports the unrelated pre-existing public
`activegraph.DeliveryMode` alias gap, tracked as `AF-h4v`; it does not omit any
artifact required by this plan.

## Completion Contract

Implementation is complete only when all seven behavior slices are green and
the evidence shows:

1. Offline construction, exact grammar/signature/copy semantics, and exact
   capabilities.
2. Internal `max_retries=0`, one transport request per internal attempt, and
   explicit injected/internal ownership and timeout limits.
3. `max_completion_tokens` plus `require_parameters=true` on every request and
   literal-HTTP success deserialization at `openai==1.55.3`.
4. Boundary-safe estimates, binding-time hard-cost rejection, strict realized
   cost/provenance, and qualified failed-charge limits.
5. Choice-first typed errors, bounded payloads, shared 408/numeric fallback,
   and literal-HTTP error deserialization before partial parsing.
6. Four-provider all-match ownership and the exact six-event blocking closure,
   causal IDs, external-call count, handler effect, and invocation-budget rule.
7. Exact extras/CI, one-source BabyAGI mapping, four-provider docs/spec, Ring-1
   generated coverage, semantic preservation of concurrent Claude work, and
   byte/value preservation of the BAML block and locked dependency entries.

No hard-cost-ceiling claim, injected-client retry promise, end-to-end deadline,
concurrent-client guarantee, dynamic OpenRouter model catalog, top-level export,
or provider registry is implied by completion.

## References

- OpenRouter OpenAI SDK integration: <https://openrouter.ai/docs/guides/community/openai-sdk>
- OpenRouter provider routing and `require_parameters`: <https://openrouter.ai/docs/guides/routing/provider-selection>
- OpenRouter errors: <https://openrouter.ai/docs/api/reference/errors-and-debugging>
- OpenRouter usage accounting: <https://openrouter.ai/docs/cookbook/administration/usage-accounting>
- OpenRouter structured outputs: <https://openrouter.ai/docs/guides/features/structured-outputs>
- OpenRouter free router: <https://openrouter.ai/docs/guides/routing/routers/free-router>
- OpenAI Python retries/timeouts: <https://github.com/openai/openai-python#retries>
