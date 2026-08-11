"""activegraph.packs.repair — dogfooding pack.

Runs a subset of the 78 confirmed findings from
thoughts/searchable/shared/research/2026-08-11-10-21-specs-diagrams-code-problems.md
through activegraph's own event-sourced graph + reactive-behavior +
LLM-behavior machinery: a `finding` object per defect, an
`@llm_behavior` (`patch_author`) that reads the real source, authors a
minimal patch, and applies it directly to the working tree; a
deterministic `patch_verifier` safety net that independently
re-checks syntax rather than trusting the LLM's self-report; and a
final aggregated `repair_memo`.

This first run seeds only the 8-finding pilot batch in `fixtures.py`,
spanning dead code, stale docstrings, a cosmetic renumbering, and one
real resource leak. Nothing is committed automatically — review with
`git diff` after a run.

    from activegraph import Runtime, Graph
    from activegraph.llm.anthropic import AnthropicProvider
    from activegraph.packs.repair import pack as repair_pack

    rt = Runtime(Graph(), llm_provider=AnthropicProvider())
    rt.load_pack(repair_pack)
    rt.run_goal("Repair: activegraph pilot batch")
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import Pack, load_prompts_from_dir
from activegraph.packs.repair.behaviors import BEHAVIORS
from activegraph.packs.repair.object_types import OBJECT_TYPES, RELATION_TYPES
from activegraph.packs.repair.tools import TOOLS


_PROMPTS_DIR = Path(__file__).parent / "prompts"


pack = Pack(
    name="repair",
    version="0.1.0",
    description=(
        "Dogfooding pack: repairs verified findings from the specs/ "
        "research pass by editing activegraph's own real source tree."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    prompts=load_prompts_from_dir(_PROMPTS_DIR),
)


__all__ = ["pack"]
