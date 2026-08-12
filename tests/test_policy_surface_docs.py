"""Static gates for the current object-approval policy surface."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_CONCEPT = ROOT / "docs" / "concepts" / "policies.md"
PACK_AUTHORING = ROOT / "docs" / "guides" / "authoring-packs.md"
CONTRACT = ROOT / "CONTRACT.md"


def test_current_docs_describe_approval_as_an_explicit_proposal() -> None:
    concept = POLICY_CONCEPT.read_text()
    authoring = PACK_AUTHORING.read_text()

    for text in (concept, authoring):
        assert "Graph.add_object" in text
        assert "Context.propose_object" in text
        assert "approval.proposed" in text

    combined = concept + authoring
    assert "settings_key=" not in combined
    assert "`object.proposed`" not in combined
    assert "Loaded policies modify how `graph.add_object` behaves" not in combined
    assert "the framework approves every proposal automatically" not in combined


def test_contract_preserves_history_and_appends_explicit_approval_overlay() -> None:
    contract = CONTRACT.read_text()

    # Append-only means the inaccurate historical language is retained and
    # narrowed by the newest numbered clause, not silently rewritten.
    assert "`memo_approval` (memo writes require approval)" in contract
    assert "its gating policies stop gating" in contract

    heading = (
        "## v1.11 #2. Object approval is explicit; policy attributes ownership"
    )
    assert contract.count(heading) == 1
    overlay = contract.split(heading, 1)[1]
    assert "supersedes only v0.9 #15" in overlay
    assert "v1.4 #3" in overlay
    assert "`Graph.add_object` is always an immediate write" in overlay
    assert (
        "`Context.propose_object` is the only object-approval entry point"
        in overlay
    )
    assert "`PackPolicy.requires_approval` supplies owner attribution" in overlay
    assert (
        "Existing pending approvals and their durable events remain unchanged"
        in overlay
    )
