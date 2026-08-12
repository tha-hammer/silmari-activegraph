"""Shared event-type policy contract and ownership tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from activegraph.runtime.event_policy import classify_event_type


EVENT_POLICY_CASES = [
    ("behavior.started", False, False, False, False),
    ("relation_behavior.completed", False, False, False, False),
    ("runtime.idle", False, False, False, False),
    ("llm.requested", False, False, True, True),
    ("tool.responded", False, False, True, True),
    ("pattern.matched", False, False, True, True),
    ("approval.proposed", False, False, True, True),
    ("embedding.requested", False, False, True, True),
    ("dev.override", False, False, True, True),
    ("authority.decision", False, False, True, True),
    ("context.read", False, False, True, False),
    ("context.foo", True, True, True, True),
    ("pack.loaded", True, True, True, True),
    ("goal.created", True, True, True, True),
    ("object.created", True, True, True, True),
    ("custom.event", True, True, True, True),
    ("runtimeish.event", True, True, True, True),
]


@pytest.mark.parametrize(
    (
        "event_type",
        "schedules_behaviors",
        "triggers_pattern_only",
        "included_in_diff",
        "included_in_strict_replay",
    ),
    EVENT_POLICY_CASES,
)
def test_event_type_policy_matrix(
    event_type: str,
    schedules_behaviors: bool,
    triggers_pattern_only: bool,
    included_in_diff: bool,
    included_in_strict_replay: bool,
) -> None:
    policy = classify_event_type(event_type)

    assert policy.schedules_behaviors is schedules_behaviors
    assert policy.triggers_pattern_only is triggers_pattern_only
    assert policy.included_in_diff is included_in_diff
    assert policy.included_in_strict_replay is included_in_strict_replay


def test_event_type_policy_is_immutable() -> None:
    policy = classify_event_type("custom.event")

    with pytest.raises(FrozenInstanceError):
        policy.schedules_behaviors = False  # type: ignore[misc]
