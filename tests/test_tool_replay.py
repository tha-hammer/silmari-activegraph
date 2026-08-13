"""Tool replay semantics. CONTRACT v0.7 tool-determinism decision.

Default: ALL tools (deterministic or not) serve from cache on replay.
Opt-in: `replay_reinvoke_deterministic=True` lets deterministic tools
        re-invoke during replay.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel

from activegraph import (
    Event,
    Graph,
    Runtime,
    Tool,
    ToolContext,
    behavior,
    llm_behavior,
    tool,
)
from activegraph.llm import LLMResponse, ToolCall
from activegraph.packs import (
    Pack,
    llm_behavior as pack_llm_behavior,
    tool as pack_tool,
)
from activegraph.tools.cache import hash_tool_call


class _Out(BaseModel):
    text: str


class _In(BaseModel):
    n: int


class _ROut(BaseModel):
    n2: int


_call_count = 0


def _make_tool(*, deterministic: bool):
    """Build a tool whose body increments a counter so we can assert
    re-invocations.
    """
    global _call_count
    _call_count = 0

    @tool(
        name="counter",
        input_schema=_In,
        output_schema=_ROut,
        deterministic=deterministic,
    )
    def counter(args, ctx):
        global _call_count
        _call_count += 1
        return _ROut(n2=args.n * 2)

    from activegraph.tools.decorators import get_tool_registry
    return next(t for t in get_tool_registry() if t.name == "counter")


def _provider_calling_tool_once(tool_name: str = "counter"):
    """Provider that returns one tool_call then one final answer."""
    responses = [
        LLMResponse(
            raw_text="", parsed=None, input_tokens=10, output_tokens=5,
            cost_usd=Decimal("0.001"), latency_seconds=0.1, model="m",
            finish_reason="tool_use",
            tool_calls=[ToolCall(id="c1", name=tool_name, args={"n": 21})],
        ),
        LLMResponse(
            raw_text="", parsed=_Out(text="done"),
            input_tokens=10, output_tokens=5,
            cost_usd=Decimal("0.001"), latency_seconds=0.1, model="m",
            finish_reason="end_turn",
        ),
    ]

    class P:
        def __init__(self):
            self.i = 0

        def complete(self, **kw):
            r = responses[self.i]
            self.i += 1
            return r

        def estimate_cost(self, **kw):
            return Decimal("0.001")

        def count_tokens(self, **kw):
            return 100

    return P()


def _register_seed_and_user(tool_inst: Tool):
    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("doc", {"title": "t"})

    @llm_behavior(
        name="ex",
        on=["object.created"],
        where={"object.type": "doc"},
        output_schema=_Out,
        tools=[tool_inst],
    )
    def ex(event, graph, ctx, out):
        pass


def test_replay_serves_non_deterministic_tool_from_cache(tmp_path):
    global _call_count

    db = str(tmp_path / "run.db")
    t = _make_tool(deterministic=False)
    _register_seed_and_user(t)

    rt = Runtime(Graph(), llm_provider=_provider_calling_tool_once(),
                 persist_to=db)
    rt.run_goal("g")
    parent_calls = _call_count
    assert parent_calls == 1

    # Fork with both caches; the tool body should NOT execute again.
    from activegraph import clear_registry, clear_tool_registry
    fork_inst = t  # re-use same Tool object — name lookup is what matters
    _register_seed_and_user(fork_inst)  # idempotent re-registration is fine
    fork = rt.fork(
        at_event=next(e for e in rt.graph.events if e.type == "goal.created").id,
        label="cached",
        replay_llm_cache=True,
        replay_tool_cache=True,
        llm_provider=_provider_calling_tool_once(),
    )
    fork.run_until_idle()
    # Non-deterministic tool, default replay behavior: served from cache.
    assert _call_count == parent_calls  # i.e. still 1
    # And the trace shows the responded event with cache_hit=true.
    tr = [e for e in fork.graph.events if e.type == "tool.responded"]
    assert tr and tr[0].payload.get("cache_hit") is True


def test_replay_serves_deterministic_tool_from_cache_by_default(tmp_path):
    global _call_count

    db = str(tmp_path / "run.db")
    t = _make_tool(deterministic=True)
    _register_seed_and_user(t)

    rt = Runtime(Graph(), llm_provider=_provider_calling_tool_once(),
                 persist_to=db)
    rt.run_goal("g")
    parent_calls = _call_count
    assert parent_calls == 1

    fork = rt.fork(
        at_event=next(e for e in rt.graph.events if e.type == "goal.created").id,
        label="cached",
        replay_llm_cache=True,
        replay_tool_cache=True,
        llm_provider=_provider_calling_tool_once(),
    )
    fork.run_until_idle()
    # Default: even deterministic tools serve from cache on replay.
    assert _call_count == parent_calls


def test_replay_reinvoke_deterministic_actually_reinvokes(tmp_path):
    global _call_count

    db = str(tmp_path / "run.db")
    t = _make_tool(deterministic=True)
    _register_seed_and_user(t)

    rt = Runtime(Graph(), llm_provider=_provider_calling_tool_once(),
                 persist_to=db)
    rt.run_goal("g")
    parent_calls = _call_count
    assert parent_calls == 1

    fork = rt.fork(
        at_event=next(e for e in rt.graph.events if e.type == "goal.created").id,
        label="rerun",
        replay_llm_cache=True,
        replay_tool_cache=True,
        replay_reinvoke_deterministic=True,
        llm_provider=_provider_calling_tool_once(),
    )
    fork.run_until_idle()
    # Opt-in re-invoke: deterministic tool runs again in the fork.
    assert _call_count == parent_calls + 1


def test_two_turn_pack_short_call_is_canonical_before_replay_boundaries(tmp_path):
    invocations: list[int] = []

    @pack_tool(
        name="counter",
        input_schema=_In,
        output_schema=_ROut,
        deterministic=False,
    )
    def pack_counter(args, ctx):
        invocations.append(args.n)
        return _ROut(n2=args.n * 2)

    @pack_llm_behavior(
        name="worker",
        on=["goal.created"],
        output_schema=_Out,
        tools=[pack_counter],
    )
    def worker(event, graph, ctx, out):
        pass

    pack = Pack(
        name="replaypack",
        version="1.0",
        behaviors=(worker,),
        tools=(pack_counter,),
    )

    class ParentProvider:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []
            self.responses = [
                LLMResponse(
                    raw_text="",
                    parsed=None,
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=Decimal("0.001"),
                    latency_seconds=0.1,
                    model="m",
                    finish_reason="tool_use",
                    tool_calls=[
                        ToolCall(id="c1", name="counter", args={"n": 21})
                    ],
                ),
                LLMResponse(
                    raw_text="done",
                    parsed=_Out(text="done"),
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd=Decimal("0.001"),
                    latency_seconds=0.1,
                    model="m",
                    finish_reason="end_turn",
                ),
            ]

        def complete(self, **kwargs):
            self.calls.append(
                {
                    "tools": list(kwargs.get("tools") or []),
                    "messages": list(kwargs["messages"]),
                }
            )
            return self.responses.pop(0)

        def estimate_cost(self, **kwargs):
            return Decimal("0.001")

        def count_tokens(self, **kwargs):
            return 10

    class ReplayProvider(ParentProvider):
        def __init__(self):
            self.calls = []

        def complete(self, **kwargs):  # pragma: no cover - cache must win
            raise AssertionError("replay unexpectedly called the LLM provider")

    db = str(tmp_path / "pack-replay.db")
    parent_provider = ParentProvider()
    graph = Graph()
    parent = Runtime(graph, llm_provider=parent_provider, persist_to=db)
    goal = Event(
        id=graph.ids.event(),
        type="goal.created",
        payload={"goal": "g"},
        actor="user",
        frame_id=None,
        caused_by=None,
        timestamp=graph.clock.now(),
    )
    graph.emit(goal)
    parent.load_pack(pack)
    parent.run_until_idle()

    assert invocations == [21]
    assert [[t["name"] for t in c["tools"]] for c in parent_provider.calls] == [
        ["replaypack.counter"],
        ["replaypack.counter"],
    ]
    assert parent_provider.calls[1]["messages"][-2].tool_calls[0].name == (
        "replaypack.counter"
    )
    assert parent_provider.calls[1]["messages"][-1].tool_name == (
        "replaypack.counter"
    )

    parent_llm_responses = [
        e for e in graph.events if e.type == "llm.responded"
    ]
    assert parent_llm_responses[0].payload["tool_calls"][0]["name"] == (
        "replaypack.counter"
    )
    parent_tool_requested = next(
        e for e in graph.events if e.type == "tool.requested"
    )
    parent_tool_responded = next(
        e for e in graph.events if e.type == "tool.responded"
    )
    expected_args_hash = hash_tool_call(
        tool_name="replaypack.counter", args={"n": 21}
    )
    assert parent_tool_requested.payload["tool"] == "replaypack.counter"
    assert parent_tool_requested.payload["args_hash"] == expected_args_hash
    assert parent_tool_responded.payload["tool"] == "replaypack.counter"
    parent_hashes = [
        e.payload["prompt_hash"]
        for e in graph.events
        if e.type == "llm.requested"
    ]
    assert len(parent_hashes) == 2
    assert parent_hashes[0] != parent_hashes[1]

    replay_provider = ReplayProvider()
    fork = parent.fork(
        at_event=goal.id,
        label="canonical-pack-tool",
        replay_llm_cache=True,
        replay_tool_cache=True,
        llm_provider=replay_provider,
    )
    fork.load_pack(pack)
    fork.run_until_idle()

    assert replay_provider.calls == []
    assert invocations == [21]
    fork_llm_responses = [
        e for e in fork.graph.events if e.type == "llm.responded"
    ]
    assert [e.payload["cache_hit"] for e in fork_llm_responses] == [True, True]
    assert fork_llm_responses[0].payload["tool_calls"][0]["name"] == (
        "replaypack.counter"
    )
    fork_tool_requested = next(
        e for e in fork.graph.events if e.type == "tool.requested"
    )
    fork_tool_responded = next(
        e for e in fork.graph.events if e.type == "tool.responded"
    )
    assert fork_tool_requested.payload["tool"] == "replaypack.counter"
    assert fork_tool_requested.payload["args_hash"] == expected_args_hash
    assert fork_tool_responded.payload["tool"] == "replaypack.counter"
    assert fork_tool_responded.payload["cache_hit"] is True
    fork_hashes = [
        e.payload["prompt_hash"]
        for e in fork.graph.events
        if e.type == "llm.requested"
    ]
    assert fork_hashes == parent_hashes
