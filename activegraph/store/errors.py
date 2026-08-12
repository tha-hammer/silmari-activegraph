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

DB-driver errors (sqlite3.OperationalError, psycopg.OperationalError)
are NOT wrapped in this PR. The failure modes are driver-specific and
the recovery prose varies enough per mode (WAL contention, auth, host
unreachable, db missing, conn dropped) that a dedicated DB-error PR
will cover them with the right per-mode "Why:" and "How to fix:"
prose. Flagged in CONTRACT v1.0 PR-C section, not silently dropped.
"""

from __future__ import annotations

from activegraph.errors import StorageError


_MIGRATION_WHY = (
    "Migration resolves both endpoint capabilities before opening either "
    "backend so an unsupported or ambiguous provider can never partially "
    "write the destination."
)


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


class CorruptedEventPayloadError(StorageError):
    """A stored event payload couldn't be decoded as JSON.

    Fires at load-time when a row's payload column contains invalid
    JSON. Distinct from :class:`NonSerializableEventError`, which fires
    at emit-time when a Python value can't be encoded to JSON.
    Corruption-on-load means the bytes on disk don't parse — a
    different failure mode requiring a different recovery.
    """

    _doc_slug = "corrupted-event-payload-error"


class MigrationBackendConflictError(StorageError):
    """More than one migration provider claims the same URL scheme."""

    _doc_slug = "migration-backend-conflict-error"

    def __init__(self, scheme: str, providers: tuple[str, ...]) -> None:
        self.scheme = scheme
        self.providers = providers
        super().__init__(
            f"multiple migration backends claim scheme {scheme!r}",
            what_failed=(
                f"Migration scheme {scheme!r} is claimed by: "
                f"{', '.join(providers) or '<unknown providers>'}."
            ),
            why=_MIGRATION_WHY,
            how_to_fix=(
                "Remove the duplicate registration or entry point. Built-in "
                "providers cannot be shadowed implicitly; use an explicit "
                "replace=True registration only when replacement is intended."
            ),
            context={"scheme": scheme, "providers": list(providers)},
        )


class UnsupportedMigrationBackendError(StorageError):
    """No migration provider is installed for a URL scheme."""

    _doc_slug = "unsupported-migration-backend-error"

    def __init__(
        self, url: str, scheme: str, installed_schemes: tuple[str, ...]
    ) -> None:
        self.url = url
        self.scheme = scheme
        self.installed_schemes = installed_schemes
        super().__init__(
            f"no migration backend supports scheme {scheme!r}",
            what_failed=f"No migration provider is registered for {url!r}.",
            why=_MIGRATION_WHY,
            how_to_fix=(
                "Install or register a provider for this scheme. Installed "
                f"schemes: {', '.join(installed_schemes) or '<none>'}."
            ),
            context={
                "url": url,
                "scheme": scheme,
                "installed_schemes": list(installed_schemes),
            },
        )


class UnsupportedMigrationCapabilityError(StorageError):
    """A provider cannot serve as the requested source/destination role."""

    _doc_slug = "unsupported-migration-capability-error"

    def __init__(
        self,
        url: str,
        scheme: str,
        required: str,
        advertised: frozenset[str],
    ) -> None:
        self.url = url
        self.scheme = scheme
        self.required = required
        self.advertised = advertised
        super().__init__(
            f"migration backend {scheme!r} lacks {required!r} capability",
            what_failed=(
                f"The provider for {url!r} advertises "
                f"{sorted(advertised)!r}, not required capability {required!r}."
            ),
            why=_MIGRATION_WHY,
            how_to_fix=(
                f"Choose a backend with {required!r} capability or install a "
                "provider that implements that role."
            ),
            context={
                "url": url,
                "scheme": scheme,
                "required": required,
                "advertised": sorted(advertised),
            },
        )


class MigrationBackendLoadError(StorageError):
    """The requested migration-provider entry point failed to load."""

    _doc_slug = "migration-backend-load-error"

    def __init__(self, scheme: str, detail: str) -> None:
        self.scheme = scheme
        self.detail = detail
        super().__init__(
            f"migration backend for scheme {scheme!r} could not be loaded",
            what_failed=detail,
            why=_MIGRATION_WHY,
            how_to_fix=(
                "Repair or reinstall the package that declares the requested "
                "activegraph.migration_backends entry point."
            ),
            context={"scheme": scheme, "detail": detail},
        )


class MigrationBackendCloseError(StorageError):
    """One or more URL-owned migration sessions failed to close."""

    _doc_slug = "migration-backend-close-error"

    def __init__(self, failures: tuple[tuple[str, BaseException], ...]) -> None:
        urls = tuple(url for url, _ in failures)
        detail = "; ".join(
            f"{url}: {type(exc).__name__}: {exc}" for url, exc in failures
        )
        super().__init__(
            "migration backend cleanup failed",
            what_failed=detail,
            why=(
                "Migration owns every session it opens and must report a close "
                "failure instead of silently leaking its connection resources."
            ),
            how_to_fix=(
                "Inspect the backend/driver error, close any remaining session "
                "resources, and retry after the connection problem is resolved."
            ),
            context={"urls": list(urls), "failure_count": len(failures)},
        )
        self.failures = failures


__all__ = [
    "SchemaVersionMismatch",
    "EventNotFoundError",
    "DuplicateEventError",
    "CorruptedEventPayloadError",
    "MigrationBackendCloseError",
    "MigrationBackendConflictError",
    "MigrationBackendLoadError",
    "UnsupportedMigrationBackendError",
    "UnsupportedMigrationCapabilityError",
]
