"""Pilot batch: 8 of the 78 findings confirmed in
thoughts/searchable/shared/research/2026-08-11-10-21-specs-diagrams-code-problems.md,
chosen to span categories (dead code, stale docstring, cosmetic,
resource leak) while staying low-risk and mechanically scoped for a
first automated pass. `hint_old_text` is ground-truth text read
directly from the file at research time — `patch_author` is instructed
to re-confirm it with `read_source` before patching, since the file
may have moved since.

Note on 14.1: the original spec (specs/14-pack-anatomy-diligence.md)
cited this as `behaviors.py:15`; re-reading the file directly during
pack construction found the actual text lives in
`packs/diligence/__init__.py:15` instead (the module docstring's
one-line behavior roster, not `behaviors.py`'s own docstring). Fixed
here.
"""

from __future__ import annotations


PILOT_FINDINGS = [
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
