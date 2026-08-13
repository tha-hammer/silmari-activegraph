"""Metrics protocol + NoOp default. CONTRACT v0.8 #8–#10.

Three methods. No timers (use a histogram with a latency value), no
summaries (Prometheus-specific), no custom types. Adding a metric is
a public API change — the standard metric list below is the operator
contract.

Cardinality rule (locked, CONTRACT v0.8 #C4):

  run_id MAY appear as a tag on gauges of active state (cardinality
  is bounded by the number of concurrently active runs).
  run_id MUST NOT appear as a tag on counters or histograms.

The ``METRIC_NAMES`` table enforces this at import time — any standard
metric whose tag set violates the rule fails the test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


# ---- bounded labels derived from open event payloads ---------------------

# These are metric-only values. Event payloads and diagnostic logs keep their
# original model/tool/reason strings; only metric tags pass through this closed
# normalization boundary.
METRIC_UNKNOWN_MODEL = "unknown_model"
METRIC_UNKNOWN_TOOL = "unknown_tool"
METRIC_UNKNOWN_REASON = "unknown_reason"
METRIC_LLM_OTHER_REASON = "llm.other"
METRIC_TOOL_OTHER_REASON = "tool.other"
METRIC_BUDGET_OTHER_REASON = "budget.other"
METRIC_EXCEPTION_OTHER_REASON = "exception.other"
METRIC_OTHER_REASON = "other"

LLM_METRIC_REASONS = frozenset(
    {
        "llm.parse_error",
        "llm.schema_violation",
        "llm.fixture_missing",
        "llm.rate_limited",
        "llm.network_error",
        "llm.auth_error",
        "llm.request_error",
    }
)

TOOL_METRIC_REASONS = frozenset(
    {
        "tool.timeout",
        "tool.network_error",
        "tool.invalid_input",
        "tool.invalid_output",
        "tool.execution_error",
        "tool.unknown_tool",
        "tool.fixture_missing",
        "tool.max_turns_exhausted",
        "tool.unrecorded_external_io",
        "budget.tool_calls_exhausted",
        "budget.cost_exhausted",
    }
)

REPLAY_METRIC_REASONS = frozenset(
    {
        "prompt_hash_mismatch",
        "embedding_hash_mismatch",
        "type_mismatch",
        "length_mismatch",
    }
)

BEHAVIOR_METRIC_REASONS = frozenset(
    {
        *LLM_METRIC_REASONS,
        *TOOL_METRIC_REASONS,
        "llm.prompt_assembly_error",
        "budget.exhausted",
        "budget.events_exhausted",
        "budget.behavior_calls_exhausted",
        "budget.llm_calls_exhausted",
        "budget.tool_calls_exhausted",
        "budget.patches_exhausted",
        "budget.depth_exhausted",
        "budget.seconds_exhausted",
        "budget.cost_exhausted",
    }
)


def normalize_metric_model(value: object) -> str:
    """Return an event model string or the stable metric-only fallback."""

    return value if isinstance(value, str) else METRIC_UNKNOWN_MODEL


def normalize_metric_tool(value: object) -> str:
    """Return an event tool string or the stable metric-only fallback."""

    return value if isinstance(value, str) else METRIC_UNKNOWN_TOOL


def normalize_llm_metric_reason(value: object) -> str:
    """Bound an open LLM event reason to the documented metric labels."""

    if not isinstance(value, str):
        return METRIC_UNKNOWN_REASON
    return value if value in LLM_METRIC_REASONS else METRIC_LLM_OTHER_REASON


def normalize_tool_metric_reason(value: object) -> str:
    """Bound an open tool event reason to the documented metric labels."""

    if not isinstance(value, str):
        return METRIC_UNKNOWN_REASON
    return value if value in TOOL_METRIC_REASONS else METRIC_TOOL_OTHER_REASON


def normalize_behavior_metric_reason(value: object) -> str:
    """Bound behavior failure reasons without changing diagnostic payloads."""

    if not isinstance(value, str):
        return METRIC_UNKNOWN_REASON
    if value in BEHAVIOR_METRIC_REASONS:
        return value
    if value.startswith("llm."):
        return METRIC_LLM_OTHER_REASON
    if value.startswith("tool."):
        return METRIC_TOOL_OTHER_REASON
    if value.startswith("budget."):
        return METRIC_BUDGET_OTHER_REASON
    if value.startswith("exception."):
        return METRIC_EXCEPTION_OTHER_REASON
    return METRIC_OTHER_REASON


def normalize_replay_metric_reason(value: object) -> str:
    """Bound strict replay divergence kinds to the closed public set."""

    if not isinstance(value, str):
        return METRIC_UNKNOWN_REASON
    return value if value in REPLAY_METRIC_REASONS else METRIC_OTHER_REASON


# ---- the protocol --------------------------------------------------------


@runtime_checkable
class Metrics(Protocol):
    """Three methods, all best-effort, all non-throwing.

    Implementations MUST tolerate unknown metric names. Unknown tag keys
    are also accepted; cardinality discipline is the caller's job. Methods
    may be called concurrently by independent runtime and sink workers.
    """

    def counter(self, name: str, tags: dict[str, str], value: float = 1.0) -> None: ...
    def histogram(self, name: str, tags: dict[str, str], value: float) -> None: ...
    def gauge(self, name: str, tags: dict[str, str], value: float) -> None: ...


# ---- the no-op default ---------------------------------------------------


class NoOpMetrics:
    """Default Metrics implementation. Does nothing.

    Three method bodies, each a single ``return``. The runtime is fully
    functional with NoOpMetrics. Profile-checked for zero allocation
    pressure under steady load.
    """

    __slots__ = ()

    def counter(self, name: str, tags: dict[str, str], value: float = 1.0) -> None:
        return

    def histogram(self, name: str, tags: dict[str, str], value: float) -> None:
        return

    def gauge(self, name: str, tags: dict[str, str], value: float) -> None:
        return


# ---- the documented metric table ----------------------------------------

# Type tags: c=counter, h=histogram, g=gauge.
# Source of truth for the standard metric list. Conformance tests pin
# this table; adding/removing a row is a public API change.


@dataclass(frozen=True)
class MetricSpec:
    name: str
    kind: str  # "counter" | "histogram" | "gauge"
    tags: tuple[str, ...]
    description: str


METRIC_NAMES: tuple[MetricSpec, ...] = (
    MetricSpec(
        "activegraph_events_emitted_total",
        "counter",
        ("event_type",),
        "Every event that lands in the graph's event log.",
    ),
    MetricSpec(
        "activegraph_behaviors_invoked_total",
        "counter",
        ("behavior",),
        "Each behavior invocation. Increments before the handler runs.",
    ),
    MetricSpec(
        "activegraph_behaviors_failed_total",
        "counter",
        ("behavior", "reason"),
        "Behavior invocations that produced a behavior.failed event.",
    ),
    MetricSpec(
        "activegraph_behaviors_duration_seconds",
        "histogram",
        ("behavior",),
        "Wall-clock duration of a behavior invocation (handler only).",
    ),
    MetricSpec(
        "activegraph_llm_calls_total",
        "counter",
        ("model",),
        "Every llm.requested event (cached and non-cached).",
    ),
    MetricSpec(
        "activegraph_llm_cache_hits_total",
        "counter",
        ("model",),
        "LLM calls served from the recorded-response cache.",
    ),
    MetricSpec(
        "activegraph_llm_failed_total",
        "counter",
        ("model", "reason"),
        "LLM responses whose error field is a mapping, by bounded reason.",
    ),
    MetricSpec(
        "activegraph_llm_tokens_in",
        "histogram",
        ("model",),
        "Nonnegative input tokens on successful llm.responded events.",
    ),
    MetricSpec(
        "activegraph_llm_tokens_out",
        "histogram",
        ("model",),
        "Nonnegative output tokens on successful llm.responded events.",
    ),
    MetricSpec(
        "activegraph_llm_cost_usd",
        "histogram",
        ("model",),
        "Valid successful response cost; logical cache hits record zero.",
    ),
    MetricSpec(
        "activegraph_tools_calls_total",
        "counter",
        ("tool",),
        "Every tool.requested event (cached and non-cached).",
    ),
    MetricSpec(
        "activegraph_tools_cache_hits_total",
        "counter",
        ("tool",),
        "Tool calls served from the recorded-response cache.",
    ),
    MetricSpec(
        "activegraph_tools_failed_total",
        "counter",
        ("tool", "reason"),
        "Tool responses whose error field is a mapping, by bounded reason.",
    ),
    MetricSpec(
        "activegraph_tools_duration_seconds",
        "histogram",
        ("tool",),
        "Response latency; cache hits and explicit early errors record zero.",
    ),
    MetricSpec(
        "activegraph_queue_depth",
        "gauge",
        (),
        "Most recently publishing runtime's local main-queue depth.",
    ),
    MetricSpec(
        "activegraph_sink_queue_depth",
        "gauge",
        ("sink", "run_id"),
        "Waiting deliveries in one attached sink's bounded queue.",
    ),
    MetricSpec(
        "activegraph_sink_events_delivered_total",
        "counter",
        ("sink",),
        "Events successfully handled by an attached sink.",
    ),
    MetricSpec(
        "activegraph_sink_events_dropped_total",
        "counter",
        ("sink", "reason"),
        "Sink deliveries rejected or evicted under declared policy.",
    ),
    MetricSpec(
        "activegraph_sink_errors_total",
        "counter",
        ("sink", "operation"),
        "Adapter lifecycle or on_event failures isolated by sink workers.",
    ),
    MetricSpec(
        "activegraph_budget_cost_remaining_usd",
        "gauge",
        ("run_id",),
        "Finite cost remaining after successful Runtime-owned observations.",
    ),
    MetricSpec(
        "activegraph_budget_events_remaining",
        "gauge",
        ("run_id",),
        "Finite event capacity after successful Runtime-owned observations.",
    ),
    MetricSpec(
        "activegraph_patterns_evaluated_total",
        "counter",
        (),
        "Every actual matcher call, including empty and raised evaluations.",
    ),
    MetricSpec(
        "activegraph_patterns_evaluation_duration_seconds",
        "histogram",
        (),
        "Duration of every actual pattern matcher call.",
    ),
    MetricSpec(
        "activegraph_replay_divergence_detected_total",
        "counter",
        ("reason",),
        "Each escaping strict replay divergence, by bounded exception kind.",
    ),
)

METRIC_BY_NAME: dict[str, MetricSpec] = {m.name: m for m in METRIC_NAMES}


def validate_cardinality_rule(metrics: tuple[MetricSpec, ...] = METRIC_NAMES) -> None:
    """Enforce CONTRACT v0.8 #C4: run_id only appears on gauges.

    Called from the conformance test. Raises if any counter or histogram
    declares run_id as a tag.
    """
    for spec in metrics:
        if "run_id" in spec.tags and spec.kind != "gauge":
            raise AssertionError(
                f"metric {spec.name!r} ({spec.kind}) lists run_id as a tag — "
                f"forbidden by the cardinality rule (run_id is gauge-only)."
            )


# Validate at import time so any in-tree edit fails loud.
validate_cardinality_rule()
