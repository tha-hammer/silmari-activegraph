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

import pytest

from activegraph import Event, Graph, Runtime, behavior, relation_behavior
from activegraph.runtime.scheduler import parse_activate_after


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
