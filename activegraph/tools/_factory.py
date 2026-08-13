"""Side-effect-free, two-stage construction for tool decorators."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable, Optional

from activegraph._signature import (
    infer_tool_input_schema,
    validate_handler_signature,
)
from activegraph.tools.base import Tool


ToolFn = Callable[..., Any]
ToolBinder = Callable[[ToolFn], Tool]


def build_tool(
    *,
    name: Optional[str] = None,
    description: str = "",
    input_schema: Optional[type] = None,
    output_schema: Optional[type] = None,
    cost_per_call: Any = Decimal("0"),
    timeout_seconds: float = 30.0,
    deterministic: bool = False,
) -> ToolBinder:
    cost = (
        cost_per_call
        if isinstance(cost_per_call, Decimal)
        else Decimal(str(cost_per_call))
    )

    def bind(fn: ToolFn) -> Tool:
        validate_handler_signature(
            fn,
            expected_params=("args", "ctx"),
            decorator="@tool",
            allow_annotated_extras=False,
        )
        resolved_input_schema = (
            input_schema
            if input_schema is not None
            else infer_tool_input_schema(fn)
        )
        return Tool(
            name=name or fn.__name__,
            fn=fn,
            description=description,
            input_schema=resolved_input_schema,
            output_schema=output_schema,
            cost_per_call=cost,
            timeout_seconds=float(timeout_seconds),
            deterministic=bool(deterministic),
        )

    return bind
