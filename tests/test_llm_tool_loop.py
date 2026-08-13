"""LLM ↔ tool turn loop tests. CONTRACT v0.7 #4 / #6.

Exercises the runtime's tool loop:
  - 0 tool turns: behavior with no tools acts like v0.6 (smoke).
  - 1 tool turn: LLM returns 1 tool_call, runtime dispatches, re-calls LLM,
    handler sees final parsed output.
  - 2 tool turns: chained tool calls.
  - max_tool_turns: runtime fails loud with reason=tool.max_turns_exhausted.
  - Tool budget enforcement triggers behavior.failed.
  - Unknown-tool refusal (LLM asks for a tool the behavior didn't declare).
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Callable, Optional

import pytest
from pydantic import BaseModel

from activegraph import (
    Graph,
    Runtime,
    Tool,
    ToolContext,
    behavior,
    llm_behavior,
    tool,
)
from activegraph.llm.cache import LLMCache
from activegraph.llm import LLMMessage, LLMResponse, ToolCall


class _Out(BaseModel):
    text: str


class _ToolIn(BaseModel):
    q: str


class _ToolOut(BaseModel):
    answer: str


def _make_response(*, model: str, parsed=None, tool_calls=None) -> LLMResponse:
    return LLMResponse(
        raw_text="",
        parsed=parsed,
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("0.001"),
        latency_seconds=0.1,
        model=model,
        finish_reason="end_turn" if tool_calls is None else "tool_use",
        tool_calls=tool_calls,
    )


class _ScriptedProvider:
    """Provider whose `complete()` runs a list of canned responses
    in order; each call pops the next one.
    """

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self.call_count = 0
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        system,
        messages,
        model,
        max_tokens,
        temperature,
        top_p,
        output_schema,
        timeout_seconds,
        tools=None,
    ) -> LLMResponse:
        self.call_count += 1
        self.calls.append(
            {
                "messages": list(messages),
                "tools": list(tools or []),
            }
        )
        return self._responses.pop(0)

    def estimate_cost(self, *, input_tokens, output_tokens, model) -> Decimal:
        return Decimal("0.001")

    def count_tokens(self, *, system, messages, model) -> int:
        return 100


def _register_tool() -> Tool:
    @tool(
        name="my_tool",
        description="t",
        input_schema=_ToolIn,
        output_schema=_ToolOut,
        deterministic=True,
    )
    def my_tool(args, ctx):
        return _ToolOut(answer=f"answer:{args.q}")

    from activegraph.tools.decorators import get_tool_registry

    return next(t for t in get_tool_registry() if t.name == "my_tool")


def _register_behaviors(*, tools=None):
    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("doc", {"title": "t"})

    received: list = []

    @llm_behavior(
        name="ex",
        on=["object.created"],
        where={"object.type": "doc"},
        output_schema=_Out,
        tools=tools or [],
    )
    def ex(event, graph, ctx, out):
        received.append(out)

    return received


# ---------- 0 / 1 / 2 / max turns ------------------------------------------


def test_zero_turn_loop_no_tools():
    """A behavior without tools is the v0.6 path: one LLM call, one parse."""
    received = _register_behaviors(tools=None)
    provider = _ScriptedProvider([
        _make_response(model="m", parsed=_Out(text="done"), tool_calls=None),
    ])
    Runtime(Graph(), llm_provider=provider).run_goal("g")
    assert len(received) == 1 and received[0].text == "done"
    assert provider.call_count == 1


def test_one_tool_turn():
    t = _register_tool()
    received = _register_behaviors(tools=[t])
    provider = _ScriptedProvider([
        _make_response(
            model="m",
            tool_calls=[ToolCall(id="c1", name="my_tool", args={"q": "x"})],
        ),
        _make_response(model="m", parsed=_Out(text="done"), tool_calls=None),
    ])
    g = Graph()
    Runtime(g, llm_provider=provider).run_goal("g")
    assert provider.call_count == 2
    assert len(received) == 1 and received[0].text == "done"
    # Trace has tool.requested + tool.responded
    types = [e.type for e in g.events]
    assert "tool.requested" in types
    assert "tool.responded" in types


def test_two_tool_turns_chain():
    t = _register_tool()
    received = _register_behaviors(tools=[t])
    provider = _ScriptedProvider([
        _make_response(
            model="m",
            tool_calls=[ToolCall(id="c1", name="my_tool", args={"q": "1"})],
        ),
        _make_response(
            model="m",
            tool_calls=[ToolCall(id="c2", name="my_tool", args={"q": "2"})],
        ),
        _make_response(model="m", parsed=_Out(text="done"), tool_calls=None),
    ])
    g = Graph()
    Runtime(g, llm_provider=provider).run_goal("g")
    assert provider.call_count == 3
    assert len(received) == 1
    tool_responded = [e for e in g.events if e.type == "tool.responded"]
    assert len(tool_responded) == 2


def test_max_tool_turns_exhausted_fails_loud():
    t = _register_tool()

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("doc", {"title": "t"})

    @llm_behavior(
        name="ex",
        on=["object.created"],
        where={"object.type": "doc"},
        output_schema=_Out,
        tools=[t],
        max_tool_turns=2,
    )
    def ex(event, graph, ctx, out):
        pass

    # Provider keeps returning tool_calls forever.
    forever_tool = [
        _make_response(
            model="m",
            tool_calls=[ToolCall(id=f"c{i}", name="my_tool", args={"q": str(i)})],
        )
        for i in range(10)
    ]
    provider = _ScriptedProvider(forever_tool)
    g = Graph()
    Runtime(g, llm_provider=provider).run_goal("g")
    failures = [e for e in g.events if e.type == "behavior.failed"]
    assert any(
        f.payload.get("reason") == "tool.max_turns_exhausted"
        for f in failures
    )


# ---------- budget enforcement ---------------------------------------------


def test_max_tool_calls_budget_triggers_behavior_failed():
    t = _register_tool()
    received = _register_behaviors(tools=[t])
    provider = _ScriptedProvider([
        _make_response(
            model="m",
            tool_calls=[ToolCall(id="c1", name="my_tool", args={"q": "1"})],
        ),
        _make_response(
            model="m",
            tool_calls=[ToolCall(id="c2", name="my_tool", args={"q": "2"})],
        ),
        _make_response(model="m", parsed=_Out(text="done"), tool_calls=None),
    ])
    g = Graph()
    rt = Runtime(g, llm_provider=provider, budget={"max_tool_calls": 1})
    rt.run_goal("g")
    # First tool call consumes max_tool_calls=1 → second one trips
    # budget.tool_calls_exhausted.
    failures = [e for e in g.events if e.type == "behavior.failed"]
    assert any(
        f.payload.get("reason") == "budget.tool_calls_exhausted"
        for f in failures
    )
    assert received == []  # handler never ran


# ---------- unknown-tool refusal -------------------------------------------


def test_unknown_tool_call_triggers_behavior_failed():
    t = _register_tool()
    _register_behaviors(tools=[t])
    provider = _ScriptedProvider([
        _make_response(
            model="m",
            tool_calls=[ToolCall(id="c1", name="nope_not_a_tool", args={})],
        ),
    ])
    g = Graph()
    Runtime(g, llm_provider=provider).run_goal("g")
    failures = [e for e in g.events if e.type == "behavior.failed"]
    assert any(f.payload.get("reason") == "tool.unknown_tool" for f in failures)


def test_returned_unique_short_tool_name_is_canonical_before_persistence():
    calls: list[str] = []
    dotted = Tool(
        name="demo.my_tool",
        fn=lambda args, ctx: calls.append(args.q) or _ToolOut(answer=args.q),
        input_schema=_ToolIn,
        output_schema=_ToolOut,
        deterministic=True,
    )
    received = _register_behaviors(tools=[dotted, dotted])
    original_call = ToolCall(id="c1", name="my_tool", args={"q": "x"})
    original_response = _make_response(model="m", tool_calls=[original_call])
    provider = _ScriptedProvider([
        original_response,
        _make_response(model="m", parsed=_Out(text="done"), tool_calls=None),
    ])
    cache = LLMCache()
    graph = Graph()
    rt = Runtime(
        graph,
        llm_provider=provider,
        tools=[dotted],
        llm_cache=cache,
    )
    rt.run_goal("g")

    assert calls == ["x"]
    assert received and received[0].text == "done"
    assert original_call.name == "my_tool"
    assert original_response.tool_calls == [original_call]
    responded = [e for e in graph.events if e.type == "llm.responded"]
    assert responded[0].payload["tool_calls"][0]["name"] == "demo.my_tool"
    assert provider.calls[1]["messages"][-2].tool_calls[0].name == "demo.my_tool"
    assert provider.calls[1]["messages"][-1].tool_name == "demo.my_tool"
    first_hash = next(e for e in graph.events if e.type == "llm.requested").payload[
        "prompt_hash"
    ]
    cached = cache.get(first_hash)
    assert cached is not None
    assert cached.tool_calls[0].name == "demo.my_tool"


@pytest.mark.parametrize(
    ("declared_names", "returned_name"),
    [
        (("demo.allowed",), "not_declared"),
        (("a.same", "b.same"), "same"),
    ],
)
def test_invalid_returned_tool_name_fails_before_success_cache_or_event(
    declared_names, returned_name
):
    declared = [
        Tool(name=name, fn=lambda args, ctx: {}, deterministic=True)
        for name in declared_names
    ]
    _register_behaviors(tools=declared)
    provider = _ScriptedProvider([
        _make_response(
            model="m",
            tool_calls=[ToolCall(id="c1", name=returned_name, args={})],
        )
    ])
    cache = LLMCache()
    graph = Graph()
    rt = Runtime(
        graph,
        llm_provider=provider,
        tools=declared,
        llm_cache=cache,
    )
    rt.run_goal("g")

    failures = [e for e in graph.events if e.type == "behavior.failed"]
    assert failures[-1].payload["reason"] == "tool.unknown_tool"
    assert failures[-1].payload["exception_type"] == "UnknownToolError"
    assert rt.budget.cost_used == Decimal("0.001")
    assert len(cache) == 0
    assert not [
        e
        for e in graph.events
        if e.type == "llm.responded" and "error" not in e.payload
    ]


# ---------- tool input validation fails loud -------------------------------


def test_bad_tool_input_emits_invalid_input_failure():
    t = _register_tool()
    _register_behaviors(tools=[t])
    provider = _ScriptedProvider([
        _make_response(
            model="m",
            # missing required `q` field
            tool_calls=[ToolCall(id="c1", name="my_tool", args={"wrong": "x"})],
        ),
    ])
    g = Graph()
    Runtime(g, llm_provider=provider).run_goal("g")
    failures = [e for e in g.events if e.type == "behavior.failed"]
    assert any(f.payload.get("reason") == "tool.invalid_input" for f in failures)


# ---------- missing tool at registration -----------------------------------


def test_missing_tool_at_registration_raises():
    from activegraph import MissingToolError

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("doc", {"title": "t"})

    @llm_behavior(
        name="ex",
        on=["object.created"],
        where={"object.type": "doc"},
        output_schema=_Out,
        tools=["nonexistent_tool_name"],
    )
    def ex(event, graph, ctx, out):
        pass

    provider = _ScriptedProvider([
        _make_response(model="m", parsed=_Out(text="x"), tool_calls=None),
    ])
    rt = Runtime(Graph(), llm_provider=provider)
    with pytest.raises(MissingToolError):
        rt.run_goal("g")
