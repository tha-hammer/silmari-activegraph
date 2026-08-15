"""Behavior 8: BAML owns a bounded retry sequence and whole-call timeout."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest

from activegraph import Graph, Runtime, behavior, llm_behavior
from activegraph.llm import LLMBehaviorError, LLMMessage
from activegraph.llm.baml_provider import BamlLLMProvider


_MODEL = "claude-sonnet-4-5"
_RETRY_ROUTE = "/retry-target"


def _anthropic_success(
    *,
    text: str = "hi after retries",
    input_tokens: int = 10,
    output_tokens: int = 5,
    finish_reason: str = "end_turn",
) -> dict[str, Any]:
    envelope = (
        "{"
        f'"text":"{text}",'
        f'"input_tokens":{input_tokens},'
        f'"output_tokens":{output_tokens},'
        f'"finish_reason":"{finish_reason}"'
        "}"
    )
    return {
        "id": "msg_retry_success",
        "type": "message",
        "role": "assistant",
        "model": _MODEL,
        "content": [{"type": "text", "text": envelope}],
        "stop_reason": finish_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }


def _anthropic_failure(message: str = "temporary upstream failure") -> dict[str, Any]:
    return {
        "type": "error",
        "error": {"type": "api_error", "message": message},
    }


def _complete(*, timeout_seconds: float = 30.0):
    return BamlLLMProvider(vendor="anthropic_with_retry").complete(
        system="You are concise.",
        messages=[LLMMessage(role="user", content="Say hi after retrying.")],
        model=_MODEL,
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=None,
        timeout_seconds=timeout_seconds,
    )


def _retry_paths(mock_llm_http_server: Any) -> list[str]:
    return [
        path
        for path in mock_llm_http_server.request_paths()
        if path.startswith(_RETRY_ROUTE)
    ]


def test_retry_policy_retries_transparently_inside_baml_runtime(
    mock_llm_http_server: Any,
) -> None:
    mock_llm_http_server.script_sequence(
        route=_RETRY_ROUTE,
        statuses_then_body=[
            (500, _anthropic_failure("first failure")),
            (500, _anthropic_failure("second failure")),
            (200, _anthropic_success()),
        ],
    )

    response = _complete()

    assert response.raw_text == "hi after retries"
    assert response.parsed is None
    assert response.input_tokens == 10
    assert response.output_tokens == 5
    assert response.cost_usd == Decimal("0.000105")
    assert response.latency_seconds >= 0
    assert response.model == _MODEL
    assert response.finish_reason == "end_turn"
    assert response.seed is None
    assert response.cache_hit is False
    assert response.tool_calls is None
    assert response.provider_meta["baml_function"] == (
        "complete_anthropic_with_retry"
    )
    assert mock_llm_http_server.hits(_RETRY_ROUTE) == 3
    assert _retry_paths(mock_llm_http_server) == [
        f"{_RETRY_ROUTE}/v1/messages",
        f"{_RETRY_ROUTE}/v1/messages",
        f"{_RETRY_ROUTE}/v1/messages",
    ]


def test_retry_exhaustion_surfaces_one_transient_error_after_three_attempts(
    mock_llm_http_server: Any,
) -> None:
    mock_llm_http_server.script_sequence(
        route=_RETRY_ROUTE,
        statuses_then_body=[
            (500, _anthropic_failure("first failure")),
            (500, _anthropic_failure("second failure")),
            (500, _anthropic_failure("third failure")),
        ],
    )

    with pytest.raises(LLMBehaviorError) as caught:
        _complete()

    assert caught.value.reason in {"llm.network_error", "llm.rate_limited"}
    assert mock_llm_http_server.hits(_RETRY_ROUTE) == 3
    assert _retry_paths(mock_llm_http_server) == [
        f"{_RETRY_ROUTE}/v1/messages",
        f"{_RETRY_ROUTE}/v1/messages",
        f"{_RETRY_ROUTE}/v1/messages",
    ]


def test_retry_compounding_is_bounded_by_outer_attempts_times_baml_attempts(
    mock_llm_http_server: Any,
) -> None:
    mock_llm_http_server.script_response(
        route=_RETRY_ROUTE,
        status=500,
        body=_anthropic_failure(),
    )

    @behavior(name="retry_compounding_seed", on=["goal.created"])
    def retry_compounding_seed(event, graph, ctx):
        graph.add_object("document", {"title": "T", "body": "B"})

    @llm_behavior(
        name="retry_compounding_extractor",
        on=["object.created"],
        where={"object.type": "document"},
        description="Exercise the real Runtime retry boundary.",
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def retry_compounding_extractor(event, graph, ctx, llm_output):
        raise AssertionError("an exhausted provider must not run its handler")

    graph = Graph()
    Runtime(
        graph,
        llm_provider=BamlLLMProvider(vendor="anthropic_with_retry"),
        llm_retry_max_attempts=2,
        llm_retry_initial_delay_seconds=0,
    ).run_goal("Run the retry compounding extractor")

    assert mock_llm_http_server.hits(_RETRY_ROUTE) == 2 * 3

    requested = [event for event in graph.events if event.type == "llm.requested"]
    errored = [
        event
        for event in graph.events
        if event.type == "llm.responded" and event.payload.get("error")
    ]
    assert len(requested) == 2
    assert len(errored) == 2
    assert [event.payload["error"]["reason"] for event in errored] == [
        "llm.network_error",
        "llm.network_error",
    ]
    assert all(event.payload["retryable"] is True for event in errored)
    assert requested[1].payload["retry_of"] == requested[0].id

    failures = [
        event
        for event in graph.events
        if event.type == "behavior.failed"
        and event.payload["behavior"] == "retry_compounding_extractor"
    ]
    assert len(failures) == 1
    assert failures[0].payload["reason"] == "llm.network_error"
    assert failures[0].payload["attempts"] == 2
    assert failures[0].payload["max_attempts"] == 2
    assert failures[0].payload["retry_exhausted"] is True


def test_timeout_bounds_the_entire_internal_retry_sequence(
    mock_llm_http_server: Any,
) -> None:
    mock_llm_http_server.script_response(
        route=_RETRY_ROUTE,
        status=200,
        body=_anthropic_success(text="too late"),
        delay_seconds=0.5,
    )

    started = time.monotonic()
    with pytest.raises(LLMBehaviorError) as caught:
        _complete(timeout_seconds=0.05)
    elapsed = time.monotonic() - started

    assert caught.value.reason == "llm.network_error"
    assert "timeout" in caught.value.payload_extras["message"].lower()
    assert elapsed < 0.4
    assert mock_llm_http_server.hits(_RETRY_ROUTE) == 1
