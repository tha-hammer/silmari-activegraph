"""Behavior 9: BAML cascades across real vendor-shaped HTTP clients."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from activegraph.llm import LLMBehaviorError, LLMMessage
from activegraph.llm.baml_provider import BamlLLMProvider


_MODEL = "claude-sonnet-4-5"
_CASCADE_ROUTES = ("/anthropic", "/openai", "/openrouter")


def _anthropic_failure(message: str = "anthropic unavailable") -> dict[str, Any]:
    return {
        "type": "error",
        "error": {"type": "api_error", "message": message},
    }


def _openai_failure(message: str) -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": "server_error",
            "param": None,
            "code": None,
        }
    }


def _openai_success() -> dict[str, Any]:
    envelope = json.dumps(
        {
            "text": "hi from fallback",
            "input_tokens": 7,
            "output_tokens": 3,
            "finish_reason": "stop",
        },
        separators=(",", ":"),
    )
    return {
        "id": "chatcmpl_fallback_success",
        "object": "chat.completion",
        "created": 0,
        "model": "openai/gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": envelope},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "total_tokens": 10,
        },
    }


def _complete():
    return BamlLLMProvider(vendor="fallback_cascade").complete(
        system="You are concise.",
        messages=[LLMMessage(role="user", content="Say hi through fallback.")],
        model=_MODEL,
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=None,
        timeout_seconds=30,
    )


def _observed_route_order(mock_llm_http_server: Any) -> list[str]:
    observed: list[str] = []
    for path in mock_llm_http_server.request_paths():
        route = next(
            (candidate for candidate in _CASCADE_ROUTES if path.startswith(candidate)),
            None,
        )
        if route is not None:
            observed.append(route)
    return observed


def _assert_one_ordered_hit_per_client(mock_llm_http_server: Any) -> None:
    assert _observed_route_order(mock_llm_http_server) == list(_CASCADE_ROUTES)
    assert [
        mock_llm_http_server.hits(route) for route in _CASCADE_ROUTES
    ] == [1, 1, 1]


def test_fallback_cascades_across_vendors_in_declared_order(
    mock_llm_http_server: Any,
) -> None:
    mock_llm_http_server.script_response(
        route="/anthropic",
        status=500,
        body=_anthropic_failure(),
    )
    mock_llm_http_server.script_response(
        route="/openai",
        status=500,
        body=_openai_failure("openai unavailable"),
    )
    mock_llm_http_server.script_response(
        route="/openrouter",
        status=200,
        body=_openai_success(),
    )

    response = _complete()

    assert response.raw_text == "hi from fallback"
    assert response.parsed is None
    assert response.input_tokens == 7
    assert response.output_tokens == 3
    assert response.cost_usd == Decimal("0.000066")
    assert response.latency_seconds >= 0
    assert response.model == _MODEL
    assert response.finish_reason == "stop"
    assert response.seed is None
    assert response.cache_hit is False
    assert response.tool_calls is None
    assert response.provider_meta["baml_function"] == "complete_fallback_cascade"
    _assert_one_ordered_hit_per_client(mock_llm_http_server)


def test_fallback_exhaustion_surfaces_one_translated_terminal_error(
    mock_llm_http_server: Any,
) -> None:
    mock_llm_http_server.script_response(
        route="/anthropic",
        status=500,
        body=_anthropic_failure(),
    )
    mock_llm_http_server.script_response(
        route="/openai",
        status=500,
        body=_openai_failure("openai unavailable"),
    )
    mock_llm_http_server.script_response(
        route="/openrouter",
        status=500,
        body=_openai_failure("openrouter unavailable"),
    )

    with pytest.raises(LLMBehaviorError) as caught:
        _complete()

    assert caught.value.reason == "llm.network_error"
    _assert_one_ordered_hit_per_client(mock_llm_http_server)
