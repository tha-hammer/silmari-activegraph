"""activegraph.packs.diligence — the reference pack for v0.9.

This is the production-quality pack for investment diligence,
evolved from `examples/diligence_with_tools.py` (which remains in
place per CONTRACT v0.9 #22).

What it provides:
  - 8 object types (company, document, question, claim, evidence,
    contradiction, risk, memo) with Pydantic schemas.
  - 6 relation types (addresses, supports, contradicts, references,
    derived_from, mitigates) with source/target type rules.
  - 7 behaviors: company_planner, question_generator,
    document_researcher (LLM + tools), evidence_linker (deterministic
    safety net), contradiction_detector (pattern subscription),
    risk_identifier (LLM, idempotent graph-scan), memo_synthesizer (LLM).
  - 3 pack-scoped tools: fetch_company_docs, search_filings,
    summarize_document. v0.9 backs these with recorded fixtures; a
    production user would swap real implementations.
  - 2 policies: memo_approval, risk_approval (gated by
    DiligenceSettings.auto_approve_memos / auto_approve_risks).
  - 4 prompts with TOML frontmatter, content-hashed for replay.
  - Settings: DiligenceSettings (Pydantic, all fields have defaults).
  - Recorded fixtures for three companies, suitable for running the
    killer demo in CI without API keys or network access.

How to use it:

    from activegraph import Runtime, Graph
    from activegraph.packs.diligence import pack, DiligenceSettings

    rt = Runtime(Graph(), llm_provider=my_provider)
    rt.load_pack(pack, settings=DiligenceSettings(...))
    rt.run_goal("Diligence: Northwind Robotics")

The Pack is exported as the single `pack` symbol per
`[project.entry-points."activegraph.packs"]` in the framework's
`pyproject.toml`. Once installed, `load_by_name("diligence")` works
from any user code.
"""

from __future__ import annotations

from pathlib import Path

from activegraph.packs import (
    Pack,
    PackPolicy,
    load_prompts_from_dir,
)
from activegraph.packs.diligence.behaviors import BEHAVIORS
from activegraph.packs.diligence.object_types import OBJECT_TYPES, RELATION_TYPES
from activegraph.packs.diligence.settings import DiligenceSettings
from activegraph.packs.diligence.tools import TOOLS


_PROMPTS_DIR = Path(__file__).parent / "prompts"


pack = Pack(
    name="diligence",
    version="0.1.0",
    description=(
        "Investment diligence: claims, evidence, contradictions, risks, "
        "memos. The v0.9 reference pack. Three behaviors are LLM-backed; "
        "fixtures ship with the pack for reproducible demos."
    ),
    object_types=OBJECT_TYPES,
    relation_types=RELATION_TYPES,
    behaviors=BEHAVIORS,
    tools=TOOLS,
    policies=[
        PackPolicy(
            name="memo_approval",
            requires_approval=("memo",),
        ),
        PackPolicy(
            name="risk_approval",
            requires_approval=("risk",),
        ),
    ],
    prompts=load_prompts_from_dir(_PROMPTS_DIR),
    settings_schema=DiligenceSettings,
)


__all__ = ["pack", "DiligenceSettings"]
