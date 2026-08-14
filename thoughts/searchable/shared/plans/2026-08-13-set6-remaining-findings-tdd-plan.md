---
date: 2026-08-13T16:40:00-04:00
planner: tha-hammer (with Claude Code)
git_commit: ad6a05cdc65ca815b46f59ffeea301398935d996
branch: repair-impl-integration
repository: silmari-activegraph
topic: "TDD plan for Set 6 — the 19 findings that fell through the planning gap"
tags: [plan, tdd, core, patch, ids, errors, graph-store, sandbox, packs, llm, cli, quickstart, trace]
status: enhanced_ready_for_implementation
related_beads: [AF-w18w]
research: thoughts/searchable/shared/research/2026-08-13-set6-remaining-findings.md
---

# TDD plan: Set 6 — the 19 remaining findings from the 2026-08-11 master audit

## Objective

Repair the 15 verified-and-still-present defects from `AF-w18w` (findings `01.1, 01.2, 01.3, 01.6,
01.9, 01.10, 06.1, 06.3, 06.4, 06.5, 07.4, 08.5, 10.8, 10.9, 10.12`) without breaking any currently
passing test, and explicitly document why the remaining 4 findings (`02.5, 07.10, 08.9, 10.11`) get
no source change. This plan makes no source change itself — it is implementation-ready, split into
Red/Green/Refactor cycles small enough that a future implementer can stop after any single phase
with the suite green.

## Coverage checklist — every one of the 19 `AF-w18w` findings, accounted for

This checklist exists because these 19 findings already fell through one planning gap once (between
the 2026-08-11 master audit and the 5 themed implementation sets). Every ID below is either mapped
to a phase that changes source, or explicitly descoped with reasoning — none are silently dropped.

| # | Finding | Disposition | Plan section |
|---|---|---|---|
| 01.1 | `PATCH_OPS` declares 4 ops; projector implements 2 | **Fixed** | [Phase 1](#phase-1--011--012-patch-operation-taxonomy) |
| 01.2 | `PATCH_OPS` is dead code, unvalidated | **Fixed** | [Phase 1](#phase-1--011--012-patch-operation-taxonomy) |
| 01.3 | `IDGen` "not thread-safe" doc vs. ID gen outside the `RLock` scope | **Fixed** | [Phase 2](#phase-2--013-id-generation-inside-the-emit-lock-scope) |
| 01.6 | `core ↔ runtime` error-class naming/location coupling | **Fixed (docs-only)** | [Phase 3](#phase-3--016-document-the-core-raises-execution-errors-pattern) |
| 01.9 | `GraphStore.clear()` depends on non-abstract `remove_patch()` | **Fixed** | [Phase 4](#phase-4--019-promote-remove_patch-to-an-abstractmethod) |
| 01.10 | Underscore-prefixed methods are documented public cross-module seams | **Fixed (consistency-only)** | [Phase 5](#phase-5--0110-standardize-the-seventh-seam-suppression-comment) |
| 02.5 | Redundant llm-cache double-write | **Descoped: already fixed** | [Descoped findings](#descoped-findings-no-source-change) |
| 06.1 | `sandbox/conformance.py` hard-imports `pytest`; `store/graph_conformance.py` doesn't | **Fixed** | [Phase 6](#phase-6--061-drop-the-pytest-hard-dependency-from-the-three-conformance-suites) |
| 06.3 | Wall-clock-killed trial's `events_appended` is inflated by the parent's own marker | **Fixed** | [Phase 7](#phase-7--063-stop-counting-the-parents-own-wall-clock-marker) |
| 06.4 | `_resolve_scenario` has no containment check | **Fixed** | [Phase 8](#phase-8--064--065-scenario-resolution-hardening) |
| 06.5 | Pack module registered in `sys.modules`; scenario module is not | **Fixed** | [Phase 8](#phase-8--064--065-scenario-resolution-hardening) |
| 07.4 | `rt.graph.emit(...)` unguarded after the `if rt.graph is not None:` guard | **Fixed (simplification)** | [Phase 9](#phase-9--074-drop-the-unreachable-guard) |
| 07.10 | `packs → llm` diagram edge needed an "example-pack-only" caveat | **Descoped: already fixed** | [Descoped findings](#descoped-findings-no-source-change) |
| 08.5 | `supports_native_structured_output` guard doesn't work as implied for `Protocol` subclasses | **Fixed** | [Phase 10](#phase-10--085-give-the-protocol-method-a-real-default-body) |
| 08.9 | `llm → core` diagram edge omitted `llm`'s dependency on `frame`/`errors` | **Descoped: already fixed** | [Descoped findings](#descoped-findings-no-source-change) |
| 10.8 | `quickstart.py` writes to a fixed shared `/tmp/activegraph_quickstart/` path; 3 unused fs imports | **Fixed** | [Phase 11](#phase-11--108--109-quickstart-cli-hygiene) |
| 10.9 | Quickstart's fire counter is name-coupled to `"growth_flagger"` | **Fixed** | [Phase 11](#phase-11--108--109-quickstart-cli-hygiene) |
| 10.11 | `DOCS_BASE_URL` comment still says it knowingly 404s | **Descoped: external infra dependency** | [Descoped findings](#descoped-findings-no-source-change) |
| 10.12 | `format_event` special-cases `llm.requested` outside its `_FORMATTERS` table | **Fixed (docs + dead-entry cleanup)** | [Phase 12](#phase-12--1012-explain-and-tighten-the-llmrequested-special-case) |

15 findings get a source change across 12 phases (some phases bundle two tightly-coupled findings:
01.1+01.2 share one root cause, 06.4+06.5 share one function pair). 4 findings are descoped with
documented reasoning: 3 were independently verified already fixed by unrelated merged work
(`02.5`, `07.10`, `08.9` — re-verified in the research pass, not merely trusted from the original
audit), and 1 (`10.11`) has no possible in-repo code fix because it depends on external DNS/Pages
infrastructure landing, and is already tracked separately (`CONTRACT.md` v1.1 #9).

## Baseline and evidence

- Research: `thoughts/searchable/shared/research/2026-08-13-set6-remaining-findings.md`.
- Baseline commit: `ad6a05cdc65ca815b46f59ffeea301398935d996` on `repair-impl-integration`.
- This worktree has a local `.venv`; use `.venv/bin/python -m pytest` (confirmed working — system
  `python3` has no `pytest` installed).
- Full-suite baseline: `.venv/bin/python -m pytest -q` → **1732 passed, 59 skipped, 1 failed** in
  30.89s. The 1 failure is `tests/test_llm_claude_code_install.py::test_first_complete_call_without_sdk_is_one_terminal_request_error_with_install_hint`,
  pre-existing and unrelated to any of the 19 findings (it fails on an install-hint subprocess
  assertion in the Claude Code provider's SDK-absent path). Do not let any phase's gate accidentally
  turn this into "2 failed" vs. "1 failed" — confirm it is the *same* test name before and after each
  phase.
- Targeted baseline for every file this plan touches or tests against: `.venv/bin/python -m pytest -q
  tests/test_patch.py tests/test_graph.py tests/test_ids.py tests/test_errors_format.py
  tests/test_graph_store.py tests/test_packs.py tests/test_sandbox_trial.py tests/test_trial_executor.py
  tests/test_store_conformance.py tests/test_event_sinks.py tests/test_llm_native_structured_output.py
  tests/test_quickstart_snapshot.py tests/test_quickstart.py tests/test_llm_trace_snapshot.py
  tests/test_replay_trace_snapshot.py tests/test_tool_trace_snapshot.py tests/test_cli.py
  tests/test_doc_links.py` → **518 passed** in 6.87s. This is the regression floor for every phase
  below.
- All file:line citations in this plan were independently re-verified by direct reads during
  planning (not copied from the research doc without checking) against `HEAD ad6a05c`. Where the
  underlying investigation was delegated to a sub-agent, this plan's author re-read the specific
  lines the decision hinges on before locking the fix.

## Decisions locked by this plan

Several findings had more than one defensible fix. Each is locked here with the reasoning, so
implementation doesn't re-litigate the judgment call.

| Finding | Engineering decision | Reasoning |
|---|---|---|
| 01.1 + 01.2 | Narrow `PATCH_OPS`/`Patch.op` docstring to `{"update", "replace"}` and validate `op` in `propose_patch`, rather than implementing `create`/`remove` semantics for patches | `Graph.add_object`/`object.created` and `Graph.remove_object`/`object.removed` (`core/graph.py:642-692,774`) are already the first-class, dedicated paths for creating/removing objects. A patch's job (CONTRACT #4/#12) is a targeted, version-checked mutation of an *existing* object's data — `create`/`remove` never had a real implementation to fall back to, they were an aspirational taxonomy that was never built. Building real create/remove-via-patch semantics now would duplicate `add_object`/`remove_object` and is a materially bigger, undiscussed feature addition; narrowing the taxonomy to match the two ops that are actually implemented is the surgical fix. |
| 01.3 | Move `self.ids.*()` calls inside the same `with self._emit_lock:` scope the paired `self.emit(...)` call already opens, rather than only editing the docstring | Investigation confirmed no code path in this repo currently calls `Graph` mutating methods on the *same instance* from two threads concurrently (`threading.Thread` appears only in `sinks/dispatch.py`'s per-sink delivery worker, which never touches `Graph`, and in one conformance test that uses two distinct `Graph` instances, one per thread). So there's no live crash to reproduce — but `_emit_lock` is an `RLock` specifically documented as serializing "each graph's live acceptance... boundary" (`graph.py:191-196`), and ID generation is part of accepting a new event. Bringing ID generation inside that existing, already-reentrant lock closes the gap between the lock's stated purpose and its actual scope, for the cost of a few `with` statements — cheaper and more honest than leaving the docstring to describe a boundary the code doesn't actually enforce. |
| 01.6 | Document the local-import-then-raise pattern as the established, intentional convention (already used for 5 exec_errors.py classes raised from `core/graph.py`, not just the 3 the finding names) rather than relocating error classes | Moving `ReservedFieldError`/`InvalidPatchLifecycleState`/`InternalEvaluatorError` (and, by the same logic, `ObjectNotFoundError`/`ApplyPatchNotFoundError`) out of `runtime/exec_errors.py` into `core/` would be a public-import-path-breaking relocation across a module every one of these classes' existing tests, docs, and any external consumer already imports from. `CONTRACT.md:298`'s "core/ knows nothing about runtime/" rule is about *behavioral* dependencies (core must not import runtime behavior), not about *which module physically defines an exception type* core happens to raise via a function-local import — the existing pattern is a narrow, already-consistent exception to that rule for exactly this purpose. Fix the documentation gap (the rule doesn't currently carve out this exception in writing), not the code. |
| 01.10 | Standardize the 7th cross-module seam call site (`packs/loader.py:925-926`) onto the same `# noqa: SLF001` suppression the other 6+ sites already use, rather than building a new internal `Protocol` type | 6+ of the 7 call sites already carry `# noqa: SLF001` and a docstring/comment marking the underscore member as an intentional seam — the pattern is already mostly consistent. The 7th site uses `# type: ignore[attr-defined]` instead, which is also the *wrong* suppression: `_pack_object_validator`/`_pack_relation_validator` are real attributes `Graph.__init__` already declares (`graph.py:217-218`), so mypy has nothing to complain about there — the actual lint concern is the private-name access, which is `SLF001`'s domain. A full internal-`Protocol` formalization is a legitimate future improvement but is new type-system surface no finding asked for; the outlier-comment fix closes the actual inconsistency the finding names. |
| 07.4 | Delete the `if rt.graph is not None:` guard around `_install_graph_validators` entirely, rather than adding a matching guard around `rt.graph.emit(...)` | `Runtime.__init__`'s `graph: Graph` parameter is non-`Optional` (`runtime.py:407-409`), assigned once to `self.graph` at `runtime.py:461`, and a repo-wide grep for `.graph = None`, `Runtime(.*graph=None`, and `Runtime(None` all return zero matches — no reachable path can make `rt.graph is None` inside `load_pack_into_runtime`. Adding a matching guard around `emit` would make the code symmetric but would keep defending against a state the type system already forbids; deleting the now-provably-dead guard removes the inconsistency the finding actually flags (one call guarded, the next one not) by making both calls share the same unconditional path. |
| 08.5 | Change `LLMProvider.supports_native_structured_output`'s Protocol body from `...` to `return False`, rather than changing the `runtime.py` guard | The `runtime.py:1226-1229` `getattr(..., None)` guard is already crash-safe (`or` short-circuits, so `not supports(b.model)` never runs against `None`) — there's no live crash. The actual gap: an explicit `class Foo(LLMProvider):` subclass that omits the override inherits the bare-`...` method, which returns `None` when called — the runtime's fallback to `"prompt"` mode only works today *by coincidence* (`not None` is `True`), not because the method resolves the way its own docstring describes ("custom providers... simply resolve to the prompt-embedded path" implies the *getattr* default is what saves them, but for a real Protocol subclass, getattr finds the inherited method — it's the accidental `None`-is-falsy fallthrough doing the work). Giving the Protocol method an explicit `return False` body makes the documented contract literally true for both duck-typed and Protocol-subclassed providers, for the cost of one line. |
| 10.8 | Replace the hardcoded `"/tmp/..."` literal with a `tempfile.gettempdir()`-derived path (keeping the single shared, cleaned-up-before-each-run filename design), and remove the two genuinely-dead imports (`os`, `shutil`) | `_QUICKSTART_RUN_ID` is a hardcoded literal by explicit design (`quickstart.py:93-95`'s own comment: "we don't want quickstart leaving N database files in /tmp") — that's a deliberate demo-hygiene tradeoff, not a bug, and quickstart is a single-shot demo CLI command with no documented concurrent-invocation contract, so changing it to a per-process-unique path would reintroduce the litter problem the existing code deliberately avoids. The real, unambiguous defects are: (a) `os`/`shutil` are dead imports with zero uses anywhere in the file (confirmed by word-boundary grep), and (b) the path is hardcoded to POSIX `/tmp` rather than using the already-imported (but unused) `tempfile.gettempdir()`, which breaks portability to non-POSIX temp layouts. Fixing (b) also makes `tempfile` a real, used import instead of a third dead one. |
| 10.12 | Add explanatory comments; do not build a generic per-formatter options-passing mechanism | Exactly one of 26 formatter functions (`_fmt_llm_requested`) needs an extra render-time kwarg (`hide_prompt_normalized`). Making every formatter accept `**opts` it ignores, or adding an introspection layer to detect which formatters want extra kwargs, is new mechanism built for a need that doesn't exist yet anywhere else in the table — exactly the kind of speculative generality the project's own conventions avoid. The `_FORMATTERS["llm.requested"]` table entry is genuinely unreachable through `format_event`'s dispatch (the early-return special case always wins first) but has independent value if anything else ever introspects `_FORMATTERS.keys()` for the set of known event types; keep it, but say why in a comment so a future reader doesn't "clean it up" into a silent behavior change. |

## Scope boundaries

This plan does not:

- implement `create`/`remove` semantics for the patch propose/apply/reject workflow (see 01.1/01.2
  decision above — that is a materially larger feature addition, not implied by any of the 19
  findings, and would need its own design pass if wanted);
- relocate any `exec_errors.py` class out of `runtime/` into `core/` (01.6);
- build a general internal `Protocol`/typed-seam system for the underscore-prefixed cross-module
  members (01.10) — only the one inconsistent suppression comment is fixed;
- add rollback/transactional semantics to `load_pack_into_runtime`'s mutation block beyond removing
  the one dead guard (07.4) — a full atomicity audit of that function is out of scope for this
  finding;
- change quickstart's shared-demo-path design to support concurrent invocations (10.8);
- touch DNS, GitHub Pages, or any deployment infrastructure for `docs.activegraph.ai` (10.11 —
  no code change is possible here; see [Descoped findings](#descoped-findings-no-source-change));
- add a generic per-formatter options-passing mechanism to `activegraph/trace/printer.py` (10.12);
- reopen or re-verify `02.5`, `07.10`, or `08.9` — the research pass already independently
  re-confirmed each is fixed by direct reads of current source, not by trusting the original audit.

## Workflow closure and blocking gates

Most of these 15 fixes are small, single-function, single-module changes with a synchronous public
trigger and observation point — **LEAF** in the sense that a direct unit test at the point of change
is sufficient production-wiring evidence. A few cross a real boundary (event emission + replay,
subprocess isolation, registry rebuild) and need an integration-level test as the release gate.

| Finding(s) | Classification and reason | SOURCE → TRIGGER → OBSERVABLE |
|---|---|---|
| 01.1 + 01.2 | **LEAF** — direct dataclass/method call, no cross-module boundary | Invalid `op` string → `Graph.propose_patch(...)` → raised typed error, no event emitted |
| 01.3 | **LEAF**, but observation requires lock-ownership introspection, not timing | `Graph.add_object`/`propose_patch` call → monkeypatched `IDGen` method asserts `graph._emit_lock._is_owned()` at call time |
| 01.9 | **LEAF** — Python's own ABC machinery is the mechanism | A `GraphStore` subclass missing `remove_patch` → class construction → `TypeError` at `__init__`, not at `clear()` |
| 06.1 | **BLOCKING** for the regression gate only, because these are base classes consumed by other test files across 3 subsystems, not called directly | Conformance module import → concrete pytest subclass runs the mixed-in suite → no `pytest` import required for the conformance module itself; existing subclass suites (`test_trial_executor.py`, `test_store_conformance.py`, `test_falkordb_store.py`, `test_postgres_store.py`, `test_event_sinks.py`) must stay green unchanged |
| 06.3 | **LEAF** — single function, store-observable via existing `Runtime.load` re-read | Trial with wall-clock kill → `run_forked_trial(...)` → `TrialReport.events_appended` |
| 06.4 + 06.5 | **BLOCKING** — `_child.py` has zero direct unit coverage today; only reachable via a real subprocess spawn in `test_sandbox_trial.py`, or a new direct-import unit test | Path-traversal `scenario` string / a resolved scenario module → `_resolve_scenario(root, scenario)` called in-process → raised `RuntimeError` / `sys.modules` entry |
| 07.4 | **LEAF**, refactor-classified (no new observable behavior — see Phase 9) | N/A — existing `tests/test_packs.py` `pack.loaded`/validator-installation assertions are the regression gate |
| 08.5 | **LEAF** | A `class BareProtocolProvider(LLMProvider):` that omits the override → direct call and `Runtime._resolve_structured_output_mode` → `False` / `"prompt"` |
| 10.8 + 10.9 | **LEAF** | `quickstart` module import / interactive scaffold write → path constant / scaffold text → resolved path, matched behavior name |
| 10.12 | **LEAF**, refactor-classified (no new observable behavior — see Phase 12) | N/A — existing `tests/test_llm_trace_snapshot.py`/`test_replay_trace_snapshot.py`/`test_tool_trace_snapshot.py` are the regression gate |

For the two **BLOCKING** rows, the phase's integration test (through the real public entry point —
a concrete conformance subclass, or a real `_child.py` import) is the release gate; a test that calls
a private helper directly without going through that boundary is not accepted as sufficient evidence
on its own, though it is still useful as a fast first Red step.

## Phase 0 — characterization and test harness

### Red

Before touching any production file, run the two baseline commands above and record their exact
pass/fail counts (already done in this plan's Baseline section — an implementer re-running this
phase should reproduce `1732 passed, 59 skipped, 1 failed` on the full suite and `518 passed` on the
targeted set, and should stop and investigate if either number differs, since that means the
worktree has drifted from this plan's assumptions).

No new test files are required in Phase 0 itself — each phase below adds its own characterization
test(s) as its first Red step, scoped to that phase's finding(s), rather than front-loading every
test into one giant phase-0 commit. This keeps each phase bisectable on its own.

### Green

No production change in this phase.

### Refactor

None. Phase 0 is a checkpoint, not a change.

## Phase 1 — 01.1 + 01.2: patch operation taxonomy

**Given** a `Graph` whose `PATCH_OPS` constant and `Patch.op` docstring claim `create | update |
replace | remove` are all valid,
**when** `propose_patch(..., op="create")` (or `"remove"`, or any other non-`update`/`replace`
string) is called,
**then** today it succeeds silently — the patch is proposed and, if later applied, the projector's
`patch.applied` branch (`core/graph.py:1085-1096`) skips all data mutation for that op while still
unconditionally bumping `obj.version` and marking the patch `applied` if the target object already
exists (or doing nothing at all if it doesn't) — no error, no taxonomy enforcement anywhere.

### Red

1. In `tests/test_patch.py`, add `test_propose_patch_rejects_op_outside_update_or_replace`: for each
   of `"create"`, `"remove"`, `"delete"`, `""`, assert `graph.propose_patch(target=obj.id, op=<bad>,
   value={...}, proposed_by="test")` raises a new `InvalidPatchOperationError`, and that after the
   raise: no `patch.proposed` event was emitted (`len(graph.events)` unchanged), no patch exists in
   `graph._state.all_patches()`, and the id counters (`graph.ids._patch_counter`,
   `graph.ids._event_counter`) are unchanged from before the call — mirroring the existing "no
   mutation on the miss" style already used in this file's other negative-path tests.
2. Add `test_propose_patch_still_accepts_update_and_replace`: `op="update"` and `op="replace"` both
   still succeed exactly as before (characterization — locks current positive-path behavior before
   touching the validation).
3. Assert the raised error is simultaneously `InvalidPatchOperationError`, `ExecutionError`,
   `ActiveGraphError`, and `ValueError` (matching the sibling `InvalidPatchLifecycleState` pattern at
   `exec_errors.py:254`), and carries the offending `op` value and the valid set as structured
   context.
4. In `tests/test_errors_format.py`, add the new leaf to the hierarchy/doc-slug audit alongside
   `InvalidPatchLifecycleState`, and a snapshot under `tests/snapshots/errors/` for its rendered
   form.
5. Run only the new tests; confirm they fail because `propose_patch` currently accepts any string.

### Green

1. In `activegraph/core/patch.py`, narrow `PATCH_OPS = {"create", "update", "replace", "remove"}` to
   `PATCH_OPS = {"update", "replace"}` (`patch.py:17`), and update the `Patch`/`op` docstring
   (`patch.py:22-28`) to say `op (update | replace)` and to note that object creation/removal are
   handled by `Graph.add_object`/`Graph.remove_object` directly, not by patches.
2. In `activegraph/runtime/exec_errors.py`, add `InvalidPatchOperationError(ExecutionError,
   ValueError)` next to `InvalidPatchLifecycleState` (`exec_errors.py:254`), following the exact same
   shape: `_doc_slug = "invalid-patch-operation-error"`, constructor taking `op: str` and the valid
   set, structured summary/cause/recovery text.
3. In `activegraph/core/graph.py`'s `propose_patch` (`graph.py:853-903`), add a validation check
   immediately after the comment-noted normalization (before any `_state`/`Patch`/`Event`
   construction, so a rejected op has zero side effects): `if op not in PATCH_OPS: from
   activegraph.runtime.exec_errors import InvalidPatchOperationError; raise
   InvalidPatchOperationError(op=op, valid_ops=PATCH_OPS)`. Import `PATCH_OPS` from
   `activegraph.core.patch` at module scope (it's already imported for `Patch`).
4. Re-export `InvalidPatchOperationError` from `activegraph/__init__.py` alongside the other runtime
   execution errors, matching the existing re-export list.
5. Leave `apply_event`'s `patch.applied` branch (`graph.py:1085-1096`) untouched — with the taxonomy
   narrowed and enforced at proposal time, its two existing branches (`update`/`replace`) are now
   exhaustive for every patch that can ever reach `applied` status; no projector change is needed.

### Refactor

- Update `docs/reference/errors/invalid-patch-lifecycle-state.md`'s neighboring doc page (or add a
  new one) for `InvalidPatchOperationError`, cross-linked from the patch lifecycle reference.
- Update `specs/01-core.md` and any prose that still lists `create | update | replace | remove` as
  the patch op taxonomy to say `update | replace`, with a one-line note on why create/remove aren't
  patch ops (they're `add_object`/`remove_object`).
- Add a changelog/migration note: `propose_patch(op=...)` now validates `op` and raises
  `InvalidPatchOperationError` for anything outside `{"update", "replace"}`; this is a
  backward-incompatible tightening for any caller that was — silently and ineffectively — passing
  `"create"`/`"remove"` before.

Targeted gate:

```bash
.venv/bin/python -m pytest -q tests/test_patch.py tests/test_graph.py tests/test_errors_format.py
```

Success: `PATCH_OPS`, the `Patch.op` docstring, and `propose_patch`'s runtime validation all agree;
attempting an unsupported op fails fast with zero side effects instead of silently no-op'ing through
the projector; `update`/`replace` behavior is byte-identical to before.

## Phase 2 — 01.3: ID generation inside the `_emit_lock` scope

**Given** `Graph._emit_lock` is a re-entrant `threading.RLock()` documented as serializing "each
graph's live acceptance... boundary" (`graph.py:191-196`), and `Graph.emit()` already acquires it
for the append/project/persist/sink-offer sequence (`graph.py:586`),
**when** any of the 8 `Graph` sugar methods that generate an id via `self.ids.*()` before their own
mutation reaches `self.emit(...)` runs,
**then** today that id generation happens *outside* the lock's scope for every one of them —
`add_object` (`graph.py:654,683`), `add_relation` (`:707,742`), `remove_relation` (`:764`),
`remove_object` (`:785`), `patch_object` (`:818,838`), `propose_patch` (`:875,894`), `apply_patch`
(`:935` on its normal path), and `reject_patch` (whose own body is just `return self._reject(...)`
— the actual `self.ids.event()` call it triggers lives at `:977`, inside the private `_reject`
helper that `apply_patch`'s version-mismatch branch also delegates to) — even though `IDGen`'s own
docstring calls it "not thread-safe" and relies on being called from inside a serialized boundary.
This was confirmed by re-running `grep -n "self\.ids\." activegraph/core/graph.py` directly against
`HEAD ad6a05c` during planning (not copied from a prior pass without checking): the only other match
is `Graph.__init__`'s `self.ids.run()` at `:176`, which is construction-time and exempt — it never
runs inside the live serialized-acceptance boundary `_emit_lock` protects.

### Red

1. In `tests/test_graph.py`, add `test_add_object_generates_ids_while_holding_emit_lock`: monkeypatch
   `graph.ids.object` (and separately `graph.ids.event`, since `add_object` also generates an event
   id before calling `emit`) with a wrapper that asserts `graph._emit_lock._is_owned()` is `True` at
   the moment it's called, then delegates to the real method. Call `graph.add_object("task", {})` and
   assert no `AssertionError` was raised by the wrapper. This is a deterministic, non-flaky test —
   it checks lock ownership at the exact call site, not timing.
2. Add `test_propose_patch_generates_ids_while_holding_emit_lock`, same pattern for
   `graph.ids.patch` and `graph.ids.event` inside `propose_patch`.
3. Add `test_add_relation_generates_ids_while_holding_emit_lock`: same wrapper pattern, monkeypatching
   `graph.ids.relation` and `graph.ids.event`. First create two objects unpatched
   (`obj_a = graph.add_object("task", {})`, `obj_b = graph.add_object("task", {})`), then install the
   wrapper and call `graph.add_relation(obj_a.id, obj_b.id, "blocks")`.
4. Add `test_remove_relation_generates_ids_while_holding_emit_lock`: create an object pair and a
   relation between them unpatched first (`add_object` x2 + `add_relation`), then monkeypatch only
   `graph.ids.event` (the sole id call `remove_relation` makes) and call
   `graph.remove_relation(relation.id)`.
5. Add `test_remove_object_generates_ids_while_holding_emit_lock`: create an object unpatched first
   (`obj = graph.add_object("task", {})`), then monkeypatch `graph.ids.event` and call
   `graph.remove_object(obj.id)`.
6. Add `test_patch_object_generates_ids_while_holding_emit_lock`: create an object unpatched first,
   then monkeypatch `graph.ids.patch` and `graph.ids.event` and call
   `graph.patch_object(obj.id, {"k": "v"})`.
7. Add `test_apply_patch_generates_ids_while_holding_emit_lock`: create an object and propose a patch
   against it unpatched first (`obj = graph.add_object("task", {})`;
   `patch = graph.propose_patch(target=obj.id, op="update", value={"k": "v"}, proposed_by="test")`,
   with the object left unmutated after proposal so `patch.expected_version` still matches
   `obj.version` and `apply_patch` takes its normal — not version-mismatch — path). Monkeypatch
   `graph.ids.event` and call `graph.apply_patch(patch.id)`.
8. Add `test_reject_patch_generates_ids_while_holding_emit_lock`: same object+proposed-patch setup as
   step 7, unpatched. Monkeypatch `graph.ids.event` and call `graph.reject_patch(patch.id, "test
   reason")`. Note for whoever implements Green: `reject_patch` (`graph.py:950-959`) has no
   `self.ids.*()` call of its own — its entire body is `return self._reject(patch_id, reason, ...)`.
   The `self.ids.event()` call this test targets is inside the private `_reject` helper
   (`graph.py:961-990`), which is where Green's fix for this test actually needs to land.
9. Add `test_apply_patch_version_mismatch_also_generates_ids_while_holding_emit_lock`: same setup as
   step 7, but mutate the object after proposing the patch (e.g. an unpatched
   `graph.patch_object(obj.id, {"k": "v2"})`) so its version advances past the proposal's
   `expected_version`. Monkeypatch `graph.ids.event` and call `graph.apply_patch(patch.id)`; this
   time `apply_patch` takes its version-mismatch branch (`graph.py:925-932`) and delegates to
   `_reject` instead of running its own direct `self.ids.event()`/`self.emit()` call at `:935`/`:948`.
   Assert no `AssertionError` from the wrapper. This is a second angle on the same `_reject` helper
   step 8 targets — it exists to prove that fixing `_reject` once (Green step 8 below) closes the gap
   for *both* of `apply_patch`'s two internal paths, not just its normal one, so no separate wrap is
   needed inside `apply_patch`'s mismatch branch itself.
10. Run the seven new tests added in steps 3-9; confirm each fails today for the stated reason (the
    wrapper's assertion fires because `_emit_lock` is not yet held at the relevant `ids.*()` call).

### Green

1. In `activegraph/core/graph.py`, wrap `add_object`'s body from the first `self.ids.object(type)`
   call through the `self.emit(event)` call in `with self._emit_lock:` (safe: `emit()` re-acquires
   the same `RLock` internally, which is reentrant by design for exactly this kind of nested-caller
   scenario per the lock's own docstring).
2. Apply the same wrapping to `propose_patch`, from `self.ids.patch()` through `self.emit(event)`.
3. Wrap `add_relation`'s body from `self.ids.relation()` (`:707`) through `self.emit(event)` (`:750`)
   in `with self._emit_lock:`.
4. Wrap `remove_relation`'s body from `self.ids.event()` (`:764`) through `self.emit(event)` (`:772`).
5. Wrap `remove_object`'s body from `self.ids.event()` (`:785`) through `self.emit(event)` (`:793`).
6. Wrap `patch_object`'s body from `self.ids.patch()` (`:818`) through `self.emit(event)` (`:850`).
7. Wrap `apply_patch`'s normal-path body from `self.ids.event()` (`:935`) through
   `return self.emit(event)` (`:948`). Leave the version-mismatch branch (`:925-932`,
   `return self._reject(...)`) untouched here — it delegates to `_reject`, which step 8 covers.
8. Wrap the shared `_reject` helper's body (`graph.py:961-990`) from `self.ids.event()` (`:977`)
   through `return self.emit(event)` (`:990`) in `with self._emit_lock:`. Because both
   `reject_patch` (`:950-959`) and `apply_patch`'s version-mismatch branch (`:925-932`) delegate to
   `_reject`, this single wrap closes the defect for both call paths at once — no separate edit is
   needed inside `reject_patch` itself, since it has no lines of its own besides the delegating
   `return self._reject(...)` call.
9. Update `activegraph/core/ids.py`'s `IDGen` class docstring (`ids.py:42`) to be accurate about the
   division of responsibility: `IDGen` itself holds no internal lock; `Graph` is responsible for
   calling every `ids.*()` method while holding `self._emit_lock`, which is what makes concurrent use
   safe in practice. Keep the existing "single-threaded loop" framing but clarify it describes
   `IDGen` in isolation, not the `Graph`-mediated calling contract.

### Refactor

- Green now adds 8 `with self._emit_lock:` wrap sites total (`add_object`, `propose_patch`,
  `add_relation`, `remove_relation`, `remove_object`, `patch_object`, `apply_patch`, and the shared
  `_reject` helper). Consider (but don't require) a small private helper (e.g. a method decorator or
  context-manager alias) only if it doesn't obscure the lock's reentrancy story for a future reader —
  the existing lock usage elsewhere in the file (10 call sites at
  `graph.py:359,365,407,434,454,476,489,501,517,529,586`) is all direct `with self._emit_lock:`
  blocks with no wrapper, so introducing one here would be a departure from the file's own
  convention; at 8 sites (up from the 2 originally scoped) this is closer to being worth it than
  before, but still optional — prefer leaving it explicit unless implementation turns up further
  friction.
- Note in `specs/01-core.md` or `CONTRACT.md` (wherever the `_emit_lock`/ID-generation contract is
  documented, if anywhere) that ID generation is part of the serialized acceptance boundary.

Targeted gate:

```bash
.venv/bin/python -m pytest -q tests/test_graph.py tests/test_ids.py tests/test_patch.py
```

Success: every `self.ids.*()` call inside a `Graph` sugar method happens while `_emit_lock` is held
by the calling thread — all 8 of them (`add_object`, `add_relation`, `remove_relation`,
`remove_object`, `patch_object`, `propose_patch`, `apply_patch`, `reject_patch` via the shared
`_reject` helper), not just the 2 originally scoped; `IDGen`'s docstring accurately describes where
thread-safety actually comes from; no observable behavior changes for any existing caller
(single-threaded call patterns are unaffected by acquiring a re-entrant lock they'd have implicitly
waited on anyway once `emit()` ran).

## Phase 3 — 01.6: document the core-raises-execution-errors pattern

**Given** `CONTRACT.md:298` states "core/ knows nothing about runtime/ or behaviors/" as an import
direction rule, and `activegraph/runtime/exec_errors.py`'s `InternalEvaluatorError` docstring
(`exec_errors.py:313`) explicitly says it is "Used by `activegraph/core/graph.py`",
**when** a reader compares the two,
**then** they look contradictory — except investigation confirms this local-import-then-raise
pattern (`from activegraph.runtime.exec_errors import X; raise X(...)`) is not a one-off exception
for 3 classes; it's the established convention for at least 5 exec_errors.py classes raised from
`core/graph.py` (`ReservedFieldError` at `graph.py:133-135`, `InvalidPatchLifecycleState` at
`graph.py:919-920`, `InternalEvaluatorError` at `graph.py:1164,1185`, plus `ObjectNotFoundError` and
`ApplyPatchNotFoundError` imported the same way at `graph.py:811,915`). This finding is a
documentation gap, not a live behavioral defect — see the locked decision above for why relocating
the classes is out of scope.

This phase makes no test-observable behavior change; there is no Red/Green pytest cycle. It is
verified by direct doc/text inspection, consistent with the research document's own note that 01.3
and 01.6 "might be handled as doc/lint-level fixes rather than behavior-changing patches."

### Green

1. In `CONTRACT.md`, immediately after the `:298` import-direction rule, add a one-sentence
   documented exception: core-layer functions may raise execution-semantics error *types* that are
   physically defined in `runtime/exec_errors.py` via a function-local import, because the error
   taxonomy (not behavior) belongs to the execution layer even when the failure is detected in
   `core/`; core never imports `runtime/` at module scope and never depends on `runtime/` behavior.
2. Update `InternalEvaluatorError`'s docstring (`exec_errors.py:304-318`) to frame its use from
   `core/graph.py` as the established pattern rather than a special case — cross-reference
   `ReservedFieldError`/`InvalidPatchLifecycleState`/`ObjectNotFoundError`/`ApplyPatchNotFoundError`
   as siblings following the same convention, so a future reader hits the explanation on the first
   class they check, not just this one.
3. Update `specs/01-core.md`'s error-taxonomy section (if it restates the layering rule) to match.

### Refactor

None beyond the doc edits above — there is no source-code change in this phase.

Verification (manual, not pytest — this phase changes no executable behavior):

```bash
grep -n "knows nothing about" CONTRACT.md
grep -n "from activegraph.runtime.exec_errors import" activegraph/core/graph.py
.venv/bin/python -m pytest -q tests/test_errors_format.py tests/test_graph.py  # regression: unchanged
```

Success: `CONTRACT.md`'s layering rule and `exec_errors.py`'s docstrings no longer read as
contradictory to a new reader; zero source files under `activegraph/` change; the regression gate
stays green because nothing executable moved.

## Phase 4 — 01.9: promote `remove_patch` to an `@abstractmethod`

**Given** `GraphStore.clear()`'s default implementation (`graph_store.py:272-279`) calls
`self.remove_patch(p.id)` for every patch, and `GraphStore.remove_patch`'s default body
(`graph_store.py:281-288`) is `raise NotImplementedError` — but `remove_patch` is **not** in the
`@abstractmethod` set (`graph_store.py:70-116`),
**when** a third-party (or future in-repo) `GraphStore` subclass implements only the currently
required abstract methods and never overrides `remove_patch`,
**then** it passes construction cleanly and only fails later, at the first `clear()` call, with a
`NotImplementedError` whose origin is one level removed from where the ABC's contract would suggest
looking.

Both concrete backends shipped in this repo (`InMemoryGraphStore` at `graph_store.py:294`,
`FalkorDBGraphStore` at `falkordb.py:168`) already override both `clear()` and `remove_patch()`
directly, so this defect is currently latent — it protects future/external backends, not anything
shipped today.

### Red

1. In `tests/test_graph_store.py`, add `test_graphstore_subclass_missing_remove_patch_fails_at_construction`:
   define a minimal local subclass of `GraphStore` inside the test that implements every method
   currently in the `@abstractmethod` set (`put_object`, `get_object`, `remove_object`,
   `all_objects`, `put_relation`, `get_relation`, `remove_relation`, `all_relations`, `put_patch`,
   `get_patch`, `all_patches` — trivial stub bodies) but does **not** override `remove_patch`. Assert
   that `MinimalStore()` raises `TypeError` containing `remove_patch` at construction time.
2. Run only the new test; confirm it currently fails (`MinimalStore()` constructs successfully today
   since `remove_patch` isn't required).
3. Add a companion characterization test (or confirm existing coverage already does this) that
   `InMemoryGraphStore()` and, where the optional FalkorDB dependency is available,
   `FalkorDBGraphStore(...)` both still construct and `clear()` successfully — regression proof that
   promoting the method doesn't break either shipped backend, since both already override it.

### Green

1. In `activegraph/core/graph_store.py`, add `@abstractmethod` immediately above `def
   remove_patch(self, patch_id: str) -> None:` (`graph_store.py:281`), keeping its existing docstring
   (it already explains why `clear()` needs it) and removing the now-redundant `raise
   NotImplementedError` body (an abstract method with no default body is the correct ABC shape; the
   docstring's explanation stays as the method's `"""..."""`, followed by `...` or `pass`).

### Refactor

- Update `docs/` or `specs/`'s `GraphStore` interface reference (wherever the required-methods list
  is documented for backend authors) to add `remove_patch` to the "must implement" list.
- Confirm no other in-repo `GraphStore` subclass exists besides the two already checked (`grep -rn
  "class.*GraphStore" --include=*.py` — confirmed exactly two in the research pass) so this change
  is a pure tightening with zero adaptation needed elsewhere in this repo.

Targeted gate:

```bash
.venv/bin/python -m pytest -q tests/test_graph_store.py tests/test_falkordb_store.py tests/test_postgres_store.py tests/test_store_conformance.py
```

Success: an incomplete `GraphStore` subclass now fails fast at construction with a standard
`TypeError` naming the missing method, matching where the ABC contract points; both shipped backends
are unaffected since they already implement `remove_patch`.

## Phase 5 — 01.10: standardize the seventh seam suppression comment

**Given** 6+ underscore-prefixed cross-module accesses of `Graph`/`SinkHandle` internals
(`Graph._replay_event`, `_remove_listener`, `_sink_names_in_use`, `SinkHandle._offer`,
`_close_worker`, `_is_terminal`) are each called from outside their defining class with a `# noqa:
SLF001` comment and a docstring marking them as an intentional seam,
**when** `activegraph/packs/loader.py:925-926` does the same kind of cross-module private-attribute
write (`graph._pack_object_validator = ...`, `graph._pack_relation_validator = ...`),
**then** it uses `# type: ignore[attr-defined]` instead — a different suppression, for a different
tool category (mypy's attribute-existence check, not ruff's private-access check), even though
`_pack_object_validator`/`_pack_relation_validator` are real attributes `Graph.__init__` already
declares at `graph.py:217-218`, so there is nothing attribute-*undefined* about this site; the actual
lint concern is the private-name cross-module write, same as the other six sites.

This phase makes no runtime-behavior change — it's a suppression-comment consistency fix. There is
no pytest Red/Green cycle for the comment swap itself; the verification is a repo-hygiene grep plus
the full regression suite for the file this touches.

### Red

1. Add a small static-consistency check, `tests/test_internal_seam_hygiene.py`, asserting: every
   line in `activegraph/` matching `_pack_object_validator\s*=|_pack_relation_validator\s*=` outside
   `activegraph/core/graph.py` itself (i.e. every cross-module write site) carries a `# noqa: SLF001`
   comment on the same line, not `# type: ignore[attr-defined]`. This is a plain grep-driven test
   (read the file, regex-match, assert), not an import-time check.
2. Run it; confirm it fails against `packs/loader.py:925-926` today.

### Green

1. In `activegraph/packs/loader.py:925-926`, replace `# type: ignore[attr-defined]` with `# noqa:
   SLF001` on both lines, matching the convention every other cross-module seam call site already
   uses.
2. If removing `# type: ignore[attr-defined]` surfaces a real mypy complaint (verify by running the
   project's configured type-check command, if any, over this file — check `pyproject.toml`/`mypy.ini`
   for the configured invocation first), that would mean the attribute genuinely isn't visible to
   mypy at this call site (e.g. because `Graph`'s `_pack_object_validator` attribute isn't typed at
   `__init__`); in that case, add a type annotation for the attribute in `Graph.__init__`
   (`graph.py:217-218`) rather than reinstating the `type: ignore`, so the underlying visibility gap
   is actually fixed instead of re-suppressed.

### Refactor

- Add a one-line comment directly above `graph._pack_object_validator = ...` in `loader.py`
  cross-referencing the same seam-list documentation as the other six sites, if one exists (e.g. a
  `core/graph.py` module comment cataloging all intentional external seams); if no such catalog
  exists yet, this is a natural place to start one, but building it out fully is optional and not
  required by this finding.

Targeted gate:

```bash
.venv/bin/python -m pytest -q tests/test_internal_seam_hygiene.py tests/test_packs.py tests/test_disable_pack.py
```

Success: all cross-module private-member access sites for `Graph`/`SinkHandle` internals use the
same `# noqa: SLF001` suppression convention; no behavior changes; pack loading/disable/reload tests
stay green.

## Phase 6 — 06.1: drop the `pytest` hard dependency from the three conformance suites

**Given** `activegraph/store/graph_conformance.py` (492 lines, `GraphStoreConformance`) has zero
`import pytest` and zero exception-path assertions,
**when** `activegraph/sandbox/conformance.py` (`TrialExecutorConformance`),
`activegraph/store/conformance.py` (`EventStoreConformance`), and `activegraph/sinks/conformance.py`
(`EventSinkConformance`) are each imported,
**then** they hard-require `pytest` at module scope solely to use `pytest.raises(...)` (plus
`excinfo.value` capture in one case) inside exactly one or two test methods each — even though
`store/conformance.py`'s own docstring already claims "The suite intentionally avoids pytest
fixtures... so it works under any test runner" (`conformance.py:11-13`), which the module-scope
`import pytest` directly contradicts.

### Red

1. In `tests/test_store_conformance.py`, `tests/test_trial_executor.py`, and `tests/test_event_sinks.py`
   (the existing concrete subclasses of these three ABCs), run the full existing suites first as a
   characterization baseline — they should already pass (confirmed: 518/518 in the targeted baseline
   run). No new failing test is needed to *observe* the pytest dependency; instead:
2. Add `tests/test_conformance_modules_are_pytest_independent.py`: for each of the three modules
   (`activegraph.sandbox.conformance`, `activegraph.store.conformance`,
   `activegraph.sinks.conformance`), read the module's source file and assert (via `ast.parse` +
   walking `Import`/`ImportFrom` nodes, not a naive substring grep, to avoid false positives on
   `pytest` appearing in a comment or docstring) that no module-scope `import pytest` statement
   exists. Run it; confirm it fails for all three today.

### Green

1. In `activegraph/sandbox/conformance.py`: remove `import pytest` (line 7). Replace
   `test_malformed_specification_fails_before_execution`'s `with pytest.raises(ValueError):
   executor.execute("{}")` with:
   ```python
   try:
       executor.execute("{}")
   except ValueError:
       pass
   else:
       raise AssertionError("expected ValueError for a malformed specification")
   ```
2. In `activegraph/store/conformance.py`: remove `import pytest` (line 20). Replace
   `test_duplicate_id_in_same_run_is_rejected`'s `with pytest.raises(DuplicateEventError) as
   excinfo:` block (opens at `conformance.py:166`) with a `try/except DuplicateEventError as exc:
   ... else: raise AssertionError(...)` shape, rewriting the follow-on
   `excinfo.value.context`/`str(excinfo.value)` assertions at `conformance.py:171-177` to use `exc`
   directly. Replace the `with pytest.raises(Exception):`
   at `conformance.py:279` the same way (`except Exception as exc:` — keep the broad catch since the
   original test intentionally didn't narrow it, but preserve that behavior rather than narrowing it
   as a side effect of this refactor).
3. In `activegraph/sinks/conformance.py`: remove `import pytest` (line 17). Replace
   `test_event_rejected_before_acceptance_is_not_delivered`'s `with pytest.raises(TypeError): ...`
   the same way.
4. Update `store/conformance.py`'s module docstring if needed so the "works under any test runner"
   claim is now actually true rather than aspirational.

### Refactor

- Double-check no other `pytest.` usage (fixtures, markers, `pytest.mark.*`) exists in any of the
  three files beyond the `raises` calls just removed — the investigation found none, but re-grep
  `pytest\.` across all three files after the edit to confirm zero remaining references.
- Leave `activegraph/store/graph_conformance.py` untouched — it's the reference pattern, not a
  target of this fix.

Targeted gate:

```bash
.venv/bin/python -m pytest -q tests/test_conformance_modules_are_pytest_independent.py tests/test_trial_executor.py tests/test_store_conformance.py tests/test_falkordb_store.py tests/test_postgres_store.py tests/test_event_sinks.py tests/test_sandbox_trial.py
```

Success: all three conformance modules import and run their assertions with zero `pytest` dependency
at the module level, matching `graph_conformance.py`'s pattern; every existing concrete subclass
suite passes unchanged (same assertions, same failure messages where the original captured
`excinfo.value`/`str(excinfo.value)` text).

## Phase 7 — 06.3: stop counting the parent's own wall-clock marker

**Given** `_run_forked_trial_local` (`sandbox/__init__.py:391-543`) snapshots `stop_sequence =
len(fork_view.graph.events)` (`:510`) *before* emitting the parent's own `trial.wall_clock_exhausted`
marker event (`:511-527`) on the `timed_out` branch,
**when** `events_appended = max(0, len(fork_view.graph.events) - initial_events)` (`:528-530`) is
computed immediately after that emit, on the same branch,
**then** `len(fork_view.graph.events)` at that point already includes the just-appended marker, so a
wall-clock-killed trial's `events_appended` is inflated by exactly 1 relative to what the child
process itself actually produced — while the non-timed-out branches, which never emit a marker
before this same line runs, are unaffected.

### Red

1. In `tests/test_sandbox_trial.py`, extend `test_wall_clock_blow_kills_the_child` (currently at
   lines 223-250, which already asserts `marker.payload["stop_position"]["accepted_sequence"] ==
   len(fork.graph.events) - 1`) with a new assertion: `report.events_appended ==
   marker.payload["stop_position"]["accepted_sequence"] - initial_events_count_for_this_fork`. Since
   the test doesn't currently expose `initial_events` directly, compute the expected value from data
   already available in the test (the fork's event count immediately after `fork()`, before the
   trial runs, mirroring how `initial_events` is captured in the production code) — or, more simply,
   assert `report.events_appended == accepted_sequence` where `accepted_sequence` is read from the
   marker's own payload (since `stop_sequence` in production is captured before `initial_events` is
   subtracted, and the fork's initial event count in this test's fixture is known/derivable).
2. Run it; confirm it fails today because `report.events_appended` is 1 higher than the marker's
   `accepted_sequence`-derived expectation.

### Green

1. In `activegraph/sandbox/__init__.py`, on the `timed_out` branch (`:507-530`), reuse the
   already-captured `stop_sequence` for the `events_appended` computation instead of re-reading
   `len(fork_view.graph.events)` after the marker emit: change the shared computation at
   `:528-530` so the `timed_out` branch computes `events_appended = max(0, stop_sequence -
   initial_events)` while the non-timed-out branches keep computing `max(0,
   len(fork_view.graph.events) - initial_events)` exactly as before (they have no marker to exclude).
   The simplest correct shape: compute `events_appended` using `stop_sequence` if `timed_out` else
   `len(fork_view.graph.events)`, both minus `initial_events`, in one `max(0, ...)` expression.
2. Update `TrialReport.events_appended`'s docstring (`sandbox/__init__.py:156-165`) to add one
   sentence: on a wall-clock-killed trial, this count excludes the parent's own
   `trial.wall_clock_exhausted` marker event — it reflects only what the child process itself
   produced.

### Refactor

- None structural expected; this is a 2-3 line change. If the `timed_out`/non-`timed_out` branching
  for `events_appended` reads awkwardly inline, extract a 3-line local helper
  (`_events_appended(fork_view, initial_events, stop_sequence, timed_out)`) only if it measurably
  improves readability — don't force it for a change this small.

Targeted gate:

```bash
.venv/bin/python -m pytest -q tests/test_sandbox_trial.py
```

Success: `report.events_appended` for a wall-clock-killed trial equals exactly what the child process
produced, excluding the parent's own instrumentation event; non-timed-out trial reports are
unaffected (same computation as before); the docstring states the exclusion explicitly.

## Phase 8 — 06.4 + 06.5: scenario resolution hardening

Both findings live in the same function pair in `activegraph/sandbox/_child.py` and share one new
test file, since neither `_resolve_scenario` nor `_materialize_pack` currently has any direct
in-process unit coverage — today they're exercised only indirectly, through real subprocess spawns in
`tests/test_sandbox_trial.py`.

**Given (06.4)** `_materialize_pack` (`_child.py:120-169`) gates its file load behind
`verify_bundle_hash(job["expected_bundle_hash"], root)` (`:142`) before importing anything from
`root`,
**when** `_resolve_scenario(root, scenario)` (`_child.py:172-192`) resolves `scenario_path = (root /
path_part).resolve()` (`:179`) from the caller-supplied `scenario` string,
**then** it performs no analogous containment check — a `scenario` value like
`"../outside/evil.py::main"` resolves to a path outside `root` and is imported and executed with
nothing to stop it.

**Given (06.5)** the pack-module load block (`_child.py:146-154`) registers its module in
`sys.modules[spec.name] = module` before `exec_module`, and a real downstream consumer of that
registration exists (`packs/loader.py:412-448`'s `_locate_pack_manifest`, reached via
`Runtime.load_pack` → `_warn_on_manifest_violations`),
**when** the scenario-module load block (`_child.py:180-186`) does the same
`spec_from_file_location`/`module_from_spec`/`exec_module` sequence,
**then** it never assigns `sys.modules[...] = module` — an asymmetry with no comment explaining it,
and out of step with the standard `importlib` idiom (register in `sys.modules` before
`exec_module`, so the module is discoverable by name/`__name__` during its own execution) even though
no current consumer in this repo depends on it for the scenario module specifically.

### Red

1. Create `tests/test_sandbox_child.py` (new file — `_child.py` internals have no direct unit test
   today; all existing coverage goes through a real subprocess in `test_sandbox_trial.py`). Import
   `activegraph.sandbox._child` directly.
2. `test_resolve_scenario_rejects_path_outside_root`: build a `tmp_path` layout with `root =
   tmp_path / "pack"` (containing a harmless dummy file) and a sibling `tmp_path / "outside" /
   "evil.py"` containing a top-level side effect (e.g. writes a sentinel file when imported) plus a
   `def main(rt): ...`. Call `_child._resolve_scenario(root, "../outside/evil.py::main")`. Assert it
   raises `RuntimeError` mentioning containment/escape, and assert the sentinel file was **never**
   created (proving the module was never imported/executed, not just that the returned callable was
   discarded).
3. `test_resolve_scenario_still_resolves_valid_in_root_scenario`: same fixture shape, but with the
   scenario file placed inside `root`; assert `_resolve_scenario(root, "scenario.py::main")` returns
   a callable and calling it runs correctly (regression/characterization for the still-valid case).
4. `test_resolve_scenario_registers_module_in_sys_modules`: call `_resolve_scenario(root,
   "scenario.py::main")` for an in-root scenario; assert `sys.modules["_trial_scenario"]` exists
   after the call and `sys.modules["_trial_scenario"].main` is the same function object
   `_resolve_scenario` returned. Clean up `sys.modules.pop("_trial_scenario", None)` in a `finally`
   so this test doesn't leak state into others.
5. Run all four; confirm the containment test and the `sys.modules` test fail today (no `RuntimeError`
   is raised for the traversal case — the sentinel file *does* get created; `sys.modules` has no
   `"_trial_scenario"` key after a normal resolve).

### Green

1. In `activegraph/sandbox/_child.py`'s `_resolve_scenario` (`:172-192`), after computing
   `scenario_path = (root / path_part).resolve()` (`:179`) and before `importlib.util.spec_from_file_location(...)`,
   add a containment check: `root_resolved = root.resolve()`; if not
   `scenario_path.is_relative_to(root_resolved)`, raise `RuntimeError(f"scenario path
   {scenario_path} escapes pack root {root_resolved}")` — matching the function's existing style of
   raising plain `RuntimeError` for its other validation failures (`:184-185`'s "cannot import
   scenario" and `:189-191`'s "has no callable"), rather than introducing a new custom exception
   class for just this one function.
2. In the same function, add `sys.modules[spec.name] = module` immediately after `module =
   importlib.util.module_from_spec(spec)` (`:183`) and before `spec.loader.exec_module(module)`
   (`:185`) — mirroring the pack-module block's ordering exactly (`_child.py:151-154`). `sys` is
   already imported in this module (used by the pack-module block).

### Refactor

- Both blocks (pack-module load, scenario-module load) now share the
  `spec_from_file_location`/`module_from_spec`/`sys.modules[...] = module`/`exec_module` shape;
  consider (optional) extracting a tiny private helper `_load_module_from_spec(name, path,
  submodule_search_locations=None)` only if it doesn't obscure the one real difference between them
  (the pack block also does `submodule_search_locations=[str(root)]` for package-style relative
  imports). Given there are only two call sites, inlining is also acceptable — don't force the
  extraction.
- Add a one-line comment above the new containment check cross-referencing `_materialize_pack`'s
  `verify_bundle_hash` gate, so a future reader sees both boundary checks are part of the same
  "nothing outside `pack_root` gets imported unverified" invariant.

Targeted gate:

```bash
.venv/bin/python -m pytest -q tests/test_sandbox_child.py tests/test_sandbox_trial.py
```

Success: a scenario path that resolves outside `root` is rejected before any import/exec happens; a
valid in-root scenario still resolves and runs exactly as before; the scenario module is registered
in `sys.modules` the same way the pack module already is; the full subprocess-level trial suite
(`test_sandbox_trial.py`) — which is what actually exercises `_child.py` end-to-end via real child
processes — remains green.

## Phase 9 — 07.4: drop the unreachable guard

**Given** `Runtime.__init__`'s `graph: Graph` parameter is non-`Optional` (`runtime.py:407-409`),
assigned once at `runtime.py:461`, with a repo-wide grep confirming no code path ever sets `rt.graph
= None` or constructs `Runtime(graph=None)`,
**when** `load_pack_into_runtime` (defined at `packs/loader.py:55`; `:257-322` is the tail
mutation/emit section this phase actually touches) reaches its guarded `_install_graph_validators`
call (`:303-304`) and its four-lines-later unguarded `rt.graph.emit(...)` call (`:308-318`),
**then** the guard on the first call is defending against a state the type system already forbids,
while the second call makes the same assumption implicitly and without a matching guard — an
inconsistency in the code, not a live bug (both calls already always execute together in every
reachable path today).

This phase is refactor-classified: removing a provably-dead conditional changes no observable
behavior for any currently-reachable call, so there is no new failing-first test to write. The
existing `tests/test_packs.py` suite (which already asserts `pack.loaded` event emission at `:934`
and validator-installation-adjacent behavior) is the regression gate.

### Red

There is no new Red test for this phase specifically. Confirm the existing regression floor before
changing anything:

```bash
.venv/bin/python -m pytest -q tests/test_packs.py tests/test_diligence_pack.py tests/test_disable_pack.py
```

Record the pass count so Green's identical run can be compared for zero regressions.

### Green

1. In `activegraph/packs/loader.py`, remove the `if rt.graph is not None:` conditional at `:303-304`
   and call `_install_graph_validators(rt.graph, state)` unconditionally, at the same position in the
   mutation sequence (immediately before the `# ---- 6. emit pack.loaded event` comment block).
2. Leave every other line in the `257-322` mutation block untouched — this phase does not add
   rollback/transactional semantics; it only removes one dead conditional, per the locked decision
   and scope boundary above.

### Refactor

- Update the module docstring's "atomic-load guarantee" comment (`loader.py:19-23`) if it references
  the removed guard specifically; otherwise no change needed, since the guarantee's actual scope
  (steps 5-8 run together, uninterrupted by anything raisable in normal operation) is unaffected by
  removing a conditional that was always `True` in practice.

Targeted gate:

```bash
.venv/bin/python -m pytest -q tests/test_packs.py tests/test_diligence_pack.py tests/test_disable_pack.py tests/test_quickstart_snapshot.py tests/test_tool_trace_snapshot.py
```

Success: identical pass count to the pre-change baseline recorded in Red; `_install_graph_validators`
and the `pack.loaded` emit are now one unconditional sequence instead of guard-then-unguarded; no
behavior changes for any currently-reachable `Runtime`/`Pack` construction.

## Phase 10 — 08.5: give the Protocol method a real default body

**Given** `LLMProvider` is a `@runtime_checkable` `Protocol` (`llm/provider.py:145-146`) whose
`supports_native_structured_output` method has a bare `...` body (`:186-199`), and its docstring
frames the runtime's `getattr(..., None)` guard (`runtime.py:1226-1229`) as what makes an
unimplemented override safely resolve to `"prompt"` mode,
**when** a class explicitly subclasses `LLMProvider` (`class Foo(LLMProvider):`) without overriding
`supports_native_structured_output`,
**then** `getattr(foo, "supports_native_structured_output", None)` does **not** hit the `getattr`
default — it finds the inherited `...`-bodied method (a real, callable, inherited method, since
`Foo` explicitly subclasses the Protocol) — and calling it returns `None`. The runtime's `if supports
is None or not supports(b.model):` check still resolves to `"prompt"` mode today, but only because
`not None` happens to be `True`, not through the mechanism the docstring describes. All 5 shipped
providers (`AnthropicProvider`, `OpenAIProvider`, `ClaudeCodeProvider`, `RecordedLLMProvider`,
`RecordingLLMProvider`) already define concrete overrides, so none currently exercise this gap.

### Red

1. In `tests/test_llm_native_structured_output.py`, add a new provider double that does what
   `ScriptedProvider` (the existing duck-typed double in `tests/_llm_helpers.py:27`, confirmed to
   have no `LLMProvider` base class at all) does *not* cover: an explicit `Protocol` subclass. Add
   `class BareProtocolProvider(LLMProvider):` (or extend `ScriptedProvider` to optionally subclass
   it) implementing `complete`, `default_model`, `recognizes_model` but deliberately omitting
   `supports_native_structured_output`.
2. `test_explicit_protocol_subclass_without_override_defaults_to_false`: assert
   `BareProtocolProvider().supports_native_structured_output("any-model")` returns `False` (not
   `None`) when called directly.
3. `test_flag_on_but_protocol_subclass_has_no_override_resolves_prompt`: build a `Runtime` with
   `native_structured_output=True` and `llm_provider=BareProtocolProvider()`, call
   `rt._resolve_structured_output_mode(behavior_with_model)`, and assert it returns `"prompt"` — same
   observable outcome as today, now for the right reason.
4. Run both; confirm the first fails today (`supports_native_structured_output` returns `None`, not
   `False`) while the second already passes today (since `not None` also resolves to `"prompt"`) —
   this second assertion is characterization, locking that the fix doesn't change the outward
   resolved mode, only the intermediate return value's honesty.

### Green

1. In `activegraph/llm/provider.py`, change `supports_native_structured_output`'s body (`:199`) from
   `...` to `return False`.
2. Update the method's docstring (`:186-198`) to state the explicit default directly: "The base
   Protocol method returns `False`: an explicit subclass that omits the override safely resolves to
   prompt-mode without relying on `getattr`'s absent-attribute fallback." Keep the existing
   explanation of the `getattr`-based duck-typing path for non-subclassing providers, since that path
   is still real and still matters for callers that don't subclass `LLMProvider` at all.

### Refactor

- Confirm none of the 5 shipped providers' existing overrides need any change — they all already
  define concrete bodies, so a Protocol-level default has no effect on them (grep `def
  supports_native_structured_output` across `activegraph/llm/*.py` to re-confirm the 5 override
  sites are untouched by this diff).
- No change needed to `runtime.py`'s `getattr(..., None)` guard itself — it remains correct and
  still protects true duck-typed (non-subclassing) providers that don't define the attribute at all.

Targeted gate:

```bash
.venv/bin/python -m pytest -q tests/test_llm_native_structured_output.py tests/test_llm_provider.py tests/test_llm_anthropic.py tests/test_llm_openai.py tests/test_llm_claude_code.py tests/test_llm_recording.py
```

Success: an explicit `LLMProvider` Protocol subclass that omits the override now gets an honest
`False` return, matching the documented contract; the runtime's resolved mode (`"prompt"`) is
unchanged for every existing and new provider shape; all 5 shipped providers' behavior is untouched.

## Phase 11 — 10.8 + 10.9: quickstart CLI hygiene

Both findings are in `activegraph/cli/quickstart.py`; bundled into one phase since they touch
adjacent, small regions of the same file.

**Given (10.8)** `quickstart.py` imports `os`, `shutil`, and `tempfile` (`:25-28`) but a
word-boundary grep for `\bos\.`, `\bshutil\.`, `\btempfile\.` across the whole file returns zero
matches — all filesystem work goes through `pathlib.Path` — and `_QUICKSTART_DB_DIR =
"/tmp/activegraph_quickstart"` (`:47`) hardcodes a POSIX-only path even though `tempfile` (imported,
unused) exists specifically to provide a portable equivalent,
**when** `quickstart` runs on a platform where `/tmp` isn't the conventional temp directory,
**then** the hardcoded path silently assumes POSIX layout instead of asking the OS.

**Given (10.9)** the interactive scaffold's behavior name `"growth_flagger"` appears independently at
three places in the same file — the scaffold's `name=` kwarg (`:231`), the scaffold's `def` line
(`:235`), both inside the `_INTERACTIVE_SCAFFOLD` string template written to the developer's file —
and the fire-counter's match string (`:436`),
**when** any one of these three literals is edited without the other two,
**then** the scaffold and the counter silently disagree — the counter's own comment already
acknowledges this ("If the user renamed it, this returns 0" — `:429-432`) as a known rc1 limitation,
but nothing enforces the three literals staying in sync even absent a user rename.

### Red

1. In `tests/test_quickstart.py`, add `test_quickstart_imports_no_dead_fs_modules`: read
   `activegraph/cli/quickstart.py`'s source and assert (via `ast.parse`, checking `Import` nodes) that
   neither `os` nor `shutil` is imported. Run it; confirm it fails today (both are imported).
2. Add `test_quickstart_db_dir_is_not_hardcoded_tmp`: assert `_QUICKSTART_DB_DIR` does not literally
   equal `"/tmp/activegraph_quickstart"` and instead starts with `tempfile.gettempdir()`. Run it;
   confirm it fails today.
3. Add `test_quickstart_scaffold_name_and_counter_share_one_constant`: monkeypatch a module-level
   `_SCAFFOLD_BEHAVIOR_NAME` constant (introduced in Green) to a different value (e.g.
   `"renamed_flagger"`), re-render `_INTERACTIVE_SCAFFOLD` (or call whatever function builds it), and
   assert both the rendered scaffold text contains the new name in both the `name=` kwarg and the
   `def` line, *and* the fire-counting function's match string uses the same patched constant —
   proving single-source-of-truth rather than three independently-typed literals that happen to
   agree. This test necessarily targets Green's new constant, so write it expecting an `AttributeError`
   (constant doesn't exist yet) as the current Red failure.
4. Run all three; confirm each fails for the stated reason.

### Green

1. In `activegraph/cli/quickstart.py`, remove the `import os` and `import shutil` lines (`:25-26`
   region) — confirmed zero uses anywhere in the file.
2. Change `_QUICKSTART_DB_DIR = "/tmp/activegraph_quickstart"` (`:47`) to `_QUICKSTART_DB_DIR =
   str(Path(tempfile.gettempdir()) / "activegraph_quickstart")`, making the `tempfile` import a real,
   used dependency instead of a third dead one. Leave `_QUICKSTART_DB_PATH`, the `mkdir` call at
   `:96`, the `unlink` call at `:102`, and the second `mkdir` site at `:258` untouched — they already
   consume `_QUICKSTART_DB_DIR` via `Path(...)`, so this is a pure constant-value change with no
   call-site edits needed. Add a one-line comment noting the shared/fixed filename remains
   intentional (single demo, no litter) even though the directory is now portable.
3. Add `_SCAFFOLD_BEHAVIOR_NAME = "growth_flagger"` as a module-level constant near
   `_INTERACTIVE_SCAFFOLD`. Change `_INTERACTIVE_SCAFFOLD` from a static triple-quoted string to an
   f-string (or a `.format()`/`%`-templated string) that interpolates `_SCAFFOLD_BEHAVIOR_NAME` into
   both the `name=` kwarg position and the `def` line. Change the fire-counter's match at `:436` from
   the literal `"growth_flagger"` to `_SCAFFOLD_BEHAVIOR_NAME`. Update the counter's existing comment
   (`:428-432`) to reference the shared constant by name instead of restating the literal.

### Refactor

- Re-run `tests/test_quickstart_snapshot.py` and check whether
  `tests/snapshots/quickstart_fixture.txt` needs regeneration — on Linux CI, `tempfile.gettempdir()`
  resolves to `/tmp` by default, so the printed output (if the DB path ever appears in
  user-visible text) should stay byte-identical; if the snapshot does change, review the diff is
  exactly the expected path-construction change and nothing else before accepting it.
- Confirm `_prepare_interactive_subdir`'s separate cwd-relative `_INTERACTIVE_SUBDIR` path
  (`:258`) is unrelated to this fix and stays untouched.

Targeted gate:

```bash
.venv/bin/python -m pytest -q tests/test_quickstart.py tests/test_quickstart_snapshot.py tests/test_wheel_completeness.py
```

Success: `os`/`shutil` are gone; `tempfile` is a real, used import; the DB directory is portable
instead of POSIX-hardcoded while keeping its single-shared-file demo-hygiene design; the scaffold
name and the fire-counter's match string are driven by one constant instead of three independently
agreeing literals.

## Phase 12 — 10.12: explain and tighten the `llm.requested` special case

**Given** `_FORMATTERS` (`trace/printer.py:398-422`) has 23 entries including `"llm.requested":
_fmt_llm_requested` (`:414`), and `format_event` (`:425-429`) checks `if event.type ==
"llm.requested":` and returns `_fmt_llm_requested(event, hide_prompt_normalized=...)` **before**
ever reaching the table lookup,
**when** an `llm.requested` event is formatted,
**then** the table entry at `:414` is provably unreachable through `format_event`'s own dispatch —
every other event type reaches its formatter through the table; this one alone is special-cased
ahead of it, with no comment explaining why the entry is kept anyway.

This phase makes no observable behavior change (the locked decision above explains why a generic
per-formatter options mechanism is out of scope for one caller). There is no new pytest Red/Green
cycle; the existing snapshot suites (`test_llm_trace_snapshot.py`, `test_replay_trace_snapshot.py`,
`test_tool_trace_snapshot.py`) are the regression gate, run unchanged before and after.

### Red

Confirm the regression floor before changing anything:

```bash
.venv/bin/python -m pytest -q tests/test_llm_trace_snapshot.py tests/test_replay_trace_snapshot.py tests/test_tool_trace_snapshot.py tests/test_trace.py tests/test_trace_accessors.py
```

### Green

1. In `activegraph/trace/printer.py`, add a comment directly above the `if event.type ==
   "llm.requested":` branch in `format_event` (`:426`) explaining why the special case exists:
   `_fmt_llm_requested` is the only formatter in `_FORMATTERS` that needs an extra render-time
   option (`hide_prompt_normalized`, threaded from the trace facade's rollup feature), and no other
   formatter needs this today, so a generic per-formatter options mechanism isn't justified.
2. Add a companion comment directly above the `"llm.requested": _fmt_llm_requested` table entry
   (`:414`) noting it is not reached through `format_event`'s dispatch (the special case above always
   wins first) but is kept so anything that introspects `_FORMATTERS.keys()` for the set of known/
   formatted event types still sees `"llm.requested"` listed.
3. No functional line changes — this phase is comments-only.

### Refactor

None — the finding's own resolution, per the locked decision, is documentation clarity rather than a
mechanism change.

Targeted gate:

```bash
.venv/bin/python -m pytest -q tests/test_llm_trace_snapshot.py tests/test_replay_trace_snapshot.py tests/test_tool_trace_snapshot.py tests/test_trace.py tests/test_trace_accessors.py
```

Success: identical pass count to Red; a future reader of `format_event` and `_FORMATTERS` no longer
has to independently discover *why* `llm.requested` is both table-registered and special-cased —
the comments say so at both sites.

## Descoped findings — no source change

Four of the 19 findings get no source change in this plan. Each was independently re-verified
against current source (not merely trusted from the original 2026-08-11 audit or the research
document's summary) before being descoped here.

### 02.5 — llm-cache double-write (already fixed)

The original finding described a conditional-expression-as-statement immediately followed by a
redundant `if self._llm_cache is None:` block at the old `runtime.py:1889-1896`. That shape no longer
exists anywhere in `runtime.py`. The current write path (`runtime.py:2360-2368`) is a single,
clean `if cached is None: ... self._llm_cache.record(...)` sequence, and a repo-wide grep for
`_llm_cache.record` returns exactly one call site. This was closed incidentally by one of the 5
already-merged repair sets; no set targeted it directly. **No action.**

### 07.10 — `packs → llm` diagram edge caveat (already fixed)

`specs/00-overview.md:280-282` now explicitly states the `packs → llm` edge is
"example-pack-only," with the exact citation the finding asked for
(`packs/diligence/fixtures/__init__.py:19,205`). The research pass additionally confirmed the newer
`packs/repair/` pack (added by one of the 5 merged sets, after the original audit) does **not**
introduce a second `packs → llm` edge — its only `activegraph.llm`-shaped text lives inside a module
docstring and inside string literals used as finding-description data, not real imports. **No
action.**

### 08.9 — `llm → core` diagram edge omission (already fixed)

`specs/00-overview.md` now has a `shared` node (line 50) and an explicit `llm --> shared` edge (line
105) alongside the existing `llm --> core` edge (line 104), covering `llm`'s real dependency on
`frame.py`/`errors.py` (`llm/prompt.py:45`, `llm/errors.py:30`). There's no physical
`activegraph/shared/` directory — `shared` is a documented logical grouping, which is what the
finding asked the diagram to show. **No action.**

### 10.11 — `DOCS_BASE_URL` renders a 404 (external infrastructure dependency, not an in-repo defect)

`activegraph/errors.py:43-49`'s comment explicitly states this is `CONTRACT v1.0 #C6`, gated on
"user-owned Pages enablement and DNS" landing — infrastructure entirely outside this git repository.
This is not an oversight: `CONTRACT.md`'s v1.1 #9 deploy-verification gate entry (confirmed present)
tracks exactly this, and `tests/test_doc_site_reachable.py` (a `@pytest.mark.slow`-marked test) is
already designed to stay red against the live site until the DNS/Pages work lands — its own module
docstring states this outright. A separate `tests/test_doc_links.py` checks the same URLs
source-tree-only (no network) and stays green today. There is no code change inside `activegraph/`
that can close this finding — changing the constant before the domain is actually live would make
every error message point at a 404 with more confidence, not less. **No action; already tracked
externally.** If the owner wants this finding formally closed rather than perpetually descoped, the
action item is "confirm DNS/Pages landed, then flip `test_doc_site_reachable.py`'s marker" — an
infrastructure task, not a code change, and out of scope for a TDD implementation plan.

## Phase 13 — integrated regression gate

### Red

None — this phase runs the accumulated suite, it doesn't add new tests.

### Green

None.

### Refactor

Run the full targeted set from every phase above, then the complete suite, and compare against the
Phase 0 baseline:

```bash
.venv/bin/python -m pytest -q \
  tests/test_patch.py \
  tests/test_graph.py \
  tests/test_ids.py \
  tests/test_errors_format.py \
  tests/test_graph_store.py \
  tests/test_falkordb_store.py \
  tests/test_postgres_store.py \
  tests/test_internal_seam_hygiene.py \
  tests/test_conformance_modules_are_pytest_independent.py \
  tests/test_trial_executor.py \
  tests/test_store_conformance.py \
  tests/test_event_sinks.py \
  tests/test_sandbox_trial.py \
  tests/test_sandbox_child.py \
  tests/test_packs.py \
  tests/test_diligence_pack.py \
  tests/test_disable_pack.py \
  tests/test_llm_native_structured_output.py \
  tests/test_llm_provider.py \
  tests/test_llm_anthropic.py \
  tests/test_llm_openai.py \
  tests/test_llm_claude_code.py \
  tests/test_llm_recording.py \
  tests/test_quickstart.py \
  tests/test_quickstart_snapshot.py \
  tests/test_wheel_completeness.py \
  tests/test_llm_trace_snapshot.py \
  tests/test_replay_trace_snapshot.py \
  tests/test_tool_trace_snapshot.py \
  tests/test_trace.py \
  tests/test_trace_accessors.py \
  tests/test_doc_links.py

.venv/bin/python -m pytest -q
```

Expected: the full-suite run reproduces `1732 + <new tests added across phases 1-12> passed, 59
skipped, 1 failed` — the same single pre-existing `test_llm_claude_code_install.py` failure noted in
Baseline, and nothing else. If any other test newly fails, bisect by phase (each phase's targeted
gate above is the bisection unit) before considering this plan complete.

Update documentation in the same behavior-sized commits as their corresponding phase, not batched at
the end:

- `docs/reference/errors/invalid-patch-operation-error.md` (new, Phase 1) and the patch lifecycle
  cross-link;
- `specs/01-core.md` patch-op taxonomy correction (Phase 1);
- `CONTRACT.md`'s core/runtime error-taxonomy exception clause (Phase 3);
- `GraphStore` backend-author interface reference (Phase 4);
- one changelog/migration entry per phase that changes observable behavior (Phases 1, 4, 8, 10, 11
  — the phases with a real Red/Green cycle above; Phases 3, 5, 9, 12 are documentation/consistency-only
  and don't need a compatibility note since nothing observable changed).

## Expected file touch map

| Finding(s) | Production files | Primary tests |
|---|---|---|
| 01.1 + 01.2 | `activegraph/core/patch.py`, `activegraph/core/graph.py`, `activegraph/runtime/exec_errors.py`, `activegraph/__init__.py` | `test_patch.py`, `test_graph.py`, `test_errors_format.py` |
| 01.3 | `activegraph/core/graph.py`, `activegraph/core/ids.py` | `test_graph.py`, `test_ids.py`, `test_patch.py` |
| 01.6 | `CONTRACT.md`, `activegraph/runtime/exec_errors.py` (docstring only), `specs/01-core.md` | none (docs-only; `test_errors_format.py`/`test_graph.py` as regression) |
| 01.9 | `activegraph/core/graph_store.py` | `test_graph_store.py`, `test_falkordb_store.py`, `test_postgres_store.py`, `test_store_conformance.py` |
| 01.10 | `activegraph/packs/loader.py` | `test_internal_seam_hygiene.py` (new), `test_packs.py`, `test_disable_pack.py` |
| 06.1 | `activegraph/sandbox/conformance.py`, `activegraph/store/conformance.py`, `activegraph/sinks/conformance.py` | `test_conformance_modules_are_pytest_independent.py` (new), `test_trial_executor.py`, `test_store_conformance.py`, `test_falkordb_store.py`, `test_postgres_store.py`, `test_event_sinks.py`, `test_sandbox_trial.py` |
| 06.3 | `activegraph/sandbox/__init__.py` | `test_sandbox_trial.py` |
| 06.4 + 06.5 | `activegraph/sandbox/_child.py` | `test_sandbox_child.py` (new), `test_sandbox_trial.py` |
| 07.4 | `activegraph/packs/loader.py` | `test_packs.py`, `test_diligence_pack.py`, `test_disable_pack.py` |
| 08.5 | `activegraph/llm/provider.py` | `test_llm_native_structured_output.py`, `test_llm_provider.py`, provider-specific suites |
| 10.8 + 10.9 | `activegraph/cli/quickstart.py` | `test_quickstart.py`, `test_quickstart_snapshot.py`, `test_wheel_completeness.py` |
| 10.12 | `activegraph/trace/printer.py` (comments only) | `test_llm_trace_snapshot.py`, `test_replay_trace_snapshot.py`, `test_tool_trace_snapshot.py` |
| 02.5, 07.10, 08.9, 10.11 | none | none — descoped, see above |

## Commit/checkpoint sequence for implementation

Keep behavior-sized commits so any regression can be bisected to one phase:

1. Patch operation taxonomy: `PATCH_OPS` narrowing, `InvalidPatchOperationError`, `propose_patch`
   validation (01.1 + 01.2).
2. ID generation inside the emit lock (01.3).
3. Core/runtime error-taxonomy documentation (01.6) — docs-only commit, no source diff under
   `activegraph/`.
4. `GraphStore.remove_patch` promoted to `@abstractmethod` (01.9).
5. Seventh seam's suppression-comment fix (01.10).
6. Drop the `pytest` hard dependency from the three conformance suites (06.1).
7. Fix the wall-clock trial's `events_appended` inflation (06.3).
8. Scenario resolution containment check + `sys.modules` symmetry, plus new `_child.py` direct unit
   tests (06.4 + 06.5).
9. Remove the dead guard in `load_pack_into_runtime` (07.4).
10. `LLMProvider.supports_native_structured_output` explicit default (08.5).
11. Quickstart portability + dead-import cleanup + scaffold/counter single-source-of-truth (10.8 +
    10.9).
12. `format_event`/`_FORMATTERS` explanatory comments (10.12) — comments-only commit.
13. Integrated regression gate, changelog entries, full-suite verification.

Do not combine the 4 descoped findings' documentation (already-current in `specs/00-overview.md` and
`CONTRACT.md`) into any of the above commits — they require no edits, so there is nothing to commit
for them. If a future reviewer wants a paper trail that they were considered, this plan document and
its `related_beads: [AF-w18w]` link are that record.
