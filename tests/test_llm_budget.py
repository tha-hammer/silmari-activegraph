"""Cost-budget enforcement and pre-call token counting
(CONTRACT v0.6 #9, #10, decision-4 adjustment).

Pre-call tokenization is paid ONLY when (max_cost_usd is set AND there
is no cached response). Cache hits are free; budget-less runs are
free. Budget exhaustion fires `behavior.failed reason="budget.cost_exhausted"`
without making the API call.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from activegraph import (
    Graph,
    InvalidRuntimeConfiguration,
    Runtime,
    behavior,
    llm_behavior,
    tool,
)
from activegraph.llm import (
    ClaudeCodeProvider,
    LLMMessage,
    LLMResponse,
    OpenRouterProvider,
    ToolCall,
)

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


def test_max_llm_calls_admits_one_behavior_with_two_provider_tool_turns():
    """The call budget counts an LLM behavior invocation, not provider turns."""

    @tool(name="lookup", description="Look up a value", deterministic=True)
    def lookup(args, ctx):
        return {"answer": f"answer:{args['q']}"}

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "T", "body": "B"})

    received: list[ClaimList] = []

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=ClaimList,
        tools=[lookup],
    )
    def extractor(event, graph, ctx, out):
        received.append(out)

    class _TwoTurnProvider:
        default_model = "test-model"

        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.responses = [
                LLMResponse(
                    raw_text="",
                    parsed=None,
                    input_tokens=1,
                    output_tokens=1,
                    cost_usd=Decimal("0"),
                    latency_seconds=0,
                    model=self.default_model,
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(id="call_1", name="lookup", args={"q": "x"})
                    ],
                ),
                LLMResponse(
                    raw_text='{"claims": [{"text": "done", "confidence": 0.9}]}',
                    parsed=ClaimList(
                        claims=[Claim(text="done", confidence=0.9)]
                    ),
                    input_tokens=1,
                    output_tokens=1,
                    cost_usd=Decimal("0"),
                    latency_seconds=0,
                    model=self.default_model,
                    finish_reason="stop",
                ),
            ]

        def complete(self, **kwargs):
            self.calls.append(kwargs)
            return self.responses.pop(0)

        def estimate_cost(self, **kwargs):
            return Decimal("0")

        def count_tokens(self, **kwargs):
            return 1

    provider = _TwoTurnProvider()
    g = Graph()
    rt = Runtime(g, llm_provider=provider, budget={"max_llm_calls": 1})

    rt.run_goal("g")

    assert len(provider.calls) == 2
    assert [message.role for message in provider.calls[1]["messages"][-2:]] == [
        "assistant",
        "tool",
    ]
    assert len(received) == 1
    assert received[0].claims[0].text == "done"
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


def _guarded_openrouter_provider():
    provider = OpenRouterProvider(client=object())
    provider.count_tokens = MagicMock(
        side_effect=AssertionError("binding validation called count_tokens")
    )
    provider.estimate_cost = MagicMock(
        side_effect=AssertionError("binding validation called estimate_cost")
    )
    provider.complete = MagicMock(
        side_effect=AssertionError("binding validation called the SDK")
    )
    return provider


def _openrouter_extractor(name="openrouter_extractor"):
    @llm_behavior(
        name=name,
        on=["object.created"],
        output_schema=ClaimList,
    )
    def extractor(event, graph, ctx, out):
        pass

    return extractor


def test_openrouter_hard_cost_budget_rejects_at_runtime_construction():
    _openrouter_extractor()
    provider = _guarded_openrouter_provider()

    with pytest.raises(InvalidRuntimeConfiguration, match="max_cost_usd"):
        Runtime(Graph(), llm_provider=provider, budget={"max_cost_usd": "1"})

    provider.count_tokens.assert_not_called()
    provider.estimate_cost.assert_not_called()
    provider.complete.assert_not_called()


def test_openrouter_hard_cost_budget_rejects_at_registry_initialization():
    from activegraph.runtime._live import _clear_for_test as _clear_live_runtimes

    provider = _guarded_openrouter_provider()
    rt = Runtime(Graph(), llm_provider=provider, budget={"max_cost_usd": "1"})
    _clear_live_runtimes()
    _openrouter_extractor()

    with pytest.raises(InvalidRuntimeConfiguration, match="max_cost_usd"):
        rt._ensure_registry()

    provider.count_tokens.assert_not_called()
    provider.estimate_cost.assert_not_called()
    provider.complete.assert_not_called()


def test_openrouter_hard_cost_budget_rejects_late_registration():
    provider = _guarded_openrouter_provider()
    rt = Runtime(Graph(), llm_provider=provider, budget={"max_cost_usd": "1"})

    with pytest.raises(InvalidRuntimeConfiguration, match="max_cost_usd"):
        _openrouter_extractor()

    # Keep the weakly tracked live Runtime reachable through registration.
    assert rt.llm_provider is provider
    provider.count_tokens.assert_not_called()
    provider.estimate_cost.assert_not_called()
    provider.complete.assert_not_called()


def test_empty_runtime_and_non_cost_budget_can_bind_openrouter():
    from activegraph.runtime._live import _clear_for_test as _clear_live_runtimes

    # With no LLM behavior there is no invalid provider/behavior binding yet.
    Runtime(
        Graph(),
        llm_provider=OpenRouterProvider(client=object()),
        budget={"max_cost_usd": "1"},
    )

    _clear_live_runtimes()
    _openrouter_extractor()
    Runtime(
        Graph(),
        llm_provider=OpenRouterProvider(client=object()),
        budget={"max_llm_calls": 1, "max_events": 100},
    )
