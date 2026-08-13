"""OpenRouter provider built on the OpenAI Chat Completions adapter.

Construction is deliberately offline.  The OpenAI SDK, API key, and live
client are resolved only when :meth:`complete` first crosses the provider
boundary; OpenRouter-specific request, response, and client policy is layered
through narrow protected hooks on :class:`~activegraph.llm.openai.OpenAIProvider`.

Every request uses ``max_completion_tokens`` and requires routed endpoints to
honor supplied parameters. Internally owned SDK clients disable SDK retries so
Runtime remains the retry owner. Valid completed responses take exact cost from
OpenRouter's returned ``usage.cost``; invalid accounting envelopes fail
terminally rather than risking a duplicate billed completion. Input token
counts remain local estimates, so Runtime rejects hard ``max_cost_usd``
bindings before any provider or tokenizer I/O.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Mapping, Optional

from activegraph.llm.errors import LLMBehaviorError
from activegraph.llm.openai import OpenAIProvider
from activegraph.llm.provider import LLMProviderCapabilities


_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_MODEL_RE = re.compile(
    r"~?[a-z0-9]+(?:[._-][a-z0-9]+)*/"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?::[a-z0-9]+(?:[._-][a-z0-9]+)*)?"
)
_PRICE_BOUNDARIES = frozenset("-._:")
_MISSING = object()

_OPENROUTER_ERROR_TYPE_REASONS = MappingProxyType(
    {
        "authentication": "llm.auth_error",
        "permission_denied": "llm.auth_error",
        "rate_limit_exceeded": "llm.rate_limited",
        "provider_overloaded": "llm.network_error",
        "provider_unavailable": "llm.network_error",
        "server": "llm.network_error",
        "timeout": "llm.network_error",
        "unmapped": "llm.network_error",
        "payment_required": "llm.request_error",
        "context_length_exceeded": "llm.request_error",
        "max_tokens_exceeded": "llm.request_error",
        "token_limit_exceeded": "llm.request_error",
        "string_too_long": "llm.request_error",
        "invalid_request": "llm.request_error",
        "invalid_prompt": "llm.request_error",
        "not_found": "llm.request_error",
        "precondition_failed": "llm.request_error",
        "payload_too_large": "llm.request_error",
        "unprocessable": "llm.request_error",
        "content_policy_violation": "llm.request_error",
        "refusal": "llm.request_error",
        "invalid_image": "llm.request_error",
        "image_too_large": "llm.request_error",
        "image_too_small": "llm.request_error",
        "unsupported_image_format": "llm.request_error",
        "image_not_found": "llm.request_error",
        "image_download_failed": "llm.request_error",
    }
)


def _decimal_price(value: Any, *, model: str, field: str) -> str:
    if type(value) not in (str, int, float, Decimal):
        raise TypeError(f"pricing[{model!r}][{field!r}] must be a decimal value")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(
            f"pricing[{model!r}][{field!r}] must be a decimal value"
        ) from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(
            f"pricing[{model!r}][{field!r}] must be finite and non-negative"
        )
    return str(value)


def _copy_pricing(
    pricing: Optional[Mapping[str, Mapping[str, str]]],
) -> dict[str, dict[str, str]]:
    if pricing is None:
        return {}
    copied: dict[str, dict[str, str]] = {}
    for model, entry in pricing.items():
        if not isinstance(entry, Mapping):
            raise TypeError(f"pricing[{model!r}] must be a mapping")
        if "input" not in entry or "output" not in entry:
            raise ValueError(
                f"pricing[{model!r}] must contain both 'input' and 'output'"
            )
        copied[str(model)] = {
            "input": _decimal_price(entry["input"], model=str(model), field="input"),
            "output": _decimal_price(
                entry["output"], model=str(model), field="output"
            ),
        }
    return copied


class OpenRouterProvider(OpenAIProvider):
    """Non-streaming OpenRouter adapter for direct Runtime injection.

    ``client=`` is an explicit ownership boundary: an injected compatible
    client bypasses key lookup, SDK construction, headers, retries, caching,
    and cleanup policy. Without it, the client is constructed lazily from
    ``OPENROUTER_API_KEY`` (or ``api_key_env=``), cached for ordinary serial
    use, and configured with ``max_retries=0``. ``timeout_seconds`` remains an
    SDK per-attempt timeout, not an end-to-end cancellation guarantee.

    ``pricing=`` is an optional ActiveGraph-owned table in USD per million
    tokens. It is used only by direct estimates; completed responses require
    finite, non-negative returned ``usage.cost`` and record
    ``cost_source=openrouter_usage`` provenance.
    """

    default_model = "openrouter/free"
    _provider_label = "OpenRouterProvider"
    _install_extra = "openrouter"
    llm_capabilities = LLMProviderCapabilities(
        enforces_max_tokens=True,
        supports_sampling_controls=True,
        input_token_count="estimate",
        max_tool_calls_per_completion=None,
        requires_generation_control_acknowledgement=False,
    )

    def __init__(
        self,
        *,
        api_key_env: str = "OPENROUTER_API_KEY",
        client: Any = None,
        pricing: Optional[Mapping[str, Mapping[str, str]]] = None,
        native_structured_output_models: Optional[tuple[str, ...]] = None,
        base_url: str = _DEFAULT_BASE_URL,
        app_url: Optional[str] = None,
        app_name: Optional[str] = None,
    ) -> None:
        owned_pricing = _copy_pricing(pricing)
        native_prefixes = tuple(native_structured_output_models or ())
        super().__init__(
            api_key_env=api_key_env,
            client=client,
            native_structured_output_models=native_prefixes,
            reasoning_model_prefixes=(),
        )
        # OpenAI's historical ``pricing=None`` behavior installs GPT defaults;
        # OpenRouter owns a distinct, empty-by-default table.
        self._pricing = owned_pricing
        self._native_prefixes = native_prefixes
        self._reasoning_prefixes = ()
        self._base_url = base_url
        self._app_url = app_url
        self._app_name = app_name

    def _sdk_client_kwargs(self, *, api_key: str) -> dict[str, Any]:
        if not api_key.strip():
            raise RuntimeError(
                f"OpenRouterProvider needs a non-empty {self._api_key_env} "
                f"in the environment."
            )
        headers: dict[str, str] = {}
        if self._app_url:
            headers["HTTP-Referer"] = self._app_url
        if self._app_name:
            headers["X-OpenRouter-Title"] = self._app_name
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": self._base_url,
            "max_retries": 0,
        }
        if headers:
            kwargs["default_headers"] = headers
        return kwargs

    def _request_policy_kwargs(
        self,
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
    ) -> dict[str, Any]:
        policy: dict[str, Any] = {
            "max_completion_tokens": int(max_tokens),
            "temperature": float(temperature),
            "extra_body": {"provider": {"require_parameters": True}},
        }
        if top_p < 1.0:
            policy["top_p"] = float(top_p)
        return policy

    def _validate_response(self, raw: Any, *, model: str) -> None:
        from activegraph.llm.wire import classify_provider_status

        choice = _first_choice(raw)
        choice_error = _extension_get(choice, "error")
        top_level_error = _extension_get(raw, "error")
        if choice_error is not _MISSING and choice_error is not None:
            error = choice_error
        elif top_level_error is not _MISSING and top_level_error is not None:
            error = top_level_error
        else:
            finish_reason = _first_finish_reason(raw)
            if finish_reason != "error":
                return
            raise LLMBehaviorError(
                "llm.network_error",
                "OpenRouter returned finish_reason='error' without an error object",
                payload_extras={
                    "model": model,
                    "status_code": None,
                    "finish_reason": finish_reason,
                },
            )

        metadata = _extension_get(error, "metadata")
        raw_status = _extension_get(error, "code")
        status_code = raw_status if type(raw_status) is int else None
        error_type = _bounded_scalar(_extension_get(metadata, "error_type"), 128)
        provider_code = _bounded_scalar(
            _extension_get(metadata, "provider_code"), 128
        )
        if provider_code is None and status_code is None:
            provider_code = _bounded_scalar(raw_status, 128)

        extras: dict[str, Any] = {
            "model": model,
            "status_code": status_code,
        }
        if error_type is not None:
            extras["error_type"] = error_type
        if provider_code is not None:
            extras["provider_code"] = provider_code
        finish_reason = _first_finish_reason(raw)
        if finish_reason is not None:
            extras["finish_reason"] = finish_reason

        reason = (
            _OPENROUTER_ERROR_TYPE_REASONS.get(error_type)
            if error_type is not None
            else None
        )
        if reason is None:
            reason = classify_provider_status(status_code)
        raise LLMBehaviorError(
            reason,
            "OpenRouter returned an in-band completion error",
            payload_extras=extras,
        )

    def _response_cost(
        self,
        raw: Any,
        *,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> Decimal:
        usage = _extension_get(raw, "usage")
        value = _extension_get(usage, "cost")
        if type(value) in (str, int, float, Decimal):
            try:
                cost = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError):
                cost = None
            if cost is not None and cost.is_finite() and cost >= 0:
                return cost

        extras: dict[str, Any] = {
            "model": model,
            "field": "usage.cost",
            "value_type": _value_type(value),
        }
        finish_reason = _first_finish_reason(raw)
        if finish_reason is not None:
            extras["finish_reason"] = finish_reason
        raise LLMBehaviorError(
            "llm.request_error",
            "the completed OpenRouter response did not contain a valid usage.cost",
            payload_extras=extras,
        )

    def _response_provider_meta(
        self,
        raw: Any,
        *,
        model: str,
    ) -> dict[str, Any]:
        return {"cost_source": "openrouter_usage"}

    def recognizes_model(self, name: str) -> bool:
        return _MODEL_RE.fullmatch(name) is not None

    def estimate_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> Decimal:
        normalized = model[1:] if model.startswith("~") else model
        if normalized == "openrouter/free" or (
            self.recognizes_model(model) and normalized.endswith(":free")
        ):
            return Decimal("0")

        best_key: Optional[str] = None
        for key in self._pricing:
            if normalized == key or (
                normalized.startswith(key)
                and len(normalized) > len(key)
                and normalized[len(key)] in _PRICE_BOUNDARIES
            ):
                if best_key is None or len(key) > len(best_key):
                    best_key = key
        if best_key is None:
            return Decimal("Infinity")

        entry = self._pricing[best_key]
        million = Decimal("1000000")
        return (
            Decimal(input_tokens) * Decimal(entry["input"]) / million
            + Decimal(output_tokens) * Decimal(entry["output"]) / million
        )


__all__ = ["OpenRouterProvider"]


def _extension_get(obj: Any, key: str) -> Any:
    if obj is _MISSING or obj is None:
        return _MISSING
    if isinstance(obj, Mapping):
        return obj.get(key, _MISSING)
    return getattr(obj, key, _MISSING)


def _value_type(value: Any) -> str:
    if value is _MISSING:
        return "missing"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return "other"


def _bounded_scalar(value: Any, limit: int) -> Optional[str]:
    if type(value) in (str, int, float, Decimal):
        return str(value)[:limit]
    return None


def _first_finish_reason(raw: Any) -> Optional[str]:
    choice = _first_choice(raw)
    return _bounded_scalar(_extension_get(choice, "finish_reason"), 64)


def _first_choice(raw: Any) -> Any:
    choices = _extension_get(raw, "choices")
    if choices is _MISSING or not choices:
        return _MISSING
    try:
        return choices[0]
    except (KeyError, IndexError, TypeError):
        return _MISSING
