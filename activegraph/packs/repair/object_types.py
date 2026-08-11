"""Repair pack object and relation types.

Domain: dogfooding activegraph on itself. A `finding` is one verified,
citation-backed defect from the specs/ research pass
(thoughts/searchable/shared/research/2026-08-11-10-21-specs-diagrams-code-problems.md).
A `patch_proposal` is a concrete code change an LLM behavior authored
and applied directly to the real source tree. A `verification` is an
independent, deterministic re-check of that change (never trusts the
LLM's own self-report). A `repair_memo` is the run's final summary.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from activegraph.packs import ObjectType, RelationType


class Finding(BaseModel):
    """One verified defect, pre-diagnosed by the specs/ research pass."""

    finding_id: str
    category: str
    file: str
    summary: str
    hint_old_text: str = ""
    hint_lines: str = ""
    spec_source: str
    status: str = Field(default="open", pattern=r"^(open|patched|failed)$")


class PatchProposal(BaseModel):
    """A patch the LLM behavior authored and (attempted to) apply."""

    finding_id: str
    file: str
    rationale: str
    diff_summary: str
    applied: bool


class Verification(BaseModel):
    """An independent, deterministic re-check of a patch proposal."""

    finding_id: str
    passed: bool
    detail: str = ""


class RepairMemo(BaseModel):
    """The run's final aggregated summary."""

    summary: str
    fixed_count: int
    failed_count: int
    fixed_finding_ids: list[str]
    failed_finding_ids: list[str]


OBJECT_TYPES = [
    ObjectType(
        name="finding", schema=Finding,
        description="A verified defect from the specs/ research pass.",
    ),
    ObjectType(
        name="patch_proposal", schema=PatchProposal,
        description="A concrete code change authored and applied for a finding.",
    ),
    ObjectType(
        name="verification", schema=Verification,
        description="An independent syntax check of a patch proposal.",
    ),
    ObjectType(
        name="repair_memo", schema=RepairMemo,
        description="The run's final repair summary.",
    ),
]


RELATION_TYPES = [
    RelationType(
        name="addresses",
        source_types=("patch_proposal",),
        target_types=("finding",),
        description="A patch proposal addresses a finding.",
    ),
    RelationType(
        name="verifies",
        source_types=("verification",),
        target_types=("patch_proposal",),
        description="A verification checks a patch proposal.",
    ),
]
