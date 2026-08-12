"""Runtime-side configuration error leaves. v1.0 PR-F.

Configuration errors fire when the caller constructs or calls the
runtime with arguments that violate a static contract — wrong types,
conflicting kwargs, out-of-range values, operations that require a
specific backend that isn't attached, state that's already set and
can't be re-set. The errors are caller-actionable: the developer
either fixes the call or restructures their setup.

Four classes here cover the audit and runtime lifecycle:

- :class:`InvalidRuntimeConfiguration` — most ValueError shapes
  (conflicting args, missing required args, out-of-range values).
  Multi-inherits :class:`ValueError` for back-compat.
- :class:`InvalidArgumentType` — wrong-type values passed to
  constructors. Multi-inherits :class:`TypeError`.
- :class:`IncompatibleRuntimeState` — operations that require a
  specific runtime state (e.g., fork requires a SQLite-backed
  runtime; graph already has a store attached). Multi-inherits
  :class:`RuntimeError`.
- :class:`RuntimeClosedError` — a mutating operation was attempted after
  deterministic Runtime shutdown. It preserves the same RuntimeError lineage.

See CONTRACT v1.0 PR-F for the audit table mapping each migrated
raise site to its class.
"""

from __future__ import annotations

from typing import Any, Optional

from activegraph.errors import ConfigurationError


class InvalidRuntimeConfiguration(ConfigurationError, ValueError):
    """Caller-provided configuration is invalid (conflicting arguments,
    missing required argument, out-of-range value).

    Multi-inherits :class:`ValueError` so user code that catches the
    builtin around runtime construction or method calls keeps working.

    Construct with a one-line ``summary`` plus the three structured
    fields. The recovery prose is per-call-site, not table-driven —
    each configuration mistake has a different fix.
    """

    _doc_slug = "invalid-runtime-configuration"

    def __init__(
        self,
        summary: str,
        *,
        what_failed: str,
        why: str,
        how_to_fix: str,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        ConfigurationError.__init__(
            self,
            summary,
            what_failed=what_failed,
            why=why,
            how_to_fix=how_to_fix,
            context=context,
        )


class InvalidArgumentType(ConfigurationError, TypeError):
    """A value passed to a constructor or method has the wrong type.

    Multi-inherits :class:`TypeError`. Used when the framework's
    contract is type-based (e.g., :class:`PostgresEventStore` accepts
    a URL string, a psycopg.Connection, or a psycopg_pool.ConnectionPool —
    anything else is refused at construction).
    """

    _doc_slug = "invalid-argument-type"

    def __init__(
        self,
        summary: str,
        *,
        what_failed: str,
        why: str,
        how_to_fix: str,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        ConfigurationError.__init__(
            self,
            summary,
            what_failed=what_failed,
            why=why,
            how_to_fix=how_to_fix,
            context=context,
        )


class IncompatibleRuntimeState(ConfigurationError, RuntimeError):
    """An operation requires a runtime state that isn't satisfied —
    either a state that must be set but isn't, or a state that mustn't
    be set but is.

    Examples: ``runtime.fork()`` requires a SQLite-backed runtime;
    ``graph.attach_store()`` requires no existing store. Multi-inherits
    :class:`RuntimeError` for back-compat.
    """

    _doc_slug = "incompatible-runtime-state"

    def __init__(
        self,
        summary: str,
        *,
        what_failed: str,
        why: str,
        how_to_fix: str,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        ConfigurationError.__init__(
            self,
            summary,
            what_failed=what_failed,
            why=why,
            how_to_fix=how_to_fix,
            context=context,
        )


class RuntimeClosedError(IncompatibleRuntimeState):
    """A mutating Runtime operation was attempted after ``close()``.

    The class intentionally shares :class:`IncompatibleRuntimeState`'s
    documentation category while giving callers a precise catch target.
    """

    def __init__(self, operation: str) -> None:
        super().__init__(
            f"Runtime is closed; {operation} is unavailable",
            what_failed=(
                f"Runtime.{operation} was called after Runtime.close(). "
                "Closed runtimes reject state-changing operations."
            ),
            why=(
                "Runtime.close() is the deterministic sink-ownership boundary. "
                "Allowing later mutations would silently resume a resource whose "
                "outbound observers have already been detached and closed."
            ),
            how_to_fix=(
                "Create a new Runtime for further work. Read-only inspection "
                "methods, sink_statuses(), and explicit close retries remain "
                "available on this closed Runtime."
            ),
            context={"operation": operation},
        )
