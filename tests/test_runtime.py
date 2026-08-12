"""Runtime loop, lifecycle events, failure handling, budget enforcement."""

import gc
import weakref

import pytest

from activegraph import (
    FrozenClock,
    Graph,
    IDGen,
    RecordingSink,
    Runtime,
    RuntimeClosedError,
    behavior,
    relation_behavior,
)
from activegraph.runtime._live import live_runtimes


def _g():
    return Graph(ids=IDGen(), clock=FrozenClock())


def test_runtime_emits_lifecycle_events_around_behavior():
    @behavior(name="noop", on=["goal.created"])
    def noop(event, graph, ctx):
        pass

    g = _g()
    Runtime(g).run_goal("hello")
    types = [e.type for e in g.events]
    assert "goal.created" in types
    assert "behavior.started" in types
    assert "behavior.completed" in types
    assert types[-1] == "runtime.idle"


def test_behavior_failure_emits_behavior_failed_and_loop_continues():
    @behavior(name="boom", on=["goal.created"])
    def boom(event, graph, ctx):
        raise ValueError("kaboom")

    @behavior(name="after", on=["goal.created"])
    def after(event, graph, ctx):
        graph.add_object("marker", {"ok": True})

    g = _g()
    Runtime(g).run_goal("test")

    types = [e.type for e in g.events]
    assert "behavior.failed" in types
    # The second behavior still ran.
    assert any(o.type == "marker" for o in g.all_objects())
    # And the failed event has the right shape.
    failed = next(e for e in g.events if e.type == "behavior.failed")
    assert failed.payload["behavior"] == "boom"
    assert failed.payload["exception_type"] == "ValueError"
    assert failed.payload["message"] == "kaboom"
    assert "Traceback" in failed.payload["traceback"]


def test_budget_exhaustion_emits_runtime_budget_exhausted():
    @behavior(name="loop", on=["goal.created", "ping.tick"])
    def loop(event, graph, ctx):
        graph.emit("ping.tick", {"n": 1})

    g = _g()
    Runtime(g, budget={"max_events": 5}).run_goal("test")
    last = g.events[-1]
    assert last.type == "runtime.budget_exhausted"
    assert last.payload["exhausted_by"] == "max_events"


def test_relation_behavior_fires_per_matching_edge():
    fired = []

    @relation_behavior(name="watch", relation_type="depends_on", on=["task.completed"])
    def watch(rel, event, graph, ctx):
        fired.append((rel.source, rel.target))

    g = _g()
    a = g.add_object("task", {})
    b = g.add_object("task", {})
    c = g.add_object("task", {})
    g.add_relation(a.id, b.id, "depends_on")
    g.add_relation(a.id, c.id, "depends_on")
    runtime = Runtime(g)
    g.emit_event_kwargs = None  # noqa
    # Use the convenience emit on graph through behavior_graph isn't accessible
    # outside; use propose path: just hand-emit a custom event.
    from activegraph import Event

    g.emit(
        Event(
            id=g.ids.event(),
            type="task.completed",
            payload={"task_id": a.id},
            actor="user",
            timestamp=g.clock.now(),
        )
    )
    runtime.run_until_idle()
    assert sorted(fired) == sorted([(a.id, b.id), (a.id, c.id)])


def test_explicit_behaviors_arg_overrides_global_registry():
    @behavior(name="from_registry", on=["goal.created"])
    def fr(event, graph, ctx):
        graph.add_object("marker", {"src": "registry"})

    @behavior(name="explicit", on=["goal.created"])
    def ex(event, graph, ctx):
        graph.add_object("marker", {"src": "explicit"})

    g = _g()
    Runtime(g, behaviors=[ex]).run_goal("test")
    sources = [o.data.get("src") for o in g.all_objects()]
    assert sources == ["explicit"]


def test_print_graph_runs_without_error(capsys):
    @behavior(name="planner", on=["goal.created"])
    def planner(event, graph, ctx):
        graph.add_object("task", {"title": "x", "status": "open"})

    g = _g()
    r = Runtime(g)
    r.run_goal("hi")
    r.print_graph()
    out = capsys.readouterr().out
    assert "graph:" in out
    assert "task#" in out


def test_successful_close_untracks_runtime_and_allows_read_only_surfaces():
    graph = _g()
    runtime = Runtime(graph, behaviors=[])
    assert runtime in live_runtimes()

    assert runtime.close() is True
    assert runtime not in live_runtimes()
    assert runtime.run_id == graph.run_id
    assert runtime.errors == []
    assert runtime.status().run_id == graph.run_id
    assert runtime.sink_statuses() == ()
    assert runtime.dev_overrides() == []
    assert runtime.authority_ceiling() == "none"
    assert runtime.loaded_packs() == []
    assert runtime.pending_approvals() == []
    assert runtime.trace.lines() == []

    runtime_ref = weakref.ref(runtime)
    del runtime
    del graph
    gc.collect()
    assert runtime_ref() is None


def test_closed_runtime_rejects_mutating_entry_points():
    runtime = Runtime(_g(), behaviors=[])
    other = Runtime(_g(), behaviors=[])
    assert runtime.close() is True

    mutators = (
        lambda: runtime.add_sink(RecordingSink()),
        lambda: runtime.remove_sink("missing"),
        lambda: runtime.dev_override(
            actor="developer",
            reason="test",
            target_gate="tool.execute",
            scope="one",
            resulting_authority="R0",
        ),
        lambda: runtime.set_authority_ceiling("R0", actor="test", reason="test"),
        lambda: runtime.evaluate_capability_authority(
            capability="test", action_class="R0"
        ),
        lambda: runtime.run_goal("too late"),
        runtime.run_until_idle,
        runtime.run_quantum,
        lambda: runtime.run_until(lambda graph: True),
        lambda: runtime.embed(["too late"], model="test"),
        lambda: runtime.load_pack(object()),
        lambda: runtime.disable_pack("missing"),
        lambda: runtime.approve("missing"),
        lambda: runtime.save_state("unused.db"),
        lambda: runtime.fork("evt_missing"),
        lambda: runtime.promote(other),
    )
    try:
        for mutate in mutators:
            with pytest.raises(RuntimeClosedError):
                mutate()
    finally:
        other.close()
