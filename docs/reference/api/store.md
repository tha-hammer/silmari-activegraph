# Store

Event stores, URL parsing, and migration. For the conceptual
model see [`concepts/graph`](../../concepts/graph.md) (graph as
projection of the event log) and
[`concepts/replay`](../../concepts/replay.md).

## Stores

::: activegraph.EventStore

::: activegraph.InMemoryEventStore

::: activegraph.SQLiteEventStore

## Graph stores

The materialized graph projection (objects, relations, patches) lives
behind a `GraphStore`, distinct from the durable `EventStore` above. See
the [Using the FalkorDB graph store](../../guides/using-falkordb.md) guide.

::: activegraph.GraphStore

::: activegraph.InMemoryGraphStore

::: activegraph.FalkorDBGraphStore

## URL parsing + helpers

::: activegraph.open_store

::: activegraph.parse_store_url

## Migration

The canonical implementation is `activegraph.store.migration`; the historical
`activegraph.observability.migration` module remains an identity-preserving
compatibility re-export.

::: activegraph.migrate

::: activegraph.MigrationReport

::: activegraph.MigrationRunReport

::: activegraph.RunRecord

### Migration backend extensions

Migration uses a capability separate from the per-run `EventStore` protocol.
Both endpoint URLs and their `read`/`write` roles are validated before either
backend opens.

::: activegraph.MigrationBackend

::: activegraph.MigrationBackendProvider

::: activegraph.register_migration_backend

::: activegraph.resolve_migration_backend

Third-party packages may publish one entry point per URL-scheme alias:

```toml
[project.entry-points."activegraph.migration_backends"]
warehouse = "acme_activegraph:warehouse_migration_provider"
```

This registry affects migration only; `open_store()` remains intentionally
limited to the built-in runtime stores.
