"""OpenRouter's standalone OpenAI-compatible LLM provider.

The suite intentionally drives the public provider surface.  Literal HTTP
transport and Runtime closure coverage are added in later TDD slices; this
first slice locks construction, identity, grammar, and owned pricing state.
"""

from __future__ import annotations

import inspect
import json
import os
import sys
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import openai
import pytest
from pydantic import BaseModel

from activegraph import Graph, Runtime, behavior, llm_behavior, tool
from activegraph.llm import (
    LLMBehaviorError,
    LLMProvider,
    LLMProviderCapabilities,
    OpenRouterProvider,
    get_llm_provider_capabilities,
)
from activegraph.llm.types import LLMMessage
from activegraph.llm.openrouter import _OPENROUTER_ERROR_TYPE_REASONS


class _Out(BaseModel):
    n: int


class _ClosureToolInput(BaseModel):
    q: str


class _ClosureToolOutput(BaseModel):
    answer: str


class _ClosureOutput(BaseModel):
    answer: str


_MISSING = object()


class _StringConversionExplodes:
    def __str__(self) -> str:
        raise RuntimeError("hostile __str__ must not escape validation")


class _HostileString(str):
    def __str__(self) -> str:
        raise RuntimeError("hostile str subclass must be treated as malformed")


def _raw_response(
    text: str | None = "ok",
    *,
    cost="0.001230",
    in_tok: int = 10,
    out_tok: int = 5,
    finish_reason: object = "stop",
    tool_calls=None,
):
    usage_values = {"prompt_tokens": in_tok, "completion_tokens": out_tok}
    if cost is not _MISSING:
        usage_values["cost"] = cost
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(**usage_values),
        model="openai/gpt-4o-mini-2026-01-01",
    )


def _client_returning(raw):
    client = MagicMock()
    client.chat.completions.create.return_value = raw
    return client


def _complete(provider: OpenRouterProvider, **overrides):
    kwargs = {
        "system": "system",
        "messages": [LLMMessage(role="user", content="hello")],
        "model": "openai/gpt-4o-mini",
        "max_tokens": 64,
        "temperature": 0.25,
        "top_p": 0.8,
        "output_schema": None,
        "timeout_seconds": 12.5,
    }
    kwargs.update(overrides)
    return provider.complete(**kwargs)


class _EnvironmentReadForbidden(dict[str, str]):
    def get(self, key, default=None):  # type: ignore[override]
        raise AssertionError(f"constructor read environment key {key!r}")

    def __getitem__(self, key: str) -> str:
        raise AssertionError(f"constructor read environment key {key!r}")


def test_constructor_signature_is_exact_and_keyword_only():
    signature = inspect.signature(OpenRouterProvider)
    assert list(signature.parameters) == [
        "api_key_env",
        "client",
        "pricing",
        "native_structured_output_models",
        "base_url",
        "app_url",
        "app_name",
    ]
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    assert signature.parameters["api_key_env"].default == "OPENROUTER_API_KEY"
    assert signature.parameters["client"].default is None
    assert signature.parameters["pricing"].default is None
    assert signature.parameters["native_structured_output_models"].default is None
    assert signature.parameters["base_url"].default == "https://openrouter.ai/api/v1"
    assert signature.parameters["app_url"].default is None
    assert signature.parameters["app_name"].default is None


def test_constructor_is_offline_and_copies_owned_configuration(monkeypatch):
    monkeypatch.setattr(os, "environ", _EnvironmentReadForbidden())
    pricing = {"openai/gpt-4": {"input": "1.25", "output": "3.5"}}
    native = ("openai/gpt-4",)

    provider = OpenRouterProvider(
        pricing=pricing,
        native_structured_output_models=native,
        base_url="https://router.invalid/v1/",
        app_url="https://example.invalid",
        app_name="ActiveGraph tests",
    )

    pricing["openai/gpt-4"]["input"] = "999"
    assert provider.estimate_cost(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        model="openai/gpt-4",
    ) == Decimal("4.75")
    assert provider.supports_native_structured_output("openai/gpt-4o") is True
    assert provider.supports_native_structured_output("anthropic/claude") is False
    assert provider._native_prefixes == tuple(native)
    assert provider._reasoning_prefixes == ()
    assert provider._base_url == "https://router.invalid/v1/"
    assert provider._app_url == "https://example.invalid"
    assert provider._app_name == "ActiveGraph tests"
    assert provider._client_cached is None


@pytest.mark.parametrize("pricing", [None, {}])
def test_none_and_empty_pricing_mean_an_empty_openrouter_table(pricing):
    provider = OpenRouterProvider(pricing=pricing)
    assert provider._pricing == {}
    assert provider.estimate_cost(
        input_tokens=0,
        output_tokens=0,
        model="openai/gpt-4o",
    ) == Decimal("Infinity")


@pytest.mark.parametrize(
    "model",
    [
        "openrouter/free",
        "openai/gpt-4o-mini",
        "anthropic/claude-3.5-sonnet:beta",
        "~openai/gpt-latest",
        "a/b",
        "owner.with-punctuation/model_name:v1-beta",
    ],
)
def test_recognizes_only_the_bounded_openrouter_model_grammar(model):
    assert OpenRouterProvider().recognizes_model(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "gpt-4o",
        "OPENAI/gpt-4o",
        "openai/GPT-4o",
        " openai/gpt-4o",
        "openai/gpt-4o ",
        "openai//gpt-4o",
        "openai/gpt/4o",
        "/gpt-4o",
        "openai/",
        "openai/.gpt",
        "openai/gpt.",
        "openai/gpt..4",
        "openai/gpt:",
        "openai/gpt:beta:two",
        "openai/gpt::beta",
        ".owner/model",
        "owner./model",
        "owner..part/model",
        "owner/-model",
        "owner/model-",
        "owner/model:.beta",
        "owner/model:beta.",
        "owner/model:beta..two",
        "~~openai/gpt",
    ],
)
def test_rejects_out_of_policy_openrouter_model_ids(model):
    assert OpenRouterProvider().recognizes_model(model) is False


@pytest.mark.parametrize(
    "pricing",
    [
        {"owner/model": {"input": "1"}},
        {"owner/model": {"output": "1"}},
        {"owner/model": {"input": True, "output": "1"}},
        {"owner/model": {"input": "1", "output": False}},
        {"owner/model": {"input": "wat", "output": "1"}},
        {"owner/model": {"input": "-0.01", "output": "1"}},
        {"owner/model": {"input": "NaN", "output": "1"}},
        {"owner/model": {"input": "Infinity", "output": "1"}},
        {"owner/model": {"input": "1", "output": "-Infinity"}},
    ],
)
def test_constructor_rejects_invalid_owned_pricing(pricing):
    with pytest.raises((TypeError, ValueError)):
        OpenRouterProvider(pricing=pricing)


def test_identity_and_capabilities_are_exact():
    import activegraph

    provider = OpenRouterProvider()
    assert provider.default_model == "openrouter/free"
    assert isinstance(provider, LLMProvider)
    assert not hasattr(activegraph, "OpenRouterProvider")
    assert get_llm_provider_capabilities(provider) == LLMProviderCapabilities(
        enforces_max_tokens=True,
        supports_sampling_controls=True,
        input_token_count="estimate",
        max_tool_calls_per_completion=None,
        requires_generation_control_acknowledgement=False,
    )


def test_internal_client_policy_is_exact_lazy_and_cached(monkeypatch):
    built = []
    live_client = object()

    def fake_openai(**kwargs):
        built.append(kwargs)
        return live_client

    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=fake_openai))
    provider = OpenRouterProvider(
        base_url="https://router.invalid/v1/",
        app_url="https://example.invalid/app",
        app_name="ActiveGraph",
    )

    assert provider._client() is live_client
    assert provider._client() is live_client
    assert built == [
        {
            "api_key": "router-key",
            "base_url": "https://router.invalid/v1/",
            "max_retries": 0,
            "default_headers": {
                "HTTP-Referer": "https://example.invalid/app",
                "X-OpenRouter-Title": "ActiveGraph",
            },
        }
    ]


@pytest.mark.parametrize(
    ("app_url", "app_name", "expected"),
    [
        (None, None, {}),
        ("", "", {}),
        ("https://example.invalid", None, {"HTTP-Referer": "https://example.invalid"}),
        (None, "Graph", {"X-OpenRouter-Title": "Graph"}),
    ],
)
def test_internal_client_omits_empty_attribution_headers(
    monkeypatch, app_url, app_name, expected
):
    built = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda **kwargs: built.append(kwargs) or object()),
    )
    OpenRouterProvider(app_url=app_url, app_name=app_name)._client()
    assert built[0] == {
        "api_key": "router-key",
        "base_url": "https://openrouter.ai/api/v1",
        "max_retries": 0,
        **({"default_headers": expected} if expected else {}),
    }


@pytest.mark.parametrize("api_key", ["", " ", "\t\n"])
def test_internal_client_rejects_empty_api_keys_before_sdk_construction(
    monkeypatch, api_key
):
    constructor = MagicMock()
    monkeypatch.setenv("OPENROUTER_API_KEY", api_key)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=constructor))
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        OpenRouterProvider()._client()
    constructor.assert_not_called()


def test_injected_client_bypasses_import_environment_and_configuration(monkeypatch):
    injected = _client_returning(_raw_response())
    monkeypatch.setattr(os, "environ", _EnvironmentReadForbidden())
    provider = OpenRouterProvider(
        client=injected,
        base_url="https://must-not-be-read.invalid",
        app_url="https://must-not-be-read.invalid/app",
        app_name="must-not-be-read",
    )
    assert provider._client() is injected
    _complete(provider)
    injected.close.assert_not_called()


def test_openrouter_missing_sdk_diagnostic_names_its_extra(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fail_openai(name, *args, **kwargs):
        if name == "openai":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_openai)
    with pytest.raises(RuntimeError, match=r"activegraph\[openrouter\]"):
        OpenRouterProvider()._client()


def test_openrouter_tokenizer_fallback_diagnostic_names_its_extra(
    monkeypatch, caplog
):
    import builtins

    real_import = builtins.__import__

    def fail_tiktoken(name, *args, **kwargs):
        if name == "tiktoken":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_tiktoken)
    caplog.set_level("DEBUG", logger="activegraph.llm.openai")
    provider = OpenRouterProvider(client=object())

    assert provider.count_tokens(
        system="0123",
        messages=[LLMMessage(role="user", content="01234567")],
        model="openai/gpt-4o-mini",
    ) == 3
    assert "OpenRouterProvider.count_tokens" in caplog.text
    assert "activegraph[openrouter]" in caplog.text


def test_every_request_enforces_parameters_and_uses_completion_token_limit():
    client = _client_returning(_raw_response())
    response = _complete(OpenRouterProvider(client=client))

    sent = client.chat.completions.create.call_args.kwargs
    assert sent == {
        "model": "openai/gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
        ],
        "timeout": 12.5,
        "max_completion_tokens": 64,
        "temperature": 0.25,
        "top_p": 0.8,
        "extra_body": {"provider": {"require_parameters": True}},
    }
    assert "max_tokens" not in sent
    assert response.cost_usd == Decimal("0.001230")
    assert response.provider_meta == {"cost_source": "openrouter_usage"}
    assert response.model == "openai/gpt-4o-mini-2026-01-01"


def test_default_top_p_is_omitted_but_required_provider_policy_remains():
    client = _client_returning(_raw_response())
    _complete(OpenRouterProvider(client=client), top_p=1.0)
    sent = client.chat.completions.create.call_args.kwargs
    assert "top_p" not in sent
    assert sent["extra_body"] == {"provider": {"require_parameters": True}}


def test_tools_merge_after_required_provider_policy():
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="pack__lookup", arguments='{"q": "x"}'),
    )
    client = _client_returning(
        _raw_response(
            None,
            tool_calls=[tool_call],
            finish_reason="tool_calls",
        )
    )
    response = _complete(
        OpenRouterProvider(client=client),
        tools=[
            {
                "name": "pack.lookup",
                "description": "look up",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )
    sent = client.chat.completions.create.call_args.kwargs
    assert sent["extra_body"] == {"provider": {"require_parameters": True}}
    assert sent["tools"][0]["function"]["name"] == "pack__lookup"
    assert response.tool_calls[0].name == "pack.lookup"


def test_native_schema_merges_after_required_provider_policy():
    client = _client_returning(_raw_response('{"n": 7}'))
    response = _complete(
        OpenRouterProvider(
            client=client,
            native_structured_output_models=("openai/gpt-4o",),
        ),
        output_schema=_Out,
        structured_output_mode="native",
    )
    sent = client.chat.completions.create.call_args.kwargs
    assert sent["extra_body"] == {"provider": {"require_parameters": True}}
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert response.parsed == _Out(n=7)


@pytest.mark.parametrize(
    ("model", "input_tokens", "output_tokens", "expected"),
    [
        ("openrouter/free", 10_000_000, 10_000_000, Decimal("0")),
        ("vendor/model:free", 10_000_000, 10_000_000, Decimal("0")),
        ("~vendor/model:free", 10_000_000, 10_000_000, Decimal("0")),
        ("openai/gpt-4", 1_000_000, 1_000_000, Decimal("5")),
        ("openai/gpt-4-mini", 1_000_000, 1_000_000, Decimal("1.5")),
        ("~openai/gpt-4-mini:beta", 2_000_000, 3_000_000, Decimal("4")),
        ("openai/gpt-4o", 0, 0, Decimal("Infinity")),
        ("vendor/unknown", 0, 0, Decimal("Infinity")),
    ],
)
def test_estimate_cost_uses_free_policy_and_boundary_safe_longest_match(
    model, input_tokens, output_tokens, expected
):
    provider = OpenRouterProvider(
        pricing={
            "openai/gpt-4": {"input": "2", "output": "3"},
            "openai/gpt-4-mini": {"input": "0.5", "output": "1"},
        }
    )
    assert provider.estimate_cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=model,
    ) == expected


@pytest.mark.parametrize(
    ("cost", "value_type"),
    [
        (_MISSING, "missing"),
        (None, "null"),
        (True, "bool"),
        (False, "bool"),
        ("not-money", "str"),
        (-1, "int"),
        (-0.25, "float"),
        ("NaN", "str"),
        ("Infinity", "str"),
        (Decimal("-Infinity"), "decimal"),
        (object(), "other"),
    ],
)
def test_invalid_realized_cost_is_terminal_and_bounded(cost, value_type):
    provider = OpenRouterProvider(client=_client_returning(_raw_response(cost=cost)))
    with pytest.raises(LLMBehaviorError) as exc:
        _complete(provider)
    assert exc.value.reason == "llm.request_error"
    assert exc.value.payload_extras == {
        "model": "openai/gpt-4o-mini",
        "field": "usage.cost",
        "value_type": value_type,
        "finish_reason": "stop",
    }
    assert "cost_source" not in exc.value.payload_extras


def test_invalid_realized_cost_bounds_finish_reason_without_copying_value():
    finish = "x" * 100
    provider = OpenRouterProvider(
        client=_client_returning(_raw_response(cost=None, finish_reason=finish))
    )
    with pytest.raises(LLMBehaviorError) as exc:
        _complete(provider)
    assert exc.value.payload_extras == {
        "model": "openai/gpt-4o-mini",
        "field": "usage.cost",
        "value_type": "null",
        "finish_reason": "x" * 64,
    }


def test_invalid_completed_cost_is_not_retried_by_runtime():
    @behavior(name="seed_invalid_openrouter_cost", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "Cost"})

    @llm_behavior(
        name="invalid_openrouter_cost",
        on=["object.created"],
        where={"object.type": "document"},
        output_schema=_Out,
    )
    def consume(event, graph, ctx, output):
        graph.add_object("should_not_exist", {})

    client = _client_returning(
        _raw_response('{"n": 1}', cost=_StringConversionExplodes())
    )
    graph = Graph()
    Runtime(
        graph,
        llm_provider=OpenRouterProvider(client=client),
        llm_retry_max_attempts=3,
        llm_retry_initial_delay_seconds=0,
    ).run_goal("cost")

    assert client.chat.completions.create.call_count == 1
    failed = next(
        event
        for event in graph.events
        if event.type == "behavior.failed"
        and event.payload["behavior"] == "invalid_openrouter_cost"
    )
    assert failed.payload["reason"] == "llm.request_error"
    [errored_response] = [
        event
        for event in graph.events
        if event.type == "llm.responded" and event.payload.get("error")
    ]
    assert errored_response.payload["retryable"] is False
    assert errored_response.payload["error"]["value_type"] == "other"
    assert not graph.objects(type="should_not_exist")


@pytest.mark.parametrize(
    ("cost", "expected"),
    [
        (0, Decimal("0")),
        (1, Decimal("1")),
        (0.125, Decimal("0.125")),
        ("0.001230", Decimal("0.001230")),
        (Decimal("2.50"), Decimal("2.50")),
    ],
)
def test_valid_realized_cost_is_exact_and_records_provenance(cost, expected):
    response = _complete(
        OpenRouterProvider(client=_client_returning(_raw_response(cost=cost)))
    )
    assert response.cost_usd == expected
    assert response.provider_meta == {"cost_source": "openrouter_usage"}


def _error_response(
    error,
    *,
    top_level_error=_MISSING,
    finish_reason="error",
    text="partial output that must not escape",
):
    choice = SimpleNamespace(
        message=SimpleNamespace(content=text, tool_calls=None),
        finish_reason=finish_reason,
        error=error,
    )
    values = {
        "choices": [choice],
        "usage": SimpleNamespace(prompt_tokens=0, completion_tokens=0, cost=None),
        "model": "provider/concrete-model",
    }
    if top_level_error is not _MISSING:
        values["error"] = top_level_error
    return SimpleNamespace(**values)


def _provider_error(
    *,
    code=500,
    error_type=None,
    provider_code=_MISSING,
):
    metadata = {}
    if error_type is not None:
        metadata["error_type"] = error_type
    if provider_code is not _MISSING:
        metadata["provider_code"] = provider_code
    return {"code": code, "message": "volatile provider prose", "metadata": metadata}


def test_error_type_table_is_complete_and_immutable():
    expected = {
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
    assert dict(_OPENROUTER_ERROR_TYPE_REASONS) == expected
    with pytest.raises(TypeError):
        _OPENROUTER_ERROR_TYPE_REASONS["future"] = "llm.network_error"


@pytest.mark.parametrize(
    ("error_type", "expected_reason"),
    list(_OPENROUTER_ERROR_TYPE_REASONS.items()),
)
def test_every_known_error_type_wins_over_numeric_status(
    error_type, expected_reason
):
    # The numeric status deliberately conflicts with several typed rows.
    error = _provider_error(code=401, error_type=error_type)
    provider = OpenRouterProvider(client=_client_returning(_error_response(error)))
    with pytest.raises(LLMBehaviorError) as exc:
        _complete(provider)
    assert exc.value.reason == expected_reason
    assert exc.value.payload_extras == {
        "model": "openai/gpt-4o-mini",
        "status_code": 401,
        "error_type": error_type,
        "finish_reason": "error",
    }


def test_choice_error_precedes_defensive_top_level_error_and_partial_parsing():
    choice_error = _provider_error(
        code=429,
        error_type="rate_limit_exceeded",
        provider_code="upstream_choice",
    )
    top_error = _provider_error(
        code=401,
        error_type="authentication",
        provider_code="top_level",
    )
    provider = OpenRouterProvider(
        client=_client_returning(
            _error_response(choice_error, top_level_error=top_error, text='{"n": 9}')
        )
    )
    with pytest.raises(LLMBehaviorError) as exc:
        _complete(provider, output_schema=_Out)
    assert exc.value.reason == "llm.rate_limited"
    assert exc.value.payload_extras == {
        "model": "openai/gpt-4o-mini",
        "status_code": 429,
        "error_type": "rate_limit_exceeded",
        "provider_code": "upstream_choice",
        "finish_reason": "error",
    }
    assert "partial" not in str(exc.value)
    assert "volatile provider prose" not in str(exc.value)


def test_mapping_shaped_top_level_error_is_the_defensive_fallback():
    raw = {
        "choices": [
            {
                "message": {"content": "partial"},
                "finish_reason": "error",
            }
        ],
        "error": _provider_error(code=403, error_type="future_type"),
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": "9"},
        "model": "ignored/concrete",
    }
    provider = OpenRouterProvider(client=_client_returning(raw))
    with pytest.raises(LLMBehaviorError) as exc:
        _complete(provider)
    assert exc.value.reason == "llm.auth_error"
    assert exc.value.payload_extras == {
        "model": "openai/gpt-4o-mini",
        "status_code": 403,
        "error_type": "future_type",
        "finish_reason": "error",
    }


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (408, "llm.network_error"),
        (409, "llm.request_error"),
        (429, "llm.rate_limited"),
        (401, "llm.auth_error"),
        (422, "llm.request_error"),
        (500, "llm.network_error"),
        (None, "llm.network_error"),
    ],
)
def test_unknown_typed_error_uses_the_shared_numeric_fallback(status, reason):
    provider = OpenRouterProvider(
        client=_client_returning(
            _error_response(_provider_error(code=status, error_type="future_type"))
        )
    )
    with pytest.raises(LLMBehaviorError) as exc:
        _complete(provider)
    assert exc.value.reason == reason


@pytest.mark.parametrize("malformed_status", ["408", True, False, 408.0, [], {}])
def test_malformed_status_is_none_and_never_reaches_numeric_classifier(
    malformed_status,
):
    provider = OpenRouterProvider(
        client=_client_returning(
            _error_response(_provider_error(code=malformed_status))
        )
    )
    with pytest.raises(LLMBehaviorError) as exc:
        _complete(provider)
    assert exc.value.reason == "llm.network_error"
    assert exc.value.payload_extras["status_code"] is None
    if isinstance(malformed_status, (str, float)) and not isinstance(
        malformed_status, bool
    ):
        assert exc.value.payload_extras["provider_code"] == str(malformed_status)
    else:
        assert "provider_code" not in exc.value.payload_extras


def test_hostile_error_scalar_falls_back_to_numeric_status_without_crashing():
    provider = OpenRouterProvider(
        client=_client_returning(
            _error_response(
                _provider_error(code=401, error_type=_HostileString("authentication"))
            )
        )
    )

    with pytest.raises(LLMBehaviorError) as exc:
        _complete(provider)

    assert exc.value.reason == "llm.auth_error"
    assert exc.value.payload_extras == {
        "model": "openai/gpt-4o-mini",
        "status_code": 401,
        "finish_reason": "error",
    }


def test_in_band_payload_fields_are_bounded_and_never_copy_raw_objects():
    error = _provider_error(
        code="provider-code-" + "c" * 200,
        error_type="future-" + "t" * 200,
        provider_code="upstream-" + "p" * 200,
    )
    provider = OpenRouterProvider(
        client=_client_returning(
            _error_response(error, finish_reason="finish-" + "f" * 100)
        )
    )
    with pytest.raises(LLMBehaviorError) as exc:
        _complete(provider)
    extras = exc.value.payload_extras
    assert set(extras) == {
        "model",
        "status_code",
        "error_type",
        "provider_code",
        "finish_reason",
    }
    assert extras["status_code"] is None
    assert len(extras["error_type"]) == 128
    assert len(extras["provider_code"]) == 128
    assert len(extras["finish_reason"]) == 64
    assert all(not isinstance(value, (dict, list)) for value in extras.values())


def test_finish_error_without_error_object_is_transient_malformed_response():
    raw = _error_response(None)
    provider = OpenRouterProvider(client=_client_returning(raw))
    with pytest.raises(LLMBehaviorError) as exc:
        _complete(provider)
    assert exc.value.reason == "llm.network_error"
    assert exc.value.payload_extras == {
        "model": "openai/gpt-4o-mini",
        "status_code": None,
        "finish_reason": "error",
    }


# ---------------------------------------------------------------------------
# Behavior 7: packaging, CI, and the deterministic API-key example


def _repository_root():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent


def _optional_dependencies():
    import tomllib

    return tomllib.loads(
        (_repository_root() / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["optional-dependencies"]


def _dependency_name(requirement: str) -> str:
    import re

    return re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0].lower()


def _load_babyagi_example():
    import importlib.util

    path = _repository_root() / "examples" / "babyagi.py"
    spec = importlib.util.spec_from_file_location("_activegraph_babyagi_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_openrouter_extra_and_openai_floors_are_exact_without_aggregate_duplicates():
    extras = _optional_dependencies()
    expected_openrouter = ["openai>=1.55.3", "tiktoken>=0.7", "pydantic>=2"]

    assert extras["openrouter"] == expected_openrouter
    assert extras["openai"] == expected_openrouter
    for aggregate in ("llm", "all"):
        assert "openai>=1.55.3" in extras[aggregate]
        names = [_dependency_name(item) for item in extras[aggregate]]
        assert len(names) == len(set(names))


def test_openrouter_packaging_preserves_baml_claude_and_test_dependency_contracts():
    source = (_repository_root() / "pyproject.toml").read_text(encoding="utf-8")
    extras = _optional_dependencies()
    baml_block = """# Runtime bridge for activegraph/baml_src's generated Python client
# (activegraph.baml_client, output_type = \"python/pydantic\" in
# activegraph/baml.toml). Not yet consumed by any LLMProvider — kept
# out of [all]/[dev] like falkordb, so opting in is explicit until the
# llm/ integration lands.
baml = [\"baml_bridge\"]"""
    claude_dependencies = [
        "claude-agent-sdk==0.2.135",
        "anyio>=4,<5",
        "sniffio>=1,<2",
        "pydantic>=2",
    ]
    expected_dev_dependencies = [
        "build>=1.2",
        "pytest>=7",
        "pydantic>=2",
        "prometheus_client>=0.20",
        "psycopg[binary]>=3.1,<4",
        "opentelemetry-api>=1.25",
        "opentelemetry-sdk>=1.25",
        "claude-agent-sdk==0.2.135",
        "anyio>=4,<5",
        "sniffio>=1,<2",
        "trio>=0.25,<1",
        "hypothesis>=6",
        "mypy>=1.10",
    ]
    claude_marker = (
        "claude_code_live: tests that spawn a real `claude` CLI subprocess "
        "against the caller's live subscription auth (skipped unless "
        "ACTIVEGRAPH_TEST_CLAUDE_CODE_LIVE is set; registering this marker "
        "alone does not skip — each test also carries an explicit skipif)"
    )

    assert baml_block in source
    assert extras["baml"] == ["baml_bridge"]
    assert extras["claude-code"] == claude_dependencies
    assert extras["dev"] == expected_dev_dependencies
    for aggregate in ("llm", "all"):
        for dependency in claude_dependencies[:-1]:
            assert extras[aggregate].count(dependency) == 1
    import tomllib

    project = tomllib.loads(source)
    assert claude_marker in project["tool"]["pytest"]["ini_options"]["markers"]


def test_tests_workflow_installs_openrouter_and_has_an_exact_minimum_sdk_lane():
    workflow = (
        _repository_root() / ".github" / "workflows" / "tests.yml"
    ).read_text(encoding="utf-8")

    assert 'run: pip install -e ".[dev,openrouter]"' in workflow
    assert "openrouter-minimum-sdk:" in workflow
    assert 'run: pip install -e ".[dev,openrouter]" "openai==1.55.3"' in workflow
    assert "run: pytest -q tests/test_llm_openrouter.py -k real_sdk" in workflow


def test_current_docs_spec_and_release_records_cover_four_providers():
    root = _repository_root()
    readme = (root / "README.md").read_text(encoding="utf-8")
    provider_reference = (
        root / "docs" / "reference" / "llm-providers.md"
    ).read_text(encoding="utf-8")
    dependency_reference = (
        root / "docs" / "reference" / "errors" / "missing-optional-dependency.md"
    ).read_text(encoding="utf-8")
    spec = (root / "specs" / "08-llm.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    contract = (root / "CONTRACT.md").read_text(encoding="utf-8")

    assert "all four shipped LLM providers" in readme
    assert "ships four concrete `LLMProvider` implementations" in provider_reference
    assert "All four shipped LLM providers" in dependency_reference
    assert (
        "[AnthropicProvider, OpenAIProvider, ClaudeCodeProvider, "
        "OpenRouterProvider]"
    ) in spec
    assert "**`ClaudeCodeProvider`** (CONTRACT v1.11 #1). A third" in changelog
    assert "**`OpenRouterProvider`** (CONTRACT v1.11 #2). A fourth" in changelog
    assert "a capability-limited third `LLMProvider`" in contract
    assert "adds the fourth concrete shipped" in contract


def test_babyagi_has_one_bounded_api_key_provider_spec_mapping():
    from activegraph.llm import AnthropicProvider, OpenAIProvider

    module = _load_babyagi_example()
    assert module.PROVIDER_SPECS == {
        "anthropic": {
            "factory": AnthropicProvider,
            "env": "ANTHROPIC_API_KEY",
        },
        "openai": {
            "factory": OpenAIProvider,
            "env": "OPENAI_API_KEY",
        },
        "openrouter": {
            "factory": OpenRouterProvider,
            "env": "OPENROUTER_API_KEY",
        },
    }
    assert not hasattr(module, "PROVIDER_DEFAULTS")


def test_babyagi_unknown_direct_provider_fails_clearly():
    module = _load_babyagi_example()
    with pytest.raises(
        ValueError,
        match=r"unknown LLM provider 'not-a-provider'.*anthropic, openai, openrouter",
    ):
        module.run_babyagi("objective", provider="not-a-provider")


def test_babyagi_constructs_from_the_provider_spec_factory(monkeypatch, tmp_path):
    module = _load_babyagi_example()
    selected_provider = object()
    seen = {}

    class FakeRuntime:
        def __init__(self, graph, **kwargs):
            seen["provider"] = kwargs["llm_provider"]

        def run_goal(self, objective):
            seen["objective"] = objective

    monkeypatch.setitem(
        module.PROVIDER_SPECS,
        "openrouter",
        {"factory": lambda: selected_provider, "env": "OPENROUTER_API_KEY"},
    )
    monkeypatch.setattr(module, "Runtime", FakeRuntime)
    monkeypatch.chdir(tmp_path)

    trace_path = module.run_babyagi("mapped objective", provider="openrouter")

    assert seen == {
        "provider": selected_provider,
        "objective": "mapped objective",
    }
    assert trace_path.startswith("traces/babyagi-")


def test_babyagi_cli_choices_and_environment_lookup_come_from_provider_specs(
    monkeypatch, capsys
):
    module = _load_babyagi_example()
    monkeypatch.setitem(
        module.PROVIDER_SPECS,
        "openrouter",
        {"factory": OpenRouterProvider, "env": "CUSTOM_OPENROUTER_KEY"},
    )
    monkeypatch.delenv("CUSTOM_OPENROUTER_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["babyagi.py", "objective", "--provider", "openrouter"],
    )

    assert module.main() == 1
    assert "CUSTOM_OPENROUTER_KEY environment variable not set" in capsys.readouterr().err


def test_babyagi_readme_documents_openrouter_selection():
    readme = (
        _repository_root() / "examples" / "babyagi" / "README.md"
    ).read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY" in readme
    assert "--provider openrouter" in readme


# ---- literal HTTP through the exact minimum supported OpenAI SDK -----------


def _literal_completion_body(
    *,
    text='{"n": 42}',
    finish_reason="stop",
    choice_error=_MISSING,
    top_level_error=_MISSING,
    cost="0.001230",
):
    choice = {
        "index": 0,
        "message": {"role": "assistant", "content": text},
        "finish_reason": finish_reason,
    }
    if choice_error is not _MISSING:
        choice["error"] = choice_error
    body = {
        "id": "gen-test",
        "object": "chat.completion",
        "created": 1,
        "model": "provider/concrete-model",
        "choices": [choice],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
            "cost": cost,
        },
    }
    if top_level_error is not _MISSING:
        body["error"] = top_level_error
    return body


def _real_sdk_client(handler):
    return openai.OpenAI(
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_real_sdk_success_preserves_extensions_and_exact_request_policy():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=_literal_completion_body())

    provider = OpenRouterProvider(
        client=_real_sdk_client(handler),
        native_structured_output_models=("openai/gpt-4o",),
    )
    response = _complete(
        provider,
        output_schema=_Out,
        structured_output_mode="native",
    )

    assert len(requests) == 1
    assert str(requests[0].url) == (
        "https://openrouter.ai/api/v1/chat/completions"
    )
    sent = json.loads(requests[0].content)
    assert sent["model"] == "openai/gpt-4o-mini"
    assert sent["max_completion_tokens"] == 64
    assert "max_tokens" not in sent
    assert sent["temperature"] == 0.25
    assert sent["top_p"] == 0.8
    assert sent["provider"] == {"require_parameters": True}
    assert sent["response_format"]["type"] == "json_schema"
    assert response.raw_text == '{"n": 42}'
    assert response.parsed == _Out(n=42)
    assert response.input_tokens == 11
    assert response.output_tokens == 7
    assert response.cost_usd == Decimal("0.001230")
    assert response.provider_meta == {"cost_source": "openrouter_usage"}
    assert response.model == "provider/concrete-model"
    assert response.finish_reason == "stop"


def test_real_sdk_choice_error_survives_and_precedes_partial_output_and_cost():
    requests = []
    error = _provider_error(code=401, error_type="provider_unavailable")

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json=_literal_completion_body(
                text='{"n": 99}',
                finish_reason="error",
                choice_error=error,
                cost=None,
            ),
        )

    provider = OpenRouterProvider(client=_real_sdk_client(handler))
    with pytest.raises(LLMBehaviorError) as exc:
        _complete(provider, output_schema=_Out)
    assert len(requests) == 1
    assert exc.value.reason == "llm.network_error"
    assert exc.value.payload_extras == {
        "model": "openai/gpt-4o-mini",
        "status_code": 401,
        "error_type": "provider_unavailable",
        "finish_reason": "error",
    }


def test_real_sdk_top_level_error_survives_as_defensive_fallback():
    requests = []
    error = _provider_error(code=403, error_type="future_error")

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json=_literal_completion_body(
                text="partial",
                finish_reason="error",
                top_level_error=error,
            ),
        )

    provider = OpenRouterProvider(client=_real_sdk_client(handler))
    with pytest.raises(LLMBehaviorError) as exc:
        _complete(provider)
    assert len(requests) == 1
    assert exc.value.reason == "llm.auth_error"


@pytest.mark.parametrize(
    ("status_code", "expected_reason"),
    [
        (408, "llm.network_error"),
        (429, "llm.rate_limited"),
        (500, "llm.network_error"),
    ],
)
def test_real_sdk_internal_client_disables_hidden_retries(
    monkeypatch, status_code, expected_reason
):
    requests = []
    real_client_class = openai.OpenAI

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status_code,
            json={"error": {"message": "retryable failure", "type": "server"}},
        )

    def client_factory(**kwargs):
        return real_client_class(
            **kwargs,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(openai, "OpenAI", client_factory)
    provider = OpenRouterProvider()

    with pytest.raises(LLMBehaviorError) as exc:
        _complete(provider)
    assert exc.value.reason == expected_reason
    assert len(requests) == 1


def test_public_runtime_tool_loop_closes_with_exact_connector_causality():
    """A single admitted behavior invocation may make two provider turns."""

    class _SequencedCompletions:
        def __init__(self, responses):
            self._responses = iter(responses)
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return next(self._responses)

    @tool(
        name="lookup",
        description="Look up a named record",
        input_schema=_ClosureToolInput,
        output_schema=_ClosureToolOutput,
        deterministic=True,
    )
    def lookup(args, ctx):
        return _ClosureToolOutput(answer=f"answer:{args.q}")

    @behavior(name="seed_openrouter_closure", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "Northwind"})

    @llm_behavior(
        name="openrouter_closure",
        on=["object.created"],
        where={"object.type": "document"},
        description="Use the lookup tool and retain its answer",
        output_schema=_ClosureOutput,
        tools=[lookup],
    )
    def openrouter_closure(event, graph, ctx, llm_output):
        graph.add_object("claim", {"text": llm_output.answer})

    first = _raw_response(
        None,
        cost="0.000400",
        finish_reason="tool_calls",
        tool_calls=[
            SimpleNamespace(
                id="call_1",
                function=SimpleNamespace(
                    name="lookup", arguments='{"q":"northwind"}'
                ),
            )
        ],
    )
    second = _raw_response(
        '{"answer":"answer:northwind"}',
        cost="0.000600",
        finish_reason="stop",
    )
    completions = _SequencedCompletions([first, second])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    graph = Graph()
    runtime = Runtime(
        graph,
        llm_provider=OpenRouterProvider(client=client),
        budget={"max_llm_calls": 1},
    )

    runtime.run_goal("Research Northwind")

    triggering_event = next(
        event
        for event in graph.events
        if event.type == "object.created"
        and event.payload["object"]["type"] == "document"
    )
    connector_trace = [
        event
        for event in graph.events
        if event.type
        in {
            "llm.requested",
            "llm.responded",
            "tool.requested",
            "tool.responded",
        }
    ]
    req0, resp0, tool_req, tool_resp, req1, resp1 = connector_trace

    assert [event.type for event in connector_trace] == [
        "llm.requested",
        "llm.responded",
        "tool.requested",
        "tool.responded",
        "llm.requested",
        "llm.responded",
    ]
    assert [req0.payload["turn_index"], req1.payload["turn_index"]] == [0, 1]
    assert [resp0.payload["turn_index"], resp1.payload["turn_index"]] == [0, 1]
    assert resp0.payload.get("error") is None
    assert resp1.payload.get("error") is None

    assert req0.caused_by == triggering_event.id
    assert resp0.caused_by == req0.id
    assert tool_req.caused_by == req0.id
    assert tool_resp.caused_by == tool_req.id
    assert req1.caused_by == req0.id
    assert resp1.caused_by == req1.id

    assert len(completions.calls) == 2
    second_messages = completions.calls[1]["messages"]
    assistant_echo = next(
        message
        for message in second_messages
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    tool_echo = next(
        message for message in second_messages if message.get("role") == "tool"
    )
    assert assistant_echo["tool_calls"][0]["id"] == "call_1"
    assert assistant_echo["tool_calls"][0]["function"]["name"] == "lookup"
    assert tool_echo["tool_call_id"] == "call_1"

    claim = graph.objects(type="claim")[0]
    assert claim.data == {"text": "answer:northwind"}
    assert claim.provenance["llm_request_event_id"] == req1.id
    assert claim.provenance["tool_request_event_ids"] == [tool_req.id]
    assert runtime.budget.used["max_llm_calls"] == 1.0
