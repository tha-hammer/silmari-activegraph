"""Migration semantics — CONTRACT v0.8 #5 (revised: transaction-per-run)."""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from activegraph import Graph, Runtime, behavior, clear_registry
from activegraph.observability.migration import migrate
from activegraph.store.errors import SchemaVersionMismatch


def _register_simple():
    clear_registry()

    @behavior(name="planner", on=["goal.created"])
    def planner(event, graph, ctx):
        graph.add_object("task", {"title": "research", "status": "open"})


def _make_run(path: str) -> str:
    _register_simple()
    g = Graph()
    rt = Runtime(g, persist_to=path)
    rt.run_goal("test goal")
    rt.save_state()
    return rt.run_id


class TestSQLiteToSQLiteMigration:
    def setup_method(self, method):
        fd1, self.src = tempfile.mkstemp(suffix=".db")
        os.close(fd1)
        os.remove(self.src)
        fd2, self.dst = tempfile.mkstemp(suffix=".db")
        os.close(fd2)
        os.remove(self.dst)

    def teardown_method(self, method):
        for p in (self.src, self.dst):
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(p + suffix)
                except FileNotFoundError:
                    pass

    def test_round_trip_preserves_event_count_and_order(self):
        run_id = _make_run(self.src)
        report = migrate(f"sqlite:///{self.src}", f"sqlite:///{self.dst}")
        assert report.ok
        assert len(report.runs) == 1
        assert report.runs[0].status == "ok"
        assert report.runs[0].run_id == run_id
        # Check event ordering preserved
        from activegraph.store.sqlite import SQLiteEventStore

        src_evs = list(SQLiteEventStore(self.src, run_id=run_id).iter_events())
        dst_evs = list(SQLiteEventStore(self.dst, run_id=run_id).iter_events())
        assert [e.id for e in src_evs] == [e.id for e in dst_evs]
        assert [e.type for e in src_evs] == [e.type for e in dst_evs]

    def test_idempotent_rerun_writes_zero(self):
        run_id = _make_run(self.src)
        first = migrate(f"sqlite:///{self.src}", f"sqlite:///{self.dst}")
        assert first.runs[0].events_migrated > 0
        # Re-run: ON CONFLICT DO NOTHING means zero new rows
        second = migrate(f"sqlite:///{self.src}", f"sqlite:///{self.dst}")
        assert second.runs[0].status == "ok"
        assert second.runs[0].events_migrated == 0

    def test_only_run_ids_filter(self):
        rid_a = _make_run(self.src)
        # Make a second run in the same source
        _register_simple()
        g2 = Graph()
        rt2 = Runtime(g2, persist_to=self.src)
        rt2.run_goal("second goal")
        rt2.save_state()
        rid_b = rt2.run_id
        assert rid_a != rid_b

        report = migrate(
            f"sqlite:///{self.src}",
            f"sqlite:///{self.dst}",
            only_run_ids=[rid_a],
        )
        assert len(report.runs) == 1
        assert report.runs[0].run_id == rid_a

    def test_progress_callback_fires_per_run(self):
        _make_run(self.src)
        _register_simple()
        g2 = Graph()
        rt2 = Runtime(g2, persist_to=self.src)
        rt2.run_goal("second")
        rt2.save_state()

        progress = []
        report = migrate(
            f"sqlite:///{self.src}",
            f"sqlite:///{self.dst}",
            on_progress=lambda r: progress.append(r.run_id),
        )
        assert len(progress) == 2
        assert progress == [r.run_id for r in report.runs]

    def test_source_schema_mismatch_still_raises_before_reporting(self):
        _make_run(self.src)
        with sqlite3.connect(self.src) as conn:
            conn.execute(
                "UPDATE meta SET value = 'future' WHERE key = 'schema_version'"
            )

        with pytest.raises(SchemaVersionMismatch):
            migrate(f"sqlite:///{self.src}", f"sqlite:///{self.dst}")

        assert not os.path.exists(self.dst)

    def test_destination_schema_mismatch_remains_failed_write_report(self):
        run_id = _make_run(self.src)
        from activegraph.store.sqlite import SQLiteEventStore

        assert SQLiteEventStore.list_runs(self.dst) == []
        with sqlite3.connect(self.dst) as conn:
            conn.execute(
                "UPDATE meta SET value = 'future' WHERE key = 'schema_version'"
            )

        report = migrate(f"sqlite:///{self.src}", f"sqlite:///{self.dst}")

        assert not report.ok
        assert len(report.runs) == 1
        assert report.runs[0].run_id == run_id
        assert report.runs[0].status == "failed"
        assert report.runs[0].events_migrated == 0
        assert "write failure: SchemaVersionMismatch:" in (
            report.runs[0].error or ""
        )


class TestMigrationFailureLeavesDestUnchanged:
    """A write failure mid-run should roll back; destination has no rows
    from the failed run. CONTRACT v0.8 #5 (transaction-per-run)."""

    def setup_method(self, method):
        fd1, self.src = tempfile.mkstemp(suffix=".db")
        os.close(fd1)
        os.remove(self.src)
        fd2, self.dst = tempfile.mkstemp(suffix=".db")
        os.close(fd2)
        os.remove(self.dst)

    def teardown_method(self, method):
        for p in (self.src, self.dst):
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(p + suffix)
                except FileNotFoundError:
                    pass

    def test_simulated_write_failure_rolls_back(self, monkeypatch):
        run_id = _make_run(self.src)

        # Patch the sqlite encode_event to raise on the third call, so
        # we fail in the middle of writing events for this run.
        from activegraph.observability import migration as mig_mod

        original = mig_mod.encode_event if hasattr(mig_mod, "encode_event") else None
        from activegraph.store import serde as serde_mod

        call_count = {"n": 0}
        real_encode = serde_mod.encode_event

        def boom(ev):
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise RuntimeError("simulated mid-write failure")
            return real_encode(ev)

        monkeypatch.setattr(serde_mod, "encode_event", boom)

        report = migrate(f"sqlite:///{self.src}", f"sqlite:///{self.dst}")
        assert not report.ok
        assert report.runs[0].status == "failed"
        assert "simulated mid-write failure" in (report.runs[0].error or "")

        # Destination should have zero events for this run (txn rolled back).
        from activegraph.store.sqlite import SQLiteEventStore

        dst_store = SQLiteEventStore(self.dst, run_id=run_id)
        assert dst_store.count() == 0

    def test_failure_in_one_run_does_not_block_others(self, monkeypatch):
        """Two runs in source; first fails, second still succeeds."""
        rid_a = _make_run(self.src)
        _register_simple()
        g2 = Graph()
        rt2 = Runtime(g2, persist_to=self.src)
        rt2.run_goal("second")
        rt2.save_state()
        rid_b = rt2.run_id

        from activegraph.store import serde as serde_mod

        real_encode = serde_mod.encode_event
        call_count = {"n": 0}

        def boom(ev):
            # Fail only during the first run's migration.
            call_count["n"] += 1
            if call_count["n"] == 2:  # 2nd event in first run
                raise RuntimeError("first-run failure")
            return real_encode(ev)

        monkeypatch.setattr(serde_mod, "encode_event", boom)

        report = migrate(f"sqlite:///{self.src}", f"sqlite:///{self.dst}")
        assert not report.ok
        # Find rid_b's report — it should be ok
        by_id = {r.run_id: r for r in report.runs}
        assert by_id[rid_a].status == "failed"
        assert by_id[rid_b].status == "ok"
