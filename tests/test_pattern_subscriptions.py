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
    Graph,
    Runtime,
    UnsupportedPatternError,
    behavior,
    clear_registry,
)
from activegraph.core.event import Event
from activegraph.runtime.registry import Registry


@pytest.mark.parametrize(
    ("event_type", "eligible"),
    [
        ("behavior.started", False),
        ("relation_behavior.completed", False),
        ("runtime.idle", False),
        ("llm.requested", False),
        ("tool.responded", False),
        ("pattern.matched", False),
        ("approval.proposed", False),
        ("embedding.requested", False),
        ("dev.override", False),
        ("authority.decision", False),
        ("context.read", False),
        ("context.foo", True),
        ("pack.loaded", True),
        ("goal.created", True),
        ("object.created", True),
        ("custom.event", True),
        ("runtimeish.event", True),
    ],
)
def test_pattern_only_registry_policy_is_complete(
    event_type: str, eligible: bool
) -> None:
    """Registry enforces pattern-only eligibility without Runtime masking it."""

    clear_registry()

    @behavior(name="auditor", pattern="(c:claim)")
    def auditor(event, graph, ctx):
        pass

    graph = Graph()
    graph.add_object("claim", {"text": "present"})
    event = Event(id="evt_policy", type=event_type)

    assert bool(Registry([auditor]).match(event, graph)) is eligible


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
