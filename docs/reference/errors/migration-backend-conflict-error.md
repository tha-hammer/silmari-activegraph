# MigrationBackendConflictError

More than one migration provider claims the same normalized URL scheme. The
resolver fails before opening either endpoint so provider precedence cannot
silently redirect a migration.

## Quick fix

Remove the duplicate explicit registration or entry point. Built-ins cannot be
shadowed implicitly; use `register_migration_backend(provider, replace=True)`
only for an intentional, scoped replacement and unregister its returned token
when finished.

## What's related

- [Store migration API](../api/store.md#migration-backend-extensions)
- [CLI migrate](../cli.md#migrate)
