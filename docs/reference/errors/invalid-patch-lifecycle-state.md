# InvalidPatchLifecycleState

`graph.apply_patch(patch_id)` was called on a patch that isn't in
the `'proposed'` state. Patches are one-shot: a proposed patch
becomes `'applied'` (success) or `'rejected'` (refusal) exactly
once. Re-applying an already-applied patch — or applying one
that's been rejected — would either emit a duplicate
`patch.applied` event (breaking replay) or contradict an explicit
refusal.

This is an **exception**, not an event, because the caller has made
a mistake the caller can fix at the call site — see
[`failure-model`](../../concepts/failure-model.md) for the
events-not-exceptions principle and why patch-lifecycle violations
land on the exception side of the line.

## Quick fix

Check the patch's status before applying:

```python
patch = graph.get_patch(patch_id)
if patch is not None and patch.status == "proposed":
    graph.apply_patch(patch_id)
else:
    # Already applied, rejected, or in another terminal state.
    # Whatever you were going to do, the patch already did it (or
    # explicitly didn't). Don't re-apply.
    pass
```

If you genuinely need to apply a new mutation, propose a new
patch — patches aren't re-used:

```python
new_patch = graph.propose_patch(target, op="patch", diff={...})
graph.apply_patch(new_patch.id)
```

## The patch lifecycle (three sentences)

A patch is created in `'proposed'` state by `graph.propose_patch`.
`graph.apply_patch` transitions it to `'applied'` and emits a
`patch.applied` event; `graph.reject_patch` transitions it to
`'rejected'` and emits `patch.rejected`. Both transitions are
terminal — a patch leaves `'proposed'` exactly once.

For the full lifecycle including optimistic concurrency on object
versions and the policy-gating semantics, see
[`concepts/patches`](../../concepts/patches.md).

## How to diagnose

The error names the patch id and its current status:

```
InvalidPatchLifecycleState: patch patch_017 is 'applied', not 'proposed'
```

From code:

```python
try:
    graph.apply_patch(patch_id)
except InvalidPatchLifecycleState as e:
    print(e.patch_id)        # 'patch_017'
    print(e.current_status)  # 'applied' | 'rejected'
```

To see what happened to the patch:

```bash
activegraph inspect <store> --event <patch.applied or patch.rejected event id>
```

The status transition is in the event log — every `patch.applied`
and `patch.rejected` event names the patch it transitioned, so the
audit trail shows when and why the patch left `'proposed'`.

## When does this fire

At `graph.apply_patch(patch_id)`, after the patch is fetched and
its current status is read. The check follows the patch-exists check,
which raises [`ApplyPatchNotFoundError`](apply-patch-not-found-error.md)
if the id is unknown, so misuse is caught early.

The error never fires from `propose_patch`, `reject_patch`, or
`get_patch` — those are read-only or transition-initiating, not
transition-completing.

## Why the framework refuses to continue

Patches are one-shot. A `'proposed'` patch becomes `'applied'`
(success) or `'rejected'` (refusal) exactly once. Re-applying an
already-applied patch would emit a duplicate `patch.applied`
event, which would break the replay contract — replay would
produce a different event stream than the original run. The
framework refuses re-application rather than emit the duplicate.

This is why the error is an exception and not an event: the caller
can fix it (check status, propose a new patch). It's not a
non-fatal stop the runtime should record and continue past. See
[`failure-model`](../../concepts/failure-model.md) for the broader
principle.

## What's related

- [`concepts/patches`](../../concepts/patches.md) — the canonical
  patch lifecycle reference. Optimistic concurrency, policy
  gating, the apply/reject transitions.
- [`runtime-context-required-error`](runtime-context-required-error.md)
  — the sibling ExecutionError for "the caller is using the
  primitive outside its intended context."
- [`patch-not-found-error`](patch-not-found-error.md) — the shared catch
  for missing apply/reject patch ids. The apply leaf remains a `KeyError`;
  the reject leaf remains an `AttributeError`, preserving operation-specific
  legacy handlers.
- [`failure-model`](../../concepts/failure-model.md) — why patch
  lifecycle violations are exceptions, not events.


---

See [Observing failures in caller code](../../concepts/failure-model.md#observing-failures-in-caller-code)
for `Runtime.errors` and the `BehaviorFailure` shape.
