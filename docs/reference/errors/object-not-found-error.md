# ObjectNotFoundError

`graph.patch_object(object_id, updates)` could not find its target object.
The call fails before allocating a patch or event id and leaves graph state
unchanged.

`ObjectNotFoundError` is an `ExecutionError`, an `ActiveGraphError`, and a
`KeyError`. The `KeyError` base preserves existing handlers around
`patch_object`; new code can catch the typed leaf and read `object_id`.

## Quick fix

Use an id from the same graph and check it before patching:

```python
obj = graph.get_object(object_id)
if obj is not None:
    graph.patch_object(obj.id, updates)
```

If the id came from a stored run, load that run before applying the mutation.

## Compatibility and diagnostics

The old failure was `KeyError("unknown object: <id>")`, with
`args == ("unknown object: <id>",)` and the quoted `KeyError` rendering.
The typed leaf retains `except KeyError` routing but intentionally adopts the
structured framework rendering and `args == (str(error),)`. Its semantic
fields are available without parsing text:

```python
try:
    graph.patch_object(object_id, updates)
except ObjectNotFoundError as error:
    print(error.object_id)
    print(error.context["object_id"])
```

Inside a behavior, the runtime records the concrete
`ObjectNotFoundError` name in `behavior.failed`, `Runtime.errors`, logs, and
the `exception.ObjectNotFoundError` failure metric discriminator.

## What's related

- [PatchNotFoundError](patch-not-found-error.md) — stable shared catch for
  missing patch proposals.
- [InvalidPatchLifecycleState](invalid-patch-lifecycle-state.md) — a known
  patch exists but is no longer proposed.
- [Patches](../../concepts/patches.md) — patch creation and transitions.
