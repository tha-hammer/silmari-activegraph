"""Storage-layer error leaves. v1.0 PR-C — StorageError category.

The classes here are the v1.0-format leaves under
:class:`activegraph.errors.StorageError`. The two pre-v1.0 storage
errors (:class:`NonSerializableEventError` in ``serde.py`` and
:class:`InvalidStoreURL` in ``url.py``) stay in their topic modules
and are re-parented in this PR; everything new lives here.

Multi-inheritance with Python builtins (KeyError, ValueError) is used
where existing user code conventionally catches the builtin —
``except KeyError`` around a store lookup, ``except ValueError`` around
event insertion. Preserves the catch site.

Duplicate-key conflicts on ``(event_id, run_id)`` are translated to the
public :class:`DuplicateEventError` by every backend. Other DB-driver errors
(sqlite3.OperationalError, psycopg.OperationalError) are NOT wrapped. Their
failure modes are driver-specific and
the recovery prose varies enough per mode (WAL contention, auth, host
unreachable, db missing, conn dropped) that a dedicated DB-error PR
will cover them with the right per-mode "Why:" and "How to fix:"
prose. Flagged in CONTRACT v1.0 PR-C section, not silently dropped.
"""

from __future__ import annotations

from activegraph.errors import StorageError


class SchemaVersionMismatch(StorageError):
    """The store's recorded ``schema_version`` doesn't match what this
    activegraph build expects.

    Fires on store open. The store file is intact; it was just written
    by a different (older or newer) activegraph build. Recovery is
    one of three things: upgrade activegraph, downgrade the store via
    migration, or migrate the run to a fresh store with the current
    build.
    """

    _doc_slug = "schema-version-mismatch"


class EventNotFoundError(StorageError, KeyError):
    """An event id wasn't found in the run's event log.

    Multi-inherits :class:`KeyError` so user code that does
    ``except KeyError`` around store lookups keeps working.
    ``store.get_event(event_id)`` returns None for a missing id.
    Fires from the fork primitive when ``--at-event`` names a missing id.
    """

    _doc_slug = "event-not-found-error"


class DuplicateEventError(StorageError, ValueError):
    """Two events with the same id were appended to the same run.

    Multi-inherits :class:`ValueError` for back-compat with user code
    catching ValueError around appends. Fires only on programmer error:
    the runtime's id generator is monotonic so duplicates shouldn't
    arise in normal use. Common cause: hand-constructing events with
    fixed ids in a test fixture.
    """

    _doc_slug = "duplicate-event-error"


def _duplicate_event_error(
    *, event_id: str, run_id: str, backend: str
) -> DuplicateEventError:
    """Build the backend-neutral duplicate-append error."""
    return DuplicateEventError(
        f"duplicate event id {event_id!r} in run {run_id!r}",
        what_failed=(
            f"An event with id {event_id!r} already exists in run {run_id!r}. "
            "Appends require unique (event id, run id) pairs."
        ),
        why=(
            "Event ids are the addressing primitive for the entire framework — "
            "behaviors reference events by id, the replay cache keys on them, "
            "and the causal chain walks them. A duplicate id would silently "
            "reroute one of those references, corrupting the audit trail. The "
            "store refuses the append rather than risk it."
        ),
        how_to_fix=(
            "Event ids in normal use come from the runtime's monotonic id "
            "generator (IDGen) and cannot collide. A duplicate almost always "
            "means a test fixture is hand-constructing events with fixed ids "
            "and a previous test left state behind. Use IDGen to generate ids, "
            "or call `clear_registry()` / construct a fresh Graph between tests."
        ),
        context={"event_id": event_id, "run_id": run_id, "backend": backend},
    )


class CorruptedEventPayloadError(StorageError):
    """A stored event payload couldn't be decoded as JSON.

    Fires at load-time when a row's payload column contains invalid
    JSON. Distinct from :class:`NonSerializableEventError`, which fires
    at emit-time when a Python value can't be encoded to JSON.
    Corruption-on-load means the bytes on disk don't parse — a
    different failure mode requiring a different recovery.
    """

    _doc_slug = "corrupted-event-payload-error"


__all__ = [
    "SchemaVersionMismatch",
    "EventNotFoundError",
    "DuplicateEventError",
    "CorruptedEventPayloadError",
]
