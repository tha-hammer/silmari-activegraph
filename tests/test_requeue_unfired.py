"""Regression tests for `_requeue_unfired` — CONTRACT v1.0-rc2 finding C3.

The bug, latent since CONTRACT v0.5 #8: `_requeue_unfired` treats
"no behavior.started event references this event id" as equivalent
to "this event was still in the queue when the runtime stopped". The
reverse implication is false — the runtime pops every event and fans
out to subscribed behaviors, but an event with **zero** subscribed
behaviors is popped-and-discarded with no behavior.started emitted.
In a real run, the majority of events (llm.requested, llm.responded,
tool.requested, tool.responded, relation.created, patch.applied,
downstream object.created) have no subscribers, so the entire log
got falsely requeued on `Runtime.load`.

The bug surface is `runtime.status().queue_depth` reading nonzero
on a freshly loaded cleanly-drained run. The fix: use the last
`runtime.idle` as the high-water mark; only live-scheduling-eligible
events emitted after that drain are candidates for requeue.
`runtime.budget_exhausted` is deliberately not a drain marker.

These tests would have failed under the old `_requeue_unfired` and
lock the regression vector.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from activegraph import Graph, Runtime, behavior, clear_registry


def _register_subscriber_for_goal_only():
    """A single behavior that subscribes to goal.created and emits a
    custom event nothing else subscribes to. The custom event would be
    falsely requeued under the pre-fix `_requeue_unfired`.
    """
    clear_registry()

    @behavior(name="emitter", on=["goal.created"])
    def emitter(event, graph, ctx):
        # Emit an event with zero subscribers — this is the shape that
        # made queue_depth read nonzero on saved runs.
        graph.emit("downstream.no_subscribers", {"note": "nothing listens"})
        graph.emit("downstream.no_subscribers", {"note": "still nothing"})


class TestRequeueUnfiredDoesNotFalselyRequeue:
    def test_queue_depth_is_zero_after_load_of_cleanly_drained_run(self):
        """A clean run (drained to runtime.idle) loads with queue_depth=0.

        Pre-fix behavior on this graph: queue_depth would be 2 (the two
        `downstream.no_subscribers` events get falsely requeued because
        they have no behavior.started referencing them).
        """
        _register_subscriber_for_goal_only()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        os.remove(path)
        try:
            g = Graph()
            rt = Runtime(g, persist_to=path)
            rt.run_goal("test_goal")
            rt.save_state()
            assert rt.status().state == "idle"
            assert rt.status().queue_depth == 0

            _register_subscriber_for_goal_only()
            rt2 = Runtime.load(path, run_id=rt.run_id)
            assert rt2.status().state == "idle"
            assert rt2.status().queue_depth == 0, (
                f"queue_depth should be 0 after loading a cleanly drained run; "
                f"got {rt2.status().queue_depth}. The runtime emitted "
                f"`runtime.idle` indicating the queue was empty when it stopped; "
                f"any nonzero queue_depth on load means events that had no "
                f"subscribers were falsely requeued (CONTRACT v1.0-rc2 finding "
                f"C3, latent since v0.5 #8)."
            )
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except FileNotFoundError:
                    pass

    def test_log_contains_an_event_no_behavior_started_referenced(self):
        """Pre-condition check that the saved log actually exhibits the
        bug shape: at least one non-lifecycle event has no
        `behavior.started` referencing it. Without this pre-condition,
        the queue_depth==0 assertion above would pass trivially against
        a log that doesn't exercise the bug.
        """
        _register_subscriber_for_goal_only()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        os.remove(path)
        try:
            g = Graph()
            rt = Runtime(g, persist_to=path)
            rt.run_goal("test_goal")
            rt.save_state()

            _register_subscriber_for_goal_only()
            rt2 = Runtime.load(path, run_id=rt.run_id)
            fired_on = set()
            for e in rt2.graph.events:
                if e.type.startswith("behavior.") or e.type.startswith("relation_behavior."):
                    eid = e.payload.get("event_id") if isinstance(e.payload, dict) else None
                    if eid:
                        fired_on.add(eid)
            popped_no_subscriber = [
                e for e in rt2.graph.events
                if e.id not in fired_on
                and not e.type.startswith("behavior.")
                and not e.type.startswith("relation_behavior.")
                and not e.type.startswith("runtime.")
            ]
            assert popped_no_subscriber, (
                "Test fixture is supposed to produce at least one event with "
                "no behavior.started reference (the `downstream.no_subscribers` "
                "events). Without this, the C3 regression test trivially passes."
            )
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except FileNotFoundError:
                    pass

    def test_zero_subscriber_event_ids_are_absent_from_requeue_set_on_load(self):
        """Boundary-anchored sibling to the queue_depth assertion above.

        CONTRACT v0.5 #8 names the boundary as `Runtime.load` and the
        subject as the requeue set itself: "events with NO
        `behavior.started` referencing them are re-queued; events that
        any behavior started on are NOT re-queued." A zero-subscriber
        event has no `behavior.started` referencing it but is
        popped-and-discarded by the runtime loop — so the contract
        carve-out is that zero-subscriber events are NOT in the
        requeue set on load.

        The sibling test above asserts `queue_depth == 0` (the
        implementation's symptom). This test asserts directly on the
        boundary the contract names: it identifies the zero-subscriber
        event IDs from the saved log, reloads, and verifies those IDs
        are absent from `rt._queue` after `_requeue_unfired` ran on
        load. Anchors on what the contract says, not on whether the
        post-load queue depth happens to be zero (CONTRACT v1.0.4 #4,
        Standing Rule §2).
        """
        _register_subscriber_for_goal_only()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        os.remove(path)
        try:
            g = Graph()
            rt = Runtime(g, persist_to=path)
            rt.run_goal("test_goal")
            rt.save_state()

            zero_subscriber_event_ids = {
                e.id for e in rt.graph.events
                if e.type == "downstream.no_subscribers"
            }
            assert zero_subscriber_event_ids, (
                "Fixture is supposed to produce at least one "
                "downstream.no_subscribers event. Without it, the "
                "contract claim's specific subject (the zero-"
                "subscriber carve-out) isn't being exercised."
            )

            _register_subscriber_for_goal_only()
            rt2 = Runtime.load(path, run_id=rt.run_id)

            requeued_event_ids = {e.id for e in rt2._queue._q}  # noqa: SLF001
            falsely_requeued = zero_subscriber_event_ids & requeued_event_ids
            assert not falsely_requeued, (
                f"CONTRACT v0.5 #8 carve-out: zero-subscriber events are "
                f"popped-and-discarded with no behavior.started emitted, "
                f"so on Runtime.load they must NOT appear in the requeue "
                f"set. The following zero-subscriber event IDs were "
                f"falsely requeued: {sorted(falsely_requeued)}. This is "
                f"the v0.5 #8 boundary the contract names; failure here "
                f"means the requeue set itself violates the contract "
                f"regardless of what queue_depth summarizes."
            )
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except FileNotFoundError:
                    pass


class TestRequeueUnfiredPreservesCrashRecovery:
    """The fix narrows the requeue scope to events after the last drain.
    A runtime that crashes BEFORE any drain still requeues all events
    (the pre-drain history is the candidate set). Verifies the fix
    didn't regress the v0.5 #8 crash-recovery contract.
    """

    def test_no_drain_falls_back_to_full_event_scan(self):
        """If no runtime.idle was ever emitted, the function still
        considers all events as requeue candidates. Simulates a runtime
        that crashed mid-loop on its first goal.

        Building this scenario requires hand-constructing a saved log
        without a terminal lifecycle event; we use the store interface
        directly rather than the runtime's save path.
        """
        from activegraph.core.event import Event
        from activegraph.runtime.runtime import _requeue_unfired

        clear_registry()
        g = Graph()
        rt = Runtime(g)

        # Hand-build events with no drain marker. The post-drain check
        # should fall back to scanning the full list.
        e1 = Event(
            id="evt_001",
            type="custom.unprocessed",
            payload={},
            actor="test",
            frame_id=None,
            caused_by=None,
            timestamp="2026-01-01T00:00:00Z",
        )
        events = [e1]
        _requeue_unfired(rt, events)
        assert len(rt._queue) == 1, (
            "When no drain lifecycle event exists in the log, the full "
            "event scan must still consider unprocessed events as requeue "
            "candidates (preserves v0.5 #8 crash-recovery contract)."
        )

    def test_post_drain_unprocessed_events_are_requeued(self):
        """If events were emitted after the last drain but before the
        runtime stopped, those events ARE candidates for requeue —
        the suffix check still applies the original fired_on filter.
        """
        from activegraph.core.event import Event
        from activegraph.runtime.runtime import _requeue_unfired

        clear_registry()
        g = Graph()
        rt = Runtime(g)

        drain = Event(
            id="evt_001",
            type="runtime.idle",
            payload={},
            actor="runtime",
            frame_id=None,
            caused_by=None,
            timestamp="2026-01-01T00:00:00Z",
        )
        post_drain = Event(
            id="evt_002",
            type="custom.post_drain",
            payload={},
            actor="test",
            frame_id=None,
            caused_by=None,
            timestamp="2026-01-01T00:00:01Z",
        )
        events = [drain, post_drain]
        _requeue_unfired(rt, events)
        assert len(rt._queue) == 1

    def test_post_drain_requeue_matches_live_scheduling_policy(
        self, tmp_path
    ):
        from activegraph.core.event import Event

        clear_registry()
        path = str(tmp_path / "policy.db")
        runtime = Runtime(Graph(), behaviors=[], persist_to=path)
        runtime.run_goal("baseline")
        event_types = [
            "behavior.started",
            "relation_behavior.completed",
            "runtime.budget_exhausted",
            "llm.requested",
            "tool.responded",
            "pattern.matched",
            "approval.proposed",
            "embedding.requested",
            "dev.override",
            "authority.decision",
            "context.read",
            "context.foo",
            "pack.loaded",
            "goal.created",
            "object.created",
            "custom.event",
            "runtimeish.event",
        ]
        visible_types = [
            "context.foo",
            "pack.loaded",
            "goal.created",
            "object.created",
            "custom.event",
            "runtimeish.event",
        ]
        store = runtime.graph.store
        assert store is not None
        for index, event_type in enumerate(event_types):
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
            store.append(
                Event(id=f"evt_policy_{index}", type=event_type, payload=payload)
            )

        fired: list[str] = []

        @behavior(name="resume-subscriber", on=visible_types)
        def resume_subscriber(event, graph, ctx):
            fired.append(event.type)

        loaded = Runtime.load(path, behaviors=[resume_subscriber])
        assert list(event.type for event in loaded._queue._q) == visible_types

        loaded.run_until_idle()

        assert fired == visible_types
        assert len(loaded._queue) == 0

    def test_budget_fired_on_and_promote_resume_invariants(self, tmp_path):
        from activegraph.core.event import Event

        clear_registry()
        path = str(tmp_path / "resume-invariants.db")
        runtime = Runtime(Graph(), behaviors=[], persist_to=path)
        runtime.run_goal("baseline")
        store = runtime.graph.store
        assert store is not None
        suffix = [
            Event(id="evt_before", type="custom.before_budget"),
            Event(id="evt_budget", type="runtime.budget_exhausted"),
            Event(id="evt_after", type="custom.after_budget"),
            Event(id="evt_fired", type="custom.already_fired"),
            Event(
                id="evt_started",
                type="behavior.started",
                payload={"event_id": "evt_fired"},
            ),
            Event(
                id="evt_promoted",
                type="custom.promoted",
                actor="promote:test",
            ),
        ]
        for event in suffix:
            store.append(event)

        loaded = Runtime.load(path, behaviors=[])

        assert [event.type for event in loaded._queue._q] == [
            "custom.before_budget",
            "custom.after_budget",
        ]
