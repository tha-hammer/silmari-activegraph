"""Match events to behaviors. CONTRACT #10: registration order for ties.

v0.7 (CONTRACT v0.7 #11): a behavior with both `on=[...]` and
`pattern=...` requires BOTH conditions — the event type matches AND
the pattern matches against the post-event graph state. A behavior
with only `pattern=...` (empty `on`) matches on every non-lifecycle
event.

`match()` returns `(behavior, relations, matches)` triples. The
`matches` list is the pattern matcher's bindings (empty for
behaviors without `pattern=`). The runtime forwards `matches` as
`ctx.matches`.
"""

from __future__ import annotations

from time import monotonic
from typing import Any, Callable, Iterable, Union

from activegraph.behaviors.base import Behavior, RelationBehavior
from activegraph.core.event import Event
from activegraph.core.graph import Graph, Relation, evaluate_where
from activegraph.runtime.event_policy import classify_event_type


BehaviorLike = Union[Behavior, RelationBehavior]
PatternObserver = Callable[[float], None]


class Registry:
    def __init__(
        self,
        behaviors: Iterable[BehaviorLike],
        *,
        pattern_observer: PatternObserver | None = None,
    ) -> None:
        self._behaviors: list[BehaviorLike] = list(behaviors)
        self._pattern_observer = pattern_observer

    def all(self) -> list[BehaviorLike]:
        return list(self._behaviors)

    def index_of(self, behavior: BehaviorLike) -> int:
        for i, b in enumerate(self._behaviors):
            if b is behavior:
                return i
        return -1

    def contains_identity(self, behavior: BehaviorLike) -> bool:
        return any(candidate is behavior for candidate in self._behaviors)

    def _match_behavior(
        self,
        behavior: BehaviorLike,
        event: Event,
        graph: Graph,
    ) -> tuple[list[Relation], list[Any]] | None:
        """Match one behavior using the same gates as live dispatch."""
        # Event-type filter: required only when `on=` is non-empty.
        # Pattern-only behaviors (empty `on`) skip this gate.
        if behavior.on and event.type not in behavior.on:
            return None
        # Suppress lifecycle events for pattern-only behaviors so a
        # pattern doesn't fire on behavior.started, etc.
        if not behavior.on and not classify_event_type(event.type).triggers_pattern_only:
            return None
        pattern_matches: list[Any] = []
        if behavior.pattern_matcher is not None:
            started = monotonic()
            try:
                pattern_matches = behavior.pattern_matcher.matches(event, graph)
            finally:
                if self._pattern_observer is not None:
                    try:
                        self._pattern_observer(monotonic() - started)
                    except Exception:
                        # Observation cannot change matcher control flow. If
                        # matching raised, that original exception still wins.
                        pass
            if not pattern_matches:
                return None
        if isinstance(behavior, RelationBehavior):
            relations = _matching_relations(behavior, event, graph)
            if not relations:
                return None
            return relations, pattern_matches
        if behavior.where and not evaluate_where(behavior.where, event.payload):
            return None
        return [], pattern_matches

    def match(
        self, event: Event, graph: Graph
    ) -> list[tuple[BehaviorLike, list[Relation], list[Any]]]:
        """Return (behavior, matching_relations, pattern_matches) triples
        in registration order. Pattern matches are empty for behaviors
        without `pattern=`.
        """
        out: list[tuple[BehaviorLike, list[Relation], list[Any]]] = []
        for b in self._behaviors:
            matched = self._match_behavior(b, event, graph)
            if matched is not None:
                relations, pattern_matches = matched
                out.append((b, relations, pattern_matches))
        return out

def _matching_relations(
    rb: RelationBehavior, event: Event, graph: Graph
) -> list[Relation]:
    # Push the relation-type filter down to the store (find_relations) instead
    # of scanning every edge in Python; the reference/where checks stay here.
    candidates = graph.relations(type=rb.relation_type)
    referenced = _collect_string_values(event.payload)
    out: list[Relation] = []
    for r in candidates:
        if r.source in referenced or r.target in referenced:
            if rb.where and not evaluate_where(rb.where, event.payload):
                continue
            out.append(r)
    return out


def _collect_string_values(obj) -> set[str]:
    out: set[str] = set()
    _walk(obj, out)
    return out


def _walk(obj, out: set[str]) -> None:
    if isinstance(obj, str):
        out.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, out)
