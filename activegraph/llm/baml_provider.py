"""BAML-backed implementation of the activegraph LLM provider surface."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Any, Mapping

from activegraph.llm.types import LLMMessage, LLMResponse


_DEFAULT_PRICING: dict[str, dict[str, str]] = {
    "claude-opus-4": {"input": "15", "output": "75"},
    "claude-sonnet-4": {"input": "3", "output": "15"},
    "claude-haiku-4-5": {"input": "1", "output": "5"},
    "gpt-4o-mini": {"input": "0.15", "output": "0.6"},
    "gpt-4o": {"input": "2.5", "output": "10"},
}

_DEFAULT_MODEL_BY_VENDOR = {
    "anthropic": "claude-sonnet-4-5",
    "anthropic_with_retry": "claude-sonnet-4-5",
    "fallback_cascade": "claude-sonnet-4-5",
    "openai": "gpt-4o-mini",
    "openrouter": "gpt-4o-mini",
    "openrouter_live": "gpt-4o-mini",
}

_FALLBACK_FAMILY_BY_VENDOR = {
    "anthropic": "claude-sonnet-4",
    "anthropic_with_retry": "claude-sonnet-4",
    "fallback_cascade": "claude-sonnet-4",
    "openai": "gpt-4o",
    "openrouter": "gpt-4o",
    "openrouter_live": "gpt-4o",
}


@lru_cache(maxsize=1)
def _token_encoding() -> Any:
    """Return the stable GPT-4-family tokenizer used for local estimates."""
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


class BamlLLMProvider:
    """Adapt generated BAML functions to activegraph's provider contract."""

    def __init__(
        self,
        *,
        vendor: str,
        model: str | None = None,
        pricing: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        if vendor not in _DEFAULT_MODEL_BY_VENDOR:
            supported = ", ".join(sorted(_DEFAULT_MODEL_BY_VENDOR))
            raise ValueError(f"unknown BAML vendor {vendor!r}; expected one of {supported}")
        self._vendor = vendor
        self._model = model
        self.default_model = model or _DEFAULT_MODEL_BY_VENDOR[vendor]
        self._pricing = dict(pricing or _DEFAULT_PRICING)

    def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        output_schema: type | None,
        timeout_seconds: float,
        tools: list[dict[str, Any]] | None = None,
        structured_output_mode: str = "prompt",
    ) -> LLMResponse:
        """Complete through BAML (implemented by Behavior 6)."""
        raise NotImplementedError("BAML completion is implemented in Behavior 6")

    def estimate_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> Decimal:
        """Price actual or worst-case usage with per-million token rates."""
        in_price, out_price = self._pricing_for(model)
        million = Decimal("1000000")
        return (Decimal(input_tokens) * in_price / million) + (
            Decimal(output_tokens) * out_price / million
        )

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

    def recognizes_model(self, name: str) -> bool:
        if self._vendor.startswith("anthropic"):
            return name.startswith("claude-")
        if self._vendor == "fallback_cascade":
            return name.startswith(("claude-", "gpt-", "o1-", "o3-", "o4-"))
        return name.startswith(("gpt-", "o1-", "o3-", "o4-"))

    def supports_native_structured_output(self, model: str) -> bool:
        """BAML owns its output parsing; activegraph native mode stays off."""
        return False

    def _pricing_for(self, model: str) -> tuple[Decimal, Decimal]:
        best_key: str | None = None
        for key in self._pricing:
            if model.startswith(key) and (
                best_key is None or len(key) > len(best_key)
            ):
                best_key = key
        if best_key is None:
            best_key = _FALLBACK_FAMILY_BY_VENDOR[self._vendor]
        entry = self._pricing[best_key]
        return Decimal(str(entry["input"])), Decimal(str(entry["output"]))
