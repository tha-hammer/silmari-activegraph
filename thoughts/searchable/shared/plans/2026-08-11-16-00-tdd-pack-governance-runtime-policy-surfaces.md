---
date: 2026-08-11T16:00:35-04:00
planner: Codex
git_commit: 8366e87506613210ea844069e029dddbf0066567
branch: repair-impl-set1
planning_branch: repair-judgment-2026-08-11-15-39
repository: silmari-activegraph
research: thoughts/searchable/shared/research/2026-08-11-15-54-pack-governance-runtime-policy-surfaces.md
beads_issue: AF-bcj
review: thoughts/searchable/shared/plans/2026-08-11-16-00-tdd-pack-governance-runtime-policy-surfaces-REVIEW.md
review_issue: AF-s7v
revision_issue: AF-pq3
implementation_head: cd30dcb760caeb80d276753af997f26db0cbfb2b
status: implemented
last_updated: 2026-08-12
tags: [tdd, pack-policy, scheduler, metrics, logging, documentation]
---

# Pack Governance and Runtime Policy Surfaces — TDD Implementation Plan

## Implementation result

Implemented on `repair-impl-set1` through
`cd30dcb760caeb80d276753af997f26db0cbfb2b` using the Red–Green–Refactor and
per-behavior commit sequence below. Final verification on 2026-08-12 passed:
Phase 0/persistence `200 passed`; scheduler `47 passed, 33 skipped`; metrics,
replay, and sinks `160 passed`; logging `35 passed`; full repository suite
`1220 passed, 55 skipped`. Skips are the existing optional backend/dependency
gates. `git diff --check` also passed.

## Overview

This plan resolves eight declared-but-unenforced surfaces without treating every
declaration as an instruction to add runtime behavior. Three gaps should be
implemented now because the public surface already promises an observable result:

1. scheduled relation behaviors must fire instead of disappearing;
2. the locked standard metric catalog must be emitted from authoritative runtime
   seams; and
3. an explicitly supplied log payload must actually pass through the configured
   redactor.

The remaining five findings are compatibility boundaries. Their current behavior
will be pinned through production entrypoints and documented explicitly, without
activating undefined or unsafe semantics. Historical numbered CONTRACT clauses
are append-only archaeology: this slice adds a v1.11 correction overlay instead
of rewriting the older v0.7/v0.9/v1.4/v1.9 entries.

Research basis:
`thoughts/searchable/shared/research/2026-08-11-15-54-pack-governance-runtime-policy-surfaces.md`.
The affected baseline is green at the pinned commit: 103 tests passed across
`test_observability_metrics.py`, `test_observability_logging.py`,
`test_activate_after.py`, `test_runtime.py`, `test_pack_manifest.py`, and
`test_packs.py`.

## Decision record

| Finding | Decision | Why |
|---|---|---|
| 07.1 `requires_approval` | **(b) Leave explicit/intentional; append a contract correction and pin behavior.** | Automatic `Graph.add_object` interception would change its `Object` return contract, require an approval bypass, break Diligence's default direct-write mode, conflict with reload-without-live-packs, and disturb byte-pinned approval logs. The field is owner-attribution metadata for the explicit `ctx.propose_object` API; it stores no policy route. |
| 07.2 `auto_apply` | **(b) Leave reserved; fix docs and pin normalization.** | No code or prose defines whether values are object types, proposal auto-grants, or settings. Activating it would invent semantics and event ordering. Keep the public field for compatibility and label it reserved/no-op. |
| 07.9 capabilities / `consumes` | **(b) Leave registration host-owned and `consumes` reserved; correct stale docs.** | Capability declarations are already validated, two-way manifest-verified, and recorded. ActiveGraph has no gateway callable/registry to register. The source explicitly assigns imperative wiring to the host, and normal manifest verification is a pinned warning tier. |
| 09.1 / 10.1 metrics | **(a) Implement now with Red–Green–Refactor.** | The catalog and operator guide say these metrics are emitted. Fifteen of 24 have no emit path, behavior metrics exclude two behavior kinds, and queue depth is stale. Existing event/budget/error data is sufficient to implement the contract without new public names. |
| 09.5 priority | **(b) Leave reserved; fix docs and pin registration order.** | `CONTRACT.md` locks registration order and explicitly says “no priority.” Activating it needs undefined high/low/tie/delayed semantics and changes deterministic event order. |
| 09.6 scheduled `RelationBehavior` | **(a) Implement now with Red–Green–Refactor.** | The decorator accepts the combination and emits `behavior.scheduled`, but fire-time code silently drops it. At fire time, current graph state governs relation candidates and pattern matches; `where` remains a filter over the immutable original event payload. |
| 09.7 `ctx.llm_provider` | **(b) Leave LLM-only; fix docs and pin identity/absence.** | Supplying the raw provider to plain/relation handlers would invite unrecorded I/O that bypasses LLM events, cache, budget, retry, provenance, tools, and replay. `@llm_behavior` and `Context.embed` are the governed paths. |
| 10.6 payload redactor | **(a) Implement now with Red–Green–Refactor and an additive v1.11 schema amendment.** | `configure_logging` publicly accepts a hook and promises it runs, but it has zero callers. Redaction must run at the configured handler's final boundary so both `runtime_log_extra(payload=...)` and direct stdlib `extra={"payload": ...}` cross it. Built-in framework logs still supply no payload. |

The load-bearing sub-decisions are also locked below: canonical delayed-work
identity, pack-disable cancellation, FIFO due-tail restoration, non-resumable
mid-fan-out exhaustion, one delayed pattern marker, last-writer queue gauges,
Runtime-owned budget observations, closed metric-label normalization, and
handler-boundary payload redaction.

## Desired end state

### Observable behaviors

1. Given a loaded pack with `requires_approval=("memo",)`, direct
   `Graph.add_object("memo", ...)` still creates an object immediately; a
   behavior that calls `ctx.propose_object` creates a pending approval instead.
   The policy supplies only the proposal's owner pack. Public docs and the new
   CONTRACT overlay describe that exact distinction.
2. Given `PackPolicy(auto_apply=[...])`, construction normalizes to a tuple and
   runtime behavior is unchanged; the field is marked reserved for future
   contract work.
3. Given capability declarations, `Pack` and manifest verification continue to
   validate/record them, but host code remains responsible for gateway wiring;
   `consumes` is manifest metadata only.
4. Given a scheduled relation behavior, when its event-count tick is reached,
   the runtime resolves the still-registered exact behavior object and validates
   its canonical name,
   re-evaluates current relation candidates and patterns, and invokes it once per
   current match under the same budget/lifecycle rules as immediate relation
   dispatch. Disabling its owning pack cancels the pending entry.
5. Given behaviors with different `priority` values, they run in registration
   order. Priority remains preserved metadata.
6. Given a runtime with an LLM provider, LLM behavior context exposes that exact
   provider, while plain and relation contexts expose `None`.
7. Given a configured metrics backend, every declared standard metric is emitted
   at its documented authoritative seam with exactly the catalog tag keys and
   closed label values; queue depth reports the last publishing runtime's local
   depth and reaches zero after that runtime drains.
8. Given either `runtime_log_extra(payload=...)` or direct stdlib
   `extra={"payload": ...}` through the handler installed by
   `configure_logging`, the handler detaches and redacts the payload before JSON
   emission. Callback/type/serialization failure omits it without losing the log
   line. Built-in runtime logs still attach no event/LLM/tool payloads.

## What this plan does not do

- It does not make `Graph.add_object` implicitly reject or route gated types.
- It does not define or remove `PackPolicy.auto_apply`.
- It does not create a capability gateway, capability owner registry, credential
  resolver, or runtime enforcement for `manifest.consumes`.
- It does not turn priority into scheduling semantics or reorder status output.
- It does not expose `ctx.llm_provider` to plain/relation behavior code.
- It does not add metric names/tags, metrics for embedding/context/scheduler
  concepts, or per-ID labels.
- It does not log graph event, prompt, response, tool argument/output, or goal
  payloads by default.
- It does not change the normal manifest warning tier, sandbox strict tier,
  approval event schema, or legacy approval snapshots.
- It does not make the redactor cover arbitrary operator-owned handlers that were
  not installed by `configure_logging`, synchronize process-global
  reconfiguration, add queue-depth aggregation across runtimes, or add
  metric-series deletion.
- It adds one public JSON-log field, `payload`, at the end of `LOG_FIELDS`; that
  additive schema change is recorded in the v1.11 CONTRACT overlay and the
  `[Unreleased]` changelog. No persistence/event schema changes are planned.

## Testing strategy

- **Framework:** pytest, using existing in-memory `Graph`, fake provider/tool
  fixtures, and a `RecordingMetrics` test double.
- **Unit/contract tests:** pack field normalization, matcher and delayed-queue
  interfaces, context identity, redactor failure policy, and metric
  name/kind/tag/value semantics.
- **Runtime integration tests:** explicit approval flow, delayed relation fan-out,
  LLM/tool/behavior/budget/pattern/replay observations, final queue gauge.
- **Backend conformance:** retain current Prometheus/OTel direct adapter tests,
  add first-observation zero-gauge coverage for OTel, and add a
  runtime-to-export closure case when optional dependencies are available.
- **Persistence/replay regression:** legacy approval byte identity, pending
  approval persistence, LLM/tool replay, strict divergence, and load/fork metric
  isolation.
- **No timing thresholds:** duration tests assert nonnegative observations and
  counts/tags, not wall-clock lower bounds.
- **Red evidence:** each implementation cycle must run its focused new test before
  production edits and capture the intended assertion failure. Documentation-only
  decisions begin with green characterization because production behavior must
  not change.
- **Documentation gate:** a focused test reads the changed docs/specs and asserts
  the required positive boundaries plus the absence of the known false examples;
  old historical CONTRACT prose is allowed only because the v1.11 overlay names
  exactly which clauses it supersedes.

The repository has no checked-in usable pytest environment in this worktree. Use
the project team's supported Python 3.12 environment when available. For this
worktree, the verified fallback command is:

```bash
PYTHONPATH="$PWD" ../tdd-baml-llm-provider-2026-08-11-10-17/.venv/bin/pytest ...
```

Do not install or lock dependencies as part of this implementation.

### Verified live seam anchors at `8366e875`

- Approval/direct-add: `activegraph/core/graph.py:625-675`,
  `activegraph/runtime/runtime.py:191-214,3038-3086`, and the stale public
  `activegraph/policy.py:12-31` docstring.
- Capability connectors: `activegraph/packs/__init__.py:638-676`,
  `activegraph/packs/manifest.py:384-482`,
  `activegraph/packs/loader.py:290-305,360-408`, and
  `activegraph/sandbox/_child.py:120-168`.
- Dispatch/scheduling: `activegraph/runtime/registry.py:40-98`,
  `activegraph/runtime/scheduler.py:44-72`,
  `activegraph/runtime/runtime.py:1261-1366,2893-2905`,
  `activegraph/core/graph.py:1113-1174`, and the unspecified store order at
  `activegraph/core/graph_store.py:101-103` /
  `activegraph/store/falkordb.py:426-451`.
- Metrics/lifecycle: `activegraph/observability/metrics.py:79-224`, queue seams at
  `activegraph/runtime/runtime.py:852-904,1267-1272,4171`, Budget at
  `activegraph/runtime/budget.py:82-148`, load/fork at
  `activegraph/runtime/runtime.py:3249-3580`, and OTel gauge deltas at
  `activegraph/observability/otel.py:76-95`.
- Logging: `activegraph/observability/logging.py:28-48,63-78,81-117,148-209`
  and the current 16-field snapshot at
  `tests/test_observability_logging.py:83-105`.

## Phase 0 — Pin intentional/reserved boundaries before behavioral changes

This phase is deliberately characterization-first, not a fake Red phase. Each
test must pass on the pinned implementation; any failure means the research or
fixture is wrong and must be resolved before edits.

### Behavior 0.1 — Approval remains an explicit proposal API (07.1)

**Given** a pack declaring `requires_approval=("secret",)`
**When** a caller directly adds `secret`, and a behavior separately proposes one
**Then** the direct object exists immediately, while only the explicit proposal
appears in `pending_approvals()` with the canonical owner pack.

#### Characterize

- Add a focused test to `tests/test_packs.py` using a fresh local pack and graph.
- Assert the direct call returns an `Object`, emits `object.created`, and creates
  no approval; compare object count and object data before/after the call.
- Run a real behavior through `Runtime` that calls `ctx.propose_object`. Handler
  return values are ignored, so capture the returned approval ID in an external
  test list; assert that exact ID appears in `pending_approvals()`, the object
  count/data do not change, and `approval.proposed.pack` is the canonical first
  owner from `gated_object_types` (no policy ID or route is stored).
- Disable the pack and assert direct add behavior is unchanged; do not alter the
  existing rule that pending approvals survive disable.

#### Document

- Rewrite `PackPolicy`'s docstring in `activegraph/packs/__init__.py` and the
  stale public `Policy` docstring in `activegraph/policy.py` so
  `requires_approval` declares owner-attribution metadata for explicit proposals,
  not interception of `Graph.add_object`.
- Correct the misleading loader “gated” comments in
  `activegraph/packs/loader.py` and the `Context.propose_object` explanation in
  `activegraph/runtime/runtime.py`.
- Reconcile `docs/concepts/policies.md` and
  `docs/guides/authoring-packs.md`: direct adds land immediately; behavior authors
  choose `ctx.propose_object`; Diligence settings make that branch choice.
- Remove the nonexistent `settings_key` and runtime-auto-apply examples.
- Append (never edit in place) a v1.11 CONTRACT overlay that names v0.9 #15's
  “memo/risk writes require approval” claim and v1.4 #3's “policies stop gating”
  phrase as superseded: direct writes are never intercepted; disable removes
  owner-attribution metadata for future explicit proposals. Preserve v1.4 #2's
  `approval.proposed -> approval.granted -> object.created` grammar and byte
  snapshots. Update current docs/specs to point to the overlay.

#### Success criteria

- [x] New characterization passes before and after docs edits.
- [x] `tests/test_persistence.py`, `tests/test_compaction.py`,
  `tests/test_promote.py`, and `tests/test_legacy_byte_identity.py` pass unchanged.
- [x] No source path reads `gated_object_types` from `Graph.add_object`.
- [x] Historical numbered CONTRACT entries remain byte-for-byte present; the
  v1.11 overlay explicitly supersedes only their inaccurate gating sentences.

### Behavior 0.2 — `auto_apply` is reserved metadata (07.2)

**Given** list and tuple values passed as `auto_apply`
**When** the `PackPolicy` is constructed and loaded
**Then** list input is normalized to a tuple and no runtime state/event/object
behavior changes.

#### Characterize and document

- Add paired otherwise-identical pack/runtime workflows in `tests/test_packs.py`,
  differing only in `auto_apply=() / ("memo",)`. Use deterministic graphs and
  compare `loaded_packs()`, normalized event type/payload streams, direct-add
  results, the externally captured explicit-proposal ID/result, graph projection,
  and `pending_approvals()`. The only allowed difference is the in-memory
  `PackPolicy.auto_apply` tuple itself.
- Assert list input normalizes to the same tuple as tuple input.
- Mark `auto_apply` as reserved and currently unread in
  `activegraph/packs/__init__.py`, `docs/concepts/policies.md`,
  `docs/guides/authoring-packs.md`, and `specs/07-packs.md`.
- State that values have no defined object-type, setting, exemption, or automatic
  grant semantics in the current contract.
- Do not validate field contents beyond existing sequence normalization; stricter
  validation would itself activate an undefined schema and could break callers.

#### Success criteria

- [x] Paired production workflows prove normalization and absence of runtime
  effect; the oracle is not merely “no exception.”
- [x] `rg '\bauto_apply\b' activegraph tests` shows only declaration,
  normalization, and the new characterization—not a semantic read.

### Behavior 0.3 — capability declarations are audited, wiring is host-owned (07.9)

**Given** a manifest and `Pack` capability surface
**When** normal load, sandbox verification, and manifest parsing occur
**Then** capabilities retain current two-way verification/recording, normal load
remains warning-only, sandbox remains strict, and `consumes` remains parsed
metadata with no runtime registration.

#### Characterize and document

- Extend `tests/test_pack_manifest.py` to assert `consumes` round-trips as a tuple
  and is intentionally excluded from `verify_surface`.
- Retain existing capability construction/reverse/risk/action/payload tests.
- Add production-connector cases: a capability mismatch discovered by
  `Runtime.load_pack` emits the normal warning and leaves the pack loaded; the
  identical mismatch fails sandbox materialization; a `consumes`-only difference
  triggers neither warning nor sandbox failure.
- Add no “absence assertion” against private runtime fields; instead document the
  public boundary in `docs/guides/authoring-packs.md`, `specs/07-packs.md`, and the
  `Pack.capabilities` docstring.
- Explicitly distinguish:
  - declaration entry type, closed risk/action values, and uniqueness validation
    at `Pack` construction (not provider/capability/credential non-emptiness);
  - two-way manifest surface verification;
  - warning-only normal load versus strict sandbox materialization;
  - `pack.loaded` audit payload; and
  - host-owned gateway registration/credential resolution.
- Correct any prose that still says capabilities are not verified at all.
- Append a v1.11 CONTRACT overlay for v1.4 #1 and v1.9 #1: v1.9 superseded the
  capability-exclusion half by making capabilities two-way verified (including
  risk/action class); only `consumes` remains excluded as host-owned metadata.
  Do not rewrite the historical v1.4 entry.

#### Success criteria

- [x] `tests/test_pack_manifest.py`, `tests/test_manifest_warning_tier.py`,
  `tests/test_sandbox_trial.py`, and `tests/test_authority.py` pass.
- [x] The normal-loader and sandbox assertions are capability-specific, while a
  `consumes`-only manifest proves both production connectors ignore it.
- [x] No gateway registry, disable behavior, or authority lookup is added.

### Behavior 0.4 — priority remains registration-ordered metadata (09.5)

**Given** plain, LLM, relation, global, and pack behaviors with unequal and tied
priorities
**When** one event matches multiple behaviors
**Then** dispatch order is registration order, and delayed entries due on one tick
remain FIFO.

#### Characterize and document

- Drive unequal and tied plain handlers through `Runtime`, not only `Registry`,
  and assert handler effects plus lifecycle order.
- Add Runtime-driven LLM and relation ordering cases with a scripted provider and
  a touching relation, a global-before-pack case, same-tick delayed FIFO, and the
  exact `Runtime.status().registered_behaviors` order. This makes the stated guarantee cover
  every named dispatch layer; no test may stop at registration inspection.
- Add the same reserved comment to `RelationBehavior.priority` and clarify all
  decorator docstrings in `activegraph/behaviors/base.py`,
  `activegraph/behaviors/decorators.py`, and `activegraph/packs/__init__.py`.
- Update `docs/concepts/behaviors.md`, `specs/02-runtime-core.md`, and
  `specs/09-tools-behaviors.md` to cite the locked registration-order rule.

#### Success criteria

- [x] Characterization passes unchanged before doc/comment edits.
- [x] No sorting is added to registry, runtime loop, scheduler, or status.
- [x] `CONTRACT.md:217-221` remains semantically unchanged.
- [x] Unequal values and ties produce registration order for plain, LLM,
  relation, global/pack, delayed, and status observables.

### Behavior 0.5 — raw LLM provider remains LLM-context-only (09.7)

**Given** one runtime configured with a sentinel provider
**When** plain, relation, and LLM handlers inspect their contexts
**Then** only the LLM handler sees the sentinel; the other two see `None`.

#### Characterize and document

- In one sentinel-provider Runtime, execute plain, relation, and LLM handlers
  through the real queue and assert only the LLM context sees the exact sentinel.
- In a no-provider Runtime, execute plain/relation handlers and assert `None`;
  separately assert that an LLM behavior raises `MissingProviderError` when the
  public run entry calls `_ensure_registry`. Decoration alone is not the failure
  boundary. A no-provider Runtime cannot validly exercise an LLM handler.
- Clarify `Context.llm_provider` in `activegraph/runtime/runtime.py` as an
  LLM-invocation field, not general runtime service access.
- Update `docs/concepts/behaviors.md`, `specs/02-runtime-core.md`, and
  `specs/09-tools-behaviors.md` to direct recorded generation through
  `@llm_behavior` and embeddings through `Context.embed`. State explicitly that
  the LLM-context identity is a compatibility characterization, not permission
  for handler code to call `llm_provider.complete()` directly and bypass runtime
  request/response, cache, budget, retry, tool, and replay governance.

#### Success criteria

- [x] Provider identity/absence tests pass before and after docs edits.
- [x] Plain/relation `Context(...)` construction remains without
  `llm_provider=self.llm_provider`.

### Phase 0 documentation/contract gate

- Add `tests/test_policy_surface_docs.py` to read the exact changed docs/specs.
- Positive assertions require: direct `Graph.add_object` is immediate;
  `Context.propose_object` is explicit; `auto_apply` is reserved; capabilities
  are two-way verified; `consumes` wiring is host-owned; priority is inert;
  raw-provider calls are not a supported generation path; and the v1.11 overlay
  names every superseded historical clause.
- Negative assertions forbid the known false examples in current docs:
  `settings_key=`, nonexistent `object.proposed`, implicit direct-add routing,
  runtime auto-apply, capability exclusion from current verification, and advice
  to call the raw provider. Historical false sentences inside the named old
  CONTRACT sections remain as archaeology and are excluded from the negative
  scan; the overlay itself is mandatory.

## Phase 1 — Scheduled relation behaviors execute at fire time (09.6)

### Locked matcher, identity, and queue interfaces

The implementation must land these exact internal contracts before dispatch is
wired:

```python
PatternObserver = Callable[[float], None]

@dataclass
class ScheduledEntry:
    behavior: BehaviorLike
    behavior_name: str
    triggering_event_id: str
    fire_at_event_count: int
    scheduled_event_id: str

class Registry:
    def __init__(
        self,
        behaviors: Iterable[BehaviorLike],
        *,
        pattern_observer: PatternObserver | None = None,
    ) -> None: ...

    def _match_behavior(
        self,
        behavior: BehaviorLike,
        event: Event,
        graph: Graph,
    ) -> tuple[list[Relation], list[Any]] | None: ...

    def contains_identity(self, behavior: BehaviorLike) -> bool: ...

class DelayedQueue:
    def cancel_behaviors(self, behaviors: Collection[BehaviorLike]) -> int: ...
    def restore_due_front(self, entries: Sequence[ScheduledEntry]) -> None: ...
```

- Existing `Registry.match(event, graph)` remains source-compatible, iterates in
  registration order, and delegates one behavior at a time to
  `_match_behavior`.
- `ScheduledEntry` stores the exact behavior object plus its canonical name; drop
  positional `behavior_index` and unused `where_recheck_path`. At fire, both
  `Registry.contains_identity(entry.behavior)` and the unchanged canonical name
  must hold. A newly reloaded wrapper with the same name cannot inherit old work.
- Preserve filter/error order exactly: event-type gate -> pattern-only lifecycle
  gate -> pattern evaluation -> relation-type query -> endpoint-reference test ->
  relation `where`; plain `where` remains after pattern evaluation. Use flat guard
  clauses. Do not move relation `where` before the endpoint-reference test.
- The pattern observer is called once per actual matcher call, including an empty
  result or raised matcher, and never for an event/lifecycle rejection or behavior
  without a matcher. Pattern evaluation and observer notification remain separate
  statements. Observer failures are suppressed so observation cannot change
  matching; if matcher and observer both fail, the matcher exception retains
  precedence. Phase 1 lands the dormant callback seam; Phase 2 wires Runtime's
  metrics observer only after the scheduler checkpoint is green.
- `cancel_behaviors` uses object identity, preserves every other entry's order,
  and returns the removed count. `restore_due_front` prepends the complete
  unprocessed due suffix in supplied FIFO order ahead of retained future work;
  Runtime never mutates `DelayedQueue.entries` directly.

### Behavior 1.1 — one current relation fires once

**Given** a `RelationBehavior(activate_after=1)` and one matching relation
**When** the trigger is scheduled and the next event advances the tick
**Then** the handler runs once with that relation and original event, after
`behavior.scheduled`, and emits normal relation-start/completion lifecycle events.

#### 🔴 Red

- Add `test_scheduled_relation_behavior_fires_for_current_match` to
  `tests/test_activate_after.py`.
- Seed a relation touching an ID in the trigger payload (a trigger-time match is
  required for scheduling), emit through Graph/Runtime, advance the real queue
  and delayed driver, and observe handler-created graph state plus lifecycle
  events. Do not call `_fire_due_delayed` or `_invoke_relation` directly.
- Assert exact one invocation, relation ID, original event ID, and ordering:
  `behavior.scheduled` precedes `relation_behavior.started`, which precedes
  `behavior.completed`.
- Confirm current code fails because `_fire_due_delayed` skips the behavior.

#### 🟢 Green

- Implement the locked `_match_behavior`/`Registry.match` delegation above.
- In `_fire_due_delayed`, re-fetch the original event, reject stale behavior
  identity/name, call `_match_behavior`, emit any pattern audit marker once, and
  invoke `_invoke_relation` once for each returned current relation.
- Do not store relation IDs in `ScheduledEntry`; current-state re-evaluation is
  the selected contract.
- Do not reschedule at fire time.

#### 🔵 Refactor

- Remove duplicated delayed `where`/pattern logic only after parity tests pass.
- Keep `_matching_relations` private behind the registry method.
- Remove all positional delayed dispatch. Preserve canonical name plus exact
  object identity and FIFO delayed-entry order.

### Behavior 1.2 — current graph state decides the fan-out set

**Given** a relation matches at trigger time
**When** it is removed before fire
**Then** no handler runs; and given a matching relation is added after trigger but
before fire, the new current relation does run.

#### 🔴 Red

- Use `run_quantum` to stop after scheduling, then mutate with public
  `Graph.add_relation` / `Graph.remove_relation` before the next event advances
  the tick.
- Removal case: seed touching relation A, schedule, remove A, advance, and assert
  no invocation beyond `behavior.scheduled` evidence.
- Addition case: seed touching A so the behavior schedules, then add touching B
  and unrelated same-type C. Assert the exact invoked ID set is `{A, B}`, each
  once, and C never runs.
- Never assert `Graph.relations()` order: `GraphStore.all_relations` leaves it
  unspecified and FalkorDB has no `ORDER BY`.

#### 🟢 Green

- Recompute `_matching_relations` at fire time from the original event and current
  graph; never depend on trigger-time `rels`.
- Keep a budget check between relation invocations, matching the immediate loop;
  Behavior 1.5 locks what happens when it fails.

#### 🔵 Refactor

- Parameterize immediate/delayed parity cases where readable.
- Ensure lifecycle filtering prevents the relation behavior from retriggering on
  its own lifecycle events.

### Behavior 1.3 — `where`, pattern audit, and `ctx.matches` retain parity

**Given** delayed relation behaviors with `where` and/or `pattern`
**When** graph state changes before fire
**Then** relation candidates and pattern bindings use current graph state, while
`where` remains a filter over the original event payload.

#### 🔴 Red

- Replace the vacuous graph-lapsed test in `tests/test_activate_after.py`: current
  `where` accepts only `event.payload`, and the handler—not Runtime—currently
  checks patched graph state. Pin a true original-payload filter across graph
  mutation and a false filter that never schedules; do not claim graph-aware
  `where` without designing a new query root.
- Add lapsed/current pattern cases. On a current match assert exactly one
  `pattern.matched` per scheduled behavior/event, not per relation or binding;
  assert original event ID, canonical behavior, `matches_count`, marker ordering
  before the first `relation_behavior.started`, and identical complete
  `ctx.matches` bindings in every relation invocation.
- Add matcher-exception parity proving immediate/delayed error precedence and one
  observer callback per actual evaluation.

#### 🟢 Green

- Pass the shared registry match's `pattern_matches` into every relation
  invocation.
- Emit delayed `pattern.matched` only after current matching returns at least one
  relation and before fan-out starts.

#### 🔵 Refactor

- Keep matcher timing/observer calls outside boolean/walrus/control expressions.

### Behavior 1.4 — pack disable cancels exact pending work

**Given** a pack-owned scheduled relation behavior with typed settings
**When** the pack is disabled before fire, then reloaded
**Then** the old exact wrapper never fires; newly triggered work from the new
wrapper can fire normally.

#### 🔴 Red

- Add a mandatory pack-local closure case to `tests/test_packs.py` or
  `tests/test_disable_pack.py`; it is the only path proving pack wrapping,
  settings injection, canonical owner identity, and disable cancellation.
- Schedule pack A, disable it before fire, advance the runtime, and assert no
  `IndexError`, wrong-behavior dispatch, or handler effect. Reloading the same
  pack/name must not resurrect the old entry; a new trigger must schedule and
  execute the fresh wrapper.
- Add two packs with due work, disable the earlier registry entry, and prove the
  later pack's exact scheduled object still fires despite the old positional
  index shifting.

#### 🟢 Green

- In `Runtime.disable_pack`, collect the exact owned wrapper objects, call
  `DelayedQueue.cancel_behaviors` before filtering `_pack_behaviors` and before
  `_ensure_registry`, then rebuild.
- At fire, stale or name-mismatched objects are silently discarded with only the
  existing schedule evidence; never search by name for a replacement object.

#### 🔵 Refactor

- Keep cancellation in the named queue method, not hidden list surgery in
  Runtime. Preserve pending work owned by every other behavior.

### Behavior 1.5 — budget exhaustion restores due entries, not partial fan-out

**Given** several due entries and a finite behavior-call budget
**When** capacity ends before the due batch finishes
**Then** the entire unprocessed entry suffix is restored in FIFO order; once one
relation entry begins fan-out, its remaining relations are not resumable.

#### 🔴 Red

- Add an independent three-entry same-tick case exposing the existing bug: first
  fires, capacity ends, and both remaining entries—not only the current one—must
  remain FIFO and run after test-controlled capacity is increased. This red fails
  on due-tail loss, independently of the scheduled-relation skip.
- Add one scheduled relation entry with multiple current matches and a budget
  allowing only a strict prefix. Assert that prefix invokes once each, no cursor
  or entry remains, and later `run_until_idle` does not repeat completed relations
  or resume the suffix. This intentionally matches immediate fan-out semantics.
- Add relation-handler failure coverage: `_invoke_relation` records failure and
  the normal sibling fan-out continues while budget remains.

#### 🟢 Green

- Enumerate the popped due batch; if exhaustion is detected before entry `i`, call
  `restore_due_front(due[i:])` and stop. A completed prefix stays consumed.
- Keep the budget check between relations. Do not requeue the current relation
  entry after fan-out begins and do not introduce a relation cursor or persisted
  resume model.

#### 🔵 Refactor

- Document the two distinct rules: scheduled-entry FIFO is resumable before an
  entry starts; relation fan-out is an atomic/non-resumable local dispatch list,
  exactly like immediate relation fan-out.

### Behavior 1.6 — optional FalkorDB closure crosses full Runtime

- Extend `tests/test_falkordb_store.py` with the same schedule -> pause ->
  add/remove -> advance -> handler/lifecycle observation through a Runtime backed
  by FalkorDB. Direct `Registry.match` parity is not closure proof.
- Compare relation ID sets and once-per-ID multiplicity with the in-memory case;
  never compare native query order. Skip only through the suite's existing
  optional-backend fixture rule.

### Phase 1 contract/docs amendment

- Append a v1.11 overlay to CONTRACT v0.7 #13: `activate_after` remains
  event-count-only; fire-time “current state” means current relation candidates
  and pattern results, while `where` is re-evaluated against the immutable-by-
  contract original event payload. The overlay also locks exact-object delayed
  identity, disable cancellation, due-suffix restoration, non-resumable relation
  fan-out, and one pattern marker before fan-out.
- Update `activegraph/runtime/scheduler.py`'s module prose,
  `specs/02-runtime-core.md`, `specs/09-tools-behaviors.md`, and diagrams that show
  the skip or graph-aware `where` claim.

#### Phase 1 success criteria

- [x] Each Red is classified before implementation: core relation dispatch fails
  for the skip; disable fails for stale identity; due-tail fails for lost suffix;
  doc/where assertions fail for the false graph-state claim.
- [x] Immediate relation tests remain unchanged and green.
- [x] Delayed plain/LLM behavior tests remain green.
- [x] Pack disable/reload cannot dispatch an old or wrong wrapper.
- [x] Complete unprocessed due suffixes remain FIFO; mid-fan-out work is
  deliberately non-resumable.
- [x] Pattern marker count/order and full `ctx.matches` are exact.
- [x] Production-chain closure test starts at Graph/Runtime and observes graph
  events/state, satisfying the research closure map's highest connector.

## Phase 2 — Emit the complete standard metric contract (09.1 / 10.1)

### Metric semantics locked by this plan

- `*_calls_total` increments once per emitted Runtime-owned `*.requested` event,
  including cache hits and each retry attempt; missing label fields use the
  locked fallbacks. Cache-hit counters require literal `cache_hit is True`.
- Request-side LLM tags use the request payload's `model`; response-side
  LLM failure/token/cost tags use the response payload's `model`. Tool tags use
  the event being observed. Missing/non-string names use the exact metric-only
  literals `unknown_model` and `unknown_tool`; no request backtracking is needed.
- LLM/tool failure counters increment once when `error` is a mapping. A missing or
  `None` error is success; a non-`None`, non-mapping error is malformed and emits
  no family-specific response metrics. Generic event counting still occurs.
- Invalid tool input is not pre-request: it emits `tool.requested` and an error
  `tool.responded`, so it increments tool calls, failures, duration `0`, and the
  wrapping behavior failure. Missing/undeclared tools and budget/cost gates that
  occur before `tool.requested` remain behavior-only.
- LLM token histograms accept only successful, nonnegative finite numeric response
  fields, including cached logical responses. LLM cost is `0` on a cache hit and
  the valid nonnegative finite response cost otherwise. Invalid numeric fields are
  omitted, never coerced.
- Tool duration is `0` on a cache hit and the valid nonnegative finite response
  latency otherwise, including the current explicit `0` early-error responses.
- Behavior invocation counts each actual plain/LLM/relation invocation; relation
  fan-out counts once per relation. Duration covers only the developer handler.
  Failure increments exactly where `behavior.failed` is emitted, but its metric
  reason is deliberately normalized rather than copying an open event/log string.
- Add named metric-only constants/tables in
  `activegraph/observability/metrics.py`: missing/non-string reasons become the
  exact literal `unknown_reason`; LLM's seven documented reasons pass through,
  ToolError's eleven documented reasons pass through, and replay's four
  `ReplayDivergenceError.kind` values pass through. Other strings normalize to
  `llm.other`, `tool.other`, or `other`. Behavior reasons may additionally pass
  the fixed `llm.prompt_assembly_error`, `budget.exhausted`, and the eight
  `_budget_reason` outputs `budget.events_exhausted`,
  `budget.behavior_calls_exhausted`, `budget.llm_calls_exhausted`,
  `budget.tool_calls_exhausted`, `budget.patches_exhausted`,
  `budget.depth_exhausted`, `budget.seconds_exhausted`, and
  `budget.cost_exhausted`; arbitrary
  `exception.<Class>` becomes `exception.other`. The exact event/log reason
  remains unchanged.
- Main queue depth is local runtime state written into an untagged shared series.
  Its operator contract is **documented last writer wins**, not aggregation: each
  listener push, recovery requeue batch, successful pop, and successful initial
  activation publishes that Runtime's current local depth. Operators needing
  independent depths use independent backend instances/registries.
- Budget gauges are **Runtime-owned mutation observations** only: finite
  `max_cost_usd` / `max_events` publish after successful construction/load and
  after each Runtime-owned `consume`/`add_cost`. Direct external mutation or
  replacement of public `Runtime.budget` has no immediate metric-freshness
  guarantee. Compute from limits/used and `cost_remaining_amount`; never hoist or
  call `Budget.remaining()` for gauges because it mutates `_exhausted_by`.
- Initial queue/budget gauges publish only after construction has passed validation
  and sink attachment, or after load/fork has passed recovery, strict verification,
  and attachment. A failed strict load may emit its one divergence counter but no
  ghost queue/budget series. The metrics protocol cannot delete series.
- Pattern metrics count and time every actual matcher call, including empty and
  raised evaluations, across immediate and delayed paths.
- Replay divergence counts once per escaping strict divergence using the closed
  `.kind`; fresh verifier work uses NoOp metrics and emits no ordinary live work.
- Every emission supplies exactly the kind and tag-key set declared in
  `METRIC_BY_NAME`. Mapping is non-throwing and never changes event payloads.

### Behavior 2.1 — tests validate observations against the catalog

#### 🔴 Red

- Extend the `RecordingMetrics` helper in
  `tests/test_observability_metrics.py` to query exact counters/histograms/gauges.
- Add an assertion helper that resolves each observation through
  `METRIC_BY_NAME`, checks metric kind, and requires exact tag-key equality.
- Define `MetricProductionCase(id, proves, drive)` in that test module. Every
  `drive(RecordingMetrics, tmp_path)` must enter a real Graph/Runtime/Sink public
  production path—never call `Metrics` or a private emitter directly. Parametrize
  over cases and require each declared `proves` name to be actually observed with
  case-specific exact values.
- Add a meta-test asserting `union(case.proves) == set(METRIC_BY_NAME)` and emit a
  deterministic `catalog_name -> sorted(case.id)` failure map. A string pointing
  at a separate pytest test is not evidence; sink names must be produced by real
  attached sink worker scenarios inside this executable matrix.
- The new coverage test must fail with the current 15-name gap.

The minimum matrix is locked as follows (one case may prove several rows):

| Production case | Catalog names proved |
|---|---|
| `runtime_plain_queue_success` | `activegraph_events_emitted_total`, `activegraph_behaviors_invoked_total`, `activegraph_behaviors_duration_seconds`, `activegraph_queue_depth` |
| `plain_failure` | `activegraph_behaviors_failed_total` |
| `llm_live_success_and_cache` | `activegraph_llm_calls_total`, `activegraph_llm_cache_hits_total`, `activegraph_llm_tokens_in`, `activegraph_llm_tokens_out`, `activegraph_llm_cost_usd` |
| `llm_error_retry` | `activegraph_llm_failed_total` |
| `tool_live_success_and_cache` | `activegraph_tools_calls_total`, `activegraph_tools_cache_hits_total`, `activegraph_tools_duration_seconds` |
| `tool_invalid_input_and_invoker_error` | `activegraph_tools_failed_total` |
| `sink_deliver_drop_error_depth` | all four `activegraph_sink_*` catalog rows via attached workers |
| `finite_budget_direct_and_load` | `activegraph_budget_cost_remaining_usd`, `activegraph_budget_events_remaining` |
| `pattern_match_and_delayed_recheck` | both `activegraph_patterns_*` rows |
| `strict_replay_divergence` | `activegraph_replay_divergence_detected_total` |

#### 🟢 Green

- No production change in this cycle; add the helper, matrix skeleton, and explicit
  expected gaps locally while building the following slices. Remove every
  expected-gap escape hatch before Phase 2 completes.

#### 🔵 Refactor

- Keep the recording helper deterministic and independent of Prometheus/OTel
  optional packages.

### Behavior 2.2 — LLM and tool event metrics

**Given** successful, cached, retried, and failed LLM/tool calls
**When** their request/response events are emitted
**Then** calls, hits, failures, tokens, cost, and duration observations follow the
locked semantics above exactly once.

#### 🔴 Red

- Add LLM scenarios using existing scripted providers/cache fixtures from
  `tests/test_llm_behavior.py`, `tests/test_llm_failure.py`, and
  `tests/test_llm_replay.py`.
- Add tool success/cache/invalid-input/invoker-error/invalid-output cases using
  fixtures from `tests/test_tool_replay.py` and `tests/test_llm_tool_loop.py`.
- Assert exact values/tags, especially cached cost/duration zero, retry attempts,
  and invalid input as one tool request + one failed response + duration zero.
- Add malformed Runtime-owned event-payload cases at the central mapper boundary:
  missing model/tool uses the named fallback; unknown reasons normalize; invalid
  cache flags do not count as hits; non-mapping `error` and invalid/negative/
  nonfinite numeric fields omit only their family-specific observations without
  raising. The generic event counter still records each event.

#### 🟢 Green

- Add one central event-to-standard-metrics helper called from `_on_event` after
  the generic event counter and before lifecycle suppression.
- Map only `llm.requested`, `llm.responded`, `tool.requested`, and
  `tool.responded`; do not parse arbitrary user event types.
- Use the locked response/request ownership and named normalization constants;
  do not copy arbitrary `error.reason` or exception class strings into tags.
- Do not change graph event payloads solely to support metrics.

#### 🔵 Refactor

- Split LLM/tool mapping into small private helpers if `_on_event` becomes noisy.
- Ensure load/fork history replay still produces no observations because listeners
  attach only after history projection.

### Behavior 2.3 — behavior metrics cover all three invocation kinds

#### 🔴 Red

- Add plain success/failure, LLM handler success/failure, relation
  success/failure, and multi-relation fan-out assertions.
- Assert invoked count occurs even when the invocation later fails.
- Assert exactly one failed observation per `behavior.failed` event and one
  duration per developer handler that actually ran.
- Assert arbitrary exception class/reason strings cannot appear as metric labels:
  generic exceptions become `exception.other`, while event/log payloads retain
  their exact reason for diagnosis.

#### 🟢 Green

- Add invoked observations at the start of `_invoke_llm` and `_invoke_relation`
  alongside the existing `_invoke` observation.
- Time only `b.handler(...)` in the LLM body and `b.run(...)` in the relation path.
- Move failure counter emission into `_emit_behavior_failed`, using its resolved
  `log_reason` only as input to the metric-only normalizer, and remove the
  plain-path direct failure increment to prevent double counting.

#### 🔵 Refactor

- Introduce private `_record_behavior_invoked/duration` helpers only if they make
  the three paths visibly symmetric; do not merge the invocation implementations.

### Behavior 2.4 — queue and finite budget gauges report current state

#### 🔴 Red

- Strengthen the existing queue test to assert the last observation equals
  `Runtime.status().queue_depth` and is zero after full drain.
- Add finite `max_events` and `max_cost_usd` cases that assert initial and
  post-consumption values by `run_id`.
- Add unlimited-budget cases asserting those gauge families are not emitted.
- Persist a partial quantum, then `Runtime.load` it with a finite budget and a new
  backend: after successful recovery, assert the initial queue gauge is nonzero,
  no history event is recounted as live work, and drain reports zero. Test an
  unlimited `Runtime.fork`; `fork` has no budget parameter and must emit no budget
  gauge. Do not invent a finite-fork case or new public API.
- Add strict-load failure: requested backend observes exactly one replay-divergence
  counter and no initial queue/budget gauge. Add direct-constructor validation or
  sink-attachment failure with the same no-ghost rule.
- Add two runtimes sharing one backend and prove documented last-writer semantics:
  final untagged queue value equals the most recent local queue mutation, never a
  sum. Add a public `Budget.consume/add_cost` characterization proving direct
  external mutation has no immediate metric guarantee.
- Add optional OTel adapter coverage for first observation `gauge(..., 0)`, then
  `0 -> 2 -> 0`; it must export a datapoint ending at zero. The current adapter
  suppresses the first zero because its delta is zero.

#### 🟢 Green

- Add one `_record_queue_depth` method that updates observed maximum and publishes
  local depth. Call it after listener push, once after `_requeue_unfired`'s
  recovery batch, after successful pop, and on successful metric activation.
  DelayedQueue length is not part of this catalog metric.
- Add `_consume_budget` / `_add_budget_cost` wrappers for every Runtime-owned
  mutation. `_consume_budget` refreshes the event gauge only for `max_events`;
  `_add_budget_cost` refreshes the cost gauge. `_record_budget_gauges` emits only
  finite catalog dimensions. Convert Decimal intentionally and clamp remaining
  values at zero; do not call `Budget.remaining()` from observation code.
- Defer initial activation. Direct construction publishes only after all checks,
  sink attachment, and tracking succeed. `load`/`fork` reconstruct with NoOp
  metrics, recover/verify first, then install the requested backend, attach sinks,
  and publish the current initial snapshot. A failed strict load emits only its
  divergence counter through the requested backend.
- In `OpenTelemetryMetrics.gauge`, distinguish an unseen series from a prior zero
  and issue the first `add(0, attributes=...)`; later unchanged zeros may remain
  suppressed.

#### 🔵 Refactor

- Audit every current `Budget.consume`/`add_cost` call into the wrappers; preserve
  its exact evaluation and failure/event order. `Budget.remaining()` mutates
  `_exhausted_by`, so do not hoist it during cleanup.
- Document last-writer queue ownership, Runtime-owned budget scope, and retained
  zero/nonzero run-id series in the operator guide.

### Behavior 2.5 — pattern evaluations are measured at the shared matcher seam

#### 🔴 Red

- Add cases for event-type mismatch (no evaluation), eligible no-match (one
  evaluation), eligible match (one), multiple pattern behaviors (one each), and
  delayed recheck (a second evaluation), plus a matcher exception (one
  evaluation with the matcher exception preserved).
- Assert one counter and one nonnegative duration observation per actual call.

#### 🟢 Green

- Use the exact constructor-injected `PatternObserver` and `_match_behavior`
  contract landed in Phase 1; direct Registry consumers remain source-compatible.
- Measure immediately around `pattern_matcher.matches`; Runtime passes one
  non-throwing callback that emits the catalog counter/histogram. Notify in a
  separate statement, including empty/raised evaluations.
- Use the same callback in immediate and delayed matching.

#### 🔵 Refactor

- Keep `Registry` independent of concrete observability backends; pass a callable,
  not `Metrics`.

### Behavior 2.6 — replay divergence is counted once

#### 🔴 Red

- Extend LLM prompt-hash cases in `tests/test_llm_replay.py`, explicit stream
  length and type cases in `tests/test_replay.py`, and embedding-hash plus
  malformed wall-stop cases in `tests/test_embedding_replay.py` with a recording
  backend. The embedding cases do not live in `test_replay.py`.
- Cover instance-owned strict embedding cache/hash and LLM hash raises as well as
  the `_verify_replay` type/length/wall-stop raises; audit all
  `ReplayDivergenceError(...)` construction sites with `rg`.
- Assert one counter with `reason=exc.kind`, no raw expected/actual tag, and no
  ordinary simulated-run metric contamination. Assert the four kinds form the
  closed label set and any future unknown kind normalizes to `other`.

#### 🟢 Green

- Add a private instance helper that records a divergence using `.kind` immediately
  before instance-owned strict checks raise.
- Around the entire `Runtime.load` `_verify_replay` call, catch
  `ReplayDivergenceError`, emit once through the caller-requested backend, and
  re-raise unchanged. The not-yet-activated Runtime and fresh verification Runtime
  both retain NoOp metrics, preventing double count and initial-gauge ghosts.
- Audit every `raise ReplayDivergenceError` with `rg` so no public strict path is
  omitted and no path double counts.

#### 🔵 Refactor

- Keep the exception type/message/snapshot behavior byte-identical.
- Complete the scenario-matrix assertion: all 24 catalog names now have a proven
  runtime/sink emission path.

### Phase 2 documentation and success criteria

- Update metric descriptions only to clarify the locked semantics above; do not
  rename catalog entries.
- Update `docs/guides/operating-in-production.md`, `CONTRACT.md`,
  `specs/10-observability-trace-cli.md`, `specs/09-tools-behaviors.md`, and stale
  runtime comments that claim nonexistent hooks.
- Remove the “catalog-only” open-gap prose after tests prove complete emission.

- [x] All 24 `METRIC_NAMES` have at least one production emission path.
- [x] The executable `MetricProductionCase` union equals the catalog exactly and
  every declared row is actually observed through its case's public path.
- [x] Exact tag-key validation passes for every observed standard metric.
- [x] Cache/retry/invalid-input/malformed-payload/failure semantics and closed
  fallback labels are pinned.
- [x] Recovery reports nonzero then zero queue depth; shared backends obey
  documented last-writer semantics; failed load/construction leaves no gauges.
- [x] Finite load and unlimited fork reflect the real APIs; direct Budget mutation
  is documented outside immediate gauge freshness.
- [x] OTel exports an initial zero gauge datapoint.
- [x] No graph event ordering/payload changes were introduced by metrics.
- [x] Prometheus/OTel adapter conformance and sink isolation tests remain green.

## Phase 3 — Explicit payload logging honors the redactor (10.6)

### Behavior 3.1 — every configured JSON payload path is detached and redacted

**Given** a nested caller-owned payload and configured redactor
**When** a log call uses either `runtime_log_extra(payload=payload)` or direct
stdlib `extra={"payload": payload}` through the configured JSON handler
**Then** the callback receives a detached copy, JSON contains only the redacted
result, and the original nested value is unchanged.

#### 🔴 Red

- Add a test to `tests/test_observability_logging.py` with `io.StringIO`, a spy
  callback that mutates its input, and a nested secret.
- Parameterize the helper and raw-stdlib-extra entry paths. Assert exactly one
  callback invocation, one valid JSON line, redacted JSON payload, no original
  secret in the line, and deep equality of the original input.
- Current code must fail because callback count is zero and payload is absent.

#### 🟢 Green

- Add `payload` to the end of `LOG_FIELDS` as an optional public JSON field.
- Keep `runtime_log_extra` non-redacting so the callback cannot run twice.
- In `JsonLineFormatter.format`, special-case `payload` before the generic
  allowlist loop. Accept any `collections.abc.Mapping`, materialize/deep-copy it
  into a detached concrete `dict`, call `redact_payload`, require the callback's
  result to be a concrete `dict`, and validate it with the formatter's exact
  `json.dumps(..., separators=(",", ":"), ensure_ascii=False)` semantics before
  adding it to output.
- Without a configured callback, emit the detached explicit mapping unchanged.
  Direct stdlib extras and helper extras therefore cross the same final boundary.

#### 🔵 Refactor

- Extract a private `_prepare_log_payload` helper to contain copy/callback/type/
  serialization handling.
- Do not expose `set_payload_redactor`/`redact_payload` at new package levels.
- Keep copy, callback, validation, and serialization as serial statements outside
  boolean/control expressions.

### Behavior 3.2 — redaction fails closed and configuration clearing is explicit

**Given** a non-Mapping input, a copy failure, or a callback that raises, returns
a non-dict, or returns non-JSON data
**When** an explicit payload is logged
**Then** logging succeeds without a `payload` field and never falls back to the
unredacted original.

**Given** a prior callback and a later `configure_logging(...,
payload_redactor=None)`
**When** an explicit payload is logged
**Then** the callback is no longer called and the detached raw explicit payload is
emitted.

#### 🔴 Red

- Add parameterized input/copy/callback failure and invalid-return tests for both
  public logging entry paths. Invalid input/copy means zero callback invocations;
  callback raise/non-dict/non-JSON means exactly one invocation.
- Add repeated-configuration clearing and no-redactor identity tests.
- Every case must produce exactly one parseable JSON line with no payload/secret;
  otherwise “no exception/no payload” could pass on today's dead callback.
- Assert existing reserved-name/None filtering and `doc_url` behavior remain
  intact.

#### 🟢 Green

- Catch `Exception` (not `BaseException`) from copy/callback/serialization only
  inside payload preparation and omit the field; do not recursively log the
  redactor error.
- Preserve current process-global setter semantics.

#### 🔵 Refactor

- Restore the global redactor to `None` in test cleanup so test order cannot leak
  configuration.
- Add a modest concurrent-call test only if existing logging tests already use
  threads; do not redesign configuration synchronization in this slice.

### Behavior 3.3 — schema versioning is explicit and built-ins remain payload-free

#### 🔴 Red

- Extend runtime event and behavior-failure log tests to assert `payload` is
  absent even when the corresponding graph event carries data/traceback.
- Assert the exact 17-field `LOG_FIELDS` snapshot retains the current 16 fields
  (including `doc_url`) and appends optional `payload`; old lines remain unchanged
  when it is absent.

#### 🟢 Green

- Make no runtime/LLM/tool/pack logger call pass a payload.
- Update `docs/guides/operating-in-production.md`, `CONTRACT.md`, and
  `specs/10-observability-trace-cli.md`: the callback applies at the configured
  ActiveGraph JSON formatter to explicit helper or direct stdlib payload extras;
  arbitrary operator-installed handlers are outside that promise, human output
  never interpolates payload, and event sinks/persistence require separate policy.
- Append the full 17-field schema as a v1.11 amendment to v0.8 #6 and add an
  `[Unreleased]` changelog entry. Do not rewrite v0.8 #6 or the later v1.0.3
  `doc_url` amendment. v1.11 (rather than a patch label) is intentional because
  adding a public `LOG_FIELDS` member and beginning scheduled relation effects are
  additive public behavior under this repository's SemVer convention; package
  version files remain unchanged until release preparation.

#### 🔵 Refactor

- Remove claims that prompts/tool responses/goals are automatically placed in
  Python logs.
- Document callback scope, accepted `Mapping` input, concrete-dict callback
  output, global clearing, detached copy, exact JSON validation, and fail-closed
  omission.

#### Phase 3 success criteria

- [x] Red test fails because the current callback is never called.
- [x] Explicit redacted payload appears and original remains unchanged.
- [x] Callback/type/serialization failures cannot leak or break the log call.
- [x] Helper and direct stdlib-extra paths both cross exactly one formatter
  redaction call; invalid input never calls it.
- [x] Built-in log records remain payload-free.
- [x] JSON and human logging tests remain green; CONTRACT/changelog/schema docs
  match the exact 17-field `LOG_FIELDS` tuple.

## File-level implementation map

| File | Planned change |
|---|---|
| `activegraph/runtime/registry.py` | Exact `_match_behavior`, identity membership, constructor-injected `PatternObserver`, preserved filter/error order. Production caller/registration: `Runtime._ensure_registry`; direct `Registry.match` remains compatible. |
| `activegraph/runtime/scheduler.py` | Store exact behavior object/name in `ScheduledEntry`; add identity cancellation and FIFO due-suffix restoration; correct graph-aware-`where` prose. Production caller: Runtime schedule/fire/disable paths. |
| `activegraph/runtime/runtime.py` | Scheduled relation dispatch, pack-disable cancellation, due-tail handling, pattern marker; LLM/tool/behavior/queue/budget/pattern/replay metric seams; context boundary docs. Register pattern observation when `_ensure_registry` builds the production Registry. |
| `activegraph/runtime/budget.py` | No public callback/API; clarify Runtime-owned observation scope only if needed. Runtime wrappers retain mutation ordering. |
| `activegraph/observability/logging.py` | Append optional `payload`; final-formatter detached redaction for helper/direct extras; fail-closed preparation. Production registration: `configure_logging` installs `JsonLineFormatter` on the ActiveGraph handler. |
| `activegraph/observability/metrics.py` | Add closed metric-only normalization constants/tables and clarify descriptions; no metric names/tags added. |
| `activegraph/observability/otel.py` | Export first-observation zero gauges instead of suppressing the datapoint. |
| `activegraph/behaviors/base.py` | Mark relation priority reserved. |
| `activegraph/behaviors/decorators.py` | Clarify priority metadata contract. |
| `activegraph/policy.py` | Correct false actively-routed/enforced `requires_approval` docstring. |
| `activegraph/packs/__init__.py` | Correct `requires_approval`, `auto_apply`, and capability boundary docstrings/comments. |
| `activegraph/packs/loader.py` | Correct misleading gating comments only. |
| `tests/test_activate_after.py`, `tests/test_pattern_subscriptions.py` | Scheduled relation, original-event `where`, pattern marker/bindings, due-tail and matcher parity Reds. |
| `tests/test_runtime.py`, `tests/test_disable_pack.py` | Full-dispatch priority/context characterization; exact scheduled identity, shifted survivor, disable/reload cancellation. |
| `tests/test_packs.py` | Explicit approval, paired auto-apply no-op, mandatory pack scheduled-wrapper/settings closure. |
| `tests/test_pack_manifest.py`, `tests/test_manifest_warning_tier.py`, `tests/test_sandbox_trial.py` | `consumes` round-trip plus capability-specific normal-warning/sandbox-strict connector coverage. |
| `tests/test_policy_surface_docs.py` (new) | Positive/negative executable gates for all five intentional boundaries and append-only overlays. |
| `tests/test_observability_metrics.py`, `tests/test_event_sinks.py` | Exact recorder and `MetricProductionCase` 24-row matrix; queue/budget/OTel/last-writer/no-ghost cases; real sink workers. |
| `tests/test_observability_logging.py` | Helper/direct-extra redaction, Mapping/copy/callback/failure/clearing/17-field schema tests. |
| `tests/test_llm_behavior.py`, `tests/test_llm_failure.py`, `tests/test_llm_replay.py` | Metric scenarios at existing provider/replay seams. |
| `tests/test_tool_replay.py`, `tests/test_llm_tool_loop.py` | Tool metric success/cache/error semantics. |
| `tests/test_replay.py`, `tests/test_embedding_replay.py` | Type/length and embedding/wall-stop divergence metrics at the correct modules. |
| `tests/test_falkordb_store.py` | Optional full-Runtime delayed relation closure with set/multiplicity oracle. |
| `docs/concepts/policies.md`, `docs/concepts/behaviors.md`, `docs/guides/authoring-packs.md`, `docs/guides/operating-in-production.md` | Replace current false claims with exact intentional/runtime boundaries. |
| `specs/02-runtime-core.md`, `specs/07-packs.md`, `specs/09-tools-behaviors.md`, `specs/10-observability-trace-cli.md` | Align interfaces, workflows, metric ownership, and exact 17-field log schema. |
| `CONTRACT.md`, `CHANGELOG.md` | Append v1.11 correction/schema amendments and `[Unreleased]` entry; never rewrite historical numbered clauses. |

No new public exception, event type, metric name/tag, gateway interface, or
persistence schema is planned. The internal `ScheduledEntry` shape changes and
the public log schema gains the additive seventeenth `payload` field.

## Workflow Closure

Every finding crosses a runtime, loader, sandbox, backend, or configured logging
boundary, so each is **BLOCKING**; unit tests alone are insufficient.

| Behavior | SOURCE | TRIGGER / real driver | FORBIDDEN SPAN | OBSERVABLE | Class |
|---|---|---|---|---|---|
| Explicit approval | Pack policy + Graph state | Real behavior event through `Runtime.run_*` (synchronous driver); sibling public `Graph.add_object` | Do not seed `_pack_state`, call `_add_pending_approval`, or inspect a handler return | `Runtime.pending_approvals`, captured public proposal ID, Graph events/projection | BLOCKING: Context -> Runtime -> Graph cross-module path |
| Reserved `auto_apply` | Paired otherwise-identical Pack values | `Runtime.load_pack`, direct add, real proposal behavior | Do not prove only constructor normalization or private-field absence | `loaded_packs`, normalized event stream, Graph projection, pending approvals | BLOCKING: loader/runtime composition |
| Capability/`consumes` | Pack + manifest | Normal `Runtime.load_pack` and sandbox `run_forked_trial` driver | Direct `verify_surface` alone is not closure | Warning + loaded/dispatchable pack; sandbox materialization result | BLOCKING: loader and subprocess boundary |
| Priority | Registered global/pack/plain/LLM/relation behaviors | Graph event + Runtime drain; delayed tick driver | Registry-only/order-only assertions | Handler effects, lifecycle order, `Runtime.status().registered_behaviors` | BLOCKING: registration and dispatch layers |
| Provider context | Sentinel provider + touching relation | Public run entry builds registry and drains queue | Manually constructed `Context` | External captures from all handler types; public `MissingProviderError` | BLOCKING: registration/invocation boundary |
| Scheduled relation | Graph event/relation state | Graph emit -> `run_quantum`/`run_until_idle` drive main and delayed queues | No direct `_match_behavior`, `_fire_due_delayed`, `_invoke_relation`, relation-ID seeding, sleeps, or span mocks | Graph lifecycle/events and handler-created state; exact identity/cancellation | BLOCKING: two queue edges and registry/runtime boundary |
| Standard metrics | Runtime/Graph/budget/sink inputs | Real public runtime, replay, and attached-sink scenarios; sink flush/drain where async | No direct Metrics call/private emitter as production proof | `RecordingMetrics` or optional adapter export through exact 24-row case matrix | BLOCKING: Runtime/Sink -> backend boundary |
| Payload redaction | Caller-owned Mapping | `get_logger(...).info` through configured JSON handler, using helper and raw extra | Do not call `redact_payload`, `_prepare_log_payload`, or formatter directly as the only proof | Parsed configured stream, callback count, caller immutability | BLOCKING: stdlib record/handler/formatter boundary |

Staged adapter proposals live beside the research document. They are not imported
or executable production artifacts.

## Implementation order and checkpoints

1. Run the affected baseline and add Phase 0 characterization tests.
2. Update only Phase 0 docs/comments; rerun approval/pack/runtime/legacy suites.
3. Complete scheduled relation Behaviors 1.1–1.6 one Red–Green–Refactor cycle at a
   time; checkpoint after immediate/delayed parity is green.
4. Add the metric recording/catalog helper, then implement event metrics,
   behavior metrics, gauges, matcher metrics, and replay metrics in that order.
5. Complete the 24-name emission coverage and backend regression checkpoint.
6. Implement explicit payload redaction in three cycles and update log docs.
7. Run focused, then full tests; review the diff for accidental public additions,
   event changes, raw metric tags, or payload logging.

Do not combine Phase 1 and Phase 2 into one refactor even though the shared matcher
supports both; checkpoint scheduled behavior with the observer dormant before
wiring Runtime's pattern metrics so a failure has one behavioral cause.

## Verification commands

Use the repository's supported environment; substitute the verified external
pytest path only in this worktree.

```bash
pytest -q tests/test_packs.py tests/test_pack_manifest.py \
  tests/test_manifest_warning_tier.py tests/test_sandbox_trial.py \
  tests/test_authority.py tests/test_disable_pack.py \
  tests/test_policy_surface_docs.py \
  tests/test_persistence.py tests/test_compaction.py \
  tests/test_promote.py tests/test_legacy_byte_identity.py

pytest -q tests/test_activate_after.py tests/test_runtime.py \
  tests/test_pattern_subscriptions.py tests/test_disable_pack.py \
  tests/test_falkordb_store.py

pytest -q tests/test_observability_metrics.py tests/test_event_sinks.py \
  tests/test_llm_behavior.py tests/test_llm_failure.py \
  tests/test_llm_replay.py tests/test_tool_replay.py \
  tests/test_llm_tool_loop.py tests/test_replay.py \
  tests/test_embedding_replay.py

pytest -q tests/test_observability_logging.py \
  tests/test_v1_0_3_behavior_failed_ux.py

pytest -q
```

Static review:

```bash
rg -n '\bauto_apply\b|gated_object_types|\.consumes\b|\.priority\b|llm_provider=' activegraph tests
rg -n 'activegraph_(llm|tools|budget|patterns|replay)' activegraph/runtime activegraph/observability
rg -n 'payload_redactor|redact_payload|runtime_log_extra|LOG_FIELDS' activegraph tests docs CONTRACT.md specs
rg -n 'settings_key|object\.proposed|capabilities.*excluded|llm_provider\.complete' docs specs
git diff --check
```

## Final acceptance checklist

### Automated

- [x] Every implementation test was observed failing for the intended reason
  before its production change.
- [x] Phase 0 characterization stayed green throughout.
- [x] All 24 declared metrics have exact-kind/tag runtime emission coverage.
- [x] The metric-only reason/name labels are closed; malformed payloads never
  throw or leak arbitrary labels.
- [x] Scheduled relation closure works through the production queue and delayed
  driver for in-memory storage; optional FalkorDB parity passes where configured.
- [x] Pack disable/reload cancels exact old work, preserves other scheduled
  objects, and due-tail/fan-out semantics match the v1.11 overlay.
- [x] Legacy approval byte identity and persistence/promote tests remain green.
- [x] Strict replay behavior/events remain unchanged except the out-of-band
  divergence counter.
- [x] Full pytest suite passes.
- [x] `git diff --check` passes and no generated/activegraph-unrelated files are
  included.

### Manual/code review

- [x] Public docs make all eight decisions explicit and contain no contradictory
  auto-gating/auto-apply/provider/payload claims.
- [x] Old numbered CONTRACT clauses remain historical; the append-only v1.11
  overlay and `[Unreleased]` changelog state the current rules.
- [x] No metric tag contains IDs, user payload, model output, error messages, or
  hashes.
- [x] Built-in logs remain payload-free.
- [x] No normal-load capability enforcement or gateway registry was introduced.
- [x] Registration order and `Runtime.status().registered_behaviors` ordering are
  unchanged.
- [x] Scheduled relation effects are called out as the one intentional runtime
  compatibility change for external packs using the formerly dropped combination.

## References

- Research:
  `thoughts/searchable/shared/research/2026-08-11-15-54-pack-governance-runtime-policy-surfaces.md`
- Applied review:
  `thoughts/searchable/shared/plans/2026-08-11-16-00-tdd-pack-governance-runtime-policy-surfaces-REVIEW.md`
- Source audit:
  `thoughts/searchable/shared/research/2026-08-11-10-21-specs-diagrams-code-problems.md`
- Implementation Beads issue: `AF-bcj` (discovered from the linked research)
- Review Beads issue: `AF-s7v`; revision blocker resolved by this plan: `AF-pq3`
- Related Beads issue: `AF-zd1` (broad system-map/spec audit; already owned)
- Contracts/specs: `CONTRACT.md`, `specs/02-runtime-core.md`,
  `specs/07-packs.md`, `specs/09-tools-behaviors.md`,
  `specs/10-observability-trace-cli.md`
