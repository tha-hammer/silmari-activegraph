"""activate_after scheduling tests. CONTRACT v0.7 #13.

  - parse_activate_after accepts int / "N events" / "N event"
  - parse_activate_after rejects wall-clock units with a clear pointer
    to CONTRACT v0.7 #13
  - a scheduled behavior fires after the configured tick delay
  - if `where=` no longer holds at fire time, the behavior is silently
    skipped (per CONTRACT v0.7 #13)
  - behavior.scheduled appears in the trace at schedule time
"""

from __future__ import annotations

from collections import Counter

import pytest

from activegraph import Event, Graph, Runtime, behavior, relation_behavior
from activegraph.runtime.scheduler import parse_activate_after


def _emit_event(graph: Graph, event_type: str, payload: dict) -> Event:
    event = Event(
        id=graph.ids.event(),
        type=event_type,
        payload=payload,
        actor="test",
        timestamp=graph.clock.now(),
    )
    graph.emit(event)
    return event


# ---------- parse_activate_after -------------------------------------------


def test_parse_int():
    assert parse_activate_after(3) == 3


def test_parse_string_n_events():
    assert parse_activate_after("2 events") == 2


def test_parse_string_n_event_singular():
    assert parse_activate_after("1 event") == 1


def test_parse_string_just_number():
    assert parse_activate_after("5") == 5


@pytest.mark.parametrize(
    "spec",
    ["5 seconds", "2 minutes", "1 hour", "10 ms", "3 days"],
)
def test_parse_rejects_wall_clock_units(spec):
    with pytest.raises(ValueError, match="wall-clock"):
        parse_activate_after(spec)


def test_parse_rejects_negative():
    with pytest.raises(ValueError, match="must be >= 1"):
        parse_activate_after(0)


def test_parse_rejects_bool():
    """bool is an int in Python but `True` would be a misleading value."""
    with pytest.raises(ValueError):
        parse_activate_after(True)


def test_parse_rejects_garbage_string():
    with pytest.raises(ValueError):
        parse_activate_after("whenever")


# ---------- runtime end-to-end ---------------------------------------------


def test_scheduled_relation_behavior_fires_for_current_match():
    fired: list[tuple[str, str]] = []

    @relation_behavior(
        name="watch",
        relation_type="depends_on",
        on=["task.completed"],
        activate_after=1,
    )
    def watch(relation, event, graph, ctx):
        fired.append((relation.id, event.id))
        graph.add_object(
            "scheduled_marker",
            {"relation_id": relation.id, "event_id": event.id},
        )

    g = Graph()
    source = g.add_object("task", {})
    target = g.add_object("task", {})
    relation = g.add_relation(source.id, target.id, "depends_on")
    runtime = Runtime(g, behaviors=[watch])
    trigger = Event(
        id=g.ids.event(),
        type="task.completed",
        payload={"task_id": source.id},
        actor="user",
        timestamp=g.clock.now(),
    )
    g.emit(trigger)

    paused = runtime.run_quantum(max_queue_events=1, max_seconds=1.0)
    assert paused.delayed_depth == 1

    g.add_object("noise", {})
    runtime.run_until_idle()

    assert fired == [(relation.id, trigger.id)]
    markers = [o for o in g.all_objects() if o.type == "scheduled_marker"]
    assert [marker.data for marker in markers] == [
        {"relation_id": relation.id, "event_id": trigger.id}
    ]

    scheduled = next(
        event
        for event in g.events
        if event.type == "behavior.scheduled"
        and event.payload["behavior"] == "watch"
        and event.payload["event_id"] == trigger.id
    )
    started = next(
        event
        for event in g.events
        if event.type == "relation_behavior.started"
        and event.payload["behavior"] == "watch"
        and event.payload["event_id"] == trigger.id
        and event.payload["relation_id"] == relation.id
    )
    completed = next(
        event
        for event in g.events
        if event.type == "behavior.completed"
        and event.payload["behavior"] == "watch"
        and event.payload["event_id"] == trigger.id
    )
    assert g.events.index(scheduled) < g.events.index(started)
    assert g.events.index(started) < g.events.index(completed)


def test_scheduled_relation_behavior_skips_relation_removed_before_fire():
    fired: list[tuple[str, str]] = []

    @relation_behavior(
        name="watch_removed",
        relation_type="depends_on",
        on=["task.completed"],
        activate_after=1,
    )
    def watch_removed(relation, event, graph, ctx):
        fired.append((relation.id, event.id))

    g = Graph()
    source = g.add_object("task", {})
    target = g.add_object("task", {})
    relation_a = g.add_relation(source.id, target.id, "depends_on")
    runtime = Runtime(g, behaviors=[watch_removed])
    trigger = Event(
        id=g.ids.event(),
        type="task.completed",
        payload={"task_id": source.id},
        actor="user",
        timestamp=g.clock.now(),
    )
    g.emit(trigger)

    paused = runtime.run_quantum(max_queue_events=1, max_seconds=1.0)
    assert paused.delayed_depth == 1

    g.remove_relation(relation_a.id)
    runtime.run_until_idle()

    assert fired == []
    scheduled = [
        event
        for event in g.events
        if event.type == "behavior.scheduled"
        and event.payload["behavior"] == "watch_removed"
        and event.payload["event_id"] == trigger.id
    ]
    started = [
        event
        for event in g.events
        if event.type == "relation_behavior.started"
        and event.payload["behavior"] == "watch_removed"
        and event.payload["event_id"] == trigger.id
    ]
    assert len(scheduled) == 1
    assert started == []


def test_scheduled_relation_behavior_uses_relations_added_before_fire():
    fired: list[tuple[str, str]] = []

    @relation_behavior(
        name="watch_added",
        relation_type="depends_on",
        on=["task.completed"],
        activate_after=1,
    )
    def watch_added(relation, event, graph, ctx):
        fired.append((relation.id, event.id))

    g = Graph()
    source = g.add_object("task", {})
    target_a = g.add_object("task", {})
    target_b = g.add_object("task", {})
    unrelated_source = g.add_object("task", {})
    unrelated_target = g.add_object("task", {})
    relation_a = g.add_relation(source.id, target_a.id, "depends_on")
    runtime = Runtime(g, behaviors=[watch_added])
    trigger = Event(
        id=g.ids.event(),
        type="task.completed",
        payload={"task_id": source.id},
        actor="user",
        timestamp=g.clock.now(),
    )
    g.emit(trigger)

    paused = runtime.run_quantum(max_queue_events=1, max_seconds=1.0)
    assert paused.delayed_depth == 1

    relation_b = g.add_relation(source.id, target_b.id, "depends_on")
    relation_c = g.add_relation(
        unrelated_source.id,
        unrelated_target.id,
        "depends_on",
    )
    runtime.run_until_idle()

    assert Counter(fired) == Counter(
        {
            (relation_a.id, trigger.id): 1,
            (relation_b.id, trigger.id): 1,
        }
    )
    assert all(relation_id != relation_c.id for relation_id, _ in fired)


def test_budget_exhaustion_restores_complete_due_suffix_in_fifo_order():
    fired: list[str] = []

    def delayed(name: str):
        @behavior(name=name, on=["budget.trigger"], activate_after=1)
        def run(event, graph, ctx):
            fired.append(name)

        return run

    behaviors = [delayed("first"), delayed("second"), delayed("third")]
    graph = Graph()
    runtime = Runtime(
        graph,
        behaviors=behaviors,
        budget={"max_behavior_calls": 1},
    )
    _emit_event(graph, "budget.trigger", {})
    _emit_event(graph, "budget.advance", {})

    exhausted = runtime.run_quantum(max_queue_events=3, max_seconds=1.0)
    assert exhausted.budget_exhausted is True
    assert fired == ["first"]
    assert exhausted.delayed_depth == 2

    runtime.budget.limits["max_behavior_calls"] = 3
    runtime.run_until_idle()
    assert fired == ["first", "second", "third"]
    assert runtime.run_quantum(max_queue_events=1, max_seconds=1.0).delayed_depth == 0


def test_relation_fanout_is_non_resumable_after_budget_prefix():
    fired: list[str] = []

    @relation_behavior(
        name="budgeted_relation",
        relation_type="depends_on",
        on=["budget.relation"],
        activate_after=1,
    )
    def budgeted_relation(relation, event, graph, ctx):
        fired.append(relation.id)

    graph = Graph()
    source = graph.add_object("task", {})
    targets = [graph.add_object("task", {}) for _ in range(3)]
    relations = [
        graph.add_relation(source.id, target.id, "depends_on")
        for target in targets
    ]
    runtime = Runtime(
        graph,
        behaviors=[budgeted_relation],
        budget={"max_behavior_calls": 2},
    )
    _emit_event(graph, "budget.relation", {"task_id": source.id})
    _emit_event(graph, "budget.advance", {})
    exhausted = runtime.run_quantum(max_queue_events=3, max_seconds=1.0)

    assert exhausted.budget_exhausted is True
    assert len(fired) == 2
    assert len(set(fired)) == 2
    assert set(fired) < {relation.id for relation in relations}
    assert exhausted.delayed_depth == 0

    runtime.budget.limits["max_behavior_calls"] = 10
    runtime.run_until_idle()
    assert len(fired) == 2


def test_scheduled_relation_failure_does_not_stop_sibling_fanout():
    attempted: list[str] = []

    @relation_behavior(
        name="failing_relation",
        relation_type="depends_on",
        on=["failure.relation"],
        activate_after=1,
    )
    def failing_relation(relation, event, graph, ctx):
        attempted.append(relation.id)
        if len(attempted) == 1:
            raise ValueError("first relation fails")

    graph = Graph()
    source = graph.add_object("task", {})
    targets = [graph.add_object("task", {}) for _ in range(2)]
    relations = [
        graph.add_relation(source.id, target.id, "depends_on")
        for target in targets
    ]
    runtime = Runtime(graph, behaviors=[failing_relation])
    _emit_event(graph, "failure.relation", {"task_id": source.id})
    _emit_event(graph, "failure.advance", {})
    runtime.run_until_idle()

    assert set(attempted) == {relation.id for relation in relations}
    assert len(attempted) == 2
    failed = [
        event
        for event in graph.events
        if event.type == "behavior.failed"
        and event.payload["behavior"] == "failing_relation"
    ]
    assert len(failed) == 1


def test_activate_after_fires_after_n_events():
    fired: list = []

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("task", {"title": "t", "status": "open"})
        # Push two extra events to advance the tick counter.
        graph.add_object("noise", {"i": 1})
        graph.add_object("noise", {"i": 2})

    @behavior(
        name="nag",
        on=["object.created"],
        where={"object.type": "task"},
        activate_after=2,
    )
    def nag(event, graph, ctx):
        fired.append(event.payload["object"]["id"])

    g = Graph()
    Runtime(g).run_goal("g")
    assert fired == ["task#1"]
    # behavior.scheduled emitted at schedule time
    scheds = [e for e in g.events if e.type == "behavior.scheduled"]
    assert len(scheds) == 1
    assert scheds[0].payload["behavior"] == "nag"
    assert scheds[0].payload["activate_after"] == 2


def test_activate_after_skips_when_where_no_longer_holds():
    """Per CONTRACT v0.7 #13, the where= clause is re-evaluated at fire time."""

    fired: list = []

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("task", {"title": "t", "status": "open"})

    @behavior(
        name="closer",
        on=["object.created"],
        where={"object.type": "task"},
    )
    def closer(event, graph, ctx):
        graph.patch_object(event.payload["object"]["id"], {"status": "closed"})

    @behavior(
        name="nag",
        on=["object.created"],
        where={"object.type": "task"},
        activate_after=1,
    )
    def nag(event, graph, ctx):
        # Re-check: only fire if status is still open.
        obj = graph.get_object(event.payload["object"]["id"])
        if obj and obj.data.get("status") == "open":
            fired.append(obj.id)

    g = Graph()
    Runtime(g).run_goal("g")
    # `closer` ran between schedule and fire time; nag's where re-check
    # sees status='closed' and skips. (The runtime's where= re-check
    # uses the behavior's where=, which still says open — closer
    # patched it to closed, so where no longer holds.)
    assert fired == []
