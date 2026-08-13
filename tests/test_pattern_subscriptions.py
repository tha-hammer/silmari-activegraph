"""Pattern subscription integration tests. CONTRACT v0.7 #11 / #12.

  - pattern + on= requires BOTH conditions to fire
  - pattern-only (no `on=`) fires on every non-lifecycle event when the
    pattern matches
  - lifecycle events (behavior.*, llm.*, tool.*, pattern.*, runtime.*)
    don't trigger pattern-only behaviors
  - ctx.matches is populated with bindings
  - registering with an invalid pattern fails loud at decoration time
"""

from __future__ import annotations

import pytest

from activegraph import (
    Event,
    Graph,
    Runtime,
    UnsupportedPatternError,
    behavior,
    clear_registry,
)
from activegraph.runtime.registry import Registry


class _PatternRecordingMetrics:
    def __init__(self) -> None:
        self.counters: list[tuple[str, dict, float]] = []
        self.histograms: list[tuple[str, dict, float]] = []

    def counter(self, name, tags, value=1.0):
        self.counters.append((name, dict(tags), value))

    def histogram(self, name, tags, value):
        self.histograms.append((name, dict(tags), value))

    def gauge(self, name, tags, value):
        return None

    def pattern_counts(self) -> list[float]:
        return [
            value
            for name, tags, value in self.counters
            if name == "activegraph_patterns_evaluated_total" and tags == {}
        ]

    def pattern_durations(self) -> list[float]:
        return [
            value
            for name, tags, value in self.histograms
            if name == "activegraph_patterns_evaluation_duration_seconds"
            and tags == {}
        ]


def test_pattern_and_event_type_both_required():
    """on= AND pattern= must both hold."""
    fired: list = []

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        c1 = graph.add_object("claim", {"text": "A", "confidence": 0.9})
        c2 = graph.add_object("claim", {"text": "B", "confidence": 0.9})
        graph.add_relation(c1.id, c2.id, "contradicts")

    @behavior(
        name="critic",
        on=["relation.created"],
        pattern="(c1:claim)-[r:contradicts]->(c2:claim) WHERE c1.confidence > 0.7",
    )
    def critic(event, graph, ctx):
        fired.append(len(ctx.matches))

    g = Graph()
    Runtime(g).run_goal("g")
    # critic should fire once: on the one contradicts relation.created event.
    # other relation.created events don't fit the pattern.
    assert fired == [1]


def test_pattern_only_fires_on_non_lifecycle_events():
    """No `on=`: pattern checks every non-lifecycle event."""
    fired: list = []

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        graph.add_object("claim", {"text": "A", "confidence": 0.9})

    @behavior(
        name="auditor",
        pattern="(c:claim) WHERE c.confidence > 0.7",
    )
    def auditor(event, graph, ctx):
        fired.append(event.type)

    g = Graph()
    Runtime(g).run_goal("g")
    # `auditor` should fire on goal.created (no matches yet, skipped),
    # then on object.created (matches now). Lifecycle events suppressed.
    assert fired == ["object.created"]


def test_ctx_matches_carries_bindings():
    captured: list = []

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        a = graph.add_object("claim", {"text": "A", "confidence": 0.9})
        b = graph.add_object("claim", {"text": "B", "confidence": 0.9})
        graph.add_relation(a.id, b.id, "contradicts")

    @behavior(
        name="critic",
        on=["relation.created"],
        where={"relation.type": "contradicts"},
        pattern="(c1:claim)-[r:contradicts]->(c2:claim)",
    )
    def critic(event, graph, ctx):
        captured.extend(ctx.matches)

    Runtime(Graph()).run_goal("g")
    assert len(captured) == 1
    assert "c1" in captured[0].bindings
    assert "c2" in captured[0].bindings
    assert "r" in captured[0].bindings


def test_invalid_pattern_fails_at_decoration():
    """A malformed pattern is caught at @behavior time."""
    with pytest.raises(UnsupportedPatternError):
        @behavior(
            name="x",
            on=["object.created"],
            pattern="(a:c) OR b",
        )
        def x(event, graph, ctx):
            pass


def test_pattern_matched_event_emitted_when_pattern_fires():
    fired: list = []

    @behavior(name="seed", on=["goal.created"])
    def seed(event, graph, ctx):
        a = graph.add_object("claim", {"text": "A", "confidence": 0.9})
        b = graph.add_object("claim", {"text": "B", "confidence": 0.9})
        graph.add_relation(a.id, b.id, "contradicts")

    @behavior(
        name="critic",
        on=["relation.created"],
        where={"relation.type": "contradicts"},
        pattern="(c1:claim)-[r:contradicts]->(c2:claim)",
    )
    def critic(event, graph, ctx):
        fired.append(len(ctx.matches))

    g = Graph()
    Runtime(g).run_goal("g")
    pm = [e for e in g.events if e.type == "pattern.matched"]
    assert len(pm) == 1
    assert pm[0].payload["behavior"] == "critic"
    assert pm[0].payload["matches_count"] == 1


def test_pattern_observer_counts_empty_and_raised_evaluations_without_masking():
    observed: list[float] = []

    @behavior(name="observed", on=["pattern.observe"])
    def observed_behavior(event, graph, ctx):
        pass

    class EmptyMatcher:
        def matches(self, event, graph):
            return []

    observed_behavior.pattern_matcher = EmptyMatcher()
    registry = Registry([observed_behavior], pattern_observer=observed.append)
    graph = Graph()
    event = Event(
        id=graph.ids.event(),
        type="pattern.observe",
        payload={},
        actor="test",
        timestamp=graph.clock.now(),
    )
    assert registry.match(event, graph) == []
    assert len(observed) == 1
    assert observed[0] >= 0

    class RaisingMatcher:
        def matches(self, event, graph):
            raise LookupError("matcher wins")

    observed_behavior.pattern_matcher = RaisingMatcher()
    with pytest.raises(LookupError, match="matcher wins"):
        registry.match(event, graph)
    assert len(observed) == 2


def test_pattern_observer_is_not_called_for_gates_or_missing_matcher():
    observed: list[float] = []

    @behavior(name="gated", on=["expected"])
    def gated(event, graph, ctx):
        pass

    class Matcher:
        def matches(self, event, graph):
            return []

    gated.pattern_matcher = Matcher()
    @behavior(name="plain", on=["actual"])
    def plain(event, graph, ctx):
        pass

    registry = Registry([gated, plain], pattern_observer=observed.append)
    graph = Graph()
    event = Event(
        id=graph.ids.event(),
        type="actual",
        payload={},
        actor="test",
        timestamp=graph.clock.now(),
    )
    registry.match(event, graph)
    assert observed == []


def test_pattern_observer_failure_never_changes_matcher_exception_precedence():
    @behavior(name="both_raise", on=["pattern.raise"])
    def both_raise(event, graph, ctx):
        pass

    class RaisingMatcher:
        def matches(self, event, graph):
            raise LookupError("matcher failure")

    both_raise.pattern_matcher = RaisingMatcher()
    registry = Registry(
        [both_raise],
        pattern_observer=lambda elapsed: (_ for _ in ()).throw(
            RuntimeError("observer failure")
        ),
    )
    graph = Graph()
    event = Event(
        id=graph.ids.event(),
        type="pattern.raise",
        payload={},
        actor="test",
        timestamp=graph.clock.now(),
    )
    with pytest.raises(LookupError, match="matcher failure"):
        registry.match(event, graph)


def test_runtime_pattern_metrics_count_only_actual_matcher_calls() -> None:
    class EmptyMatcher:
        def matches(self, event, graph):
            return []

    @behavior(name="eligible_empty", on=["audit.observe"])
    def eligible_empty(event, graph, ctx):
        pass

    eligible_empty.pattern_matcher = EmptyMatcher()
    metrics = _PatternRecordingMetrics()
    graph = Graph()
    runtime = Runtime(graph, behaviors=[eligible_empty], metrics=metrics)
    graph.emit(
        Event(
            id=graph.ids.event(),
            type="different.type",
            payload={},
            timestamp=graph.clock.now(),
        )
    )
    runtime.run_until_idle()
    assert metrics.pattern_counts() == []
    assert metrics.pattern_durations() == []

    graph.emit(
        Event(
            id=graph.ids.event(),
            type="audit.observe",
            payload={},
            timestamp=graph.clock.now(),
        )
    )
    runtime.run_until_idle()
    assert metrics.pattern_counts() == [1.0]
    assert len(metrics.pattern_durations()) == 1
    assert metrics.pattern_durations()[0] >= 0.0


def test_runtime_pattern_metrics_cover_multiple_and_delayed_recheck() -> None:
    class MatchMatcher:
        def matches(self, event, graph):
            return [object()]

    @behavior(name="first_pattern", on=["audit.multi"])
    def first_pattern(event, graph, ctx):
        pass

    @behavior(name="second_pattern", on=["audit.multi"])
    def second_pattern(event, graph, ctx):
        pass

    first_pattern.pattern_matcher = MatchMatcher()
    second_pattern.pattern_matcher = MatchMatcher()
    metrics = _PatternRecordingMetrics()
    graph = Graph()
    runtime = Runtime(
        graph,
        behaviors=[first_pattern, second_pattern],
        metrics=metrics,
    )
    graph.emit(
        Event(
            id=graph.ids.event(),
            type="audit.multi",
            payload={},
            timestamp=graph.clock.now(),
        )
    )
    runtime.run_until_idle()
    assert metrics.pattern_counts() == [1.0, 1.0]

    @behavior(name="delayed_pattern", on=["audit.delayed"], activate_after=1)
    def delayed_pattern(event, graph, ctx):
        pass

    delayed_pattern.pattern_matcher = MatchMatcher()
    delayed_metrics = _PatternRecordingMetrics()
    delayed_graph = Graph()
    delayed_runtime = Runtime(
        delayed_graph,
        behaviors=[delayed_pattern],
        metrics=delayed_metrics,
    )
    for event_type in ("audit.delayed", "advance.tick"):
        delayed_graph.emit(
            Event(
                id=delayed_graph.ids.event(),
                type=event_type,
                payload={},
                timestamp=delayed_graph.clock.now(),
            )
        )
    delayed_runtime.run_until_idle()
    assert delayed_metrics.pattern_counts() == [1.0, 1.0]
    assert len(delayed_metrics.pattern_durations()) == 2
    assert all(value >= 0.0 for value in delayed_metrics.pattern_durations())


def test_runtime_pattern_metrics_preserve_matcher_exception() -> None:
    class RaisingMatcher:
        def matches(self, event, graph):
            raise LookupError("runtime matcher failure")

    @behavior(name="raising_pattern", on=["audit.raise"])
    def raising_pattern(event, graph, ctx):
        pass

    raising_pattern.pattern_matcher = RaisingMatcher()
    metrics = _PatternRecordingMetrics()
    graph = Graph()
    runtime = Runtime(graph, behaviors=[raising_pattern], metrics=metrics)
    graph.emit(
        Event(
            id=graph.ids.event(),
            type="audit.raise",
            payload={},
            timestamp=graph.clock.now(),
        )
    )

    with pytest.raises(LookupError, match="runtime matcher failure"):
        runtime.run_until_idle()
    assert metrics.pattern_counts() == [1.0]
    assert len(metrics.pattern_durations()) == 1
