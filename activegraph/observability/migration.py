"""Compatibility re-exports for canonical :mod:`activegraph.store.migration`."""

from activegraph.store.migration import (
    BackendRegistration,
    CorruptMigrationEvent,
    MigrationBackend,
    MigrationBackendProvider,
    MigrationCapability,
    MigrationItem,
    MigrationReport,
    MigrationRunReport,
    clear_migration_backend_cache,
    migrate,
    register_migration_backend,
    resolve_migration_backend,
)

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
