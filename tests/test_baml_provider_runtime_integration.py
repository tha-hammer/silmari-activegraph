"""Behavior 11: BamlLLMProvider completes one real turn through Runtime.

The plan's one BLOCKING closure test (Seam F) — proves `BamlLLMProvider`
is reachable in production the same way `AnthropicProvider`/`OpenAIProvider`
already are: constructed by a caller and handed to `Runtime(llm_provider=...)`,
driven by the real public entrypoint `Runtime.run_goal`, not a direct
`.complete()` call (that's Behaviors 1-10) and not the internal
`Runtime._invoke_llm_body`.
"""

from __future__ import annotations

from activegraph import Graph, Runtime, behavior, llm_behavior
from activegraph.llm import BamlLLMProvider
from activegraph.llm.cache import LLMCache


def _anthropic_response(answer_text: str) -> dict:
    return {
        "id": "msg_mock_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5",
        "content": [{"type": "text", "text": answer_text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 4},
    }


def test_one_real_turn_through_runtime_with_baml_provider(mock_llm_http_server):
    mock_llm_http_server.script_response(
        route="/anthropic",
        status=200,
        body=_anthropic_response(
            '{"text":"hi","input_tokens":12,'
            '"output_tokens":4,"finish_reason":"end_turn"}'
        ),
    )

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "T", "body": "B"})

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        description="Say hi.",
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    g = Graph()
    llm_cache = LLMCache()
    provider = BamlLLMProvider(vendor="anthropic")
    # CONTRACT correction #4 (plan Architecture section): estimate_cost() is
    # only called when `budget.has_cost_limit()` is true, so a cost limit
    # must be set for this test to actually exercise it.
    rt = Runtime(
        g,
        llm_provider=provider,
        llm_cache=llm_cache,
        budget={"max_cost_usd": 1.0},
    )
    rt.run_goal("Run extractor")

    responded = [e for e in g.events if e.type == "llm.responded"]
    assert len(responded) == 1
    assert not responded[0].payload.get("error")

    requested = next(e for e in g.events if e.type == "llm.requested")
    prompt_hash = requested.payload["prompt_hash"]
    assert llm_cache.has(prompt_hash)
    cached = llm_cache.get(prompt_hash)
    assert cached.raw_text == "hi"
    assert cached.input_tokens == 12
    assert cached.output_tokens == 4

    assert mock_llm_http_server.hits("/anthropic") == 1
