"""GraphStore conformance against InMemoryGraphStore + a Graph integration check.

The in-memory store is the default backend; this also guards that the
GraphStore seam in `core.graph` did not change graph semantics.
"""

from __future__ import annotations

import pytest

import activegraph.store.falkordb as falkordb_module
from activegraph.core.graph import Graph
from activegraph.core.graph_store import InMemoryGraphStore
from activegraph.store.falkordb import _resolve_connection
from activegraph.store.graph_conformance import GraphStoreConformance


class TestInMemoryGraphStoreConformance(GraphStoreConformance):
    __test__ = True

    def make_store(self):
        return InMemoryGraphStore()


def test_graph_uses_injected_graph_store():
    store = InMemoryGraphStore()
    g = Graph(graph_store=store)
    obj = g.add_object("memo", {"text": "hi"})
    # The object the Graph returns is the one the store holds.
    assert store.get_object(obj.id) is obj
    assert g.get_object(obj.id) is obj


def test_graph_patch_flow_round_trips_through_store():
    g = Graph()
    obj = g.add_object("memo", {"text": "first"})
    g.patch_object(obj.id, {"text": "second"})
    reread = g.get_object(obj.id)
    assert reread.data["text"] == "second"
    assert reread.version == 2


def test_graph_object_removal_cascades_relations():
    g = Graph()
    a = g.add_object("node", {})
    b = g.add_object("node", {})
    g.add_relation(a.id, b.id, "links")
    assert len(g.all_relations()) == 1
    g.remove_object(a.id)
    assert g.get_object(a.id) is None
    assert g.all_relations() == []


# --- FalkorDB connection resolution (no server needed) ---


@pytest.fixture(autouse=True)
def _clear_falkordb_env(monkeypatch):
    for var in (
        "FALKORDB_URL",
        "FALKORDB_HOST",
        "FALKORDB_PORT",
        "FALKORDB_USERNAME",
        "FALKORDB_PASSWORD",
    ):
        monkeypatch.delenv(var, raising=False)


def test_resolve_connection_none_falls_back_to_embedded():
    assert _resolve_connection(None, None, None, None, None) is None


def test_resolve_connection_explicit_url():
    assert _resolve_connection("falkor://h:6380", None, None, None, None) == {
        "url": "falkor://h:6380"
    }


def test_resolve_connection_explicit_host_defaults_port():
    assert _resolve_connection(None, "myhost", None, None, None) == {
        "host": "myhost",
        "port": 6379,
        "username": None,
        "password": None,
    }


def test_resolve_connection_explicit_host_full():
    assert _resolve_connection(None, "myhost", 7000, "u", "p") == {
        "host": "myhost",
        "port": 7000,
        "username": "u",
        "password": "p",
    }


def test_resolve_connection_env_url(monkeypatch):
    monkeypatch.setenv("FALKORDB_URL", "falkor://env:6379")
    assert _resolve_connection(None, None, None, None, None) == {
        "url": "falkor://env:6379"
    }


def test_resolve_connection_env_host(monkeypatch):
    monkeypatch.setenv("FALKORDB_HOST", "envhost")
    monkeypatch.setenv("FALKORDB_PORT", "6500")
    monkeypatch.setenv("FALKORDB_USERNAME", "envuser")
    monkeypatch.setenv("FALKORDB_PASSWORD", "envpass")
    assert _resolve_connection(None, None, None, None, None) == {
        "host": "envhost",
        "port": 6500,
        "username": "envuser",
        "password": "envpass",
    }


def test_resolve_connection_explicit_args_override_env(monkeypatch):
    monkeypatch.setenv("FALKORDB_HOST", "envhost")
    monkeypatch.setenv("FALKORDB_URL", "falkor://env:6379")
    # explicit url wins over both env vars
    assert _resolve_connection("falkor://explicit:1", None, None, None, None) == {
        "url": "falkor://explicit:1"
    }


# --- FalkorDB index provisioning (no optional dependency needed) ---


class _FakeResponseError(Exception):
    pass


class _IndexGraph:
    def __init__(self, errors=()):
        self.errors = list(errors)
        self.statements = []
        self.close_count = 0

    def query(self, statement):
        self.statements.append(statement)
        index = len(self.statements) - 1
        if index < len(self.errors) and self.errors[index] is not None:
            raise self.errors[index]

    def close(self):
        self.close_count += 1


class _OwnedDB:
    def __init__(self, graph=None, select_error=None, close_error=None):
        self.graph = graph
        self.select_error = select_error
        self.close_error = close_error
        self.close_count = 0

    def select_graph(self, name):
        if self.select_error is not None:
            raise self.select_error
        return self.graph

    def close(self):
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


_INDEX_PROPERTIES = ("id", "id", "id", "type", "id")


def _install_response_error(monkeypatch):
    monkeypatch.setattr(
        falkordb_module,
        "_response_error_type",
        lambda: _FakeResponseError,
        raising=False,
    )


def _install_owned_db(monkeypatch, db):
    monkeypatch.setattr(falkordb_module, "_resolve_connection", lambda *args: None)
    monkeypatch.setattr(falkordb_module, "_require_falkordblite", lambda: lambda: db)


def test_falkor_index_exact_duplicate_type_text_and_property_is_ignored(monkeypatch):
    _install_response_error(monkeypatch)
    graph = _IndexGraph(
        [_FakeResponseError(f"Attribute '{field}' is already indexed") for field in _INDEX_PROPERTIES]
    )

    falkordb_module.FalkorDBGraphStore(graph=graph)

    assert len(graph.statements) == 5


def test_falkor_index_generic_error_with_duplicate_text_propagates(monkeypatch):
    _install_response_error(monkeypatch)
    error = RuntimeError("Attribute 'id' is already indexed")
    graph = _IndexGraph([error])

    with pytest.raises(RuntimeError) as excinfo:
        falkordb_module.FalkorDBGraphStore(graph=graph)

    assert excinfo.value is error
    assert len(graph.statements) == 1


def test_falkor_index_response_error_with_other_text_propagates(monkeypatch):
    _install_response_error(monkeypatch)
    error = _FakeResponseError("Syntax error near CREATE")
    graph = _IndexGraph([error])

    with pytest.raises(_FakeResponseError) as excinfo:
        falkordb_module.FalkorDBGraphStore(graph=graph)

    assert excinfo.value is error
    assert len(graph.statements) == 1


def test_falkor_index_duplicate_for_wrong_property_propagates(monkeypatch):
    _install_response_error(monkeypatch)
    errors = [None, None, None, _FakeResponseError("Attribute 'id' is already indexed")]
    graph = _IndexGraph(errors)

    with pytest.raises(_FakeResponseError) as excinfo:
        falkordb_module.FalkorDBGraphStore(graph=graph)

    assert excinfo.value is errors[3]
    assert len(graph.statements) == 4


def test_falkor_index_resolver_failure_preserves_query_error(monkeypatch):
    original = RuntimeError("connection dropped")
    graph = _IndexGraph([original])

    def fail_resolver():
        raise ModuleNotFoundError("redis unavailable")

    monkeypatch.setattr(
        falkordb_module, "_response_error_type", fail_resolver, raising=False
    )

    with pytest.raises(RuntimeError) as excinfo:
        falkordb_module.FalkorDBGraphStore(graph=graph)

    assert excinfo.value is original


def test_falkor_client_constructor_failure_has_no_owned_cleanup(monkeypatch):
    primary = RuntimeError("client construction failed")

    class FailingFactory:
        close_count = 0

        def __call__(self):
            raise primary

        def close(self):
            self.close_count += 1

    factory = FailingFactory()
    monkeypatch.setattr(falkordb_module, "_resolve_connection", lambda *args: None)
    monkeypatch.setattr(falkordb_module, "_require_falkordblite", lambda: factory)

    with pytest.raises(RuntimeError) as excinfo:
        falkordb_module.FalkorDBGraphStore()

    assert excinfo.value is primary
    assert factory.close_count == 0


def test_falkor_select_graph_failure_closes_owned_client(monkeypatch):
    primary = RuntimeError("select failed")
    db = _OwnedDB(select_error=primary)
    _install_owned_db(monkeypatch, db)

    with pytest.raises(RuntimeError) as excinfo:
        falkordb_module.FalkorDBGraphStore()

    assert excinfo.value is primary
    assert db.close_count == 1


def test_falkor_index_failure_closes_owned_but_not_injected_client(monkeypatch):
    _install_response_error(monkeypatch)
    primary = RuntimeError("permission denied")
    owned_graph = _IndexGraph([primary])
    db = _OwnedDB(graph=owned_graph)
    _install_owned_db(monkeypatch, db)

    with pytest.raises(RuntimeError) as excinfo:
        falkordb_module.FalkorDBGraphStore()
    assert excinfo.value is primary
    assert db.close_count == 1

    injected_graph = _IndexGraph([primary])
    with pytest.raises(RuntimeError) as excinfo:
        falkordb_module.FalkorDBGraphStore(graph=injected_graph)
    assert excinfo.value is primary
    assert injected_graph.close_count == 0


def test_falkor_cleanup_failure_never_masks_constructor_failure(monkeypatch):
    _install_response_error(monkeypatch)
    primary = RuntimeError("index provisioning failed")
    db = _OwnedDB(
        graph=_IndexGraph([primary]),
        close_error=RuntimeError("cleanup failed"),
    )
    _install_owned_db(monkeypatch, db)

    with pytest.raises(RuntimeError) as excinfo:
        falkordb_module.FalkorDBGraphStore()

    assert excinfo.value is primary
    assert db.close_count == 1
