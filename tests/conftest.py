"""Shared fixtures. Most tests want a clean global behavior + tool registry."""

import pytest

from activegraph import clear_registry, clear_tool_registry
from activegraph.runtime._live import _clear_for_test as _clear_live_runtimes


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
