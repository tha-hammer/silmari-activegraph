"""Replay semantics — projection-only rebuild, plus replay_strict for
detecting non-determinism."""

from __future__ import annotations

import pytest

from activegraph import (
    FrozenClock,
    Graph,
    ReplayDivergenceError,
    Runtime,
    behavior,
    clear_registry,
)


def _tmp_db(tmp_path):
    return str(tmp_path / "run.db")


def test_replay_rebuilds_graph_without_firing_behaviors(tmp_path):
    """Replay populates _events and projects, but does not run behaviors.
    We assert this by giving the loaded runtime a behavior that would mutate
    the graph if it fired during replay — and verifying it doesn't.
    """
    db = _tmp_db(tmp_path)

    @behavior(name="noop_planner", on=["goal.created"])
    def planner(event, graph, ctx):
        graph.add_object("task", {"title": "t"})

    rt = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt.run_goal("x")
    saved_count = len(rt.graph.all_objects())

    # Re-register a behavior that would ADD an object if it fired during replay.
    side_effects = []

    @behavior(name="spy", on=["goal.created"])
    def spy(event, graph, ctx):
        side_effects.append("fired")

    loaded = Runtime.load(db, run_id=rt.run_id)
    # Replay finished. Spy must NOT have fired.
    assert side_effects == []
    # Object count matches the saved graph.
    assert len(loaded.graph.all_objects()) == saved_count


def test_replay_marks_events_with_replay_flag(tmp_path):
    db = _tmp_db(tmp_path)

    @behavior(name="p", on=["goal.created"])
    def p(event, graph, ctx):
        graph.add_object("x", {})

    rt = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt.run_goal("x")
    n = len(rt.graph.events)

    loaded = Runtime.load(db, run_id=rt.run_id)
    assert len(loaded.graph.replayed_ids) == n
    for e in loaded.graph.events:
        assert e.id in loaded.graph.replayed_ids


def test_replay_strict_deterministic_run_passes(tmp_path):
    """A deterministic run should reload with replay_strict=True without
    raising. We don't currently re-verify when run_id has no seed events
    other than goal.created — this asserts the happy path."""
    db = _tmp_db(tmp_path)

    @behavior(name="det", on=["goal.created"])
    def det(event, graph, ctx):
        graph.add_object("claim", {"text": "fixed text"})

    rt = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt.run_goal("Evaluate")

    Runtime.load(db, run_id=rt.run_id, replay_strict=True)


def test_replay_strict_non_deterministic_behavior_raises(tmp_path):
    """Behavior that uses ctx.random differently on re-run → divergence.
    We force divergence by mutating module-level state between runs."""
    import random as _r

    db = _tmp_db(tmp_path)

    toggle = {"value": "first"}

    @behavior(name="reads_module_state", on=["goal.created"])
    def b(event, graph, ctx):
        if toggle["value"] == "first":
            graph.add_object("a", {})
            graph.add_object("a", {})
        else:
            # Different number of events on re-run → strict replay will catch it.
            graph.add_object("b", {})

    rt = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt.run_goal("x")

    toggle["value"] = "second"
    from tests.test_observability_metrics import RecordingMetrics

    metrics = RecordingMetrics()
    with pytest.raises(ReplayDivergenceError) as excinfo:
        Runtime.load(
            db,
            run_id=rt.run_id,
            replay_strict=True,
            metrics=metrics,
        )
    # The error pins an event id so the operator knows where it diverged.
    assert excinfo.value.event_id.startswith("evt_")
    assert excinfo.value.kind == "length_mismatch"
    assert metrics.values(
        "counter",
        "activegraph_replay_divergence_detected_total",
        {"reason": "length_mismatch"},
    ) == [1.0]


def test_replay_strict_type_mismatch_records_one_metric(tmp_path):
    db = str(tmp_path / "type-mismatch.db")
    toggle = {"first": True}

    @behavior(name="changes_event_type", on=["goal.created"])
    def changes_event_type(event, graph, ctx):
        if toggle["first"]:
            graph.add_object("recorded_type", {})
        else:
            graph.emit("live.type", {})

    runtime = Runtime(Graph(clock=FrozenClock()), behaviors=[changes_event_type], persist_to=db)
    runtime.run_goal("x")
    toggle["first"] = False
    from tests.test_observability_metrics import RecordingMetrics

    metrics = RecordingMetrics()
    with pytest.raises(ReplayDivergenceError) as exc_info:
        Runtime.load(
            db,
            run_id=runtime.run_id,
            behaviors=[changes_event_type],
            replay_strict=True,
            metrics=metrics,
        )
    assert exc_info.value.kind == "type_mismatch"
    assert metrics.values(
        "counter",
        "activegraph_replay_divergence_detected_total",
        {"reason": "type_mismatch"},
    ) == [1.0]


@pytest.mark.parametrize(
    "event_type",
    [
        "llm.requested",
        "tool.responded",
        "pattern.matched",
        "approval.proposed",
        "embedding.requested",
        "dev.override",
        "authority.decision",
        "context.foo",
        "pack.loaded",
        "goal.created",
        "object.created",
        "custom.event",
        "runtimeish.event",
    ],
)
def test_strict_replay_includes_nonstructural_history(tmp_path, event_type):
    from activegraph.core.event import Event
    from activegraph.store.sqlite import SQLiteEventStore

    clear_registry()

    @behavior(name="det", on=["goal.created"])
    def det(event, graph, ctx):
        graph.add_object("claim", {"text": "stable"})

    db = _tmp_db(tmp_path)
    runtime = Runtime(
        Graph(clock=FrozenClock()), behaviors=[det], persist_to=db
    )
    runtime.run_goal("go")
    goal = next(event for event in runtime.graph.events if event.type == "goal.created")
    payload = {}
    if event_type == "object.created":
        payload = {
            "object": {
                "id": "probe#1",
                "type": "probe",
                "data": {},
                "version": 1,
                "provenance": {},
            }
        }
    SQLiteEventStore(db, run_id=runtime.run_id).append(
        Event(
            id="evt_injected",
            type=event_type,
            payload=payload,
            caused_by=goal.id,
            timestamp=runtime.graph.clock.now(),
        )
    )

    with pytest.raises(ReplayDivergenceError):
        Runtime.load(
            db, run_id=runtime.run_id, behaviors=[det], replay_strict=True
        )


@pytest.mark.parametrize(
    "event_type",
    [
        "behavior.started",
        "relation_behavior.completed",
        "runtime.idle",
        "runtime.budget_exhausted",
        "context.read",
    ],
)
def test_strict_replay_excludes_structural_and_context_read_history(
    tmp_path, event_type
):
    from activegraph.core.event import Event
    from activegraph.store.sqlite import SQLiteEventStore

    db = _tmp_db(tmp_path)

    @behavior(name="det", on=["goal.created"])
    def det(event, graph, ctx):
        graph.add_object("claim", {"text": "stable"})

    runtime = Runtime(
        Graph(clock=FrozenClock()), behaviors=[det], persist_to=db
    )
    runtime.run_goal("go")
    goal = next(event for event in runtime.graph.events if event.type == "goal.created")
    SQLiteEventStore(db, run_id=runtime.run_id).append(
        Event(
            id="evt_injected",
            type=event_type,
            caused_by=goal.id,
            timestamp=runtime.graph.clock.now(),
        )
    )

    Runtime.load(db, run_id=runtime.run_id, behaviors=[det], replay_strict=True)


def test_replay_emits_no_store_writes(tmp_path):
    """Critical invariant: replay must not append events back to the store
    (would duplicate them). Verify by event count before/after load."""
    from activegraph.store.sqlite import SQLiteEventStore

    db = _tmp_db(tmp_path)

    @behavior(name="p", on=["goal.created"])
    def p(event, graph, ctx):
        graph.add_object("x", {})

    rt = Runtime(Graph(clock=FrozenClock()), persist_to=db)
    rt.run_goal("x")
    before = SQLiteEventStore(db, run_id=rt.run_id).count()

    loaded = Runtime.load(db, run_id=rt.run_id)
    after = SQLiteEventStore(db, run_id=rt.run_id).count()
    assert before == after
