# LLM — the external-model boundary

## Responsibility

`activegraph/llm/` owns everything that touches an external model and nothing that decides *when*
to touch one. It supplies the deterministic prompt assembler, the two narrow provider Protocols,
the wire-format translation into vendor SDKs, the content-keyed replay caches, and the LLM-specific
error taxonomy. Four concrete completion providers ship behind `LLMProvider`, in ownership-probe
order: `AnthropicProvider`, `OpenAIProvider`, `ClaudeCodeProvider`, and `OpenRouterProvider`. It
deliberately does **not** orchestrate: there is no turn loop, no retry, no budget, and no event
emission inside the package — "the loop is orchestrated by the runtime, not the provider"
(`activegraph/llm/provider.py:49-53`). Two provider seams live here: `LLMProvider` for text
completion (`activegraph/llm/provider.py:146`) and `EmbeddingProvider`, "the runtime's second
provider seam" (`activegraph/llm/embedding.py:5`, `:32`). The dependency direction is strictly
one-way — `llm/` never imports `runtime/`, `store/`, `sinks/`, `observability/`, `packs/`, `tools/`,
or `behaviors/`.

## Component map

```mermaid
graph TD
  subgraph external["outside llm/"]
    RT["runtime/runtime.py<br/>turn loop, retry, budget"]
    BH["behaviors/base.py<br/>LLMBehavior.build_prompt"]
    TL["tools/base.py<br/>Tool.to_definition"]
    PK["packs/diligence/fixtures<br/>RecordedDiligenceProvider"]
    CORE["core/ + frame.py + errors.py<br/>Event, View, Frame, ExecutionError"]
  end

  subgraph llm["activegraph/llm/"]
    PROMPT["prompt.py<br/>assemble_prompt, AssembledPrompt<br/>serialize_view LOCKED"]
    PID["prompt_identity.py<br/>canonical identity payload + SHA-256"]
    TYPES["types.py<br/>LLMMessage, LLMResponse, ToolCall"]
    PROV["provider.py<br/>LLMProvider Protocol"]
    EMBP["embedding.py<br/>EmbeddingProvider, HashEmbeddingProvider"]
    ANT["anthropic.py<br/>AnthropicProvider"]
    OAI["openai.py<br/>OpenAIProvider"]
    CC["claude_code.py<br/>ClaudeCodeProvider"]
    OR["openrouter.py<br/>OpenRouterProvider"]
    REC["recorded.py<br/>Recorded / RecordingLLMProvider"]
    WIRE["wire.py<br/>tool-name sanitize, exception classify"]
    NAT["native.py<br/>native_schema_compatible"]
    PARSE["parsing.py<br/>parse_structured_response"]
    CACHE["cache.py<br/>LLMCache"]
    ECACHE["embedding_cache.py<br/>EmbeddingCache"]
    ERR["errors.py<br/>LLMBehaviorError, MissingProviderError,<br/>PromptIdentityError"]
  end

  BH -->|assemble_prompt| PROMPT
  TL -->|schema_to_json| PROMPT
  RT -->|complete / count_tokens / estimate_cost| PROV
  RT -->|embed| EMBP
  RT -->|get / record / from_events| CACHE
  RT -->|hash_embedding_request| ECACHE
  RT -->|native_schema_compatible| NAT
  RT -->|per-turn identity| PID
  PK -.->|regex-sniffs locked format| PROMPT
  PROMPT --> TYPES
  PROMPT --> CORE
  PROMPT --> PID
  ANT --> PROV
  OAI --> PROV
  CC --> PROV
  OR --> PROV
  OR -->|protected-hook reuse| OAI
  REC --> PROV
  REC --> PID
  ANT --> WIRE
  OAI --> WIRE
  CC --> WIRE
  OR --> WIRE
  ANT --> NAT
  OAI --> NAT
  CC --> NAT
  OR --> NAT
  ANT --> PARSE
  OAI --> PARSE
  CC --> PARSE
  OR --> PARSE
  WIRE --> ERR
  PARSE --> ERR
  ERR --> CORE
  CACHE --> TYPES
```

## Key types & entry points

### Protocols

- `LLMProvider` — `runtime_checkable` Protocol; 3 required methods + 3 additive, getattr-guarded members — `activegraph/llm/provider.py:146`
- `LLMProviderCapabilities` — additive frozen descriptor outside the Protocol; absent declarations
  resolve to `FULL_LLM_PROVIDER_CAPABILITIES` through `get_llm_provider_capabilities`
- `EmbeddingProvider` — `runtime_checkable` Protocol; `embed()` + `default_model` — `activegraph/llm/embedding.py:32`
- `HashEmbeddingProvider` — deterministic dependency-free test double, sha256-bucket + L2 norm; vectors are explicitly **not** semantically meaningful — `activegraph/llm/embedding.py:54`, `:61-65`

### Data shapes (public contract)

- `LLMMessage(role, content, tool_use_id, tool_name, tool_calls)` — frozen dataclass — `activegraph/llm/types.py:35`
- `Role = Literal["user","assistant","tool"]` — `activegraph/llm/types.py:31`
- `ToolCall(id, name, args)` — frozen dataclass — `activegraph/llm/types.py:75`
- `LLMResponse(raw_text, parsed, input_tokens, output_tokens, cost_usd, latency_seconds, model, finish_reason, seed, cache_hit, provider_meta, tool_calls)` — mutable dataclass — `activegraph/llm/types.py:94`
- `AssembledPrompt(system, messages, model, max_tokens, temperature, top_p, output_schema_name, output_schema_json, deterministic, structured_output_mode, sections)` — `activegraph/llm/prompt.py:53`

### Prompt assembly (pure, no I/O)

- `assemble_prompt(...) -> AssembledPrompt` — top-level assembler; "Pure function over its arguments — no I/O, no provider calls" — `activegraph/llm/prompt.py:457`, docstring `:482`
- `serialize_view(view, *, around, depth) -> str` — **format is locked and snapshot-tested; changing it is a breaking change** — `activegraph/llm/prompt.py:108`, `:116-118`
- `build_system_prompt(...)` — `activegraph/llm/prompt.py:188`
- `build_user_message(...)` — `activegraph/llm/prompt.py:343`
- `build_instruction(creates, output_schema_name)` — `activegraph/llm/prompt.py:401`
- `schema_to_json(schema) -> Optional[dict]` — Pydantic v2 `model_json_schema()` with a name-only shell fallback — `activegraph/llm/prompt.py:437`
- `example_instance_from_schema(schema)` — deterministic placeholder instance, depth-bounded at 6 — `activegraph/llm/prompt.py:254`; worker `_example_instance` at `:281`
- `_strip_volatile(value)` — recursively drops `provenance`, `timestamp`, `run_id` from the event payload before hashing/prompting — `activegraph/llm/prompt.py:374-395`

### Prompt identity

- `build_prompt_identity_payload(...)` — the single normalized payload builder used by public
  prompt identity, runtime turn-cache identity, and recorded fixtures —
  `activegraph/llm/prompt_identity.py:19-55`
- `canonical_prompt_json(payload)` / `hash_prompt_payload(payload)` — the shared canonical JSON and
  SHA-256 implementation — `activegraph/llm/prompt_identity.py:58-64`
- Omitting `tools` preserves the public `AssembledPrompt` identity domain; passing `tools=None` or
  an empty collection emits `"tools": null` for the runtime/fixture domain
  (`activegraph/llm/prompt_identity.py:33-38`, `:51-54`).

### Providers

The exact shipped-provider surface is:

| ownership order | provider | default model | `recognizes_model` ownership | native structured output |
|---:|---|---|---|---|
| 1 | `AnthropicProvider` | `claude-sonnet-4-5` | `claude-*` | configured Claude-family prefixes |
| 2 | `OpenAIProvider` | `gpt-4o-mini` | `gpt-*`, `o1-*`, `o3-*`, `o4-*` | configured OpenAI-family prefixes |
| 3 | `ClaudeCodeProvider` | `claude-sonnet-4-5` | `claude-*` (intentionally overlaps Anthropic) | shared Claude-family prefixes |
| 4 | `OpenRouterProvider` | `openrouter/free` | bounded `~?owner/model[:variant]` grammar | caller-supplied prefixes only; empty by default |

`AnthropicProvider` and `OpenAIProvider` omit a descriptor and therefore resolve to the exact full
default. Claude Code and OpenRouter declare their deltas explicitly:

```python
FULL_LLM_PROVIDER_CAPABILITIES = LLMProviderCapabilities(
    enforces_max_tokens=True,
    supports_sampling_controls=True,
    input_token_count="official",
    max_tool_calls_per_completion=None,
    requires_generation_control_acknowledgement=False,
)

ClaudeCodeProvider.llm_capabilities = LLMProviderCapabilities(
    enforces_max_tokens=False,
    supports_sampling_controls=False,
    input_token_count="estimate",
    max_tool_calls_per_completion=1,
    requires_generation_control_acknowledgement=True,
)

OpenRouterProvider.llm_capabilities = LLMProviderCapabilities(
    enforces_max_tokens=True,
    supports_sampling_controls=True,
    input_token_count="estimate",
    max_tool_calls_per_completion=None,
    requires_generation_control_acknowledgement=False,
)
```

`OpenRouterProvider` is public from `activegraph.llm` but is deliberately not re-exported from the
top-level `activegraph` package. The generated `activegraph/baml_client` runtime bridge remains an
explicit `[baml]` integration, not an `LLMProvider` and not a fifth shipped-provider ownership
candidate.

- `RecordedLLMProvider(fixtures_dir, *, structured_output_mode="prompt")` — `activegraph/llm/recorded.py:187`
- `RecordingLLMProvider(inner, fixtures_dir)` — `activegraph/llm/recorded.py:332`
- `RecordedDiligenceProvider` — an out-of-package scripted provider, duck-typed rather than a Protocol subclass — `activegraph/packs/diligence/fixtures/__init__.py:56`

### Wire helpers, classification, structured output

- `sanitize_tool_name(name)` — `.` → `__`, other non-`[A-Za-z0-9_-]` → `_` — `activegraph/llm/wire.py:41`
- `build_tool_name_map(tools) -> dict[wire, canonical]`; **raises `ValueError` on collision** — `activegraph/llm/wire.py:62`, raise at `:76-83`
- `restore_tool_name(name, name_map)` — table lookup, never a blind string replace — `activegraph/llm/wire.py:87`
- `classify_provider_status(status_code)` — shared bare-status ladder: 429 rate-limited; 401/403
  auth; 408 network/timeout; every other 4xx request error; 5xx/unknown/missing network error —
  `activegraph/llm/wire.py:113`
- `classify_provider_exception(e) -> reason_code` — `activegraph/llm/wire.py:134`
- `parse_structured_response(text, schema)` — the sole boundary between raw provider text and typed objects — `activegraph/llm/parsing.py:38`
- `native_schema_compatible(schema) -> bool` — offline pre-flight for constrained decoding — `activegraph/llm/native.py:53`
- `inject_additional_properties_false(schema)` — the only permitted schema mutation — `activegraph/llm/native.py:142`

### Caches and errors

- `LLMCache` — `get / has / __len__ / record / from_events` — `activegraph/llm/cache.py:46`
- `EmbeddingCache` — same surface — `activegraph/llm/embedding_cache.py:40`
- `hash_embedding_request(*, texts, model) -> sha256hex` — `activegraph/llm/embedding_cache.py:20`
- `MissingProviderError(behavior_name)` — subclasses `RegistrationError, RuntimeError` — `activegraph/llm/errors.py:202`
- `LLMBehaviorError(reason, message, *, payload_extras)` — subclasses `ExecutionError, Exception` — `activegraph/llm/errors.py:256`
- `PromptIdentityError(kind, ...)` — internal runtime/fixture identity invariant; subclasses
  `ExecutionError, ValueError` and is deliberately not translated into a provider retry —
  `activegraph/llm/errors.py:300`, `activegraph/runtime/runtime.py:2250-2254`

## Interfaces & contracts at each seam

Runtime, behavior, tool, and pack consumers import from `activegraph/llm/` at the seams described
below. Separately, `activegraph/__init__.py` re-exports `LLMBehaviorError` and
`MissingProviderError` into the top-level public namespace; it does not re-export provider classes.

### llm <-> runtime (completion turn loop)

The primary seam. `runtime/runtime.py` imports `LLMProvider`, `LLMMessage`, `ToolCall`,
`LLMBehaviorError`, and `MissingProviderError` at module level (`runtime.py:97-106`) and lazily
imports `native_schema_compatible` / `schema_to_json` at `runtime.py:1227-1230`. The single hot call
site is `Runtime._invoke_llm_body` (`runtime.py:1993`), whose documented step order lives at
`runtime.py:1911-1924`. Registration-time entry is `Runtime._ensure_registry` (`runtime.py:1239`),
which raises `MissingProviderError(behavior_name=b.name)` at `runtime.py:1258` when any `LLMBehavior`
is registered with `llm_provider is None`.

```ebnf
completion-request ::= complete( "system" "=" string ,
                                 "messages" "=" message-list ,
                                 "model" "=" model-name ,
                                 "max_tokens" "=" int ,
                                 "temperature" "=" float ,
                                 "top_p" "=" float ,
                                 "output_schema" "=" ( pydantic-model | None ) ,
                                 "timeout_seconds" "=" float ,
                                 [ "tools" "=" tool-def-list ] ,
                                 [ "structured_output_mode" "=" "native" ] )
                       (* keyword-only, all of them; the mode kwarg is passed ONLY
                          when native resolved — runtime.py:2213-2221 *)

message-list       ::= message { message }
message            ::= LLMMessage( role , content [ , tool_use_id ]
                                                  [ , tool_name ]
                                                  [ , tool_calls ] )
role               ::= "user" | "assistant" | "tool"

tool-def-list      ::= tool-def { tool-def }
tool-def           ::= "{" "name" ":" canonical-name ,
                           "description" ":" string ,
                           "input_schema" ":" json-schema "}"
canonical-name     ::= [ pack-name "." ] identifier

completion-response ::= LLMResponse( raw_text , parsed , input_tokens , output_tokens ,
                                     cost_usd , latency_seconds , model , finish_reason ,
                                     seed , cache_hit , provider_meta , tool_calls )
tool-call          ::= ToolCall( id , returned-name , args-dict )
returned-name      ::= canonical-name | identifier
                       (* shipped adapters restore canonical names; the runtime
                          canonicalizes custom-provider short names before persistence *)

completion-failure ::= LLMBehaviorError( reason , message , payload_extras )
reason             ::= terminal-reason | transient-reason
terminal-reason    ::= "llm.parse_error" | "llm.schema_violation" | "llm.fixture_missing"
                     | "llm.auth_error" | "llm.request_error"
transient-reason   ::= "llm.network_error" | "llm.rate_limited"
payload_extras     ::= "{" "model" , "exception_type" , "message"
                           [ , "retry_after_seconds" ] "}"

(* additive declarations, all getattr-guarded by the runtime *)
provider-decls     ::= "default_model" ":" model-name
                     | recognizes_model( name ) "->" bool
                     | supports_native_structured_output( model ) "->" bool
                     | estimate_cost( input_tokens , output_tokens , model ) "->" Decimal
                     | count_tokens( system , messages , model ) "->" int

capability-decl    ::= "llm_capabilities" ":" LLMProviderCapabilities
                       (* additive, not a Protocol member; absent means FULL *)

mode-resolution    ::= "native"  when runtime.native_structured_output
                                  and behavior.model is not None
                                  and getattr(provider,"supports_native_structured_output")(model)
                                  and native_schema_compatible( schema_to_json(output_schema) )
                     | "prompt"  otherwise     (* silent, debug-logged, audited *)

tool-ref             ::= Tool | string
bound-tools          ::= tuple( resolve( tool-ref , behavior-owner ) )
                         (* rebuilt once per registry pass; declaration order and
                            duplicate entries are preserved *)
undotted-resolution  ::= own-pack-tool , exact-global-tool , unique-cross-pack-tool
                          when behavior-owner is a pack
                        | exact-global-tool , unique-pack-tool
                          when behavior-owner is global
returned-resolution  ::= exact-bound-canonical-name
                        | unique-bound-canonical-suffix
                        | UnknownToolError
```

**Contract notes.**

- All Protocol methods are keyword-only (`provider.py:152-184`). Three are required — `complete`,
  `estimate_cost`, `count_tokens`. Three members are additive and getattr-guarded by the runtime —
  `default_model`, `recognizes_model`, `supports_native_structured_output` (`provider.py:150`,
  `:186-211`) — so custom pre-v1.0.2 providers keep working; they just require explicit `model=` and
  never get native mode. `llm_capabilities` is separately additive without becoming a Protocol
  member: an absent descriptor resolves to `FULL_LLM_PROVIDER_CAPABILITIES`, preserving historical
  custom-provider behavior.
- `recognizes_model` must be **permissive**: unknown names — fine-tuned models, internal deployment
  names, experimental prefixes — should return `False` (`provider.py:110-113`).
- No streaming, no multi-model orchestration (`provider.py:33-34`). Tool-loop ownership is the
  runtime's: the provider returns `tool_calls`, the runtime invokes the tools and re-calls
  `complete()` with a `role="tool"` message (`provider.py:34-37`).
- `LLMBehavior.tools` is the mutable authoring surface (`Tool | str`). On each registry pass the
  runtime resolves it once to a homogeneous `tuple[Tool, ...]`, preserving order and duplicates.
  Provider definitions, authorization, and dispatch consume that same tuple. Pack-local object
  refs become the exact canonical Tool copies owned by the loaded runtime; foreign pack-local
  object identity fails validation before pack state mutates.
- Returned tool calls cross a second runtime boundary immediately after provider/cache retrieval.
  Exact declared canonical names remain unchanged; an undotted suffix is copied to the one
  distinct matching bound canonical name after duplicate declarations collapse. Zero or multiple
  matches raise `UnknownToolError` before the response can be cached, successfully emitted,
  appended to messages, hashed into another turn, or dispatched. Provider cost is still charged
  for a live rejected response; cache hits are not charged.
- `count_tokens` is called **only** when `cached is None and self.budget.has_cost_limit()`
  (`runtime.py:2079-2081`); a raise there becomes `behavior.failed` with
  `reason="llm.network_error", extras={"phase":"count_tokens"}` (`runtime.py:2087-2094`).
- `estimate_cost` prices the **worst case** — `max_tokens` is passed as the output-token estimate
  (`runtime.py:2095`).
- Native fallback is **silent-but-audited, never an error** (`native.py:16-17`,
  `runtime.py:1216-1218`): a non-qualifying schema logs one debug line at registration
  (`runtime.py:1231-1235`) and the resolved mode rides every `llm.requested` payload
  (`runtime.py:2148-2152`).
- `native_schema_compatible` is deliberately conservative (`native.py:53-65`): root must be an
  object; only 15 allowlisted keywords (`native.py:28-50`); **every object property must be in
  `required`** — optional fields would need a semantic rewrite "the framework refuses to do
  silently" (`native.py:106-108`); `additionalProperties` must be absent or `false`; `$ref` must be
  internal (`#/`-prefixed) and non-recursive (`native.py:90-100`).
- `inject_additional_properties_false` is the **single permitted mutation**, justified as "a pure
  narrowing — Pydantic validation ignored extra keys, so forbidding them changes what the model may
  emit, never what a conforming response means" (`native.py:143-147`).
- **Retry/backoff is entirely the runtime's.** There is no retry logic anywhere in
  `activegraph/llm/`; every provider makes exactly one SDK call per `complete()`. **CONTRACT v1.11
  #1 capability exception**: `ClaudeCodeProvider`'s "one call" is additionally constrained to "one
  call, `max_turns=1`, 0-or-1 tool calls" — the Claude Agent SDK backend has no `max_tokens`/
  `temperature`/`top_p` controls at all (accepted for Protocol conformance, never forwarded). Unlike
  a per-call guard, this is enforced by `Runtime`'s capability-binding validation
  (`activegraph/llm/provider.py`'s additive `LLMProviderCapabilities`, read at all three binding
  moments): a `deterministic=True` behavior or a hard `max_cost_usd` budget is refused at binding
  time, before any provider call, rather than silently degraded or raised mid-run. See
  `claude_code.py`'s module docstring and `CONTRACT.md` v1.11 #1 for the full capability-limited-
  provider record. Config lives at
  `runtime.py:422-424` (`llm_retry_max_attempts=3`, `initial_delay=0.5s`, `max_delay=8.0s`), clamped
  at `runtime.py:501-507`. `_llm_retry_delay_seconds` (`runtime.py:4442-4459`) lets a
  provider-supplied `retry_after_seconds` win when present, clamped to `[0, maximum]`
  (`:4449-4455`), else uses `min(initial * 2**attempt_index, maximum)` (`:4456-4459`). The
  transient set is runtime-side: `_TRANSIENT_LLM_REASONS = {"llm.network_error",
  "llm.rate_limited"}` (`runtime.py:4435`), checked by `_is_transient_llm_reason` (`:4438-4439`).
- **OpenRouter is generation-control capable but not hard-cost capable.** It enforces
  `max_completion_tokens` and sampling controls by requiring routed parameters, but its input count
  is still a local estimate. Consequently a hard `max_cost_usd` budget is rejected at each of the
  same three binding moments before `count_tokens`, `estimate_cost`, client construction, or SDK
  access. It needs no generation-control acknowledgement and remains compatible with non-cost
  budgets, including `max_llm_calls`.
- **Every failed attempt is logged** as its own `llm.responded` event carrying an `error` object
  (`runtime.py:2873-2918`), so "a provider outage cannot be confused with a valid empty response"
  (`errors.py:121-123`). Retried events chain via `retry_of` and `caused_by = <previous error event
  id>` (`runtime.py:2153-2157`, `:2271`). Retries reuse the **same turn hash**, and only
  `attempt_index == 0` gets the strict-replay hash check (`runtime.py:2187-2188`).
- **There is no rate limiter, concurrency cap, RPM tracker, or circuit breaker** anywhere in the LLM
  path. Rate limiting is purely reactive: 429 → classify → backoff, and the sleep is a blocking
  `time.sleep` on the runtime thread (`runtime.py:2273`, `:2313`).
- Error classification order is load-bearing and documented: rate-limit first (it is also a 4xx),
  then auth, then 408 timeout as transient network failure, then other 4xx, then network.
  `classify_provider_status` owns that numeric ladder; exception classification prefers the SDK's
  `status_code` attribute and falls back to type-name heuristics. **The fallback is the transient
  code on purpose** — "anything unrecognized stays `llm.network_error` so unknown failure shapes
  keep their pre-v1.3 retry behavior rather than being silently promoted to terminal"
  (`wire.py:26-28`). The historical bug this fixed: before v1.3 "a bad API key was retried with
  exponential backoff before failing" (`wire.py:19-22`).
- `LLMBehaviorError.__init__(reason, message, *, payload_extras)` is frozen "so the ~8 internal raise
  sites in providers do not change" (`errors.py:265-267`). `MissingProviderError` fires **at
  registration, not per-invocation** (`errors.py:207-208`), because "silently no-op'ing the behavior
  would corrupt the audit trail" (`errors.py:237-241`).

Reason-code raise sites:

| reason | terminal / transient | raised at |
|---|---|---|
| `llm.parse_error` | terminal | `parsing.py:67`; also `runtime.py:2506` when `parsed is None` despite a schema |
| `llm.schema_violation` | terminal | `parsing.py:76`; also `runtime.py:2491` on cache-hydration failure |
| `llm.fixture_missing` | terminal | `recorded.py:261` |
| `llm.rate_limited` | **transient** | via `wire.py:126-129` |
| `llm.network_error` | **transient** | via shared status 408/5xx/missing fallback, exception fallback, or typed provider errors |
| `llm.auth_error` | terminal | via shared 401/403 or typed provider errors |
| `llm.request_error` | terminal | via other shared 4xx, typed provider errors, or an unusable completed OpenRouter envelope |

### llm <-> runtime (cache and replay)

`LLMCache` is keyed by **prompt hash (content match), not event id** — "that's what lets a fork's
regenerated prompts hit the same recorded responses" (`cache.py:3-5`). The originating
`llm.requested` id is stored for lineage but is not the key (`cache.py:6-7`; `CachedEntry` at
`:41`). The runtime populates it from an event stream on `Runtime.load(..., replay_llm_cache=True)`
and `runtime.fork(...)` (`runtime.py:3824`, `:4026`, `:4769`), and records inline after each live
call (`runtime.py:2361-2368`) so same-run repeats hit too.

```ebnf
cache-read     ::= get( prompt_hash ) "->" ( LLMResponse{cache_hit=true} | None )
                 | has( prompt_hash ) "->" bool
cache-write    ::= record( prompt_hash , LLMResponse , requesting_event_id )
                   (* stores a copy with cache_hit=false *)
cache-hydrate  ::= from_events( event-stream ) "->" LLMCache

pairing-rule   ::= responded-event
                   where responded.type = "llm.responded"
                     and not responded.payload["error"]
                     and requested = lookup( responded.caused_by )
                     and requested.type = "llm.requested"
                     and requested.payload["prompt_hash"] is truthy
                   yields ( requested.payload["prompt_hash"] -> responded )

turn-cache-key ::= sha256( canonical-json( "{" model , system , messages ,
                     output_schema_name , output_schema_json , max_tokens ,
                     temperature , top_p , deterministic ,
                     "tools" ":" ( tool-def-list | null )
                     [ , "structured_output_mode" ":" "native" ] "}" ) )
                   (* _hash_turn_prompt, runtime.py:4527 — includes running_messages,
                      so each turn of a tool loop keys distinctly *)
```

**Contract notes.**

- `get()` returns a **copy with `cache_hit=True`**, never the stored object, so a second hit on the
  same hash also reports `cache_hit=True` (`cache.py:55-75`); `record()` stores an un-flagged copy
  (`cache.py:93-109`).
- `from_events` **skips events with a truthy `payload["error"]`** — failed attempts are not reusable
  model output (`cache.py:133`).
- `tool_calls` round-trip through both `get`/`record` (`cache.py:73-74`, `:108`) and through the
  event payload via `_response_from_event_payload` (`cache.py:151-184`), "so the turn loop sees the
  same shape live vs cached" (`cache.py:72-73`).
- **No eviction, no TTL, no size bound** — `_by_hash` is an unbounded dict (`cache.py:48`).
- Cache **reads** are gated on `self.replay_llm_cache` (`runtime.py:2079-2081`); **writes** are not —
  the runtime lazily creates an `LLMCache` if none exists (`runtime.py:2361-2368`). A cache hit
  forces `max_attempts = 1` (`runtime.py:2123-2128`).

**One implementation owns two intentional identity domains.** Every payload and digest delegates
to `activegraph.llm.prompt_identity`:

| Domain | Entry points | Distinguishing fields | Consumer |
|---|---|---|---|
| public prompt identity | `AssembledPrompt.to_hashable/hash` — `prompt.py:82-103` | omits `tools`; includes `structured_output_mode` only for native | public inspection and snapshot tests |
| per-turn / fixture identity | `_hash_turn_prompt` — `runtime.py:4527-4555`; `_prompt_payload` — `recorded.py:66-93` | always includes `tools` (`null` when absent); hashes the running messages and declared `prompt.deterministic` | `LLMCache`, audit `prompt_hash`, recorded fixture filename |

The runtime supplies `prompt_hash` and `deterministic` only to providers advertising
`accepts_prompt_identity` (`runtime.py:2229-2249`). Recorded providers advertise that additive
capability (`recorded.py:200-202`, `:345`), validate the pair before I/O, and use the declared
determinism as the canonical identity (`recorded.py:96-145`). `RecordedLLMProvider` may read one
legacy fixture whose old filename inferred determinism from sampling controls; canonical lookup
wins and `RecordingLLMProvider` writes only the canonical filename (`recorded.py:147-181`,
`:406-447`).

Public prompt identity and turn identity still differ intentionally because the latter has a
`tools` key. Runtime and fixture identity no longer drift. `timeout_seconds` is in neither domain,
so changing only a timeout does not invalidate a cache entry or fixture. Embeddings use an
independent content key (`embedding_cache.py:20-28`).

### llm <-> runtime (embedding)

`Runtime.embed` (`runtime.py:1566`) hashes the request with `hash_embedding_request`
(`runtime.py:1598-1600`), consults `EmbeddingCache`, and only then calls
`provider.embed(texts=, model=)` at `runtime.py:1659-1661`, defaulting the model from
`provider.default_model` (`runtime.py:1587-1595`).

```ebnf
embed-request    ::= embed( "texts" "=" string-list , "model" "=" model-name )
                     "->" vector-list
vector-list      ::= vector { vector }     (* len == len(texts), order-preserving *)
vector           ::= float { float }       (* uniform dimensionality per model *)
embed-failure    ::= any-exception         (* MUST raise, never return partial *)

embed-cache-key  ::= sha256( json( "{" "model" ":" model , "texts" ":" string-list "}" ,
                                   sort_keys , separators=(",", ":") ) )
embed-cache-read ::= get( inputs_hash ) "->" ( fresh-list-copies | None )
embed-hydrate    ::= from_events( event-stream ) "->" EmbeddingCache
                     accepting only pairs where every vector is a list of finite
                     non-bool numbers, all vectors share one dimension, and
                     len(vectors) == requested.payload["input_count"]
```

**Contract notes.**

- `EmbeddingProvider.embed` must be order-preserving with uniform dimensionality, and
  "implementations should raise on failure rather than returning partial results" because "a
  silently short list would misalign every downstream score" (`embedding.py:44-50`).
- The cache key uses the same canonical JSON options as the prompt hashes (`sort_keys=True`, compact
  separators, default `ensure_ascii=True`) but a separate `{model, texts}` payload
  (`embedding_cache.py:20-28`).
- `EmbeddingCache.get()` returns fresh `list` copies "so callers cannot mutate the replay authority"
  (`embedding_cache.py:5-6`, `:46-52`, `:62-74`); storage is an immutable tuple-of-tuples.
- `from_events` validation is strict and silent: it skips error responses and rejects non-list
  vectors, mixed dimensionality, bool/non-numeric components, non-finite floats, and an input-count
  mismatch, `continue`-ing past the offending pair (`embedding_cache.py:76-127`).
- The `embedding.requested` event stores **only the content hash, never the input text**
  (`runtime.py:1574-1579`, payload at `:1606-1616`); the response event stores the vectors
  (`runtime.py:1679-1692`).
- Under `replay_strict`, `Runtime.embed` refuses to fall through to live I/O **even when a provider
  is configured** (`runtime.py:1636-1645`).

### llm <-> behaviors

`LLMBehavior.build_prompt` (`behaviors/base.py:146`) is the only behaviors-side entry into `llm/`.
It lazily imports `assemble_prompt` at `:161` and calls it at `:183-200`; `AssembledPrompt` is a
`TYPE_CHECKING`-only import at `:30`, so `behaviors` carries no runtime import edge for the type.
The runtime calls it at `runtime.py:2025-2032` with `structured_output_mode=so_mode`. It is public
per CONTRACT v0.6 #20 — "Reproducible (pure over inputs); cheap (no I/O)" (`behaviors/base.py:157-158`).

```ebnf
prompt-assembly ::= assemble_prompt( behavior_name , description , model , output_schema ,
                                     creates , view , event , frame , around , depth ,
                                     max_tokens , temperature , top_p , deterministic ,
                                     [ prompt_template ] , [ structured_output_mode ] )
                    "->" AssembledPrompt

assembled-prompt ::= system-text , user-message-list , call-params , sections

system-text     ::= identity-line
                    [ "Mission: " goal ]
                    [ "Constraints:" bullet-list ]
                    [ "Role: " description ]
                    [ schema-block ]                  (* joined with "\n\n" — prompt.py:251 *)
identity-line   ::= 'You are an active-graph behavior named "' name '".'
schema-block    ::= native-schema-sentence | prompt-schema-block
native-schema-sentence
                ::= "Respond with JSON that matches the `" name "` schema."
prompt-schema-block
                ::= instance-framing , "Schema:" json , "Example instance..." json

user-message    ::= view-block "\n\n" "## Triggering event\n" event-block
                    "\n\n" "## Task\n" instruction
                  | prompt_template.format( system , view , event , instruction )

view-block      ::= "## Graph context" [ " (" header-bits ")" ]
                    "### Objects"       object-line-list
                    "### Relations"     relation-line-list
                    "### Recent events" event-line-list
                    (* FORMAT IS LOCKED — snapshot-tested, prompt.py:116-118 *)
header-bits     ::= [ "depth=" int ] [ ", " ] [ "around=" object-id ]
object-line     ::= "- " id " (" type "): " canonical-json | "- (none)"
relation-line   ::= "- " source " --" type "--> " target | "- (none)"
event-line      ::= "- " id " " type [ summary-tail ] | "- (none)"

event-block     ::= "- id: " id "\n- type: " type "\n- actor: " actor
                    "\n- payload:\n```\n" volatile-stripped-json "\n```"
volatile-stripped ::= json minus keys { "provenance" , "timestamp" , "run_id" }
```

**Contract notes.**

- **Four locked sources, fixed order**: system → view → event → instruction (`prompt.py:7-14`).
  `prompt_template=` (a `str.format` over `{system}{view}{event}{instruction}`) is "the only escape
  hatch — and it still receives the same four runtime-assembled inputs. There is no raw
  string-concat path in user code" (`prompt.py:30-33`). An unknown placeholder raises `ValueError`
  naming the allowed set (`prompt.py:513-517`).
- **View serialization format is part of the public contract**, snapshot-tested; changing it is a
  breaking change (`prompt.py:16-18`, `:116-118`).
- **Volatile-field stripping** removes `provenance`, `timestamp`, `run_id` recursively before
  hashing/prompting, because provenance embeds the parent `run_id` and "without this, the cache
  would miss on every fork" (`prompt.py:360-363`, `:374-395`). The runtime advertises this as
  `prompt_normalized: True` on every `llm.requested` (`runtime.py:2143-2146`).
- **Determinism normalization**: `deterministic=True` forces `temperature=0.0` and `top_p=1.0`
  (`prompt.py:523-524`).
- **Structured-output modes shape the system prompt.** In prompt mode (default),
  `build_system_prompt` embeds the JSON Schema *plus* a generated example instance with explicit
  "Return an INSTANCE ... NOT the schema itself" framing — added because "some models echo the JSON
  Schema definition back instead of an instance, triggering `llm.schema_violation`"
  (`prompt.py:229-249`, rationale `:234-238`); `build_instruction` repeats the framing
  (`prompt.py:413-424`). In native mode the schema dump, example, and framing are all **omitted** in
  favor of one sentence, because constrained decoding eliminates the failure mode
  (`prompt.py:220-228`).
- When `self.model is None`, `build_prompt` falls back to the hardcoded `"claude-sonnet-4-5"` **for
  inspection only** (`behaviors/base.py:182`).

### llm <-> tools

`Tool.to_definition()` (`tools/base.py:52`) lazily imports `schema_to_json` at `:59` and emits
`{"name", "description", "input_schema"}` (`:61-68`) — the *framework* tool-definition shape that
each shipped completion provider then translates at its external boundary. The runtime calls it at
`runtime.py:2021` to build
`tool_defs`.

```ebnf
tool-definition ::= schema_to_json( input_schema ) "->" ( json-schema | None )
json-schema     ::= pydantic_v2.model_json_schema()
                  | "{" "type" ":" "object" , "title" ":" class-name "}"   (* fallback *)
```

**Contract notes.** `tools/cache.py:3` and `tools/recorded.py:3` state that they *mirror*
`activegraph.llm.cache` / `activegraph.llm.recorded` — they are **copies, not imports**; there is no
code edge in that direction.

### llm <-> runtime/_live (cross-provider model validation)

`_which_shipped_provider_claims(name, *, exclude)` lazily imports the four concrete classes only
when cross-provider validation needs them. It probes the exact ordered list
`[AnthropicProvider, OpenAIProvider, ClaudeCodeProvider, OpenRouterProvider]`, skips the configured
provider class, constructs each remaining candidate without credentials or I/O, and calls
`recognizes_model(name)`. A candidate-constructor exception is swallowed defensively so it cannot
hide a later match.

```ebnf
model-validation ::= for each LLMBehavior b in registration source:
                       if b.model is None -> b.model := provider.default_model
                                             (fallback "claude-sonnet-4-5" — runtime.py:4474-4479)
                       else               -> _validate_one( b , provider )
claim-lookup     ::= _which_shipped_provider_claims( name , exclude=type(provider) )
                     "->" list[provider-class]
                     (* every match, in Anthropic/OpenAI/ClaudeCode/OpenRouter order;
                        [] when none; the configured class is excluded *)
mismatch-outcome ::= InvalidRuntimeConfiguration  when one or more other shipped providers claim it
                   | pass-through                 when nobody claims it (permissive)
```

The result is deliberately a list of **all** matches, not the first match. `claude-*` therefore
reports both `AnthropicProvider` and `ClaudeCodeProvider` when neither is the configured provider;
OpenRouter's slash-qualified grammar is disjoint from those shipped plain-prefix families today,
but it participates in the same all-match loop rather than introducing a first-match shortcut.
Diagnostics always expose plural `claiming_provider_names` and
`claiming_provider_defaults` in this stable order. Deprecated singular `claimed_by_provider` and
`claimed_by_default_model` compatibility keys appear only when the list has exactly one match.

### llm <-> packs

`packs/diligence/fixtures/__init__.py` imports `LLMMessage, LLMResponse` at `:19` and `ToolCall`
lazily at `:205` to implement `RecordedDiligenceProvider` (`:56`) — a scripted, **duck-typed**
`LLMProvider` that does not subclass the Protocol. Critically, its dispatch is a consumer of the
*locked prompt format*, not just the types: `_extract_behavior_name` (`:151`) regexes
`behavior named "([^"]+)"` out of the system prompt, matching `prompt.py:207-208` exactly, and
`_extract_company_name` (`:171`) splits on the literal `"## Triggering event"` header (`:176-179`),
matching `build_user_message` (`prompt.py:352`). `cli/quickstart.py:112`, `:410` construct
`Runtime(..., llm_provider=RecordedDiligenceProvider(...))`, so the CLI reaches `llm/` only through
this pack.

```ebnf
scripted-provider ::= duck-typed LLMProvider    (* NOT a Protocol subclass *)
dispatch-key      ::= sniff( system-prompt , 'behavior named "..."' )
                      x sniff( user-message , "## Triggering event" section )
                      x output_schema
                      (* hard dependency on the LOCKED prompt format *)
```

**Contract note.** This is the strongest practical argument for why `prompt.py:16-18` calls format
changes breaking — a scripted provider's dispatch silently misroutes if the header text moves.

### llm <-> core / frame / errors (outbound)

`llm/` reaches out to exactly four activegraph targets, and no others:

| Target | Symbol | Site | Why |
|---|---|---|---|
| `core/event.py` | `Event` | `prompt.py:43`, `cache.py:36`, `embedding_cache.py:17` | Prompt serializes the triggering event (`prompt.py:360-372`); both caches walk an event log in `from_events` |
| `core/view.py` | `View` | `prompt.py:44` | `serialize_view` reads `view.objects()`, `view.relations()`, `view.events()` (`prompt.py:129`, `:142`, `:147`) |
| `activegraph/frame.py` (top-level, **not** `core/`) | `Frame` | `prompt.py:45` | `build_system_prompt` reads `frame.goal` and `frame.constraints` (`prompt.py:211-216`) |
| `activegraph/errors.py` (top-level) | `ExecutionError`, `RegistrationError` | `errors.py:30` | Parent classes for the three LLM error types (`errors.py:202`, `:256`, `:300`) |

### llm -> vendor SDKs (the external wire)

The four shipped providers translate the same framework shapes at their external boundaries.
Anthropic and OpenAI use the canonical API-key SDK wires below. OpenRouter deliberately reuses the
OpenAI translation through protected hooks but owns a stricter request, client, error, and returned-
cost policy. Claude Code instead flattens the message history into its canonical transcript and
drives one isolated Claude Agent SDK query with `max_turns=1`, as constrained by its capability
record above. SDK imports and credential reads remain lazy.

```ebnf
anthropic-wire  ::= messages.create( "model" , "max_tokens" , "messages" , "temperature" ,
                                     [ "system" ] ,
                                     [ "top_p" ]        (* iff top_p < 1.0 *) ,
                                     [ "tools" ] , [ "output_config" ] , "timeout" )
output_config   ::= "{" "format" ":" "{" "type" ":" "json_schema" ,
                                        "schema" ":" narrowed-schema "}" "}"
anth-message    ::= "{" "role" ":" ("user"|"assistant") , "content" ":" string "}"
                  | "{" "role" ":" "user" , "content" ":"
                        "[" "{" "type" ":" "tool_result" ,
                                "tool_use_id" ":" id , "content" ":" string "}" "]" "}"
                  | "{" "role" ":" "assistant" , "content" ":" block-list "}"
block-list      ::= [ text-block ] tool-use-block { tool-use-block }

openai-wire     ::= chat.completions.create( "model" , "messages" , "timeout" ,
                                             ( "max_tokens" "," "temperature" [ "," "top_p" ]
                                             | "max_completion_tokens" )  (* reasoning families *) ,
                                             [ "tools" ] , [ "response_format" ] )
openrouter-wire ::= chat.completions.create( "model" , "messages" , "timeout" ,
                                             "max_completion_tokens" , "temperature" ,
                                             [ "top_p" ] ,
                                             "extra_body" "=" "{" "provider" ":"
                                               "{" "require_parameters" ":" true "}" "}" ,
                                             [ "tools" ] , [ "response_format" ] )
response_format ::= "{" "type" ":" "json_schema" ,
                        "json_schema" ":" "{" "name" , "schema" ":" narrowed-schema ,
                                              "strict" ":" true "}" "}"
openai-tool-def ::= "{" "type" ":" "function" ,
                        "function" ":" "{" "name" ":" wire-name ,
                                           "description" , "parameters" "}" "}"

wire-name       ::= sanitize( canonical-name )
sanitize        ::= replace( "." , "__" ) then replace( ~[A-Za-z0-9_-] , "_" )
narrowed-schema ::= json-schema with additionalProperties:false injected on every
                    object node                      (* native.py:142 *)
```

**Contract notes.**

- **Tool-name sanitization (CONTRACT v1.3 #3).** Canonical dotted names
  (`diligence.fetch_company_docs`) stay canonical *everywhere inside the runtime* — event log,
  registry, `get_tool()` — and are rewritten **only on the wire** (`wire.py:8-15`). The reverse
  mapping is an explicit per-request table, "never a blind string replace, so a tool legitimately
  named with `__` cannot be mangled" (`wire.py:13-15`). **Collisions raise `ValueError`**
  (`wire.py:76-83`) because "silently dispatching the wrong tool would corrupt the event log's
  causality" (`wire.py:66-68`). Wire-safe names pass through byte-identically (`wire.py:49-50`), so
  non-pack tools produce pre-v1.3-identical requests. Echoed assistant `tool_calls` are
  **re-sanitized on the way back out** (`anthropic.py:355-364`, `openai.py:477-496`) "or the provider
  rejects the conversation it produced itself" (`openai.py:479-481`).
- **`top_p` is forwarded only when `< 1.0`** — "1.0 is the model default; only forward when
  narrowing" (`anthropic.py:155-157`, `openai.py:212-213`).
- **Reasoning families** (`o1`, `o3`, `o4`, `gpt-5` — `openai.py:124`) take `max_completion_tokens`
  and get **no** `temperature`/`top_p` at all (`openai.py:204-213`). "Before v1.3 the provider sent
  the GPT-4-era parameters unconditionally, so every call to these families was a guaranteed 400"
  (`openai.py:120-122`).
- **Structured-output parsing is skipped mid-tool-loop**: `parsed` is computed only when
  `output_schema is not None and not tool_calls` (`anthropic.py:211-212`, `openai.py:318-319`) —
  "Parsing structured output happens on the final turn, not mid-loop" (`anthropic.py:208-210`).
- **Multi-turn assistant echo (v1.0.3 #4)**: an assistant message carrying `tool_calls` must be
  reconstructed as full content blocks (text + `tool_use`), not raw text — "Direct Anthropic API
  access tolerated raw_text-only echo; the Vertex AI proxy enforces the spec strictly and 400s
  without it" (`anthropic.py:331-336`, impl `:348-365`).
- `LLMMessage.to_dict()` **omits `tool_use_id` / `tool_name` / `tool_calls` when `None`** so recorded
  fixture hashes for single-turn flows stay byte-identical (`types.py:61-71`, rationale `:67-68`).
  The same omit-when-absent pattern governs `structured_output_mode` in every hash
  (`prompt.py:96-97`, `recorded.py:99-100`, `runtime.py:4532-4533`).
- All four shipped providers set `seed=None` — Anthropic's messages API has no seed parameter;
  OpenAI does the same despite the API having one, OpenRouter inherits that response translation,
  and Claude Code returns the same framework shape.
- `parse_structured_response` extraction order: verbatim `json.loads` → fenced ` ```json ` block →
  first balanced `{...}` / `[...]` span (`parsing.py:51-65`, regexes at `:34-35`). Provider symmetry
  is an explicit promise: "AnthropicProvider and OpenAIProvider produce identical errors for
  identical responses through this function" (`parsing.py:21-22`).
- **Static cost accounting** in `AnthropicProvider` and `OpenAIProvider` is pure `Decimal`
  arithmetic over a per-million-token family-prefix table using **longest matching prefix**
  (`anthropic.py:46-62`, `openai.py:67-83`); unknown models fall back to `claude-sonnet-4` /
  `gpt-4o` (`anthropic.py:59-60`, `openai.py:80-81`). Tables are constructor-overridable via
  `pricing=` (`anthropic.py:93`, `openai.py:128`). Those providers surface
  `retry_after_seconds` through the shared lowercase-header parser (`wire.py:164-177`; provider call
  sites `anthropic.py:183-185`, `openai.py:308-310`). OpenRouter's returned-cost contract is
  intentionally different and is specified below.
- **Replay determinism.** `RecordedLLMProvider` "raises if a fixture is missing (so tests fail loud
  rather than regressing into live calls)" (`recorded.py:6-8`); the raise is
  `LLMBehaviorError("llm.fixture_missing", ...)` at `recorded.py:261-268` carrying `prompt_hash` and
  `fixtures_dir` in `payload_extras`, and there is **no silent fallthrough to a real call**
  (`recorded.py:192-193`). Fixture path is `<fixtures_dir>/<sha256_hex>.json` (`recorded.py:178`,
  layout at `:17-36`); `recorded_at` sits deliberately outside the hashed `prompt` payload so it
  "doesn't perturb lookups but stays available for future debugging when fixtures drift"
  (`recorded.py:38-40`). `recognizes_model` returns `True` unconditionally so cross-provider
  validation never fires against fixtures (`recorded.py:213-216`), and
  `supports_native_structured_output` is **per-instance, not model-derived** — it returns
  `self._structured_output_mode == "native"` (`recorded.py:218-225`) — because fixture-backed replay
  must assemble prompts exactly the way the recording run did. `estimate_cost` returns `Decimal("0")`
  (`recorded.py:273-277`) and `count_tokens` is `chars//4` floored at 1 (`recorded.py:279-283`).
  `RecordingLLMProvider` delegates `llm_capabilities` and the generation-control acknowledgement
  (`recorded.py:347-364`), `default_model` / model recognition (`:366-378`), native structured
  output (`:380-388`), and cost/token methods (`:450-462`) to the inner provider so wrapping does
  not widen or otherwise change the binding surface. It forwards `structured_output_mode` **only
  when native**, so pre-v1.3 inner providers are never called with an unknown parameter
  (`recorded.py:420-437`).
- The sandbox seam is the *absence* of a provider: the child process "configures NO llm_provider, so
  key-freedom is structural" (`sandbox/_child.py:248`).

### OpenRouter-specific request, lifecycle, cost, and failure contracts

`OpenRouterProvider` accepts exactly the bounded ownership grammar
`^~?[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*(?::[a-z0-9]+(?:[._-][a-z0-9]+)*)?$`.
That diagnostic grammar is not a catalog allowlist: a future identifier outside it is unclaimed by
OpenRouter during ownership diagnosis but still passes through when no other shipped provider
claims it. The provider's exact keyword-only construction surface is:

```python
OpenRouterProvider(
    *,
    api_key_env: str = "OPENROUTER_API_KEY",
    client: Any = None,
    pricing: Optional[Mapping[str, Mapping[str, str]]] = None,
    native_structured_output_models: Optional[tuple[str, ...]] = None,
    base_url: str = "https://openrouter.ai/api/v1",
    app_url: Optional[str] = None,
    app_name: Optional[str] = None,
)
```

**Client ownership and lifecycle.** Construction reads no environment, imports no SDK, performs no
network I/O, and creates no client. `pricing=None` and `{}` both mean an empty OpenRouter-owned
table rather than inherited GPT prices; nested pricing and native-prefix inputs are copied, and the
reasoning-prefix tuple is fixed empty. An injected client bypasses SDK import, environment lookup,
OpenRouter configuration, and internal caching. Its retry policy, closing, and thread safety remain
caller-owned. An internally owned client is built lazily on first use and cached once for ordinary
serial synchronous Runtime use. The exact `os.environ[api_key_env]` value is passed as `api_key`
and must be non-empty after stripping; `base_url` is passed byte-for-byte; `max_retries=0`;
non-empty `app_url`/`app_name` become only `HTTP-Referer` /
`X-OpenRouter-Title`. `LLMProvider` has no `close`, so ActiveGraph promises neither deterministic
cleanup nor a lock-protected concurrent singleton.

`timeout_seconds` is the OpenAI SDK call's per-HTTP-attempt timeout, not an end-to-end Runtime
deadline, cancellation token, or in-flight interruption guarantee. OpenRouter's server-side routed
provider fallback happens inside one SDK request; it is separate from Runtime-owned retries.

**Request policy.** Every request sends `model`, the shared translated `messages`, `timeout`,
`max_completion_tokens=int(max_tokens)`, `temperature=float(temperature)`, and exactly
`extra_body={"provider": {"require_parameters": True}}`. It never sends deprecated `max_tokens`.
`top_p=float(top_p)` appears only when `top_p < 1.0`. Sanitized `tools` and an opted-in native
`response_format` merge after this policy without deleting `require_parameters`. Native mode is
off by default and recognizes only caller-supplied `native_structured_output_models` prefixes;
unsupported routed endpoints fail rather than silently ignoring parameters. No OpenRouter-only
reasoning parameter widens the shared Protocol.

**Estimates, realized cost, and budgets.** Constructor `pricing` values are ActiveGraph-owned USD
per one million tokens with exactly `input` and `output` decimal values. Missing fields, booleans,
malformed values, negatives, NaN, and either infinity are rejected at construction. Estimation
removes one optional leading `~` and selects the longest key whose next model character is `-`,
`.`, `_`, or `:` (or which matches exactly). `openrouter/free` and recognized `:free` models are
zero; an unknown paid model is `Decimal("Infinity")`, including for zero tokens.

A completed response never falls back to the estimate: `usage.cost` must convert through
`Decimal(str(value))` to a finite, non-negative, non-boolean value. Success returns that exact value
and `provider_meta={"cost_source": "openrouter_usage"}`. Missing, null, boolean, malformed,
negative, NaN, or infinite cost raises terminal `llm.request_error` with exactly `model`,
`field="usage.cost"`, `value_type`, and optional 64-character `finish_reason`; `value_type` is one
of `missing`, `null`, `bool`, `int`, `float`, `str`, `decimal`, or `other`, and the raw value is
never retained.

Because `input_token_count="estimate"`, every binding between OpenRouter and an `LLMBehavior` in a
Runtime with hard `max_cost_usd` is rejected at construction-time binding, registry initialization,
or late registration, before tokenization, estimation, client construction, or SDK access. An
empty Runtime has no behavior to reject, and non-cost budgets bind normally. In particular,
`max_llm_calls` counts **one behavior invocation**, not provider turns or HTTP attempts:
`Runtime._invoke_llm` consumes it once in `finally` after the whole retry/tool loop, so a two-turn
OpenRouter tool call still consumes one LLM invocation.

Exact realized accounting is promised only for completed responses carrying valid usage. A failed,
timed-out, cancelled, or transport-lost request may be billed without returning usage; Runtime
records such failed attempts as zero and cannot claim a hard monetary ceiling across them.

**In-band errors.** Validation runs immediately after the SDK call, outside the broad
SDK-exception catch and before partial text, tools, schema, usage, or cost extraction. It checks
`choices[0].error` first and defensive top-level `error` second; choice-level wins. Those OpenRouter
extension reads, plus `usage.cost`, accept attribute or mapping form, while ordinary successful
OpenAI fields retain their canonical SDK-attribute contract. `finish_reason="error"` without an
error object is transient `llm.network_error`.

Typed `error.metadata.error_type` classification precedes numeric status and is the exact immutable
map below:

| ActiveGraph reason | exact OpenRouter `error_type` values |
|---|---|
| `llm.auth_error` | `authentication`, `permission_denied` |
| `llm.rate_limited` | `rate_limit_exceeded` |
| `llm.network_error` | `provider_overloaded`, `provider_unavailable`, `server`, `timeout`, `unmapped` |
| `llm.request_error` | `payment_required`, `context_length_exceeded`, `max_tokens_exceeded`, `token_limit_exceeded`, `string_too_long`, `invalid_request`, `invalid_prompt`, `not_found`, `precondition_failed`, `payload_too_large`, `unprocessable`, `content_policy_violation`, `refusal`, `invalid_image`, `image_too_large`, `image_too_small`, `unsupported_image_format`, `image_not_found`, `image_download_failed` |

Unknown types fall through to `classify_provider_status`; OpenRouter contains no duplicate numeric
ladder. Only a true `int` error code becomes `status_code`, so booleans, numeric strings, floats,
and other malformed values normalize to `None`. Shared status policy makes 408 transient
`llm.network_error`, 409 and other non-auth/non-429 4xx terminal `llm.request_error`, 401/403 auth,
429 rate-limited, and 5xx/missing status network error. The same 408 rule applies to SDK-raised
exceptions.

In-band error extras contain exactly always-present `model: str` and `status_code: int | None`, plus
only bounded scalar `error_type` (128 characters), `provider_code` (128; metadata first, then a
non-integer error code), and `finish_reason` (64). No raw response, partial output, provider message,
metadata/error mapping, headers, exception, or volatile provider prose enters the event payload.
The inherited SDK-exception payload remains the separately characterized OpenAI contract.

## Sequence: one live LLM turn with a tool call and a hard cost budget

```mermaid
sequenceDiagram
    autonumber
    participant RT as Runtime._invoke_llm_body<br/>runtime.py:1993
    participant BH as LLMBehavior.build_prompt<br/>behaviors/base.py:146
    participant PA as assemble_prompt<br/>prompt.py:457
    participant CA as LLMCache<br/>cache.py:46
    participant PR as AnthropicProvider<br/>anthropic.py:83
    participant WI as wire.py
    participant SDK as anthropic SDK
    participant PS as parse_structured_response<br/>parsing.py:38

    RT->>BH: build_prompt(event, graph, frame, structured_output_mode)
    BH->>PA: assemble_prompt(...) [lazy import, base.py:161]
    PA->>PA: serialize_view + _strip_volatile(payload)
    PA-->>BH: AssembledPrompt
    BH-->>RT: AssembledPrompt
    RT->>RT: _hash_turn_prompt(prompt, tool_defs) [runtime.py:4527]
    RT->>CA: get(prompt_hash) [gated on replay_llm_cache]
    CA-->>RT: None (miss)
    RT->>PR: count_tokens(system, messages, model) [runtime.py:2088]
    PR-->>RT: input_tokens
    RT->>PR: estimate_cost(input_tokens, max_tokens, model) [runtime.py:2102]
    PR-->>RT: Decimal cost
    RT->>PR: complete(system, messages, model, tools=tool_defs, ...) [runtime.py:2237]
    PR->>WI: build_tool_name_map(tools) [wire.py:62]
    WI-->>PR: {wire_name -> canonical_name}
    PR->>SDK: messages.create(model, messages, tools, timeout)
    SDK-->>PR: content blocks incl. tool_use
    PR->>WI: restore_tool_name(name, name_map) [wire.py:87]
    WI-->>PR: canonical name
    Note over PR,PS: parsed skipped while tool_calls present<br/>anthropic.py:191-196
    PR-->>RT: LLMResponse(tool_calls=[ToolCall(...)])
    RT->>RT: invoke tool, append role="tool" LLMMessage
    RT->>PR: complete(... running_messages + tool result ...)
    PR->>SDK: messages.create(...)
    SDK-->>PR: final text
    PR->>PS: parse_structured_response(text, output_schema)
    PS-->>PR: parsed model | raise LLMBehaviorError
    PR-->>RT: LLMResponse(raw_text, parsed)
    RT->>CA: record(prompt_hash, response, requesting_event_id) [runtime.py:2364]
    RT->>RT: emit llm.responded
```

On the failure path, `classify_provider_exception` (`wire.py:134-161`) maps the SDK exception to a
reason code, the provider raises `LLMBehaviorError` with `retry_after_seconds` lifted through the
shared header parser (`wire.py:164-177`, `anthropic.py:175-186`), and the runtime logs an
`llm.responded` carrying an `error` object (`runtime.py:2872-2918`). If the reason is in
`_TRANSIENT_LLM_REASONS` (`runtime.py:4435`), the runtime re-enters at the same turn hash after a
blocking sleep (`runtime.py:2271-2281`).

## Finding status

### Still open or intentionally absent

1. **The native-capability `getattr` fallback is meaningful primarily for duck-typed providers.**
   `_resolve_structured_output_mode` guards
   `supports_native_structured_output` with `getattr(..., None)` (`runtime.py:1214-1230`). A direct
   `LLMProvider` subclass that omitted the override would inherit the Protocol stub and receive a
   falsy `None`, while a duck-typed provider such as `RecordedDiligenceProvider` has no attribute at
   all. Both outcomes correctly select prompt mode; the two paths simply reach the same result by
   different Python mechanisms.

2. **There is no proactive rate limiter, concurrency cap, RPM tracker, or circuit breaker in the
   LLM path.** Rate limiting is reactive: a classified 429 may honor `retry-after`, then the runtime
   blocks its current thread before retrying (`wire.py:113-177`, `runtime.py:2255-2281`). This is a
   documented absence, not an implied package responsibility.

3. **The package/top-level export split is intentional.** `activegraph.llm.__all__` includes all
   four providers, capability symbols, `sanitize_tool_name`, and `native_schema_compatible`
   (`llm/__init__.py:44-98`), while the top-level `activegraph` package exports LLM error types but
   no provider classes.

### Resolved findings from the 2026-08 repair sets

- **08.1–08.3 resolved:** `AssembledPrompt.hash()` is now documented as public content identity,
  all prompt hashes share `prompt_identity.py`, and runtime-supplied declared determinism is the
  canonical fixture identity with a bounded legacy read fallback (`prompt.py:20-25`,
  `prompt_identity.py:19-64`, `recorded.py:96-181`).
- **08.4 resolved:** Anthropic's `_pricing_for` docstring now states only the fallback it actually
  performs (`anthropic.py:42-56`).
- **08.6 resolved:** exception classification and `retry-after` parsing are shared in `wire.py`,
  with Anthropic/OpenAI importing the shared functions (`wire.py:134-177`, `anthropic.py:35-36`,
  `openai.py:46-47`).
- **08.7–08.8 resolved:** `native.py` defines `__all__`, and the package exports
  `native_schema_compatible` plus `sanitize_tool_name` (`native.py:25`, `llm/__init__.py:50-68`).
- **08.9 reconciled:** the component map and outbound dependency table above distinguish `core/`,
  top-level `frame.py`, and top-level `errors.py`.
- **08.10 resolved:** `RecordedDiligenceProvider` now documents the actual `behavior named "..."`
  and `## Triggering event` probes (`packs/diligence/fixtures/__init__.py:56-74`, `:152-180`).
