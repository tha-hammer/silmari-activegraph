"""Event-count scheduler for `activate_after`. CONTRACT v0.7 #13.

The decision: v0.7's `activate_after` is event-count only, never
wall-clock. Wall-clock would require a clock-source abstraction and
break determinism under replay. Event-count is deterministic across
replay, composes with the tick model, and the escape hatch ("user
drives `runtime.tick()` and injects timer.fired events") is the v1+
extension point if anyone needs wall-clock.

How it works:

  * When a triggering event matches a behavior with `activate_after=N`,
    the runtime calls `scheduler.schedule(...)` instead of invoking
    the behavior directly. A `behavior.scheduled` event is emitted.

  * The scheduler tracks each pending invocation with a fire-at-event
    counter (= current_event_count + N).

  * After every non-lifecycle event the runtime processes, it calls
    `scheduler.due(graph)` which returns the pending entries whose
    fire counters have been reached.

  * For each due entry, the runtime RE-CHECKS the `where=` clause
    against the latest graph state before invoking. If it no longer
    holds, the invocation is silently skipped (no extra event). If it
    holds, the behavior fires normally.

Parsing `activate_after`:
  * int → number of events
  * str "N" or "N events" / "N event" → N events
  * anything else → ValueError at registration time

Anything wall-clock (e.g. "5 minutes") is intentionally rejected with
a clear message pointing at CONTRACT v0.7 #13.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from activegraph.runtime.registry import BehaviorLike


@dataclass
class ScheduledEntry:
    behavior: BehaviorLike
    behavior_name: str
    triggering_event_id: str
    fire_at_event_count: int
    scheduled_event_id: str  # the behavior.scheduled event id


@dataclass
class DelayedQueue:
    """Pending `activate_after` invocations. FIFO within the same fire tick."""

    entries: list[ScheduledEntry] = field(default_factory=list)

    def push(self, entry: ScheduledEntry) -> None:
        self.entries.append(entry)

    def cancel_behaviors(self, behaviors: Collection[BehaviorLike]) -> int:
        """Cancel entries owned by the exact supplied behavior objects."""
        targets = tuple(behaviors)
        kept: list[ScheduledEntry] = []
        removed = 0
        for entry in self.entries:
            if any(entry.behavior is behavior for behavior in targets):
                removed += 1
            else:
                kept.append(entry)
        self.entries = kept
        return removed

    def pop_due(self, current_event_count: int) -> list[ScheduledEntry]:
        due: list[ScheduledEntry] = []
        kept: list[ScheduledEntry] = []
        for e in self.entries:
            if e.fire_at_event_count <= current_event_count:
                due.append(e)
            else:
                kept.append(e)
        self.entries = kept
        return due

    def __len__(self) -> int:
        return len(self.entries)


_DURATION_RE = re.compile(
    r"^\s*(\d+)\s*(events?)?\s*$", re.IGNORECASE
)
_WALL_CLOCK_WORDS = {
    "second", "seconds", "ms", "millisecond", "milliseconds",
    "minute", "minutes", "min", "mins",
    "hour", "hours", "day", "days", "week", "weeks",
}


from activegraph.errors import RegistrationError as _RegistrationError


class InvalidActivateAfter(_RegistrationError, ValueError):
    """``activate_after=`` on a @behavior / @llm_behavior decorator was
    passed an unparseable or out-of-range value.

    Multi-inherits :class:`ValueError` for back-compat with user code
    catching the builtin around behavior registration.
    """

    _doc_slug = "invalid-activate-after"

    def __init__(self, *, spec: Any, kind: str, hint: str) -> None:
        self.spec = spec
        self.kind = kind
        self.hint = hint
        from activegraph.errors import RegistrationError as _R
        _R.__init__(
            self,
            f"activate_after={spec!r} is invalid ({kind})",
            what_failed=(
                f"A behavior decorator was given `activate_after={spec!r}`, "
                f"which the scheduler refused as {kind}."
            ),
            why=(
                "`activate_after` schedules a behavior to fire N events after "
                "its triggering event. The runtime evaluates the schedule "
                "against the event log (not wall-clock) so replay produces "
                "identical timing — wall-clock units are intentionally out "
                "of scope (CONTRACT v0.7 #13). An unparseable value would "
                "make scheduling silently wrong, which would corrupt the "
                "audit trail at every replay."
            ),
            how_to_fix=hint,
            context={"spec": repr(spec), "kind": kind},
        )


def parse_activate_after(spec: Any) -> int:
    """Parse `activate_after=` into an integer event count.

    Accepts:
      - int N (N >= 1)
      - str "N", "N event", "N events"

    Rejects:
      - 0 or negative
      - any wall-clock unit (seconds, minutes, ...): wall-clock is
        intentionally out of scope for v0.7 (see CONTRACT v0.7 #13).
    """
    if isinstance(spec, bool):
        # bool is an int in Python — guard against accidental True/False.
        raise InvalidActivateAfter(
            spec=spec,
            kind="bool not int",
            hint=(
                "Pass an integer event count instead:\n"
                "    activate_after=5\n"
                "or a string with the 'events' unit:\n"
                "    activate_after='5 events'"
            ),
        )
    if isinstance(spec, int):
        n = spec
    elif isinstance(spec, str):
        s = spec.strip().lower()
        # Detect wall-clock units and reject with a CONTRACT-pointing message.
        for word in _WALL_CLOCK_WORDS:
            if word in s.split():
                raise InvalidActivateAfter(
                    spec=spec,
                    kind="wall-clock unit",
                    hint=(
                        f"Wall-clock units (seconds/minutes/hours) are not "
                        f"supported. Express the delay as an event count:\n"
                        f"    activate_after=5\n"
                        f"    activate_after='5 events'\n"
                        f"\n"
                        f"If you genuinely need wall-clock scheduling, file "
                        f"an issue — the v1+ contract leaves room for it "
                        f"behind a separate primitive (see CONTRACT v0.7 #23)."
                    ),
                )
        m = _DURATION_RE.match(s)
        if not m:
            raise InvalidActivateAfter(
                spec=spec,
                kind="unparseable string",
                hint=(
                    "Use one of:\n"
                    "    activate_after=5\n"
                    "    activate_after='5'\n"
                    "    activate_after='5 events'\n"
                    "    activate_after='5 event'"
                ),
            )
        n = int(m.group(1))
    else:
        raise InvalidActivateAfter(
            spec=spec,
            kind=f"type {type(spec).__name__}",
            hint=(
                "Pass an int or a string. Example:\n"
                "    activate_after=5\n"
                "    activate_after='5 events'"
            ),
        )
    if n < 1:
        raise InvalidActivateAfter(
            spec=spec,
            kind="must be >= 1",
            hint=(
                "`activate_after` schedules N events after the trigger. "
                "Zero or negative N has no defined meaning. The minimum "
                "is 1 (fire on the very next event after the trigger)."
            ),
        )
    return n
