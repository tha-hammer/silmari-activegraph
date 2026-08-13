"""Priority metadata never reorders Runtime dispatch.

CONTRACT #10 fixes registration order for plain, LLM, relation, pack, and
delayed behavior dispatch.  ``priority`` is retained metadata only.
"""

from __future__ import annotations

from activegraph import Event, Graph, Pack, Runtime, behavior, llm_behavior
from activegraph import relation_behavior
from activegraph.packs import EmptySettings, behavior as pack_behavior

from tests._llm_helpers import ScriptedProvider


def _emit(graph: Graph, event_type: str, payload: dict) -> Event:
    event = Event(
        id=graph.ids.event(),
        type=event_type,
        payload=payload,
        actor="test",
        timestamp=graph.clock.now(),
    )
    graph.emit(event)
    return event


def _completed_for(graph: Graph, event_id: str) -> list[str]:
    return [
        event.payload["behavior"]
        for event in graph.events
        if event.type == "behavior.completed"
        and event.payload["event_id"] == event_id
    ]


def test_plain_priorities_dispatch_in_registration_order_for_unequal_and_tied_values():
    effects: list[str] = []

    @behavior(name="plain_low", on=["priority.plain"], priority=-100)
    def plain_low(event, graph, ctx):
        effects.append("plain_low")

    @behavior(name="plain_high", on=["priority.plain"], priority=100)
    def plain_high(event, graph, ctx):
        effects.append("plain_high")

    @behavior(name="plain_tied", on=["priority.plain"], priority=100)
    def plain_tied(event, graph, ctx):
        effects.append("plain_tied")

    graph = Graph()
    runtime = Runtime(graph, behaviors=[plain_low, plain_high, plain_tied])
    trigger = _emit(graph, "priority.plain", {})
    runtime.run_until_idle()

    expected = ["plain_low", "plain_high", "plain_tied"]
    assert effects == expected
    assert _completed_for(graph, trigger.id) == expected
    assert [b.name for b in runtime.status().registered_behaviors] == expected


def test_llm_priorities_dispatch_in_registration_order_for_unequal_and_tied_values():
    effects: list[str] = []

    @llm_behavior(name="llm_low", on=["priority.llm"], priority=-1)
    def llm_low(event, graph, ctx, llm_output):
        effects.append("llm_low")

    @llm_behavior(name="llm_high", on=["priority.llm"], priority=9)
    def llm_high(event, graph, ctx, llm_output):
        effects.append("llm_high")

    @llm_behavior(name="llm_tied", on=["priority.llm"], priority=9)
    def llm_tied(event, graph, ctx, llm_output):
        effects.append("llm_tied")

    provider = ScriptedProvider(respond_fn=lambda messages, schema: None)
    graph = Graph()
    runtime = Runtime(
        graph,
        behaviors=[llm_low, llm_high, llm_tied],
        llm_provider=provider,
    )
    trigger = _emit(graph, "priority.llm", {})
    runtime.run_until_idle()

    expected = ["llm_low", "llm_high", "llm_tied"]
    assert effects == expected
    assert _completed_for(graph, trigger.id) == expected
    assert [b.name for b in runtime.status().registered_behaviors] == expected


def test_relation_priorities_dispatch_in_registration_order_for_unequal_and_tied_values():
    effects: list[str] = []

    @relation_behavior(
        "links", name="relation_low", on=["priority.relation"], priority=-5
    )
    def relation_low(relation, event, graph, ctx):
        effects.append("relation_low")

    @relation_behavior(
        "links", name="relation_high", on=["priority.relation"], priority=5
    )
    def relation_high(relation, event, graph, ctx):
        effects.append("relation_high")

    @relation_behavior(
        "links", name="relation_tied", on=["priority.relation"], priority=5
    )
    def relation_tied(relation, event, graph, ctx):
        effects.append("relation_tied")

    graph = Graph()
    left = graph.add_object("node", {})
    right = graph.add_object("node", {})
    graph.add_relation(left.id, right.id, "links")
    runtime = Runtime(
        graph,
        behaviors=[relation_low, relation_high, relation_tied],
    )
    trigger = _emit(graph, "priority.relation", {"node_id": left.id})
    runtime.run_until_idle()

    expected = ["relation_low", "relation_high", "relation_tied"]
    assert effects == expected
    assert _completed_for(graph, trigger.id) == expected
    assert [b.name for b in runtime.status().registered_behaviors] == expected


def test_global_behaviors_remain_before_pack_behaviors_regardless_of_priority():
    effects: list[str] = []

    @behavior(name="global_first", on=["priority.pack"], priority=-50)
    def global_first(event, graph, ctx):
        effects.append("global_first")

    @pack_behavior(name="pack_second", on=["priority.pack"], priority=500)
    def pack_second(event, graph, ctx):
        effects.append("pack_second")

    pack = Pack(
        name="priority_pack",
        version="0.1.0",
        behaviors=(pack_second,),
        settings_schema=EmptySettings,
    )
    graph = Graph()
    runtime = Runtime(graph)
    runtime.load_pack(pack)
    trigger = _emit(graph, "priority.pack", {})
    runtime.run_until_idle()

    expected = ["global_first", "priority_pack.pack_second"]
    assert effects == ["global_first", "pack_second"]
    assert _completed_for(graph, trigger.id) == expected
    assert [b.name for b in runtime.status().registered_behaviors] == expected


def test_same_tick_delayed_behaviors_remain_fifo_regardless_of_priority():
    effects: list[str] = []

    @behavior(
        name="delayed_low",
        on=["priority.delay"],
        activate_after=1,
        priority=-10,
    )
    def delayed_low(event, graph, ctx):
        effects.append("delayed_low")

    @behavior(
        name="delayed_high",
        on=["priority.delay"],
        activate_after=1,
        priority=10,
    )
    def delayed_high(event, graph, ctx):
        effects.append("delayed_high")

    @behavior(
        name="delayed_tied",
        on=["priority.delay"],
        activate_after=1,
        priority=10,
    )
    def delayed_tied(event, graph, ctx):
        effects.append("delayed_tied")

    graph = Graph()
    runtime = Runtime(
        graph,
        behaviors=[delayed_low, delayed_high, delayed_tied],
    )
    trigger = _emit(graph, "priority.delay", {})
    _emit(graph, "priority.advance", {})
    runtime.run_until_idle()

    expected = ["delayed_low", "delayed_high", "delayed_tied"]
    assert effects == expected
    assert _completed_for(graph, trigger.id) == expected
    scheduled = [
        e.payload["behavior"]
        for e in graph.events
        if e.type == "behavior.scheduled" and e.payload["event_id"] == trigger.id
    ]
    assert scheduled == expected
