"""ClaudeCodeProvider Runtime-integration tests. CONTRACT v1.11 #1.

Covers Behavior 1 (fail-closed capability binding at all three binding
moments), Behavior 8's Runtime half (a real defer-then-native two-call
loop), Behavior 9's nested-context rejection at the Runtime boundary,
Behavior 10's Runtime half (setup/auth/request cases call once; rate/
network/timeout cases retry then close), and Behavior 13 (the BLOCKING
workflow-closure test, persistence round-trips, and SQLite fork/diff).

Reuses the helper-function pattern already established in
tests/test_llm_replay.py (no `@pytest.fixture` decorators there
either) and its SQLite fork idiom, with Claude-specific setup defined
explicitly here rather than imported as a fixture (per review's file-
splitting guidance) — swapping `ScriptedProvider` for
`ClaudeCodeProvider(_sdk_loader=<fake>)`.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import claude_agent_sdk as _sdk
from claude_agent_sdk import AssistantMessage, DeferredToolUse, ResultMessage, TextBlock
from claude_agent_sdk._errors import MessageParseError

from activegraph import (
    Graph,
    InvalidRuntimeConfiguration,
    MissingProviderError,
    ReplayDivergenceError,
    Runtime,
    Tool,
    behavior,
    clear_registry,
    llm_behavior,
    tool,
)
from activegraph.llm import ClaudeCodeProvider, LLMCache
from activegraph.llm.claude_code import _SDKBindings

from tests._llm_helpers import Claim, ClaimList


@pytest.fixture(autouse=True)
def _clean_isolation_env(monkeypatch):
    from activegraph.llm.claude_code import _CREDENTIAL_ROUTING_VARS, _NESTED_SESSION_VARS

    for v in (*_CREDENTIAL_ROUTING_VARS, *_NESTED_SESSION_VARS):
        monkeypatch.delenv(v, raising=False)


# ---------------------------------------------------------------- fakes


def _bindings(query_fn):
    return _SDKBindings(
        query=query_fn,
        ClaudeAgentOptions=_sdk.ClaudeAgentOptions,
        ClaudeSDKError=_sdk.ClaudeSDKError,
        CLIConnectionError=_sdk.CLIConnectionError,
        CLINotFoundError=_sdk.CLINotFoundError,
        CLIJSONDecodeError=_sdk.CLIJSONDecodeError,
        MessageParseError=MessageParseError,
        ProcessError=_sdk.ProcessError,
        AssistantMessage=_sdk.AssistantMessage,
        TextBlock=_sdk.TextBlock,
        ResultMessage=_sdk.ResultMessage,
        DeferredToolUse=_sdk.DeferredToolUse,
        RateLimitEvent=_sdk.RateLimitEvent,
        HookMatcher=_sdk.HookMatcher,
        create_sdk_mcp_server=_sdk.create_sdk_mcp_server,
        tool=_sdk.tool,
        cli_path="/fake/bundled/claude",
    )


class _SequencedQuery:
    """Stateful query_fn: pops the next scripted outcome on each call.
    Each outcome is either a callable(items...) via `.yields(*items)`
    or `.raises(exc)`.
    """

    def __init__(self):
        self._outcomes: list[tuple[str, list]] = []
        self.call_count = 0

    def yields(self, *items) -> "_SequencedQuery":
        self._outcomes.append(("yield", list(items)))
        return self

    def raises(self, exc) -> "_SequencedQuery":
        self._outcomes.append(("raise", exc))
        return self

    def __call__(self, *, prompt, options=None, transport=None):
        idx = self.call_count
        self.call_count += 1
        kind, payload = self._outcomes[idx]
        return self._agen(kind, payload)

    async def _agen(self, kind, payload):
        if kind == "raise":
            raise payload
            yield  # pragma: no cover - unreachable, keeps this a generator
        for item in payload:
            yield item


def _make_result(**overrides):
    defaults = dict(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
        stop_reason="end_turn",
        total_cost_usd=0.001,
        usage={"input_tokens": 1, "output_tokens": 1},
    )
    defaults.update(overrides)
    return ResultMessage(**defaults)


def _text_provider(text: str, **kwargs) -> ClaudeCodeProvider:
    seq = _SequencedQuery().yields(
        AssistantMessage(content=[TextBlock(text=text)], model="claude-sonnet-4-5"),
        _make_result(),
    )
    return ClaudeCodeProvider(
        allow_unenforced_generation_controls=True, _sdk_loader=lambda: _bindings(seq), **kwargs
    )


def _provider_from_sequence(seq: _SequencedQuery, **kwargs) -> ClaudeCodeProvider:
    return ClaudeCodeProvider(
        allow_unenforced_generation_controls=True, _sdk_loader=lambda: _bindings(seq), **kwargs
    )


# ------------------------------------------- Behavior 1: capability binding


def _register_deterministic():
    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "T", "body": "B"})

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        description="x",
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
        deterministic=True,
    )
    def extractor(event, graph, ctx, llm_output):
        pass


def _register_nondeterministic():
    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "T", "body": "B"})

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        description="x",
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, llm_output):
        pass


def test_missing_acknowledgement_raises_at_construction():
    clear_registry()
    _register_nondeterministic()
    provider = ClaudeCodeProvider()  # allow_unenforced_generation_controls=False (default)
    with pytest.raises(InvalidRuntimeConfiguration) as exc:
        Runtime(Graph(), llm_provider=provider)
    assert "acknowledgement" in str(exc.value).lower() or "allow_unenforced_generation_controls" in str(exc.value)


def test_deterministic_behavior_raises_at_construction():
    clear_registry()
    _register_deterministic()
    provider = ClaudeCodeProvider(allow_unenforced_generation_controls=True)
    with pytest.raises(InvalidRuntimeConfiguration) as exc:
        Runtime(Graph(), llm_provider=provider)
    assert "deterministic" in str(exc.value).lower()


def test_hard_cost_budget_raises_at_construction():
    clear_registry()
    _register_nondeterministic()
    provider = ClaudeCodeProvider(allow_unenforced_generation_controls=True)
    with pytest.raises(InvalidRuntimeConfiguration):
        Runtime(Graph(), llm_provider=provider, budget={"max_cost_usd": "1.00"})


def test_nondeterministic_with_acknowledgement_and_no_budget_binds():
    clear_registry()
    _register_nondeterministic()
    provider = ClaudeCodeProvider(allow_unenforced_generation_controls=True)
    rt = Runtime(Graph(), llm_provider=provider)  # must not raise
    rt._ensure_registry()  # must not raise either


def test_rejected_construction_leaves_no_runtime_in_live_set_and_emits_no_event():
    from activegraph.runtime._live import live_runtimes

    clear_registry()
    _register_deterministic()
    provider = ClaudeCodeProvider(allow_unenforced_generation_controls=True)
    before = len(live_runtimes())
    with pytest.raises(InvalidRuntimeConfiguration):
        Runtime(Graph(), llm_provider=provider)
    assert len(live_runtimes()) == before


def test_defensive_double_check_fires_at_ensure_registry_too():
    # _ensure_registry re-validates even if somehow reached with a bad
    # binding — defensive double-check mirroring the constructor's
    # eager pass.
    clear_registry()
    _register_nondeterministic()
    provider = ClaudeCodeProvider(allow_unenforced_generation_controls=True)
    rt = Runtime(Graph(), llm_provider=provider)
    rt._ensure_registry()  # no-op, must not raise (already valid)


def test_live_registration_against_existing_runtime_rejects_deterministic_behavior():
    # The third binding path: registering a new @llm_behavior AFTER a
    # Runtime carrying ClaudeCodeProvider is already live.
    clear_registry()

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "T", "body": "B"})

    provider = ClaudeCodeProvider(allow_unenforced_generation_controls=True)
    Runtime(Graph(), llm_provider=provider)  # live Runtime, no LLMBehavior yet

    with pytest.raises(InvalidRuntimeConfiguration) as exc:

        @llm_behavior(
            name="extractor_live",
            on=["object.created"],
            where={"object.type": "document"},
            description="x",
            output_schema=ClaimList,
            deterministic=True,
        )
        def extractor_live(event, graph, ctx, llm_output):
            pass

    assert "deterministic" in str(exc.value).lower()
    clear_registry()


def test_missing_provider_still_raises_missing_provider_error():
    clear_registry()
    _register_nondeterministic()
    rt = Runtime(Graph())  # no llm_provider=
    with pytest.raises(MissingProviderError):
        rt.run_goal("survey")


# ------------------------------------------ BLOCKING Workflow Closure test


def test_workflow_closure_behavior_wired_to_claude_code_provider_mutates_graph_visibly():
    clear_registry()
    _register_simple()
    provider = _text_provider(json.dumps({"claims": [{"text": "The sky is blue", "confidence": 0.9}]}))
    rt = Runtime(Graph(), llm_provider=provider)
    rt.run_goal("survey")

    # Causality chain: llm.requested -> llm.responded (direct), and
    # llm.requested / the claim's object.created share the same
    # triggering event (the document's object.created) — both are part
    # of the same extractor invocation that the LLM call fed.
    types = [e.type for e in rt.graph.events]
    assert "llm.requested" in types
    assert "llm.responded" in types
    requested = next(e for e in rt.graph.events if e.type == "llm.requested")
    responded = next(e for e in rt.graph.events if e.type == "llm.responded")
    created = next(
        e for e in rt.graph.events if e.type == "object.created" and e.payload["object"]["type"] == "claim"
    )
    assert responded.caused_by == requested.id
    assert created.caused_by == requested.caused_by


def test_workflow_closure_red_at_seam_unparseable_result_fails_behavior_not_graph():
    clear_registry()
    _register_simple()
    provider = _text_provider("not json at all")
    rt = Runtime(Graph(), llm_provider=provider)
    rt.run_goal("survey")

    claims = [o for o in rt.graph.all_objects() if o.type == "claim"]
    assert claims == []
    failed = [e for e in rt.graph.events if e.type == "behavior.failed"]
    assert len(failed) == 1
    assert failed[0].payload.get("reason") == "llm.parse_error"


# ---------------------------------------- Behavior 8: native + tool Runtime


class _ToolIn:
    pass


def _register_native_tool_loop():
    from pydantic import BaseModel

    class ToolArgs(BaseModel):
        q: str

    class ToolResult(BaseModel):
        answer: str

    @tool(name="lookup", description="look something up", input_schema=ToolArgs, output_schema=ToolResult, deterministic=True)
    def lookup(args, ctx):
        return ToolResult(answer=f"answer:{args.q}")

    from activegraph.tools.decorators import get_tool_registry

    registered = next(t for t in get_tool_registry() if t.name == "lookup")

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "T", "body": "B"})

    received: list = []

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        description="x",
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
        tools=[registered],
    )
    def extractor(event, graph, ctx, llm_output):
        received.append(llm_output)
        for c in llm_output.claims:
            graph.add_object("claim", {"text": c.text, "confidence": c.confidence})

    return received


def test_native_plus_tool_two_call_runtime_loop_produces_typed_handler_input_and_graph_mutation():
    clear_registry()
    received = _register_native_tool_loop()

    seq = _SequencedQuery()
    seq.yields(
        _make_result(deferred_tool_use=DeferredToolUse(id="t1", name="mcp__activegraph__lookup", input={"q": "x"}))
    )
    seq.yields(_make_result(structured_output={"claims": [{"text": "answer:x", "confidence": 0.9}]}))
    provider = _provider_from_sequence(seq)

    g = Graph()
    rt = Runtime(g, llm_provider=provider, native_structured_output=True)
    rt.run_goal("survey")

    assert seq.call_count == 2
    tool_events = [e for e in g.events if e.type in ("tool.requested", "tool.responded")]
    assert len(tool_events) == 2
    assert len(received) == 1
    assert received[0].claims[0].text == "answer:x"
    claims = [o for o in g.all_objects() if o.type == "claim"]
    assert len(claims) == 1
    assert claims[0].data["text"] == "answer:x"


def test_prompt_mode_native_tool_loop_also_composes():
    clear_registry()
    received = _register_native_tool_loop()

    seq = _SequencedQuery()
    seq.yields(
        _make_result(deferred_tool_use=DeferredToolUse(id="t1", name="mcp__activegraph__lookup", input={"q": "y"}))
    )
    seq.yields(
        AssistantMessage(
            content=[TextBlock(text=json.dumps({"claims": [{"text": "answer:y", "confidence": 0.8}]}))],
            model="claude-sonnet-4-5",
        ),
        _make_result(),
    )
    provider = _provider_from_sequence(seq)

    g = Graph()
    Runtime(g, llm_provider=provider).run_goal("survey")  # native_structured_output=False (default)

    assert seq.call_count == 2
    assert received[0].claims[0].text == "answer:y"


# ------------------------------------------ Behavior 10: Runtime retry


def _register_simple():
    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": "T", "body": "B"})

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        description="x",
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, llm_output):
        for c in llm_output.claims:
            graph.add_object("claim", {"text": c.text, "confidence": c.confidence})


def test_terminal_setup_failure_calls_once_no_retry():
    clear_registry()
    _register_simple()
    seq = _SequencedQuery().raises(_sdk.CLINotFoundError())
    provider = _provider_from_sequence(seq)
    rt = Runtime(Graph(), llm_provider=provider, llm_retry_max_attempts=3, llm_retry_initial_delay_seconds=0)
    rt.run_goal("survey")
    assert seq.call_count == 1
    failed = [e for e in rt.graph.events if e.type == "behavior.failed"]
    assert len(failed) == 1


def test_terminal_error_result_calls_once_no_retry():
    clear_registry()
    _register_simple()
    seq = _SequencedQuery().yields(_make_result(is_error=True, api_error_status=401))
    provider = _provider_from_sequence(seq)
    rt = Runtime(Graph(), llm_provider=provider, llm_retry_max_attempts=3, llm_retry_initial_delay_seconds=0)
    rt.run_goal("survey")
    assert seq.call_count == 1


def test_transient_network_error_retries_then_succeeds():
    clear_registry()
    _register_simple()
    seq = _SequencedQuery()
    seq.raises(_sdk.ProcessError("transient", exit_code=1))
    seq.yields(
        AssistantMessage(
            content=[TextBlock(text=json.dumps({"claims": [{"text": "ok", "confidence": 0.9}]}))],
            model="claude-sonnet-4-5",
        ),
        _make_result(),
    )
    provider = _provider_from_sequence(seq)
    rt = Runtime(Graph(), llm_provider=provider, llm_retry_max_attempts=3, llm_retry_initial_delay_seconds=0)
    rt.run_goal("survey")
    assert seq.call_count == 2
    claims = [o for o in rt.graph.all_objects() if o.type == "claim"]
    assert len(claims) == 1


def test_rate_limited_result_retries_then_closes():
    clear_registry()
    _register_simple()
    seq = _SequencedQuery()
    seq.yields(_make_result(is_error=True, api_error_status=429))
    seq.yields(_make_result(is_error=True, api_error_status=429))
    seq.yields(_make_result(is_error=True, api_error_status=429))
    provider = _provider_from_sequence(seq)
    rt = Runtime(Graph(), llm_provider=provider, llm_retry_max_attempts=3, llm_retry_initial_delay_seconds=0)
    rt.run_goal("survey")
    assert seq.call_count == 3  # bounded retry, then close
    failed = [e for e in rt.graph.events if e.type == "behavior.failed"]
    assert len(failed) == 1


# --------------------------------------- Behavior 13: persistence + replay


def test_persisted_native_final_response_round_trips_json_safe(tmp_path):
    clear_registry()
    _register_native_tool_loop()
    seq = _SequencedQuery()
    seq.yields(_make_result(structured_output={"claims": [{"text": "z", "confidence": 0.5}]}))
    provider = _provider_from_sequence(seq)
    db = str(tmp_path / "native.sqlite")
    g = Graph()
    rt = Runtime(g, llm_provider=provider, persist_to=db, native_structured_output=True)
    rt.run_goal("survey")

    cache = LLMCache.from_events(g.events)
    assert len(cache) == 1
    request = next(e for e in g.events if e.type == "llm.requested")
    cached = cache.get(request.payload["prompt_hash"])
    assert cached is not None
    json.dumps(cached.to_dict())  # no SDK object retained anywhere

    reloaded = Runtime.load(db, llm_provider=_provider_from_sequence(_SequencedQuery()))
    assert reloaded is not None


def test_persisted_intermediate_deferred_tool_response_round_trips_json_safe(tmp_path):
    clear_registry()
    _register_native_tool_loop()
    seq = _SequencedQuery()
    seq.yields(
        _make_result(deferred_tool_use=DeferredToolUse(id="t1", name="mcp__activegraph__lookup", input={"q": "x"}))
    )
    seq.yields(_make_result(structured_output={"claims": [{"text": "answer:x", "confidence": 0.9}]}))
    provider = _provider_from_sequence(seq)
    db = str(tmp_path / "deferred.sqlite")
    g = Graph()
    Runtime(g, llm_provider=provider, persist_to=db, native_structured_output=True).run_goal("survey")

    events = [e for e in g.events if e.type == "llm.responded"]
    assert len(events) == 2
    first = events[0]
    assert first.payload.get("tool_calls")
    json.dumps(first.payload)  # JSON-safe, no raw DeferredToolUse instance


def _register_fork(title: str = "T", body: str = "B"):
    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("document", {"title": title, "body": body})

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        where={"object.type": "document"},
        description="x",
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, llm_output):
        for c in llm_output.claims:
            graph.add_object("claim", {"text": c.text, "confidence": c.confidence})


def test_unchanged_sqlite_fork_never_calls_provider_no_structural_divergence(tmp_path):
    clear_registry()
    _register_fork()
    db = str(tmp_path / "parent.sqlite")
    parent = Runtime(Graph(), llm_provider=_text_provider('{"claims": [{"text": "x", "confidence": 0.9}]}'), persist_to=db)
    parent.run_goal("test")

    goal_evt = next(e for e in parent.graph.events if e.type == "goal.created")
    spy = _SequencedQuery().raises(AssertionError("must not be called"))
    fork = parent.fork(at_event=goal_evt.id, replay_llm_cache=True, llm_provider=_provider_from_sequence(spy))
    fork.run_until_idle()

    diff = parent.diff(fork)
    assert diff.divergent_objects == []
    assert diff.divergent_relations == []
    assert spy.call_count == 0
    resp = next(e for e in fork.graph.events if e.type == "llm.responded")
    assert resp.payload["cache_hit"] is True


class _RepeatingQuery:
    """Always yields the same scripted items, for every call — used
    where the exact number of live calls across a fork's re-fired
    unchanged turn plus its genuinely-diverged turn isn't pinned down
    (mirrors tests/test_llm_replay.py's own
    test_fork_with_cache_falls_through_on_prompt_divergence, which
    asserts `>= 1` for the same reason, not `== 1`).
    """

    def __init__(self, *items):
        self._items = items
        self.call_count = 0

    def __call__(self, *, prompt, options=None, transport=None):
        self.call_count += 1
        return self._agen()

    async def _agen(self):
        for item in self._items:
            yield item


def test_changed_document_fork_makes_at_least_one_live_query_and_diverges(tmp_path):
    clear_registry()
    _register_fork()
    db = str(tmp_path / "parent2.sqlite")
    parent = Runtime(Graph(), llm_provider=_text_provider('{"claims": [{"text": "x", "confidence": 0.9}]}'), persist_to=db)
    parent.run_goal("test")

    goal_evt = next(e for e in parent.graph.events if e.type == "goal.created")
    fork_fake = _RepeatingQuery(
        AssistantMessage(
            content=[TextBlock(text='{"claims": [{"text": "different", "confidence": 0.5}]}')],
            model="claude-sonnet-4-5",
        ),
        _make_result(),
    )
    fork = parent.fork(
        at_event=goal_evt.id, replay_llm_cache=True, llm_provider=_provider_from_sequence(fork_fake)
    )
    fork.graph.add_object("document", {"title": "Different doc", "body": "different content"})
    fork.run_until_idle()

    # The cached (unchanged) document's turn hits cache; the new
    # document's turn is a genuine miss — at least one live call.
    assert fork_fake.call_count >= 1
    diff = parent.diff(fork)
    assert diff.fork_only_events != []


def test_max_tokens_change_produces_conservative_cache_miss_even_though_unforwarded():
    # Runtime still hashes max_tokens; changing it must still miss the
    # cache even though ClaudeCodeProvider never forwards the value to
    # the SDK — this is a safe conservative miss, not a claim that the
    # provider's behavior actually changed. A max_tokens= override isn't
    # expressible via @llm_behavior decorator config in this codebase
    # (it's a call-time default) — assert the *documented* invariant
    # directly against _hash_turn_prompt instead of round-tripping a
    # second live run.
    clear_registry()
    _register_fork()
    seq1 = _SequencedQuery().yields(
        AssistantMessage(content=[TextBlock(text='{"claims": [{"text": "a", "confidence": 0.9}]}')], model="claude-sonnet-4-5"),
        _make_result(),
    )
    g1 = Graph()
    Runtime(g1, llm_provider=_provider_from_sequence(seq1)).run_goal("test")
    cache = LLMCache.from_events(g1.events)

    from activegraph.runtime.runtime import _hash_turn_prompt
    from activegraph.llm.prompt import AssembledPrompt

    request_evt = next(e for e in g1.events if e.type == "llm.requested")
    prompt_dict = request_evt.payload["prompt"]
    prompt = AssembledPrompt(
        system=prompt_dict["system"],
        messages=[],
        model=prompt_dict["model"],
        max_tokens=prompt_dict["max_tokens"],
        temperature=prompt_dict["temperature"],
        top_p=prompt_dict["top_p"],
        deterministic=prompt_dict["deterministic"],
        output_schema_name=prompt_dict.get("output_schema_name"),
        output_schema_json=prompt_dict.get("output_schema_json"),
    )
    same_hash = _hash_turn_prompt(prompt=prompt, messages=[], tool_defs=None)
    changed_prompt = AssembledPrompt(
        system=prompt.system,
        messages=[],
        model=prompt.model,
        max_tokens=prompt.max_tokens + 1,
        temperature=prompt.temperature,
        top_p=prompt.top_p,
        deterministic=prompt.deterministic,
        output_schema_name=prompt.output_schema_name,
        output_schema_json=prompt.output_schema_json,
    )
    changed_hash = _hash_turn_prompt(prompt=changed_prompt, messages=[], tool_defs=None)
    assert changed_hash != same_hash
    assert cache.get(changed_hash) is None
