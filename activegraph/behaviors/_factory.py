"""Side-effect-free, two-stage construction for behavior decorators."""

from __future__ import annotations

from typing import Any, Callable, Optional

from activegraph._signature import validate_handler_signature
from activegraph.behaviors.base import (
    Behavior,
    LLMBehavior,
    RelationBehavior,
    _llm_behavior_fn_placeholder,
)
from activegraph.runtime.patterns import parse as parse_pattern
from activegraph.runtime.scheduler import parse_activate_after


BehaviorFn = Callable[..., None]
BehaviorBinder = Callable[[BehaviorFn], Behavior]
LLMBehaviorBinder = Callable[[BehaviorFn], LLMBehavior]
RelationBehaviorBinder = Callable[[BehaviorFn], RelationBehavior]


def _prepare_timing(
    pattern: Optional[str], activate_after: Any
) -> tuple[Any, Optional[int]]:
    matcher = parse_pattern(pattern).compile() if pattern is not None else None
    delay = (
        parse_activate_after(activate_after)
        if activate_after is not None
        else None
    )
    return matcher, delay


def _validate_output_schema(output_schema: Any) -> None:
    if output_schema is None:
        return
    try:
        from pydantic import BaseModel
    except ImportError:  # pragma: no cover - Pydantic is a runtime dependency
        return
    if isinstance(output_schema, type) and issubclass(output_schema, BaseModel):
        return
    passed = (
        type(output_schema).__name__
        if not isinstance(output_schema, type)
        else f"{output_schema.__name__} (a class, but not a BaseModel subclass)"
    )
    raise TypeError(
        f"output_schema must be a Pydantic BaseModel subclass, not "
        f"{passed}.\n"
        f"\n"
        f"Example:\n"
        f"    from pydantic import BaseModel\n"
        f"\n"
        f"    class MyOutput(BaseModel):\n"
        f"        result: str\n"
        f"\n"
        f"    @llm_behavior(output_schema=MyOutput)\n"
        f"    def my_behavior(event, graph, ctx, out): ...\n"
        f"\n"
        f"Dict-form output_schema (e.g., JSON Schema as a dict) is\n"
        f"filed as a v1.1 candidate. See CONTRACT v1.0.3 #2."
    )


def build_behavior(
    name: Optional[str] = None,
    on: Optional[list[str]] = None,
    where: Optional[dict[str, Any]] = None,
    view: Optional[dict[str, Any]] = None,
    creates: Optional[list[str]] = None,
    budget: Optional[dict[str, Any]] = None,
    priority: int = 0,
    *,
    pattern: Optional[str] = None,
    activate_after: Any = None,
) -> BehaviorBinder:
    matcher, delay = _prepare_timing(pattern, activate_after)

    def bind(fn: BehaviorFn) -> Behavior:
        validate_handler_signature(
            fn,
            expected_params=("event", "graph", "ctx"),
            decorator="@behavior",
            allow_annotated_extras=True,
        )
        return Behavior(
            name=name or fn.__name__,
            fn=fn,
            on=list(on or []),
            where=dict(where) if where else None,
            view_spec=dict(view) if view else None,
            creates=list(creates or []),
            budget=dict(budget) if budget else None,
            priority=priority,
            pattern=pattern,
            pattern_matcher=matcher,
            activate_after=delay,
        )

    return bind


def build_llm_behavior(
    *,
    name: Optional[str] = None,
    on: Optional[list[str]] = None,
    where: Optional[dict[str, Any]] = None,
    description: str = "",
    model: Optional[str] = None,
    output_schema: Optional[type] = None,
    view: Optional[dict[str, Any]] = None,
    creates: Optional[list[str]] = None,
    budget: Optional[dict[str, Any]] = None,
    deterministic: bool = False,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    top_p: float = 1.0,
    timeout_seconds: float = 60.0,
    prompt_template: Optional[str] = None,
    priority: int = 0,
    pattern: Optional[str] = None,
    activate_after: Any = None,
    tools: Optional[list[Any]] = None,
    max_tool_turns: int = 6,
) -> LLMBehaviorBinder:
    matcher, delay = _prepare_timing(pattern, activate_after)
    _validate_output_schema(output_schema)

    def bind(fn: BehaviorFn) -> LLMBehavior:
        validate_handler_signature(
            fn,
            expected_params=("event", "graph", "ctx", "llm_output"),
            decorator="@llm_behavior",
            allow_annotated_extras=True,
        )
        return LLMBehavior(
            name=name or fn.__name__,
            fn=_llm_behavior_fn_placeholder,
            on=list(on or []),
            where=dict(where) if where else None,
            view_spec=dict(view) if view else None,
            creates=list(creates or []),
            budget=dict(budget) if budget else None,
            priority=priority,
            handler=fn,
            description=description,
            model=model,
            output_schema=output_schema,
            deterministic=deterministic,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            timeout_seconds=timeout_seconds,
            prompt_template=prompt_template,
            pattern=pattern,
            pattern_matcher=matcher,
            activate_after=delay,
            tools=list(tools) if tools else [],
            max_tool_turns=max_tool_turns,
        )

    return bind


def build_relation_behavior(
    relation_type: str,
    on: Optional[list[str]] = None,
    name: Optional[str] = None,
    where: Optional[dict[str, Any]] = None,
    view: Optional[dict[str, Any]] = None,
    creates: Optional[list[str]] = None,
    budget: Optional[dict[str, Any]] = None,
    priority: int = 0,
    *,
    pattern: Optional[str] = None,
    activate_after: Any = None,
) -> RelationBehaviorBinder:
    matcher, delay = _prepare_timing(pattern, activate_after)

    def bind(fn: BehaviorFn) -> RelationBehavior:
        validate_handler_signature(
            fn,
            expected_params=("relation", "event", "graph", "ctx"),
            decorator="@relation_behavior",
            allow_annotated_extras=True,
        )
        return RelationBehavior(
            name=name or fn.__name__,
            fn=fn,
            relation_type=relation_type,
            on=list(on or []),
            where=dict(where) if where else None,
            view_spec=dict(view) if view else None,
            creates=list(creates or []),
            budget=dict(budget) if budget else None,
            priority=priority,
            pattern=pattern,
            pattern_matcher=matcher,
            activate_after=delay,
        )

    return bind
