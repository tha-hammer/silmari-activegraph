"""Fixture-based tool invokers for tests. CONTRACT v0.7 #15.

Mirrors `activegraph.llm.recorded`. Fixtures live at
`tests/fixtures/tools/<tool_name>/<args_hash>.json`. Same
`recorded_at`-outside-the-hash pattern as v0.6's LLM fixtures.

  RecordedToolProvider   — wraps an inner invocation pipeline. On
                           `invoke(tool, args, ctx)`, computes the
                           args hash, reads the fixture, returns the
                           cached response. Missing fixtures raise
                           ToolError(reason="tool.fixture_missing").

  RecordingToolProvider  — wraps a real invoker. Calls the real
                           thing, persists the response as a
                           fixture, returns it. Use once under a
                           `@pytest.mark.records_tools` opt-in to
                           seed fixtures; commit; run thereafter
                           against `RecordedToolProvider`.

Fixture file shape:

    {
      "tool":        "web_fetch",
      "args_hash":   "<sha256_hex>",
      "recorded_at": "2026-05-15T10:32:01Z",
      "args":        { ... only this contributes to the hash ... },
      "output":      { ... },
      "error":       null | { "reason": "tool.network_error", "message": "..." },
      "latency_seconds": 0.8,
      "cost_usd":    "0.001"
    }

The "invoker" abstraction matters: tools register as Python
callables, but the runtime's tool-dispatch path can be wrapped by
either Recorded or Recording — the registered tool function is the
inner-most callable, and the Recording wrapper intercepts and
fingerprints. This is exactly the same pattern as
RecordingLLMProvider wrapping AnthropicProvider.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from activegraph.tools.base import Tool
from activegraph.tools.cache import CachedToolResponse, hash_tool_call
from activegraph.tools.context import ToolContext
from activegraph.tools.errors import ToolError


def _now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _normalize_args(tool: Tool, args: Any) -> Any:
    """Normalize tool arguments by delegating to canonicalize_args.

    The tool parameter is currently unused; canonicalize_args handles all cases.
    """
    from activegraph.tools.cache import canonicalize_args

    return canonicalize_args(args)


class RecordedToolProvider:
    """Read-only invoker. Tests use this so they never call out."""

    def __init__(self, fixtures_dir: str) -> None:
        self._dir = fixtures_dir

    def invoke(
        self,
        tool: Tool,
        args: Any,
        ctx: ToolContext,
    ) -> CachedToolResponse:
        args_hash = hash_tool_call(tool_name=tool.name, args=args)
        path = os.path.join(self._dir, tool.name, f"{args_hash}.json")
        if not os.path.exists(path):
            raise ToolError(
                "tool.fixture_missing",
                f"no recorded fixture for tool={tool.name!r} "
                f"args_hash={args_hash} in {self._dir}",
                payload_extras={
                    "tool": tool.name,
                    "args_hash": args_hash,
                    "fixtures_dir": self._dir,
                },
            )
        with open(path, "r") as f:
            data = json.load(f)
        return CachedToolResponse(
            output=data.get("output"),
            error=data.get("error"),
            latency_seconds=float(data.get("latency_seconds", 0.0) or 0.0),
            cost_usd=_decimal(data.get("cost_usd", "0")),
        )


class RecordingToolProvider:
    """Wraps an inner invoker and persists each response as a fixture.

    The inner invoker is normally the runtime's direct-call dispatcher
    (i.e. it just runs `tool.fn(args, ctx)` with the right validation).
    For seeding fixtures from a live tool body, use this:

        invoker = RecordingToolProvider(
            inner=DirectToolInvoker(),   # runs tool.fn(args, ctx)
            fixtures_dir="tests/fixtures/tools",
        )
    """

    def __init__(self, inner, fixtures_dir: str) -> None:
        self._inner = inner
        self._dir = fixtures_dir
        os.makedirs(self._dir, exist_ok=True)

    def invoke(
        self,
        tool: Tool,
        args: Any,
        ctx: ToolContext,
    ) -> CachedToolResponse:
        response = self._inner.invoke(tool, args, ctx)
        args_hash = hash_tool_call(tool_name=tool.name, args=args)
        fixture_dir = os.path.join(self._dir, tool.name)
        os.makedirs(fixture_dir, exist_ok=True)
        path = os.path.join(fixture_dir, f"{args_hash}.json")
        fixture = {
            "tool": tool.name,
            "args_hash": args_hash,
            "recorded_at": _now_iso(),
            "args": _normalize_args(tool, args),
            "output": response.output,
            "error": response.error,
            "latency_seconds": response.latency_seconds,
            "cost_usd": str(response.cost_usd),
        }
        with open(path, "w") as f:
            json.dump(fixture, f, indent=2, sort_keys=True)
        return response


class DirectToolInvoker:
    """The default invoker: just calls `tool.fn(args, ctx)` with timing
    and exception trapping. The runtime uses this when no provider
    wrapper is in play (i.e. production).
    """

    def invoke(
        self,
        tool: Tool,
        args: Any,
        ctx: ToolContext,
    ) -> CachedToolResponse:
        import time

        # CONTRACT v0.7 #6: timeout and execution_error are mapped.
        # Other failure modes (invalid_input/output) are checked by the
        # runtime, NOT here — schema validation happens before/after.
        t0 = time.monotonic()
        try:
            result = tool.fn(args, ctx)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(
                "tool.execution_error",
                f"{type(e).__name__}: {e}",
                payload_extras={
                    "tool": tool.name,
                    "exception_type": type(e).__name__,
                },
            ) from e
        latency = time.monotonic() - t0
        # Output can be a Pydantic instance or a dict. The runtime
        # validates after; here we just store what the tool returned.
        dump = getattr(result, "model_dump", None)
        output: Any
        if callable(dump):
            try:
                output = dump(mode="json")
            except TypeError:
                output = dump()
        else:
            output = result
        return CachedToolResponse(
            output=output,
            error=None,
            latency_seconds=latency,
            cost_usd=tool.cost_per_call,
        )


def _decimal(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))
