"""Frozen prompt identity bytes and shared-owner delegation."""

from __future__ import annotations

import importlib
import json
from decimal import Decimal

import pytest

from activegraph import Graph, Runtime, clear_registry, llm_behavior
from activegraph.llm import prompt_identity
from activegraph.llm.cache import LLMCache
from activegraph.llm.prompt import AssembledPrompt
from activegraph.llm.recorded import RecordedLLMProvider, RecordingLLMProvider
from activegraph.llm.types import LLMMessage, LLMResponse, ToolCall


MESSAGES = [
    LLMMessage(role="user", content="café"),
    LLMMessage(
        role="assistant",
        content="",
        tool_calls=(ToolCall(id="call-1", name="lookup", args={"q": "café"}),),
    ),
]
SCHEMA_JSON = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}
COMMON = {
    "model": "m",
    "system": "sys",
    "messages": MESSAGES,
    "output_schema_name": "Out",
    "output_schema_json": SCHEMA_JSON,
    "max_tokens": 64,
    "temperature": 0.0,
    "top_p": 1.0,
    "deterministic": False,
}
PUBLIC_CANONICAL = (
    '{"deterministic":false,"max_tokens":64,"messages":['
    '{"content":"caf\\u00e9","role":"user"},'
    '{"content":"","role":"assistant","tool_calls":['
    '{"args":{"q":"caf\\u00e9"},"id":"call-1","name":"lookup"}]}],'
    '"model":"m","output_schema_json":{"properties":{"text":'
    '{"type":"string"}},"required":["text"],"type":"object"},'
    '"output_schema_name":"Out","system":"sys","temperature":0.0,'
    '"top_p":1.0}'
)
NULL_TOOLS_CANONICAL = PUBLIC_CANONICAL.replace(
    '"temperature":0.0,"top_p":1.0}',
    '"temperature":0.0,"tools":null,"top_p":1.0}',
)


def test_public_prompt_identity_frozen_vector() -> None:
    payload = prompt_identity.build_prompt_identity_payload(**COMMON)

    assert payload["messages"] == [
        {"role": "user", "content": "café"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call-1", "name": "lookup", "args": {"q": "café"}}
            ],
        },
    ]
    assert "tools" not in payload
    assert "structured_output_mode" not in payload
    assert prompt_identity.canonical_prompt_json(payload) == PUBLIC_CANONICAL
    assert prompt_identity.hash_prompt_payload(payload) == (
        "eb3f78f77bf3960c9de71be0fa80958d8b4f56d556c447e06a56876afd7b46dd"
    )


@pytest.mark.parametrize("tools", [None, []])
def test_per_turn_empty_tools_normalize_to_null(tools) -> None:
    payload = prompt_identity.build_prompt_identity_payload(**COMMON, tools=tools)

    assert payload["tools"] is None
    assert prompt_identity.canonical_prompt_json(payload) == NULL_TOOLS_CANONICAL
    assert prompt_identity.hash_prompt_payload(payload) == (
        "6308c8b3e634a8a5044774b80b121df987e2a223da17eeecafc1e50584d67a80"
    )


def test_nonempty_tools_and_native_mode_are_hash_load_bearing() -> None:
    tools = [
        {
            "name": "lookup",
            "description": "Lookup",
            "input_schema": {"type": "object"},
        }
    ]
    with_tools = prompt_identity.build_prompt_identity_payload(
        **COMMON, tools=tools
    )
    native = prompt_identity.build_prompt_identity_payload(
        **COMMON, tools=None, structured_output_mode="native"
    )

    assert with_tools["tools"] == tools
    assert prompt_identity.hash_prompt_payload(with_tools) == (
        "4d292d6d2cc5d35dc47d3bc857e8b6a2e683e534fccb144c1019f106b8628de9"
    )
    assert native["structured_output_mode"] == "native"
    assert prompt_identity.hash_prompt_payload(native) == (
        "8b67bb18b76549ab3548933d2dafb06a933948726892e306973ca346ed1fec9d"
    )


def _assembled_prompt() -> AssembledPrompt:
    return AssembledPrompt(
        system="sys",
        messages=MESSAGES,
        model="m",
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema_name="Out",
        output_schema_json=SCHEMA_JSON,
        deterministic=False,
    )


def test_public_and_runtime_producers_delegate_to_shared_owner(monkeypatch) -> None:
    prompt_module = importlib.import_module("activegraph.llm.prompt")
    runtime_module = importlib.import_module("activegraph.runtime.runtime")
    calls: list[dict] = []

    def build(**kwargs):
        calls.append(kwargs)
        return {"sentinel": len(calls)}

    monkeypatch.setattr(prompt_identity, "build_prompt_identity_payload", build)
    monkeypatch.setattr(
        prompt_identity, "canonical_prompt_json", lambda payload: "canonical"
    )
    monkeypatch.setattr(
        prompt_identity, "hash_prompt_payload", lambda payload: "digest"
    )

    prompt = _assembled_prompt()
    assert prompt.to_hashable() == {"sentinel": 1}
    assert prompt.canonical_json() == "canonical"
    assert prompt.hash() == "digest"
    assert runtime_module._hash_turn_prompt(
        prompt=prompt, messages=MESSAGES, tool_defs=[]
    ) == "digest"

    assert prompt_module.prompt_identity is prompt_identity
    assert runtime_module.prompt_identity is prompt_identity
    assert "tools" not in calls[0]
    assert calls[-1]["tools"] == []


def test_recorded_providers_delegate_payload_and_hash_to_shared_owner(
    tmp_path, monkeypatch
) -> None:
    recorded_module = importlib.import_module("activegraph.llm.recorded")
    calls: list[dict] = []

    def build(**kwargs):
        calls.append(kwargs)
        return {"sentinel": True}

    monkeypatch.setattr(prompt_identity, "build_prompt_identity_payload", build)
    monkeypatch.setattr(
        prompt_identity, "hash_prompt_payload", lambda payload: "sentinel-hash"
    )
    fixture = {
        "response": {
            "raw_text": "recorded",
            "parsed": None,
            "input_tokens": 1,
            "output_tokens": 1,
            "cost_usd": "0",
            "latency_seconds": 0.0,
            "model": "m",
            "finish_reason": "end_turn",
        }
    }
    (tmp_path / "sentinel-hash.json").write_text(json.dumps(fixture))
    kwargs = dict(
        system="sys",
        messages=MESSAGES,
        model="m",
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=None,
        timeout_seconds=1.0,
        tools=[],
    )

    assert RecordedLLMProvider(str(tmp_path)).complete(**kwargs).raw_text == "recorded"

    class Inner:
        def complete(self, **inner_kwargs):
            return LLMResponse(
                raw_text="live",
                parsed=None,
                input_tokens=1,
                output_tokens=1,
                cost_usd=Decimal("0"),
                latency_seconds=0.0,
                model="m",
                finish_reason="end_turn",
            )

        def estimate_cost(self, **kwargs):
            return Decimal("0")

        def count_tokens(self, **kwargs):
            return 1

    RecordingLLMProvider(Inner(), str(tmp_path)).complete(**kwargs)
    written = json.loads((tmp_path / "sentinel-hash.json").read_text())

    assert written["prompt"] == {"sentinel": True}
    assert written["prompt_hash"] == "sentinel-hash"
    assert calls[-1]["tools"] == []
    assert recorded_module.prompt_identity is prompt_identity
    assert not hasattr(recorded_module, "_canonical_prompt_payload")
    assert not hasattr(recorded_module, "_hash_payload")


def test_runtime_identity_digest_flows_through_cache_events_and_strict_replay(
    tmp_path, monkeypatch
) -> None:
    class Provider:
        default_model = "m"

        def __init__(self) -> None:
            self.calls = 0

        def recognizes_model(self, name: str) -> bool:
            return True

        def complete(self, **kwargs):
            self.calls += 1
            return LLMResponse(
                raw_text="ok",
                parsed=None,
                input_tokens=1,
                output_tokens=1,
                cost_usd=Decimal("0"),
                latency_seconds=0.0,
                model="m",
                finish_reason="end_turn",
            )

        def estimate_cost(self, **kwargs):
            return Decimal("0")

        def count_tokens(self, **kwargs):
            return 1

    clear_registry()

    @llm_behavior(name="identity", on=["goal.created"], model="m")
    def identity(event, graph, ctx, out):
        pass

    provider = Provider()
    path = str(tmp_path / "identity.db")
    monkeypatch.setattr(
        prompt_identity, "hash_prompt_payload", lambda payload: "sentinel-digest"
    )
    runtime = Runtime(
        Graph(),
        behaviors=[identity],
        llm_provider=provider,
        persist_to=path,
        replay_llm_cache=True,
        llm_cache=LLMCache(),
    )
    runtime.run_goal("first")
    runtime.run_goal("second")

    requested = [e for e in runtime.graph.events if e.type == "llm.requested"]
    responded = [e for e in runtime.graph.events if e.type == "llm.responded"]
    assert [e.payload["prompt_hash"] for e in requested] == [
        "sentinel-digest",
        "sentinel-digest",
    ]
    assert [e.payload["prompt_hash"] for e in responded] == [
        "sentinel-digest",
        "sentinel-digest",
    ]
    assert [e.payload["cache_hit"] for e in requested] == [False, True]
    assert provider.calls == 1

    Runtime.load(
        path,
        behaviors=[identity],
        llm_provider=provider,
        replay_strict=True,
    )
    assert provider.calls == 1
