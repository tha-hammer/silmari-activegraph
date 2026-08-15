"""BAML-backed implementation of the activegraph LLM provider surface."""

from __future__ import annotations

import json
import math
import os
import re
import time
from decimal import Decimal
from functools import lru_cache
from typing import Any, Mapping

from activegraph.llm.errors import LLMBehaviorError
from activegraph.llm.parsing import parse_structured_response
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
    "openrouter_live": "openrouter/free",
}

_FALLBACK_FAMILY_BY_VENDOR = {
    "anthropic": "claude-sonnet-4",
    "anthropic_with_retry": "claude-sonnet-4",
    "fallback_cascade": "claude-sonnet-4",
    "openai": "gpt-4o",
    "openrouter": "gpt-4o",
    "openrouter_live": "gpt-4o",
}

_FUNCTION_BY_VENDOR = {
    "anthropic": "complete_anthropic",
    "anthropic_with_retry": "complete_anthropic_with_retry",
    "fallback_cascade": "complete_fallback_cascade",
    "openai": "complete_openai",
    "openrouter": "complete_openrouter",
    "openrouter_live": "complete_openrouter_live",
}

_HTTP_4XX_RE = re.compile(r"\b(4\d{2})\b")


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
        if vendor == "openrouter_live":
            selected_model = (
                model
                or os.environ.get("OPENROUTER_FREE_MODEL")
                or _DEFAULT_MODEL_BY_VENDOR[vendor]
            )
            # The 0.15 client config accepts env references or literals, but
            # not a defaulting expression. Keep the generated client and the
            # provider's advertised default on one source of truth.
            os.environ["OPENROUTER_FREE_MODEL"] = selected_model
        else:
            selected_model = model or _DEFAULT_MODEL_BY_VENDOR[vendor]
        self._model = selected_model
        self.default_model = selected_model
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
        """Call the generated sync BAML function and adapt its typed result."""
        if tools:
            raise LLMBehaviorError(
                "llm.request_error",
                "BAML providers do not support activegraph tool calls",
            )

        # Import lazily: pytest_configure must install local mock URLs before
        # the generated runtime reads environment-backed client options.
        from activegraph.baml_client import baml_sdk

        function_name = _FUNCTION_BY_VENDOR[self._vendor]
        generated_function = getattr(baml_sdk, function_name)
        messages_text = json.dumps(
            [message.to_dict() for message in messages],
            separators=(",", ":"),
            sort_keys=True,
        )

        started = time.monotonic()
        try:
            result = generated_function(
                system=system,
                messages_text=messages_text,
                timeout_ms=max(1, math.ceil(timeout_seconds * 1000)),
            )
        except Exception as exc:
            from baml_bridge import BamlError

            if isinstance(exc, BamlError):
                raise _translate_baml_error(exc) from exc
            raise
        latency_seconds = time.monotonic() - started

        # BAML 0.15's generated stub advertises the generated Pydantic class,
        # while the bridge currently materializes this class result as a
        # plain dict. Accept both shapes at this single compatibility seam.
        text = _result_field(result, "text")
        input_tokens = _result_field(result, "input_tokens")
        output_tokens = _result_field(result, "output_tokens")
        finish_reason = _result_field(result, "finish_reason")

        parsed = (
            parse_structured_response(text, output_schema)
            if output_schema is not None
            else None
        )
        return LLMResponse(
            raw_text=text,
            parsed=parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self.estimate_cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model=model,
            ),
            latency_seconds=latency_seconds,
            model=model,
            finish_reason=finish_reason,
            provider_meta={
                "baml_function": function_name,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "structured_output_mode": structured_output_mode,
            },
        )

    def estimate_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> Decimal:
        """Price actual or worst-case usage with per-million token rates."""
        in_price, out_price = self._pricing_for(model)
        weighted_tokens = (
            Decimal(input_tokens) * in_price
            + Decimal(output_tokens) * out_price
        )
        return weighted_tokens / Decimal(1_000_000)

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
        if self._vendor in {"openrouter", "openrouter_live"}:
            return "/" in name or name.startswith(("gpt-", "o1-", "o3-", "o4-"))
        return name.startswith(("gpt-", "o1-", "o3-", "o4-"))

    def supports_native_structured_output(self, model: str) -> bool:
        """BAML owns its output parsing; activegraph native mode stays off."""
        return False

    def _pricing_for(self, model: str) -> tuple[Decimal, Decimal]:
        if self._vendor in {"openrouter", "openrouter_live"} and (
            model == "openrouter/free" or model.endswith(":free")
        ):
            return Decimal(0), Decimal(0)
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


def _result_field(result: Any, name: str) -> Any:
    if isinstance(result, Mapping):
        return result[name]
    return getattr(result, name)


def _translate_baml_error(baml_exc: Any) -> LLMBehaviorError:
    """Translate BAML's typed error union to ActiveGraph's seven reasons.

    Unlike ``wire.classify_provider_exception``, this boundary also owns
    typed BAML parse/schema failures because the BAML runtime surfaces HTTP,
    prompt, and result parsing failures through the same error union.
    Unknown variants deliberately retain the transient network fallback.
    """
    from baml_bridge import BamlError

    from activegraph.baml_client.baml_sdk.baml import errors as baml_errors

    wrapper = baml_exc if isinstance(baml_exc, BamlError) else None
    value = wrapper.value if wrapper is not None else baml_exc
    variant_name = type(value).__name__
    if wrapper is not None and wrapper.class_name:
        variant_name = wrapper.class_name.rsplit(".", 1)[-1]

    if isinstance(value, Mapping):
        message = str(value.get("message", value))
    else:
        message = str(getattr(value, "message", value))
    lower_message = message.lower()
    status_match = _HTTP_4XX_RE.search(message)
    status = int(status_match.group(1)) if status_match else None

    # BAML 0.15 currently wraps real provider HTTP failures as DevOther and
    # carries only the vendor body (and sometimes the status) in ``message``.
    # Preserve wire.py's fixed classification precedence before considering
    # the typed variant name.
    if status == 429 or any(
        marker in lower_message
        for marker in ("rate_limit", "ratelimit", "rate limit")
    ):
        reason = "llm.rate_limited"
    elif status in (401, 403) or any(
        marker in lower_message
        for marker in ("authentication", "permission_denied", "permission denied")
    ):
        reason = "llm.auth_error"
    elif status is not None or any(
        marker in lower_message
        for marker in ("invalid_request", "badrequest", "unprocessable")
    ):
        reason = "llm.request_error"
    elif variant_name == baml_errors.ParseError.__name__:
        reason = "llm.parse_error"
    elif variant_name == baml_errors.TypeMismatch.__name__:
        reason = "llm.schema_violation"
    elif variant_name == baml_errors.AccessError.__name__:
        reason = "llm.auth_error"
    elif variant_name == baml_errors.LlmClient.__name__:
        if any(word in lower_message for word in ("parse", "json", "decode")):
            reason = "llm.parse_error"
        else:
            reason = "llm.network_error"
    elif variant_name in {
        baml_errors.InvalidArgument.__name__,
        baml_errors.Unsupported.__name__,
        baml_errors.RenderPrompt.__name__,
        baml_errors.NotImplemented.__name__,
        baml_errors.CompilationError.__name__,
    }:
        reason = "llm.request_error"
    else:
        reason = "llm.network_error"

    payload_extras: dict[str, Any] = {
        "exception_type": variant_name,
        "message": message,
    }
    if status is not None:
        payload_extras["status_code"] = status
    if wrapper is not None and wrapper.baml_trace:
        payload_extras["baml_trace"] = list(wrapper.baml_trace)
    return LLMBehaviorError(reason, message, payload_extras=payload_extras)
