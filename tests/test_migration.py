"""Migration semantics — CONTRACT v0.8 #5 (revised: transaction-per-run)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from urllib.parse import urlparse

import pytest

from activegraph import (
    Graph,
    Event,
    MigrationBackendCloseError,
    MigrationReport,
    MigrationRunReport,
    Runtime,
    RunRecord,
    SchemaVersionMismatch,
    UnsupportedMigrationBackendError,
    UnsupportedMigrationCapabilityError,
    behavior,
    clear_registry,
    migrate,
    register_migration_backend,
    resolve_migration_backend,
)
from activegraph.store.migration import clear_migration_backend_cache
from activegraph.store.url import InvalidStoreURL, parse_store_url


def test_store_migration_is_canonical_and_compat_exports_preserve_identity():
    import activegraph
    import activegraph.observability as observability
    import activegraph.observability.migration as compat
    import activegraph.store as store
    import activegraph.store.migration as canonical

    for name in (
        "migrate",
        "MigrationReport",
        "MigrationRunReport",
        "MigrationBackend",
        "MigrationBackendProvider",
        "CorruptMigrationEvent",
        "BackendRegistration",
        "register_migration_backend",
        "resolve_migration_backend",
    ):
        expected = getattr(canonical, name)
        assert getattr(compat, name) is expected
        assert getattr(observability, name) is expected
        assert getattr(store, name) is expected
        assert getattr(activegraph, name) is expected

    assert MigrationReport is canonical.MigrationReport
    assert MigrationRunReport is canonical.MigrationRunReport
    assert migrate is canonical.migrate


def test_central_migration_has_no_backend_sql_or_private_backend_imports():
    import inspect

    import activegraph.store.migration as canonical

    source = inspect.getsource(canonical)
    for forbidden in (
        "sqlite3",
        "psycopg",
        "_ConnectionSource",
        "_ensure_schema",
        "INSERT INTO",
        "SELECT * FROM events",
    ):
        assert forbidden not in source


def test_migration_registry_does_not_broaden_open_store_url_parser():
    provider = _ProbeProvider("migration-only")
    token = register_migration_backend(provider)
    try:
        assert (
            resolve_migration_backend("migration-only://source", require="read")
            is provider
        )
        with pytest.raises(InvalidStoreURL):
            parse_store_url("migration-only://source")
    finally:
        token.unregister()


class _ProbeProvider:
    def __init__(
        self,
        scheme: str,
        capabilities: frozenset[str] = frozenset({"read", "write"}),
    ) -> None:
        self.schemes = (scheme,)
        self.capabilities = capabilities
        self.opens = 0
        self.validated: list[str] = []

    def validate_url(self, url: str) -> None:
        self.validated.append(url)
        assert urlparse(url).scheme == self.schemes[0]

    def open(self, url: str):
        self.opens += 1
        raise AssertionError("preflight test must not open a backend")


def test_migration_resolver_rejects_unknown_and_wrong_capability_before_open():
    with pytest.raises(UnsupportedMigrationBackendError):
        resolve_migration_backend("no-such-migration://source", require="read")

    source = _ProbeProvider("probe-source", frozenset({"read"}))
    destination = _ProbeProvider("probe-dest", frozenset({"read"}))
    source_token = register_migration_backend(source)
    destination_token = register_migration_backend(destination)
    try:
        with pytest.raises(UnsupportedMigrationCapabilityError):
            migrate("probe-source://source", "probe-dest://destination")
        assert source.opens == 0
        assert destination.opens == 0
        assert source.validated == ["probe-source://source"]
        assert destination.validated == []
    finally:
        destination_token.unregister()
        source_token.unregister()


def test_explicit_replace_token_restores_builtin_provider():
    replacement = _ProbeProvider("sqlite")
    token = register_migration_backend(replacement, replace=True)
    try:
        assert (
            resolve_migration_backend("sqlite:///replacement.db", require="read")
            is replacement
        )
    finally:
        token.unregister()
    restored = resolve_migration_backend("sqlite:///restored.db", require="read")
    assert type(restored).__name__ == "SQLiteMigrationBackendProvider"


class _FakeEntryPoint:
    def __init__(self, name: str, loaded) -> None:
        self.name = name
        self._loaded = loaded
        self.load_calls = 0

    def load(self):
        self.load_calls += 1
        if isinstance(self._loaded, BaseException):
            raise self._loaded
        return self._loaded


class _FakeEntryPoints(tuple):
    def select(self, *, group: str):
        assert group == "activegraph.migration_backends"
        return self


def test_entry_point_discovery_loads_only_requested_scheme(monkeypatch):
    import importlib.metadata

    requested = _ProbeProvider("entry-probe")
    good = _FakeEntryPoint("entry-probe", requested)
    unrelated = _FakeEntryPoint("broken-unrelated", RuntimeError("broken plugin"))
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda: _FakeEntryPoints((good, unrelated)),
    )
    clear_migration_backend_cache()
    try:
        resolved = resolve_migration_backend("entry-probe://source", require="read")
        assert resolved is requested
        assert good.load_calls == 1
        assert unrelated.load_calls == 0
    finally:
        clear_migration_backend_cache()


class _CloseProbeBackend:
    def __init__(self, provider, endpoint: str) -> None:
        self.provider = provider
        self.endpoint = endpoint

    def list_runs(self):
        return self.provider.records

    def iter_run(self, run_id):
        return iter(self.provider.events.get(run_id, ()))

    def write_run_transactionally(self, record, events):
        return len(events)

    def close(self):
        self.provider.close_order.append(self.endpoint)
        if self.endpoint in self.provider.fail_close:
            raise OSError(f"close {self.endpoint}")


class _CloseProbeProvider:
    schemes = ("close-probe",)
    capabilities = frozenset({"read", "write"})

    def __init__(self) -> None:
        self.records = []
        self.events = {}
        self.close_order = []
        self.fail_close = set()

    def validate_url(self, url):
        assert url.startswith("close-probe://")

    def open(self, url):
        return _CloseProbeBackend(self, url.split("://", 1)[1])


def test_backend_close_failures_raise_typed_error_in_destination_source_order():
    provider = _CloseProbeProvider()
    provider.fail_close = {"src", "dst"}
    token = register_migration_backend(provider)
    try:
        with pytest.raises(MigrationBackendCloseError) as excinfo:
            migrate("close-probe://src", "close-probe://dst")
        assert provider.close_order == ["dst", "src"]
        assert [url for url, _ in excinfo.value.failures] == [
            "close-probe://dst",
            "close-probe://src",
        ]
    finally:
        token.unregister()


def test_callback_error_remains_primary_when_backend_close_also_fails():
    provider = _CloseProbeProvider()
    record = RunRecord("run_1", None, None, None, "2026-01-01", None, None)
    provider.records = [record]
    provider.events = {
        "run_1": [
            Event(
                id="evt_1",
                type="test.event",
                payload={},
                timestamp="2026-01-01T00:00:00+00:00",
            )
        ]
    }
    provider.fail_close = {"dst"}
    token = register_migration_backend(provider)
    try:
        with pytest.raises(ValueError, match="callback failed") as excinfo:
            migrate(
                "close-probe://src",
                "close-probe://dst",
                on_progress=lambda report: (_ for _ in ()).throw(
                    ValueError("callback failed")
                ),
            )
        assert any("close-probe://dst" in note for note in excinfo.value.__notes__)
        assert provider.close_order == ["dst", "src"]
    finally:
        token.unregister()


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

    def test_source_schema_mismatch_still_raises_before_reporting(self):
        _make_run(self.src)
        with sqlite3.connect(self.src) as conn:
            conn.execute(
                "UPDATE meta SET value = 'future' WHERE key = 'schema_version'"
            )

        with pytest.raises(SchemaVersionMismatch):
            migrate(f"sqlite:///{self.src}", f"sqlite:///{self.dst}")

        assert not os.path.exists(self.dst)

    def test_destination_schema_mismatch_still_raises_before_reporting(self):
        _make_run(self.src)
        from activegraph.store.sqlite import SQLiteEventStore

        assert SQLiteEventStore.list_runs(self.dst) == []
        with sqlite3.connect(self.dst) as conn:
            conn.execute(
                "UPDATE meta SET value = 'future' WHERE key = 'schema_version'"
            )

        with pytest.raises(SchemaVersionMismatch):
            migrate(f"sqlite:///{self.src}", f"sqlite:///{self.dst}")

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
