"""Registration-time handler-signature validation (v1.3).

A wrong-arity handler used to register fine and fail at first
invocation with a TypeError swallowed into a `behavior.failed` event.
Decoration now validates the positional calling convention, mirroring
the CONTRACT v1.0.3 #2 precedent (`output_schema=` strict validation
at the @llm_behavior line).

Covers the global decorators and the pack-scoped variants, plus the
permissive escape hatches: `*args`, extras with defaults, and the
pack settings-injection pattern (annotated extras).
"""

import importlib
import inspect

import pytest
from pydantic import BaseModel

from activegraph import behavior, llm_behavior, relation_behavior, tool
from activegraph import packs as pack_api


class _Settings(BaseModel):
    threshold: float = 0.5


# ---------------------------------------------------------------- @tool


def test_tool_accepts_two_positional_params():
    @tool(name="ok")
    def ok(args, ctx):
        return {}

    assert ok.name == "ok"


def test_tool_rejects_one_positional_param():
    with pytest.raises(TypeError, match=r"@tool.*must accept 2 positional"):

        @tool(name="bad")
        def bad(ctx):
            return {}


def test_tool_rejects_extra_required_param():
    with pytest.raises(TypeError, match=r"beyond the \(args, ctx\) contract"):

        @tool(name="bad")
        def bad(args, ctx, extra):
            return {}


def test_tool_allows_extra_param_with_default():
    @tool(name="ok")
    def ok(args, ctx, extra=None):
        return {}

    assert ok.name == "ok"


def test_tool_annotation_alone_does_not_excuse_extras():
    # Tools have no settings injection: annotated-but-required extras
    # still fail at call time, so they fail at decoration time too.
    with pytest.raises(TypeError, match=r"beyond the \(args, ctx\) contract"):

        @tool(name="bad")
        def bad(args, ctx, *, settings: _Settings):
            return {}


def test_tool_allows_var_positional():
    @tool(name="ok")
    def ok(*call_args):
        return {}

    assert ok.name == "ok"


# ------------------------------------------------------------ @behavior


def test_behavior_accepts_three_positional_params():
    @behavior(name="ok", on=["goal.created"])
    def ok(event, graph, ctx):
        pass

    assert ok.name == "ok"


def test_behavior_rejects_two_positional_params():
    with pytest.raises(TypeError, match=r"@behavior.*must accept 3 positional"):

        @behavior(name="bad", on=["goal.created"])
        def bad(event, ctx):
            pass


def test_behavior_allows_annotated_keyword_only_extra():
    # The pack settings-injection pattern: keyword-only, annotated,
    # no default. The loader injects it; decoration must not reject it.
    @behavior(name="ok", on=["goal.created"])
    def ok(event, graph, ctx, *, settings: _Settings):
        pass

    assert ok.name == "ok"


def test_behavior_rejects_bare_keyword_only_extra():
    with pytest.raises(TypeError, match=r"beyond the .* contract"):

        @behavior(name="bad", on=["goal.created"])
        def bad(event, graph, ctx, *, mystery):
            pass


def test_behavior_allows_var_keyword():
    @behavior(name="ok", on=["goal.created"])
    def ok(event, graph, ctx, **extras):
        pass

    assert ok.name == "ok"


# --------------------------------------------------- @relation_behavior


def test_relation_behavior_requires_four_positional_params():
    with pytest.raises(
        TypeError, match=r"@relation_behavior.*must accept 4 positional"
    ):

        @relation_behavior("blocks", on=["object.created"])
        def bad(event, graph, ctx):
            pass


def test_relation_behavior_accepts_four_positional_params():
    @relation_behavior("blocks", on=["object.created"], name="ok")
    def ok(relation, event, graph, ctx):
        pass

    assert ok.name == "ok"


# -------------------------------------------------------- @llm_behavior


def test_llm_behavior_requires_four_positional_params():
    with pytest.raises(
        TypeError, match=r"@llm_behavior.*must accept 4 positional"
    ):

        @llm_behavior(name="bad", on=["goal.created"], model="claude-sonnet-4-5")
        def bad(event, graph, ctx):
            pass


def test_llm_behavior_accepts_four_positional_params():
    @llm_behavior(name="ok", on=["goal.created"], model="claude-sonnet-4-5")
    def ok(event, graph, ctx, out):
        pass

    assert ok.name == "ok"


# ----------------------------------------------- pack-scoped decorators


def test_pack_behavior_validates_and_allows_settings_injection():
    @pack_api.behavior(name="ok", on=["goal.created"])
    def ok(event, graph, ctx, *, settings: _Settings):
        pass

    assert ok.name == "ok"

    with pytest.raises(TypeError, match=r"@behavior.*must accept 3 positional"):

        @pack_api.behavior(name="bad", on=["goal.created"])
        def bad(ctx):
            pass


def test_pack_llm_behavior_validates():
    with pytest.raises(
        TypeError, match=r"@llm_behavior.*must accept 4 positional"
    ):

        @pack_api.llm_behavior(name="bad", on=["goal.created"])
        def bad(event, graph, ctx):
            pass


def test_pack_relation_behavior_validates():
    with pytest.raises(
        TypeError, match=r"@relation_behavior.*must accept 4 positional"
    ):

        @pack_api.relation_behavior("blocks", on=["object.created"])
        def bad(event, graph, ctx):
            pass


def test_pack_tool_validates():
    with pytest.raises(TypeError, match=r"@tool.*must accept 2 positional"):

        @pack_api.tool(name="bad")
        def bad(ctx):
            return {}


# ------------------------------------------------------ escape hatches


def test_uninspectable_callable_passes_through():
    from activegraph._signature import validate_handler_signature

    # Builtins often have no inspectable signature; validation skips
    # rather than guessing.
    validate_handler_signature(
        print,
        expected_params=("event", "graph", "ctx"),
        decorator="@behavior",
        allow_annotated_extras=True,
    )


def test_non_callable_is_rejected():
    from activegraph._signature import validate_handler_signature

    with pytest.raises(TypeError, match="must decorate a callable"):
        validate_handler_signature(
            42,
            expected_params=("event", "graph", "ctx"),
            decorator="@behavior",
            allow_annotated_extras=True,
        )


# ----------------------------------------- shared behavior construction


@pytest.mark.parametrize(
    ("global_decorator", "pack_decorator"),
    [
        (behavior, pack_api.behavior),
        (llm_behavior, pack_api.llm_behavior),
        (relation_behavior, pack_api.relation_behavior),
    ],
)
def test_behavior_decorator_common_signatures_match(
    global_decorator, pack_decorator
) -> None:
    global_params = inspect.signature(global_decorator).parameters
    pack_params = inspect.signature(pack_decorator).parameters

    assert tuple(global_params) == tuple(pack_params)
    for name, parameter in global_params.items():
        peer = pack_params[name]
        assert parameter.kind == peer.kind
        assert parameter.default == peer.default
        assert parameter.annotation == peer.annotation


def test_pack_llm_omitted_model_and_schema_validation_match_global() -> None:
    @pack_api.llm_behavior(name="pack-llm", on=["goal.created"])
    def pack_llm(event, graph, ctx, out):
        pass

    assert pack_llm.model is None

    errors: list[str] = []
    for decorator in (llm_behavior, pack_api.llm_behavior):
        with pytest.raises(TypeError) as exc_info:
            decorator(output_schema={"type": "object"})
        errors.append(str(exc_info.value))
    assert errors[0] == errors[1]


def test_llm_schema_validation_precedes_bad_handler_on_both_paths() -> None:
    for decorator in (llm_behavior, pack_api.llm_behavior):
        with pytest.raises(TypeError, match="output_schema must be"):
            binder = decorator(output_schema="not-a-schema")

            @binder
            def bad_handler(event):
                pass


def test_all_six_behavior_decorators_delegate_to_shared_builders(
    monkeypatch,
) -> None:
    factory = importlib.import_module("activegraph.behaviors._factory")
    global_module = importlib.import_module("activegraph.behaviors.decorators")
    pack_module = importlib.import_module("activegraph.packs")
    outer_calls: list[tuple[str, tuple, dict]] = []
    bind_calls: list[tuple[str, object]] = []

    def plain_builder(*args, **kwargs):
        outer_calls.append(("plain", args, kwargs))

        def bind(fn):
            from activegraph.behaviors.base import Behavior

            bind_calls.append(("plain", fn))
            return Behavior(name=kwargs.get("name") or fn.__name__, fn=fn)

        return bind

    def llm_builder(*args, **kwargs):
        outer_calls.append(("llm", args, kwargs))

        def bind(fn):
            from activegraph.behaviors.base import LLMBehavior

            bind_calls.append(("llm", fn))
            return LLMBehavior(
                name=kwargs.get("name") or fn.__name__, fn=lambda *a: None, handler=fn
            )

        return bind

    def relation_builder(*args, **kwargs):
        outer_calls.append(("relation", args, kwargs))

        def bind(fn):
            from activegraph.behaviors.base import RelationBehavior

            bind_calls.append(("relation", fn))
            return RelationBehavior(
                name=kwargs.get("name") or fn.__name__,
                fn=fn,
                relation_type=args[0],
            )

        return bind

    monkeypatch.setattr(factory, "build_behavior", plain_builder)
    monkeypatch.setattr(factory, "build_llm_behavior", llm_builder)
    monkeypatch.setattr(factory, "build_relation_behavior", relation_builder)

    def plain(event, graph, ctx):
        pass

    def llm(event, graph, ctx, out):
        pass

    def relation(rel, event, graph, ctx):
        pass

    global_module.behavior(name="global-plain")(plain)
    pack_module.behavior(name="pack-plain")(plain)
    global_module.llm_behavior(name="global-llm")(llm)
    pack_module.llm_behavior(name="pack-llm")(llm)
    global_module.relation_behavior("edge", name="global-relation")(relation)
    pack_module.relation_behavior("edge", name="pack-relation")(relation)

    assert global_module.behavior_factory is factory
    assert pack_module.behavior_factory is factory
    assert [kind for kind, _, _ in outer_calls] == [
        "plain",
        "plain",
        "llm",
        "llm",
        "relation",
        "relation",
    ]
    assert [kind for kind, _ in bind_calls] == [
        "plain",
        "plain",
        "llm",
        "llm",
        "relation",
        "relation",
    ]


def test_global_llm_validates_before_append_and_failure_is_atomic(
    monkeypatch,
) -> None:
    decorators = importlib.import_module("activegraph.behaviors.decorators")
    live = importlib.import_module("activegraph.runtime._live")
    order: list[str] = []

    class SpyRegistry(list):
        def append(self, value):
            order.append("append")
            super().append(value)

    registry = SpyRegistry()
    monkeypatch.setattr(decorators, "_REGISTRY", registry)
    monkeypatch.setattr(
        live,
        "validate_behavior_against_live_runtimes",
        lambda behavior: order.append("validate"),
    )

    @decorators.llm_behavior(name="ordered")
    def ordered(event, graph, ctx, out):
        pass

    assert order == ["validate", "append"]
    assert registry == [ordered]

    order.clear()
    registry.clear()

    def reject(behavior):
        order.append("validate")
        raise RuntimeError("rejected")

    monkeypatch.setattr(live, "validate_behavior_against_live_runtimes", reject)
    with pytest.raises(RuntimeError, match="rejected"):

        @decorators.llm_behavior(name="rejected")
        def rejected(event, graph, ctx, out):
            pass

    assert order == ["validate"]
    assert registry == []


def test_pack_behavior_decorators_have_no_transient_global_effects(
    monkeypatch,
) -> None:
    decorators = importlib.import_module("activegraph.behaviors.decorators")
    live = importlib.import_module("activegraph.runtime._live")
    effects: list[str] = []

    class SpyRegistry(list):
        def append(self, value):
            effects.append("append")
            super().append(value)

    monkeypatch.setattr(decorators, "_REGISTRY", SpyRegistry())
    monkeypatch.setattr(
        live,
        "validate_behavior_against_live_runtimes",
        lambda behavior: effects.append("validate"),
    )

    @pack_api.behavior(name="plain")
    def plain(event, graph, ctx):
        pass

    @pack_api.llm_behavior(name="llm")
    def llm(event, graph, ctx, out):
        pass

    @pack_api.relation_behavior("edge", name="relation")
    def relation(rel, event, graph, ctx):
        pass

    assert effects == []
    assert getattr(plain, "_pack_local") is True
    assert getattr(llm, "_pack_local") is True
    assert getattr(relation, "_pack_local") is True


def test_llm_outer_validation_precedence_matches_across_namespaces() -> None:
    cases = [
        {
            "pattern": "(a:c) OR b",
            "activate_after": "not events",
            "output_schema": "not-a-schema",
        },
        {
            "activate_after": "not events",
            "output_schema": "not-a-schema",
        },
    ]
    for kwargs in cases:
        errors: list[tuple[type[Exception], str]] = []
        for decorator in (llm_behavior, pack_api.llm_behavior):
            with pytest.raises(Exception) as exc_info:
                decorator(**kwargs)
            errors.append((type(exc_info.value), str(exc_info.value)))
        assert errors[0] == errors[1]


# ----------------------------------------- input_schema inference (v1.3)


class _FetchArgs(BaseModel):
    q: str


def test_tool_infers_input_schema_from_annotation():
    @tool(name="fetch")
    def fetch(args: _FetchArgs, ctx):
        return {}

    assert fetch.input_schema is _FetchArgs
    # The model sees real parameters, not an empty schema.
    definition = fetch.to_definition()
    assert definition["input_schema"]["properties"] == {"q": {"title": "Q", "type": "string"}}
    assert definition["input_schema"]["required"] == ["q"]


def test_tool_explicit_input_schema_wins_over_annotation():
    class _Other(BaseModel):
        z: int

    @tool(name="fetch", input_schema=_Other)
    def fetch(args: _FetchArgs, ctx):
        return {}

    assert fetch.input_schema is _Other


def test_tool_without_annotation_keeps_empty_schema():
    @tool(name="fetch")
    def fetch(args, ctx):
        return {}

    assert fetch.input_schema is None
    definition = fetch.to_definition()
    assert definition["input_schema"] == {"type": "object", "properties": {}}


def test_tool_non_model_annotation_is_ignored():
    @tool(name="fetch")
    def fetch(args: dict, ctx):
        return {}

    assert fetch.input_schema is None


def test_pack_tool_infers_input_schema_from_annotation():
    @pack_api.tool(name="fetch")
    def fetch(args: _FetchArgs, ctx):
        return {}

    assert fetch.input_schema is _FetchArgs


def test_tool_inference_resolves_string_annotations():
    # This module has `from __future__ import annotations`? It does
    # not — simulate PEP 563 with an explicit string annotation.
    @tool(name="fetch")
    def fetch(args: "_FetchArgs", ctx):
        return {}

    assert fetch.input_schema is _FetchArgs
