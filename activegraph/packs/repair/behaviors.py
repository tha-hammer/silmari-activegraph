"""Repair pack behaviors.

Four behaviors mirroring the diligence pack's anatomy:
  - finding_loader (plain @behavior, trigger-only): seeds the pilot
    batch from fixtures.py, same role as diligence's company_planner.
  - patch_author (@llm_behavior + tools): the one genuinely
    LLM-judgment behavior — reads real source, authors a minimal
    patch, applies it. Fires once per finding.
  - patch_verifier (plain @behavior, deterministic safety net): never
    trusts patch_author's self-reported `applied`/`ok` — independently
    re-parses the file. Same role as diligence's evidence_linker.
  - repair_memo_synthesizer (plain @behavior, idempotent-gate): pure
    aggregation of already-materialized graph state, not LLM
    judgment, so it costs nothing extra and can't discard wasted LLM
    calls the way diligence's memo_synthesizer does when it fires
    more than once.
"""

from __future__ import annotations

import ast
from pathlib import Path

from pydantic import BaseModel

from activegraph.packs import behavior, llm_behavior
from activegraph.packs.repair.fixtures import PILOT_FINDINGS
from activegraph.packs.repair.object_types import (
    Finding,
    PatchProposal,
    RepairMemo,
    Verification,
)
from activegraph.packs.repair.tools import apply_patch, read_source, run_check

REPO_ROOT = Path(__file__).resolve().parents[3]


@behavior(name="finding_loader", on=["goal.created"])
def finding_loader(event, graph, ctx):
    """Bootstrap: seed the pilot batch of findings from fixtures.py."""
    goal_text = event.payload.get("goal", "")
    if not goal_text.startswith("Repair:"):
        return
    if ctx.view.objects(type="finding"):
        return
    for f in PILOT_FINDINGS:
        graph.add_object("finding", Finding(**f).model_dump())


class PatchAuthorOutput(BaseModel):
    rationale: str
    diff_summary: str
    applied: bool


@llm_behavior(
    name="patch_author",
    on=["object.created"],
    where={"object.type": "finding"},
    description=(
        "Fix exactly one verified, pre-diagnosed code defect described in "
        "the triggering finding object. Use read_source, apply_patch, and "
        "run_check to confirm current content, apply a minimal fix, and "
        "verify it still parses."
    ),
    output_schema=PatchAuthorOutput,
    tools=[read_source, apply_patch, run_check],
    creates=["patch_proposal"],
    deterministic=True,
    budget={"max_tool_calls": 6},
)
def patch_author(event, graph, ctx, out):
    finding_id = event.payload["object"]["id"]
    data = event.payload["object"]["data"]
    patch = graph.add_object(
        "patch_proposal",
        PatchProposal(
            finding_id=data["finding_id"],
            file=data["file"],
            rationale=out.rationale,
            diff_summary=out.diff_summary,
            applied=out.applied,
        ).model_dump(),
    )
    graph.add_relation(patch.id, finding_id, "addresses")
    graph.patch_object(finding_id, {"status": "patched" if out.applied else "failed"})


@behavior(
    name="patch_verifier",
    on=["object.created"],
    where={"object.type": "patch_proposal"},
)
def patch_verifier(event, graph, ctx):
    """Deterministic safety net: never trust the LLM's self-report.

    Independently re-parses the file the patch touched, the same way
    diligence's evidence_linker independently re-links evidence rather
    than trusting the LLM behavior that created it.
    """
    data = event.payload["object"]["data"]
    patch_id = event.payload["object"]["id"]
    if not data.get("applied"):
        v = graph.add_object(
            "verification",
            Verification(
                finding_id=data["finding_id"],
                passed=False,
                detail="patch_author reported applied=False",
            ).model_dump(),
        )
        graph.add_relation(v.id, patch_id, "verifies")
        return

    full_path = REPO_ROOT / data["file"]
    try:
        ast.parse(full_path.read_text(), filename=str(full_path))
        passed, detail = True, "independent ast.parse confirms valid syntax"
    except SyntaxError as e:
        passed, detail = False, f"independent check failed: {type(e).__name__}: {e}"
    v = graph.add_object(
        "verification",
        Verification(
            finding_id=data["finding_id"], passed=passed, detail=detail
        ).model_dump(),
    )
    graph.add_relation(v.id, patch_id, "verifies")


@behavior(
    name="repair_memo_synthesizer",
    on=["object.created"],
    where={"object.type": "verification"},
)
def repair_memo_synthesizer(event, graph, ctx):
    """Idempotent-scan: fires on every verification, only materializes
    once all findings have one and no memo exists yet — same gating
    shape as diligence's risk_identifier/memo_synthesizer, but plain
    Python aggregation instead of an LLM call, since there's no
    judgment left to make once every finding is verified.
    """
    findings = ctx.view.objects(type="finding")
    verifications = ctx.view.objects(type="verification")
    if len(verifications) < len(findings):
        return
    if ctx.view.objects(type="repair_memo"):
        return

    fixed = sorted({v.data["finding_id"] for v in verifications if v.data.get("passed")})
    failed = sorted({v.data["finding_id"] for v in verifications if not v.data.get("passed")})
    graph.add_object(
        "repair_memo",
        RepairMemo(
            summary=(
                f"Repair pilot: {len(fixed)}/{len(findings)} findings patched "
                f"and independently verified; {len(failed)} failed verification."
            ),
            fixed_count=len(fixed),
            failed_count=len(failed),
            fixed_finding_ids=fixed,
            failed_finding_ids=failed,
        ).model_dump(),
    )


BEHAVIORS = [finding_loader, patch_author, patch_verifier, repair_memo_synthesizer]
