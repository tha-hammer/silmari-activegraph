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
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from activegraph import (
    Graph,
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
