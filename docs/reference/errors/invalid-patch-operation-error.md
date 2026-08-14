# InvalidPatchOperationError

`graph.propose_patch(op=...)` was called with an `op` outside
`{"update", "replace"}`. Object creation and removal are not patch
ops — those are handled directly by `Graph.add_object` and
`Graph.remove_object`, which are already the first-class, dedicated
paths for creating and removing objects. A patch's job is a
targeted, version-checked mutation of an *existing* object's data.

This is an **exception**, not an event, because the caller has made
a mistake the caller can fix at the call site — see
[`failure-model`](../../concepts/failure-model.md) for the
events-not-exceptions principle.

## Quick fix

Use `op="update"` or `op="replace"`:

```python
graph.propose_patch(
    target=obj.id,
    op="update",
    value={"status": "closed"},
    proposed_by="planner",
)
```

To create or remove an object, call `graph.add_object(...)` or
`graph.remove_object(...)` directly instead of proposing a patch:

```python
graph.add_object("task", {"status": "open"})
graph.remove_object(obj.id)
```

## How to diagnose

The error names the offending `op` and the valid set:

```
InvalidPatchOperationError: patch op 'create' is not one of: replace, update
```

From code:

```python
try:
    graph.propose_patch(target=obj.id, op="create", value={...}, proposed_by="x")
except InvalidPatchOperationError as e:
    print(e.op)         # 'create'
    print(e.valid_ops)  # {'update', 'replace'}
```

## When does this fire

At `graph.propose_patch(...)`, before any patch, event, or state
mutation happens — a rejected `op` has zero side effects: no
`patch.proposed` event is emitted, no patch is created, and the id
counters are unchanged.

The error never fires from `patch_object`, which always passes
`op="update"` internally.

## What's related

- [`invalid-patch-lifecycle-state`](invalid-patch-lifecycle-state.md)
  — the sibling patch-lifecycle leaf: a status-transition violation
  on an already-proposed patch, rather than a taxonomy violation at
  proposal time.
- [`concepts/patches`](../../concepts/patches.md) — the canonical
  patch lifecycle reference.
- [`failure-model`](../../concepts/failure-model.md) — why this is
  an exception, not an event.

---

See [Observing failures in caller code](../../concepts/failure-model.md#observing-failures-in-caller-code)
for `Runtime.errors` and the `BehaviorFailure` shape.
