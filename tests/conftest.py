"""Shared fixtures. Most tests want a clean global behavior + tool registry."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from activegraph import clear_registry, clear_tool_registry
from activegraph.runtime._live import _clear_for_test as _clear_live_runtimes
from tests.fixtures.mock_llm_http_server import MockLLMHTTPServer


_BAML_MOCK_ENV = {
    "ACTIVEGRAPH_BAML_MOCK_ANTHROPIC_URL": "/anthropic",
    "ACTIVEGRAPH_BAML_MOCK_OPENAI_URL": "/openai",
    "ACTIVEGRAPH_BAML_MOCK_OPENROUTER_URL": "/openrouter",
    "ACTIVEGRAPH_BAML_MOCK_RETRY_URL": "/retry-target",
}
_mock_server: MockLLMHTTPServer | None = None
_previous_baml_mock_env: dict[str, str | None] = {}


def pytest_configure(config: pytest.Config) -> None:
    """Set BAML client URLs before pytest imports generated client modules."""
    global _mock_server
    if _mock_server is not None:
        return
    _mock_server = MockLLMHTTPServer()
    for name, route in _BAML_MOCK_ENV.items():
        _previous_baml_mock_env[name] = os.environ.get(name)
        os.environ[name] = f"{_mock_server.url}{route}"


def pytest_unconfigure(config: pytest.Config) -> None:
    global _mock_server
    if _mock_server is None:
        return
    _mock_server.close()
    _mock_server = None
    for name, previous in _previous_baml_mock_env.items():
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous
    _previous_baml_mock_env.clear()


@pytest.fixture
def mock_llm_http_server() -> MockLLMHTTPServer:
    assert _mock_server is not None
    return _mock_server


@pytest.fixture(autouse=True)
def _reset_mock_llm_http_server() -> Iterator[None]:
    assert _mock_server is not None
    _mock_server.reset()
    yield
    _mock_server.reset()


@pytest.fixture
def baml_question_list_schema():
    """Real JSON Schema for the BAML-generated QuestionListBaml class
    (activegraph/baml_src/schemas.baml). Shared by Behavior 2's codegen
    test and Behavior 3's native_schema_compatible() comparison."""
    from activegraph.baml_client.baml_sdk import QuestionListBaml

    return QuestionListBaml.model_json_schema()


@pytest.fixture(autouse=True)
def _isolate_registry():
    clear_registry()
    clear_tool_registry()
    # v1.0.2.post1: the live-Runtime WeakSet is module-level state used
    # for cross-provider validation. It auto-cleans on GC in production,
    # but pytest's exception machinery keeps Runtimes alive within a
    # test session via traceback strong-refs, so clear it explicitly
    # between tests to prevent cross-test bleed.
    _clear_live_runtimes()
    yield
    clear_registry()
    clear_tool_registry()
    _clear_live_runtimes()
