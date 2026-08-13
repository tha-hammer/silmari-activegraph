# DuplicateEventError

A store append failed because an event with the same id already
exists in the run. Event ids must be unique per run — duplicates
would silently reroute references that downstream code depends on,
corrupting the audit trail.

In normal use, this never fires. The runtime's id generator (IDGen)
is monotonic; events constructed through `graph.add_object`,
`graph.emit`, etc. always have fresh ids. The error is almost always
a test-fixture problem: hand-constructed events with fixed ids, plus
state left over from a previous test.

Multi-inherits `ValueError` for back-compat: user code that does
`except ValueError` around appends continues to work.

## Quick fix

If you're in test code:

```python
# Use IDGen to generate ids:
from activegraph import IDGen
ids = IDGen()
event = Event(id=ids.event(), type="my.event", payload={}, timestamp=...)

# Or call clear_registry() / construct a fresh Graph between tests:
from activegraph import clear_registry, Graph
clear_registry()
graph = Graph()
```

If you genuinely need fixed event ids in a fixture (e.g., for
snapshot tests), ensure each test gets a fresh store rather than
sharing state:

```python
@pytest.fixture
def fresh_store():
    # The same rule applies to any EventStore implementation.
    return InMemoryEventStore(run_id="run_test")
```

If this fires in production code, you've found a bug — the runtime
should never produce a duplicate id. File an issue with the run id
and the event id that collided.

## How to diagnose

The error message names the offending event id and the run:

```
DuplicateEventError: duplicate event id: evt_001

What failed:
  An event with id 'evt_001' already exists in run 'run_test'.
  Appends require unique (event id, run id) pairs.
```

From code:

```python
try:
    store.append(event)
except DuplicateEventError as e:
    print(e.context["event_id"])
    print(e.context["run_id"])
    print(e.context["backend"])
```

If the collision is in a test, check whether the test's setup tears
down state from the previous test — `pytest`'s function-scoped
fixtures are the canonical pattern; module-scoped or session-scoped
fixtures that hold an `InMemoryEventStore` will accumulate events
across tests and produce duplicates on the second run of any test
that constructs the same id.

## When does this fire

At `store.append()` only. Iteration, lookup, and read operations
can't produce duplicates — they're append-side only.

Every shipped EventStore reports this same public error. Backend-specific
encoding, connection, permission, and operational failures keep their native
exception types.

## Why the framework refuses to continue

Event ids are the addressing primitive for the entire framework.
Behaviors reference events by id, the replay cache keys on them,
the causal chain walks them. A duplicate id would silently reroute
one of those references, with the second-added event shadowing the
first or vice versa depending on store implementation. Either way,
the audit trail would record two events as one.

See [`failure-model`](../../concepts/failure-model.md) for the
broader principle.

## What's related

- [`EventNotFoundError`](event-not-found-error.md) — the sibling
  for the opposite failure: a lookup for an id that doesn't exist.
- `activegraph.IDGen` — the canonical id generator. Use it instead
  of hand-constructing ids in test fixtures unless you need fixed
  ids for a specific reason.


---

See [Observing failures in caller code](../../concepts/failure-model.md#observing-failures-in-caller-code)
for `Runtime.errors` and the `BehaviorFailure` shape.
