"""Cost-budget enforcement and pre-call token counting
(CONTRACT v0.6 #9, #10, decision-4 adjustment).

Pre-call tokenization is paid ONLY when (max_cost_usd is set AND there
is no cached response). Cache hits are free; budget-less runs are
free. Budget exhaustion fires `behavior.failed reason="budget.cost_exhausted"`
without making the API call.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from activegraph import Graph, InvalidRuntimeConfiguration, Runtime, behavior, llm_behavior
from activegraph.llm import ClaudeCodeProvider, LLMMessage, LLMResponse

from tests._llm_helpers import Claim, ClaimList, ScriptedProvider


def _seed_doc():
    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "T", "body": "B"})


def _scripted():
    return ScriptedProvider(
        respond_fn=lambda m, s: ClaimList(claims=[Claim(text="x", confidence=0.9)])
    )


def test_cost_budget_blocks_call_when_exceeded():
    _seed_doc()

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        pass

    # Fixed cost = $0.0012 per call; cap is well below the conservative
    # pre-call estimate (which assumes max_tokens output).
    provider = _scripted()
    provider.fixed_cost = Decimal("9999")  # huge per-call cost

    g = Graph()
    Runtime(
        g,
        llm_provider=provider,
        budget={"max_cost_usd": "0.000001"},
    ).run_goal("g")

    # Provider's complete() was NEVER called.
    assert provider.call_log == []
    failed = next(e for e in g.events if e.type == "behavior.failed")
    assert failed.payload["reason"] == "budget.cost_exhausted"
    assert "estimated_cost_usd" in failed.payload
    assert "budget_remaining_usd" in failed.payload


def test_no_cost_budget_skips_count_tokens():
    _seed_doc()

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        pass

    provider = _scripted()
    g = Graph()
    Runtime(g, llm_provider=provider).run_goal("g")  # no max_cost_usd
    assert provider.token_count_log == []  # never called count_tokens
    assert len(provider.call_log) == 1


def test_cost_budget_set_calls_count_tokens_once():
    _seed_doc()

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        pass

    provider = _scripted()
    g = Graph()
    Runtime(
        g, llm_provider=provider, budget={"max_cost_usd": "10.00"}
    ).run_goal("g")
    assert len(provider.token_count_log) == 1
    assert len(provider.call_log) == 1


def test_actual_cost_replaces_estimate_in_budget_used():
    _seed_doc()

    @behavior(name="more", on=["goal.created"])
    def more(event, graph, ctx):
        # one extra doc, so two LLM calls total
        graph.add_object("document", {"title": "U", "body": "C"})

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        pass

    provider = _scripted()
    g = Graph()
    rt = Runtime(
        g, llm_provider=provider, budget={"max_cost_usd": "10.00"}
    )
    rt.run_goal("g")
    # Two calls * $0.0012 = $0.0024 actual cost
    assert rt.budget.cost_used == Decimal("0.0024")


def test_max_llm_calls_dimension_consumed_per_call():
    _seed_doc()

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        pass

    provider = _scripted()
    g = Graph()
    rt = Runtime(g, llm_provider=provider, budget={"max_llm_calls": 1})
    rt.run_goal("g")
    assert rt.budget.used["max_llm_calls"] == 1.0


# ---- capability-aware hard-budget rejection (v1.11 #1, Behavior 1) --------


def test_hard_max_cost_usd_rejects_binding_to_capability_limited_provider():
    _seed_doc()

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        pass

    provider = ClaudeCodeProvider(allow_unenforced_generation_controls=True)
    with pytest.raises(InvalidRuntimeConfiguration) as exc:
        Runtime(Graph(), llm_provider=provider, budget={"max_cost_usd": "1.00"})
    assert "max_cost_usd" in str(exc.value) or "enforces_max_tokens" in str(exc.value)


def test_no_hard_budget_binds_fine_to_capability_limited_provider():
    _seed_doc()

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        pass

    provider = ClaudeCodeProvider(allow_unenforced_generation_controls=True)
    Runtime(Graph(), llm_provider=provider, budget={"max_llm_calls": 5})  # must not raise


def test_non_cost_budget_dimensions_still_bind_to_capability_limited_provider():
    _seed_doc()

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, out):
        pass

    provider = ClaudeCodeProvider(allow_unenforced_generation_controls=True)
    rt = Runtime(
        Graph(),
        llm_provider=provider,
        budget={"max_events": 100, "max_behavior_calls": 50, "max_seconds": 60},
    )
    rt._ensure_registry()  # must not raise
