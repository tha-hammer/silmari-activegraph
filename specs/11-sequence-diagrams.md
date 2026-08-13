# End-to-End Sequence Diagrams

Six flows that cross subsystem boundaries. Each diagram is preceded by the prose that carries the
`file:line` evidence; the diagrams themselves stay free of citations so they remain readable.
Only steps substantiated by the subsystem research are shown — where a flow is knowingly
incomplete (the sandbox's out-of-repo orchestrator, for instance) that is stated rather than
invented.

Subsystem detail lives in [01-core.md](01-core.md) … [10-observability-trace-cli.md](10-observability-trace-cli.md);
the dependency graph is in [00-overview.md](00-overview.md).

---

## 1. A pack loads and its behaviors reach the runtime

`Runtime.load_pack(pack, settings=None) -> bool` (`activegraph/runtime/runtime.py:3261-3273`) is a
thin front door over `load_pack_into_runtime` (`activegraph/packs/loader.py:55-322`). Pack
contributions are committed only after the loader revalidates tool membership (`:63-66`), builds
and applies settings (`:106-109`), completes the conflict scan (`:110-232`), and constructs all
canonical behavior and tool copies (`:234-255`). The contribution mutation begins with
`state.loaded_packs[pack.name] = pack` at `:259`. One internal caveat narrows the docstring's
stronger “runtime exactly unchanged” wording: `_ensure_pack_state` initializes the otherwise-empty
lazy state container at `:69` before settings validation, so a failed *first* settings build can
leave that empty container installed. A later failure from the attached store or a listener while
emitting `pack.loaded` can also propagate after the contribution mutations at `:259-304`; the
atomicity guarantee therefore covers the loader's own validation/conflict failures, not arbitrary
failures at that late event boundary. Behaviors and tools themselves are never mutated in place —
`_wrap_behavior_for_pack` (`:673-759`) and `_rename_tool` (`:822-837`) return fresh objects under
the canonical name `{pack}.{short}`, which are appended to `_pack_behaviors` (`:292-295`) and
`_pack_tools` (`:269`).

Object types and relation types are the asymmetry worth remembering: they are **not** prefixed and
occupy a flat global namespace, so a cross-pack collision raises `PackConflictError`
(`activegraph/packs/loader.py:189-202`, registrations at `:277-284`). Two packs contributing the
same *short* behavior or tool name is legal — the short-name lookup table is poisoned with the
`AMBIGUOUS` sentinel (`activegraph/packs/loader.py:533-544`) so an unqualified
`rt.get_behavior(name)` raises rather than silently picking one
(`activegraph/runtime/runtime.py:3419-3459`).

Nothing is registered with the dispatch `Registry` at load time. The loader sets
`rt.registry = None` and the merge happens on the next entry point, inside `_ensure_registry`
(`activegraph/runtime/runtime.py:1243-1328`), which rebuilds `tool_registry` from scratch on
**every** call. The loader also installs the schema-validator callbacks onto the graph
(`activegraph/packs/loader.py:918-926`) and emits `pack.loaded` (`:306-318`) with the byte-stable
settings value built at `:973-1013`.

```mermaid
sequenceDiagram
    autonumber
    participant App as caller
    participant RT as Runtime
    participant L as packs.loader
    participant S as PackRuntimeState
    participant G as core.Graph
    participant R as runtime.Registry

    App->>RT: load_pack(pack, settings)
    RT->>L: load_pack_into_runtime(rt, pack, settings)
    L->>L: revalidate behavior-to-tool membership
    L->>S: lazily ensure PackRuntimeState
    L->>S: read loaded_packs for idempotency and version
    alt same (name, version) already loaded
        L-->>RT: False, nothing mutated
    else same name at a different version
        L-->>RT: raise PackVersionConflictError
    end
    L->>L: build settings and apply recorded fork overrides
    L->>L: conflict scan over canonical names, flat types, global exports
    L->>L: construct canonical behavior and tool copies
    L->>S: loaded_packs[name] = pack, pack_settings[name] = obj
    L->>RT: extend _pack_tools and _pack_behaviors
    L->>RT: rt.registry = None
    L->>G: set _pack_object_validator and _pack_relation_validator
    L->>G: emit(pack.loaded)
    L->>L: manifest warning tier, logs WARNING, never raises
    L-->>RT: True
    RT-->>App: True

    App->>RT: run_goal(...) or run_until_idle()
    RT->>RT: _ensure_registry()
    RT->>R: Registry(global registry + _pack_behaviors)
    RT->>RT: rebuild tool_registry from tools + _pack_tools + LLMBehavior.tools
```

---

## 2. A behavior fires: dispatch, read, mutate, persist, observe

`Graph.emit` is the hinge. Its ordering is contractual (CONTRACT v1.8 #2,
`activegraph/core/graph.py:584-625`): validate → append to the in-memory log → `apply_event`
project → `store.append` → offer to sinks → **release the lock** → call listeners synchronously.
Sinks are offered *before* legacy listeners so a listener re-entering `emit` cannot reverse
observation order (`activegraph/core/graph.py:597-620`), and listeners run outside the `RLock` so a
listener that waits on a second emitting thread cannot deadlock (`:621-625`).

The runtime is one of those listeners. `Runtime._on_event`
(`activegraph/runtime/runtime.py:1060-1092`) counts every event and records LLM/tool event metrics,
then returns early for the promote-quiescent flag (`:1070-1075`) or when the centralized event
policy says the type does not schedule behaviors (`:1076-1079`). That policy suppresses the
prefixes `behavior.`, `relation_behavior.`, `runtime.`, `llm.`, `tool.`, `pattern.`, `approval.`,
`embedding.`, `dev.`, and `authority.`, plus the exact type `context.read`
(`activegraph/runtime/event_policy.py:13-53`). Everything else is pushed onto a bare `deque` FIFO
(`activegraph/runtime/queue.py:11-27`).

The drain loop (`activegraph/runtime/runtime.py:1697-1734`) pops, consumes `max_events`, advances
`_tick`, and asks `Registry.match(event, graph)`
(`activegraph/runtime/registry.py:91-104`) which returns matches in **registration order**, then
dispatches or schedules them. `_invoke` (`activegraph/runtime/runtime.py:1822-1908`) builds the
`View` via `build_view` (`activegraph/runtime/view_builder.py:16-52`), wraps the graph in a
`BehaviorGraph` (`activegraph/runtime/behavior_graph.py:35-169`), emits `behavior.started`, calls
`b.run(event, bgraph, ctx)` (`activegraph/runtime/runtime.py:1868`), and on any exception other
than `ReplayDivergenceError` emits `behavior.failed` rather than propagating (`:1869-1887`).

Every mutation the behavior makes re-enters `Graph.emit` at the top of this diagram, carrying
framework-written provenance the behavior cannot forge — `actor`, `caused_by`, `frame_id`, plus
`llm_request_event_id` and `tool_request_event_ids` for LLM behaviors
(`activegraph/runtime/behavior_graph.py:35-67`, stamped by `Graph._provenance` at
`activegraph/core/graph.py:994-1021`). The five mutation counters ride the `behavior.completed`
payload (`activegraph/runtime/runtime.py:1894-1905`).

```mermaid
sequenceDiagram
    autonumber
    participant G as core.Graph
    participant ST as store.EventStore
    participant SK as sinks.SinkHandle
    participant RT as Runtime
    participant REG as runtime.Registry
    participant VB as runtime.view_builder
    participant B as behavior function
    participant BG as BehaviorGraph

    Note over G: an event has just been submitted
    G->>G: validate_event, append to log, apply_event projection
    G->>ST: append(event)
    G->>SK: _offer(event, DeliveryContext(run_id, sequence, live))
    Note over SK: non-blocking, non-throwing, worker thread delivers
    G->>RT: _on_event(event), listeners run outside the emit lock
    RT->>RT: counter activegraph_events_emitted_total
    alt event policy suppresses scheduling, or promote-quiescent
        RT-->>G: suppressed, never enqueued
    else
        RT->>RT: queue.push(event), gauge activegraph_queue_depth
    end

    RT->>RT: _loop pops event, consumes max_events, advances _tick
    RT->>REG: match(event, graph)
    REG-->>RT: triples in registration order
    RT->>VB: build_view(behavior, event, graph)
    VB->>G: all_objects / neighborhood / objects_in_types / recent events
    VB-->>RT: View snapshot
    RT->>BG: BehaviorGraph(graph, actor, caused_by, frame_id)
    RT->>G: emit(behavior.started)
    RT->>B: b.run(event, bgraph, ctx)
    B->>BG: add_object(type, data)
    BG->>G: add_object with stamped actor, caused_by, frame_id
    G->>G: reject reserved fields, run pack validator, write provenance
    G->>G: emit(object.created)
    Note over G: re-enters this diagram at step 1
    B-->>RT: returns None, the value is ignored
    alt handler raised
        RT->>G: emit(behavior.failed with traceback)
    else
        RT->>G: emit(behavior.completed with five mutation counters)
    end
    opt trace_context_reads enabled and read set non-empty
        RT->>G: emit(context.read with capped object ids)
    end
    RT->>RT: _fire_due_delayed() after every tick
```

---

## 3. An LLM behavior's turn loop calls a tool

`_invoke_llm` (`activegraph/runtime/runtime.py:1910-2000`) prepares the view, context,
`BehaviorGraph`, metrics, and `behavior.started` event, then delegates to `_invoke_llm_body`
(`:2002-2561`). Keeping the turn loop in that body lets the wrapper emit one context-read trace
after every normal body exit without duplicating it across the body's many failure returns
(`:1977-2000`). A propagating `ReplayDivergenceError` skips that commit.

Prompt assembly is pure and lives in `llm/`: `LLMBehavior.build_prompt`
(`activegraph/behaviors/base.py:146-200`) lazily calls `assemble_prompt`
(`activegraph/llm/prompt.py:458-483`), which serializes the `View` in a **locked,
snapshot-tested format** (`activegraph/llm/prompt.py:106-118`) and recursively strips
`provenance`, `timestamp`, and `run_id` keys from the event payload so a fork's regenerated prompt
still hits cache (`:360-396`).

The runtime cache key is `_hash_turn_prompt` (`activegraph/runtime/runtime.py:4527-4555`), which
uses the shared prompt-identity builder and explicitly includes the current running messages and a
`tools` key. It is deliberately a different identity domain from the public
`AssembledPrompt.hash()`, which omits `tools` (`activegraph/llm/prompt_identity.py:19-55`;
`activegraph/llm/prompt.py:82-103`). Cache reads are gated on `replay_llm_cache`
(`activegraph/runtime/runtime.py:2078-2081`); successful live responses are recorded regardless
of that read flag (`:2361-2368`). A cache hit forces `max_attempts = 1` (`:2123-2128`).

Retry attempts and sleeps are owned by the runtime; provider adapters only classify failures and
surface any `retry_after_seconds`. The transient set is
`{"llm.network_error", "llm.rate_limited"}` (`activegraph/runtime/runtime.py:4435-4439`); a retry
emits a fresh `llm.requested` carrying `attempt_index`, `max_attempts`, and `retry_of`
(`:2160-2164`), and the delay is a blocking `time.sleep` on the runtime thread
(`:2271-2281`, `:2311-2321`).

Tool dispatch runs inside the turn. The response's tool names are canonicalized against the
behavior's resolved declarations before the response is cached or emitted; an undeclared name
fails as `tool.unknown_tool` (`activegraph/runtime/runtime.py:2344-2359`). For each authorized call,
the specific `max_tool_calls` gate runs before the generic remaining-budget gate and defensive
declaration lookup (`:2409-2449`). `_invoke_tool` (`:2565-2781`) validates input before its cache
and cost gates; schema-invalid input still emits a complete `tool.requested` / `tool.responded`
error pair (`:2583-2628`). A valid output-schema result is normalized with
`model_dump(mode="json")` when supported (`:2713-2723`), placed in `tool.responded` (`:2752-2768`),
and JSON-encoded into `LLMMessage(role="tool", ...)` for the next turn (`:2770-2780`). A tool cost
rejection occurs before `tool.requested` (`:2639-2651`) and therefore has no tool-event pair.

Provenance for the whole turn is stamped just before the handler runs (`:2525-2530`) so every
object the handler creates carries the `llm.requested` id whose response actually fed it, plus
every `tool.requested` id from the loop.

```mermaid
sequenceDiagram
    autonumber
    participant RT as Runtime turn loop
    participant PB as behaviors.build_prompt
    participant PA as llm.prompt.assemble_prompt
    participant C as llm.LLMCache
    participant P as llm.LLMProvider
    participant TI as tools invoker
    participant H as LLM handler
    participant G as core.Graph

    RT->>RT: resolve tool_defs from behavior.tools
    RT->>PB: build_prompt(event, graph, frame, structured_output_mode)
    PB->>PA: assemble_prompt(view, event, frame, schema, ...)
    PA-->>PB: AssembledPrompt, view format is locked
    PB-->>RT: AssembledPrompt
    RT->>RT: record prompt-serialized object ids into the read set

    loop each turn, up to max_tool_turns
        RT->>RT: _hash_turn_prompt(prompt, messages, tool_defs)
        opt replay_llm_cache enabled
            RT->>C: get(prompt_hash)
        end
        opt cache miss and a cost limit exists
            RT->>P: count_tokens + estimate_cost, only when a cost limit exists
        end
        loop provider attempts, or one cache attempt
            RT->>G: emit(llm.requested)
            alt cached response found
                C-->>RT: cached LLMResponse
            else cache miss
                RT->>P: complete(system, messages, model, tools, output_schema)
                alt provider failure
                    P-->>RT: classified reason code
                    RT->>G: emit(llm.responded with error block)
                    Note over RT: retry only for llm.network_error or llm.rate_limited
                else success
                    P-->>RT: LLMResponse
                end
            end
        end
        opt live response
            RT->>RT: budget.add_cost(response.cost_usd)
        end
        RT->>RT: canonicalize and authorize returned tool names
        opt live authorized response
            RT->>C: record(prompt_hash, response)
        end
        RT->>G: emit(llm.responded)

        alt response has no tool_calls
            Note over RT: final turn, leave the loop
        else response has tool_calls
            loop each ToolCall
                RT->>RT: max_tool_calls and remaining-budget gates
                RT->>RT: defensive declaration lookup
                RT->>RT: validate input
                alt input schema invalid
                    RT->>G: emit(tool.requested), then tool.responded(error)
                else valid input
                    RT->>RT: replay-cache lookup and cost gate
                    RT->>G: emit(tool.requested)
                    alt cached tool response
                        RT->>RT: use cached response
                    else live tool call
                        RT->>TI: invoke(tool, input_obj, ToolContext)
                        TI-->>RT: CachedToolResponse or ToolError
                    end
                    RT->>RT: validate output schema and normalize to JSON values
                    RT->>G: emit(tool.responded)
                    RT->>RT: append LLMMessage(role=tool) to running messages
                end
            end
        end
    end

    RT->>RT: stamp llm_request_event_id and tool_request_event_ids on the BehaviorGraph
    RT->>H: handler(event, bgraph, ctx, response.parsed)
    H->>G: mutations, each carrying the stamped provenance
    RT->>G: emit(behavior.completed with tool_calls count)
```

---

## 4. A run is persisted, then re-opened by the CLI

On the write side, serialization is a **pre-check, not a post-hoc encode**: `Graph.emit` calls
`validate_event` (`activegraph/store/serde.py:201-203`) *before* touching any state, so a
non-serializable payload can never enter the in-memory log either
(`activegraph/core/graph.py:584-596`). The guard is intentionally conditional on a store being
attached, so a store-less graph accepts unserializable payloads. The
encode adapters narrow `Decimal → str`, `datetime/date → ISO 8601`, `set → sorted list`
(`activegraph/store/serde.py:40-48`), and the narrowing is **one-way**: decoding does not
reconstruct those types (`:8-11`).

Ordering authority in SQLite is the `seq` AUTOINCREMENT column, never `timestamp` — *"wall clocks
can lie; AUTOINCREMENT cannot"* (`activegraph/store/sqlite.py:28-29`); every `iter_events` sorts
`ORDER BY seq` (`:366-381`). Event ids are unique per `(id, run_id)` rather than globally,
precisely so a fork can preserve the parent's `evt_017` (`activegraph/store/sqlite.py:31-36`,
schema at `:59-72`).

For its default status path, `activegraph inspect <url>` (`activegraph/cli/main.py:224-325`) selects
the run with `_most_recent_run_id_or_die` when `--run-id` is absent (`:92-125`, call at `:295`),
then constructs the runtime via `Runtime.load` — never a bare `Runtime(...)` — at `:301`. It calls
`rt.status(recent=tail)` (`activegraph/runtime/runtime.py:3069-3184`) and renders the snapshot,
using `status_to_dict` (`activegraph/observability/status.py:82-89`) for `--json`. `inspect` does not
call `_open_store_or_die`; `Runtime.load` owns the store open on this path.

`Runtime.load` (`activegraph/runtime/runtime.py:3745-3903`) is where the round trip closes: if the
first event is `runtime.snapshot` it materializes the compaction snapshot with a hash check that
fails loud (`_materialize_snapshot`, `:5031-5091`, raising `SnapshotIntegrityError`), replays every
event through `graph._replay_event` — the *silent* mutator that never persists, never offers sinks
and never fires listeners (`activegraph/core/graph.py:630-638`) — then reseeds the id generators
(`activegraph/core/ids.py:90-136`), attaches the store, and re-pushes any non-lifecycle events
emitted after the last `runtime.idle` high-water mark (`_requeue_unfired`,
`activegraph/runtime/runtime.py:4648-4725`). Sinks are attached **last**, after replay and strict
verification, so a load never redelivers history (`:3895-3898`).

```mermaid
sequenceDiagram
    autonumber
    participant B as behavior
    participant G as core.Graph
    participant SD as store.serde
    participant GS as core.GraphStore
    participant DB as SQLiteEventStore

    B->>G: add_object / patch_object / emit
    opt a store is attached
        G->>SD: validate_event(event) before any state change
        SD-->>G: ok, or NonSerializableEventError naming the field path
    end
    G->>G: append to _events
    G->>GS: put_object / put_relation via apply_event
    G->>DB: append(event)
    DB->>SD: encode_event, payload to JSON text
    DB->>DB: INSERT with UNIQUE(id, run_id), autocommit, seq assigned

    Note over G,DB: later, a separate process

    participant CLI as cli.main
    participant RT as Runtime
    opt --run-id omitted
        CLI->>DB: most_recent_run_id(url)
    end
    CLI->>RT: Runtime.load(url, run_id)
    RT->>DB: open the run-scoped store
    RT->>DB: iter_events() ordered by seq
    DB->>SD: decode_event per row
    SD-->>RT: Event objects, or CorruptedEventPayloadError
    opt first event is runtime.snapshot
        RT->>DB: get_snapshot(state_hash)
        RT->>RT: verify state_hash_of(blob), else SnapshotIntegrityError
        RT->>GS: put_object / put_relation from the blob, prime id counters
    end
    loop remaining events
        RT->>G: _replay_event(ev), silent - no persist, no sinks, no listeners
    end
    RT->>G: ids.reseed_from_events(events)
    RT->>G: attach_store(store)
    RT->>RT: _requeue_unfired since the last runtime.idle
    RT->>RT: attach sinks last, so load never redelivers history
    CLI->>RT: status(recent=tail)
    RT-->>CLI: RuntimeStatus frozen-dataclass snapshot
    CLI->>CLI: status_to_dict for --json, or formatted text
```

---

## 5. A candidate pack is trialed in the sandbox

The division of authority is fixed: **the parent forks, the child only appends to that fork's run
id** (`activegraph/sandbox/__init__.py:11-14`). `run_forked_trial`
(`activegraph/sandbox/__init__.py:546-575`) is a compatibility wrapper over
`_run_forked_trial_local` (`:391-543`), which since CONTRACT v1.8 #9–#12 sits behind the
serialized, provider-neutral `TrialExecutor` protocol (`activegraph/sandbox/executor.py:224-269`).

The parent creates the fork with full `fork()` semantics — SQLite-only, promote-block cut guard —
then **drops its handle** (`del fork_rt`, `activegraph/sandbox/__init__.py:435-439`). Two channels
cross into the child and are kept strictly separate (`:196-245`): the environment is a **closed
allow-list** of `PATH`, `HOME`, `LANG` plus explicit `env_passthrough`, so no ambient API key
reaches candidate code; the code location is an **explicit** `PYTHONPATH` computed from the
parent's resolved `sys.path`, never forwarded from ambient env.

Materialization inside the child is **pin-first** and the order is load-bearing
(`activegraph/sandbox/_child.py:120-169`): `verify_bundle_hash` **before any import**, then
`load_manifest`, then import, then `verify_surface` two-way against the live `Pack`. Schema v2
requires an exact `sha256:` plus 64 lowercase hexadecimal pin for the candidate and every extra
(`activegraph/sandbox/__init__.py:102-126`); the child verifies it unconditionally even when
manifest checks are disabled (`activegraph/sandbox/_child.py:140-144`). The serialized-spec reader
accepts schema v1 only as a pinned migration input, normalizes it to v2, and rejects missing or
invalid v1 pins before the parent fork seam (`activegraph/sandbox/executor.py:77-118`, `:326-349`;
2026-08-12 Set 4 amendment #4). This resolves the original audit finding that bundle pins defaulted
off.

Three independent nets bound the trial: requested rlimits in the child (`RLIMIT_AS`, `RLIMIT_CPU`,
which only ever *lower* and turn `setrlimit` failures into warnings rather than crashing —
`activegraph/sandbox/_child.py:57-109`), a parent-side wall-clock kill
(`activegraph/sandbox/__init__.py:274-305`), and the runtime's own `Budget`
(`activegraph/sandbox/_child.py:242-260`). Key-freedom is structural: the
child configures no LLM provider at all, so an LLM-calling candidate fails loud at registration
(`activegraph/sandbox/_child.py:247-260`).

Classification is total and closed (`activegraph/sandbox/__init__.py:464-479`), and **the store is
the record for counts** — `events_appended` and `behavior_failures` are re-read from the fork's run
after the child exits; the stdout tail supplies outcome/detail rather than authoritative counts
(`:502-531`). On timeout the parent appends its own `trial.wall_clock_exhausted` marker
(`:507-527`). Because `events_appended` is calculated after that append (`:528-530`), the timeout
count includes this one parent-authored marker in addition to child-authored events.

Not verifiable from this repo: the orchestration *around* the trial — proposal, static gate, promote
— lives in the out-of-repo `activegraph-packs` evolution pack. Nothing under `activegraph/` calls
`run_forked_trial`.

```mermaid
sequenceDiagram
    autonumber
    participant O as orchestrator, out of repo
    participant SB as sandbox parent
    participant PRT as parent Runtime
    participant DB as SQLite store
    participant CH as child process
    participant CRT as child Runtime

    O->>SB: run_forked_trial(store_path, parent_run_id, at_event, pack_source, limits)
    SB->>PRT: Runtime.load(store_path, run_id=parent_run_id, behaviors=[])
    SB->>PRT: fork(at_event, label, behaviors=[])
    Note over PRT: enforces SQLite-only and the promote-block cut guard
    PRT->>DB: fork_run copies rows into a new run id
    PRT-->>SB: fork_run_id, initial_events
    SB->>SB: del fork_rt, the child owns the fork from here
    SB->>SB: build closed-allowlist env plus computed PYTHONPATH
    SB->>CH: Popen(python -m activegraph.sandbox._child), job JSON on stdin

    CH->>CH: _apply_rlimits, only ever lowers, degrades to warnings
    CH->>CH: verify_bundle_hash BEFORE any import
    CH->>CH: load_manifest, then import pack, then verify_surface
    alt pin, manifest or surface violation
        CH-->>SB: report materialization_failed, exit 50
    else
        CH->>CRT: Runtime.load(store_path, run_id=fork_run_id, behaviors=[], budget)
        Note over CRT: no llm_provider is configured, key-freedom is structural
        CH->>CRT: load_pack for each extra pack, then the candidate
        CH->>CRT: scenario(rt) or run_until_idle()
        CRT->>DB: appends events to the fork run only
        CH-->>SB: one JSON report line on stdout, exit 0 / 30 / 40
    end

    SB->>SB: classify outcome from timeout, tail, then exit code
    SB->>PRT: Runtime.load(fork_run_id) to re-read the store
    opt parent wall-clock deadline elapsed
        Note over SB,CH: _run_child already killed and reaped the child
        SB->>PRT: emit trial.wall_clock_exhausted marker
    end
    SB->>PRT: derive counts from the reloaded fork
    SB-->>O: TrialReport(outcome, fork_run_id, events_appended, behavior_failures, warnings)
```

---

## 6. A fork is promoted into its parent

`Runtime.promote(fork, *, dry_run=False)` (`activegraph/runtime/runtime.py:4110-4409`) is
**fail-closed and atomic**: any conflict raises `PromoteConflictError` *before the first mutation*
(`:4242-4249`), and there is no `force=` flag — the escape hatch is to re-fork
(`activegraph/runtime/promote.py:14-17`).

Preconditions are checked in a fixed order and lineage comes from the **store**, not from what the
caller claims: both runtimes must be on `SQLiteEventStore` (`:4160-4193`) and on the same file path
(`:4194-4203`); `fork_store.get_run().parent_run_id` must equal `self.run_id` (`:4205-4216`); a
`forked_at_event_id` must be recorded (`:4217-4224`). Grandchildren promote one level at a time.

The plan is a **pure three-way comparison**, not event replay. `build_base_graph`
(`activegraph/runtime/promote.py:160-181`) replays the parent's log up to `forked_at_event` into a
throwaway `Graph`, and `compute_promote_plan` (`:188-353`) compares base / parent-now / fork-now:
fork-only changes promote, both-sides changes conflict — *including identical concurrent edits*,
because v1 makes no semantic judgment (`:241-247`) — and parent-only changes are left alone.
Referential integrity is part of the conflict check, via `dangling_relation` (`:293-313`) and
`orphaning_removal` (`:315-336`). Relation "patches" are modelled as a remove+create pair sharing
one id, which is why `PromotePlan` has no `relation_patches` field (`:277-281`).

Application is **quiescent**: the `promote.applied` marker is emitted *first*, then
`_promote_quiescent` is raised (`activegraph/runtime/runtime.py:4282-4325`) so the delta events
project and persist but never enqueue for behavior matching — the marker is the single reaction
point (`activegraph/runtime/runtime.py:1070-1079`). Apply order is load-bearing: relation removals
→ object removals → object creates → object patches → relation creates (`:4326-4398`). Sinks still
observe the delta events even though scheduling ignores them. Finally the id generators are
reseeded past the promoted ids so future mints cannot collide (`:4401-4403`).

Pack code is never adopted — fork-only `pack.loaded` and `pack.settings_overridden` events surface
as plan **warnings** only (`activegraph/runtime/promote.py:356-411`) — but the delta *is*
revalidated against this runtime's pack schemas before it is applied
(`activegraph/runtime/runtime.py:4251-4280`).

```mermaid
sequenceDiagram
    autonumber
    participant Op as caller or cli promote
    participant PRT as parent Runtime
    participant DB as SQLite store
    participant PM as runtime.promote
    participant BG as base Graph, throwaway
    participant G as parent core.Graph
    participant SK as sinks

    Op->>PRT: promote(fork_rt, dry_run)
    PRT->>DB: require both stores to be SQLite
    opt either store is not SQLite
        PRT-->>Op: raise IncompatibleRuntimeState
    end
    PRT->>DB: require both SQLite stores to use the same path
    opt store paths differ
        PRT-->>Op: raise PromoteLineageError
    end
    PRT->>DB: fork_store.get_run() for parent_run_id and forked_at_event_id
    opt lineage does not check out
        PRT-->>Op: raise PromoteLineageError
    end
    PRT->>PM: promote_warnings(parent_rt, fork_rt, forked_at_event)
    PM-->>PRT: fork-only pack loads and settings overrides, advisory only
    PRT->>PM: compute_promote_plan(parent_graph, fork_graph, forked_at_event)
    PM->>BG: build_base_graph replays the parent up to forked_at_event
    PM->>PM: three-way compare base / parent-now / fork-now
    PM-->>PRT: PromotePlan with creates, patches, removes, conflicts, warnings

    alt dry_run
        PRT-->>Op: PromotePlan, computed_against records the parent tip
    else conflicts present
        PRT-->>Op: raise PromoteConflictError, nothing mutated
    else
        PRT->>G: revalidate the delta against this runtime's pack validators
        PRT->>G: emit(promote.applied) marker first
        Note over PRT: marker is the single queue-visible reaction point
        PRT->>PRT: _promote_quiescent = True
        PRT->>G: relation removals, object removals, object creates, patches, relation creates
        G->>SK: each delta event is still offered to sinks
        Note over PRT: delta events project and persist but never enqueue
        PRT->>PRT: _promote_quiescent = False
        PRT->>G: ids.reseed_from_events past the promoted ids
        PRT-->>Op: PromoteResult(plan, marker_event_id, applied_event_ids)
    end
```
