# MigrationBackendCloseError

Migration completed its primary work but one or more URL-owned backend
sessions failed to close. The error lists affected URLs in destination/source
cleanup order. If another exception was already active, that exception remains
primary and receives cleanup diagnostics instead.

## Quick fix

Inspect the underlying driver error, release any remaining connection
resources, and retry after the backend's connection problem is resolved.

## What's related

- [Store migration API](../api/store.md#migration-backend-extensions)
- [CLI migrate](../cli.md#migrate)
