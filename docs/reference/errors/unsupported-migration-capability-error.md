# UnsupportedMigrationCapabilityError

The selected provider exists but cannot serve the requested role: migration
requires `read` at the source and `write` at the destination. The failure is
preflight-only, before either backend opens.

## Quick fix

Choose a destination provider advertising `write`, or a source provider
advertising `read`, and retry with the corrected endpoint.

## What's related

- [Store migration API](../api/store.md#migration-backend-extensions)
- [CLI migrate](../cli.md#migrate)
