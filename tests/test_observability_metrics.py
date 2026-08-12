"""Metrics protocol + standard metric table — CONTRACT v0.8 #8–#10."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from activegraph import (
    Event,
    Graph,
    OverflowPolicy,
    Runtime,
    SinkConfig,
    behavior,
    clear_registry,
)
from activegraph.observability.metrics import (
    METRIC_BY_NAME,
    METRIC_NAMES,
    Metrics,
    NoOpMetrics,
    validate_cardinality_rule,
)


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
        assert "ValueError" in tags["reason"]

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
        assert any(n == "activegraph_queue_depth" for n, _, _ in m.gauges)


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


def _drive_plain_failure(metrics: RecordingMetrics, _tmp_path: Path) -> None:
    @behavior(name="metric_failure", on=["goal.created"])
    def metric_failure(event, graph, ctx):
        raise ValueError("metric failure")

    Runtime(Graph(), behaviors=[metric_failure], metrics=metrics).run_goal("metrics")


def _check_plain_failure(metrics: RecordingMetrics) -> None:
    assert metrics.values(
        "counter",
        "activegraph_behaviors_failed_total",
        {"behavior": "metric_failure", "reason": "exception.ValueError"},
    ) == [1.0]


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
        "plain_failure",
        frozenset({"activegraph_behaviors_failed_total"}),
        _drive_plain_failure,
        _check_plain_failure,
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


# Removed slice-by-slice as later Phase 2 behaviors add executable cases.
EXPECTED_METRIC_PRODUCTION_GAPS = frozenset(
    {
        "activegraph_llm_calls_total",
        "activegraph_llm_cache_hits_total",
        "activegraph_llm_failed_total",
        "activegraph_llm_tokens_in",
        "activegraph_llm_tokens_out",
        "activegraph_llm_cost_usd",
        "activegraph_tools_calls_total",
        "activegraph_tools_cache_hits_total",
        "activegraph_tools_failed_total",
        "activegraph_tools_duration_seconds",
        "activegraph_budget_cost_remaining_usd",
        "activegraph_budget_events_remaining",
        "activegraph_patterns_evaluated_total",
        "activegraph_patterns_evaluation_duration_seconds",
        "activegraph_replay_divergence_detected_total",
    }
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
    assert proved.isdisjoint(EXPECTED_METRIC_PRODUCTION_GAPS)
    assert proved | EXPECTED_METRIC_PRODUCTION_GAPS == set(METRIC_BY_NAME), json.dumps(
        catalog_to_cases, sort_keys=True, indent=2
    )


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
