"""Behavior 10: credential-gated real OpenRouter free-router smoke test."""

from __future__ import annotations

import json
import os

import pytest

from activegraph.llm import LLMBehaviorError, LLMMessage
from activegraph.llm.baml_provider import BamlLLMProvider


OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_FREE_MODEL = os.environ.setdefault(
    "OPENROUTER_FREE_MODEL",
    "openrouter/free",
)


def test_openrouter_live_generated_callable_exists() -> None:
    """Keep a real Red/Green signal even when the live credential is absent."""
    from activegraph.baml_client import baml_sdk

    assert callable(getattr(baml_sdk, "complete_openrouter_live"))


def test_openrouter_live_build_request_targets_stable_free_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    selected_model = "meta-llama/test-model:free"
    monkeypatch.setenv("OPENROUTER_FREE_MODEL", selected_model)
    from activegraph.baml_client import baml_sdk

    # complete_openrouter_live's client is a custom ai.Client
    # (OpenAiCompatClient, activegraph/baml_src/clients.baml) rather than a
    # BAML builtin -- BAML only auto-generates the `<fn>__build_request`
    # introspection helper for its own builtin client types, not for
    # functions using a custom client, so this calls the purpose-built
    # `complete_openrouter_live__test_build_request` debug function instead,
    # which mirrors the same model/credential/URL resolution without making
    # a live call.
    request = baml_sdk.complete_openrouter_live__test_build_request(
        system="Say hi.",
        messages_text="[]",
    )
    payload = json.loads(request["body"])

    assert request["method"] == "POST"
    assert request["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer test-key"
    assert payload["model"] == selected_model


def test_openrouter_live_provider_model_contract_tracks_selected_free_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_model = "meta-llama/test-model:free"
    monkeypatch.setenv("OPENROUTER_FREE_MODEL", selected_model)

    provider = BamlLLMProvider(vendor="openrouter_live")

    assert provider.default_model == selected_model
    assert provider.recognizes_model(selected_model) is True
    assert provider.estimate_cost(
        input_tokens=1_000,
        output_tokens=500,
        model=selected_model,
    ) == 0


@pytest.mark.live_llm
@pytest.mark.skipif(
    OPENROUTER_API_KEY is None,
    reason="set OPENROUTER_API_KEY to run live OpenRouter tests",
)
def test_complete_live_against_openrouter_free_model() -> None:
    provider = BamlLLMProvider(vendor="openrouter_live")

    try:
        response = provider.complete(
            system="Return a friendly greeting in exactly five words.",
            messages=[LLMMessage(role="user", content="Say hello.")],
            model=OPENROUTER_FREE_MODEL,
            max_tokens=32,
            temperature=0.0,
            top_p=1.0,
            output_schema=None,
            timeout_seconds=60,
        )
    except LLMBehaviorError as exc:
        if exc.reason == "llm.rate_limited":
            pytest.skip("OpenRouter free tier rate-limited this live request")
        raise

    assert response.raw_text.strip()
    assert response.input_tokens > 0
    assert response.output_tokens > 0
    assert response.cost_usd >= 0
    assert response.latency_seconds > 0
    assert response.model == OPENROUTER_FREE_MODEL
    assert response.finish_reason
    assert response.provider_meta["baml_function"] == "complete_openrouter_live"
