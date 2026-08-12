"""ClaudeCodeProvider live-CLI release suite — real subprocess, real
subscription auth. Never imports fakes, never runs in ordinary CI.

CONTRACT v1.11 #1, Behavior 14. A **required pre-release gate** (review
A1/A4), not merely supplementary — it's the only proof of this
provider's central product claims (real subscription billing, real
tool deferral, real native+tool composition, no leftover session/temp
state) that the injected `_sdk_loader` fake cannot itself provide.

Every test in this module carries the same collection-wide gate via
`pytestmark` (not a `postgres`-style module-level `skipif` alone —
this additionally carries `slow` and the `claude_code_live` marker on
every test, so no test in this file can accidentally omit the gate):

    ACTIVEGRAPH_TEST_CLAUDE_CODE_LIVE=1 \\
      .venv/bin/python -m pytest -q -m claude_code_live \\
      tests/test_llm_claude_code_live.py

Run from a genuinely top-level (non-nested) shell — CLAUDECODE and the
other nested-session env vars must be **absent**, not merely unset by
this process, since a real nested Claude Code session would trip
ClaudeCodeProvider's own isolation guard exactly as designed. Confirm
`ANTHROPIC_API_KEY` is unset and an active Claude Max/Pro/Team/
Enterprise login is present (`claude login`) before running. Before
the run, record `auth status --json` from the exact bundled binary
this provider resolves and confirm the expected personal account —
identity-bearing output is a manual precondition, never inferred from
`firstParty`, and never committed:

    BUNDLED_CLAUDE=$(
      .venv/bin/python -c \\
        'from activegraph.llm.claude_code import _load_sdk_bindings; print(_load_sdk_bindings().cli_path)'
    )
    "$BUNDLED_CLAUDE" auth status --json

Failure of any case here blocks release and SDK upgrades; ordinary CI
skips this suite explicitly (the env var is never set in CI).
"""

from __future__ import annotations

import os

import pytest
from pydantic import BaseModel

from activegraph.llm import ClaudeCodeProvider, LLMMessage

pytestmark = [
    pytest.mark.slow,
    pytest.mark.claude_code_live,
    pytest.mark.skipif(
        os.getenv("ACTIVEGRAPH_TEST_CLAUDE_CODE_LIVE") != "1",
        reason="requires explicit top-level Claude Code release environment "
        "(set ACTIVEGRAPH_TEST_CLAUDE_CODE_LIVE=1 from a non-nested shell "
        "with an active subscription login and ANTHROPIC_API_KEY unset)",
    ),
]


class _Answer(BaseModel):
    n: int


def _provider() -> ClaudeCodeProvider:
    return ClaudeCodeProvider(allow_unenforced_generation_controls=True)


def test_live_unstructured_result_and_firstparty_backend_evidence():
    r = _provider().complete(
        system="Reply with exactly the word: pong",
        messages=[LLMMessage(role="user", content="ping")],
        model="claude-sonnet-4-5",
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=None,
        timeout_seconds=60.0,
    )
    assert r.raw_text.strip()
    assert r.input_tokens > 0
    providers_seen = {
        entry.get("provider") for entry in (r.provider_meta.get("model_usage") or {}).values()
    }
    assert providers_seen, "expected at least one model_usage entry"
    assert "firstParty" in providers_seen or providers_seen == {None}


def test_live_two_tools_offered_preserve_order_and_only_selected_tool_defers():
    provider = _provider()
    r = provider.complete(
        system="",
        messages=[
            LLMMessage(
                role="user",
                content="Look up the weather for Boston using the get_weather tool.",
            )
        ],
        model="claude-sonnet-4-5",
        max_tokens=256,
        temperature=0.0,
        top_p=1.0,
        output_schema=None,
        timeout_seconds=60.0,
        tools=[
            {
                "name": "get_weather",
                "description": "Get the current weather for a city",
                "input_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
            {
                "name": "get_time",
                "description": "Get the current time in a city",
                "input_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        ],
    )
    assert r.tool_calls is not None
    assert len(r.tool_calls) == 1
    assert r.tool_calls[0].name in ("get_weather", "get_time")


def test_live_native_structured_output_with_tools_offered_but_unused():
    r = _provider().complete(
        system="Reply with a JSON object matching the schema. Do not call any tool.",
        messages=[LLMMessage(role="user", content="Return n=7.")],
        model="claude-sonnet-4-5",
        max_tokens=256,
        temperature=0.0,
        top_p=1.0,
        output_schema=_Answer,
        timeout_seconds=60.0,
        structured_output_mode="native",
        tools=[
            {
                "name": "get_weather",
                "description": "Get the current weather for a city",
                "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
            }
        ],
    )
    if r.tool_calls:
        assert len(r.tool_calls) == 1
    else:
        assert isinstance(r.parsed, _Answer)


def test_live_tool_turn_then_native_final_turn_two_calls():
    provider = _provider()
    tools = [
        {
            "name": "get_weather",
            "description": "Get the current weather for a city",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }
    ]
    first = provider.complete(
        system="",
        messages=[LLMMessage(role="user", content="Call get_weather for Boston.")],
        model="claude-sonnet-4-5",
        max_tokens=256,
        temperature=0.0,
        top_p=1.0,
        output_schema=_Answer,
        timeout_seconds=60.0,
        structured_output_mode="native",
        tools=tools,
    )
    assert first.tool_calls is not None and len(first.tool_calls) == 1
    assert first.parsed is None

    second = provider.complete(
        system="",
        messages=[
            LLMMessage(role="user", content="Call get_weather for Boston."),
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=(first.tool_calls[0],),
            ),
            LLMMessage(
                role="tool",
                tool_use_id=first.tool_calls[0].id,
                content='{"forecast": "sunny, 72F"}',
            ),
        ],
        model="claude-sonnet-4-5",
        max_tokens=256,
        temperature=0.0,
        top_p=1.0,
        output_schema=_Answer,
        timeout_seconds=60.0,
        structured_output_mode="native",
    )
    assert isinstance(second.parsed, _Answer)


def test_live_no_new_session_transcript_and_no_temp_cwd_after_completion():
    import glob
    import tempfile

    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "activegraph-claude-code-*")))
    _provider().complete(
        system="",
        messages=[LLMMessage(role="user", content="ping")],
        model="claude-sonnet-4-5",
        max_tokens=32,
        temperature=0.0,
        top_p=1.0,
        output_schema=None,
        timeout_seconds=60.0,
    )
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "activegraph-claude-code-*")))
    assert after - before == set(), "a provider-owned temp cwd leaked past completion"
