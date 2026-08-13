# MigrationBackendLoadError

The entry point for the requested URL scheme could not be imported or did not
produce a valid migration provider. Unrelated entry points are lazy and cannot
cause this error.

## Quick fix

Repair or reinstall the package declaring the requested
`activegraph.migration_backends` entry point, then verify its provider declares
the entry-point scheme, capabilities, `validate_url()`, and `open()`.

## What's related

- [Store migration API](../api/store.md#migration-backend-extensions)
- [CLI migrate](../cli.md#migrate)
