# UnsupportedMigrationBackendError

No installed migration provider declares the URL's normalized scheme. This is
separate from ordinary `open_store()` support: migration has its own extension
boundary.

## Quick fix

Install a package exposing an `activegraph.migration_backends` entry point or
register a provider explicitly before invoking `migrate()` or the CLI.

## What's related

- [Store migration API](../api/store.md#migration-backend-extensions)
- [CLI migrate](../cli.md#migrate)
