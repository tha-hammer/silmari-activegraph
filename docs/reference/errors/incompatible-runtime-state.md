# IncompatibleRuntimeState

An operation requires a runtime state that isn't satisfied — either
a state that must be set but isn't, or a state that mustn't be set
but is. Two sites currently raise this: `runtime.fork()` requires a
SQLite-backed runtime, and `graph.attach_store()` requires no store
already attached.

This is part of the three-page Configuration cluster, along with
[`invalid-runtime-configuration`](invalid-runtime-configuration.md)
and [`invalid-argument-type`](invalid-argument-type.md). All three
fire at construction or operation time when the runtime's setup
doesn't match what the operation needs.

## Quick fix

The recovery depends on which site fired the error. The summary
line names the operation and the current state.

### fork() requires SQLite

```bash
# Migrate the run to a SQLite store first, then fork:
activegraph migrate --from <current-url> --to sqlite:///fork-source.db
activegraph fork sqlite:///fork-source.db --run-id <run> --at-event <evt>
```

`Runtime.fork()` uses SQLite-specific transactional copy primitives
(CONTRACT v0.5 #9 and the 2026-08-12 Set 4 amendment #2). Postgres-native
Runtime forking is a known follow-on — file
an issue if you need it for a production workflow.

### attach_store when one is already attached

```python
# Construct a new Graph rather than re-attaching:
fresh = Graph()
fresh.attach_store(new_store)

# Or, to copy the existing run to a new store, use migration:
# activegraph migrate --from <old-url> --to <new-url>
```

A Graph's store attaches at most once per lifetime. Re-attaching
would split the event log across two stores (subsequent events
going to the new one, earlier events stuck in the old) or require
a copy operation that isn't an attach. Migration is the right
primitive for moving a run between stores.

## How to diagnose

The error names the operation and the current runtime state:

```
IncompatibleRuntimeState: runtime.fork() requires a SQLite-backed
runtime (current: PostgresEventStore)
```

From code:

```python
try:
    rt.fork(at_event=evt_id, label="...")
except IncompatibleRuntimeState as e:
    print(e.context["current_store_kind"])   # 'PostgresEventStore'
```

The `context` dict carries the current state so test code or
operator scripts can branch on it before invoking the operation.

## When does this fire

At the operation itself — `runtime.fork()`, `graph.attach_store()` —
not at runtime construction. The runtime constructs fine in both
cases; the constraint is on what the runtime can do, not what it
can be.

Multi-inherits `RuntimeError` for back-compat with code that
catches the builtin around runtime operations.

## Why the framework refuses to continue

Both raise sites protect runtime invariants that, if violated, would
corrupt the audit trail:

- Fork on non-SQLite would either skip the operation silently (no
  fork happens) or attempt a copy via a primitive the store doesn't
  support (partial copy that mixes runs).
- Re-attaching a store would split a single run's event log across
  two stores, making replay see only half.

Refusing the operation is the framework's way of asking the operator
to pick the right primitive (migrate, fresh Graph) instead of
discovering the data corruption later.

See [`failure-model`](../../concepts/failure-model.md) for the
broader principle.

## What's related

- [`invalid-runtime-configuration`](invalid-runtime-configuration.md)
  — sibling for argument-shape problems at construction
  (persist_to vs store, save_state path conflicts, recent<0).
- [`invalid-argument-type`](invalid-argument-type.md) — sibling
  for wrong-type values at construction (e.g., PostgresEventStore
  target).
- `activegraph migrate` in the [CLI reference](../cli/) — the
  canonical primitive for moving a run across stores.
