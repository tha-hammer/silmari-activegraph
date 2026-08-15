"""Behavior 5: BamlLLMProvider counts prompt tokens locally."""

from __future__ import annotations

from hypothesis import given, strategies as st

from activegraph.llm import LLMMessage
from activegraph.llm.baml_provider import BamlLLMProvider


def test_count_tokens_matches_real_independent_tokenizer() -> None:
    import tiktoken

    system = "You are helpful."
    messages = [LLMMessage(role="user", content="Explain graph traversal.")]

    provider = BamlLLMProvider(vendor="anthropic")
    actual = provider.count_tokens(
        system=system,
        messages=messages,
        model="claude-sonnet-4-5",
    )

    encoding = tiktoken.get_encoding("cl100k_base")
    expected = len(encoding.encode(system)) + len(
        encoding.encode(messages[0].content)
    )
    assert actual == expected
    assert actual > 0


def test_count_tokens_handles_empty_messages() -> None:
    provider = BamlLLMProvider(vendor="anthropic")

    assert (
        provider.count_tokens(
            system="A system prompt.",
            messages=[],
            model="claude-sonnet-4-5",
        )
        > 0
    )


def test_count_tokens_handles_long_system_prompt() -> None:
    provider = BamlLLMProvider(vendor="anthropic")

    assert provider.count_tokens(
        system="token chunk " * 20_000,
        messages=[],
        model="claude-sonnet-4-5",
    ) > 20_000


@given(st.text(min_size=1, max_size=200))
def test_count_tokens_property_non_empty_text_is_positive(text: str) -> None:
    provider = BamlLLMProvider(vendor="anthropic")

    assert provider.count_tokens(
        system=text,
        messages=[],
        model="claude-sonnet-4-5",
    ) > 0


@given(
    st.text(min_size=1, max_size=100),
    st.text(min_size=1, max_size=100),
)
def test_count_tokens_property_adding_message_is_monotonic(
    prefix: str,
    suffix: str,
) -> None:
    provider = BamlLLMProvider(vendor="anthropic")
    base_messages = [LLMMessage(role="user", content=prefix)]

    base = provider.count_tokens(
        system="",
        messages=base_messages,
        model="claude-sonnet-4-5",
    )
    extended = provider.count_tokens(
        system="",
        messages=[*base_messages, LLMMessage(role="user", content=suffix)],
        model="claude-sonnet-4-5",
    )

    assert base <= extended
