## Plan Review Report: Set 6 — the 19 Remaining Findings from the 2026-08-11 Master Audit [REVIEW]

Plan reviewed: `thoughts/searchable/shared/plans/2026-08-13-set6-remaining-findings-tdd-plan.md`

Tracking issue: `AF-w18w`

Live-code baseline: `ad6a05cdc65ca815b46f59ffeea301398935d996` on `repair-impl-integration` (current `HEAD` matches the plan's recorded baseline exactly). All 19 findings' file:line citations were independently re-verified against this `HEAD` by six parallel, read-only codebase-analyzer passes — one per descoped-findings cluster and one per 2-3-phase cluster — not accepted from the plan's prose.

This review was conducted cold: no research document, no prior review, and no context beyond the plan text itself and direct reads of current source.

### Review Summary

| Category | Status | Issues Found |
|---|---|---|
| Coverage completeness (all 19 IDs) | ✅ | 0 — all 19 present, none silently dropped |
| Descoped-finding reasoning (4 findings) | ✅ | 0 — all 4 independently re-confirmed against source |
| Contracts | ✅ | 0 critical |
| Interfaces | ✅ | 0 critical, 1 warning |
| Promises / backward compatibility | ✅ | 0 critical — breaking changes are explicitly called out |
| Data models | ✅ | 0 critical |
| Workflow closure | ❌ | 1 critical (Phase 2 scope), 3 warning (citation drift) |
| Test-spec quality | ❌ | 1 critical (Phase 2 Red coverage), 2 warning |

**Overall: Needs Minor Revision.** This is a strong plan. The coverage checklist is accurate — all 19 `AF-w18w` findings are accounted for, and none of the four "already fixed" descopes rest on stale or incorrect reasoning; every one independently re-checks out against current source. The one substantive problem is localized to a single phase: Phase 2 (finding 01.3, ID generation inside the `_emit_lock` scope) hardcodes a fix for only 2 of the ~8 `Graph` sugar methods that share the exact defect shape it targets, and the plan's own contingency instruction for catching the rest names an incomplete candidate list. Everything else — including the four descopes the task briefing specifically flagged for scrutiny — holds up.

---

## Coverage Checklist Verification (explicitly required)

The plan carries its own checklist mapping all 19 `AF-w18w` finding IDs to a disposition. I cross-checked that checklist against the required list of 19 IDs and against the phase sections it points to.

| # | Finding | Plan's disposition | Points to | Verified present? |
|---|---|---|---|---|
| 01.1 | `PATCH_OPS` declares 4 ops; projector implements 2 | Fixed | Phase 1 | ✅ Phase 1 exists, has Red/Green/Refactor |
| 01.2 | `PATCH_OPS` dead code, unvalidated | Fixed | Phase 1 | ✅ bundled with 01.1, same root cause |
| 01.3 | `IDGen` "not thread-safe" doc vs. ID gen outside `RLock` | Fixed | Phase 2 | ✅ present, but **scope gap found — see Critical Issue 1** |
| 01.6 | `core ↔ runtime` error-class naming/location coupling | Fixed (docs-only) | Phase 3 | ✅ present, no source diff (correctly labeled docs-only) |
| 01.9 | `GraphStore.clear()` depends on non-abstract `remove_patch()` | Fixed | Phase 4 | ✅ present |
| 01.10 | Underscore-prefixed methods documented public seams | Fixed (consistency-only) | Phase 5 | ✅ present |
| 02.5 | Redundant llm-cache double-write | Descoped: already fixed | Descoped section | ✅ present, reasoning re-verified — see below |
| 06.1 | `sandbox/conformance.py` hard-imports `pytest` | Fixed | Phase 6 | ✅ present |
| 06.3 | Wall-clock-killed trial's `events_appended` inflated | Fixed | Phase 7 | ✅ present |
| 06.4 | `_resolve_scenario` has no containment check | Fixed | Phase 8 | ✅ present, bundled with 06.5 |
| 06.5 | Pack module registered in `sys.modules`; scenario isn't | Fixed | Phase 8 | ✅ present |
| 07.4 | `rt.graph.emit(...)` unguarded after guarded call | Fixed (simplification) | Phase 9 | ✅ present |
| 07.10 | `packs → llm` diagram edge needed caveat | Descoped: already fixed | Descoped section | ✅ present, reasoning re-verified — see below |
| 08.5 | `supports_native_structured_output` guard doesn't work for `Protocol` subclasses | Fixed | Phase 10 | ✅ present |
| 08.9 | `llm → core` diagram edge omitted `frame`/`errors` dep | Descoped: already fixed | Descoped section | ✅ present, reasoning re-verified — see below |
| 10.8 | Quickstart hardcodes `/tmp` path; 3 unused imports | Fixed | Phase 11 | ✅ present, bundled with 10.9 |
| 10.9 | Quickstart fire counter name-coupled | Fixed | Phase 11 | ✅ present |
| 10.11 | `DOCS_BASE_URL` comment says it knowingly 404s | Descoped: external infra | Descoped section | ✅ present, reasoning re-verified — see below |
| 10.12 | `format_event` special-cases `llm.requested` outside table | Fixed (docs + cleanup) | Phase 12 | ✅ present |

**Result: all 19 required IDs are present and accounted for. None are silently missing.** 15 get a source change across 12 numbered phases; 4 are explicitly descoped with stated reasoning. This matches the plan's own summary count exactly, and my independent tally of the checklist table (not just the plan's stated count) confirms 19/19.

---

## Descoped-Finding Deep Verification (explicitly required scrutiny)

The task briefing specifically asked that descoped findings be checked against actual source, not trusted from the plan's claims, since a wrong-reasoning descope is the same failure mode as a silently dropped finding. All four were independently re-verified by a dedicated codebase-analyzer pass with no access to the plan's own summary text (it was given only the specific citations to check).

### 02.5 — llm-cache double-write: descope CONFIRMED correct
The plan's claim is that the old double-write shape at the historical `runtime.py:1889-1896` no longer exists, and the current write path (`runtime.py:2360-2368`) is a single clean sequence. Independently verified: `activegraph/runtime/runtime.py:2361-2368` is exactly `if cached is None: → if self._llm_cache is None: self._llm_cache = LLMCache() → self._llm_cache.record(...)`. A repo-wide grep for `_llm_cache.record` returns exactly one production call site. **No discrepancy.** The descope reasoning holds.

### 07.10 — `packs → llm` diagram edge caveat: descope CONFIRMED correct
`specs/00-overview.md:280-282` does state the edge is "example-pack-only" and does cite `packs/diligence/fixtures/__init__.py:19,205` — both lines independently confirmed to be real `from activegraph.llm...` imports. The plan's additional claim — that the newer `packs/repair/` pack (added after the original audit) does *not* introduce a second real edge — was also independently checked: `packs/repair/` exists, and all 8 `activegraph.llm`-shaped grep hits inside it are either inside the module docstring's usage example or inside string-literal finding-description data (`BATCH2_FINDINGS`), not real top-level imports. **No discrepancy.** This is the finding the task briefing was most worried about (a descope whose "already fixed by newer work" reasoning turns out wrong once new code is added) — it was specifically re-checked against the newer pack and holds up.

### 08.9 — `llm → core` diagram edge omission: descope CONFIRMED correct
`specs/00-overview.md` has a `shared` node at line 50, an `llm --> core` edge at line 104, and an `llm --> shared` edge at line 105 — all exact-line confirmed. `llm/prompt.py:45` and `llm/errors.py:30` were independently confirmed to import `Frame` and `errors`-module symbols respectively, substantiating the dependency the diagram now shows. No physical `activegraph/shared/` directory exists, consistent with the plan's "documented logical grouping" characterization. **No discrepancy.**

### 10.11 — `DOCS_BASE_URL` 404: descope CONFIRMED correct, one wording nuance
`activegraph/errors.py:43-49`'s comment does cite CONTRACT v1.0 #C6 and the DNS/Pages gating. `CONTRACT.md:6472` does have a "v1.1 #9. Deploy-verification CI gate" entry describing the same root cause. `tests/test_doc_site_reachable.py` exists and is marked `@pytest.mark.slow` at line 78. `tests/test_doc_links.py` exists and is confirmed network-free (no `urllib`/`socket` imports). One minor overstatement: the plan says the reachable-test's docstring "states this outright" that it's designed to stay red — the actual docstring says `"(green once Pages is enabled and DNS resolves)"`, which implies but doesn't literally assert the current-red state. This is a paraphrase-precision nit, not a reasoning error — the underlying claim (this is intentionally red until external infra lands, and is already tracked in `CONTRACT.md`) is accurate. **No discrepancy that affects the descope decision.**

**Verdict: all four descopes are reasoned correctly against current source.** This is a materially different outcome than the failure mode the task was worried about — none of the 19 findings were silently dropped, and none of the 4 descopes rest on stale/incorrect claims.

---

## Critical Issues

### 1. Phase 2 (01.3) hardcodes a fix for 2 of ~8 methods sharing the exact defect shape, and its own contingency check names an incomplete candidate list

The finding is that `Graph` sugar methods generate IDs via `self.ids.*()` *before* entering the `with self._emit_lock:` scope that `emit()` opens, even though `IDGen` is documented as relying on being called from inside that serialized boundary. Phase 2's Red section only writes characterization tests for `add_object` and `propose_patch`, and Green only wraps those same two methods in `with self._emit_lock:`.

Phase 2's own Red step 4 anticipates this might be incomplete: *"Grep `activegraph/core/graph.py` for every other `self.ids\.` call site... to confirm `add_object` and `propose_patch` are the only two sugar methods with this shape before deciding Green's scope is complete — if `add_relation`, `apply_patch`, or `reject_patch` also call `self.ids.*()` before their own `self.emit(...)`, add the same characterization test and fix for each one found."*

I ran that exact grep independently. The full match list for `self\.ids\.` in `activegraph/core/graph.py` is:

```
176:  self.run_id: str = run_id or self.ids.run()        (Graph.__init__ — not a mutation, exempt)
654:  obj_id = self.ids.object(type)                      (add_object)      — plan covers
683:  id=self.ids.event(),                                (add_object)      — plan covers
707:  rel_id = self.ids.relation()                         (add_relation)    — NOT in plan's candidate list
742:  id=self.ids.event(),                                (add_relation)    — NOT in plan's candidate list
764:  id=self.ids.event(),                                (remove_relation) — NOT in plan's candidate list
785:  id=self.ids.event(),                                (remove_object)   — NOT in plan's candidate list
818:  id=self.ids.patch(),                                (patch_object)    — NOT in plan's candidate list
838:  id=self.ids.event(),                                (patch_object)    — NOT in plan's candidate list
875:  id=self.ids.patch(),                                (propose_patch)   — plan covers
894:  id=self.ids.event(),                                (propose_patch)   — plan covers
935:  id=self.ids.event(),                                (apply_patch)     — plan's candidate list names this
977:  id=self.ids.event(),                                (reject_patch)    — plan's candidate list names this
```

Every one of `add_relation`, `remove_relation`, `remove_object`, `patch_object`, `apply_patch`, and `reject_patch` generates an ID via `self.ids.*()` before its own `self.emit(...)` call — the identical shape Phase 2 exists to fix. That's **6 methods**, not the 2 the plan's Green section hardcodes fixes for. Worse: the plan's own suggested candidate list for the audit only names `add_relation`, `apply_patch`, and `reject_patch` — it's missing `remove_relation`, `remove_object`, and `patch_object` entirely. An implementer who runs exactly the grep the plan tells them to run, sees `remove_relation`/`remove_object`/`patch_object` show up, and has no explicit plan instruction telling them these are in-scope too (though the general instruction — "if [any of the named ones] also call `self.ids.*()`... add the same fix for each one found" — is worded generally enough that a careful implementer would still catch them by analogy). But the plan's baseline section states *"All file:line citations in this plan were independently re-verified by direct reads during planning... this plan's author re-read the specific lines the decision hinges on before locking the fix."* Given that framing, this specific grep — one line, directly gating Green's scope for finding 01.3 — should have already been run during planning, and Phase 2's Red/Green should already contain characterization tests and fixes for all 6 additional methods, not defer discovery of 3 of them to implementation time via an incomplete candidate list.

**Impact:** as currently written, an implementer who does *not* run Red step 4's audit (treating it as optional, since it's phrased as a step inside "Red" rather than a blocking gate) ships Phase 2 having fixed only `add_object`/`propose_patch`, leaving `add_relation`, `remove_relation`, `remove_object`, `patch_object`, `apply_patch`, and `reject_patch` with ID generation still outside the lock — i.e., finding 01.3 is only ~25% actually fixed (2 of 8 total sugar methods with the shape), even though the plan's checklist marks it "Fixed" with no qualification.

**Suggested fix:** expand Phase 2's Red and Green sections to explicitly cover all 6 additional methods now (matching the same test-per-method / wrap-per-method pattern already used for `add_object`/`propose_patch`), rather than leaving them to a conditional implementation-time grep. If the plan's author intends to keep the audit-then-expand structure, at minimum correct the named candidate list in Red step 4 to include `remove_relation`, `remove_object`, and `patch_object` so a literal reading of the plan doesn't miss them.

---

## Citation Accuracy — Warnings (do not affect correctness of the proposed fixes)

All six verification passes confirmed the *substance* of every phase's citations — the described bugs are real, the described fix approach is sound, and the described current behavior matches actual source. A handful of individual line numbers drifted by a few lines from what's cited, which is worth flagging since the plan explicitly claims rigorous re-verification, but none of these affect whether the fix is correct — an implementer will locate the right code by content/context regardless.

- **Phase 6 (06.1):** `store/conformance.py`'s `with pytest.raises(DuplicateEventError) as excinfo:` statement is at line **166**, not within the cited "lines 171-177" range (171-177 correctly contains the follow-on assertions, just not the opening `with` statement the citation implies). All other Phase 6 citations (module line counts, `import pytest` line numbers, the second `pytest.raises(Exception)` at line 279, and the "pytest usage is limited to exactly one/two `raises` calls per file" claim) are exact.
- **Phase 7 (06.3):** the `timed_out` branch's `stop_sequence = len(fork_view.graph.events)` line is at **510**, not "~517"; the marker `emit()` block is at **511-527**, not "~518-530"; the `events_appended` computation is at **528-530**, not "~531-533" — a consistent few-line offset across all three citations in the same function. The causal chain itself (stop_sequence captured before the marker emit; events_appended recomputed via a fresh `len()` call after the marker was appended, only on the `timed_out` branch) is confirmed exactly as described — this is a real, precisely-diagnosed bug, just with drifted line numbers.
- **Phase 9 (07.4):** `load_pack_into_runtime` is cited as `packs/loader.py:257-322`, but the function's actual `def` is at line **55** — 257-322 is the function's tail (mutation/emit/manifest-warning steps), which does correctly contain the two lines the phase is actually about (guarded call at 303-304, unguarded call at 308-318). Likely intentional shorthand for "the relevant section," but worth being precise about since a reader might assume the whole function is 66 lines when it's actually ~268.
- **Phase 11 (10.8+10.9):** two small mislabels — line 96 is cited as one of "the unlink calls," but it's actually the `mkdir` call (`Path(_QUICKSTART_DB_DIR).mkdir(...)`); the only real `unlink` call is at line 102. Separately, line 258 is called "a third `mkdir` site" but is actually the *second* of only two `mkdir` sites in the file (96 and 258). Neither mislabel changes what Green actually asks the implementer to do (leave both sites untouched since they already consume `_QUICKSTART_DB_DIR`/`_INTERACTIVE_SUBDIR` via `Path(...)`), so there's no functional impact — just imprecise labels.

---

## Finding-by-Finding Notes

### Phase 1 — 01.1 + 01.2 (patch operation taxonomy)
✅ Fully confirmed. `PATCH_OPS` at `patch.py:17`, the docstring at `patch.py:22-28`, and the projector's `patch.applied` branch at `graph.py:1085-1096` all match exactly — including the precise mechanism the finding describes: for any `op` outside `{"update","replace"}`, neither branch fires, `obj.data` is untouched, but `obj.version += 1` and `put_object` still run unconditionally. The locked decision (narrow the taxonomy rather than implement `create`/`remove` semantics) is well-supported: `Graph.add_object`/`remove_object` are confirmed as the real, already-implemented paths for those operations. One trivial wording nit: the plan calls the pre-validation-check anchor point "the docstring-noted normalization," but `propose_patch` has no docstring — the normalization is preceded by a plain comment. Doesn't affect the fix.

### Phase 2 — 01.3 (ID generation inside emit-lock scope)
See Critical Issue 1 above. All individual citations (the `RLock` doc at `graph.py:191-196`, `emit()`'s lock acquisition at `:586`, the 11 direct `with self._emit_lock:` sites, `IDGen`'s docstring at `ids.py:42`) are exact. The defect mechanism is real. The problem is scope, not accuracy.

### Phase 3 — 01.6 (core-raises-execution-errors documentation)
✅ Fully confirmed, including the specific claim that this is an *established* pattern, not a one-off: all five named raise sites (`ReservedFieldError` at `:133-135`, `InvalidPatchLifecycleState` at `:919-920`, `InternalEvaluatorError` at `:1164,1185`, `ObjectNotFoundError` at `:811`, `ApplyPatchNotFoundError` at `:915`) use local (function-scope) imports from `runtime.exec_errors`, confirmed by checking `graph.py`'s module-level import block does *not* import any of these five names at module scope. This is a docs-only phase and correctly labeled as such.

### Phase 4 — 01.9 (promote `remove_patch` to `@abstractmethod`)
✅ Fully confirmed, including the specific claim that exactly two concrete `GraphStore` subclasses exist repo-wide (`InMemoryGraphStore`, `FalkorDBGraphStore`) and both already override `remove_patch`/`clear`. Independently confirmed `tests/test_postgres_store.py` subclasses `EventStoreConformance`, not `GraphStore` — so it's correctly excluded from this finding's blast radius, and the plan's "exactly two" claim holds even accounting for the Postgres backend's existence.

### Phase 5 — 01.10 (seventh seam suppression comment)
✅ Fully confirmed. `loader.py:925-926` does use `# type: ignore[attr-defined]` where six-plus sibling sites use `# noqa: SLF001`; `graph.py:217-218` does declare both attributes as real (`self._pack_object_validator = None`), confirming the mypy suppression is the wrong tool for this site. The plan's "6+" count is conservative — the actual repo-wide `noqa: SLF001` count is much higher (~45, including many unrelated internal-counter suppressions), but all six specifically-named sibling methods are confirmed present with that suppression.

### Phase 6 — 06.1 (drop pytest hard dependency)
✅ Fully confirmed, including the precise claim that `graph_conformance.py` (492 lines) has zero `pytest` usage while the three target files import it solely for `pytest.raises`/`excinfo`. The independent grep confirms pytest usage in each of the three target files is limited to exactly the call sites the plan's Green section rewrites — no fixtures, markers, or other pytest API calls exist that the plan's fix would miss.

### Phase 7 — 06.3 (wall-clock marker inflation)
✅ Bug mechanism fully confirmed (see citation-drift note above for line numbers). This is a precise, well-diagnosed off-by-one: `stop_sequence` is captured before the marker event is emitted, but `events_appended` is recomputed via a fresh `len()` call *after* the marker was appended — and only on the `timed_out` branch, since non-timed-out trials never emit that marker before the same computation runs. The proposed fix (reuse `stop_sequence` on the `timed_out` branch) is correct given this mechanism.

### Phase 8 — 06.4 + 06.5 (scenario resolution hardening)
✅ Fully confirmed, including the security-relevant core claim: `_resolve_scenario` (`_child.py:172-192`) resolves `scenario_path` at line 179 with **no containment check** against `root` before importing and executing it — independently verified via a grep for `is_relative_to`/`containment`/`traversal` in the file returning zero matches. The `sys.modules` asymmetry (pack-module block registers at line 154 before `exec_module`; scenario-module block never registers) is also confirmed exactly, as is the downstream consumer chain for the pack-module registration (`Runtime.load_pack` → `load_pack_into_runtime` → `_warn_on_manifest_violations` → `_locate_pack_manifest`, which reads `sys.modules.get(mod_name)` at `loader.py:433`). No existing direct unit test for `_child.py` exists today — confirmed via a `tests/` grep — supporting the plan's "BLOCKING" workflow-closure classification for this phase.

### Phase 9 — 07.4 (drop unreachable guard)
✅ Fully confirmed, including the "no reachable path sets `rt.graph = None`" claim — a repo-wide `.py`-scoped grep for `.graph = None`, `Runtime(...graph=None`, and `Runtime(None` returns zero production/test matches (the only hits are in `thoughts/` planning docs discussing this very finding). The guarded call (`:303-304`) and unguarded call (`:308-318`) are both confirmed exactly as described.

### Phase 10 — 08.5 (Protocol method default body)
✅ Fully confirmed, including the specific claim that no class anywhere in the repo currently subclasses `LLMProvider(Protocol)` without overriding `supports_native_structured_output` — independently verified that all 5 classes matching `class X(LLMProvider):` also appear in the `def supports_native_structured_output` override list, and `ScriptedProvider` (the one existing test double that reaches the `getattr`-fallback path) has no `LLMProvider` base class at all. This confirms the bug is real but currently latent, exactly as the plan states.

### Phase 11 — 10.8 + 10.9 (quickstart hygiene)
✅ Fully confirmed (see citation-drift notes above for two minor mislabels). The dead-import claim (`os`, `shutil` imported, zero uses anywhere) is confirmed by word-boundary grep. The three-independent-literals claim for `"growth_flagger"` is confirmed accurate in substance — a raw grep finds a 4th textual occurrence, but it's inside the same comment block (`:428-432`) the plan already schedules for a rewording in Green step 3, so there's no missed site.

### Phase 12 — 10.12 (llm.requested special case)
✅ Fully confirmed, including the strongest form of the "provably unreachable" claim: `_FORMATTERS` is referenced in exactly two places repo-wide (its own definition and the one lookup inside `format_event`, which is always short-circuited by the `if event.type == "llm.requested":` check three lines earlier). The 23-entry count for `_FORMATTERS` is confirmed by direct count.

---

## Workflow Closure Review

| Finding | Status | Note |
|---|---|---|
| 01.1 + 01.2 | ✅ | LEAF classification correct — direct dataclass/method call, no cross-module boundary |
| 01.3 | ❌ | **Scope gap — see Critical Issue 1.** The LEAF classification and lock-ownership introspection test design are sound for the 2 methods covered, but 6 more methods with the identical shape are undertested |
| 01.6 | ✅ | Correctly classified as no-Red/Green docs phase |
| 01.9 | ✅ | LEAF via ABC machinery — correct, and the "only 2 concrete subclasses" premise independently confirmed |
| 01.10 | ✅ | Consistency-only, correctly scoped to the one outlier site |
| 06.1 | ✅ | BLOCKING classification justified — conformance modules are consumed as base classes by 5+ other test files |
| 06.3 | ✅ | LEAF, store-observable via existing test — correct given the confirmed bug mechanism |
| 06.4 + 06.5 | ✅ | BLOCKING classification justified — confirmed zero existing direct unit coverage for `_child.py` |
| 07.4 | ✅ | Refactor-classified correctly — confirmed no reachable path can violate the guard's assumption |
| 08.5 | ✅ | LEAF, correct — confirmed the gap is currently latent (no subclass exercises it yet) |
| 10.8 + 10.9 | ✅ | LEAF, correct |
| 10.12 | ✅ | Refactor-classified correctly — confirmed the table entry is genuinely dead code |

---

## Suggested Plan Amendment

```diff
~ Phase 2 (01.3) scope
- Red step 4: grep to confirm add_object/propose_patch are the only two methods
- with this shape; conditionally fix add_relation/apply_patch/reject_patch "if found."
+ Green already covers 8 methods with this shape, confirmed by direct grep during
+ planning: add_object, add_relation, remove_relation, remove_object, patch_object,
+ propose_patch, apply_patch, reject_patch. Add one characterization test + one
+ `with self._emit_lock:` wrap per method not already covered (6 additional:
+ add_relation [graph.py:707,742], remove_relation [:764], remove_object [:785],
+ patch_object [:818,838], apply_patch [:935], reject_patch [:977]).
+ If keeping the audit-then-expand structure instead, correct Red step 4's named
+ candidate list to include remove_relation, remove_object, and patch_object —
+ currently only add_relation/apply_patch/reject_patch are named, which would lead
+ a literal reading of the plan to still miss 3 of the 6 methods needing the fix.
```

No other phase needs a substantive amendment. The line-number drifts noted above (Phases 6, 7, 9, 11) are worth a quick re-read-and-correct pass before implementation begins, but they don't change what any phase should do.

## Approval Status

- [ ] Ready for Implementation — no critical issues
- [x] **Needs Minor Revision — one scope gap in Phase 2 (01.3) must be resolved before implementing that phase; all other 11 phases and all 4 descopes are implementation-ready as written**
- [ ] Needs Major Revision — critical contract and closure gaps must be resolved first
