"""Diligence pack integration test.

The killer demo (`examples/diligence_real_run.py`) is the spec
(CONTRACT v0.9 #19). This test asserts the verifiable memo bar
against a fresh runtime:
  - Three memos produced (one per company).
  - Each memo has the contracted sections.
  - Every claim in a memo cites at least one evidence id.
  - At least one contradiction is surfaced OR explicitly noted absent.
  - At least one risk per memo.
  - No uncited claims.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

import pytest

from activegraph import Graph, PackSchemaViolation, Runtime
from activegraph.packs.diligence import pack as diligence_pack
from activegraph.packs.diligence import DiligenceSettings
from activegraph.packs.diligence.object_types import OBJECT_TYPES, RELATION_TYPES
from activegraph.packs.diligence.fixtures import (
    RecordedDiligenceProvider,
    THREE_COMPANIES,
    company_goal,
)
from activegraph.packs.manifest import (
    load_manifest,
    verify_content_hash,
    verify_surface,
)


@pytest.fixture
def diligence_runtime():
    """Fresh runtime with the diligence pack loaded and three companies run.

    Per CONTRACT v0.9 #18, fixtures are embedded; the test runs under 30s.
    """
    provider = RecordedDiligenceProvider(companies=THREE_COMPANIES)
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(db_path)  # remove the empty file
    try:
        graph = Graph()
        rt = Runtime(
            graph,
            llm_provider=provider,
            persist_to=db_path,
            budget={"max_llm_calls": 100, "max_tool_calls": 200, "max_cost_usd": "5.00"},
        )
        rt.load_pack(diligence_pack, settings=DiligenceSettings())
        for c in THREE_COMPANIES:
            rt.run_goal(company_goal(c))
        yield rt
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_three_memos_produced(diligence_runtime):
    memos = [o for o in diligence_runtime.graph.all_objects() if o.type == "memo"]
    assert len(memos) == 3, f"expected 3 memos (one per company), got {len(memos)}"


def test_diligence_manifest_matches_source_surface_and_content():
    import activegraph.packs.diligence as diligence_module

    root = Path(diligence_module.__file__).resolve().parent
    manifest = load_manifest(root)
    verify_surface(manifest, diligence_pack)
    verify_content_hash(manifest, root)


def test_diligence_load_emits_no_manifest_warning(caplog):
    from activegraph.packs import loader as pack_loader

    pack_loader._manifest_checked.clear()
    provider = RecordedDiligenceProvider(companies=THREE_COMPANIES)
    with caplog.at_level(logging.WARNING, logger="activegraph.packs.manifest"):
        runtime = Runtime(Graph(), llm_provider=provider)
        assert runtime.load_pack(
            diligence_pack, settings=DiligenceSettings()
        ) is True
    assert not [
        record
        for record in caplog.records
        if record.name == "activegraph.packs.manifest"
        and record.levelno >= logging.WARNING
    ]


def test_each_memo_has_required_sections(diligence_runtime):
    required = (
        "summary",
        "thesis_questions_addressed",
        "key_claims",
        "open_contradictions",
        "risks",
    )
    memos = [o for o in diligence_runtime.graph.all_objects() if o.type == "memo"]
    for memo in memos:
        for sec in required:
            assert sec in memo.data, f"memo {memo.id} missing section {sec!r}"


def test_each_memo_has_at_least_one_risk(diligence_runtime):
    memos = [o for o in diligence_runtime.graph.all_objects() if o.type == "memo"]
    for memo in memos:
        risks = memo.data.get("risks") or []
        assert len(risks) >= 1, f"memo {memo.id} surfaces zero risks"


def test_each_memo_addresses_contradictions(diligence_runtime):
    memos = [o for o in diligence_runtime.graph.all_objects() if o.type == "memo"]
    for memo in memos:
        contradictions = memo.data.get("open_contradictions") or []
        if not contradictions:
            note = memo.data.get("contradictions_note", "")
            assert note == "no contradictions found", (
                f"memo {memo.id}: zero contradictions and no explicit note"
            )


def test_each_memo_claim_cites_evidence(diligence_runtime):
    """The CONTRACT-mandated 'no uncited claims' rule (v0.9 #19)."""
    memos = [o for o in diligence_runtime.graph.all_objects() if o.type == "memo"]
    for memo in memos:
        for kc in memo.data.get("key_claims", []):
            ev_ids = kc.get("evidence_ids") or []
            assert len(ev_ids) >= 1, (
                f"memo {memo.id}: claim {kc.get('claim_id', '?')!r} "
                f"has no evidence_ids; uncited claims violate the memo bar"
            )


def test_contradiction_detected_for_stellar(diligence_runtime):
    """Stellar's fixture deliberately encodes a +18% filing vs -7%
    survey contradiction. The pattern subscription must detect it
    and create a `contradiction` object.
    """
    contradictions = [
        o for o in diligence_runtime.graph.all_objects()
        if o.type == "contradiction"
    ]
    assert len(contradictions) >= 1, (
        "expected at least one contradiction to be detected via pattern "
        "subscription on Stellar's filing-vs-survey claims"
    )


def test_diligence_schema_inventory_includes_claim_contradiction_edge(
    diligence_runtime,
):
    assert len(OBJECT_TYPES) == 8
    assert len(RELATION_TYPES) == 7
    selected = [
        relation
        for relation in RELATION_TYPES
        if relation.name == "has_contradiction"
    ]
    assert len(selected) == 1
    assert selected[0].source_types == ("claim",)
    assert selected[0].target_types == ("contradiction",)

    loaded = next(
        event for event in diligence_runtime.graph.events if event.type == "pack.loaded"
    )
    assert len(loaded.payload["object_types"]) == 8
    assert len(loaded.payload["relation_types"]) == 7
    assert loaded.payload["relation_types"].count("has_contradiction") == 1


def test_each_contradiction_is_reachable_from_both_real_claims(diligence_runtime):
    graph = diligence_runtime.graph
    contradictions = graph.objects(type="contradiction")
    assert contradictions
    selected_relations = graph.relations(type="has_contradiction")

    for contradiction in contradictions:
        claim_ids = (
            contradiction.data["claim_a_id"],
            contradiction.data["claim_b_id"],
        )
        exact = [
            relation
            for relation in selected_relations
            if relation.target == contradiction.id
        ]
        assert {(relation.source, relation.target) for relation in exact} == {
            (claim_ids[0], contradiction.id),
            (claim_ids[1], contradiction.id),
        }
        assert len(exact) == 2

        for claim_id in claim_ids:
            assert graph.get_object(claim_id).type == "claim"
            objects, relations = graph.neighborhood(claim_id, depth=1)
            assert contradiction.id in {obj.id for obj in objects}
            assert (claim_id, contradiction.id, "has_contradiction") in {
                (relation.source, relation.target, relation.type)
                for relation in relations
            }

    created = [
        event
        for event in graph.events
        if event.type == "relation.created"
        and event.payload["relation"]["type"] == "has_contradiction"
    ]
    assert len(created) == 2 * len(contradictions)


def test_has_contradiction_rejects_known_wrong_endpoint_types():
    graph = Graph()
    runtime = Runtime(graph, behaviors=[])
    runtime.load_pack(diligence_pack, settings=DiligenceSettings())
    company = graph.add_object("company", {"name": "Test Co"})
    claim = graph.add_object(
        "claim",
        {"text": "claim", "confidence": 0.9, "company_id": company.id},
    )
    contradiction = graph.add_object(
        "contradiction",
        {"claim_a_id": claim.id, "claim_b_id": claim.id},
    )
    before = len(graph.events)

    with pytest.raises(PackSchemaViolation):
        graph.add_relation(company.id, contradiction.id, "has_contradiction")
    with pytest.raises(PackSchemaViolation):
        graph.add_relation(claim.id, company.id, "has_contradiction")

    assert len(graph.events) == before
    assert graph.relations(type="has_contradiction") == []


def test_pack_loaded_event_carries_prompt_hashes(diligence_runtime):
    """CONTRACT v0.9 #10 + #13: prompt content hashes are recorded in
    the `pack.loaded` event so replay can verify drift.
    """
    pack_loaded = [
        e for e in diligence_runtime.graph.events if e.type == "pack.loaded"
    ]
    assert len(pack_loaded) == 1
    prompts = pack_loaded[0].payload.get("prompts", {})
    assert "question_generator" in prompts
    assert "document_researcher" in prompts
    assert "risk_identifier" in prompts
    assert "memo_synthesizer" in prompts
    for name, manifest in prompts.items():
        assert manifest["hash"].startswith("sha256:"), (
            f"prompt {name!r}: hash does not start with sha256: ({manifest['hash']!r})"
        )
        assert manifest["version"], f"prompt {name!r}: missing declared version"


def test_behaviors_use_canonical_prefixed_names(diligence_runtime):
    """CONTRACT v0.9 #8: behaviors in the trace appear with their
    canonical `diligence.<name>` prefix.
    """
    behaviors_started = [
        e for e in diligence_runtime.graph.events
        if e.type == "behavior.started"
    ]
    names = {e.payload["behavior"] for e in behaviors_started}
    # Pack-owned behaviors all have the prefix
    pack_owned = {n for n in names if n.startswith("diligence.")}
    assert "diligence.question_generator" in pack_owned
    assert "diligence.document_researcher" in pack_owned
    assert "diligence.memo_synthesizer" in pack_owned


def test_loaded_packs_inspection(diligence_runtime):
    packs = diligence_runtime.loaded_packs()
    assert len(packs) == 1
    assert packs[0].name == "diligence"


def test_short_name_lookup(diligence_runtime):
    """Single pack loaded → short names always work."""
    b = diligence_runtime.get_behavior("question_generator")
    assert b.name == "diligence.question_generator"


def test_diligence_llm_tool_refs_are_canonical_runtime_tools(diligence_runtime):
    researcher = diligence_runtime.get_behavior("diligence.document_researcher")
    expected = [
        diligence_runtime.get_tool("diligence.fetch_company_docs"),
        diligence_runtime.get_tool("diligence.summarize_document"),
    ]
    assert researcher.tools == expected
    assert all(actual is wanted for actual, wanted in zip(researcher.tools, expected))


def test_killer_demo_script_runs():
    """The full killer demo is the executable contract."""
    import subprocess
    script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "examples", "diligence_real_run.py",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = (
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )
    result = subprocess.run(
        [sys.executable, script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"killer demo exited {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n"
    )
    assert "OK: 3 memos" in result.stdout
