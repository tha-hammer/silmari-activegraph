"""EventStore conformance suite. CONTRACT v0.8 #17.

A reusable pytest-compatible base class that exercises any EventStore
implementation against the protocol. Concrete subclasses override
``make_store(run_id)`` and ``cleanup()``; the tests run identically.

The InMemory, SQLite, and (with testcontainers) Postgres stores all run
through this suite. Any future store implementation gets free coverage
by subclassing.

The suite intentionally avoids pytest fixtures and yields plain
``unittest.TestCase``-style methods, so it works under any test runner.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pytest

from activegraph.core.event import Event
from activegraph.store.errors import DuplicateEventError


class EventStoreConformance(ABC):
    """Mix into a pytest test class to inherit the full suite.

    Subclasses MUST implement:

        def make_store(self, run_id: str) -> EventStore: ...
        def make_second_store(self, run_id: str) -> EventStore: ...
        def close_store(self, store: EventStore) -> None: ...
        def cleanup(self) -> None: ...

    Tests are method names starting with ``test_``. They are picked up
    by pytest automatically when the concrete subclass is collected.

    The base class is abstract (no ``test_`` methods callable on it)
    by virtue of ``make_store`` raising on the abstract class. pytest
    will still try to collect it; the ``__test__ = False`` attribute
    prevents that.
    """

    __test__ = False  # do not collect the base; subclasses override.

    backend_name: str

    @abstractmethod
    def make_store(self, run_id: str) -> Any:
        """Return a fresh EventStore for ``run_id``. Called per test."""

    @abstractmethod
    def make_second_store(self, run_id: str) -> Any:
        """Return another handle scoped to a distinct run."""

    @abstractmethod
    def close_store(self, store: Any) -> None:
        """Close one handle opened by the conformance suite."""

    def cleanup(self) -> None:
        """Tear down any resources after a test. Default: no-op."""

    # ---- helpers ----

    def _ev(self, eid: str, type_: str = "object.created", payload: dict | None = None) -> Event:
        return Event(
            id=eid,
            type=type_,
            payload=payload or {"k": "v"},
            actor="test",
            frame_id=None,
            caused_by=None,
            timestamp="2026-01-01T00:00:00Z",
        )

    # ---- the suite ----

    def test_append_then_iter_in_order(self) -> None:
        try:
            store = self.make_store("run_conformance_1")
            for i in range(5):
                store.append(self._ev(f"evt_{i}"))
            ids = [e.id for e in store.iter_events()]
            assert ids == [f"evt_{i}" for i in range(5)]
        finally:
            self.cleanup()

    def test_count(self) -> None:
        try:
            store = self.make_store("run_conformance_2")
            assert store.count() == 0
            store.append(self._ev("evt_a"))
            store.append(self._ev("evt_b"))
            assert store.count() == 2
        finally:
            self.cleanup()

    def test_get_event_known_and_unknown(self) -> None:
        try:
            store = self.make_store("run_conformance_3")
            store.append(self._ev("evt_known"))
            got = store.get_event("evt_known")
            assert got is not None
            assert got.id == "evt_known"
            assert store.get_event("evt_missing") is None
        finally:
            self.cleanup()

    def test_iter_after_skips_inclusive_boundary(self) -> None:
        try:
            store = self.make_store("run_conformance_4")
            for i in range(4):
                store.append(self._ev(f"evt_{i}"))
            tail = [e.id for e in store.iter_events(after="evt_1")]
            assert tail == ["evt_2", "evt_3"]
        finally:
            self.cleanup()

    def test_iter_until_includes_boundary(self) -> None:
        try:
            store = self.make_store("run_conformance_5")
            for i in range(4):
                store.append(self._ev(f"evt_{i}"))
            head = [e.id for e in store.iter_events(until="evt_2")]
            assert head == ["evt_0", "evt_1", "evt_2"]
        finally:
            self.cleanup()

    def test_truncate_after_drops_tail(self) -> None:
        try:
            store = self.make_store("run_conformance_6")
            for i in range(5):
                store.append(self._ev(f"evt_{i}"))
            store.truncate_after("evt_2")
            ids = [e.id for e in store.iter_events()]
            assert ids == ["evt_0", "evt_1", "evt_2"]
        finally:
            self.cleanup()

    def test_payload_round_trip_preserves_structure(self) -> None:
        try:
            store = self.make_store("run_conformance_7")
            payload = {
                "nested": {"k": [1, 2, {"a": "b"}]},
                "unicode": "café — 🚀",
                "empty": [],
                "null_in_value": None,
            }
            store.append(self._ev("evt_payload", payload=payload))
            got = store.get_event("evt_payload")
            assert got is not None
            assert got.payload == payload
        finally:
            self.cleanup()

    def test_duplicate_id_in_same_run_is_rejected(self) -> None:
        store = None
        try:
            store = self.make_store("run_conformance_8")
            event = self._ev("evt_dup")
            store.append(event)
            before = store.get_event(event.id)
            before_count = store.count()

            with pytest.raises(DuplicateEventError) as excinfo:
                store.append(self._ev(event.id, payload={"replacement": True}))

            assert store.get_event(event.id) == before
            assert store.count() == before_count
            assert excinfo.value.context == {
                "event_id": event.id,
                "run_id": store.run_id,
                "backend": self.backend_name,
            }
            assert event.id in str(excinfo.value)
            assert store.run_id in str(excinfo.value)
        finally:
            if store is not None:
                self.close_store(store)
            self.cleanup()

    def test_same_event_id_is_allowed_in_distinct_runs(self) -> None:
        primary = None
        secondary = None
        try:
            primary = self.make_store("run_conformance_distinct_primary")
            secondary = self.make_second_store("run_conformance_distinct_secondary")
            assert secondary.run_id != primary.run_id

            event = self._ev("evt_shared")
            primary.append(event)
            secondary.append(event)

            assert primary.get_event(event.id) is not None
            assert secondary.get_event(event.id) is not None
            assert primary.count() == 1
            assert secondary.count() == 1
        finally:
            if secondary is not None:
                self.close_store(secondary)
            if primary is not None:
                self.close_store(primary)
            self.cleanup()

    def test_close_is_idempotent(self) -> None:
        try:
            store = self.make_store("run_conformance_9")
            store.append(self._ev("evt_a"))
            store.close()
            # Closing twice should not raise.
            store.close()
        finally:
            self.cleanup()


class ForkRunAtomicityConformance:
    """Shared durable-store fork atomicity tests.

    Concrete classes provide exact-run and raw-seeding hooks because the
    rollback case deliberately starts from an orphan event row.
    """

    __test__ = False

    fork_target: Any

    def open_store_exact(self, run_id: str) -> Any:
        raise NotImplementedError

    def register_cleanup_run_id(self, run_id: str) -> None:
        raise NotImplementedError

    def seed_orphan_event(self, event: Event, *, run_id: str) -> None:
        raise NotImplementedError

    def call_fork(
        self,
        *,
        parent_run_id: str,
        new_run_id: str,
        at_event_id: str,
    ) -> int:
        raise NotImplementedError

    def _fork_parent(self, stem: str) -> Any:
        parent = self.make_store(stem)
        parent.upsert_run(
            created_at="2026-01-01T00:00:00Z",
            goal="atomic fork",
            frame_id="frm_atomic",
        )
        for i in range(4):
            parent.append(self._ev(f"evt_fork_{i}", payload={"position": i}))
        return parent

    def _run_ids(self, store: Any) -> list[str]:
        return [record.run_id for record in type(store).list_runs(self.fork_target)]

    def test_fork_failure_restores_destination_and_parent(self) -> None:
        parent = None
        child = None
        try:
            parent = self._fork_parent("run_fork_atomic_parent")
            child_run_id = f"{parent.run_id}_child"
            self.register_cleanup_run_id(child_run_id)
            self.seed_orphan_event(
                self._ev("evt_fork_1", payload={"orphan": True}),
                run_id=child_run_id,
            )

            child = self.open_store_exact(child_run_id)
            parent_events_before = list(parent.iter_events())
            parent_run_before = parent.get_run()
            child_events_before = list(child.iter_events())
            runs_before = self._run_ids(parent)
            recent_before = type(parent).most_recent_run_id(self.fork_target)

            with pytest.raises(Exception):
                self.call_fork(
                    parent_run_id=parent.run_id,
                    new_run_id=child_run_id,
                    at_event_id="evt_fork_2",
                )

            assert self._run_ids(parent) == runs_before
            assert type(parent).most_recent_run_id(self.fork_target) == recent_before
            assert list(parent.iter_events()) == parent_events_before
            assert parent.get_run() == parent_run_before
            assert list(child.iter_events()) == child_events_before
            assert child.get_run() is None
        finally:
            if child is not None:
                self.close_store(child)
            if parent is not None:
                self.close_store(parent)
            self.cleanup()

    def test_successful_fork_commits_metadata_and_complete_prefix(self) -> None:
        parent = None
        child = None
        try:
            parent = self._fork_parent("run_fork_atomic_success_parent")
            child_run_id = f"{parent.run_id}_child"
            self.register_cleanup_run_id(child_run_id)
            parent_events_before = list(parent.iter_events())
            parent_run_before = parent.get_run()

            copied = self.call_fork(
                parent_run_id=parent.run_id,
                new_run_id=child_run_id,
                at_event_id="evt_fork_2",
            )
            child = self.open_store_exact(child_run_id)

            assert copied == 3
            assert [event.id for event in child.iter_events()] == [
                "evt_fork_0",
                "evt_fork_1",
                "evt_fork_2",
            ]
            child_run = child.get_run()
            assert child_run is not None
            assert child_run.parent_run_id == parent.run_id
            assert child_run.forked_at_event_id == "evt_fork_2"
            assert child_run.label == "atomic-child"
            assert child_run.goal == "atomic fork"
            assert child_run.frame_id == "frm_atomic"
            assert list(parent.iter_events()) == parent_events_before
            assert parent.get_run() == parent_run_before
        finally:
            if child is not None:
                self.close_store(child)
            if parent is not None:
                self.close_store(parent)
            self.cleanup()
