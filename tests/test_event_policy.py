"""Shared event-type policy contract and ownership tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import importlib

import pytest

from activegraph import FrozenClock, Graph, Runtime, behavior, clear_registry
from activegraph.core.event import Event
from activegraph.runtime.event_policy import EventTypePolicy, classify_event_type
from activegraph.runtime.registry import Registry


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


def test_event_policy_has_one_imported_owner() -> None:
    runtime_module = importlib.import_module("activegraph.runtime.runtime")
    registry_module = importlib.import_module("activegraph.runtime.registry")
    diff_module = importlib.import_module("activegraph.runtime.diff")

    assert runtime_module.classify_event_type is classify_event_type
    assert registry_module.classify_event_type is classify_event_type
    assert diff_module.classify_event_type is classify_event_type
    assert not hasattr(runtime_module, "_is_lifecycle")
    assert not hasattr(registry_module, "_is_lifecycle")
    assert not hasattr(diff_module, "_is_lifecycle")
    assert not hasattr(diff_module, "_LIFECYCLE_PREFIXES")


def test_live_resume_registry_and_diff_consume_their_policy_fields(
    monkeypatch,
) -> None:
    runtime_module = importlib.import_module("activegraph.runtime.runtime")
    registry_module = importlib.import_module("activegraph.runtime.registry")
    diff_module = importlib.import_module("activegraph.runtime.diff")
    rejected = EventTypePolicy(False, False, False, False)

    monkeypatch.setattr(runtime_module, "classify_event_type", lambda _: rejected)
    runtime = Runtime(Graph())
    runtime.graph.emit(Event(id="evt_live", type="custom.event"))
    assert len(runtime._queue) == 0

    runtime_module._requeue_unfired(
        runtime, [Event(id="evt_resume", type="custom.event")]
    )
    assert len(runtime._queue) == 0

    clear_registry()

    @behavior(name="auditor", pattern="(c:claim)")
    def auditor(event, graph, ctx):
        pass

    graph = Graph()
    graph.add_object("claim", {})
    monkeypatch.setattr(registry_module, "classify_event_type", lambda _: rejected)
    assert Registry([auditor]).match(
        Event(id="evt_registry", type="custom.event"), graph
    ) == []

    parent = Graph()
    fork = Graph()
    fork.emit(Event(id="evt_diff", type="custom.event"))
    monkeypatch.setattr(diff_module, "classify_event_type", lambda _: rejected)
    diff = diff_module.compute_diff(parent, fork, "parent", "fork")
    assert diff.fork_only_events == []


def test_strict_replay_consumes_shared_policy_alias(tmp_path, monkeypatch) -> None:
    runtime_module = importlib.import_module("activegraph.runtime.runtime")
    clear_registry()

    @behavior(name="det", on=["goal.created"])
    def deterministic(event, graph, ctx):
        graph.add_object("claim", {"text": "stable"})

    path = str(tmp_path / "policy.db")
    runtime = Runtime(
        Graph(clock=FrozenClock()), behaviors=[deterministic], persist_to=path
    )
    runtime.run_goal("go")

    seen: list[str] = []

    def spy(event_type: str) -> EventTypePolicy:
        seen.append(event_type)
        return classify_event_type(event_type)

    monkeypatch.setattr(runtime_module, "classify_event_type", spy)
    Runtime.load(path, behaviors=[deterministic], replay_strict=True)

    assert {"goal.created", "object.created", "behavior.started", "runtime.idle"} <= set(
        seen
    )
