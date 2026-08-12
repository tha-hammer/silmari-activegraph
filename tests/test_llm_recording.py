"""`RecordingLLMProvider`/`RecordedLLMProvider` capability delegation.
CONTRACT v1.11 #1.

`RecordingLLMProvider` must delegate the wrapped provider's declared
`llm_capabilities` and its `allow_unenforced_generation_controls` flag,
so Runtime's capability-binding validation sees identical constraints
whether a behavior is bound directly to a capability-limited provider
or to one wrapped in a recording shim. `RecordedLLMProvider` keeps the
full default (`FULL_LLM_PROVIDER_CAPABILITIES`) since fixture replay
does no live generation and has no spend to bound.
"""

from __future__ import annotations

from activegraph.llm import (
    FULL_LLM_PROVIDER_CAPABILITIES,
    AnthropicProvider,
    ClaudeCodeProvider,
    RecordedLLMProvider,
    RecordingLLMProvider,
    get_llm_provider_capabilities,
)


def test_recorded_provider_has_full_default_capabilities(tmp_path):
    p = RecordedLLMProvider(str(tmp_path))
    assert get_llm_provider_capabilities(p) is FULL_LLM_PROVIDER_CAPABILITIES


def test_recording_provider_delegates_full_capabilities_from_anthropic(tmp_path):
    inner = AnthropicProvider(client=object())
    p = RecordingLLMProvider(inner, str(tmp_path))
    assert get_llm_provider_capabilities(p) is FULL_LLM_PROVIDER_CAPABILITIES


def test_recording_provider_delegates_claude_code_limited_capabilities(tmp_path):
    inner = ClaudeCodeProvider()
    p = RecordingLLMProvider(inner, str(tmp_path))
    caps = get_llm_provider_capabilities(p)
    assert caps is get_llm_provider_capabilities(inner)
    assert caps.requires_generation_control_acknowledgement is True
    assert caps.supports_sampling_controls is False


def test_recording_provider_delegates_acknowledgement_flag(tmp_path):
    inner_off = ClaudeCodeProvider(allow_unenforced_generation_controls=False)
    inner_on = ClaudeCodeProvider(allow_unenforced_generation_controls=True)
    p_off = RecordingLLMProvider(inner_off, str(tmp_path))
    p_on = RecordingLLMProvider(inner_on, str(tmp_path))
    assert p_off.allow_unenforced_generation_controls is False
    assert p_on.allow_unenforced_generation_controls is True


def test_recording_provider_wrapping_a_legacy_provider_defaults_acknowledgement_false(tmp_path):
    class _Legacy:
        default_model = "x"

        def complete(self, **kw):
            raise NotImplementedError

        def estimate_cost(self, **kw):
            raise NotImplementedError

        def count_tokens(self, **kw):
            raise NotImplementedError

    p = RecordingLLMProvider(_Legacy(), str(tmp_path))
    assert p.allow_unenforced_generation_controls is False
    assert get_llm_provider_capabilities(p) is FULL_LLM_PROVIDER_CAPABILITIES
