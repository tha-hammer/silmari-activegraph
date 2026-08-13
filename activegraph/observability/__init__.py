"""Operator-facing observability surface. v0.8.

Three pillars, all opt-in:

- Structured logging (``configure_logging``) — JSON-line logger setup
  matching the documented schema. Off by default; users with existing
  ``logging`` config keep theirs.
- Metrics (``Metrics`` protocol + ``NoOpMetrics`` + ``PrometheusMetrics`` +
  ``OpenTelemetryMetrics``) — three methods, fixed metric names. NoOp is
  the default.
- Runtime introspection (``RuntimeStatus``) — frozen snapshot returned
  by ``runtime.status()``.

The framework never auto-configures any of these. A library that does
is hostile to operators who already have their own config.
"""

from activegraph.observability.logging import (
    LOG_FIELDS,
    configure_logging,
    get_logger,
    runtime_log_extra,
)
from activegraph.observability.metrics import (
    METRIC_NAMES,
    Metrics,
    NoOpMetrics,
)
from activegraph.observability.migration import (
    BackendRegistration,
    CorruptMigrationEvent,
    MigrationBackend,
    MigrationBackendProvider,
    MigrationCapability,
    MigrationItem,
    MigrationReport,
    MigrationRunReport,
    clear_migration_backend_cache,
    migrate,
    register_migration_backend,
    resolve_migration_backend,
)
from activegraph.observability.otel import OpenTelemetryMetrics
from activegraph.observability.prometheus import PrometheusMetrics
from activegraph.observability.status import (
    BehaviorInfo,
    BudgetSnapshot,
    EventSummary,
    FrameSnapshot,
    RuntimeStatus,
    status_to_dict,
)

__all__ = [
    "BehaviorInfo",
    "BackendRegistration",
    "BudgetSnapshot",
    "EventSummary",
    "FrameSnapshot",
    "LOG_FIELDS",
    "METRIC_NAMES",
    "Metrics",
    "CorruptMigrationEvent",
    "MigrationBackend",
    "MigrationBackendProvider",
    "MigrationCapability",
    "MigrationItem",
    "MigrationReport",
    "MigrationRunReport",
    "NoOpMetrics",
    "OpenTelemetryMetrics",
    "PrometheusMetrics",
    "RuntimeStatus",
    "configure_logging",
    "clear_migration_backend_cache",
    "get_logger",
    "migrate",
    "register_migration_backend",
    "resolve_migration_backend",
    "runtime_log_extra",
    "status_to_dict",
]
