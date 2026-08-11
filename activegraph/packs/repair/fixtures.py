"""Repair batches: findings confirmed in
thoughts/searchable/shared/research/2026-08-11-10-21-specs-diagrams-code-problems.md,
chosen to stay low-risk and mechanically scoped for automated repair —
single-file, no design decisions (unenforced-feature and
diverged-duplicate findings that require choosing which copy is
authoritative are deliberately excluded; those need human judgment).
`hint_old_text` is ground-truth text read directly from the file at
research/fixture-authoring time — `patch_author` is instructed to
re-confirm it with `read_source` before patching, since the file may
have moved since.

BATCH1 (8 findings) shipped in the first pilot run — kept here as a
historical record, no longer seeded by finding_loader. See the
research doc's "Follow-up Research" section for the full run log.

Note on 14.1: the original spec (specs/14-pack-anatomy-diligence.md)
cited this as `behaviors.py:15`; re-reading the file directly during
pack construction found the actual text lives in
`packs/diligence/__init__.py:15` instead (the module docstring's
one-line behavior roster, not `behaviors.py`'s own docstring). Fixed
here.
"""

from __future__ import annotations


BATCH1_FINDINGS = [
    {
        "finding_id": "03.1",
        "category": "dead_code",
        "file": "activegraph/runtime/promote.py",
        "summary": (
            "Dead import: `from activegraph.core.event import Event` at line 37 "
            "is never referenced again anywhere in the file. Remove the import line."
        ),
        "hint_old_text": "from activegraph.core.event import Event\n",
        "hint_lines": "37",
        "spec_source": "specs/03-runtime-governance.md",
    },
    {
        "finding_id": "02.1",
        "category": "dead_code",
        "file": "activegraph/runtime/runtime.py",
        "summary": (
            "`self._inside_dispatch = False` at line 471 is assigned once in "
            "__init__ and never read or written again anywhere in the codebase "
            "— a vestige of a re-entrancy guard. Remove the assignment line."
        ),
        "hint_old_text": "        self._inside_dispatch = False\n",
        "hint_lines": "471",
        "spec_source": "specs/02-runtime-core.md",
    },
    {
        "finding_id": "07.3",
        "category": "dead_code",
        "file": "activegraph/packs/loader.py",
        "summary": (
            "`import copy` at line 27 has zero `copy.` usages anywhere in the "
            "file. Remove the dead import."
        ),
        "hint_old_text": "import copy\n",
        "hint_lines": "27",
        "spec_source": "specs/07-packs.md",
    },
    {
        "finding_id": "14.1",
        "category": "stale_docstring",
        "file": "activegraph/packs/diligence/__init__.py",
        "summary": (
            "The module docstring's one-line behavior roster claims "
            "'risk_identifier (LLM, activate_after=8)', but the actual "
            "risk_identifier decorator (behaviors.py:305-322) takes no "
            "activate_after argument at all — it uses an idempotent graph-scan "
            "instead (behaviors.py:309-310 explicitly says so: 'Simpler than "
            "activate_after for v0.9... the killer demo doesn't need delayed "
            "scheduling here'). Reword the docstring line to describe the "
            "idempotent-scan approach instead of the false activate_after=8 claim."
        ),
        "hint_old_text": (
            "risk_identifier (LLM, activate_after=8), memo_synthesizer (LLM)."
        ),
        "hint_lines": "15",
        "spec_source": "specs/14-pack-anatomy-diligence.md",
    },
    {
        "finding_id": "08.10",
        "category": "stale_docstring",
        "file": "activegraph/packs/diligence/fixtures/__init__.py",
        "summary": (
            "RecordedDiligenceProvider's class docstring says it inspects "
            "\"the system prompt (contains the behavior_name on the "
            "'## Behavior:' line)\", but the actual code "
            "(_extract_behavior_name) regexes for `behavior named \"...\"` and "
            "splits the user message on the marker '## Triggering event' — "
            "neither matches the documented '## Behavior:' line. Reword the "
            "docstring bullet to describe the real markers "
            "(`behavior named \"...\"` in the system prompt, and the "
            "'## Triggering event' split point in the user message)."
        ),
        "hint_old_text": (
            "    The provider inspects:\n"
            "      - the system prompt (contains the behavior_name on the\n"
            "        '## Behavior:' line)\n"
            "      - the user message (contains the triggering event payload)\n"
        ),
        "hint_lines": "60-63",
        "spec_source": "specs/08-llm.md",
    },
    {
        "finding_id": "08.4",
        "category": "stale_docstring",
        "file": "activegraph/llm/anthropic.py",
        "summary": (
            "`_pricing_for`'s docstring claims unknown models 'fall back to "
            "sonnet-4 pricing and emit a warning via the returned `Decimal`', "
            "but the function body (lines 54-61) contains no logging or "
            "warning call at all — it just returns a Decimal tuple. Remove the "
            "false warning claim from the docstring (compare with openai.py's "
            "accurate equivalent docstring, which makes no such claim)."
        ),
        "hint_old_text": (
            "    Unknown models fall back to sonnet-4 pricing and emit a warning\n"
            "    via the returned `Decimal` (caller can detect by comparing to\n"
            "    family default).\n"
        ),
        "hint_lines": "49-51",
        "spec_source": "specs/08-llm.md",
    },
    {
        "finding_id": "04.7",
        "category": "cosmetic",
        "file": "activegraph/store/retention.py",
        "summary": (
            "The `pins()` function's inline pin-numbering comments read "
            "'# Pin 1', '# Pin 2', '# Pin 4' — there is no '# Pin 3' anywhere "
            "in the function, only three pins actually exist. Renumber the "
            "'# Pin 4' comment (around line 222) to '# Pin 3'. Only change "
            "that one comment's number; do not touch the Pin 1 or Pin 2 "
            "comments or any code."
        ),
        "hint_old_text": (
            "    # Pin 4: pending machinery — unresolved approvals, proposed patches.\n"
        ),
        "hint_lines": "222",
        "spec_source": "specs/04-store.md",
    },
    {
        "finding_id": "04.5",
        "category": "resource_leak",
        "file": "activegraph/store/retention.py",
        "summary": (
            "In the `retire()` function (around line 333), `store = "
            "SQLiteEventStore(path, run_id=run_id)` is constructed and used "
            "once via `store.archive_run(...)`, then the function returns "
            "without ever calling `store.close()` — an unbounded connection "
            "leak on every retire() call. Wrap the existing call in a "
            "try/finally that closes the store. SCOPE: only fix retire() (this "
            "single call site around line 333). Do NOT touch the pins() "
            "function above it — it opens several SQLiteEventStore instances "
            "inside loops and is out of scope for this pass."
        ),
        "hint_old_text": (
            "    store = SQLiteEventStore(path, run_id=run_id)\n"
            "    return store.archive_run(archived_at=_now_iso())\n"
        ),
        "hint_lines": "333-334",
        "spec_source": "specs/04-store.md",
    },
]


# BATCH2 (8 more findings): same low-risk profile as batch 1 — every
# entry here is a single-file stale-docstring/comment correction or a
# dead-code deletion with no other readers in the file. Findings that
# require a design decision (unenforced-feature) or picking which of
# several diverged implementations is authoritative are still excluded.
BATCH2_FINDINGS = [
    {
        "finding_id": "00.1",
        "category": "stale_docstring",
        "file": "activegraph/behaviors/__init__.py",
        "summary": (
            "The module docstring claims 'Imports core only (CONTRACT #14)', "
            "but behaviors/decorators.py makes function-local imports of "
            "runtime.patterns.parse, runtime.scheduler.parse_activate_after, "
            "and runtime._live.validate_behavior_against_live_runtimes, and "
            "behaviors/base.py imports llm.prompt.assemble_prompt and "
            "runtime.view_builder.build_view inside LLMBehavior.build_prompt. "
            "The real edge set is behaviors -> {core (types only), llm, "
            "runtime}. Reword the docstring to say that instead of "
            "'Imports core only'."
        ),
        "hint_old_text": (
            '"""Behavior decorators and base classes. Imports core only (CONTRACT #14)."""\n'
        ),
        "hint_lines": "1",
        "spec_source": "specs/00-overview.md",
    },
    {
        "finding_id": "04.3",
        "category": "stale_docstring",
        "file": "activegraph/store/errors.py",
        "summary": (
            "EventNotFoundError's docstring says it 'Fires from every "
            "store.get_event(event_id)', but all three backends "
            "(InMemoryEventStore, SQLiteEventStore, PostgresEventStore) "
            "return None for an unknown id instead of raising, and the "
            "conformance suite asserts exactly that "
            "(assert store.get_event('evt_missing') is None). Reword only "
            "the 'Fires from every store.get_event(event_id)' clause to say "
            "get_event returns None for a missing id instead. Leave the rest "
            "of the docstring (the fork-primitive / --at-event sentence) "
            "untouched — that part was not part of this finding."
        ),
        "hint_old_text": (
            "    Multi-inherits :class:`KeyError` so user code that does\n"
            "    ``except KeyError`` around store lookups keeps working. Fires from\n"
            "    every ``store.get_event(event_id)`` and from the fork primitive\n"
            "    when ``--at-event`` names a missing id.\n"
        ),
        "hint_lines": "44-48",
        "spec_source": "specs/04-store.md",
    },
    {
        "finding_id": "04.1",
        "category": "stale_docstring",
        "file": "activegraph/store/base.py",
        "summary": (
            "replay_into's docstring says 'The single replay entry point — "
            "used by Runtime.load and Runtime.fork', but it has zero "
            "in-package callers — both Runtime.load (runtime.py:3316-3317) "
            "and Runtime.fork (runtime.py:3487-3492) inline their own replay "
            "loop (graph._replay_event(ev) in a for loop) instead of calling "
            "this function. Reword the docstring to not claim it's used by "
            "them, since it currently is not."
        ),
        "hint_old_text": (
            "    The single replay entry point — used by `Runtime.load` and `Runtime.fork`.\n"
            "    Returns the number of events replayed.\n"
        ),
        "hint_lines": "79-80",
        "spec_source": "specs/04-store.md",
    },
    {
        "finding_id": "05.2",
        "category": "stale_docstring",
        "file": "activegraph/core/graph.py",
        "summary": (
            "Graph.add_sink's docstring never mentions that a sink attached "
            "this way (as opposed to via Runtime.add_sink) gets a "
            "NoOpMetrics backend by default — only Runtime.add_sink injects "
            "the real metrics backend. Add one sentence to the docstring "
            "documenting this default. This is an ADDITIVE fix — add a "
            "sentence, do not remove or reword the existing three sentences."
        ),
        "hint_old_text": (
            "        The returned handle owns a bounded FIFO and daemon worker.  A class\n"
            "        name is used when ``name`` is omitted; names must be unique within\n"
            "        the graph because they key both status and metrics.  Historical\n"
            "        events already present in the graph are never delivered.\n"
            "        \"\"\"\n"
        ),
        "hint_lines": "380-384",
        "spec_source": "specs/05-sinks.md",
    },
    {
        "finding_id": "08.7",
        "category": "missing_export",
        "file": "activegraph/llm/native.py",
        "summary": (
            "native.py defines no __all__, unlike its sibling "
            "embedding_cache.py which does. It's reachable only via lazy "
            "imports from anthropic.py, openai.py, and runtime.py. Add "
            "`__all__ = [\"native_schema_compatible\", "
            "\"inject_additional_properties_false\"]` right after the module's "
            "imports (before the _ALLOWED_KEYWORDS constant) — those are the "
            "two public functions the rest of the codebase actually imports; "
            "leave the underscore-prefixed helpers (_node_compatible, "
            "_inject) out of it."
        ),
        "hint_old_text": (
            "from __future__ import annotations\n\nimport copy\nfrom typing import Any, Optional\n"
        ),
        "hint_lines": "20-23",
        "spec_source": "specs/08-llm.md",
    },
    {
        "finding_id": "09.10",
        "category": "stale_docstring",
        "file": "activegraph/tools/recorded.py",
        "summary": (
            "_normalize_args(tool, args)'s docstring describes a two-branch "
            "behavior ('If args is a dict... If args is a BaseModel "
            "instance...') but the body ignores the `tool` parameter "
            "entirely and just delegates unconditionally to "
            "canonicalize_args(args). Reword the docstring to describe what "
            "the function actually does (delegates to canonicalize_args "
            "regardless of tool or args' type). Do NOT change the function "
            "signature or behavior — docstring only."
        ),
        "hint_old_text": (
            "    \"\"\"If args is a dict and the tool has an input_schema, return the dict.\n"
            "    If args is a BaseModel instance, dump to dict via canonicalize_args.\n"
            "    \"\"\"\n"
        ),
        "hint_lines": "64-66",
        "spec_source": "specs/09-tools-behaviors.md",
    },
    {
        "finding_id": "02.2",
        "category": "stale_docstring",
        "file": "activegraph/runtime/scheduler.py",
        "summary": (
            "ScheduledEntry.where_recheck_path's inline comment says "
            "\"behavior's `where=` payload path is kept\", but the only "
            "construction site (runtime.py:1325) always passes None, and "
            "the actual re-check at fire time reads behavior.where directly "
            "(runtime.py:1344) — the field is never read anywhere. Reword "
            "the comment to say it's currently unused/always None instead of "
            "claiming the path is 'kept'. Do NOT remove the field or change "
            "the dataclass shape — comment only."
        ),
        "hint_old_text": (
            "    where_recheck_path: Optional[str]  # behavior's `where=` payload path is kept\n"
        ),
        "hint_lines": "50",
        "spec_source": "specs/02-runtime-core.md",
    },
    {
        "finding_id": "07.3b",
        "category": "dead_code",
        "file": "activegraph/packs/loader.py",
        "summary": (
            "pre_ambiguous_behaviors and pre_ambiguous_tools are each "
            "computed via _compute_new_ambiguous_shorts(...) and never read "
            "again anywhere in the file (confirmed by grep — only their own "
            "assignment lines mention them). Remove both assignments. Keep "
            "the comment above them if it still makes sense standalone, or "
            "remove it too if it only makes sense next to the removed code — "
            "use your judgment, but do not change anything below this block."
        ),
        "hint_old_text": (
            "    pre_ambiguous_behaviors = _compute_new_ambiguous_shorts(\n"
            "        state.behavior_short_to_canonical, new_canonical_behaviors\n"
            "    )\n"
            "    pre_ambiguous_tools = _compute_new_ambiguous_shorts(\n"
            "        state.tool_short_to_canonical, new_canonical_tools\n"
            "    )\n"
        ),
        "hint_lines": "209-214",
        "spec_source": "specs/07-packs.md",
    },
]


# BATCH3 (9 more findings): same low-risk profile as batches 1-2. One
# entry (12.1) is a genuine one-line functional change rather than a
# docstring/comment reword — still single-file, single-line, and the
# correct output is unambiguous (match the ensure_ascii convention
# every other canonical-hash function in the package already uses).
BATCH3_FINDINGS = [
    {
        "finding_id": "01.5",
        "category": "stale_comment",
        "file": "activegraph/core/graph.py",
        "summary": (
            "Graph.emit's comment says 'Fail-fast serialization check at "
            "emit time so bad payloads never land in the in-memory log "
            "either (CONTRACT v0.5 #4)', but the check that follows only "
            "runs `if self._store is not None:` — a store-less graph accepts "
            "unserializable payloads despite the comment's unconditional "
            "framing. Reword the comment to note the check is conditional on "
            "a store being attached, not remove the CONTRACT v0.5 #4 "
            "citation."
        ),
        "hint_old_text": (
            "            # Fail-fast serialization check at emit time so bad payloads never\n"
            "            # land in the in-memory log either (CONTRACT v0.5 #4).\n"
            "            if self._store is not None:\n"
        ),
        "hint_lines": "570-572",
        "spec_source": "specs/01-core.md",
    },
    {
        "finding_id": "01.7",
        "category": "stale_docstring",
        "file": "activegraph/core/view.py",
        "summary": (
            "View's class docstring says 'nothing done to it mutates the "
            "graph', but View.objects()/.relations() only copy the outer "
            "list — the contained Object/Relation instances returned are the "
            "live, non-copied projection objects, so e.g. "
            "`view.objects()[0].data['x'] = 1` mutates graph state directly, "
            "bypassing the event log. Reword only the clause 'but nothing "
            "done to it mutates the graph' to note that filtering the view "
            "doesn't mutate the graph, but mutating an object/relation "
            "returned from it does (since they're the live instances, not "
            "copies). Do not change the rest of the docstring or any code."
        ),
        "hint_old_text": (
            "    (CONTRACT #11). A View is a point-in-time snapshot — filter it\n"
            "    with :meth:`objects` / :meth:`relations` / :meth:`events`, but\n"
            "    nothing done to it mutates the graph; mutations go through the\n"
            "    context's propose/patch surface and land as events.\n"
        ),
        "hint_lines": "21-25",
        "spec_source": "specs/01-core.md",
    },
    {
        "finding_id": "05.4",
        "category": "missing_export",
        "file": "activegraph/__init__.py",
        "summary": (
            "DeliveryMode is exported from activegraph.sinks.__init__ but "
            "missing from this top-level package's sinks re-export block, "
            "unlike every sibling sink type (DeliveryContext, EventSink, "
            "JSONLEventSink, OverflowPolicy, RecordedDelivery, RecordingSink, "
            "SinkConfig, SinkHandle, SinkState, SinkStatus are all here; "
            "DeliveryMode alone is missing). Add `DeliveryMode,` to this "
            "import block, keeping alphabetical order (it goes right after "
            "the opening paren, before DeliveryContext). You will also need "
            "to add \"DeliveryMode\" to this module's `__all__` list further "
            "down the file (use read_source on a wider range if you need to "
            "find it) — call apply_patch twice, once for each edit."
        ),
        "hint_old_text": (
            "from activegraph.sinks import (\n"
            "    DeliveryContext,\n"
            "    EventSink,\n"
        ),
        "hint_lines": "69-71",
        "spec_source": "specs/05-sinks.md",
    },
    {
        "finding_id": "08.1",
        "category": "stale_docstring",
        "file": "activegraph/llm/prompt.py",
        "summary": (
            "The module docstring describes AssembledPrompt.hash() as 'the "
            "cache key used by the replay layer', but the runtime's actual "
            "cache key (built by _hash_turn_prompt in runtime.py) always "
            "includes a 'tools' field that AssembledPrompt.hash()'s payload "
            "never has, so the two can never agree — hash()/canonical_json() "
            "have no non-test call sites anywhere in the repo (confirmed by "
            "grep). Reword only the sentence 'This is the cache key used by "
            "the replay layer.' to say hash() is a stable content-identity "
            "helper that is NOT the runtime's actual LLM-cache key (that's "
            "built separately, per-turn, in runtime.py). Keep the rest of "
            "the bullet (the SHA-256/canonical JSON description, 'Hash "
            "stability matters; tests snapshot it.') unchanged."
        ),
        "hint_old_text": (
            "  * `AssembledPrompt.hash()` returns a stable SHA-256 over the\n"
            "    canonical JSON of {model, system, messages, output_schema_name,\n"
            "    temperature, max_tokens, top_p, deterministic}. This is the cache\n"
            "    key used by the replay layer. Hash stability matters; tests\n"
            "    snapshot it.\n"
        ),
        "hint_lines": "20-24",
        "spec_source": "specs/08-llm.md",
    },
    {
        "finding_id": "08.8",
        "category": "missing_export",
        "file": "activegraph/llm/__init__.py",
        "summary": (
            "This package's __all__ omits sanitize_tool_name and "
            "native_schema_compatible even though runtime.py imports and "
            "uses both (via activegraph.llm.wire and activegraph.llm.native "
            "directly, not through this package). This file currently does "
            "not import either function at module level at all — you need "
            "TWO edits, each via apply_patch: (1) add a new import line "
            "`from activegraph.llm.native import native_schema_compatible` "
            "and `from activegraph.llm.wire import sanitize_tool_name` near "
            "the other `from activegraph.llm.*` import lines (alphabetical "
            "order: native then provider then recorded then types then "
            "wire — wire's import doesn't exist yet so add it after types); "
            "(2) add \"native_schema_compatible\" and \"sanitize_tool_name\" "
            "to the __all__ list (alphabetical order). Read the whole file "
            "with read_source first (it's short) so both edits are precise."
        ),
        "hint_old_text": (
            "from activegraph.llm.provider import LLMProvider\n"
            "from activegraph.llm.recorded import RecordedLLMProvider, RecordingLLMProvider\n"
            "from activegraph.llm.types import LLMMessage, LLMResponse, ToolCall\n"
        ),
        "hint_lines": "43-45",
        "spec_source": "specs/08-llm.md",
    },
    {
        "finding_id": "09.2",
        "category": "stale_docstring",
        "file": "activegraph/observability/metrics.py",
        "summary": (
            "activegraph_tools_failed_total's MetricSpec description says "
            "'Tool calls that produced a tool.failed event.', but no "
            "tool.failed event type exists anywhere in the codebase "
            "(confirmed by grep — only this docstring and one unrelated code "
            "comment mention the string 'tool.failed'). Reword the "
            "description to not claim a specific event name that doesn't "
            "exist — e.g. describe it as counting failed tool calls, without "
            "naming a 'tool.failed' event."
        ),
        "hint_old_text": (
            "    MetricSpec(\n"
            "        \"activegraph_tools_failed_total\",\n"
            "        \"counter\",\n"
            "        (\"tool\", \"reason\"),\n"
            "        \"Tool calls that produced a tool.failed event.\",\n"
            "    ),\n"
        ),
        "hint_lines": "152-157",
        "spec_source": "specs/09-tools-behaviors.md",
    },
    {
        "finding_id": "09.3",
        "category": "stale_docstring",
        "file": "activegraph/tools/errors.py",
        "summary": (
            "ToolError's class docstring enumerates nine legal reason codes, "
            "but tool.max_turns_exhausted (raised in runtime.py) and "
            "tool.unrecorded_external_io (used in tools/web_fetch.py) are "
            "both actually used in the code and are missing from this "
            "enumerated list. Add both to the list, in the same style as the "
            "existing entries. This is a documentation-only, additive fix — "
            "do NOT touch the _TOOL_REASON_PROSE dict elsewhere in the file, "
            "that's a separate, larger change out of scope here."
        ),
        "hint_old_text": (
            "      tool.timeout, tool.network_error, tool.invalid_input,\n"
            "      tool.invalid_output, tool.execution_error,\n"
            "      tool.unknown_tool, tool.fixture_missing,\n"
            "      budget.tool_calls_exhausted, budget.cost_exhausted.\n"
        ),
        "hint_lines": "282-285",
        "spec_source": "specs/09-tools-behaviors.md",
    },
    {
        "finding_id": "10.3",
        "category": "stale_docstring",
        "file": "activegraph/cli/main.py",
        "summary": (
            "The 'no such run' error message printed by cmd_promote tells "
            "the operator to run `activegraph inspect <url> --runs` to list "
            "runs, but cmd_inspect has no --runs option at all (confirmed by "
            "reading its full @click.option list) and no other CLI command "
            "lists runs either. Remove the false parenthetical hint "
            "'(activegraph inspect {url} --runs lists them)' — do not invent "
            "a replacement suggestion or add a new flag, just remove the "
            "false claim so the message states only the fact (no such run) "
            "without a misleading pointer."
        ),
        "hint_old_text": (
            "            click.echo(\n"
            "                f\"{label_} {rid!r}: no such run in {url} \"\n"
            "                f\"(activegraph inspect {url} --runs lists them)\",\n"
            "                err=True,\n"
            "            )\n"
        ),
        "hint_lines": "913-917",
        "spec_source": "specs/10-observability-trace-cli.md",
    },
    {
        "finding_id": "12.1",
        "category": "functional_consistency",
        "file": "activegraph/llm/embedding_cache.py",
        "summary": (
            "hash_embedding_request uses json.dumps(..., ensure_ascii=False, "
            "sort_keys=True, separators=(',', ':')), while every other "
            "canonical-hash implementation in the package "
            "(AssembledPrompt.canonical_json in llm/prompt.py, "
            "_hash_turn_prompt in runtime.py, _canonical_prompt_payload in "
            "llm/recorded.py) omits ensure_ascii entirely and so gets "
            "json.dumps's default of True — a non-ASCII input text "
            "canonicalizes differently under this one hashing scheme than "
            "every other hash in the codebase. Remove the `ensure_ascii=False,` "
            "line entirely so this function falls back to the same default "
            "(True) every other canonical-hash function in the package uses. "
            "Do not change sort_keys or separators."
        ),
        "hint_old_text": (
            "    canonical = json.dumps(\n"
            "        {\"model\": model, \"texts\": texts},\n"
            "        ensure_ascii=False,\n"
            "        sort_keys=True,\n"
            "        separators=(\",\", \":\"),\n"
            "    )\n"
        ),
        "hint_lines": "23-28",
        "spec_source": "specs/12-data-flow.md",
    },
]


# Active batch: finding_loader seeds whatever this currently points at.
PILOT_FINDINGS = BATCH3_FINDINGS
