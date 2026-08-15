"""Behavior 6: generated BAML functions cross a real HTTP boundary."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from activegraph.llm import LLMMessage
from activegraph.llm.baml_provider import BamlLLMProvider


class _Out(BaseModel):
    n: int


def _anthropic_response(answer_text: str) -> dict:
    return {
        "id": "msg_mock_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5",
        "content": [{"type": "text", "text": answer_text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 137, "output_tokens": 42},
    }


def _complete(provider: BamlLLMProvider, *, output_schema: type | None = None):
    return provider.complete(
        system="You are concise.",
        messages=[LLMMessage(role="user", content="Say hi.")],
        model="claude-sonnet-4-5",
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=output_schema,
        timeout_seconds=30,
    )


def test_complete_succeeds_via_real_mock_http_server(
    mock_llm_http_server,
) -> None:
    mock_llm_http_server.script_response(
        route="/anthropic",
        status=200,
        body=_anthropic_response(
            '{"text":"hi","input_tokens":137,'
            '"output_tokens":42,"finish_reason":"end_turn"}'
        ),
    )

    response = _complete(BamlLLMProvider(vendor="anthropic"))

    assert response.raw_text == "hi"
    assert response.parsed is None
    assert response.input_tokens == 137
    assert response.output_tokens == 42
    assert response.cost_usd == Decimal("0.001041")
    assert response.latency_seconds >= 0
    assert response.model == "claude-sonnet-4-5"
    assert response.finish_reason == "end_turn"
    assert mock_llm_http_server.hits("/anthropic") == 1
    paths = mock_llm_http_server.request_paths()
    assert len(paths) == 1
    assert paths[0].startswith("/anthropic/")


def test_complete_parses_activegraph_output_schema_after_baml_call(
    mock_llm_http_server,
) -> None:
    mock_llm_http_server.script_response(
        route="/anthropic",
        status=200,
        body=_anthropic_response(
            '{"text":"{\\"n\\":42}","input_tokens":137,'
            '"output_tokens":42,"finish_reason":"end_turn"}'
        ),
    )

    response = _complete(
        BamlLLMProvider(vendor="anthropic"),
        output_schema=_Out,
    )

    assert isinstance(response.parsed, _Out)
    assert response.parsed.n == 42
    assert mock_llm_http_server.hits("/anthropic") == 1


def test_baml_provider_is_exported_from_public_llm_package() -> None:
    from activegraph.llm import BamlLLMProvider as PublicProvider

    assert PublicProvider is BamlLLMProvider
