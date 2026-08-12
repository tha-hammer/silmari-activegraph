# ApplyPatchNotFoundError

`graph.apply_patch(patch_id)` could not find the proposed patch. The call
fails before lifecycle validation, id allocation, or event emission.

The leaf is both [`PatchNotFoundError`](patch-not-found-error.md) and
`KeyError`. Existing ordered handlers therefore keep selecting the
`KeyError` branch; it is intentionally not an `AttributeError`.

## Quick fix

```python
patch = graph.get_patch(patch_id)
if patch is not None and patch.status == "proposed":
    graph.apply_patch(patch.id)
```

## Compatibility and diagnostics

The old failure was `KeyError("unknown patch: <id>")`, with
`args == ("unknown patch: <id>",)` and the quoted `KeyError` rendering.
The new leaf retains `except KeyError` routing but intentionally uses the
structured framework rendering and `args == (str(error),)`. Read
`error.patch_id` or `error.context["patch_id"]` instead of parsing text.

## What's related

- [RejectPatchNotFoundError](reject-patch-not-found-error.md) — the reject
  operation's compatibility leaf.
- [InvalidPatchLifecycleState](invalid-patch-lifecycle-state.md) — a known
  patch is not proposed.
