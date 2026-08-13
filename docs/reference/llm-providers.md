# LLM providers

Active Graph ships four concrete `LLMProvider` implementations:
`AnthropicProvider`, `OpenAIProvider`, `ClaudeCodeProvider`, and
`OpenRouterProvider`. All expose the same Protocol surface —
`complete()`, `estimate_cost()`, `count_tokens()` — so a runtime
swapping the injected provider doesn't reshape any call site.
`ClaudeCodeProvider` is Protocol-conformant in shape too, but is a
**capability-limited provider**, enforced by `Runtime` via the
additive `LLMProviderCapabilities` descriptor — see its own section
below before using it. OpenRouter has estimated input counts and a
corresponding hard-cost-budget restriction, also documented below.

```python
from activegraph import Graph, Runtime
from activegraph.llm import AnthropicProvider, OpenAIProvider, OpenRouterProvider

rt = Runtime(Graph(), llm_provider=AnthropicProvider())  # or:
rt = Runtime(Graph(), llm_provider=OpenAIProvider())
rt = Runtime(Graph(), llm_provider=OpenRouterProvider())
```

## Installing

Pick the extra(s) you need. They install cleanly and don't conflict.

```bash
pip install "activegraph[anthropic]"    # AnthropicProvider only
pip install "activegraph[openai]"       # OpenAIProvider only
pip install "activegraph[openrouter]"   # OpenRouterProvider only
pip install "activegraph[claude-code]"  # ClaudeCodeProvider only
pip install "activegraph[llm]"          # all four shipped providers
```

The `[openai]` and `[openrouter]` extras pull in `tiktoken` so client-side token
counting is accurate; see the count_tokens row below for what
happens when tiktoken is missing. `claude-agent-sdk` is exact-pinned
(`==0.2.135`) wherever it appears — `[claude-code]`, `[llm]`, and
`[all]` all carry the identical exact pin, so there's exactly one
supported version regardless of which extra installed it.
`ClaudeCodeProvider` still refuses to run without an explicit
`allow_unenforced_generation_controls=True` acknowledgement (Runtime
capability-binding validation) regardless of whether its SDK happens
to be installed.

## API keys

The three API-key providers read their key from the
environment, never from code or a checked-in config:

```bash
export ANTHROPIC_API_KEY='...'
export OPENAI_API_KEY='...'
export OPENROUTER_API_KEY='...'
```

Override the env-var name via the `api_key_env=` constructor kwarg
if you need a different one (per-environment key rotation, for
example). `ClaudeCodeProvider` deliberately does **not** take an
`api_key_env=` kwarg — it bills against a Claude subscription instead
of a metered API key; see its own section below.

## Default model resolution

Each provider declares a `default_model` — the model name the
runtime uses when an `@llm_behavior` doesn't pin one explicitly:

```python
@llm_behavior(name="extractor", output_schema=Claim)
def extractor(event, graph, ctx, llm_output):
    ...
```

With `AnthropicProvider()` this resolves to `"claude-sonnet-4-5"`;
with `OpenAIProvider()` it resolves to `"gpt-4o-mini"`; with
`OpenRouterProvider()` it resolves to `"openrouter/free"`. The
runtime stamps the resolved name onto the behavior at
registration time (inside `Runtime(...)`'s first registry
materialization), so swapping providers is a one-line change:

```python
rt = Runtime(Graph(), llm_provider=OpenAIProvider())  # gpt-4o-mini
rt = Runtime(Graph(), llm_provider=AnthropicProvider())  # claude-sonnet-4-5
rt = Runtime(Graph(), llm_provider=OpenRouterProvider())  # openrouter/free
```

Pass `model="..."` on the decorator to override:

```python
@llm_behavior(name="extractor", output_schema=Claim, model="gpt-4o")
def extractor(event, graph, ctx, llm_output):
    ...
```

## Cross-provider model-name validation

When a behavior pins `model="..."` explicitly, the runtime checks
the name against each shipped provider's `recognizes_model()`:

| Provider | Recognized prefixes |
| --- | --- |
| `AnthropicProvider` | `claude-` |
| `OpenAIProvider` | `gpt-`, `o1-`, `o3-`, `o4-` |
| `ClaudeCodeProvider` | `claude-` |
| `OpenRouterProvider` | bounded `owner/model[:variant]` grammar; optional leading `~` |

If the configured provider doesn't recognize the name but a
*different* shipped provider does, the runtime raises
`InvalidRuntimeConfiguration` at registration time with a
structured error naming both providers. This catches the most
common shape of provider-swap misconfiguration — an `@llm_behavior`
copied from an Anthropic example into an OpenAI-configured runtime
— before the first network call, instead of letting the provider
404 silently.

Names no shipped provider recognizes (custom deployments, OpenAI
fine-tunes like `ft:gpt-4o-mini:org::id`, internal naming
conventions) pass through silently. The validation is permissive
by design: only *recognized* cross-provider mismatches fire.

## Side-by-side

| Aspect | `AnthropicProvider` | `OpenAIProvider` | `OpenRouterProvider` |
| --- | --- | --- | --- |
| `default_model` | `"claude-sonnet-4-5"` | `"gpt-4o-mini"` | `"openrouter/free"` |
| Recognized model families | `claude-*` | `gpt-*`, `o1-*`, `o3-*`, `o4-*` | bounded `owner/model[:variant]` grammar |
| API key env | `ANTHROPIC_API_KEY` | `OPENAI_API_KEY` | `OPENROUTER_API_KEY` |
| SDK | `anthropic>=0.40` | `openai>=1.55.3` | `openai>=1.55.3` against OpenRouter's compatible endpoint |
| Structured output | Prompt mode by default; native Messages `output_config` for supported Claude families | Prompt mode by default; native strict Chat Completions JSON schema for configured families | Prompt mode by default; native mode only for caller-supplied prefixes and always requires routed parameter support |
| `count_tokens()` | Provider-side official count | Local `tiktoken`, chars/4 fallback | Local `tiktoken`, chars/4 fallback; declared `"estimate"` |
| Tool use and names | Supported; dotted names rewritten on the wire | Supported; dotted names rewritten on the wire | Same inherited Chat Completions tool wire; parameter support required |
| Error mapping | Shared 429/auth/4xx/5xx taxonomy | Shared SDK-exception taxonomy | Choice-level typed in-band errors first, then shared numeric taxonomy; 408 is transient |
| Pricing | Family-prefix estimate | Family-prefix estimate | Optional ActiveGraph-owned per-million table for direct estimates; valid completed calls use returned `usage.cost` exactly |

## `OpenRouterProvider` — routed OpenAI-compatible completions

`OpenRouterProvider` is directly injectable and reuses the OpenAI Chat
Completions translation without copying its turn implementation:

```python
from activegraph import Graph, Runtime
from activegraph.llm import OpenRouterProvider

rt = Runtime(Graph(), llm_provider=OpenRouterProvider())
```

Construction is offline. The default endpoint is
`https://openrouter.ai/api/v1`; the provider lazily creates and caches
one ordinary synchronous SDK client, with `max_retries=0` so Runtime is
the only retry owner. Optional `app_url=` / `app_name=` become
`HTTP-Referer` / `X-OpenRouter-Title` headers. An injected `client=`
bypasses SDK import, environment access, header/retry configuration,
and caching; its retries, thread safety, closing, and lifecycle are the
caller's responsibility. The Protocol has no `close()` method, so an
internally owned client has no deterministic framework cleanup promise.

Every request sends `max_completion_tokens`, sampling controls,
per-attempt `timeout`, and
`provider.require_parameters=true`. Unsupported routed endpoints fail
instead of silently ignoring generation or schema parameters. The
timeout is one SDK HTTP-attempt timeout, not an end-to-end Runtime
deadline, cancellation token, or guarantee that server work stopped.
OpenRouter's own provider fallback occurs inside that single request.

Active Graph's ownership grammar is deliberately bounded:

```text
^~?[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[a-z0-9]+(?:[._-][a-z0-9]+)*)?$
```

Examples: `openrouter/free`, `openai/gpt-4o-mini`,
`anthropic/claude-3.5-sonnet:beta`, `~openai/gpt-latest`. This drives
cross-provider diagnostics; it is not an allowlist, and an unclaimed
future model form is still forwarded under Runtime's permissive rule.

`pricing=` is an optional mapping from model-family key to
`{"input": decimal_string, "output": decimal_string}` in USD per one
million tokens. Estimation strips one leading `~`, uses the longest
boundary-safe match, returns zero for `openrouter/free` and recognized
`:free` models, and `Decimal("Infinity")` for unknown paid models.
Completed responses do not fall back to estimates: `usage.cost` must be
finite, non-negative, and non-boolean, and is recorded with
`provider_meta={"cost_source": "openrouter_usage"}`. Invalid or missing
cost is terminal `llm.request_error` because retrying a potentially
billed completed call could pay twice.

The provider declares estimated input counts. Consequently, Runtime
rejects every binding to a hard `max_cost_usd` budget before tokenizing
or making SDK calls. Other budgets, including `max_llm_calls`, remain
supported. Exact accounting is limited to valid completed responses:
failed, timed-out, cancelled, or transport-lost work can be billed
without a returned usage envelope, and Runtime cannot invent or enforce
a hard monetary ceiling across those unobservable charges.

## `ClaudeCodeProvider` — Claude subscription billing (capability-limited)

CONTRACT v1.11 #1. `ClaudeCodeProvider` is a third `LLMProvider`, backed
by the **Claude Agent SDK** (`claude-agent-sdk`, pinned to exactly
`0.2.135` with its bundled CLI `2.1.227` — see "Exact SDK
compatibility" below) instead of a direct API call. Its entire
purpose: bill LLM calls against the caller's Claude
Max/Pro/Team/Enterprise **subscription** instead of `ANTHROPIC_API_KEY`
metered billing.

```bash
pip install "activegraph[claude-code]"
claude login   # or otherwise ensure an active subscription session
```

```python
from activegraph import Graph, Runtime
from activegraph.llm import ClaudeCodeProvider

rt = Runtime(
    Graph(),
    llm_provider=ClaudeCodeProvider(allow_unenforced_generation_controls=True),
)
```

**This is a capability-limited provider — `Runtime` enforces the gap,
not a per-call flag.** `ClaudeCodeProvider.llm_capabilities`
(`LLMProviderCapabilities`) declares:

```python
LLMProviderCapabilities(
    enforces_max_tokens=False,
    supports_sampling_controls=False,
    input_token_count="estimate",
    max_tool_calls_per_completion=1,
    requires_generation_control_acknowledgement=True,
)
```

Before running any `@llm_behavior`, `Runtime` reads this descriptor —
at construction, at `_ensure_registry()`, and when a new behavior is
registered against an already-live Runtime — and refuses the binding
(`InvalidRuntimeConfiguration`, before any I/O) when:

- the provider requires acknowledgement and
  `allow_unenforced_generation_controls=True` wasn't passed to its
  constructor (the SDK has no `max_tokens`/`temperature`/`top_p`
  fields at all — this is the explicit opt-in that a caller accepts
  that gap);
- any bound `@llm_behavior(deterministic=True)` is present — the SDK
  has no sampling controls, so `Runtime` refuses the binding rather
  than silently producing non-deterministic output that claims
  determinism; or
- `budget={"max_cost_usd": ...}` is set — the pre-call budget gate
  needs both an official (non-estimated) token count and an
  enforceable output bound, and this provider has neither.

There is no `deterministic=` parameter on `complete()` and no `tools=`
parameter on `count_tokens()` to guard per-call — the locked
`LLMProvider` Protocol signatures never grow provider-specific
keywords; capability limits are declared data `Runtime` checks before
a provider is ever called. Tool-call cardinality is **0-or-1 per
`complete()`**, never a batch — a task needing several tools takes
several `Runtime`-driven turns. `LLMResponse.cost_usd` is sourced from
the SDK's own list-rate `total_cost_usd`/`model_usage` fields — for
subscribers this is a **standard-rate accounting estimate**, never
your actual subscription draw or remaining credit meter.
`model_usage[*]["provider"] == "firstParty"` is evidence the request
didn't use a *named cloud backend*; it is **not** proof of
subscription billing.

**Scope.** Intended for a caller's own local/ordinary use, matching
Anthropic's published terms for Agent SDK/`claude -p` subscription
usage. Not for routing third-party/hosted users through one
subscription's credentials.

**Auth is best-effort, not proof.** `reject_metered_env_auth=True`
(the default) refuses to run — terminal `llm.auth_error`, before any
subprocess — when `ANTHROPIC_API_KEY`, a Bedrock/Vertex/Foundry/Mantle/
AWS cloud-credential selector, or an `ANTHROPIC_*_BASE_URL` routing
override is set; those outrank subscription OAuth per the CLI's
documented precedence in non-interactive mode:

```bash
unset ANTHROPIC_API_KEY   # let ClaudeCodeProvider bill against your subscription
```

Pass `reject_metered_env_auth=False` to opt out and allow one of those
credential sources instead — nested-session/isolation env vars
(`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, ...) are **always** rejected
regardless of that flag (terminal `llm.request_error`) since an active
one changes SDK/CLI behavior this provider's isolation contract can't
reconcile. `CLAUDE_CODE_OAUTH_TOKEN` is never rejected — official
precedence defines it as subscription OAuth ahead of saved `/login`
credentials. `setting_sources=[]` on every call does **not** suppress
managed policy or global CLI configuration — a stated limitation, not
a promise the SDK can't keep.

**Isolation.** Every call constructs a fresh `ClaudeAgentOptions` and a
stateless `query()` — no session reuse or persistence, no ambient
skills/plugins/subagents, a private empty temp working directory alive
only through the call, and a hard-coded `permission_mode="dontAsk"`
(deny anything not pre-approved by the tool allow-list, never prompt).

**What still works.** `tools=` tool-use (via an exactly-anchored
`PreToolUse`-defer hook per tool — never a catch-all matcher),
structured output in both prompt and native mode, the same 7
`LLMBehaviorError` reason codes the other shipped providers use, and full
`Runtime` cache/fork/replay compatibility: `complete()` is a pure
function of exactly what the runtime's per-turn cache hashes, so a
cache hit or an unchanged `Runtime.fork(..., replay_llm_cache=True)`
never calls this provider at all, identical to the other providers.

**Multi-turn tool continuation.** The SDK takes a single `prompt: str`,
not a `messages[]` array, so each live `complete()` call flattens the
entire `messages` history into one canonical, versioned JSON transcript
(`activegraph-transcript-json-v1`) sent as a single new turn to a
fresh, non-resumed SDK call — the only mechanism verified to always
produce exactly one clean response for a real multi-turn conversation.
This is lossy (the model reads prose describing prior turns rather
than native content blocks) but deliberate; see
`activegraph/llm/claude_code.py`'s module docstring for the
alternatives that were tried and rejected.

**Exact SDK compatibility.** The only supported pair is
`claude-agent-sdk==0.2.135` with its own bundled `claude` CLI
`2.1.227` — resolved and verified internally (never a caller override,
never a system-`PATH` fallback). A missing/wrong-version SDK or
missing bundled binary is a terminal `llm.request_error` naming the
install command. Upgrading the pinned version is a deliberate
compatibility change, gated by this provider's full fake and live test
suites — not a silent version-range widening.

**Testing.** `ClaudeCodeProvider(_sdk_loader=<fake>)` is the test seam
— a zero-arg callable returning a `_SDKBindings` instance bundling
every SDK symbol the provider touches (options, message/exception
classes, hooks, MCP tool factories), constructed from real
`claude_agent_sdk` classes in tests, not hand-rolled doubles. A
separate, `claude_code_live`-marked suite
(`tests/test_llm_claude_code_live.py`, gated on
`ACTIVEGRAPH_TEST_CLAUDE_CODE_LIVE=1`) exercises the real CLI and real
subscription auth; it never runs in ordinary CI and is a required
pre-release gate, not merely supplementary.

## Native structured output (opt-in)

CONTRACT v1.3 #1. With `Runtime(native_structured_output=True)`, the
runtime resolves a structured-output mode per behavior at
registration time: native constrained decoding when the provider
supports it for the resolved model **and** the behavior's
`output_schema` fits the native subset (every field required, no
numeric/string constraint keywords, no recursion); the
prompt-embedded path otherwise. Nothing changes at the
`@llm_behavior` surface — the schema stays `output_schema=`.

Three things to know before flipping the flag:

- **Prompt hashes change.** Native mode drops the schema block from
  the system prompt and adds a mode field to the hash input, so LLM
  caches recorded in prompt mode won't be hit, and
  `replay_strict=True` will (correctly) raise
  `ReplayDivergenceError` replaying a log recorded under the other
  mode. Match the flag to how the log was recorded.
- **Fallback is silent but audited.** A behavior that can't go
  native (provider capability, model family, or schema subset) uses
  the prompt path; the resolved mode is recorded on every
  `llm.requested` event's `structured_output_mode` payload field.
- **Validation doesn't move.** Responses still flow through
  `parse_structured_response`, so `llm.parse_error` /
  `llm.schema_violation` semantics are identical in both modes.

`RecordedLLMProvider` replays whatever mode the fixture was recorded
in — construct it with `structured_output_mode="native"` to serve
native-mode fixtures (the default `"prompt"` keeps every pre-v1.3
fixture reachable unchanged).

## Embedding providers (v1.3; recorded runtime path in v1.8)

`EmbeddingProvider` is the runtime's second provider seam, next to
`LLMProvider`. Memory and retrieval capabilities still live in packs,
but calls now go through `Runtime.embed` or the packs-facing `ctx.embed`
so external I/O is recorded and replayable:

```python
from activegraph import Runtime
from activegraph.llm import EmbeddingProvider

class MyEmbedder:                      # any object with the Protocol shape
    default_model = "text-embedding-3-small"
    def embed(self, *, texts, model):
        ...                            # call your embedding API
        return vectors                 # one list[float] per input text

rt = Runtime(graph, embedding_provider=MyEmbedder())
vectors = rt.embed(["first document", "second document"])

# Inside a behavior or pack:
# vectors = ctx.embed(texts, model="text-embedding-3-small")
```

Forks inherit the parent's embedding provider (override per-fork with
`fork(embedding_provider=...)`); `Runtime.load(...,
embedding_provider=...)` wires one at load time. Set
`replay_embedding_cache=True` on load/fork to hydrate
`EmbeddingCache` from recorded `embedding.responded` events. Strict
replay enables the cache automatically, verifies the request-hash
sequence, and never calls the provider.

`embedding.requested` records the model and a content hash, not the
source text. `embedding.responded` records validated ordered vectors.
Calling `rt.embedding_provider.embed(...)` directly remains possible
for compatibility but bypasses both events and the cache, so it
forfeits ActiveGraph replay guarantees.

The runtime ships exactly one implementation:
`HashEmbeddingProvider`, a deterministic, dependency-free test double
(token-hash buckets, L2-normalized). Its vectors reflect token
overlap, not semantics — use it to test embedding plumbing offline;
wire a real provider (OpenAI embeddings, Voyage, a local model) for
retrieval quality. Real embedding implementations are deliberately
not shipped in the runtime: no network dependencies, no API keys.

## Mixing with [`RecordedLLMProvider`](api/index.md)

The fixture-backed provider is provider-agnostic: fixtures are keyed
by prompt-content hash, and the model name (`claude-…` or `gpt-…`)
is part of the hash input. Fixtures recorded against one provider
replay against `RecordedLLMProvider` regardless of which live
provider you switch to next.

```python
from activegraph.llm import RecordingLLMProvider, OpenAIProvider

inner = OpenAIProvider()
provider = RecordingLLMProvider(inner, fixtures_dir="tests/fixtures/llm")
```

`RecordingLLMProvider` wraps any concrete provider the same way.
Record once against a live key, commit the fixtures, run tests
against `RecordedLLMProvider` thereafter.

## Writing a custom provider

`LLMProvider` is a runtime-checkable `Protocol`. Any class with the
three methods is a provider — no inheritance required, no
registration step:

```python
from decimal import Decimal
from activegraph.llm import LLMMessage, LLMResponse, LLMProvider

class MyProvider:
    default_model = "my-model-name"   # v1.0.2 #1 — used when @llm_behavior omits model=

    def complete(self, *, system, messages, model, max_tokens,
                 temperature, top_p, output_schema, timeout_seconds,
                 tools=None, structured_output_mode="prompt") -> LLMResponse:
        ...

    def estimate_cost(self, *, input_tokens, output_tokens, model) -> Decimal:
        ...

    def count_tokens(self, *, system, messages, model) -> int:
        ...

    def recognizes_model(self, name: str) -> bool:  # v1.0.2 #1
        return name.startswith("my-")

    def supports_native_structured_output(self, model: str) -> bool:  # v1.3 #1
        return False

assert isinstance(MyProvider(), LLMProvider)
```

`default_model`, `recognizes_model`, `supports_native_structured_output`,
and `llm_capabilities` are all additive (v1.0.2 #1 / v1.3 #1 / v1.11
#1). Custom providers that omit them keep working at every call site —
the runtime guards each lookup with `getattr(...)`/
`get_llm_provider_capabilities(...)` — they just require an explicit
`model=` on every `@llm_behavior`, don't participate in cross-provider
validation, resolve to the prompt-embedded structured-output path, and
(for `llm_capabilities`) resolve to `FULL_LLM_PROVIDER_CAPABILITIES` —
the same full-parity behavior every provider had before that
descriptor existed. Declare `llm_capabilities` on your own provider
only if it genuinely can't enforce something the locked `complete()`/
`count_tokens()` signatures imply (see `ClaudeCodeProvider`'s section
above for the full descriptor shape) — `Runtime` will then refuse to
bind a deterministic behavior or a hard `max_cost_usd` budget to it,
the same way it does for `ClaudeCodeProvider`. (The `isinstance` check
above requires the full current method set; the runtime itself never
isinstance-checks providers.)

If your provider exposes the framework's instruction-based
structured-output path (most do), reuse
`parse_structured_response(text, schema)` from
`activegraph.llm.parsing` for byte-identical error semantics with
the shipped providers — same `llm.parse_error` and
`llm.schema_violation` reason codes for the same response shapes.

See [CONTRACT v1.0.1 #5](https://github.com/yoheinakajima/activegraph/blob/main/CONTRACT.md)
for the provider-commitment surface: which methods are stable and
which behaviors are provider-dependent (`count_tokens`). Native
structured-output mode is specified in CONTRACT v1.3 #1.
