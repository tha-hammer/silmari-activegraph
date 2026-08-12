"""RecordedLLMProvider + RecordingLLMProvider (CONTRACT v0.6 #12 +
decision-3 adjustment).

Recording produces fixtures keyed by prompt hash with `recorded_at`
outside the hashed content. Recorded mode reads them. Missing fixture
raises so tests fail loud rather than silently calling out.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from decimal import Decimal

import pytest
from pydantic import BaseModel

from activegraph.llm import (
    LLMBehaviorError,
    LLMMessage,
    LLMResponse,
    RecordedLLMProvider,
    RecordingLLMProvider,
)
from activegraph.llm import prompt_identity
from activegraph.llm.errors import PromptIdentityError
from activegraph.llm.prompt import schema_to_json


class _Out(BaseModel):
    n: int


class _StubInner:
    def __init__(self):
        self.calls = []

    def complete(self, **kw):
        self.calls.append(kw)
        return LLMResponse(
            raw_text='{"n": 1}',
            parsed=_Out(n=1),
            input_tokens=5,
            output_tokens=2,
            cost_usd=Decimal("0.0001"),
            latency_seconds=0.05,
            model=kw["model"],
            finish_reason="end_turn",
        )

    def estimate_cost(self, **kw):
        return Decimal("0.0001")

    def count_tokens(self, **kw):
        return 5


def _kwargs():
    return dict(
        system="sys",
        messages=[LLMMessage(role="user", content="hi")],
        model="claude-sonnet-4-5",
        max_tokens=64,
        temperature=0.0,
        top_p=1.0,
        output_schema=_Out,
        timeout_seconds=30.0,
    )


def _identity_hash(
    deterministic: bool,
    *,
    tools=None,
    structured_output_mode: str = "prompt",
) -> str:
    kwargs = _kwargs()
    payload = prompt_identity.build_prompt_identity_payload(
        model=kwargs["model"],
        system=kwargs["system"],
        messages=kwargs["messages"],
        output_schema_name=_Out.__name__,
        output_schema_json=schema_to_json(_Out),
        max_tokens=kwargs["max_tokens"],
        temperature=kwargs["temperature"],
        top_p=kwargs["top_p"],
        deterministic=deterministic,
        tools=tools,
        structured_output_mode=structured_output_mode,
    )
    return prompt_identity.hash_prompt_payload(payload)


def _write_fixture(path, prompt_hash: str, raw_text: str) -> None:
    data = {
        "prompt_hash": prompt_hash,
        "response": {
            "raw_text": raw_text,
            "parsed": {"n": 1},
            "input_tokens": 1,
            "output_tokens": 1,
            "cost_usd": "0",
            "latency_seconds": 0,
            "model": "claude-sonnet-4-5",
            "finish_reason": "end_turn",
        },
    }
    path.write_text(json.dumps(data))


@pytest.mark.parametrize("provider_kind", ["recorded", "recording"])
@pytest.mark.parametrize(
    ("identity_kwargs", "kind"),
    [
        ({"prompt_hash": "only-hash"}, "incomplete_metadata_pair"),
        ({"deterministic": False}, "incomplete_metadata_pair"),
        (
            {"prompt_hash": "not-the-computed-hash", "deterministic": False},
            "hash_mismatch",
        ),
    ],
)
def test_prompt_identity_metadata_errors_precede_fixture_and_live_effects(
    tmp_path, monkeypatch, provider_kind, identity_kwargs, kind
):
    inner = _StubInner()
    provider = (
        RecordedLLMProvider(str(tmp_path))
        if provider_kind == "recorded"
        else RecordingLLMProvider(inner, str(tmp_path))
    )
    probes: list[str] = []

    def forbidden_probe(path):
        probes.append(path)
        raise AssertionError("fixture path probed before identity validation")

    monkeypatch.setattr("activegraph.llm.recorded.os.path.exists", forbidden_probe)

    with pytest.raises(PromptIdentityError) as exc_info:
        provider.complete(**_kwargs(), **identity_kwargs)

    assert exc_info.value.kind == kind
    assert probes == []
    assert inner.calls == []
    assert list(tmp_path.iterdir()) == []


def test_declared_identity_prefers_canonical_fixture_without_legacy_probe(
    tmp_path, monkeypatch
):
    canonical = _identity_hash(False)
    legacy = _identity_hash(True)
    _write_fixture(tmp_path / f"{canonical}.json", canonical, "canonical")
    _write_fixture(tmp_path / f"{legacy}.json", legacy, "legacy")
    original_exists = os.path.exists
    probes: list[str] = []

    def exists(path):
        probes.append(path)
        return original_exists(path)

    monkeypatch.setattr("activegraph.llm.recorded.os.path.exists", exists)
    response = RecordedLLMProvider(str(tmp_path)).complete(
        **_kwargs(), prompt_hash=canonical, deterministic=False
    )

    assert response.raw_text == "canonical"
    assert probes == [str(tmp_path / f"{canonical}.json")]


def test_declared_identity_falls_back_once_to_distinct_legacy_hash(
    tmp_path, monkeypatch
):
    canonical = _identity_hash(False)
    legacy = _identity_hash(True)
    _write_fixture(tmp_path / f"{legacy}.json", legacy, "legacy")
    original_exists = os.path.exists
    probes: list[str] = []

    def exists(path):
        probes.append(path)
        return original_exists(path)

    monkeypatch.setattr("activegraph.llm.recorded.os.path.exists", exists)
    response = RecordedLLMProvider(str(tmp_path)).complete(
        **_kwargs(), prompt_hash=canonical, deterministic=False
    )

    assert response.raw_text == "legacy"
    assert probes == [
        str(tmp_path / f"{canonical}.json"),
        str(tmp_path / f"{legacy}.json"),
    ]


def test_declared_identity_total_miss_reports_canonical_hash(tmp_path) -> None:
    canonical = _identity_hash(False)

    with pytest.raises(LLMBehaviorError) as exc_info:
        RecordedLLMProvider(str(tmp_path)).complete(
            **_kwargs(), prompt_hash=canonical, deterministic=False
        )

    assert exc_info.value.reason == "llm.fixture_missing"
    assert exc_info.value.payload_extras["prompt_hash"] == canonical


def test_declared_identity_does_not_probe_equal_legacy_hash(
    tmp_path, monkeypatch
) -> None:
    canonical = _identity_hash(True)
    probes: list[str] = []

    def missing(path):
        probes.append(path)
        return False

    monkeypatch.setattr("activegraph.llm.recorded.os.path.exists", missing)
    with pytest.raises(LLMBehaviorError):
        RecordedLLMProvider(str(tmp_path)).complete(
            **_kwargs(), prompt_hash=canonical, deterministic=True
        )

    assert probes == [str(tmp_path / f"{canonical}.json")]


def test_declared_identity_round_trips_native_mode_and_nonempty_tools(
    tmp_path,
) -> None:
    tools = [
        {
            "name": "lookup",
            "description": "Lookup",
            "input_schema": {"type": "object"},
        }
    ]
    prompt_hash = _identity_hash(
        False, tools=tools, structured_output_mode="native"
    )
    kwargs = _kwargs() | {
        "tools": tools,
        "structured_output_mode": "native",
        "prompt_hash": prompt_hash,
        "deterministic": False,
    }
    inner = _StubInner()

    RecordingLLMProvider(inner, str(tmp_path)).complete(**kwargs)
    fixture = json.loads((tmp_path / f"{prompt_hash}.json").read_text())
    replayed = RecordedLLMProvider(str(tmp_path)).complete(**kwargs)

    assert fixture["prompt"]["tools"] == tools
    assert fixture["prompt"]["structured_output_mode"] == "native"
    assert replayed.raw_text == '{"n": 1}'


def test_recording_writes_fixture_with_recorded_at_outside_hash():
    with tempfile.TemporaryDirectory() as td:
        inner = _StubInner()
        rec = RecordingLLMProvider(inner, td)
        rec.complete(**_kwargs())
        files = os.listdir(td)
        assert len(files) == 1
        path = os.path.join(td, files[0])
        with open(path) as f:
            data = json.load(f)
        # recorded_at present, but it's NOT inside the hashed `prompt` blob
        assert "recorded_at" in data
        assert re.match(
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", data["recorded_at"]
        )
        assert "recorded_at" not in data["prompt"]
        # The filename matches the hash inside.
        assert files[0] == f"{data['prompt_hash']}.json"


def test_recorded_provider_reads_back_response():
    with tempfile.TemporaryDirectory() as td:
        inner = _StubInner()
        RecordingLLMProvider(inner, td).complete(**_kwargs())
        recorded = RecordedLLMProvider(td)
        response = recorded.complete(**_kwargs())
        assert response.raw_text == '{"n": 1}'
        # Parsed re-validates against schema → Pydantic instance.
        assert isinstance(response.parsed, _Out)
        assert response.parsed.n == 1


def test_recorded_missing_fixture_raises_llm_behavior_error():
    with tempfile.TemporaryDirectory() as td:
        recorded = RecordedLLMProvider(td)
        with pytest.raises(LLMBehaviorError) as exc:
            recorded.complete(**_kwargs())
        assert exc.value.reason == "llm.fixture_missing"
        assert "prompt_hash" in exc.value.payload_extras


def test_recording_provider_delegates_count_tokens_and_estimate_cost():
    with tempfile.TemporaryDirectory() as td:
        inner = _StubInner()
        rec = RecordingLLMProvider(inner, td)
        assert rec.estimate_cost(
            input_tokens=10, output_tokens=2, model="x"
        ) == Decimal("0.0001")
        assert rec.count_tokens(
            system="s", messages=[], model="x"
        ) == 5


def test_fixture_hash_is_stable_across_recordings_of_same_prompt():
    """Same prompt → same hash → same fixture filename (overwrite)."""

    with tempfile.TemporaryDirectory() as td:
        inner = _StubInner()
        rec = RecordingLLMProvider(inner, td)
        rec.complete(**_kwargs())
        rec.complete(**_kwargs())
        # Same key → one file, not two.
        assert len(os.listdir(td)) == 1
