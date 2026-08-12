"""Determinism mode (CONTRACT v0.6 #7).

`deterministic=True` forces temperature=0 and top_p=1, regardless of
what was passed alongside. The Anthropic provider records no seed
(the messages API has no seed parameter). The prompt hash includes
`deterministic` so a determinism-mode and a stochastic-mode prompt
with otherwise-identical content hash differently.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from activegraph import FrozenClock, Graph, Runtime, behavior, clear_registry, llm_behavior
from activegraph.llm import LLMResponse, RecordedLLMProvider, RecordingLLMProvider
from activegraph.llm.errors import PromptIdentityError
from activegraph.llm.provider import LLMProvider
from activegraph.llm.prompt import assemble_prompt
from activegraph.core.view import View

from tests._llm_helpers import Claim, ClaimList, ScriptedProvider


class _StrictProvider:
    default_model = "m"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def recognizes_model(self, name: str) -> bool:
        return True

    def supports_native_structured_output(self, model: str) -> bool:
        return False

    def complete(
        self,
        *,
        system,
        messages,
        model,
        max_tokens,
        temperature,
        top_p,
        output_schema,
        timeout_seconds,
        tools=None,
    ):
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "output_schema": output_schema,
                "timeout_seconds": timeout_seconds,
                "tools": tools,
            }
        )
        return LLMResponse(
            raw_text="ok",
            parsed=None,
            input_tokens=1,
            output_tokens=1,
            cost_usd=Decimal("0"),
            latency_seconds=0.0,
            model=model,
            finish_reason="end_turn",
        )

    def estimate_cost(self, **kwargs):
        return Decimal("0")

    def count_tokens(self, **kwargs):
        return 1


def _identity_behavior(*, deterministic: bool):
    def handler(event, graph, ctx, out):
        pass

    return llm_behavior(
        name="identity",
        on=["goal.created"],
        model="m",
        deterministic=deterministic,
        temperature=0.0,
        top_p=1.0,
    )(handler)


@pytest.mark.parametrize("marker", ["absent", False])
def test_unadvertised_prompt_identity_metadata_is_not_sent(marker) -> None:
    clear_registry()
    provider = _StrictProvider()
    if marker != "absent":
        provider.accepts_prompt_identity = marker
    assert isinstance(provider, LLMProvider)
    assert "accepts_prompt_identity" not in LLMProvider.__dict__
    runtime = Runtime(Graph(), behaviors=[_identity_behavior(deterministic=False)], llm_provider=provider)

    runtime.run_goal("go")

    assert len(provider.calls) == 1
    assert "prompt_hash" not in provider.calls[0]
    assert "deterministic" not in provider.calls[0]


def test_truthy_prompt_identity_marker_receives_atomic_pair() -> None:
    class OptInProvider(_StrictProvider):
        accepts_prompt_identity = True

        def complete(self, *, prompt_hash=None, deterministic=None, **kwargs):
            self.identity = (prompt_hash, deterministic)
            return super().complete(**kwargs)

    clear_registry()
    provider = OptInProvider()
    Runtime(
        Graph(),
        behaviors=[_identity_behavior(deterministic=False)],
        llm_provider=provider,
    ).run_goal("go")

    assert provider.identity[0]
    assert provider.identity[1] is False


def test_runtime_recording_uses_declared_determinism_and_replays_offline(
    tmp_path,
) -> None:
    clear_registry()
    false_behavior = _identity_behavior(deterministic=False)
    true_behavior = _identity_behavior(deterministic=True)
    false_dir = tmp_path / "false"
    true_dir = tmp_path / "true"
    false_inner = _StrictProvider()
    runtime = Runtime(
        Graph(clock=FrozenClock()),
        behaviors=[false_behavior],
        llm_provider=RecordingLLMProvider(false_inner, str(false_dir)),
    )
    runtime.run_goal("go")
    request = next(e for e in runtime.graph.events if e.type == "llm.requested")
    false_file = next(false_dir.iterdir())
    false_fixture = json.loads(false_file.read_text())

    assert false_fixture["prompt"]["deterministic"] is False
    assert false_fixture["prompt_hash"] == request.payload["prompt_hash"]
    assert false_file.name == f"{request.payload['prompt_hash']}.json"
    assert "prompt_hash" not in false_inner.calls[0]
    assert "deterministic" not in false_inner.calls[0]

    replay = Runtime(
        Graph(clock=FrozenClock()),
        behaviors=[false_behavior],
        llm_provider=RecordedLLMProvider(str(false_dir)),
    )
    replay.run_goal("go")
    assert any(e.type == "llm.responded" for e in replay.graph.events)

    true_runtime = Runtime(
        Graph(clock=FrozenClock()),
        behaviors=[true_behavior],
        llm_provider=RecordingLLMProvider(_StrictProvider(), str(true_dir)),
    )
    true_runtime.run_goal("go")
    true_request = next(
        e for e in true_runtime.graph.events if e.type == "llm.requested"
    )
    assert true_request.payload["prompt_hash"] != request.payload["prompt_hash"]


def test_prompt_identity_error_bypasses_provider_retry_and_error_events() -> None:
    class BrokenIdentityProvider(_StrictProvider):
        accepts_prompt_identity = True

        def complete(self, *, prompt_hash=None, deterministic=None, **kwargs):
            raise PromptIdentityError(
                "hash_mismatch",
                prompt_hash=prompt_hash,
                computed_hash="different",
                deterministic=deterministic,
            )

    clear_registry()
    runtime = Runtime(
        Graph(),
        behaviors=[_identity_behavior(deterministic=False)],
        llm_provider=BrokenIdentityProvider(),
        llm_retry_max_attempts=1,
        llm_retry_initial_delay_seconds=0,
    )

    with pytest.raises(PromptIdentityError):
        runtime.run_goal("go")

    assert len([e for e in runtime.graph.events if e.type == "llm.requested"]) == 1
    assert not any(e.type == "llm.responded" for e in runtime.graph.events)
    assert not any(e.type == "behavior.failed" for e in runtime.graph.events)


def test_deterministic_forces_temperature_zero_and_top_p_one():
    captured = {}

    class _Cap:
        def complete(self, **kw):
            captured.update(kw)
            return type("R", (), {
                "raw_text": "{}", "parsed": None, "input_tokens": 1,
                "output_tokens": 1, "cost_usd": __import__("decimal").Decimal("0"),
                "latency_seconds": 0.0, "model": kw["model"],
                "finish_reason": "end_turn", "seed": None,
                "cache_hit": False, "provider_meta": {},
                "to_dict": lambda self: {
                    "raw_text": "", "parsed": None, "input_tokens": 1,
                    "output_tokens": 1, "cost_usd": "0", "latency_seconds": 0.0,
                    "model": kw["model"], "finish_reason": "end_turn",
                    "seed": None, "cache_hit": False, "provider_meta": {},
                },
            })()

        def estimate_cost(self, **kw):
            from decimal import Decimal
            return Decimal("0")

        def count_tokens(self, **kw):
            return 1

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "T", "body": "B"})

    @llm_behavior(
        name="ex",
        on=["object.created"],
        where={"object.type": "document"},
        view={"around": "event.payload.object.id"},
        deterministic=True,
        temperature=0.9,   # ignored under determinism
        top_p=0.5,         # ignored under determinism
    )
    def ex(event, graph, ctx, out):
        pass

    g = Graph()
    Runtime(g, llm_provider=_Cap()).run_goal("g")
    assert captured["temperature"] == 0.0
    assert captured["top_p"] == 1.0


def test_deterministic_flag_is_in_prompt_hash():
    g = Graph()
    obj = g.add_object("doc", {"title": "T", "body": "B"})
    ev = next(e for e in g.events if e.type == "object.created")
    v = View(objects=g.all_objects(), relations=[], events=g.events)
    base = dict(
        behavior_name="x", description="d", model="m",
        output_schema=None, creates=[],
        view=v, event=ev, frame=None,
        around=None, depth=None,
        max_tokens=64, temperature=0.0, top_p=1.0,
    )
    h_det = assemble_prompt(deterministic=True, **base).hash()
    h_stoch = assemble_prompt(deterministic=False, **base).hash()
    assert h_det != h_stoch


def test_seed_field_is_none_for_anthropic():
    """Anthropic's messages API does not expose a seed. The provider
    reflects this honestly — no fake seeds."""

    from activegraph.llm import AnthropicProvider, LLMMessage
    from unittest.mock import MagicMock
    from types import SimpleNamespace

    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(text="ok")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        model="claude-sonnet-4-5",
        stop_reason="end_turn",
    )
    p = AnthropicProvider(client=client)
    r = p.complete(
        system="", messages=[LLMMessage(role="user", content="hi")],
        model="claude-sonnet-4-5", max_tokens=4, temperature=0.0, top_p=1.0,
        output_schema=None, timeout_seconds=10,
    )
    assert r.seed is None
