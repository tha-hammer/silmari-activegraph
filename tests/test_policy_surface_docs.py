"""Static gates for the current object-approval policy surface."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_CONCEPT = ROOT / "docs" / "concepts" / "policies.md"
PACK_AUTHORING = ROOT / "docs" / "guides" / "authoring-packs.md"
BEHAVIOR_CONCEPT = ROOT / "docs" / "concepts" / "behaviors.md"
PACK_SPEC = ROOT / "specs" / "07-packs.md"
RUNTIME_SPEC = ROOT / "specs" / "02-runtime-core.md"
BEHAVIOR_SPEC = ROOT / "specs" / "09-tools-behaviors.md"
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


def test_auto_apply_docs_mark_the_field_reserved_and_semantically_undefined() -> None:
    for path in (POLICY_CONCEPT, PACK_AUTHORING, PACK_SPEC):
        text = path.read_text()
        normalized = " ".join(text.split())
        assert "auto_apply" in normalized
        assert "reserved compatibility metadata" in normalized
        assert "list input is normalized to a tuple" in normalized.lower()
        assert (
            "no defined object-type, setting, exemption, or automatic grant semantics"
            in normalized
        )

        assert "auto_apply controls" not in normalized
        assert "auto_apply exempts" not in normalized
        assert "auto_apply automatically grants" not in normalized


def test_priority_docs_lock_registration_order_for_all_dispatch_paths() -> None:
    for path in (BEHAVIOR_CONCEPT, RUNTIME_SPEC, BEHAVIOR_SPEC):
        normalized = " ".join(path.read_text().split()).lower()
        assert "priority" in normalized
        assert "registration order" in normalized
        assert "reserved" in normalized

    behavior_docs = " ".join(BEHAVIOR_CONCEPT.read_text().split()).lower()
    assert "plain, llm, relation, global, pack, and delayed" in behavior_docs
    assert "runtimestatus.registered_behaviors" not in behavior_docs
    assert "runtime.status().registered_behaviors" in behavior_docs


def test_provider_docs_keep_raw_access_outside_the_supported_generation_path() -> None:
    for path in (BEHAVIOR_CONCEPT, RUNTIME_SPEC, BEHAVIOR_SPEC):
        normalized = " ".join(path.read_text().split()).lower()
        assert "llm_provider" in normalized
        assert "plain" in normalized and "relation" in normalized
        assert "none" in normalized
        assert "unsupported" in normalized or "not a supported" in normalized
        assert "@llm_behavior" in normalized
        assert "embed" in normalized


def test_capability_docs_separate_verification_from_host_owned_wiring() -> None:
    for path in (PACK_AUTHORING, PACK_SPEC):
        normalized = " ".join(path.read_text().split())
        assert "capabilities" in normalized
        assert "both directions" in normalized
        assert "risk_class" in normalized
        assert "action_class" in normalized
        assert "warning" in normalized
        assert "sandbox" in normalized
        assert "consumes" in normalized
        assert "host-owned" in normalized
        assert "pack.loaded" in normalized

        assert "capabilities are excluded from" not in normalized.lower()
        assert "consumes registers" not in normalized.lower()


def test_contract_appends_capability_verification_overlay() -> None:
    contract = CONTRACT.read_text()
    assert "`capabilities`/`consumes` deliberately excluded" in contract

    heading = (
        "## v1.11 #3. Capability declarations are verified; wiring remains "
        "host-owned"
    )
    assert contract.count(heading) == 1
    overlay = " ".join(contract.split(heading, 1)[1].split())
    assert "supersedes only v1.4 #1" in overlay
    assert "clarifies v1.9 #1" in overlay
    assert "`verify_surface` checks capabilities in both directions" in overlay
    assert "normal `Runtime.load_pack` retains v1.6 #1's warning tier" in overlay
    assert "Fork-trial sandbox materialization" in overlay
    assert "Manifest `consumes` parses and normalizes to a tuple" in overlay
    assert "does not create a gateway registry" in overlay


def test_delayed_relation_docs_lock_current_state_identity_and_budget_rules() -> None:
    for path in (BEHAVIOR_CONCEPT, RUNTIME_SPEC, BEHAVIOR_SPEC):
        normalized = " ".join(path.read_text().split()).lower()
        assert "relation" in normalized and "activate_after" in normalized
        assert "current" in normalized and "pattern" in normalized
        assert "original event payload" in normalized

    contract = CONTRACT.read_text()
    heading = "## v1.11 #4. Delayed relation behavior fire-time contract"
    assert contract.count(heading) == 1
    overlay = " ".join(contract.split(heading, 1)[1].split())
    assert "corrects and completes v0.7 #13" in overlay
    assert "exact still-registered behavior object" in overlay
    assert "Disabling a pack cancels pending entries" in overlay
    assert "entire unprocessed due suffix" in overlay
    assert "non-resumable" in overlay
    assert "exactly one `pattern.matched` marker" in overlay


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
