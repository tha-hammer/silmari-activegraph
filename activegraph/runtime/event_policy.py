"""Purpose-specific policy for runtime event types.

"Lifecycle" is not a single property: bookkeeping events may be ineligible
for behavior scheduling while still forming meaningful diff or replay history.
Keep those decisions together without collapsing their distinct contracts.
"""

from __future__ import annotations

from dataclasses import dataclass


_BOOKKEEPING_PREFIXES = (
    "behavior.",
    "relation_behavior.",
    "runtime.",
    "llm.",
    "tool.",
    "pattern.",
    "approval.",
    "embedding.",
    "dev.",
    "authority.",
)
_STRUCTURAL_PREFIXES = ("behavior.", "relation_behavior.", "runtime.")
_SCHEDULING_EXACT_TYPES = frozenset({"context.read"})
_STRICT_REPLAY_EXACT_TYPES = frozenset({"context.read"})


@dataclass(frozen=True)
class EventTypePolicy:
    schedules_behaviors: bool
    triggers_pattern_only: bool
    included_in_diff: bool
    included_in_strict_replay: bool


def classify_event_type(event_type: str) -> EventTypePolicy:
    """Return the scheduling, pattern, diff, and replay policy for a type."""

    is_bookkeeping = event_type.startswith(_BOOKKEEPING_PREFIXES)
    schedules_behaviors = (
        not is_bookkeeping and event_type not in _SCHEDULING_EXACT_TYPES
    )
    is_structural = event_type.startswith(_STRUCTURAL_PREFIXES)

    return EventTypePolicy(
        schedules_behaviors=schedules_behaviors,
        triggers_pattern_only=schedules_behaviors,
        included_in_diff=not is_structural,
        included_in_strict_replay=(
            not is_structural and event_type not in _STRICT_REPLAY_EXACT_TYPES
        ),
    )
