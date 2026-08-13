"""Tests for the pack format. CONTRACT v0.9 #11 / #14.

Covers:
  - Pack dataclass: frozen, eq/hash by (name, version), validation
  - Pack-aware decorators: no global side effects
  - Pack loading: happy path, idempotency, conflict detection,
    settings validation, namespace prefixing
  - Object type schema validation: typed pack rejects malformed,
    untyped still works
  - Settings access: typed injection (Form 1), ctx.settings (Form 2),
    ctx.pack_settings (Form 3)
  - Prompt loading: frontmatter parsing, content hashing,
    PackPromptLoadError surface
  - Pack lookup: short name resolves when unambiguous, raises on
    ambiguity, fully-qualified always works
  - Entry point discovery: enumerate, load_by_name
"""

from __future__ import annotations

import textwrap
from dataclasses import fields
from decimal import Decimal, InvalidOperation
import importlib
import inspect
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from activegraph import (
    FrozenClock,
    Graph,
    Object,
    Pack,
    PackConflictError,
    PackError,
    PackPolicy,
    PackPromptLoadError,
    PackSchemaViolation,
    PackSettingsMissingError,
    PackValidationError,
    PackVersionConflictError,
    ObjectType,
    RelationType,
    Runtime,
    behavior as user_behavior,
    llm_behavior as user_llm_behavior,
    relation_behavior as user_relation_behavior,
    tool as user_tool,
    clear_registry,
    clear_tool_registry,
    discover,
    get_registry,
    get_tool_registry,
    load_by_name,
    load_prompts_from_dir,
)
from activegraph.packs import (
    EmptySettings,
    PackPrompt,
    behavior,
    llm_behavior,
    relation_behavior,
    tool,
)
from activegraph.packs.loader import AMBIGUOUS
from activegraph.core.event import Event


# ---------------------------------------------------- Pack dataclass


class _DemoSettings(BaseModel):
    threshold: float = 0.5


class _Widget(BaseModel):
    name: str
    size: int = Field(ge=0)


def test_pack_basic_construction():
    @behavior(name="ping", on=["goal.created"])
    def ping(event, graph, ctx):
        pass

    p = Pack(
        name="demo",
        version="0.1.0",
        description="A demo pack.",
        object_types=[ObjectType(name="widget", schema=_Widget)],
        behaviors=[ping],
        settings_schema=_DemoSettings,
    )
    assert p.name == "demo"
    assert p.version == "0.1.0"
    assert isinstance(p.object_types, tuple)
    assert isinstance(p.behaviors, tuple)


def test_requires_approval_attributes_only_explicit_proposals():
    """``requires_approval`` does not intercept ``Graph.add_object``.

    The pack policy supplies owner attribution only when behavior code
    explicitly chooses ``Context.propose_object``.
    """
    proposal_ids: list[str] = []

    @behavior(name="proposer", on=["goal.created"])
    def proposer(event, graph, ctx):
        proposal_ids.append(
            ctx.propose_object(
                "secret",
                {"value": "proposed"},
                reason="operator review",
            )
        )

    pack = Pack(
        name="approval_explicit",
        version="0.1.0",
        behaviors=(proposer,),
        policies=(
            PackPolicy(
                name="secret_approval",
                requires_approval=("secret",),
            ),
        ),
    )
    rt = _fresh_runtime()
    rt.load_pack(pack)

    before = list(rt.graph.all_objects())
    direct = rt.graph.add_object("secret", {"value": "direct"})

    assert isinstance(direct, Object)
    assert len(rt.graph.all_objects()) == len(before) + 1
    assert rt.graph.get_object(direct.id).data == {"value": "direct"}
    assert rt.pending_approvals() == []
    direct_event = next(
        event
        for event in rt.graph.events
        if event.type == "object.created" and event.payload["id"] == direct.id
    )
    assert direct_event.payload["object"]["data"] == {"value": "direct"}

    object_snapshot = [
        (obj.id, obj.type, obj.data) for obj in rt.graph.all_objects()
    ]
    rt.run_goal("propose the reviewed value")

    assert proposal_ids == ["approval_001"]
    assert [
        (obj.id, obj.type, obj.data) for obj in rt.graph.all_objects()
    ] == object_snapshot
    pending = rt.pending_approvals()
    assert [approval.id for approval in pending] == proposal_ids
    assert pending[0].pack == "approval_explicit"
    assert pending[0].object_type == "secret"
    assert pending[0].data == {"value": "proposed"}
    proposed_event = next(
        event
        for event in rt.graph.events
        if event.type == "approval.proposed"
    )
    assert proposed_event.payload["approval_id"] == proposal_ids[0]
    assert proposed_event.payload["pack"] == "approval_explicit"

    assert rt.disable_pack("approval_explicit") is True
    before_disabled_add = len(rt.graph.all_objects())
    after_disable = rt.graph.add_object("secret", {"value": "after disable"})
    assert isinstance(after_disable, Object)
    assert len(rt.graph.all_objects()) == before_disabled_add + 1
    assert rt.graph.get_object(after_disable.id).data == {
        "value": "after disable"
    }
    assert [approval.id for approval in rt.pending_approvals()] == proposal_ids

    approved_object_id = rt.approve(proposal_ids[0], approved_by="operator")
    assert rt.pending_approvals() == []
    approved = rt.graph.get_object(approved_object_id)
    assert approved is not None
    assert approved.type == "secret"
    assert approved.data == {"value": "proposed"}


def test_auto_apply_is_normalized_reserved_metadata_without_runtime_effect():
    """``auto_apply`` is preserved metadata, not a runtime instruction."""

    tuple_policy = PackPolicy(name="memo_review", auto_apply=("memo",))
    list_policy = PackPolicy(name="memo_review", auto_apply=["memo"])
    assert list_policy.auto_apply == tuple_policy.auto_apply == ("memo",)

    def run_workflow(auto_apply):
        proposal_ids: list[str] = []

        @behavior(name="proposer", on=["goal.created"])
        def proposer(event, graph, ctx):
            proposal_ids.append(
                ctx.propose_object(
                    "memo",
                    {"value": "proposed"},
                    reason="operator review",
                )
            )

        policy = PackPolicy(
            name="memo_review",
            requires_approval=("memo",),
            auto_apply=auto_apply,
        )
        pack = Pack(
            name="reserved_auto_apply",
            version="0.1.0",
            behaviors=(proposer,),
            policies=(policy,),
        )
        rt = Runtime(
            Graph(
                clock=FrozenClock("2026-08-12T00:00:00Z"),
                run_id="run_reserved_auto_apply",
            )
        )

        assert rt.load_pack(pack) is True
        direct = rt.graph.add_object("memo", {"value": "direct"})
        rt.run_goal("propose the reviewed memo")

        assert proposal_ids == ["approval_001"]
        assert policy.auto_apply == tuple(auto_apply)
        return policy, {
            "loaded_packs": rt.loaded_packs(),
            "events": [event.to_dict() for event in rt.graph.events],
            "direct": direct.to_dict(),
            "objects": [obj.to_dict() for obj in rt.graph.all_objects()],
            "proposal_ids": proposal_ids,
            "pending": rt.pending_approvals(),
        }

    empty_policy, without_reserved_value = run_workflow(())
    populated_policy, with_reserved_value = run_workflow(("memo",))

    assert empty_policy.auto_apply == ()
    assert populated_policy.auto_apply == ("memo",)
    assert without_reserved_value == with_reserved_value


def test_pack_is_frozen():
    @behavior(name="ping", on=["goal.created"])
    def ping(event, graph, ctx):
        pass

    p = Pack(name="demo", version="0.1.0", behaviors=[ping], settings_schema=EmptySettings)
    with pytest.raises(Exception):  # FrozenInstanceError
        p.name = "different"  # type: ignore[misc]


def test_pack_equality_by_name_and_version():
    @behavior(name="ping", on=["goal.created"])
    def ping(event, graph, ctx):
        pass

    @behavior(name="pong", on=["goal.created"])
    def pong(event, graph, ctx):
        pass

    p1 = Pack(name="demo", version="0.1.0", behaviors=[ping], settings_schema=EmptySettings)
    p2 = Pack(name="demo", version="0.1.0", behaviors=[pong], settings_schema=EmptySettings)
    p3 = Pack(name="demo", version="0.2.0", behaviors=[ping], settings_schema=EmptySettings)
    assert p1 == p2  # Different behaviors but same (name, version)
    assert p1 != p3  # Different version
    assert hash(p1) == hash(p2)
    assert hash(p1) != hash(p3)


def test_pack_name_validation():
    with pytest.raises(PackValidationError):
        Pack(name="UPPER", version="0.1.0", settings_schema=EmptySettings)
    with pytest.raises(PackValidationError):
        Pack(name="9_starts_with_digit", version="0.1.0", settings_schema=EmptySettings)
    with pytest.raises(PackValidationError):
        Pack(name="", version="0.1.0", settings_schema=EmptySettings)


def test_pack_duplicate_behavior_name_rejected():
    @behavior(name="ping", on=["goal.created"])
    def ping1(event, graph, ctx):
        pass

    @behavior(name="ping", on=["goal.created"])
    def ping2(event, graph, ctx):
        pass

    with pytest.raises(PackValidationError, match="duplicate behavior"):
        Pack(name="demo", version="0.1.0",
             behaviors=[ping1, ping2], settings_schema=EmptySettings)


def test_pack_rejects_globally_registered_behavior():
    """A @behavior decorated from activegraph (not activegraph.packs)
    registers globally; passing it to Pack must raise.
    """
    clear_registry()

    @user_behavior(name="user_ping", on=["goal.created"])
    def user_ping(event, graph, ctx):
        pass

    with pytest.raises(PackValidationError, match="not declared via"):
        Pack(name="demo", version="0.1.0", behaviors=[user_ping],
             settings_schema=EmptySettings)
    clear_registry()


def test_pack_rejects_globally_registered_tool():
    @user_tool(name="global-tool")
    def global_tool(args, ctx):
        return None

    with pytest.raises(PackValidationError, match="activegraph.packs.tool"):
        Pack(
            name="demo",
            version="0.1.0",
            tools=[global_tool],
            settings_schema=EmptySettings,
        )


def test_pack_settings_schema_must_be_basemodel():
    class NotAModel:
        pass

    with pytest.raises(PackValidationError, match="BaseModel subclass"):
        Pack(name="demo", version="0.1.0", settings_schema=NotAModel)  # type: ignore[arg-type]


# ---------------------------------------------------- decorators have no global side effects


def test_pack_decorators_do_not_register_globally():
    """The single most important property of the pack format: a pack
    module's decorators must not leak into the global registry.
    """
    clear_registry()
    clear_tool_registry()

    @behavior(name="x", on=["a.b"])
    def x(event, graph, ctx):
        pass

    @llm_behavior(name="y", on=["a.b"], output_schema=_Widget)
    def y(event, graph, ctx, out):
        pass

    @tool(name="t", input_schema=_Widget, output_schema=_Widget)
    def t(args, ctx):
        return _Widget(name="x", size=0)

    @relation_behavior("supports", name="z", on=["relation.created"])
    def z(rel, event, graph, ctx):
        pass

    assert get_registry() == []
    assert get_tool_registry() == []


def test_pack_decorators_attach_pack_meta_to_function():
    @behavior(name="x", on=["a.b"])
    def x(event, graph, ctx):
        pass

    # The decorator returns a Behavior object; the underlying function
    # gets __pack_meta__.
    assert hasattr(x.fn, "__pack_meta__")
    assert x.fn.__pack_meta__["kind"] == "behavior"
    assert x.fn.__pack_meta__["name"] == "x"


def _assert_behavior_domain_parity(global_obj, pack_obj) -> None:
    skipped = {"fn", "handler", "pattern_matcher"}
    for field in fields(global_obj):
        if field.name in skipped:
            continue
        value = getattr(global_obj, field.name)
        peer = getattr(pack_obj, field.name)
        assert value == peer, field.name
        assert type(value) is type(peer), field.name

    assert global_obj.pattern_matcher.pattern.source == (
        pack_obj.pattern_matcher.pattern.source
    )
    assert global_obj.pattern_matcher.pattern.match == (
        pack_obj.pattern_matcher.pattern.match
    )
    assert global_obj.pattern_matcher.pattern.where == (
        pack_obj.pattern_matcher.pattern.where
    )
    positive = Graph()
    positive.add_object("claim", {})
    event = Event(id="evt_match", type="custom.event")
    assert bool(global_obj.pattern_matcher.matches(event, positive)) is True
    assert bool(pack_obj.pattern_matcher.matches(event, positive)) is True
    assert global_obj.pattern_matcher.matches(event, Graph()) == []
    assert pack_obj.pattern_matcher.matches(event, Graph()) == []


def test_global_and_pack_behavior_construction_has_exact_domain_parity():
    common = dict(
        name="parity",
        on=["custom.event"],
        where={"kind": "x"},
        view={"objects": ["claim"]},
        creates=["result"],
        budget={"max_events": 2},
        priority=3,
        pattern="(c:claim)",
        activate_after=2,
    )

    def global_plain_fn(event, graph, ctx):
        pass

    def pack_plain_fn(event, graph, ctx):
        pass

    global_plain = user_behavior(**common)(global_plain_fn)
    pack_plain = behavior(**common)(pack_plain_fn)
    _assert_behavior_domain_parity(global_plain, pack_plain)
    assert global_plain.fn is global_plain_fn
    assert pack_plain.fn is pack_plain_fn

    llm_common = common | {
        "description": "describe",
        "model": "m",
        "output_schema": _Widget,
        "deterministic": True,
        "max_tokens": 12,
        "temperature": 0.0,
        "top_p": 1.0,
        "timeout_seconds": 2,
        "prompt_template": "{system}",
        "tools": ["lookup"],
        "max_tool_turns": 2,
    }

    def global_llm_fn(event, graph, ctx, out):
        pass

    def pack_llm_fn(event, graph, ctx, out):
        pass

    global_llm = user_llm_behavior(**llm_common)(global_llm_fn)
    pack_llm = llm_behavior(**llm_common)(pack_llm_fn)
    _assert_behavior_domain_parity(global_llm, pack_llm)
    assert global_llm.fn is pack_llm.fn
    assert global_llm.handler is global_llm_fn
    assert pack_llm.handler is pack_llm_fn

    relation_common = common | {"relation_type": "supports"}

    def global_relation_fn(relation, event, graph, ctx):
        pass

    def pack_relation_fn(relation, event, graph, ctx):
        pass

    global_relation = user_relation_behavior(**relation_common)(global_relation_fn)
    pack_relation = relation_behavior(**relation_common)(pack_relation_fn)
    _assert_behavior_domain_parity(global_relation, pack_relation)
    assert global_relation.fn is global_relation_fn
    assert pack_relation.fn is pack_relation_fn


def test_pack_behavior_metadata_and_loader_clones_preserve_all_fields():
    @behavior(
        name="plain",
        on=["custom.event"],
        where={"kind": "x"},
        pattern="(c:claim)",
        activate_after=2,
    )
    def plain(event, graph, ctx):
        pass

    @llm_behavior(
        name="llm",
        on=["custom.event"],
        where={"kind": "x"},
        output_schema=_Widget,
        pattern="(c:claim)",
        activate_after=2,
    )
    def llm(event, graph, ctx, out):
        pass

    @relation_behavior(
        "supports",
        name="relation",
        on=["custom.event"],
        pattern="(c:claim)",
        activate_after=2,
    )
    def relation(rel, event, graph, ctx):
        pass

    assert plain.fn.__pack_meta__ == {
        "kind": "behavior",
        "name": "plain",
        "on": ["custom.event"],
        "where": {"kind": "x"},
    }
    assert llm.handler.__pack_meta__ == {
        "kind": "llm_behavior",
        "name": "llm",
        "on": ["custom.event"],
        "where": {"kind": "x"},
        "output_schema": "_Widget",
    }
    assert relation.fn.__pack_meta__ == {
        "kind": "relation_behavior",
        "name": "relation",
        "relation_type": "supports",
    }

    pack = Pack(
        name="clonepack",
        version="1.0.0",
        behaviors=[plain, llm, relation],
        settings_schema=EmptySettings,
    )
    runtime = _fresh_runtime()
    runtime.load_pack(pack)
    clones = {clone._short_name: clone for clone in runtime._pack_behaviors}

    for original in (plain, llm, relation):
        clone = clones[original.name]
        assert clone.name == f"clonepack.{original.name}"
        assert clone._pack_owner == "clonepack"
        assert clone._short_name == original.name
        assert clone._pack_local is True
        for field in fields(original):
            if field.name in {"name", "fn", "handler"}:
                continue
            value = getattr(original, field.name)
            peer = getattr(clone, field.name)
            assert value == peer, field.name
            assert type(value) is type(peer), field.name


def test_global_and_pack_tool_signatures_match_except_pack_export_policy():
    global_params = inspect.signature(user_tool).parameters
    pack_params = inspect.signature(tool).parameters
    assert tuple(global_params) == tuple(
        name for name in pack_params if name != "export_globally"
    )
    for name, parameter in global_params.items():
        peer = pack_params[name]
        assert parameter.kind == peer.kind
        assert parameter.default == peer.default
        assert parameter.annotation == peer.annotation


@pytest.mark.parametrize("cost", [2, "2.50", Decimal("3.00")])
def test_global_and_pack_tool_construction_has_exact_type_parity(cost):
    def global_fn(args: _Widget, ctx):
        return args

    def pack_fn(args: _Widget, ctx):
        return args

    kwargs = {
        "name": "parity-tool",
        "description": "parity",
        "cost_per_call": cost,
        "timeout_seconds": 2,
        "deterministic": 1,
    }
    global_tool = user_tool(**kwargs)(global_fn)
    pack_tool = tool(**kwargs)(pack_fn)

    for field in fields(global_tool):
        if field.name == "fn":
            continue
        value = getattr(global_tool, field.name)
        peer = getattr(pack_tool, field.name)
        assert value == peer, field.name
        assert type(value) is type(peer), field.name
    assert global_tool.fn is global_fn
    assert pack_tool.fn is pack_fn
    assert global_tool.input_schema is _Widget
    assert pack_tool.input_schema is _Widget
    assert type(pack_tool.timeout_seconds) is float
    assert type(pack_tool.deterministic) is bool


def test_omitted_pack_tool_cost_is_exact_canonical_decimal_zero():
    @user_tool(name="global-zero")
    def global_zero(args, ctx):
        return None

    @tool(name="pack-zero")
    def pack_zero(args, ctx):
        return None

    for decorated in (global_zero, pack_zero):
        assert type(decorated.cost_per_call) is Decimal
        assert str(decorated.cost_per_call) == "0"
        assert decorated.cost_per_call.as_tuple() == Decimal("0").as_tuple()


def test_invalid_tool_cost_precedes_bad_handler_in_both_namespaces():
    for decorator in (user_tool, tool):
        with pytest.raises(InvalidOperation):
            binder = decorator(cost_per_call="not-a-decimal")

            @binder
            def bad_handler(ctx):
                pass


def test_both_tool_decorators_delegate_and_keep_effect_policy(
    monkeypatch,
) -> None:
    factory = importlib.import_module("activegraph.tools._factory")
    global_module = importlib.import_module("activegraph.tools.decorators")
    pack_module = importlib.import_module("activegraph.packs")
    outer: list[dict] = []
    bound: list[object] = []
    appended: list[object] = []

    class SpyRegistry(list):
        def append(self, value):
            appended.append(value)
            super().append(value)

    monkeypatch.setattr(global_module, "_TOOL_REGISTRY", SpyRegistry())

    def builder(**kwargs):
        outer.append(kwargs)

        def bind(fn):
            from activegraph.tools.base import Tool

            bound.append(fn)
            return Tool(name=kwargs.get("name") or fn.__name__, fn=fn)

        return bind

    monkeypatch.setattr(factory, "build_tool", builder)

    def global_fn(args, ctx):
        return None

    def pack_fn(args, ctx):
        return None

    global_result = global_module.tool(name="global")(global_fn)
    pack_result = pack_module.tool(name="pack", export_globally=1)(pack_fn)

    assert global_module.tool_factory is factory
    assert pack_module.tool_factory is factory
    assert [call["name"] for call in outer] == ["global", "pack"]
    assert bound == [global_fn, pack_fn]
    assert appended == [global_result]
    assert getattr(pack_result, "_pack_local") is True
    assert getattr(pack_result, "_export_globally") is True
    assert pack_fn.__pack_meta__ == {
        "kind": "tool",
        "name": "pack",
        "deterministic": False,
        "export_globally": True,
    }


def test_pack_tool_export_aliases_share_loader_clone_without_global_append():
    @tool(name="hidden", export_globally=False)
    def hidden(args, ctx):
        return None

    @tool(name="shown", export_globally=True)
    def shown(args, ctx):
        return None

    pack = Pack(
        name="toolpack",
        version="1.0.0",
        tools=[hidden, shown],
        settings_schema=EmptySettings,
    )
    assert get_tool_registry() == []
    runtime = Runtime(Graph(), behaviors=[], tools=[])
    runtime.load_pack(pack)
    runtime._ensure_registry()

    hidden_clone = runtime.tool_registry["toolpack.hidden"]
    shown_clone = runtime.tool_registry["toolpack.shown"]
    assert "hidden" not in runtime.tool_registry
    assert runtime.get_tool("hidden") is hidden_clone
    assert runtime.tool_registry["shown"] is shown_clone
    assert shown_clone is not shown
    assert hidden_clone is not hidden
    assert shown_clone._pack_owner == "toolpack"
    assert shown_clone._short_name == "shown"
    assert shown_clone._pack_local is True
    assert shown_clone._export_globally is True
    for original, clone in ((hidden, hidden_clone), (shown, shown_clone)):
        for field in fields(original):
            if field.name in {"name", "fn"}:
                continue
            value = getattr(original, field.name)
            peer = getattr(clone, field.name)
            assert value == peer, field.name
            assert type(value) is type(peer), field.name
    assert get_tool_registry() == []


def test_pack_accepts_all_decorator_products_and_tool_metadata_is_exact():
    @behavior(name="plain")
    def plain(event, graph, ctx):
        pass

    @llm_behavior(name="llm", output_schema=_Widget)
    def llm(event, graph, ctx, out):
        pass

    @relation_behavior("edge", name="relation")
    def relation(rel, event, graph, ctx):
        pass

    @tool(name="pack-tool", deterministic=1, export_globally=1)
    def pack_tool(args: _Widget, ctx):
        return args

    pack = Pack(
        name="completepack",
        version="1.0.0",
        behaviors=[plain, llm, relation],
        tools=[pack_tool],
        settings_schema=EmptySettings,
    )

    assert tuple(pack.behaviors) == (plain, llm, relation)
    assert tuple(pack.tools) == (pack_tool,)
    assert pack_tool.fn.__pack_meta__ == {
        "kind": "tool",
        "name": "pack-tool",
        "deterministic": True,
        "export_globally": True,
    }


def test_exported_pack_tool_collision_is_premutation():
    @user_tool(name="collision")
    def global_collision(args, ctx):
        return None

    @tool(name="collision", export_globally=True)
    def pack_collision(args, ctx):
        return None

    pack = Pack(
        name="toolpack",
        version="1.0.0",
        tools=[pack_collision],
        settings_schema=EmptySettings,
    )
    runtime = Runtime(Graph())
    before_events = runtime.graph.events

    with pytest.raises(PackConflictError):
        runtime.load_pack(pack)

    assert runtime.loaded_packs() == []
    assert runtime._pack_tools == []
    assert runtime.graph.events == before_events
    assert get_tool_registry() == [global_collision]


# ---------------------------------------------------- prompt loading


def test_load_prompts_from_dir(tmp_path):
    (tmp_path / "first.md").write_text(textwrap.dedent("""
        ---
        version = "1.2.3"
        ---
        Body of the first prompt.
    """).strip(), encoding="utf-8")
    (tmp_path / "second.md").write_text(textwrap.dedent("""
        ---
        version = "0.1.0"
        name = "renamed_second"
        ---
        Body of the second prompt.
    """).strip(), encoding="utf-8")

    prompts = load_prompts_from_dir(tmp_path)
    by_name = {p.name: p for p in prompts}
    assert "first" in by_name
    assert "renamed_second" in by_name
    assert by_name["first"].version == "1.2.3"
    assert by_name["renamed_second"].version == "0.1.0"


def test_load_prompts_content_hash_is_stable(tmp_path):
    (tmp_path / "p.md").write_text(textwrap.dedent("""
        ---
        version = "1.0.0"
        ---
        Body content.
    """).strip(), encoding="utf-8")
    p1 = load_prompts_from_dir(tmp_path)[0]
    p2 = load_prompts_from_dir(tmp_path)[0]
    assert p1.content_hash == p2.content_hash
    assert p1.content_hash.startswith("sha256:")


def test_load_prompts_content_hash_changes_on_edit(tmp_path):
    (tmp_path / "p.md").write_text("---\nversion = \"1.0.0\"\n---\nOriginal body.", encoding="utf-8")
    h1 = load_prompts_from_dir(tmp_path)[0].content_hash
    (tmp_path / "p.md").write_text("---\nversion = \"1.0.0\"\n---\nDifferent body.", encoding="utf-8")
    h2 = load_prompts_from_dir(tmp_path)[0].content_hash
    assert h1 != h2  # Content drift detected even though declared version unchanged


def test_load_prompts_missing_frontmatter(tmp_path):
    (tmp_path / "broken.md").write_text("No frontmatter here.", encoding="utf-8")
    with pytest.raises(PackPromptLoadError, match="frontmatter"):
        load_prompts_from_dir(tmp_path)


def test_load_prompts_missing_version(tmp_path):
    (tmp_path / "p.md").write_text("---\nname = \"x\"\n---\nBody.", encoding="utf-8")
    with pytest.raises(PackPromptLoadError, match="version"):
        load_prompts_from_dir(tmp_path)


def test_load_prompts_malformed_toml(tmp_path):
    (tmp_path / "p.md").write_text("---\nthis is not = valid = toml\n---\nBody.", encoding="utf-8")
    with pytest.raises(PackPromptLoadError, match="TOML"):
        load_prompts_from_dir(tmp_path)


def test_load_prompts_directory_missing(tmp_path):
    with pytest.raises(PackPromptLoadError, match="does not exist"):
        load_prompts_from_dir(tmp_path / "nope")


def test_pack_prompt_from_body():
    p = PackPrompt.from_body(name="x", version="1.0.0", body="hello")
    assert p.content_hash.startswith("sha256:")
    assert p.body == "hello"


# ---------------------------------------------------- pack loading basics


def _fresh_runtime():
    return Runtime(Graph())


def test_load_pack_emits_pack_loaded_event():
    @behavior(name="ping", on=["goal.created"])
    def ping(event, graph, ctx):
        pass

    pack = Pack(name="demo", version="0.1.0",
                behaviors=[ping], settings_schema=EmptySettings)
    rt = _fresh_runtime()
    rt.load_pack(pack)
    evts = [e for e in rt.graph.events if e.type == "pack.loaded"]
    assert len(evts) == 1
    assert evts[0].payload["name"] == "demo"
    assert evts[0].payload["version"] == "0.1.0"


def test_load_pack_idempotent_on_name_version():
    @behavior(name="ping", on=["goal.created"])
    def ping(event, graph, ctx):
        pass

    pack = Pack(name="demo", version="0.1.0", behaviors=[ping],
                settings_schema=EmptySettings)
    rt = _fresh_runtime()
    assert rt.load_pack(pack) is True
    assert rt.load_pack(pack) is False  # idempotent
    evts = [e for e in rt.graph.events if e.type == "pack.loaded"]
    assert len(evts) == 1  # only ONE pack.loaded


def test_load_pack_version_conflict():
    pack_v1 = Pack(name="demo", version="0.1.0", settings_schema=EmptySettings)
    pack_v2 = Pack(name="demo", version="0.2.0", settings_schema=EmptySettings)
    rt = _fresh_runtime()
    rt.load_pack(pack_v1)
    with pytest.raises(PackVersionConflictError):
        rt.load_pack(pack_v2)


def test_load_pack_conflict_on_object_type():
    pack_a = Pack(name="a", version="0.1.0",
                  object_types=[ObjectType(name="widget", schema=_Widget)],
                  settings_schema=EmptySettings)
    pack_b = Pack(name="b", version="0.1.0",
                  object_types=[ObjectType(name="widget", schema=_Widget)],
                  settings_schema=EmptySettings)
    rt = _fresh_runtime()
    rt.load_pack(pack_a)
    with pytest.raises(PackConflictError, match="widget"):
        rt.load_pack(pack_b)


def test_load_pack_conflict_is_premutation():
    """A failed load_pack must leave the runtime exactly as it was."""
    pack_a = Pack(name="a", version="0.1.0",
                  object_types=[ObjectType(name="widget", schema=_Widget)],
                  settings_schema=EmptySettings)
    pack_b = Pack(name="b", version="0.1.0",
                  object_types=[ObjectType(name="widget", schema=_Widget)],
                  settings_schema=EmptySettings)
    rt = _fresh_runtime()
    rt.load_pack(pack_a)
    events_before = len(rt.graph.events)
    with pytest.raises(PackConflictError):
        rt.load_pack(pack_b)
    # No pack.loaded event for pack b; runtime is unchanged.
    assert len(rt.graph.events) == events_before
    assert len(rt.loaded_packs()) == 1
    assert rt.loaded_packs()[0].name == "a"


# ---------------------------------------------------- settings


class _Settings1(BaseModel):
    n: int = 1


class _Settings2(BaseModel):
    required: str  # No default


def test_settings_inferred_default():
    pack = Pack(name="p1", version="0.1.0", settings_schema=_Settings1)
    rt = _fresh_runtime()
    rt.load_pack(pack)  # no settings= → defaults are used


def test_settings_required_raises():
    pack = Pack(name="p2", version="0.1.0", settings_schema=_Settings2)
    rt = _fresh_runtime()
    with pytest.raises(PackSettingsMissingError):
        rt.load_pack(pack)


def test_settings_dict_coercion():
    pack = Pack(name="p3", version="0.1.0", settings_schema=_Settings1)
    rt = _fresh_runtime()
    rt.load_pack(pack, settings={"n": 42})


# ---------------------------------------------------- schema validation


def test_schema_validation_rejects_malformed():
    pack = Pack(name="p4", version="0.1.0",
                object_types=[ObjectType(name="widget", schema=_Widget)],
                settings_schema=EmptySettings)
    rt = _fresh_runtime()
    rt.load_pack(pack)
    # Valid creation works.
    rt.graph.add_object("widget", {"name": "ok", "size": 1})
    # Invalid creation raises.
    with pytest.raises(PackSchemaViolation):
        rt.graph.add_object("widget", {"name": "neg", "size": -1})


def test_schema_validation_load_order_asymmetric():
    """Objects created BEFORE the pack loads are NOT retroactively
    validated (CONTRACT v0.9 #5).
    """
    rt = _fresh_runtime()
    rt.graph.add_object("widget", {"name": "pre", "size": -999})  # untyped, fine
    pack = Pack(name="p5", version="0.1.0",
                object_types=[ObjectType(name="widget", schema=_Widget)],
                settings_schema=EmptySettings)
    rt.load_pack(pack)
    # The pre-existing object is still there with its untyped data.
    pre = next(o for o in rt.graph.all_objects() if o.data.get("name") == "pre")
    assert pre.data["size"] == -999
    # Post-load creation is validated.
    with pytest.raises(PackSchemaViolation):
        rt.graph.add_object("widget", {"name": "post", "size": -1})


def test_schema_validation_does_not_apply_without_pack():
    """In a no-pack runtime, add_object accepts arbitrary data (v0.8
    semantics, backward compat).
    """
    rt = _fresh_runtime()
    rt.graph.add_object("anything", {"foo": "bar", "size": -42})  # no validation


# ---------------------------------------------------- namespace prefixing


def test_behavior_canonical_name_is_prefixed():
    @behavior(name="ping", on=["goal.created"])
    def ping(event, graph, ctx):
        pass

    pack = Pack(name="myp", version="0.1.0", behaviors=[ping],
                settings_schema=EmptySettings)
    rt = _fresh_runtime()
    rt.load_pack(pack)
    b = rt.get_behavior("myp.ping")
    assert b.name == "myp.ping"


def test_behavior_short_name_lookup_when_unambiguous():
    @behavior(name="ping", on=["goal.created"])
    def ping(event, graph, ctx):
        pass

    pack = Pack(name="myp", version="0.1.0", behaviors=[ping],
                settings_schema=EmptySettings)
    rt = _fresh_runtime()
    rt.load_pack(pack)
    b = rt.get_behavior("ping")
    assert b.name == "myp.ping"


def test_behavior_short_name_lookup_ambiguous():
    @behavior(name="ping", on=["goal.created"])
    def ping1(event, graph, ctx):
        pass

    @behavior(name="ping", on=["goal.created"])
    def ping2(event, graph, ctx):
        pass

    pack_a = Pack(name="a", version="0.1.0", behaviors=[ping1],
                  settings_schema=EmptySettings)
    pack_b = Pack(name="b", version="0.1.0", behaviors=[ping2],
                  settings_schema=EmptySettings)
    rt = _fresh_runtime()
    rt.load_pack(pack_a)
    rt.load_pack(pack_b)  # not a conflict — short names alone don't conflict
    with pytest.raises(ValueError, match="ambiguous"):
        rt.get_behavior("ping")
    # Fully qualified always works.
    assert rt.get_behavior("a.ping").name == "a.ping"
    assert rt.get_behavior("b.ping").name == "b.ping"


# ---------------------------------------------------- runtime execution


def test_pack_behavior_runs_with_typed_settings_injection():
    """Form 1 (typed parameter injection)."""

    class _S(BaseModel):
        marker: str = "hello"

    captured = {}

    @behavior(name="b", on=["object.created"])
    def b(event, graph, ctx, *, settings: _S):
        captured["marker"] = settings.marker

    pack = Pack(name="injtest", version="0.1.0", behaviors=[b],
                settings_schema=_S)
    rt = _fresh_runtime()
    rt.load_pack(pack, settings=_S(marker="bingo"))
    rt.graph.add_object("trigger", {"foo": 1})
    rt.run_until_idle()
    assert captured["marker"] == "bingo"


def test_pack_behavior_runs_with_ctx_settings():
    """Form 2 (ctx.settings)."""

    class _S(BaseModel):
        marker: str = "default"

    captured = {}

    @behavior(name="b", on=["object.created"])
    def b(event, graph, ctx):
        captured["marker"] = ctx.settings.marker

    pack = Pack(name="ctxtest", version="0.1.0", behaviors=[b],
                settings_schema=_S)
    rt = _fresh_runtime()
    rt.load_pack(pack, settings=_S(marker="ctx_via"))
    rt.graph.add_object("trigger", {"foo": 1})
    rt.run_until_idle()
    assert captured["marker"] == "ctx_via"


def test_pack_settings_cross_pack_lookup():
    """Form 3 (ctx.pack_settings)."""

    class _A(BaseModel):
        a: int = 1

    class _B(BaseModel):
        b: str = "x"

    pack_a = Pack(name="a", version="0.1.0", settings_schema=_A)
    pack_b = Pack(name="b", version="0.1.0", settings_schema=_B)

    captured = {}

    @behavior(name="probe", on=["object.created"])
    def probe(event, graph, ctx):
        captured["a"] = ctx.pack_settings("a").a
        captured["b"] = ctx.pack_settings("b").b
        captured["missing"] = ctx.pack_settings("nonexistent")

    pack_probe = Pack(name="probe_pack", version="0.1.0", behaviors=[probe],
                      settings_schema=EmptySettings)
    rt = _fresh_runtime()
    rt.load_pack(pack_a, settings=_A(a=42))
    rt.load_pack(pack_b, settings=_B(b="hello"))
    rt.load_pack(pack_probe)
    rt.graph.add_object("trigger", {})
    rt.run_until_idle()
    assert captured["a"] == 42
    assert captured["b"] == "hello"
    assert captured["missing"] is None
