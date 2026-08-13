"""The raw LLM provider is exposed only to LLM invocation contexts."""

from __future__ import annotations

import pytest

from activegraph import (
    Event,
    Graph,
    MissingProviderError,
    Runtime,
    behavior,
    llm_behavior,
    relation_behavior,
)

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


def test_only_llm_invocation_context_exposes_the_configured_provider():
    captured: dict[str, object] = {}

    @behavior(name="plain_probe", on=["provider.plain"])
    def plain_probe(event, graph, ctx):
        captured["plain"] = ctx.llm_provider

    @relation_behavior("links", name="relation_probe", on=["provider.relation"])
    def relation_probe(relation, event, graph, ctx):
        captured["relation"] = ctx.llm_provider

    @llm_behavior(name="llm_probe", on=["provider.llm"])
    def llm_probe(event, graph, ctx, llm_output):
        captured["llm"] = ctx.llm_provider

    provider = ScriptedProvider(respond_fn=lambda messages, schema: None)
    graph = Graph()
    left = graph.add_object("node", {})
    right = graph.add_object("node", {})
    graph.add_relation(left.id, right.id, "links")
    runtime = Runtime(
        graph,
        behaviors=[plain_probe, relation_probe, llm_probe],
        llm_provider=provider,
    )

    _emit(graph, "provider.plain", {})
    _emit(graph, "provider.relation", {"node_id": left.id})
    _emit(graph, "provider.llm", {})
    runtime.run_until_idle()

    assert captured == {"plain": None, "relation": None, "llm": provider}


def test_plain_and_relation_contexts_remain_none_without_a_provider():
    captured: dict[str, object] = {}

    @behavior(name="plain_probe", on=["provider.plain"])
    def plain_probe(event, graph, ctx):
        captured["plain"] = ctx.llm_provider

    @relation_behavior("links", name="relation_probe", on=["provider.relation"])
    def relation_probe(relation, event, graph, ctx):
        captured["relation"] = ctx.llm_provider

    graph = Graph()
    left = graph.add_object("node", {})
    right = graph.add_object("node", {})
    graph.add_relation(left.id, right.id, "links")
    runtime = Runtime(graph, behaviors=[plain_probe, relation_probe])

    _emit(graph, "provider.plain", {})
    _emit(graph, "provider.relation", {"node_id": left.id})
    runtime.run_until_idle()

    assert captured == {"plain": None, "relation": None}


def test_missing_provider_fails_at_the_public_run_entry_not_decoration():
    @llm_behavior(name="needs_provider", on=["goal.created"])
    def needs_provider(event, graph, ctx, llm_output):
        raise AssertionError("handler must not run")

    runtime = Runtime(Graph(), behaviors=[needs_provider])

    with pytest.raises(MissingProviderError) as exc_info:
        runtime.run_goal("exercise registry construction")

    assert exc_info.value.behavior_name == "needs_provider"
    assert runtime.graph.events == []
