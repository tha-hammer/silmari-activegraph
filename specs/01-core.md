# Core — Event-Sourced Graph Data Model

## Responsibility

`core/` is the event-sourced graph data model and its projector. The append-only event log is
the source of truth (CONTRACT #2) and `Graph.emit` is the only live mutator; objects, relations,
patches, and views are all *projections* derived from that log
(`activegraph/core/event.py:1-6`, `activegraph/core/graph.py:1-23`). The subsystem owns five
primitives — `Event`, `Object`, `Relation`, `Patch`, `View` — plus the ID generator, the clock
abstraction, the `where` predicate evaluator, and the `GraphStore` ABC that storage backends
implement against. Its boundary is stated in one line: *"Core primitives. Knows nothing about
runtime or behaviors."* (`activegraph/core/__init__.py:1`).

That boundary is enforced structurally: **`core/` has zero module-level imports of any other
`activegraph` subpackage.** Every cross-package reference is either under `if TYPE_CHECKING:` or
a function-local deferred import, so at module-import time core depends on nothing outside
`activegraph.core`.

## Component map

```mermaid
graph TD
    subgraph core["activegraph/core"]
        Event["Event<br/>frozen dataclass<br/>event.py:14"]
        Graph["Graph<br/>log + projection + listeners + sinks<br/>graph.py:152"]
        apply["apply_event()<br/>THE projector<br/>graph.py:1001"]
        Object["Object<br/>graph.py:48"]
        Relation["Relation<br/>graph.py:76"]
        Patch["Patch<br/>patch.py:20"]
        View["View<br/>point-in-time slice<br/>view.py:16"]
        IDGen["IDGen<br/>ids.py:41"]
        Clock["Clock / FrozenClock / TickingClock<br/>clock.py:8"]
        evalwhere["evaluate_where()<br/>graph.py:1124"]
        GS["GraphStore (ABC)<br/>graph_store.py:58"]
        IMGS["InMemoryGraphStore<br/>graph_store.py:294"]
    end

    Graph -->|"emit / _replay_event<br/>sole callers"| apply
    Graph -->|"mints ids"| IDGen
    Graph -->|"reads time"| Clock
    Graph -->|"objects(where=)"| evalwhere
    View -->|"objects(where=)"| evalwhere
    apply -->|"put_/get_/remove_"| GS
    IMGS -.->|implements| GS
    apply -->|projects into| Object
    apply -->|projects into| Relation
    apply -->|projects into| Patch
    Graph -->|"appends to _events"| Event
    View -.->|"copies of live instances"| Object

    Graph ==>|"deferred: validate_event / store.append"| store["store/ EventStore"]
    Graph ==>|"deferred: _offer(event, ctx)"| sinks["sinks/ SinkHandle"]
    Graph ==>|"deferred: NoOpMetrics"| obs["observability/ Metrics"]
    Graph -.->|"deferred, error classes only"| rt["runtime/ exec_errors"]
```

Solid arrows are intra-core. Bold arrows (`==>`) are core's outbound calls, all via deferred
imports. The dotted arrow to `runtime/` is a naming-only coupling (exception classes), not a
behavioral dependency.

## Key types & entry points

- `Event` — frozen dataclass; one immutable record in the append-only log. Fields `id, type, payload, actor, frame_id, caused_by, timestamp` — `activegraph/core/event.py:14-43`
- `Object` — typed node in the projection: `id, type, data, version, provenance` — `activegraph/core/graph.py:48-73`
- `Relation` — typed edge: `id, source, target, type, data, provenance`. Dangling endpoints are legal — `activegraph/core/graph.py:76-103`
- `Patch` — proposed single-target mutation: `id, target, op, value, expected_version, proposed_by, rationale, evidence, status, rejection_reason, provenance` — `activegraph/core/patch.py:20-58`
- `PATCH_OPS = {"create","update","replace","remove"}` — `activegraph/core/patch.py:17`
- `Graph` — the aggregate: log + projection + listeners + sinks + optional `EventStore` — `activegraph/core/graph.py:152-995`
- `Graph.emit(event) -> Event` — **the only live mutator** — `activegraph/core/graph.py:564-606`
- `Graph._replay_event(event) -> None` — the replay mutator; silent (no persist, no sinks, no listeners) — `activegraph/core/graph.py:610-618`
- `apply_event(graph, event) -> None` — module-level projector; the ONLY function that mutates projected state (CONTRACT v0.5 #15) — `activegraph/core/graph.py:1001-1077`
- `Graph._provenance(...)` — framework-written provenance dict for every object/relation/patch — `activegraph/core/graph.py:968-995`
- `Graph._reject_reserved_fields(data, api=, param=)` — raises `ReservedFieldError` on reserved-key collision — `activegraph/core/graph.py:117-136`
- `RESERVED_DATA_FIELDS = frozenset({"provenance"})` — one table for all four mutation surfaces — `activegraph/core/graph.py:109-114`
- `GraphStore` (ABC) — pluggable materialized-state backend — `activegraph/core/graph_store.py:58-291`
- `InMemoryGraphStore` — the default, dict-backed, no-copy backend — `activegraph/core/graph_store.py:294-357`
- `ChainMatch` — frozen dataclass `{objects, relations}` returned by `match_chain` — `activegraph/core/graph_store.py:43-55`
- `View` — read-only point-in-time slice handed to behaviors as `ctx.view` — `activegraph/core/view.py:16-60`
- `IDGen` — per-graph monotonic ID generator + `reseed_from_events` — `activegraph/core/ids.py:41-136`
- `Clock` / `FrozenClock` / `TickingClock` — ISO-8601-second UTC time source — `activegraph/core/clock.py:8-63`
- `evaluate_where(where, root) -> bool` — predicate evaluator shared by `Graph.objects`, `View.objects`, and `runtime.registry` — `activegraph/core/graph.py:1124-1171`

## Interfaces & contracts at each seam

Inbound reach was verified by `grep -rn "activegraph\.core" activegraph/ --include="*.py"`
excluding `core/` itself: **13 of 13 other subpackages import core, and nothing imports core's
internals except via the names below.** The public re-export surface is
`activegraph/__init__.py:15-20` (`Clock, FrozenClock, TickingClock, Event, Graph, Object,
Relation, IDGen, Patch, View`); `GraphStore` / `InMemoryGraphStore` reach the top level
indirectly via `activegraph/store/__init__.py:14` and are listed in `__all__` at
`activegraph/__init__.py:178,181`.

### core <-> runtime (Graph control surface)

`runtime/` is core's largest consumer. `runtime/runtime.py:75-79` imports `Event`, `Graph`,
`evaluate_where as _evaluate_where`, `GraphStore`, `IDGen`, and `View` at module level. The
runtime constructs the `Graph`, attaches itself as a listener so behaviors fire on accepted
events, attaches the durable store, and drives replay on load/fork. Only five concrete control
calls cross this boundary; everything else runtime does with a graph is a read.

```ebnf
graph-construction  ::= Graph( [ids: IDGen] , [clock: Clock] ,
                               [run_id: str] , [graph_store: GraphStore] )
                        (* run_id defaults to ids.run() — a ULID *)

live-mutation       ::= graph.emit(Event) "->" Event
                      | sugar-mutation

sugar-mutation      ::= add_object(type, data, *, actor="system", caused_by?,
                                   frame_id?, evidence?, llm_request_event_id?,
                                   tool_request_event_ids?) "->" Object
                      | add_relation(source, target, type, data?, *, actor,
                                     caused_by?, frame_id?, ...) "->" Relation
                      | remove_object(object_id, *, actor, ...) "->" None
                      | remove_relation(relation_id, *, actor, ...) "->" None
                      | patch_object(target, updates, *, actor, rationale?,
                                     evidence?, ...) "->" Patch
                      | propose_patch(target, op, value, *, proposed_by,
                                      rationale?, evidence?, ...) "->" Patch
                      | apply_patch(patch_id, *, approved_by="system", ...) "->" Event
                      | reject_patch(patch_id, reason, *, actor, ...) "->" Event

replay-mutation     ::= graph._replay_event(Event) "->" None   (* silent *)

listener-attach     ::= graph.add_listener( (Event) "->" None ) "->" None
listener-detach     ::= graph._remove_listener(fn) "->" bool

store-attach        ::= graph.attach_store(EventStore) "->" None
                      | raises IncompatibleRuntimeState

read-surface        ::= objects(type?, where?) "->" [Object]
                      | query(object_type?, where?) "->" [Object]     (* alias *)
                      | relations(source?, target?, type?) "->" [Relation]
                      | get_relations(object_id?, type?, direction) "->" [Relation]
                      | neighborhood(object_id, depth=1) "->" ([Object],[Relation])
                      | match_chain([type?], [(rel_type?, dir)]) "->" [ChainMatch]
                      | objects_in_types([str]) "->" [Object]
                      | has_object_of_type(str) "->" bool
                      | get_object|get_relation|get_patch(id) "->" T | None
                      | all_objects() | all_relations() "->" [T]
                      | events "->" [Event]           (* property, copied list *)
                      | replayed_ids "->" frozenset[str]
                      | store "->" EventStore | None
direction           ::= "outgoing" | "incoming" | "both" | other  (* other == ignore object_id *)

errors              ::= ObjectNotFoundError          (* patch_object target miss;
                                                        also KeyError *)
                      | PatchNotFoundError            (* shared apply/reject catch *)
                      | ApplyPatchNotFoundError       (* apply miss; also KeyError,
                                                        never AttributeError *)
                      | RejectPatchNotFoundError      (* reject miss; also AttributeError,
                                                        never KeyError *)
                      | InvalidPatchLifecycleState   (* apply_patch on non-proposed *)
                      | ReservedFieldError           (* reserved key in caller data *)
                      | IncompatibleRuntimeState     (* second attach_store *)
                      | InternalEvaluatorError       (* unknown where operator *)
```

Call sites: `graph.add_listener(self._on_event)` — `activegraph/runtime/runtime.py:472`, listener
signature `(Event) -> None` defined at `activegraph/runtime/runtime.py:853`.
`graph.attach_store(store)` — `activegraph/runtime/runtime.py:512, :3246, :3319, :3494`.
`graph._remove_listener(self._on_event)` — `activegraph/runtime/runtime.py:544` (returns `bool`;
defined `activegraph/core/graph.py:357-365`). `graph._replay_event(ev)` —
`activegraph/runtime/runtime.py:3317` (load), `:3492` (fork), `:4310` (strict-replay verify),
plus `activegraph/runtime/promote.py:173` and `activegraph/store/retention.py:360`. `Graph(...)`
construction — `activegraph/runtime/runtime.py:3309, :3484, :4260`,
`activegraph/runtime/promote.py:170`, `activegraph/store/retention.py:358`.

Other runtime modules importing core types: `behavior_graph.py:16-18` (Event, Graph, Object,
Relation, Patch), `context_reads.py:47-48` (Object, View), `diff.py:19-20`, `patterns.py:45-46`
(TYPE_CHECKING only), `promote.py:37-39`, `queue.py:8`, `registry.py:20-21` (imports
`evaluate_where` for pattern `where` clauses), `view_builder.py:8-10`, `dev_override.py:8`.

Contract notes:

- **Emit ordering (CONTRACT v1.8 #2)** — `activegraph/core/graph.py:564-606`, strictly:
  (1) `validate_event` — only if a store is attached; (2) append to `_events`; (3) `apply_event`
  (project); (4) `store.append` (durable); (5) offer to sinks; (6) release lock; (7) call
  listeners synchronously. Sinks are offered *after* projection + durable append and *before*
  legacy listeners, so a listener re-entering `emit` cannot reverse observation order and a
  listener failure cannot suppress observation of an already-accepted event
  (`activegraph/core/graph.py:577-580`). Listeners run **outside** `_emit_lock` (an RLock,
  `activegraph/core/graph.py:191-196`) so a listener that spawns a second emitting thread and
  waits on it does not deadlock (`activegraph/core/graph.py:601-603`).
- **Replay is silent (CONTRACT v0.5 #14)** — `_replay_event` appends, projects, and records the
  id in `_replayed_ids`, but never persists, never offers sinks, never fires listeners
  (`activegraph/core/graph.py:610-618`): "replay rebuilds graph state; it does NOT fire
  behaviors."
- **Store attachment is once-per-graph-lifetime** — `attach_store` is idempotent on the same
  store; a *different* store raises `IncompatibleRuntimeState` with a full what/why/how-to-fix
  (`activegraph/core/graph.py:519-556`). Rationale: re-attach would either split the log across
  two stores or silently perform a migration.
- **Provenance is framework-written (CONTRACT #5 / v1.10 #2)** — every object/relation/patch
  carries provenance written by `Graph._provenance`, never by the caller
  (`activegraph/core/graph.py:968-995`). `_reject_reserved_fields` raises `ReservedFieldError`
  on collision from `add_object:636`, `add_relation:689`, `patch_object:793`,
  `propose_patch:850`. Pre-v1.10 this was a *silent strip*, which meant "a caller who thought
  they attached provenance had attached nothing" (`activegraph/core/graph.py:17-19`). Non-dict
  input passes through unchanged (`activegraph/core/graph.py:129-130`).
- **Versioning + optimistic concurrency (CONTRACT #4, #12)** — `Object.version` bumps by exactly
  1 on every `patch.applied` (`activegraph/core/graph.py:1069`). A `Patch.expected_version`
  mismatch does **not** raise — it emits `patch.rejected` with reason
  `"version mismatch: expected N, got M"` (`activegraph/core/graph.py:899-908`). `apply_patch` on
  a non-`proposed` patch raises `InvalidPatchLifecycleState`
  (`activegraph/core/graph.py:894-898`) because re-applying would emit a duplicate
  `patch.applied` and break replay determinism (`activegraph/runtime/exec_errors.py:173-179`).
  A patch targets exactly one object and an object is never mutated except by an applied patch's
  event (`activegraph/core/patch.py:22-31`); lifecycle is `proposed → applied` XOR
  `proposed → rejected` (`activegraph/core/patch.py:4-6`). `patch_object` is the auto-apply
  shortcut — status `"applied"` from birth, emits `patch.applied` directly
  (`activegraph/core/graph.py:775-829`); `propose_patch` emits `patch.proposed` and waits for
  explicit approval (`activegraph/core/graph.py:831-881`), stripping an `"object:"`/`"relation:"`
  prefix from `target` and defaulting `expected_version` to `0` for an unknown target
  (`activegraph/core/graph.py:845-848`).
- **Lookup failures are typed and atomic** — `patch_object` raises
  `ObjectNotFoundError(ExecutionError, KeyError)` for an unknown target; `apply_patch` and
  `reject_patch` raise operation leaves under the shared `PatchNotFoundError(ExecutionError)`.
  The apply leaf alone retains `KeyError`, and the reject leaf alone retains `AttributeError`,
  so ordered legacy handlers select the same branch as before. Every miss is checked before id
  allocation, event emission, object-version mutation, or lifecycle mutation. The three raised
  leaves use structured framework rendering and one-element `args == (str(error),)`; that
  rendering intentionally replaces the old quoted `KeyError` strings and reject's incidental
  `NoneType.target` message.
- **Removal semantics** — `remove_object` / `remove_relation` are no-ops on an unknown id; they
  return before emitting, so no event is written (`activegraph/core/graph.py:741-742, 762-763`).
  `object.removed` cascades: the projector drops every relation touching the removed id, deduped
  so a self-loop is removed once (`activegraph/core/graph.py:1023-1037`). `relation.removed` does
  NOT cascade to objects.
- **`where` evaluator** — operators `> < >= <= == != in "not in"`
  (`activegraph/core/graph.py:1098-1107`); ordering ops are `None`-safe by returning `False`
  (`activegraph/core/graph.py:1099-1102`). Keys are dotted paths resolved through dicts then
  attributes (`activegraph/core/graph.py:1110-1121`); both top-level dict entries and
  `{op: value}` dicts are accepted (`activegraph/core/graph.py:1130-1134`). An unknown operator
  raises `InternalEvaluatorError` — treated as a framework bug, not user error
  (`activegraph/core/graph.py:1135-1165`).

### core -> store (durability: the EventStore)

`Graph` persists through an optional `EventStore` attached at runtime. The reference is
TYPE_CHECKING-only (`activegraph/core/graph.py:42`); both the serialization guard and the append
are function-local deferred imports inside `emit`. `EventStore` is a `Protocol`
(`activegraph/store/base.py:45-73`), so core never names a concrete backend. Replay flows the
other way: `store/base.replay_into` is the single entry point and calls back into
`graph._replay_event`.

```ebnf
pre-append-check ::= validate_event(Event) "->" None | raises serde-error
                     (* only when a store is attached; graph.py:569 *)
durable-append   ::= store.append(Event) "->" None
event-store-iface::= { run_id: str
                     , append(Event) -> None
                     , iter_events(after?, until?) -> Iterator[Event]
                     , get_event(event_id) -> Event | None
                     , count() -> int
                     , truncate_after(event_id) -> None
                     , close() -> None }
replay-entry     ::= replay_into(Graph, Iterable[Event]) "->" int
                     (* store/base.py:76 — the single replay entry point;
                        internally calls graph._replay_event per event *)
ordering         ::= append-to-log , project , durable-append , offer-sinks ,
                     release-lock , notify-listeners
```

Contract notes:

- `validate_event(event: Event) -> None` (`activegraph/store/serde.py:201-203`) round-trips
  `encode_payload(event.payload)` so a non-serializable payload can never enter the in-memory log
  either (CONTRACT v0.5 #4) — **but only when a store is attached**
  (`activegraph/core/graph.py:569-572`). See Open Question 5.
- `replay_into(graph, events) -> int` at `activegraph/store/base.py:76-86` calls
  `graph._replay_event(ev)` with an explicit `# noqa: SLF001 — internal seam by design`.
- Other `store/` modules that import from core: `store/base.py:20,23` (`Event`, `Graph`,
  TYPE_CHECKING), `store/serde.py:21`, `memory.py:11`, `sqlite.py:49`, `postgres.py:25`,
  `retention.py:72`, `conformance.py:22` — all `Event` only.

### core <- store (the GraphStore ABC, implemented by backends)

`GraphStore` is core's *inbound* storage seam: core defines the materialized-state interface and
storage backends subclass it. `apply_event` writes projected state through it exclusively, so
the projector is backend-agnostic. `graph_store.py:38-40` imports `Object, Relation, Patch` under
TYPE_CHECKING only — deliberately, so backends "stay decoupled from query-language details and
there is no import cycle with core.graph" (`activegraph/core/graph_store.py:118-126`).

```ebnf
graph-store       ::= required-ops , optional-hooks , lifecycle
required-ops      ::= put_object(Object) -> None
                    | get_object(id) -> Object | None
                    | remove_object(id) -> None
                    | all_objects() -> [Object]          (* order unspecified *)
                    | put_relation(Relation) -> None
                    | get_relation(id) -> Relation | None
                    | remove_relation(id) -> None
                    | all_relations() -> [Relation]
                    | put_patch(Patch) -> None
                    | get_patch(id) -> Patch | None
                    | all_patches() -> [Patch]
optional-hooks    ::= find_objects(type?) -> [Object]
                    | find_objects_in_types([str]) -> [Object]   (* order-preserving *)
                    | find_relations(source?, target?, type?) -> [Relation]  (* AND *)
                    | neighborhood(id, depth=1) -> ([Object],[Relation])
                    | match_chain([type?], [(rel_type?, dir)]) -> [ChainMatch]
dir               ::= "right"   (* (a)-[]->(b) *)
                    | "left"    (* (a)<-[]-(b) *)
ChainMatch        ::= { objects: [Object] (* len == n *)
                      , relations: [Relation] (* len == n-1 *) }
lifecycle         ::= clear() -> None | remove_patch(id) -> None | close() -> None
conformance       ::= subclass GraphStoreConformance , implement make_store() ,
                      [override cleanup()]
override-rule     ::= "an override MUST return exactly what the default would"
```

Contract notes (CONTRACT v1.2 #1, #5):

- **A GraphStore is NOT an EventStore.** Losing a GraphStore is recoverable (replay the log);
  losing the EventStore is not (`activegraph/core/graph_store.py:12-15`).
- `put_*` is upsert; `get_*` returns `None` for unknown ids; `remove_*` is a no-op for unknown
  ids (`activegraph/core/graph_store.py:58-66`).
- `all_objects()` / `all_relations()` / `all_patches()` order is **unspecified**
  (`activegraph/core/graph_store.py:84, 102, 116`).
- The five optional pushdown hooks have working Python defaults, so the base class *is* the
  canonical semantics; an override MUST return exactly what the default would, pinned by
  `GraphStoreConformance` (`activegraph/core/graph_store.py:118-126`,
  `activegraph/store/graph_conformance.py:1-38`).
- `find_objects_in_types` MUST preserve single-pass `all_objects` order even when pushed down
  (`activegraph/core/graph_store.py:137-148`).
- `match_chain` is explicitly **homomorphic** — one object or relation may fill more than one
  position, matching FalkorDB, neither enforcing node/relation uniqueness
  (`activegraph/core/graph_store.py:218-225`). Requires `len(rels) == len(node_types) - 1`
  (`activegraph/core/graph_store.py:214-215`).
- The rich `where` predicate is deliberately **not** a hook — it stays in Python in
  `core/graph.py` because it is hard to translate faithfully
  (`activegraph/core/graph_store.py:25-29`).
- `neighborhood` is an undirected BFS; endpoints that are not materialized objects are skipped;
  returns `([], [])` for an unknown start (`activegraph/core/graph_store.py:170-178`).
- `remove_patch` on the ABC raises `NotImplementedError`; subclasses storing patches MUST
  override (`activegraph/core/graph_store.py:281-288`).
- `InMemoryGraphStore` returns live instances with **no copy**, so the projector's in-place
  mutations (`obj.data.update(...)`, `obj.version += 1`) work as they did pre-v1.2
  (`activegraph/core/graph_store.py:294-302`). The projector still calls `put_object(obj)` after
  mutating — a no-op in memory, but what makes a remote backend correct
  (`activegraph/core/graph.py:1063-1070`).
- Backend implementation reference: `store/falkordb.py:70-72` imports `Object`, `Relation`,
  `ChainMatch`, `GraphStore`, and `FalkorDBGraphStore` overrides all five optional hooks
  (`find_objects:384`, `find_objects_in_types:403`, `find_relations:426`, `neighborhood:453`,
  `match_chain:518`) to push work into Cypher.

### core -> sinks (accepted-event fanout, CONTRACT v1.8)

`Graph` fans accepted events out to registered sinks inside `emit`, after durable append and
before legacy listeners. Types are TYPE_CHECKING-only at `activegraph/core/graph.py:40-41`
(`EventSink`, `OverflowPolicy`, `SinkStatus`, `SinkHandle`); `add_sink` and `emit` both use
deferred imports. Core treats sinks as a pure observation channel: return values are ignored and
exceptions are contained, so an observer bug can never become graph control flow.

```ebnf
sink-attach   ::= graph.add_sink(EventSink, *, name?, queue_capacity=1024,
                                 overflow_policy="drop_newest", metrics?) "->" SinkHandle
                | raises TypeError   (* not an EventSink Protocol *)
                | raises ValueError  (* empty name | duplicate name *)
sink-detach   ::= graph.remove_sink(name | SinkHandle, *, timeout=5.0) "->" bool
sink-flush    ::= graph.flush_sinks(timeout=5.0) "->" bool
sink-closeall ::= graph.close_sinks(timeout=5.0) "->" bool
sink-status   ::= graph.sink_statuses() "->" (SinkStatus, ...)
                  (* active ++ closing ++ retained-terminal *)

offer         ::= handle._offer(Event, DeliveryContext) "->" bool
                  (* non-throwing by design; core wraps in except-continue anyway *)
DeliveryContext ::= { run_id: nonempty-str
                    , sequence: int >= 1      (* len(graph._events) after append *)
                    , mode: "live" }          (* replay never offers *)
EventSink     ::= { open() -> None, on_event(Event, DeliveryContext) -> None,
                    flush() -> None, close() -> None }
                  (* all return values ignored; all exceptions isolated *)
OverflowPolicy::= "drop_newest" | "drop_oldest" | "fail_sink"
```

Contract notes:

- `add_sink` deferred-imports `EventSink` (isinstance-checked as a runtime-checkable Protocol),
  `OverflowPolicy`, `SinkHandle`, and `NoOpMetrics` (`activegraph/core/graph.py:386-389`), then
  constructs the handle at `activegraph/core/graph.py:404-412`.
- `emit` deferred-imports `DeliveryContext` (`activegraph/core/graph.py:582-588`) and calls
  `sink._offer(event, context)` per attached handle inside a bare `except Exception: continue`
  containment boundary (`activegraph/core/graph.py:589-599`). `SinkHandle._offer` is documented
  non-throwing at `activegraph/sinks/dispatch.py:142-148`; the guard is a belt-and-braces
  boundary.
- Lifecycle passthrough: `remove_sink` / `close_sinks` call `handle._close_worker(...)` and
  `handle._is_terminal()` — `activegraph/core/graph.py:442-451, 504-514`.
- Inbound from `sinks/`: `sinks/base.py:13`, `dispatch.py:16`, `jsonl.py:10`, `testing.py:9`
  import `Event`; `sinks/conformance.py:19-20` imports `Event` and `Graph` and constructs real
  `Graph` instances to exercise emit-order (`activegraph/sinks/conformance.py:99,118,137-138`).

### core -> observability (metrics collaborator)

Core never records a metric. It accepts an optional `Metrics` collaborator on `add_sink` and
forwards it into the `SinkHandle`, defaulting to `NoOpMetrics` when none is given. `Metrics` is
TYPE_CHECKING-only at `activegraph/core/graph.py:39`; `NoOpMetrics` is deferred-imported at
`activegraph/core/graph.py:386, 410`.

```ebnf
metrics-passthrough ::= graph.add_sink(..., metrics = Metrics | None)
                        (* None -> NoOpMetrics(); forwarded into SinkHandle *)
Metrics             ::= { counter(name, tags, value=1.0) -> None
                        , histogram(name, tags, value) -> None
                        , gauge(name, tags, value) -> None }
                        (* best-effort, non-throwing, concurrency-tolerant *)
```

Contract note: `Metrics` is a 3-method Protocol, all methods best-effort and non-throwing
(`activegraph/observability/metrics.py:28-38`). `observability/migration.py:26` imports `Event`.

### core -> runtime (error taxonomy only — the apparent cycle)

Core imports from `runtime/` only function-locally, and only for exception classes.
There is no TYPE_CHECKING runtime import and no protocol or callback import, so the cycle is
error-taxonomy-only and import-time-acyclic.

| core site | imported symbol | raised when |
|---|---|---|
| `activegraph/core/graph.py:133` | `runtime.exec_errors.ReservedFieldError` | caller `data`/`updates`/`value` contains a reserved key |
| `activegraph/core/graph.py:527` | `runtime.config_errors.IncompatibleRuntimeState` | `attach_store` called twice with different stores |
| `activegraph/core/graph.py:794` | `runtime.exec_errors.ObjectNotFoundError` | `patch_object` target id is absent |
| `activegraph/core/graph.py:898` | `runtime.exec_errors.ApplyPatchNotFoundError` | `apply_patch` patch id is absent |
| `activegraph/core/graph.py:895` | `runtime.exec_errors.InvalidPatchLifecycleState` | `apply_patch` on a non-`proposed` patch |
| `activegraph/core/graph.py:958` | `runtime.exec_errors.RejectPatchNotFoundError` | `reject_patch` patch id is absent |
| `activegraph/core/graph.py:1136-1139` | `activegraph.errors.internal_bug_fields` + `runtime.exec_errors.InternalEvaluatorError` | `evaluate_where` sees an operator not in `_OPS` |

```ebnf
deferred-error-import ::= "inside function body" , from-runtime-errors , raise
from-runtime-errors   ::= ReservedFieldError(field=, api=, param=)
                        | ObjectNotFoundError(object_id=)
                        | ApplyPatchNotFoundError(patch_id=)
                        | RejectPatchNotFoundError(patch_id=)
                        | InvalidPatchLifecycleState(patch_id=, current_status=)
                        | InternalEvaluatorError(summary, what_failed=, why=,
                                                 how_to_fix=, context=)
                        | IncompatibleRuntimeState(summary, what_failed=, why=,
                                                   how_to_fix=)
(* Every constructor is keyword-only. Compatibility builtins are specific to
   each leaf; no runtime type, protocol, or callback is imported by core. *)
```

Contract note: this is a *naming* coupling, not a behavioral one. The object/patch lookup leaves,
`ReservedFieldError`, and `InvalidPatchLifecycleState` are raised from `core/graph.py`; their
placement in `runtime/exec_errors.py` keeps them under the public `ExecutionError` taxonomy.
`InternalEvaluatorError` likewise names `activegraph/core/graph.py` as its user. See Open
Question 6.

### core <- packs (schema validator injection)

`packs.loader` installs two callables directly onto the `Graph` instance after loading a pack's
schema, so `add_object` / `add_relation` enforce pack-declared types without core ever knowing
what a pack is. Both default to `None`, preserving untyped v0.8 semantics. `packs/loader.py:38`
imports `Event`.

```ebnf
validator-install ::= graph._pack_object_validator   := (type: str, data: dict) -> dict
                    | graph._pack_relation_validator := (type: str,
                                                         src_type: str|None,
                                                         tgt_type: str|None) -> None
call-sites        ::= add_object   -> object_validator(type, clean)    (* result replaces data *)
                    | add_relation -> relation_validator(type, src.type?, tgt.type?)
default           ::= None   (* untyped v0.8 semantics preserved *)
```

Contract note: installed at `activegraph/packs/loader.py:842-843`
(`graph._pack_object_validator = _make_object_validator(state)` /
`graph._pack_relation_validator = _make_relation_validator(state)`), read back by runtime at
`activegraph/runtime/runtime.py:3735,3739`. The object validator's **return value replaces the
data dict**; the relation validator is checked for its exception only.

### core -> runtime / behaviors / llm (the View)

`View` is the read surface behaviors get instead of the live graph (CONTRACT #11). Behaviors
never query the graph directly; they declare `view=` decorator metadata and the runtime builds
the `View` before invocation. `runtime/view_builder.build_view` is the sole production
constructor (`activegraph/runtime/view_builder.py:16-51`): it reads `graph.all_objects()`,
`graph.all_relations()`, `graph.neighborhood(center_id, depth)`,
`graph.objects_in_types(include_types)`, and `graph.events[-n:]`, then hands the slice over.

```ebnf
view-construction ::= build_view(Behavior | RelationBehavior, Event, Graph) "->" View
view-spec         ::= { around?: "event.payload.<path>"
                      , depth?: int = 1
                      , include_types?: [str]
                      , recent_events?: int = 50 }
view-read         ::= view.objects(type?, where?) "->" [Object]
                    | view.relations(type?) "->" [Relation]
                    | view.events(type?) "->" [Event]
traced-view       ::= TracedView(View, ReadRecorder)   (* overrides objects() only *)
                      "->" records post-filter object ids into context.read payload
invariant         ::= "no method on View mutates the graph"
```

Contract notes:

- A `View` is a point-in-time snapshot; nothing done to it mutates the graph
  (`activegraph/core/view.py:1-6, 16-26`). All three accessors return `list(out)` copies
  (`activegraph/core/view.py:48, 54, 60`) — but the contained `Object`/`Relation` instances are
  the live ones, so the invariant is **unenforced**. See Open Question 7.
- `runtime/context_reads.TracedView(View)` subclasses `View` and reaches into the protected
  fields `view._objects / _relations / _events`
  (`activegraph/runtime/context_reads.py:105-107`) — a deliberate but fragile coupling.
- Downstream readers: `behaviors/base.py:22-23` (`Event`, `Graph`, `Relation`, TYPE_CHECKING
  only), `llm/prompt.py:43-44` (`Event`, `View` — renders the view into prompts).

### core -> everything (the universal DTO and its payload schemas)

`Event` is the one type that crosses every boundary in the system, and the projector's payload
expectations are the implicit wire contract every producer must satisfy. Remaining consumers
that import only `Event`: `llm/cache.py:36`, `llm/embedding_cache.py:17`, `tools/cache.py:31`,
`trace/causal.py:18-19` and `trace/printer.py:20-21` (also `Graph`), `cli/main.py:586,794`
(`IDGen`, `Event`; deferred/function-local), `sandbox/__init__.py:495` (deferred).

```ebnf
Event        ::= { id: str , type: event-type , payload: dict
                 , actor: str | None , frame_id: str | None
                 , caused_by: str | None , timestamp: iso8601-Z }
                 (* frozen dataclass; payload NOT deeply frozen *)
event-type   ::= projected-type | passthrough-type
projected-type   ::= "object.created" | "object.removed"
                   | "relation.created" | "relation.removed"
                   | "patch.proposed" | "patch.applied" | "patch.rejected"
passthrough-type ::= any other dotted name  (* projector no-op *)
serialization    ::= event.to_dict() -> { id, type, payload, actor,
                                          frame_id, caused_by, timestamp }

object.created   ::= { object: { id, type, data, version, provenance }, id }
object.removed   ::= { id }                (* cascades: drop relations touching id *)
relation.created ::= { relation: { id, source, target, type, data, provenance }
                     , id, source, target }
relation.removed ::= { id }
patch.proposed   ::= { patch: patch-dict }
patch.applied    ::= { patch: patch-dict , target , diff , [approved_by] }
patch.rejected   ::= { patch_id , target , reason , current_version }
patch-dict       ::= { id, target, op, value, expected_version, proposed_by,
                       rationale, evidence, status, rejection_reason, provenance }
op               ::= "create" | "update" | "replace" | "remove"
                     (* projector implements ONLY "update" and "replace" *)
status           ::= "proposed" | "applied" | "rejected"
diff             ::= { field: { old , new } }   (* only fields that actually change *)
provenance       ::= { created_by, caused_by_event, frame_id, timestamp,
                       evidence: [str], run_id
                     , [llm_request_event_id], [tool_request_event_ids: [str]] }
```

Contract notes:

- **The projector is deliberately partial.** `apply_event` handles exactly the 7 projected types
  above (`activegraph/core/graph.py:1011-1077`) and has **no `else` branch**: every other event
  type in the system (`behavior.started`, `llm.requested`, `approval.granted`,
  `promote.applied`, `runtime.snapshot`, `authority.decision`, …) is silently a projection no-op.
  Correct by design — those events don't change graph state — but nowhere stated as a contract.
- **Events are frozen; payloads are not deeply frozen.** Immutability after `emit` is by
  convention only (`activegraph/core/event.py:3-5`). Unenforced invariant.
- **ID generation (CONTRACT #1, v0.5 #6)** — `activegraph/core/ids.py:41-86`. Objects share ONE
  global counter prefixed by type: `task#1, task#2, claim#3` — not `claim#1`
  (`activegraph/core/ids.py:53-56, 63-65`). Events/relations/patches/frames each get their own
  zero-padded 3-digit sequence (`evt_001`, `rel_001`, `patch_001`, `frame_001`) —
  `activegraph/core/ids.py:67-81`. `run()` returns a 26-char Crockford-base32 ULID, deliberately
  *not* counter-based because runs live in storage and are looked up by id, so cross-file
  collisions are the risk (`activegraph/core/ids.py:83-86, :21-34`); explicitly **not** strictly
  monotonic within a millisecond (`activegraph/core/ids.py:22-25`). Replay does NOT call the
  generator — recorded events carry their ids, which is what keeps forked/reloaded runs aligned
  with their logs (`activegraph/core/ids.py:47-49`). `reseed_from_events(events)` sets each
  counter past the highest id seen using `max(current, seen)` so it never regresses
  (`activegraph/core/ids.py:90-136`); object ids parse as `^(?P<type>[^#]+)#(?P<n>\d+)$`, the
  rest as `^[a-zA-Z]+_(?P<n>\d+)$` (`activegraph/core/ids.py:37-38`). **Not thread-safe**
  (`activegraph/core/ids.py:42`) — see Open Question 3.
- **Clock contract (CONTRACT #8)** — behaviors get time only via `ctx.clock` and the runtime
  reads time only through this interface, so deterministic runs swap in
  `FrozenClock`/`TickingClock` and replay never depends on the machine clock
  (`activegraph/core/clock.py:1-15`). Format: ISO-8601, second precision, `Z` suffix
  (`activegraph/core/clock.py:17-22`).

## Sequence: a behavior patches an object through `patch_object`

The flow that best shows what core is for: a caller proposes a mutation, core mints ids and
provenance, builds the event, and `emit` runs the seven-step ordering — validate, log, project,
persist, fan out, unlock, notify. Note that `ids.event()` is called *before* the lock is taken
(Open Question 3), and that the projector writes back through `GraphStore` even when the store
returned a live instance.

```mermaid
sequenceDiagram
    participant Caller as runtime / behavior
    participant G as Graph
    participant IDs as IDGen
    participant Serde as store.serde
    participant P as apply_event
    participant GS as GraphStore
    participant ES as EventStore
    participant SH as SinkHandle
    participant L as listeners

    Caller->>G: patch_object(target, updates, actor=...)
    G->>G: _reject_reserved_fields(updates, api="patch_object")
    Note over G: raises ReservedFieldError on "provenance"
    G->>IDs: patch() / event()
    IDs-->>G: "patch_001", "evt_007"
    G->>G: _provenance(...) yields created_by, caused_by_event,<br/>frame_id, timestamp, evidence, run_id
    G->>G: build Patch(status="applied") + Event("patch.applied")
    G->>G: emit(event)

    activate G
    Note over G: acquire _emit_lock (RLock)
    G->>Serde: validate_event(event)
    Note over G,Serde: only if a store is attached (graph.py:569)
    Serde-->>G: ok
    G->>G: _events.append(event)
    G->>P: apply_event(graph, event)
    P->>GS: get_object(target)
    GS-->>P: Object (live instance, no copy in-memory)
    P->>P: obj.data.update(diff); obj.version += 1
    P->>GS: put_object(obj)
    P->>GS: put_patch(patch)
    P-->>G: None
    G->>ES: store.append(event)
    G->>SH: _offer(event, DeliveryContext(run_id, sequence, "live"))
    Note over G,SH: exceptions contained by except Exception, continue
    Note over G: release _emit_lock
    deactivate G

    G->>L: listener(event)  (synchronous, outside the lock)
    L-->>G: (exceptions propagate)
    G-->>Caller: Patch
```

## Open questions

1. **`PATCH_OPS` declares four ops; the projector implements two.** `activegraph/core/patch.py:17`
   defines `{create, update, replace, remove}` and `Patch.op`'s docstring repeats all four
   (`activegraph/core/patch.py:24-25`), but `apply_event`'s `patch.applied` branch only handles
   `"update"` and `"replace"` (`activegraph/core/graph.py:1065-1068`). A `create` or `remove`
   patch applies successfully — the patch is stored, `obj.version += 1` fires — but the object's
   data is unchanged. Either dead constants or a silent no-op bug.

2. **`PATCH_OPS` appears to be unreferenced.** Nothing in `core/` imports or checks it;
   `propose_patch` accepts any `op` string without validation
   (`activegraph/core/graph.py:831-844`). Probable dead code — the test suite was not grepped, so
   it may be used there.

3. **`IDGen` is documented not-thread-safe, but `Graph` is explicitly hardened for concurrent
   emitters.** `activegraph/core/ids.py:42` says "Not thread-safe (single-threaded loop)", while
   `activegraph/core/graph.py:191-196` adds an RLock precisely because "concurrent callers cannot
   reverse log/sink order". Every convenience builder calls `self.ids.event()` *before* entering
   `emit`'s lock (e.g. `activegraph/core/graph.py:634, 663`), so two threads calling `add_object`
   concurrently can race the counters. Unclear whether concurrent *sugar* calls are supported, or
   only concurrent raw `emit` of pre-built events.

4. **Resolved — graph mutation lookup failures use structured framework leaves.**
   `patch_object` now raises `ObjectNotFoundError`; `apply_patch` and `reject_patch` raise
   operation-specific leaves under `PatchNotFoundError`. The apply leaf retains only the old
   `KeyError` route, while the reject leaf retains only the old `AttributeError` route. This
   preserves ordered legacy handler selection without exposing the previous incidental
   `NoneType.target` text, and every check occurs before mutation.

5. **Serialization validation is conditional on a store.** `validate_event` only runs when
   `self._store is not None` (`activegraph/core/graph.py:569`). The comment claims the purpose is
   "so bad payloads never land in the in-memory log either"
   (`activegraph/core/graph.py:567-568`) — but for a store-less graph they do. Likely intentional
   (no serialization requirement without persistence), but the comment overstates it.

6. **The `core` → `runtime` error-class cycle.** Import-time acyclic (all four sites are
   function-local: `activegraph/core/graph.py:133, 527, 895, 1137`), so it is not a correctness
   problem — but `ReservedFieldError` and `InvalidPatchLifecycleState` are raised *only* from
   `core/graph.py` while living in `runtime/exec_errors.py`, and `InternalEvaluatorError`'s
   docstring names core as its user (`activegraph/runtime/exec_errors.py:206-208`). Candidate for
   relocation into `core/` or a shared `errors/` module.

7. **`View` immutability is nominal, not enforced.** Accessors return copied *lists*
   (`activegraph/core/view.py:48, 54, 60`) but the contained `Object` instances are the live
   projection objects (`InMemoryGraphStore` returns them without copy,
   `activegraph/core/graph_store.py:298-301`), so a behavior can do
   `ctx.view.objects()[0].data["x"] = 1` and silently corrupt state outside the event log. Same
   for `Event.payload` (`activegraph/core/event.py:3-5`). Both are convention-only.

8. **Resolved — Graph and View share one authoritative object-query root.** The root starts with
   object data to preserve ordinary bare-field shorthand, then overwrites `id`, `type`,
   `version`, `data`, and `provenance` with framework metadata. Thus the two public APIs agree;
   bare framework names have exactly one meaning, while a colliding domain value remains
   addressable as `data.<field>` (and a dict-valued domain field named `data` through
   `data.data.<nested>`). `evaluate_where` grammar itself is unchanged.

9. **`GraphStore.clear()`'s default depends on `remove_patch`, which the ABC raises
   `NotImplementedError` for** (`activegraph/core/graph_store.py:272-288`). A backend that
   implements the three abstract patch methods but forgets `remove_patch` passes
   `@abstractmethod` checks and then fails at `clear()`, not at `remove_patch`.
   `GraphStoreConformance` presumably catches this, but the ABC's own shape allows the hole.

10. **`Graph._replay_event` is prefixed private but is a documented public seam** — used from
    `store/base.py:84`, `runtime/runtime.py:3317/3492/4310`, `runtime/promote.py:173`, and
    `store/retention.py:360`, each with `# noqa: SLF001` and a comment calling it "the load/fork
    seam". Same for `_remove_listener`, `_pack_object_validator`, `SinkHandle._offer`,
    `_close_worker`, and `_is_terminal`. These are first-class interfaces despite the underscore.

11. **CONTRACT numbers not cross-checked.** `CONTRACT.md` (382KB) was not read; every CONTRACT
    reference in this document (#1–#15, v0.5/v0.6/v0.7/v0.9/v1.2/v1.8/v1.10) is quoted from the
    source docstring, not validated against the canonical contract document.
