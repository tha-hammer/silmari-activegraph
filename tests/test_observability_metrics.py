"""Metrics protocol + standard metric table — CONTRACT v0.8 #8–#10."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

import pytest
from pydantic import BaseModel

from activegraph import (
    Event,
    FrozenClock,
    Graph,
    InvalidRuntimeConfiguration,
    OverflowPolicy,
    ReplayDivergenceError,
    Runtime,
    SinkConfig,
    Tool,
    behavior,
    clear_registry,
    llm_behavior,
    relation_behavior,
)
from activegraph.llm import (
    ClaudeCodeProvider,
    LLMBehaviorError,
    LLMResponse,
    ToolCall,
)
from activegraph.observability.metrics import (
    LLM_METRIC_REASONS,
    METRIC_BY_NAME,
    METRIC_NAMES,
    METRIC_UNKNOWN_MODEL,
    METRIC_UNKNOWN_REASON,
    METRIC_UNKNOWN_TOOL,
    Metrics,
    NoOpMetrics,
    TOOL_METRIC_REASONS,
    normalize_llm_metric_reason,
    normalize_behavior_metric_reason,
    normalize_metric_model,
    normalize_metric_tool,
    normalize_replay_metric_reason,
    normalize_tool_metric_reason,
    validate_cardinality_rule,
)
from tests._llm_helpers import Claim, ClaimList, ScriptedProvider


class RecordingMetrics:
    """Test double — records every metric call so assertions are easy."""

    def __init__(self):
        self.counters: list[tuple[str, dict, float]] = []
        self.histograms: list[tuple[str, dict, float]] = []
        self.gauges: list[tuple[str, dict, float]] = []

    def counter(self, name, tags, value=1.0):
        self.counters.append((name, dict(tags), value))

    def histogram(self, name, tags, value):
        self.histograms.append((name, dict(tags), value))

    def gauge(self, name, tags, value):
        self.gauges.append((name, dict(tags), value))

    def observations(self):
        return [
            *(('counter', name, tags, value) for name, tags, value in self.counters),
            *(('histogram', name, tags, value) for name, tags, value in self.histograms),
            *(('gauge', name, tags, value) for name, tags, value in self.gauges),
        ]

    def values(self, kind, name, tags=None):
        return [
            value
            for observed_kind, observed_name, observed_tags, value in self.observations()
            if observed_kind == kind
            and observed_name == name
            and (tags is None or observed_tags == tags)
        ]

    def assert_standard_observations_match_catalog(self):
        for kind, name, tags, _value in self.observations():
            spec = METRIC_BY_NAME.get(name)
            assert spec is not None, f"unknown standard metric observation: {name}"
            assert kind == spec.kind, (
                f"{name} observed as {kind}, catalog declares {spec.kind}"
            )
            assert set(tags) == set(spec.tags), (
                f"{name} tags {sorted(tags)} != catalog tags {sorted(spec.tags)}"
            )


class TestProtocolShape:
    def test_noop_satisfies_protocol(self):
        m: Metrics = NoOpMetrics()
        m.counter("x", {})
        m.histogram("x", {}, 1.0)
        m.gauge("x", {}, 1.0)

    def test_noop_does_not_throw_on_unknown_names(self):
        m = NoOpMetrics()
        m.counter("never_registered", {"random": "tag"}, 42)


class TestStandardMetricTable:
    def test_cardinality_rule_passes_for_built_in_metrics(self):
        validate_cardinality_rule(METRIC_NAMES)

    def test_run_id_only_on_gauges(self):
        for spec in METRIC_NAMES:
            if "run_id" in spec.tags:
                assert spec.kind == "gauge", (
                    f"{spec.name} ({spec.kind}) lists run_id as a tag — "
                    "forbidden by CONTRACT v0.8 #C4."
                )

    def test_cardinality_rule_catches_violation(self):
        from activegraph.observability.metrics import MetricSpec

        bad = (
            MetricSpec("bad_counter_total", "counter", ("run_id",), "x"),
        )
        with pytest.raises(AssertionError, match="run_id"):
            validate_cardinality_rule(bad)

    def test_known_metric_names_present(self):
        # Anchor a few well-known names so accidental renames fail loud.
        for name in (
            "activegraph_events_emitted_total",
            "activegraph_behaviors_invoked_total",
            "activegraph_behaviors_failed_total",
            "activegraph_llm_calls_total",
            "activegraph_llm_cache_hits_total",
            "activegraph_tools_calls_total",
            "activegraph_tools_cache_hits_total",
            "activegraph_queue_depth",
            "activegraph_budget_cost_remaining_usd",
            "activegraph_replay_divergence_detected_total",
        ):
            assert name in METRIC_BY_NAME, f"missing standard metric: {name}"

    def test_counters_end_in_total(self):
        for spec in METRIC_NAMES:
            if spec.kind == "counter":
                assert spec.name.endswith("_total"), (
                    f"counter {spec.name} should end with _total per "
                    "Prometheus conventions."
                )

    def test_duration_histograms_end_in_seconds(self):
        for spec in METRIC_NAMES:
            if spec.kind == "histogram" and "duration" in spec.name:
                assert spec.name.endswith("_seconds"), (
                    f"duration histogram {spec.name} should end with _seconds."
                )

    def test_cost_histograms_end_in_usd(self):
        for spec in METRIC_NAMES:
            if spec.kind == "histogram" and "cost" in spec.name:
                assert spec.name.endswith("_usd"), (
                    f"cost histogram {spec.name} should end with _usd."
                )

    def test_llm_tool_metric_label_normalizers_are_closed(self):
        assert METRIC_UNKNOWN_MODEL == "unknown_model"
        assert METRIC_UNKNOWN_TOOL == "unknown_tool"
        assert METRIC_UNKNOWN_REASON == "unknown_reason"
        assert LLM_METRIC_REASONS == frozenset(
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
        assert TOOL_METRIC_REASONS == frozenset(
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
        assert normalize_metric_model(7) == "unknown_model"
        assert normalize_metric_tool(None) == "unknown_tool"
        assert normalize_llm_metric_reason(None) == "unknown_reason"
        assert normalize_llm_metric_reason("llm.user-controlled") == "llm.other"
        assert normalize_tool_metric_reason(7) == "unknown_reason"
        assert normalize_tool_metric_reason("tool.user-controlled") == "tool.other"

    def test_behavior_metric_reason_normalizer_is_closed(self):
        assert normalize_behavior_metric_reason("llm.parse_error") == "llm.parse_error"
        assert normalize_behavior_metric_reason("llm.user-controlled") == "llm.other"
        assert normalize_behavior_metric_reason("tool.invalid_input") == "tool.invalid_input"
        assert normalize_behavior_metric_reason("tool.user-controlled") == "tool.other"
        assert normalize_behavior_metric_reason("budget.events_exhausted") == (
            "budget.events_exhausted"
        )
        assert normalize_behavior_metric_reason("budget.user-controlled") == "budget.other"
        assert normalize_behavior_metric_reason("exception.ValueError") == "exception.other"
        assert normalize_behavior_metric_reason("user-controlled") == "other"
        assert normalize_behavior_metric_reason(None) == "unknown_reason"

    def test_replay_metric_reason_normalizer_is_closed(self):
        for reason in (
            "prompt_hash_mismatch",
            "embedding_hash_mismatch",
            "type_mismatch",
            "length_mismatch",
        ):
            assert normalize_replay_metric_reason(reason) == reason
        assert normalize_replay_metric_reason("future_kind") == "other"
        assert normalize_replay_metric_reason(None) == "unknown_reason"


class TestRuntimeEmitsExpectedMetrics:
    def test_events_emitted_total_fires(self):
        clear_registry()

        @behavior(name="p", on=["goal.created"])
        def p(event, graph, ctx):
            graph.add_object("task", {"x": 1})

        m = RecordingMetrics()
        g = Graph()
        rt = Runtime(g, metrics=m)
        rt.run_goal("test")
        names = [n for n, _, _ in m.counters]
        assert "activegraph_events_emitted_total" in names
        # Must carry the event_type tag
        for name, tags, _ in m.counters:
            if name == "activegraph_events_emitted_total":
                assert "event_type" in tags

    def test_behaviors_invoked_total_fires(self):
        clear_registry()

        @behavior(name="planner", on=["goal.created"])
        def planner(event, graph, ctx):
            pass

        m = RecordingMetrics()
        g = Graph()
        rt = Runtime(g, metrics=m)
        rt.run_goal("x")
        invoked = [t for n, t, _ in m.counters if n == "activegraph_behaviors_invoked_total"]
        assert len(invoked) == 1
        assert invoked[0] == {"behavior": "planner"}

    def test_behaviors_failed_total_fires_on_exception(self):
        clear_registry()

        @behavior(name="bad", on=["goal.created"])
        def bad(event, graph, ctx):
            raise ValueError("nope")

        m = RecordingMetrics()
        g = Graph()
        rt = Runtime(g, metrics=m)
        rt.run_goal("x")
        failed = [(t, v) for n, t, v in m.counters if n == "activegraph_behaviors_failed_total"]
        assert len(failed) == 1
        tags, _ = failed[0]
        assert tags["behavior"] == "bad"
        assert tags["reason"] == "exception.other"

    def test_behaviors_duration_histogram_fires(self):
        clear_registry()

        @behavior(name="p", on=["goal.created"])
        def p(event, graph, ctx):
            pass

        m = RecordingMetrics()
        g = Graph()
        rt = Runtime(g, metrics=m)
        rt.run_goal("x")
        names = [n for n, _, _ in m.histograms]
        assert "activegraph_behaviors_duration_seconds" in names

    def test_queue_depth_gauge_updates(self):
        clear_registry()

        @behavior(name="p", on=["goal.created"])
        def p(event, graph, ctx):
            graph.add_object("t", {"x": 1})

        m = RecordingMetrics()
        g = Graph()
        rt = Runtime(g, metrics=m)
        rt.run_goal("x")
        values = m.values("gauge", "activegraph_queue_depth")
        assert values
        assert values[-1] == float(rt.status().queue_depth) == 0.0


def test_finite_budget_direct_constructor_and_external_mutation_staleness() -> None:
    metrics = RecordingMetrics()
    runtime = Runtime(
        Graph(run_id="run_finite_direct"),
        behaviors=[],
        budget={"max_events": 3, "max_cost_usd": "1.25"},
        metrics=metrics,
    )

    event_tags = {"run_id": "run_finite_direct"}
    assert metrics.values(
        "gauge", "activegraph_budget_events_remaining", event_tags
    ) == [3.0]
    assert metrics.values(
        "gauge", "activegraph_budget_cost_remaining_usd", event_tags
    ) == [1.25]

    runtime.budget.consume("max_events")
    runtime.budget.add_cost(Decimal("0.25"))
    assert runtime.budget.used["max_events"] == 1.0
    assert runtime.budget.cost_used == Decimal("0.25")
    assert metrics.values(
        "gauge", "activegraph_budget_events_remaining", event_tags
    ) == [3.0]
    assert metrics.values(
        "gauge", "activegraph_budget_cost_remaining_usd", event_tags
    ) == [1.25]


def test_load_activates_metrics_after_recovery_and_tracks_only_continuation(
    tmp_path: Path,
) -> None:
    @behavior(name="metric_recovery_chain", on=["object.created"])
    def chain(event, graph, ctx):
        data = event.payload["object"]["data"]
        if data["ordinal"] == 0:
            graph.add_object("metric_unit", {"ordinal": 1})

    path = str(tmp_path / "metric-recovery.db")
    original = Runtime(
        Graph(run_id="run_metric_recovery", clock=FrozenClock()),
        behaviors=[chain],
        persist_to=path,
    )
    original.graph.add_object("metric_unit", {"ordinal": 0})
    original.run_quantum(max_queue_events=1)
    original.graph.store.close()

    metrics = RecordingMetrics()
    loaded = Runtime.load(
        path,
        run_id="run_metric_recovery",
        behaviors=[chain],
        budget={"max_events": 5, "max_cost_usd": "2.00"},
        metrics=metrics,
        sinks=[SinkConfig(_MetricRecordingSink(), name="metric_recovered")],
    )
    try:
        tags = {"run_id": "run_metric_recovery"}
        assert loaded.status().queue_depth == 1
        assert metrics.values("gauge", "activegraph_queue_depth") == [1.0]
        assert metrics.values(
            "gauge", "activegraph_budget_events_remaining", tags
        ) == [5.0]
        assert metrics.values(
            "gauge", "activegraph_budget_cost_remaining_usd", tags
        ) == [2.0]
        assert metrics.values("counter", "activegraph_events_emitted_total") == []

        loaded.run_until_idle()
        assert loaded.flush_sinks(timeout=2.0)
        assert loaded.status().queue_depth == 0
        assert metrics.values("gauge", "activegraph_queue_depth")[-1] == 0.0
        assert metrics.values(
            "gauge", "activegraph_budget_events_remaining", tags
        )[-1] == 4.0
        assert metrics.values(
            "counter",
            "activegraph_sink_events_delivered_total",
            {"sink": "metric_recovered"},
        )
    finally:
        loaded.close_sinks(timeout=2.0)
        loaded.graph.store.close()


def test_unlimited_fork_reports_recovered_queue_without_budget_gauges(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "metric-fork.db")
    parent = Runtime(
        Graph(run_id="run_metric_fork_parent", clock=FrozenClock()),
        behaviors=[],
        persist_to=path,
    )
    parent.graph.add_object("metric_seed", {})
    at_event = parent.graph.events[-1].id
    metrics = RecordingMetrics()
    fork = parent.fork(at_event=at_event, behaviors=[], metrics=metrics)
    try:
        assert fork.status().queue_depth == 1
        assert metrics.values("gauge", "activegraph_queue_depth") == [1.0]
        assert metrics.values("gauge", "activegraph_budget_events_remaining") == []
        assert metrics.values("gauge", "activegraph_budget_cost_remaining_usd") == []
        assert metrics.values("counter", "activegraph_events_emitted_total") == []
        fork.run_until_idle()
        assert fork.status().queue_depth == 0
        assert metrics.values("gauge", "activegraph_queue_depth")[-1] == 0.0
    finally:
        fork.graph.store.close()
        parent.graph.store.close()


def test_constructor_capability_failure_emits_no_gauges_and_leaves_no_listener() -> None:
    @llm_behavior(
        name="metric_capability_failure",
        on=["goal.created"],
        output_schema=ClaimList,
    )
    def extract(event, graph, ctx, output):
        return None

    metrics = RecordingMetrics()
    graph = Graph(run_id="run_metric_invalid")
    with pytest.raises(InvalidRuntimeConfiguration):
        Runtime(
            graph,
            behaviors=[extract],
            llm_provider=ClaudeCodeProvider(
                allow_unenforced_generation_controls=True
            ),
            budget={"max_events": 2, "max_cost_usd": "1.00"},
            metrics=metrics,
        )
    graph.add_object("after_failure", {})
    assert metrics.gauges == []
    assert metrics.counters == []


def test_strict_load_failure_emits_no_queue_or_budget_ghost_gauges(
    tmp_path: Path,
) -> None:
    toggle = {"first": True}

    @behavior(name="metric_strict_divergence", on=["goal.created"])
    def diverge(event, graph, ctx):
        graph.add_object("metric_a" if toggle["first"] else "metric_b", {})
        if toggle["first"]:
            graph.add_object("metric_a", {})

    path = str(tmp_path / "metric-strict.db")
    original = Runtime(
        Graph(run_id="run_metric_strict", clock=FrozenClock()),
        behaviors=[diverge],
        persist_to=path,
    )
    original.run_goal("strict")
    original.graph.store.close()
    toggle["first"] = False

    metrics = RecordingMetrics()
    with pytest.raises(ReplayDivergenceError):
        Runtime.load(
            path,
            run_id="run_metric_strict",
            behaviors=[diverge],
            replay_strict=True,
            budget={"max_events": 2, "max_cost_usd": "1.00"},
            metrics=metrics,
        )
    assert metrics.values("gauge", "activegraph_queue_depth") == []
    assert metrics.values("gauge", "activegraph_budget_events_remaining") == []
    assert metrics.values("gauge", "activegraph_budget_cost_remaining_usd") == []


def test_shared_backend_queue_gauge_is_last_writer_not_aggregate() -> None:
    metrics = RecordingMetrics()
    first_graph = Graph(run_id="run_metric_first")
    second_graph = Graph(run_id="run_metric_second")
    first = Runtime(first_graph, behaviors=[], metrics=metrics)
    second = Runtime(second_graph, behaviors=[], metrics=metrics)

    first_graph.add_object("metric", {"runtime": 1})
    second_graph.add_object("metric", {"runtime": 2, "n": 1})
    second_graph.add_object("metric", {"runtime": 2, "n": 2})
    first.run_quantum(max_queue_events=1)

    assert first.status().queue_depth == 0
    assert second.status().queue_depth == 2
    assert metrics.values("gauge", "activegraph_queue_depth")[-1] == 0.0


def test_llm_handler_behavior_metrics_cover_success_and_failure() -> None:
    def run_case(*, fails: bool) -> tuple[RecordingMetrics, Graph]:
        name = "metric_llm_handler_failure" if fails else "metric_llm_handler_success"

        @llm_behavior(name=name, on=["goal.created"], output_schema=ClaimList)
        def handler(event, graph, ctx, output):
            if fails:
                raise ValueError("developer handler failed")

        metrics = RecordingMetrics()
        graph = Graph()
        provider = ScriptedProvider(
            respond_fn=lambda messages, schema: ClaimList(claims=[]),
            default_model="metric-model",
        )
        Runtime(
            graph,
            behaviors=[handler],
            llm_provider=provider,
            metrics=metrics,
        ).run_goal("metrics")
        return metrics, graph

    success, _success_graph = run_case(fails=False)
    failure, failure_graph = run_case(fails=True)

    for metrics, name in (
        (success, "metric_llm_handler_success"),
        (failure, "metric_llm_handler_failure"),
    ):
        tags = {"behavior": name}
        assert metrics.values(
            "counter", "activegraph_behaviors_invoked_total", tags
        ) == [1.0]
        durations = metrics.values(
            "histogram", "activegraph_behaviors_duration_seconds", tags
        )
        assert len(durations) == 1 and durations[0] >= 0.0

    assert failure.values(
        "counter",
        "activegraph_behaviors_failed_total",
        {"behavior": "metric_llm_handler_failure", "reason": "exception.other"},
    ) == [1.0]
    failed_event = next(
        event for event in failure_graph.events if event.type == "behavior.failed"
    )
    assert failed_event.payload["exception_type"] == "ValueError"
    assert failed_event.payload.get("reason") is None


def _relation_metric_graph() -> tuple[Graph, str]:
    graph = Graph()
    source = graph.add_object("task", {"name": "source"})
    first = graph.add_object("task", {"name": "first"})
    second = graph.add_object("task", {"name": "second"})
    graph.add_relation(source.id, first.id, "depends_on")
    graph.add_relation(source.id, second.id, "depends_on")
    return graph, source.id


def test_relation_behavior_metrics_count_each_fanout_success_and_failure() -> None:
    def run_case(*, fails: bool) -> tuple[RecordingMetrics, Graph]:
        name = "metric_relation_failure" if fails else "metric_relation_success"

        @relation_behavior(
            name=name,
            relation_type="depends_on",
            on=["task.completed"],
        )
        def handler(relation, event, graph, ctx):
            if fails:
                raise LookupError("relation handler failed")

        graph, source_id = _relation_metric_graph()
        metrics = RecordingMetrics()
        runtime = Runtime(graph, behaviors=[handler], metrics=metrics)
        _emit_public_metric_event(
            graph,
            "task.completed",
            {"task_id": source_id},
        )
        runtime.run_until_idle()
        return metrics, graph

    success, _success_graph = run_case(fails=False)
    failure, failure_graph = run_case(fails=True)

    for metrics, name in (
        (success, "metric_relation_success"),
        (failure, "metric_relation_failure"),
    ):
        tags = {"behavior": name}
        assert metrics.values(
            "counter", "activegraph_behaviors_invoked_total", tags
        ) == [1.0, 1.0]
        durations = metrics.values(
            "histogram", "activegraph_behaviors_duration_seconds", tags
        )
        assert len(durations) == 2
        assert all(value >= 0.0 for value in durations)

    failed_tags = {
        "behavior": "metric_relation_failure",
        "reason": "exception.other",
    }
    assert failure.values(
        "counter", "activegraph_behaviors_failed_total", failed_tags
    ) == [1.0, 1.0]
    failed_events = [
        event for event in failure_graph.events if event.type == "behavior.failed"
    ]
    assert len(failed_events) == 2
    assert {event.payload["exception_type"] for event in failed_events} == {
        "LookupError"
    }


@dataclass(frozen=True)
class MetricProductionCase:
    id: str
    proves: frozenset[str]
    drive: Callable[[RecordingMetrics, Path], None]
    check: Callable[[RecordingMetrics], None]


def _drive_runtime_plain_queue_success(metrics: RecordingMetrics, _tmp_path: Path) -> None:
    @behavior(name="metric_plain", on=["goal.created"])
    def metric_plain(event, graph, ctx):
        return None

    Runtime(Graph(), behaviors=[metric_plain], metrics=metrics).run_goal("metrics")


def _check_runtime_plain_queue_success(metrics: RecordingMetrics) -> None:
    assert metrics.values(
        "counter", "activegraph_behaviors_invoked_total", {"behavior": "metric_plain"}
    ) == [1.0]
    durations = metrics.values(
        "histogram", "activegraph_behaviors_duration_seconds", {"behavior": "metric_plain"}
    )
    assert len(durations) == 1 and durations[0] >= 0
    assert metrics.values("gauge", "activegraph_queue_depth")


def _drive_finite_budget_metrics(metrics: RecordingMetrics, _tmp_path: Path) -> None:
    extract = _metric_llm_behavior("metric_finite_budget")
    provider = ScriptedProvider(
        respond_fn=lambda messages, schema: ClaimList(claims=[]),
        fixed_cost=Decimal("0.0012"),
        default_model="metric-model",
    )
    Runtime(
        Graph(run_id="run_metric_budget"),
        behaviors=[extract],
        llm_provider=provider,
        budget={"max_events": 2, "max_cost_usd": "0.01"},
        metrics=metrics,
    ).run_goal("metrics")


def _check_finite_budget_metrics(metrics: RecordingMetrics) -> None:
    tags = {"run_id": "run_metric_budget"}
    assert metrics.values(
        "gauge", "activegraph_budget_events_remaining", tags
    ) == [2.0, 1.0]
    assert metrics.values(
        "gauge", "activegraph_budget_cost_remaining_usd", tags
    ) == pytest.approx([0.01, 0.0088])


def _drive_plain_failure(metrics: RecordingMetrics, _tmp_path: Path) -> None:
    @behavior(name="metric_failure", on=["goal.created"])
    def metric_failure(event, graph, ctx):
        raise ValueError("metric failure")

    Runtime(Graph(), behaviors=[metric_failure], metrics=metrics).run_goal("metrics")


def _check_plain_failure(metrics: RecordingMetrics) -> None:
    assert metrics.values(
        "counter",
        "activegraph_behaviors_failed_total",
        {"behavior": "metric_failure", "reason": "exception.other"},
    ) == [1.0]


def _metric_llm_behavior(name: str):
    @llm_behavior(name=name, on=["goal.created"], output_schema=ClaimList)
    def extract(event, graph, ctx, output):
        return None

    return extract


def _drive_llm_live_success_and_cache(metrics: RecordingMetrics, tmp_path: Path) -> None:
    extract = _metric_llm_behavior("metric_llm_cache")
    provider = ScriptedProvider(
        respond_fn=lambda messages, schema: ClaimList(
            claims=[Claim(text="cached", confidence=1.0)]
        ),
        fixed_cost=Decimal("0.0012"),
        default_model="metric-model",
    )
    runtime = Runtime(
        Graph(),
        behaviors=[extract],
        llm_provider=provider,
        metrics=metrics,
        persist_to=str(tmp_path / "llm-metrics.db"),
    )
    runtime.run_goal("same prompt")
    goal = next(event for event in runtime.graph.events if event.type == "goal.created")
    cached_provider = ScriptedProvider(
        respond_fn=lambda messages, schema: ClaimList(claims=[]),
        default_model="metric-model",
    )
    fork = runtime.fork(
        at_event=goal.id,
        label="metric-cache",
        llm_provider=cached_provider,
        replay_llm_cache=True,
        metrics=metrics,
    )
    fork.run_until_idle()
    assert len(provider.call_log) == 1
    assert cached_provider.call_log == []


def _check_llm_live_success_and_cache(metrics: RecordingMetrics) -> None:
    tags = {"model": "metric-model"}
    assert metrics.values("counter", "activegraph_llm_calls_total", tags) == [1.0, 1.0]
    assert metrics.values("counter", "activegraph_llm_cache_hits_total", tags) == [1.0]
    assert metrics.values("histogram", "activegraph_llm_tokens_in", tags) == [42.0, 42.0]
    assert metrics.values("histogram", "activegraph_llm_tokens_out", tags) == [11.0, 11.0]
    assert metrics.values("histogram", "activegraph_llm_cost_usd", tags) == [0.0012, 0.0]


class _FailsOnceProvider:
    default_model = "metric-model"

    def __init__(self) -> None:
        self.calls = 0

    def recognizes_model(self, name: str) -> bool:
        return True

    def complete(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise LLMBehaviorError(
                "llm.network_error",
                "temporary",
                payload_extras={"retry_after_seconds": 0},
            )
        return LLMResponse(
            raw_text='{"claims": []}',
            parsed=ClaimList(claims=[]),
            input_tokens=7,
            output_tokens=3,
            cost_usd=Decimal("0.002"),
            latency_seconds=0.01,
            model=kwargs["model"],
            finish_reason="end_turn",
        )

    def estimate_cost(self, *, input_tokens, output_tokens, model):
        return Decimal("0.002")

    def count_tokens(self, *, system, messages, model):
        return 7


def _drive_llm_error_retry(metrics: RecordingMetrics, _tmp_path: Path) -> None:
    extract = _metric_llm_behavior("metric_llm_retry")
    provider = _FailsOnceProvider()
    Runtime(
        Graph(),
        behaviors=[extract],
        llm_provider=provider,
        llm_retry_max_attempts=2,
        llm_retry_initial_delay_seconds=0,
        metrics=metrics,
    ).run_goal("retry")
    assert provider.calls == 2


def _check_llm_error_retry(metrics: RecordingMetrics) -> None:
    tags = {"model": "metric-model"}
    assert metrics.values("counter", "activegraph_llm_calls_total", tags) == [1.0, 1.0]
    assert metrics.values(
        "counter",
        "activegraph_llm_failed_total",
        {"model": "metric-model", "reason": "llm.network_error"},
    ) == [1.0]
    assert metrics.values("histogram", "activegraph_llm_tokens_in", tags) == [7.0]
    assert metrics.values("histogram", "activegraph_llm_tokens_out", tags) == [3.0]
    assert metrics.values("histogram", "activegraph_llm_cost_usd", tags) == [0.002]


class _MetricToolInput(BaseModel):
    query: str


class _MetricToolOutput(BaseModel):
    answer: str


class _ToolLoopProvider:
    default_model = "metric-model"

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def recognizes_model(self, name: str) -> bool:
        return True

    def complete(self, **kwargs):
        self.calls += 1
        return self.responses.pop(0)

    def estimate_cost(self, *, input_tokens, output_tokens, model):
        return Decimal("0")

    def count_tokens(self, *, system, messages, model):
        return 1


def _tool_response(*, tool_calls=None, parsed=None) -> LLMResponse:
    return LLMResponse(
        raw_text="",
        parsed=parsed,
        input_tokens=2,
        output_tokens=1,
        cost_usd=Decimal("0"),
        latency_seconds=0.01,
        model="metric-model",
        finish_reason="tool_use" if tool_calls else "end_turn",
        tool_calls=tool_calls,
    )


def _metric_tool_behavior(name: str, metric_tool: Tool):
    @llm_behavior(
        name=name,
        on=["goal.created"],
        output_schema=ClaimList,
        tools=[metric_tool],
    )
    def use_tool(event, graph, ctx, output):
        return None

    return use_tool


def _drive_tool_live_success_and_cache(metrics: RecordingMetrics, tmp_path: Path) -> None:
    metric_tool = Tool(
        name="metric_tool",
        fn=lambda args, ctx: _MetricToolOutput(answer=args.query),
        input_schema=_MetricToolInput,
        output_schema=_MetricToolOutput,
        deterministic=True,
    )
    use_tool = _metric_tool_behavior("metric_tool_cache", metric_tool)
    provider = _ToolLoopProvider(
        [
            _tool_response(
                tool_calls=[
                    ToolCall(
                        id="metric-call",
                        name="metric_tool",
                        args={"query": "x"},
                    )
                ]
            ),
            _tool_response(parsed=ClaimList(claims=[])),
        ]
    )
    runtime = Runtime(
        Graph(),
        behaviors=[use_tool],
        tools=[metric_tool],
        llm_provider=provider,
        metrics=metrics,
        persist_to=str(tmp_path / "tool-metrics.db"),
    )
    runtime.run_goal("same tool prompt")
    goal = next(event for event in runtime.graph.events if event.type == "goal.created")
    cached_provider = _ToolLoopProvider([])
    fork = runtime.fork(
        at_event=goal.id,
        label="metric-tool-cache",
        llm_provider=cached_provider,
        replay_llm_cache=True,
        replay_tool_cache=True,
        tools=[metric_tool],
        metrics=metrics,
    )
    fork.run_until_idle()
    assert provider.calls == 2
    assert cached_provider.calls == 0


def _check_tool_live_success_and_cache(metrics: RecordingMetrics) -> None:
    tags = {"tool": "metric_tool"}
    assert metrics.values("counter", "activegraph_tools_calls_total", tags) == [1.0, 1.0]
    assert metrics.values("counter", "activegraph_tools_cache_hits_total", tags) == [1.0]
    durations = metrics.values("histogram", "activegraph_tools_duration_seconds", tags)
    assert len(durations) == 2
    assert durations[0] >= 0.0
    assert durations[1] == 0.0


def _one_tool_call_provider(tool_name: str, args: dict) -> _ToolLoopProvider:
    return _ToolLoopProvider(
        [
            _tool_response(
                tool_calls=[ToolCall(id="metric-error", name=tool_name, args=args)]
            )
        ]
    )


def _drive_tool_invalid_input_and_invoker_error(
    metrics: RecordingMetrics, _tmp_path: Path
) -> None:
    invalid_tool = Tool(
        name="invalid_metric_tool",
        fn=lambda args, ctx: _MetricToolOutput(answer=args.query),
        input_schema=_MetricToolInput,
        output_schema=_MetricToolOutput,
    )
    invalid_behavior = _metric_tool_behavior("metric_tool_invalid", invalid_tool)
    Runtime(
        Graph(),
        behaviors=[invalid_behavior],
        tools=[invalid_tool],
        llm_provider=_one_tool_call_provider("invalid_metric_tool", {"wrong": "x"}),
        metrics=metrics,
    ).run_goal("invalid")

    def explode(args, ctx):
        raise RuntimeError("tool exploded")

    failing_tool = Tool(
        name="failing_metric_tool",
        fn=explode,
        input_schema=_MetricToolInput,
        output_schema=_MetricToolOutput,
    )
    failing_behavior = _metric_tool_behavior("metric_tool_invoker", failing_tool)
    Runtime(
        Graph(),
        behaviors=[failing_behavior],
        tools=[failing_tool],
        llm_provider=_one_tool_call_provider("failing_metric_tool", {"query": "x"}),
        metrics=metrics,
    ).run_goal("failure")


def _check_tool_invalid_input_and_invoker_error(metrics: RecordingMetrics) -> None:
    for tool_name, reason in (
        ("invalid_metric_tool", "tool.invalid_input"),
        ("failing_metric_tool", "tool.execution_error"),
    ):
        tags = {"tool": tool_name}
        assert metrics.values("counter", "activegraph_tools_calls_total", tags) == [1.0]
        assert metrics.values(
            "counter",
            "activegraph_tools_failed_total",
            {"tool": tool_name, "reason": reason},
        ) == [1.0]
        assert metrics.values(
            "histogram", "activegraph_tools_duration_seconds", tags
        ) == [0.0]


def _drive_pattern_match_and_delayed_recheck(
    metrics: RecordingMetrics, _tmp_path: Path
) -> None:
    class MatchMatcher:
        def matches(self, event, graph):
            return [object()]

    @behavior(name="metric_delayed_pattern", on=["audit.metric"], activate_after=1)
    def metric_delayed_pattern(event, graph, ctx):
        return None

    metric_delayed_pattern.pattern_matcher = MatchMatcher()
    graph = Graph()
    runtime = Runtime(
        graph,
        behaviors=[metric_delayed_pattern],
        metrics=metrics,
    )
    for event_type in ("audit.metric", "advance.tick"):
        _emit_public_metric_event(graph, event_type, {})
    runtime.run_until_idle()


def _check_pattern_match_and_delayed_recheck(metrics: RecordingMetrics) -> None:
    assert metrics.values(
        "counter", "activegraph_patterns_evaluated_total", {}
    ) == [1.0, 1.0]
    durations = metrics.values(
        "histogram", "activegraph_patterns_evaluation_duration_seconds", {}
    )
    assert len(durations) == 2
    assert all(value >= 0.0 for value in durations)


def _drive_strict_replay_divergence(
    metrics: RecordingMetrics, tmp_path: Path
) -> None:
    toggle = {"extra": True}

    @behavior(name="metric_replay_divergence", on=["goal.created"])
    def diverge(event, graph, ctx):
        graph.add_object("metric_replay", {})
        if toggle["extra"]:
            graph.add_object("metric_replay", {})

    path = str(tmp_path / "metric-replay-divergence.db")
    original = Runtime(
        Graph(run_id="run_metric_divergence", clock=FrozenClock()),
        behaviors=[diverge],
        persist_to=path,
    )
    original.run_goal("metrics")
    original.graph.store.close()
    toggle["extra"] = False
    with pytest.raises(ReplayDivergenceError) as exc_info:
        Runtime.load(
            path,
            run_id="run_metric_divergence",
            behaviors=[diverge],
            replay_strict=True,
            metrics=metrics,
        )
    assert exc_info.value.kind == "length_mismatch"


def _check_strict_replay_divergence(metrics: RecordingMetrics) -> None:
    assert metrics.observations() == [
        (
            "counter",
            "activegraph_replay_divergence_detected_total",
            {"reason": "length_mismatch"},
            1.0,
        )
    ]


class _MetricRecordingSink:
    def open(self) -> None:
        return None

    def on_event(self, event, context) -> None:
        return None

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class _MetricRaisingSink(_MetricRecordingSink):
    def on_event(self, event, context) -> None:
        raise RuntimeError("metric sink failure")


class _MetricGateSink(_MetricRecordingSink):
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def on_event(self, event, context) -> None:
        self.entered.set()
        assert self.release.wait(timeout=2.0)

    def close(self) -> None:
        self.release.set()


def _metric_event(graph: Graph, n: int) -> Event:
    return Event(
        id=graph.ids.event(),
        type="metric.event",
        payload={"n": n},
        timestamp=graph.clock.now(),
    )


def _drive_sink_metrics(metrics: RecordingMetrics, _tmp_path: Path) -> None:
    delivered_graph = Graph()
    delivered = Runtime(
        delivered_graph,
        behaviors=[],
        metrics=metrics,
        sinks=[SinkConfig(_MetricRecordingSink(), name="metric_delivered")],
    )
    try:
        delivered_graph.emit(_metric_event(delivered_graph, 1))
        assert delivered.flush_sinks(timeout=2.0)
    finally:
        delivered.close_sinks(timeout=2.0)

    error_graph = Graph()
    error = Runtime(
        error_graph,
        behaviors=[],
        metrics=metrics,
        sinks=[SinkConfig(_MetricRaisingSink(), name="metric_error")],
    )
    try:
        error_graph.emit(_metric_event(error_graph, 1))
        assert error.flush_sinks(timeout=2.0)
    finally:
        error.close_sinks(timeout=2.0)

    gate = _MetricGateSink()
    drop_graph = Graph()
    dropped = Runtime(
        drop_graph,
        behaviors=[],
        metrics=metrics,
        sinks=[
            SinkConfig(
                gate,
                name="metric_drop",
                queue_capacity=1,
                overflow_policy=OverflowPolicy.DROP_NEWEST,
            )
        ],
    )
    try:
        drop_graph.emit(_metric_event(drop_graph, 1))
        assert gate.entered.wait(timeout=2.0)
        drop_graph.emit(_metric_event(drop_graph, 2))
        drop_graph.emit(_metric_event(drop_graph, 3))
        gate.release.set()
        assert dropped.flush_sinks(timeout=2.0)
    finally:
        gate.release.set()
        dropped.close_sinks(timeout=2.0)


def _check_sink_metrics(metrics: RecordingMetrics) -> None:
    assert metrics.values(
        "counter",
        "activegraph_sink_events_delivered_total",
        {"sink": "metric_delivered"},
    ) == [1.0]
    assert metrics.values(
        "counter",
        "activegraph_sink_errors_total",
        {"sink": "metric_error", "operation": "on_event"},
    ) == [1.0]
    assert metrics.values(
        "counter",
        "activegraph_sink_events_dropped_total",
        {"sink": "metric_drop", "reason": "overflow.drop_newest"},
    ) == [1.0]
    assert metrics.values("gauge", "activegraph_sink_queue_depth")


METRIC_PRODUCTION_CASES = (
    MetricProductionCase(
        "runtime_plain_queue_success",
        frozenset(
            {
                "activegraph_events_emitted_total",
                "activegraph_behaviors_invoked_total",
                "activegraph_behaviors_duration_seconds",
                "activegraph_queue_depth",
            }
        ),
        _drive_runtime_plain_queue_success,
        _check_runtime_plain_queue_success,
    ),
    MetricProductionCase(
        "finite_budget",
        frozenset(
            {
                "activegraph_budget_events_remaining",
                "activegraph_budget_cost_remaining_usd",
            }
        ),
        _drive_finite_budget_metrics,
        _check_finite_budget_metrics,
    ),
    MetricProductionCase(
        "plain_failure",
        frozenset({"activegraph_behaviors_failed_total"}),
        _drive_plain_failure,
        _check_plain_failure,
    ),
    MetricProductionCase(
        "llm_live_success_and_cache",
        frozenset(
            {
                "activegraph_llm_calls_total",
                "activegraph_llm_cache_hits_total",
                "activegraph_llm_tokens_in",
                "activegraph_llm_tokens_out",
                "activegraph_llm_cost_usd",
            }
        ),
        _drive_llm_live_success_and_cache,
        _check_llm_live_success_and_cache,
    ),
    MetricProductionCase(
        "llm_error_retry",
        frozenset({"activegraph_llm_failed_total"}),
        _drive_llm_error_retry,
        _check_llm_error_retry,
    ),
    MetricProductionCase(
        "tool_live_success_and_cache",
        frozenset(
            {
                "activegraph_tools_calls_total",
                "activegraph_tools_cache_hits_total",
                "activegraph_tools_duration_seconds",
            }
        ),
        _drive_tool_live_success_and_cache,
        _check_tool_live_success_and_cache,
    ),
    MetricProductionCase(
        "tool_invalid_input_and_invoker_error",
        frozenset({"activegraph_tools_failed_total"}),
        _drive_tool_invalid_input_and_invoker_error,
        _check_tool_invalid_input_and_invoker_error,
    ),
    MetricProductionCase(
        "pattern_match_and_delayed_recheck",
        frozenset(
            {
                "activegraph_patterns_evaluated_total",
                "activegraph_patterns_evaluation_duration_seconds",
            }
        ),
        _drive_pattern_match_and_delayed_recheck,
        _check_pattern_match_and_delayed_recheck,
    ),
    MetricProductionCase(
        "strict_replay_divergence",
        frozenset({"activegraph_replay_divergence_detected_total"}),
        _drive_strict_replay_divergence,
        _check_strict_replay_divergence,
    ),
    MetricProductionCase(
        "sink_deliver_drop_error_depth",
        frozenset(
            {
                "activegraph_sink_queue_depth",
                "activegraph_sink_events_delivered_total",
                "activegraph_sink_events_dropped_total",
                "activegraph_sink_errors_total",
            }
        ),
        _drive_sink_metrics,
        _check_sink_metrics,
    ),
)


@pytest.mark.parametrize("case", METRIC_PRODUCTION_CASES, ids=lambda case: case.id)
def test_metric_production_case_observes_every_declared_name(
    case: MetricProductionCase, tmp_path: Path
) -> None:
    metrics = RecordingMetrics()
    case.drive(metrics, tmp_path)
    metrics.assert_standard_observations_match_catalog()
    observed_names = {name for _kind, name, _tags, _value in metrics.observations()}
    assert case.proves <= observed_names
    case.check(metrics)


def test_metric_production_matrix_accounts_for_the_catalog() -> None:
    catalog_to_cases = {
        name: sorted(case.id for case in METRIC_PRODUCTION_CASES if name in case.proves)
        for name in sorted(METRIC_BY_NAME)
    }
    proved = {name for name, cases in catalog_to_cases.items() if cases}
    assert proved == set(METRIC_BY_NAME), json.dumps(
        catalog_to_cases, sort_keys=True, indent=2
    )


def _emit_public_metric_event(graph: Graph, type_: str, payload: dict) -> None:
    graph.emit(
        Event(
            id=graph.ids.event(),
            type=type_,
            payload=payload,
            timestamp=graph.clock.now(),
        )
    )


def test_public_mapper_uses_each_llm_event_own_model() -> None:
    """Public mapper robustness pins request/response label ownership."""

    metrics = RecordingMetrics()
    graph = Graph()
    Runtime(graph, behaviors=[], metrics=metrics)
    _emit_public_metric_event(
        graph,
        "llm.requested",
        {"model": "request-model", "cache_hit": False},
    )
    _emit_public_metric_event(
        graph,
        "llm.responded",
        {
            "model": "response-model",
            "error": None,
            "input_tokens": 1,
            "output_tokens": 2,
            "cost_usd": "0.25",
            "cache_hit": False,
        },
    )
    assert metrics.values(
        "counter", "activegraph_llm_calls_total", {"model": "request-model"}
    ) == [1.0]
    response_tags = {"model": "response-model"}
    assert metrics.values("histogram", "activegraph_llm_tokens_in", response_tags) == [
        1.0
    ]
    assert metrics.values(
        "histogram", "activegraph_llm_tokens_out", response_tags
    ) == [2.0]
    assert metrics.values("histogram", "activegraph_llm_cost_usd", response_tags) == [
        0.25
    ]


def test_public_llm_tool_event_mapper_is_robust_to_malformed_payloads() -> None:
    """Public Graph.emit robustness, not a provider-production-path proof."""

    metrics = RecordingMetrics()
    graph = Graph()
    Runtime(graph, behaviors=[], metrics=metrics)
    malformed_events = (
        ("llm.requested", {"cache_hit": 1}),
        ("tool.requested", {"tool": 7, "cache_hit": "true"}),
        (
            "llm.responded",
            {"error": {"reason": "llm.user-controlled"}, "cost_usd": "0"},
        ),
        (
            "tool.responded",
            {"error": {}, "latency_seconds": 0, "cache_hit": False},
        ),
        ("llm.responded", {"model": "m", "error": "not-a-mapping"}),
        ("tool.responded", {"tool": "t", "error": ["bad"]}),
        (
            "llm.responded",
            {
                "model": "bad-numbers",
                "error": None,
                "input_tokens": True,
                "output_tokens": -1,
                "cost_usd": "NaN",
            },
        ),
        (
            "llm.responded",
            {
                "model": "bad-token-shapes",
                "error": None,
                "input_tokens": 1.5,
                "output_tokens": "2",
                "cost_usd": -1,
            },
        ),
        (
            "llm.responded",
            {
                "model": "bad-cost-string",
                "error": None,
                "cost_usd": "not-a-decimal",
            },
        ),
        (
            "tool.responded",
            {
                "tool": "bad-duration",
                "error": None,
                "latency_seconds": float("inf"),
            },
        ),
    )
    for type_, payload in malformed_events:
        _emit_public_metric_event(graph, type_, payload)

    assert metrics.values(
        "counter", "activegraph_llm_calls_total", {"model": "unknown_model"}
    ) == [1.0]
    assert metrics.values(
        "counter", "activegraph_tools_calls_total", {"tool": "unknown_tool"}
    ) == [1.0]
    assert metrics.values("counter", "activegraph_llm_cache_hits_total") == []
    assert metrics.values("counter", "activegraph_tools_cache_hits_total") == []
    assert metrics.values(
        "counter",
        "activegraph_llm_failed_total",
        {"model": "unknown_model", "reason": "llm.other"},
    ) == [1.0]
    assert metrics.values(
        "counter",
        "activegraph_tools_failed_total",
        {"tool": "unknown_tool", "reason": "unknown_reason"},
    ) == [1.0]
    assert metrics.values("histogram", "activegraph_llm_tokens_in") == []
    assert metrics.values("histogram", "activegraph_llm_tokens_out") == []
    assert metrics.values("histogram", "activegraph_llm_cost_usd") == []
    assert metrics.values(
        "histogram",
        "activegraph_tools_duration_seconds",
        {"tool": "unknown_tool"},
    ) == [0.0]
    assert metrics.values(
        "histogram",
        "activegraph_tools_duration_seconds",
        {"tool": "bad-duration"},
    ) == []
    assert len(metrics.values("counter", "activegraph_events_emitted_total")) == len(
        malformed_events
    )
    metrics.assert_standard_observations_match_catalog()


class TestPrometheusMetricsOptional:
    """Prometheus is opt-in. If installed, basic emission works."""

    def test_available_flag(self):
        from activegraph.observability.prometheus import PrometheusMetrics

        # Whether prometheus_client is installed or not, this is a boolean.
        assert isinstance(PrometheusMetrics.available(), bool)

    def test_emit_with_prometheus(self):
        from activegraph.observability.prometheus import PrometheusMetrics

        if not PrometheusMetrics.available():
            pytest.skip("prometheus_client not installed")
        import prometheus_client

        registry = prometheus_client.CollectorRegistry()
        m = PrometheusMetrics(registry=registry)
        m.counter("activegraph_events_emitted_total", {"event_type": "goal.created"})
        m.histogram(
            "activegraph_behaviors_duration_seconds",
            {"behavior": "p"},
            0.01,
        )
        m.gauge("activegraph_queue_depth", {}, 2.0)
        # Roundtrip via the registry — names should be present.
        names = {metric.name for metric in registry.collect()}
        assert "activegraph_events_emitted" in names  # _total suffix dropped
        assert "activegraph_behaviors_duration_seconds" in names
        assert "activegraph_queue_depth" in names

    def test_concurrent_first_use_creates_one_instrument(self, monkeypatch):
        from activegraph.observability.prometheus import PrometheusMetrics

        if not PrometheusMetrics.available():
            pytest.skip("prometheus_client not installed")
        import prometheus_client

        registry = prometheus_client.CollectorRegistry()
        metrics = PrometheusMetrics(registry=registry)
        original_counter = metrics._client.Counter
        first_entered = threading.Event()
        second_entered = threading.Event()
        release = threading.Event()
        call_lock = threading.Lock()
        call_count = 0

        def gated_counter(*args, **kwargs):
            nonlocal call_count
            with call_lock:
                call_count += 1
                current = call_count
            if current == 1:
                first_entered.set()
                release.wait(timeout=2.0)
            else:
                second_entered.set()
            return original_counter(*args, **kwargs)

        monkeypatch.setattr(metrics._client, "Counter", gated_counter)
        start = threading.Barrier(3)

        def observe() -> None:
            start.wait()
            metrics.counter("activegraph_race_total", {"sink": "shared"})

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(observe) for _ in range(2)]
            start.wait()
            assert first_entered.wait(timeout=2.0)
            raced_into_constructor = second_entered.wait(timeout=0.05)
            release.set()
            for future in futures:
                future.result(timeout=2.0)

        assert raced_into_constructor is False
        assert call_count == 1


class TestOpenTelemetryMetricsOptional:
    """OpenTelemetry is opt-in. If installed, basic emission works."""

    def test_available_flag(self):
        from activegraph.observability.otel import OpenTelemetryMetrics

        assert isinstance(OpenTelemetryMetrics.available(), bool)

    def test_emit_with_opentelemetry_in_memory_reader(self):
        from activegraph.observability.otel import OpenTelemetryMetrics

        if not OpenTelemetryMetrics.available():
            pytest.skip("opentelemetry-api/opentelemetry-sdk not installed")

        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader

        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        meter = provider.get_meter("activegraph.test")
        m = OpenTelemetryMetrics(meter=meter)

        m.counter("activegraph_events_emitted_total", {"event_type": "goal.created"})
        m.histogram(
            "activegraph_behaviors_duration_seconds",
            {"behavior": "planner"},
            0.25,
        )
        m.gauge("activegraph_queue_depth", {}, 2.0)
        m.gauge("activegraph_queue_depth", {}, 5.0)

        metrics = _otel_metrics_by_name(reader.get_metrics_data())
        assert set(metrics) == {
            "activegraph_events_emitted_total",
            "activegraph_behaviors_duration_seconds",
            "activegraph_queue_depth",
        }

        event_dp = metrics["activegraph_events_emitted_total"].data.data_points[0]
        assert event_dp.attributes == {"event_type": "goal.created"}
        assert event_dp.value == 1.0

        duration_dp = metrics["activegraph_behaviors_duration_seconds"].data.data_points[0]
        assert duration_dp.attributes == {"behavior": "planner"}
        assert duration_dp.count == 1
        assert duration_dp.sum == 0.25

        queue_data = metrics["activegraph_queue_depth"].data
        assert queue_data.is_monotonic is False
        queue_dp = queue_data.data_points[0]
        assert queue_dp.attributes == {}
        assert queue_dp.value == 5.0

    def test_first_zero_gauge_exports_and_round_trip_returns_to_zero(self):
        from activegraph.observability.otel import OpenTelemetryMetrics

        if not OpenTelemetryMetrics.available():
            pytest.skip("opentelemetry-api/opentelemetry-sdk not installed")

        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader

        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        metrics = OpenTelemetryMetrics(
            meter=provider.get_meter("activegraph.zero-gauge")
        )

        metrics.gauge("activegraph_queue_depth", {}, 0.0)
        first = _otel_metrics_by_name(reader.get_metrics_data())
        assert first["activegraph_queue_depth"].data.data_points[0].attributes == {}
        assert first["activegraph_queue_depth"].data.data_points[0].value == 0.0

        metrics.gauge("activegraph_queue_depth", {}, 2.0)
        metrics.gauge("activegraph_queue_depth", {}, 0.0)
        final = _otel_metrics_by_name(reader.get_metrics_data())
        assert final["activegraph_queue_depth"].data.data_points[0].value == 0.0

    def test_concurrent_gauge_updates_serialize_previous_value(self):
        from activegraph.observability.otel import OpenTelemetryMetrics

        if not OpenTelemetryMetrics.available():
            pytest.skip("opentelemetry-api/opentelemetry-sdk not installed")

        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader

        class GatedValues(dict):
            def __init__(self):
                super().__init__()
                self.first_entered = threading.Event()
                self.second_entered = threading.Event()
                self.release = threading.Event()
                self.call_lock = threading.Lock()
                self.call_count = 0

            def get(self, key, default=None):
                with self.call_lock:
                    self.call_count += 1
                    current = self.call_count
                if current == 1:
                    self.first_entered.set()
                    self.release.wait(timeout=2.0)
                else:
                    self.second_entered.set()
                return super().get(key, default)

        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        metrics = OpenTelemetryMetrics(meter=provider.get_meter("activegraph.race"))
        values = GatedValues()
        metrics._gauge_values = values

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(metrics.gauge, "activegraph_race_depth", {}, 1.0)
            assert values.first_entered.wait(timeout=2.0)
            second = pool.submit(metrics.gauge, "activegraph_race_depth", {}, 2.0)
            raced_into_read = values.second_entered.wait(timeout=0.05)
            values.release.set()
            first.result(timeout=2.0)
            second.result(timeout=2.0)

        assert raced_into_read is False
        metric = _otel_metrics_by_name(reader.get_metrics_data())[
            "activegraph_race_depth"
        ]
        assert metric.data.data_points[0].value == 2.0


def _otel_metrics_by_name(metrics_data):
    out = {}
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                out[metric.name] = metric
    return out
