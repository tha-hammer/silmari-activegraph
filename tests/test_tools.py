"""Tool primitive tests. CONTRACT v0.7 #2 / #3 / #5 / #6 / #15 / #16.

Covers:
  - @tool decorator + global registry
  - Tool input/output schema validation (happy path + failure paths)
  - Recorded / Recording providers (round-trip)
  - graph_query factory binding
  - Tool cache key hash stability
"""

from __future__ import annotations

import json
import os
from decimal import Decimal

import pytest
from pydantic import BaseModel

from activegraph import (
    Graph,
    Tool,
    ToolContext,
    ToolError,
    clear_tool_registry,
    get_tool_registry,
    tool,
)
from activegraph.tools.cache import (
    ToolCache,
    canonicalize_args,
    hash_tool_call,
)
from activegraph.tools.graph_query import (
    GraphQueryInput,
    GraphQueryOutput,
    make_graph_query_tool,
)
from activegraph.tools.recorded import (
    DirectToolInvoker,
    RecordedToolProvider,
    RecordingToolProvider,
)

from tests._object_query_helpers import collision_graph


# ---------- @tool registration ---------------------------------------------


class _AddIn(BaseModel):
    a: int
    b: int


class _AddOut(BaseModel):
    sum: int


def test_tool_decorator_registers_globally():
    @tool(
        name="add",
        description="Add two integers.",
        input_schema=_AddIn,
        output_schema=_AddOut,
        deterministic=True,
    )
    def add(args: _AddIn, ctx: ToolContext) -> _AddOut:
        return _AddOut(sum=args.a + args.b)

    reg = get_tool_registry()
    assert any(t.name == "add" for t in reg)


def test_tool_decorator_inherits_function_name_when_name_missing():
    @tool(description="x", input_schema=_AddIn, output_schema=_AddOut)
    def explicitly_named_tool(args, ctx):  # noqa: ANN001
        return _AddOut(sum=0)

    assert any(t.name == "explicitly_named_tool" for t in get_tool_registry())


def test_tool_cost_per_call_is_decimal():
    @tool(name="x", input_schema=_AddIn, output_schema=_AddOut, cost_per_call="0.05")
    def x(args, ctx):
        return _AddOut(sum=0)

    reg = {t.name: t for t in get_tool_registry()}
    assert isinstance(reg["x"].cost_per_call, Decimal)
    assert reg["x"].cost_per_call == Decimal("0.05")


def test_tool_to_definition_shape():
    @tool(
        name="add",
        description="d",
        input_schema=_AddIn,
        output_schema=_AddOut,
    )
    def add(args, ctx):
        return _AddOut(sum=0)

    reg = {t.name: t for t in get_tool_registry()}
    defn = reg["add"].to_definition()
    assert defn["name"] == "add"
    assert defn["description"] == "d"
    assert "input_schema" in defn


# ---------- canonicalize_args / hash_tool_call -----------------------------


def test_canonicalize_pydantic_model_dumps_to_dict():
    out = canonicalize_args(_AddIn(a=1, b=2))
    assert out == {"a": 1, "b": 2}


def test_canonicalize_dict_with_decimal():
    out = canonicalize_args({"price": Decimal("1.23"), "qty": 4})
    assert out == {"price": "1.23", "qty": 4}


def test_hash_tool_call_stable_across_dict_order():
    h1 = hash_tool_call(tool_name="x", args={"a": 1, "b": 2})
    h2 = hash_tool_call(tool_name="x", args={"b": 2, "a": 1})
    assert h1 == h2


def test_hash_tool_call_changes_with_name():
    h1 = hash_tool_call(tool_name="x", args={"a": 1})
    h2 = hash_tool_call(tool_name="y", args={"a": 1})
    assert h1 != h2


# ---------- DirectToolInvoker (the production path) ------------------------


def test_direct_invoker_runs_tool_body():
    @tool(name="add", input_schema=_AddIn, output_schema=_AddOut, deterministic=True)
    def add(args, ctx):
        return _AddOut(sum=args.a + args.b)

    t = next(t for t in get_tool_registry() if t.name == "add")
    ctx = ToolContext(
        behavior_name="b",
        event_id="evt_1",
        frame=None,
        idempotency_key="k",
        timeout_seconds=1.0,
    )
    resp = DirectToolInvoker().invoke(t, _AddIn(a=1, b=2), ctx)
    assert resp.output == {"sum": 3}
    assert resp.error is None
    assert resp.cache_hit is False


def test_direct_invoker_traps_tool_exception_as_execution_error():
    @tool(name="broken", input_schema=_AddIn, output_schema=_AddOut)
    def broken(args, ctx):
        raise ValueError("nope")

    t = next(t for t in get_tool_registry() if t.name == "broken")
    ctx = ToolContext(
        behavior_name="b", event_id="evt_1", frame=None,
        idempotency_key="k", timeout_seconds=1.0,
    )
    with pytest.raises(ToolError) as exc_info:
        DirectToolInvoker().invoke(t, _AddIn(a=1, b=2), ctx)
    assert exc_info.value.reason == "tool.execution_error"


def test_direct_invoker_propagates_explicit_tool_error():
    @tool(name="timeout_tool", input_schema=_AddIn, output_schema=_AddOut)
    def timeout_tool(args, ctx):
        raise ToolError("tool.timeout", "took too long")

    t = next(t for t in get_tool_registry() if t.name == "timeout_tool")
    ctx = ToolContext(
        behavior_name="b", event_id="evt_1", frame=None,
        idempotency_key="k", timeout_seconds=1.0,
    )
    with pytest.raises(ToolError) as exc_info:
        DirectToolInvoker().invoke(t, _AddIn(a=1, b=2), ctx)
    assert exc_info.value.reason == "tool.timeout"


# ---------- RecordedToolProvider / RecordingToolProvider -------------------


def test_recording_then_recorded_round_trip(tmp_path):
    @tool(name="add", input_schema=_AddIn, output_schema=_AddOut, deterministic=True)
    def add(args, ctx):
        return _AddOut(sum=args.a + args.b)

    t = next(t for t in get_tool_registry() if t.name == "add")
    fixtures_dir = str(tmp_path)
    ctx = ToolContext(
        behavior_name="b", event_id="evt_1", frame=None,
        idempotency_key="k", timeout_seconds=1.0,
    )
    rec = RecordingToolProvider(DirectToolInvoker(), fixtures_dir)
    rec.invoke(t, _AddIn(a=2, b=3), ctx)

    files = [f for r, _, fs in os.walk(fixtures_dir) for f in fs if f.endswith(".json")]
    assert len(files) == 1

    recorded = RecordedToolProvider(fixtures_dir)
    resp = recorded.invoke(t, _AddIn(a=2, b=3), ctx)
    assert resp.output == {"sum": 5}


def test_recorded_missing_fixture_raises_tool_error(tmp_path):
    @tool(name="add", input_schema=_AddIn, output_schema=_AddOut)
    def add(args, ctx):
        return _AddOut(sum=0)

    t = next(t for t in get_tool_registry() if t.name == "add")
    ctx = ToolContext(
        behavior_name="b", event_id="evt_1", frame=None,
        idempotency_key="k", timeout_seconds=1.0,
    )
    with pytest.raises(ToolError) as exc_info:
        RecordedToolProvider(str(tmp_path)).invoke(t, _AddIn(a=9, b=9), ctx)
    assert exc_info.value.reason == "tool.fixture_missing"


# ---------- ToolCache.from_events ------------------------------------------


def test_tool_cache_from_events_picks_up_recorded_pairs():
    from activegraph.core.event import Event
    req = Event(
        id="evt_001",
        type="tool.requested",
        payload={"tool": "x", "args_hash": "deadbeef"},
        caused_by=None,
    )
    resp = Event(
        id="evt_002",
        type="tool.responded",
        payload={
            "tool": "x",
            "args_hash": "deadbeef",
            "output": {"y": 1},
            "error": None,
            "latency_seconds": 0.1,
            "cost_usd": "0.001",
        },
        caused_by="evt_001",
    )
    cache = ToolCache.from_events([req, resp])
    assert cache.has("deadbeef")
    entry = cache.get("deadbeef")
    assert entry.output == {"y": 1}
    assert entry.cache_hit is True


# ---------- graph_query factory binding ------------------------------------


def test_graph_query_factory_binds_to_graph():
    g = Graph()
    g.add_object("claim", {"text": "x", "confidence": 0.5})
    g.add_object("claim", {"text": "y", "confidence": 0.9})
    g.add_object("doc", {"title": "d"})
    tool_inst = make_graph_query_tool(g)
    assert tool_inst.deterministic is True
    out = tool_inst.fn(
        GraphQueryInput(object_type="claim"),
        ctx=ToolContext(
            behavior_name="b", event_id="e", frame=None,
            idempotency_key="k", timeout_seconds=1.0,
        ),
    )
    assert isinstance(out, GraphQueryOutput)
    assert {r.type for r in out.refs} == {"claim"}
    assert len(out.refs) == 2


def test_graph_query_factory_does_not_register_globally():
    g = Graph()
    before = len(get_tool_registry())
    _ = make_graph_query_tool(g)
    assert len(get_tool_registry()) == before


@pytest.mark.parametrize(
    "where",
    [
        {"confidence": 0.9},
        {"id": "claim#1"},
        {"data.id": "domain-id"},
        {"data.data.nested": True},
    ],
)
def test_graph_query_tool_uses_canonical_object_where_root(where):
    graph, colliding_id, _ = collision_graph()
    graph_query = make_graph_query_tool(graph)
    ctx = ToolContext(
        behavior_name="b",
        event_id="e",
        frame=None,
        idempotency_key="k",
        timeout_seconds=1.0,
    )

    response = DirectToolInvoker().invoke(
        graph_query,
        GraphQueryInput(object_type="claim", where=where),
        ctx,
    )

    assert response.error is None
    assert [ref["id"] for ref in response.output["refs"]] == [colliding_id]
