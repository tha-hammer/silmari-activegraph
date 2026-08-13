"""Backend-neutral, extensible event-log migration.

This is the canonical migration module. Ordinary :class:`EventStore` remains
per-run and deliberately narrow; administrative multi-run migration uses the
separate provider capability defined here.
"""

from __future__ import annotations

import importlib.metadata
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeAlias, cast, runtime_checkable
from urllib.parse import urlparse

from activegraph.core.event import Event
from activegraph.store.base import RunRecord
from activegraph.store.errors import (
    CorruptedEventPayloadError,
    MigrationBackendCloseError,
    MigrationBackendConflictError,
    MigrationBackendLoadError,
    UnsupportedMigrationBackendError,
    UnsupportedMigrationCapabilityError,
)
from activegraph.store.url import parse_store_url


MigrationCapability: TypeAlias = Literal["read", "write"]


@dataclass(frozen=True)
class CorruptMigrationEvent:
    """One stored row whose event payload could not be decoded."""

    event_id: str
    error: CorruptedEventPayloadError


MigrationItem: TypeAlias = Event | CorruptMigrationEvent


@runtime_checkable
class MigrationBackend(Protocol):
    """One opened, URL-bound administrative migration session."""

    def list_runs(self) -> Sequence[RunRecord]: ...

    def iter_run(self, run_id: str) -> Iterator[MigrationItem]: ...

    def write_run_transactionally(
        self, record: RunRecord, events: Sequence[Event]
    ) -> int: ...

    def close(self) -> None: ...


@runtime_checkable
class MigrationBackendProvider(Protocol):
    """Factory and pure URL validator for one or more URL schemes."""

    schemes: tuple[str, ...]
    capabilities: frozenset[MigrationCapability]

    def validate_url(self, url: str) -> None: ...

    def open(self, url: str) -> MigrationBackend: ...


@dataclass(frozen=True)
class MigrationRunReport:
    """Outcome of migrating one run between stores."""

    run_id: str
    status: str
    events_migrated: int
    error: str | None = None
    skipped_events: tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationReport:
    """Aggregate result of a store-to-store migration."""

    source_url: str
    dest_url: str
    runs: tuple[MigrationRunReport, ...]

    @property
    def ok(self) -> bool:
        return all(report.status != "failed" for report in self.runs)

    @property
    def failures(self) -> tuple[MigrationRunReport, ...]:
        return tuple(report for report in self.runs if report.status == "failed")


_ENTRY_POINT_GROUP = "activegraph.migration_backends"
_LOCK = threading.RLock()
_next_registration_id = 0
_registration_layers: dict[
    str, list[tuple[int, MigrationBackendProvider, bool]]
] = {}
_entry_points_cache: dict[str, tuple[Any, ...]] | None = None
_loaded_entry_points: dict[int, MigrationBackendProvider] = {}


def _provider_name(provider: Any) -> str:
    return f"{type(provider).__module__}.{type(provider).__qualname__}"


def _validated_provider(provider: Any) -> MigrationBackendProvider:
    schemes = getattr(provider, "schemes", None)
    capabilities = getattr(provider, "capabilities", None)
    if (
        not isinstance(schemes, tuple)
        or not schemes
        or any(
            not isinstance(scheme, str)
            or not scheme
            or scheme != scheme.lower()
            or ":" in scheme
            for scheme in schemes
        )
        or len(set(schemes)) != len(schemes)
        or not isinstance(capabilities, frozenset)
        or not capabilities.issubset({"read", "write"})
        or not callable(getattr(provider, "validate_url", None))
        or not callable(getattr(provider, "open", None))
    ):
        raise TypeError(
            "migration provider must declare unique lowercase schemes, a "
            "read/write capability frozenset, validate_url(), and open()"
        )
    return cast(MigrationBackendProvider, provider)


def _builtin_providers() -> dict[str, MigrationBackendProvider]:
    from activegraph.store.postgres import PostgresMigrationBackendProvider
    from activegraph.store.sqlite import SQLiteMigrationBackendProvider

    providers: tuple[MigrationBackendProvider, ...] = (
        cast(MigrationBackendProvider, SQLiteMigrationBackendProvider()),
        cast(MigrationBackendProvider, PostgresMigrationBackendProvider()),
    )
    return {
        scheme: provider for provider in providers for scheme in provider.schemes
    }


class BackendRegistration:
    """Idempotent token for one explicit provider registration."""

    def __init__(self, registration_id: int, schemes: tuple[str, ...]) -> None:
        self._registration_id = registration_id
        self._schemes = schemes
        self._active = True

    def unregister(self) -> None:
        """Remove this layer and reveal the previous mapping, if any."""

        with _LOCK:
            if not self._active:
                return
            for scheme in self._schemes:
                layers = _registration_layers.get(scheme, [])
                layers[:] = [
                    layer for layer in layers if layer[0] != self._registration_id
                ]
                if not layers:
                    _registration_layers.pop(scheme, None)
            self._active = False


def register_migration_backend(
    provider: MigrationBackendProvider, *, replace: bool = False
) -> BackendRegistration:
    """Register a provider explicitly and return an unregister token."""

    checked = _validated_provider(provider)
    builtins = _builtin_providers()
    global _next_registration_id
    with _LOCK:
        for scheme in checked.schemes:
            if not replace and (scheme in builtins or _registration_layers.get(scheme)):
                existing = (
                    _provider_name(_registration_layers[scheme][-1][1])
                    if _registration_layers.get(scheme)
                    else _provider_name(builtins[scheme])
                )
                raise MigrationBackendConflictError(
                    scheme, (existing, _provider_name(checked))
                )
        _next_registration_id += 1
        registration_id = _next_registration_id
        for scheme in checked.schemes:
            _registration_layers.setdefault(scheme, []).append(
                (registration_id, checked, replace)
            )
    return BackendRegistration(registration_id, checked.schemes)


def _entry_points_by_scheme() -> dict[str, tuple[Any, ...]]:
    global _entry_points_cache
    with _LOCK:
        if _entry_points_cache is not None:
            return _entry_points_cache
        discovered = importlib.metadata.entry_points()
        if hasattr(discovered, "select"):
            entries = tuple(discovered.select(group=_ENTRY_POINT_GROUP))
        else:  # pragma: no cover - Python/importlib compatibility
            entries = tuple(discovered.get(_ENTRY_POINT_GROUP, ()))
        grouped: dict[str, list[Any]] = {}
        for entry in entries:
            grouped.setdefault(str(entry.name).lower(), []).append(entry)
        _entry_points_cache = {
            scheme: tuple(matches) for scheme, matches in grouped.items()
        }
        return _entry_points_cache


def clear_migration_backend_cache() -> None:
    """Clear only cached entry-point discovery/provider loads (test support)."""

    global _entry_points_cache
    with _LOCK:
        _entry_points_cache = None
        _loaded_entry_points.clear()


def _scheme_from_url(url: str) -> str:
    if not isinstance(url, str) or not url or not urlparse(url).scheme:
        parse_store_url(url)  # raises the existing structured InvalidStoreURL
        raise AssertionError("parse_store_url accepted a URL without a scheme")
    scheme = urlparse(url).scheme.lower()
    if not scheme or any(character.isspace() for character in scheme):
        parse_store_url(url)
        raise AssertionError("parse_store_url accepted a malformed scheme")
    return scheme


def _load_entry_point(scheme: str, entry: Any) -> MigrationBackendProvider:
    key = id(entry)
    with _LOCK:
        cached = _loaded_entry_points.get(key)
    if cached is not None:
        return cached
    try:
        loaded = entry.load()
        provider = loaded() if isinstance(loaded, type) else loaded
        checked = _validated_provider(provider)
        if scheme not in checked.schemes:
            raise TypeError(
                f"entry point {entry.name!r} loaded a provider that does not "
                f"declare scheme {scheme!r}"
            )
    except Exception as exc:
        raise MigrationBackendLoadError(
            scheme,
            f"Entry point {entry!r} failed: {type(exc).__name__}: {exc}",
        ) from exc
    with _LOCK:
        _loaded_entry_points[key] = checked
    return checked


def resolve_migration_backend(
    url: str, *, require: MigrationCapability
) -> MigrationBackendProvider:
    """Resolve and purely validate the provider for ``url`` and role."""

    if require not in ("read", "write"):
        raise ValueError("require must be 'read' or 'write'")
    scheme = _scheme_from_url(url)
    builtins = _builtin_providers()
    with _LOCK:
        layers = tuple(_registration_layers.get(scheme, ()))
    explicit = layers[-1] if layers else None
    matches = _entry_points_by_scheme().get(scheme, ())

    if len(matches) > 1:
        raise MigrationBackendConflictError(
            scheme, tuple(repr(entry) for entry in matches)
        )
    if explicit is not None:
        _, provider, replaces = explicit
        if matches and not replaces:
            raise MigrationBackendConflictError(
                scheme, (_provider_name(provider), repr(matches[0]))
            )
    elif scheme in builtins:
        provider = builtins[scheme]
        if matches:
            raise MigrationBackendConflictError(
                scheme, (_provider_name(provider), repr(matches[0]))
            )
    elif matches:
        provider = _load_entry_point(scheme, matches[0])
    else:
        installed = tuple(
            sorted(
                set(builtins)
                | set(_registration_layers)
                | set(_entry_points_by_scheme())
            )
        )
        raise UnsupportedMigrationBackendError(url, scheme, installed)

    provider = _validated_provider(provider)
    if require not in provider.capabilities:
        raise UnsupportedMigrationCapabilityError(
            url, scheme, require, frozenset(provider.capabilities)
        )
    provider.validate_url(url)
    return provider


def _migrate_one_run(
    source: MigrationBackend,
    destination: MigrationBackend,
    record: RunRecord,
    *,
    skip_corrupted: bool,
) -> MigrationRunReport:
    events: list[Event] = []
    skipped: list[str] = []
    try:
        for item in source.iter_run(record.run_id):
            if isinstance(item, CorruptMigrationEvent):
                if not skip_corrupted:
                    raise item.error
                skipped.append(item.event_id)
            else:
                events.append(item)
    except Exception as exc:
        return MigrationRunReport(
            record.run_id,
            "failed",
            0,
            error=f"read failure: {exc}",
            skipped_events=tuple(skipped),
        )

    try:
        migrated = destination.write_run_transactionally(record, events)
    except Exception as exc:
        return MigrationRunReport(
            record.run_id,
            "failed",
            0,
            error=f"write failure: {exc}",
            skipped_events=tuple(skipped),
        )
    return MigrationRunReport(
        record.run_id,
        "ok",
        migrated,
        skipped_events=tuple(skipped),
    )


def migrate(
    source_url: str,
    dest_url: str,
    *,
    only_run_ids: list[str] | None = None,
    on_progress: Callable[[MigrationRunReport], None] | None = None,
    skip_corrupted: bool = False,
) -> MigrationReport:
    """Copy selected/all runs through migration-specific backend sessions."""

    source_provider = resolve_migration_backend(source_url, require="read")
    destination_provider = resolve_migration_backend(dest_url, require="write")

    source: MigrationBackend | None = None
    destination: MigrationBackend | None = None
    active_error: BaseException | None = None
    close_failures: list[tuple[str, BaseException]] = []
    try:
        source = source_provider.open(source_url)
        destination = destination_provider.open(dest_url)
        records = list(source.list_runs())
        if only_run_ids is not None:
            wanted = set(only_run_ids)
            records = [record for record in records if record.run_id in wanted]

        reports: list[MigrationRunReport] = []
        for record in records:
            report = _migrate_one_run(
                source,
                destination,
                record,
                skip_corrupted=skip_corrupted,
            )
            reports.append(report)
            if on_progress is not None:
                on_progress(report)
        return MigrationReport(source_url, dest_url, tuple(reports))
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        for url, backend in ((dest_url, destination), (source_url, source)):
            if backend is None:
                continue
            try:
                backend.close()
            except BaseException as close_error:
                close_failures.append((url, close_error))
                if active_error is not None:
                    active_error.add_note(
                        f"Migration backend close failed for {url}: "
                        f"{type(close_error).__name__}: {close_error}"
                    )
        if active_error is None and close_failures:
            raise MigrationBackendCloseError(tuple(close_failures))


__all__ = [
    "BackendRegistration",
    "CorruptMigrationEvent",
    "MigrationBackend",
    "MigrationBackendProvider",
    "MigrationCapability",
    "MigrationItem",
    "MigrationReport",
    "MigrationRunReport",
    "clear_migration_backend_cache",
    "migrate",
    "register_migration_backend",
    "resolve_migration_backend",
]
