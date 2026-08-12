"""Patch lifecycle. CONTRACT #4 (versioning) and #12 (single-target atomic)."""

import pytest

from activegraph import (
    ActiveGraphError,
    ApplyPatchNotFoundError,
    ExecutionError,
    FrozenClock,
    Graph,
    IDGen,
    InvalidPatchLifecycleState,
    ObjectNotFoundError,
    PatchNotFoundError,
    RejectPatchNotFoundError,
)


def _g():
    return Graph(ids=IDGen(), clock=FrozenClock())


def test_patch_object_emits_patch_applied_with_diff():
    g = _g()
    o = g.add_object("task", {"status": "blocked"})
    p = g.patch_object(o.id, {"status": "open"})
    assert p.status == "applied"
    assert g.get_object(o.id).version == 2  # bumped
    assert g.get_object(o.id).data["status"] == "open"
    last = g.events[-1]
    assert last.type == "patch.applied"
    assert last.payload["target"] == o.id
    assert last.payload["diff"]["status"] == {"old": "blocked", "new": "open"}


def test_propose_patch_emits_proposed_and_can_apply():
    g = _g()
    o = g.add_object("memory", {"summary": "old"})
    p = g.propose_patch(
        target=o.id,
        op="update",
        value={"summary": "new"},
        proposed_by="memory_behavior",
        rationale="user said so",
    )
    assert p.status == "proposed"
    types = [e.type for e in g.events]
    assert "patch.proposed" in types

    g.apply_patch(p.id, approved_by="reviewer")
    assert g.get_object(o.id).data["summary"] == "new"
    assert g.get_object(o.id).version == 2
    assert g.get_patch(p.id).status == "applied"


def test_apply_patch_with_stale_version_is_rejected():
    g = _g()
    o = g.add_object("memory", {"summary": "v1"})
    p = g.propose_patch(
        target=o.id,
        op="update",
        value={"summary": "v3-from-stale-branch"},
        proposed_by="A",
    )
    # Meanwhile, someone else patches the object — bumps version.
    g.patch_object(o.id, {"summary": "v2"})
    assert g.get_object(o.id).version == 2

    g.apply_patch(p.id, approved_by="reviewer")
    types_after = [e.type for e in g.events]
    assert types_after[-1] == "patch.rejected"
    # Object data unchanged by the rejected patch.
    assert g.get_object(o.id).data["summary"] == "v2"
    assert g.get_patch(p.id).status == "rejected"


def _state_before_lookup_miss(g, patch_id=None):
    return {
        "objects": [o.to_dict() for o in g.all_objects()],
        "events": [e.to_dict() for e in g.events],
        "patch": g.get_patch(patch_id).to_dict() if patch_id else None,
        "id_counters": vars(g.ids).copy(),
    }


def test_patch_object_missing_target_raises_typed_key_error_without_mutation():
    g = _g()
    existing = g.add_object("task", {"status": "open"})
    before = _state_before_lookup_miss(g)

    with pytest.raises(ObjectNotFoundError) as excinfo:
        g.patch_object("task#404", {"status": "closed"})

    err = excinfo.value
    assert isinstance(err, (ExecutionError, ActiveGraphError, KeyError))
    assert not isinstance(err, AttributeError)
    assert err.object_id == "task#404"
    assert "unknown object: task#404" in str(err)
    assert "get_object" in err.how_to_fix
    assert _state_before_lookup_miss(g) == before
    assert g.get_object(existing.id).version == 1


@pytest.mark.parametrize(
    ("operation", "error_type", "legacy_type", "forbidden_legacy_type"),
    [
        ("apply", ApplyPatchNotFoundError, KeyError, AttributeError),
        ("reject", RejectPatchNotFoundError, AttributeError, KeyError),
    ],
)
def test_missing_patch_uses_operation_specific_leaf_without_mutation(
    operation, error_type, legacy_type, forbidden_legacy_type
):
    g = _g()
    obj = g.add_object("task", {"status": "open"})
    existing = g.propose_patch(
        obj.id,
        "update",
        {"status": "closed"},
        proposed_by="planner",
    )
    before = _state_before_lookup_miss(g, existing.id)

    with pytest.raises(error_type) as excinfo:
        if operation == "apply":
            g.apply_patch("patch_404")
        else:
            g.reject_patch("patch_404", "not acceptable")

    err = excinfo.value
    assert isinstance(err, (PatchNotFoundError, ExecutionError, ActiveGraphError))
    assert isinstance(err, legacy_type)
    assert not isinstance(err, forbidden_legacy_type)
    assert err.patch_id == "patch_404"
    assert "unknown patch: patch_404" in str(err)
    assert "get_patch" in err.how_to_fix
    assert _state_before_lookup_miss(g, existing.id) == before


def test_missing_patch_preserves_ordered_legacy_handler_selection():
    g = _g()

    try:
        g.apply_patch("patch_404")
    except AttributeError:
        apply_branch = "attribute"
    except KeyError:
        apply_branch = "key"

    try:
        g.reject_patch("patch_404", "not acceptable")
    except KeyError:
        reject_branch = "key"
    except AttributeError:
        reject_branch = "attribute"

    assert apply_branch == "key"
    assert reject_branch == "attribute"


@pytest.mark.parametrize("terminal_state", ["applied", "rejected"])
def test_apply_known_terminal_patch_still_raises_lifecycle_error(terminal_state):
    g = _g()
    obj = g.add_object("task", {"status": "open"})
    patch = g.propose_patch(
        obj.id,
        "update",
        {"status": "closed"},
        proposed_by="planner",
    )
    if terminal_state == "applied":
        g.apply_patch(patch.id)
    else:
        g.reject_patch(patch.id, "not acceptable")

    with pytest.raises(InvalidPatchLifecycleState) as excinfo:
        g.apply_patch(patch.id)

    assert excinfo.value.current_status == terminal_state
