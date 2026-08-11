"""BAML-backed implementation of the activegraph LLM provider surface."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from activegraph.llm.types import LLMMessage


@lru_cache(maxsize=1)
def _token_encoding() -> Any:
    """Return the stable GPT-4-family tokenizer used for local estimates."""
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


class BamlLLMProvider:
    """Adapt generated BAML functions to activegraph's provider contract."""

    def __init__(self, *, vendor: str, model: str | None = None) -> None:
        self._vendor = vendor
        self._model = model

    def count_tokens(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
    ) -> int:
        """Count prompt text with a real local tokenizer.

        This is an independent estimate rather than a vendor-side counting
        request, matching the existing OpenAI provider's offline path.
        """
        encoding = _token_encoding()
        return len(encoding.encode(system)) + sum(
            len(encoding.encode(message.content)) for message in messages
        )
