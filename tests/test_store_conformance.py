"""EventStoreConformance against InMemory and SQLite.

PostgresEventStore runs the same suite in test_postgres_store.py,
gated by the ACTIVEGRAPH_TEST_POSTGRES_URL env var.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from activegraph.store.conformance import EventStoreConformance
from activegraph.store.memory import InMemoryEventStore
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


class TestSQLiteConformance(EventStoreConformance):
    __test__ = True
    backend_name = "sqlite"

    def setup_method(self, method):
        fd, self._path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self._path)

    def make_store(self, run_id):
        return SQLiteEventStore(self._path, run_id=run_id)

    def make_second_store(self, run_id):
        return self.make_store(run_id)

    def close_store(self, store):
        store.close()

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
