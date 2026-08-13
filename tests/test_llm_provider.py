"""`LLMProviderCapabilities` — the additive capability descriptor.
CONTRACT v1.11 #1, Behavior 0.

Covers: the full default every legacy custom provider resolves to, the
exact limited descriptor `ClaudeCodeProvider` declares, value
immutability, `isinstance(..., LLMProvider)` conformance is unaffected,
and that the locked `LLMProvider` Protocol method signatures never grew
a `deterministic=` parameter on `complete()` or a `tools=` parameter on
`count_tokens()` — capability limits are declared data, not per-call
keyword arguments a provider must recognize.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from activegraph.llm import (
    AnthropicProvider,
    ClaudeCodeProvider,
    FULL_LLM_PROVIDER_CAPABILITIES,
    LLMProvider,
    LLMProviderCapabilities,
    OpenAIProvider,
    OpenRouterProvider,
    get_llm_provider_capabilities,
)


def test_full_capabilities_is_the_default():
    caps = LLMProviderCapabilities()
    assert caps.enforces_max_tokens is True
    assert caps.supports_sampling_controls is True
    assert caps.input_token_count == "official"
    assert caps.max_tool_calls_per_completion is None
    assert caps.requires_generation_control_acknowledgement is False
    assert caps == FULL_LLM_PROVIDER_CAPABILITIES


def test_legacy_custom_provider_resolves_to_full_capabilities():
    class _LegacyCustomProvider:
        default_model = "my-model"

        def complete(self, **kw):
            raise NotImplementedError

        def estimate_cost(self, **kw):
            raise NotImplementedError

        def count_tokens(self, **kw):
            raise NotImplementedError

    provider = _LegacyCustomProvider()
    assert not hasattr(provider, "llm_capabilities")
    assert get_llm_provider_capabilities(provider) is FULL_LLM_PROVIDER_CAPABILITIES


def test_anthropic_and_openai_resolve_to_full_capabilities():
    # Neither shipped pre-v1.11 provider declares llm_capabilities —
    # zero behavior change for them.
    assert not hasattr(AnthropicProvider(client=object()), "llm_capabilities")
    assert not hasattr(OpenAIProvider(client=object()), "llm_capabilities")
    assert get_llm_provider_capabilities(AnthropicProvider(client=object())) is (
        FULL_LLM_PROVIDER_CAPABILITIES
    )
    assert get_llm_provider_capabilities(OpenAIProvider(client=object())) is (
        FULL_LLM_PROVIDER_CAPABILITIES
    )


def test_claude_code_provider_declares_the_exact_limited_descriptor():
    provider = ClaudeCodeProvider()
    caps = get_llm_provider_capabilities(provider)
    assert caps.enforces_max_tokens is False
    assert caps.supports_sampling_controls is False
    assert caps.input_token_count == "estimate"
    assert caps.max_tool_calls_per_completion == 1
    assert caps.requires_generation_control_acknowledgement is True


def test_openrouter_provider_declares_estimated_input_counts():
    provider = OpenRouterProvider()
    caps = get_llm_provider_capabilities(provider)
    assert caps == LLMProviderCapabilities(
        enforces_max_tokens=True,
        supports_sampling_controls=True,
        input_token_count="estimate",
        max_tool_calls_per_completion=None,
        requires_generation_control_acknowledgement=False,
    )


def test_capabilities_are_frozen():
    caps = LLMProviderCapabilities()
    with pytest.raises(dataclasses.FrozenInstanceError):
        caps.enforces_max_tokens = False  # type: ignore[misc]


def test_capabilities_to_dict_is_json_safe():
    caps = get_llm_provider_capabilities(ClaudeCodeProvider())
    d = caps.to_dict()
    assert d == {
        "enforces_max_tokens": False,
        "supports_sampling_controls": False,
        "input_token_count": "estimate",
        "max_tool_calls_per_completion": 1,
        "requires_generation_control_acknowledgement": True,
    }


def test_isinstance_llm_provider_still_holds_for_every_shipped_provider():
    assert isinstance(AnthropicProvider(client=object()), LLMProvider)
    assert isinstance(OpenAIProvider(client=object()), LLMProvider)
    assert isinstance(ClaudeCodeProvider(), LLMProvider)
    assert isinstance(OpenRouterProvider(), LLMProvider)


# ---- locked Protocol signatures --------------------------------------------


def test_complete_signature_has_no_deterministic_parameter():
    sig = inspect.signature(LLMProvider.complete)
    assert "deterministic" not in sig.parameters
    assert "tools" in sig.parameters


def test_count_tokens_signature_has_no_tools_parameter():
    sig = inspect.signature(LLMProvider.count_tokens)
    assert "tools" not in sig.parameters
    assert set(sig.parameters) - {"self"} == {"system", "messages", "model"}
