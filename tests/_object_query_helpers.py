"""Shared object-query fixtures for Graph/View parity tests."""

from __future__ import annotations

from activegraph import FrozenClock, Graph, IDGen


def collision_graph() -> tuple[Graph, str, str]:
    """Return a graph whose first object collides with framework field names."""
    graph = Graph(ids=IDGen(), clock=FrozenClock())
    colliding = graph.add_object(
        "claim",
        {
            "id": "domain-id",
            "type": "domain-type",
            "version": 99,
            "data": {"nested": True},
            "confidence": 0.9,
        },
        actor="fixture",
    )
    ordinary = graph.add_object(
        "claim",
        {
            "id": "other-domain-id",
            "type": "other-domain-type",
            "version": 100,
            "data": {"nested": False},
            "confidence": 0.4,
        },
        actor="other",
    )
    return graph, colliding.id, ordinary.id


OBJECT_QUERY_CASES = [
    ({"confidence": 0.9}, "colliding"),
    ({"data.confidence": 0.9}, "colliding"),
    ({"id": "claim#1"}, "colliding"),
    ({"type": "claim"}, "both"),
    ({"version": 1}, "both"),
    ({"data.id": "domain-id"}, "colliding"),
    ({"data.type": "domain-type"}, "colliding"),
    ({"data.version": 99}, "colliding"),
    ({"data.data.nested": True}, "colliding"),
    ({"provenance.created_by": "fixture"}, "colliding"),
]
