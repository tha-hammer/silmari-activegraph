# RejectPatchNotFoundError

`graph.reject_patch(patch_id, reason)` could not find the proposed patch.
The call fails before target dereference, id allocation, or event emission.

The leaf is both [`PatchNotFoundError`](patch-not-found-error.md) and
`AttributeError`. Existing ordered handlers therefore keep selecting the
`AttributeError` branch; it is intentionally not a `KeyError`.

## Quick fix

```python
patch = graph.get_patch(patch_id)
if patch is not None and patch.status == "proposed":
    graph.reject_patch(patch.id, reason)
```

## Compatibility and diagnostics

The old incidental failure was
`AttributeError("'NoneType' object has no attribute 'target'")`, with the
same string in its one-element `args`. The typed leaf retains
`except AttributeError` routing, replaces that implementation detail with
the semantic `unknown patch: <id>` structured message, and sets
`args == (str(error),)`. Read `error.patch_id` or
`error.context["patch_id"]` instead of parsing text.

## What's related

- [ApplyPatchNotFoundError](apply-patch-not-found-error.md) — the apply
  operation's compatibility leaf.
- [InvalidPatchLifecycleState](invalid-patch-lifecycle-state.md) — a known
  patch is not proposed.
