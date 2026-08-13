# PatchNotFoundError

`PatchNotFoundError` is the stable shared `ExecutionError` category for a
missing patch proposal. Public operations raise one of its two concrete
leaves:

- [`ApplyPatchNotFoundError`](apply-patch-not-found-error.md) from
  `graph.apply_patch`;
- [`RejectPatchNotFoundError`](reject-patch-not-found-error.md) from
  `graph.reject_patch`.

Catch the shared category when both operations have the same recovery:

```python
try:
    transition_patch()
except PatchNotFoundError as error:
    print(error.patch_id)
```

The shared category deliberately inherits neither `KeyError` nor
`AttributeError`. Those compatibility bases belong to the operation leaves,
so applying a missing patch remains a `KeyError` only and rejecting one
remains an `AttributeError` only. This preserves ordered legacy handlers
without broadening either operation into the other's branch.

## Quick fix

Use the id returned by `graph.propose_patch()` from this graph and verify the
proposal still exists:

```python
patch = graph.get_patch(patch_id)
if patch is not None and patch.status == "proposed":
    graph.apply_patch(patch.id)
```

## What's related

- [ObjectNotFoundError](object-not-found-error.md) — direct object-patch
  target lookup miss.
- [InvalidPatchLifecycleState](invalid-patch-lifecycle-state.md) — the patch
  exists but is already terminal.
- [Patches](../../concepts/patches.md) — lifecycle and event semantics.
