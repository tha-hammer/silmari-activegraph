"""Behavior 7: generated BAML failures use ActiveGraph reason codes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from baml_bridge import BamlError
from hypothesis import given, strategies as st

from activegraph.baml_client.baml_sdk.baml import errors as baml_errors
from activegraph.llm import LLMBehaviorError, LLMMessage
from activegraph.llm.baml_provider import (
    BamlLLMProvider,
    _translate_baml_error,
)


_REASON_CODES = {
    "llm.parse_error",
    "llm.schema_violation",
    "llm.fixture_missing",
    "llm.rate_limited",
    "llm.network_error",
    "llm.auth_error",
    "llm.request_error",
}

_ERROR_TYPES = [
    baml_errors.InvalidArgument,
    baml_errors.ParseError,
    baml_errors.Io,
    baml_errors.Timeout,
    baml_errors.Unsupported,
    baml_errors.AccessError,
    baml_errors.RenderPrompt,
    baml_errors.NotImplemented,
    baml_errors.LlmClient,
    baml_errors.DevOther,
    baml_errors.HostCallable,
    baml_errors.GenericSdkError,
    baml_errors.CompilationError,
    baml_errors.TypeMismatch,
]


def _complete() -> None:
    BamlLLMProvider(vendor="anthropic").complete(
        system="You are concise.",
        messages=[LLMMessage(role="user", content="Say hi.")],
        model="claude-sonnet-4-5",
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=None,
        timeout_seconds=30,
    )


def _assert_real_failure(
    mock_llm_http_server: Any,
    script: Callable[[], None],
    expected_reason: str,
) -> None:
    script()
    with pytest.raises(LLMBehaviorError) as caught:
        _complete()
    assert caught.value.reason == expected_reason
    assert caught.value.payload_extras["exception_type"]
    assert mock_llm_http_server.hits("/anthropic") == 1


@pytest.mark.parametrize(
    ("status", "error_type", "expected_reason"),
    [
        (429, "rate_limit_error", "llm.rate_limited"),
        (401, "authentication_error", "llm.auth_error"),
        (422, "invalid_request_error", "llm.request_error"),
    ],
)
def test_real_anthropic_http_error_maps_to_reason_code(
    mock_llm_http_server: Any,
    status: int,
    error_type: str,
    expected_reason: str,
) -> None:
    def script() -> None:
        mock_llm_http_server.script_response(
            route="/anthropic",
            status=status,
            body={
                "type": "error",
                "error": {
                    "type": error_type,
                    # Deliberately omit the numeric status here: BAML 0.15's
                    # DevOther bridge value retains the vendor error body but
                    # can discard the outer HTTP status.
                    "message": "scripted vendor failure",
                },
            },
        )

    _assert_real_failure(mock_llm_http_server, script, expected_reason)


def test_real_malformed_200_maps_to_parse_error(mock_llm_http_server: Any) -> None:
    _assert_real_failure(
        mock_llm_http_server,
        lambda: mock_llm_http_server.script_response(
            route="/anthropic",
            status=200,
            body="{this is not valid JSON",
        ),
        "llm.parse_error",
    )


def test_real_connection_reset_maps_to_network_error(mock_llm_http_server: Any) -> None:
    _assert_real_failure(
        mock_llm_http_server,
        lambda: mock_llm_http_server.script_connection_reset(route="/anthropic"),
        "llm.network_error",
    )


@pytest.mark.parametrize(
    ("error_value", "expected_reason"),
    [
        (baml_errors.ParseError(message="bad output"), "llm.parse_error"),
        (baml_errors.TypeMismatch(message="wrong shape"), "llm.schema_violation"),
        (baml_errors.AccessError(message="denied"), "llm.auth_error"),
        (baml_errors.InvalidArgument(message="bad argument"), "llm.request_error"),
        (baml_errors.LlmClient(message="HTTP status 429"), "llm.rate_limited"),
        (baml_errors.LlmClient(message="HTTP status 403"), "llm.auth_error"),
        (baml_errors.LlmClient(message="HTTP status 404"), "llm.request_error"),
        (baml_errors.Io(message="connection reset"), "llm.network_error"),
    ],
)
def test_typed_baml_error_mapping(error_value: Any, expected_reason: str) -> None:
    assert _translate_baml_error(error_value).reason == expected_reason
    assert _translate_baml_error(BamlError(error_value)).reason == expected_reason


@given(st.sampled_from(_ERROR_TYPES))
def test_every_generated_baml_error_variant_maps_to_existing_reason_code(
    error_type: type,
) -> None:
    # model_construct deliberately covers opaque/host-only variants such as
    # HostCallable without inventing a native handle solely for classification.
    error_value = error_type.model_construct(message="synthetic BAML failure")

    translated = _translate_baml_error(error_value)

    assert isinstance(translated, LLMBehaviorError)
    assert translated.reason in _REASON_CODES
