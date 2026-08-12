"""PostgresEventStore — runs the conformance suite against a live Postgres.

Gated by ACTIVEGRAPH_TEST_POSTGRES_URL. If not set, the tests skip.
CI / contributors with Docker can use testcontainers (see CONTRIBUTING.md);
local dev without Docker just skips these and runs the other 247+ tests
unaffected. CONTRACT v0.8 #18.
"""

from __future__ import annotations

import os
import uuid

import pytest

from activegraph.store.conformance import EventStoreConformance

PG_URL = os.environ.get("ACTIVEGRAPH_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    PG_URL is None,
    reason="set ACTIVEGRAPH_TEST_POSTGRES_URL to run Postgres tests",
)


@pytest.mark.postgres
class TestPostgresConformance(EventStoreConformance):
    __test__ = True
    backend_name = "postgres"

    def setup_method(self, method):
        # Each test uses a unique run_id so conformance tests are
        # independent in a shared database.
        self._created_run_ids: list[str] = []

    def make_store(self, run_id):
        from activegraph.store.postgres import PostgresEventStore

        # Append a uuid suffix so multiple test runs in the same DB
        # don't collide on run_id.
        scoped = f"{run_id}_{uuid.uuid4().hex[:8]}"
        self._created_run_ids.append(scoped)
        return PostgresEventStore(PG_URL, run_id=scoped)

    def make_second_store(self, run_id):
        return self.make_store(run_id)

    def close_store(self, store):
        store.close()

    def cleanup(self):
        import psycopg

        with psycopg.connect(PG_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                for rid in self._created_run_ids:
                    cur.execute("DELETE FROM events WHERE run_id = %s", (rid,))
                    cur.execute("DELETE FROM runs WHERE run_id = %s", (rid,))
        self._created_run_ids = []
