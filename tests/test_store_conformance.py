"""EventStoreConformance against InMemory and SQLite.

PostgresEventStore runs the same suite in test_postgres_store.py,
gated by the ACTIVEGRAPH_TEST_POSTGRES_URL env var.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from activegraph.store.conformance import (
    EventStoreConformance,
    ForkRunAtomicityConformance,
)
from activegraph.store.memory import InMemoryEventStore
from activegraph.store.serde import encode_event
from activegraph.store.sqlite import SQLiteEventStore


class TestInMemoryConformance(EventStoreConformance):
    __test__ = True
    backend_name = "memory"

    def make_store(self, run_id):
        return InMemoryEventStore(run_id=run_id)

    def make_second_store(self, run_id):
        return self.make_store(run_id)

    def close_store(self, store):
        store.close()


class TestSQLiteConformance(EventStoreConformance, ForkRunAtomicityConformance):
    __test__ = True
    backend_name = "sqlite"

    def setup_method(self, method):
        fd, self._path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self._path)

    def make_store(self, run_id):
        return SQLiteEventStore(self._path, run_id=run_id)

    @property
    def fork_target(self):
        return self._path

    def make_second_store(self, run_id):
        return self.make_store(run_id)

    def close_store(self, store):
        store.close()

    def open_store_exact(self, run_id):
        return SQLiteEventStore(self._path, run_id=run_id)

    def register_cleanup_run_id(self, run_id):
        pass

    def seed_orphan_event(self, event, *, run_id):
        row = encode_event(event)
        conn = sqlite3.connect(self._path)
        try:
            conn.execute(
                """
                INSERT INTO events
                    (id, type, actor, payload, frame_id, caused_by, timestamp, run_id)
                VALUES
                    (:id, :type, :actor, :payload, :frame_id, :caused_by, :timestamp, :run_id)
                """,
                {**row, "run_id": run_id},
            )
            conn.commit()
        finally:
            conn.close()

    def call_fork(self, *, parent_run_id, new_run_id, at_event_id):
        return SQLiteEventStore.fork_run(
            self._path,
            parent_run_id=parent_run_id,
            new_run_id=new_run_id,
            at_event_id=at_event_id,
            label="atomic-child",
            created_at="2026-01-02T00:00:00Z",
        )

    def cleanup(self):
        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass
        for suffix in ("-wal", "-shm"):
            try:
                os.remove(self._path + suffix)
            except FileNotFoundError:
                pass
