# Policies

A policy declares governance metadata for a pack or behavior. Object
approval is an explicit workflow: behavior code calls
`Context.propose_object`, the runtime creates an **approval** in
`proposed` state, and an operator decides whether the object lands.
Policies never intercept a direct `Graph.add_object` call.

Policies let the event and pending-approval record identify the pack that
owns the review convention. The behavior still chooses between an
immediate write and an operator-reviewed proposal. If pack settings make
that choice configurable, the behavior must branch explicitly; the runtime
does not switch or auto-approve the proposal path.

## What uses the approval lifecycle

These proposal lifecycles are explicit:

- **Object proposals via `ctx.propose_object(type, data, reason)`.**
  The framework always creates a pending approval and emits
  `approval.proposed`. The object lands only when the approval is granted.
- **Patches.** A patch declared as policy-gated takes the same
  proposed-and-approved path, except the patch lifecycle lives in
  the patch event types (`patch.proposed` / `patch.applied`)
  rather than approval event types. See [`patches`](patches.md)
  for the patch state machine.

Object proposals are the more common shape. The diligence pack's
`memo_approval` and `risk_approval` policies are the canonical examples:
the declarations attribute explicit memo and risk proposals to that pack.

Direct `graph.add_object`,
`graph.patch_object`, and `graph.emit` calls land immediately;
they are not rewritten by policy declarations. The behavior chooses
operator review by calling the proposal method instead of the direct method.

## The approval lifecycle

```
            proposed ──approve──> granted
                |
                └──deny────────> denied
```

Both transitions are one-shot. A proposed approval becomes granted
exactly once (via `runtime.approve(id, approved_by=...)`) or
denied exactly once (via `runtime.deny(id, denied_by=..., reason=...)`).
Calling either on an already-terminal approval raises
[`approval-not-found-error`](../reference/errors/approval-not-found-error.md)
— the approval id is consumed by the transition.

Each transition emits an event:

- `approval.proposed` — carries the proposal kind (`object` /
  `patch`), the type, the data, the reason from the proposing
  behavior, and the pack attributed by the first matching loaded policy.
- `approval.granted` — carries the approval id, the approver
  identity, and the resulting object id (or applied patch id).
- `approval.denied` — carries the approval id, the denier
  identity, and the denial reason.

The events sit in the log alongside everything else. Downstream
behaviors can subscribe to them; replay reconstructs the full
proposal-and-decision sequence; the trace renders them.

## Declaring policies

Packs declare policies as part of their `Pack(...)` declaration:

```python
from activegraph.packs import Pack, PackPolicy

pack = Pack(
    name="diligence",
    version="0.1.0",
    policies=[
        PackPolicy(
            name="memo_approval",
            requires_approval=["memo"],
        ),
        ...
    ],
    ...
)
```

`requires_approval` lists the object types for which the policy supplies
proposal-owner attribution. If multiple loaded policies list the same type,
the first loaded policy supplies the pack name. The declaration does not
gate `Graph.add_object` and does not create a proposal by itself.

A pack may expose a setting such as
`DiligenceSettings(auto_approve_memos=...)`, but behavior code owns its
meaning: it must choose a direct add or an explicit proposal. The runtime
does not inspect that setting or automatically grant proposals.

### Reserved `auto_apply` metadata

`PackPolicy.auto_apply` is reserved compatibility metadata and currently has
no runtime effect. List input is normalized to a tuple, but the loader and
runtime do not read it. Its values have no defined object-type, setting,
exemption, or automatic grant semantics. Do not use it to select direct writes,
exempt a type from review, or grant an explicit proposal. Those choices remain
in behavior and operator code until a future contract defines otherwise.

## How a behavior proposes

A behavior that wants its change to flow through a policy calls
`ctx.propose_object` instead of `graph.add_object`:

```python
@behavior(name="memo_synthesizer", on=["claim.completed"])
def memo_synthesizer(event, graph, ctx):
    ...
    ctx.propose_object(
        "memo",
        data={"title": "Diligence memo", "body": "..."},
        reason="diligence run complete",
    )
```

The propose call always queues the proposal and returns its approval id.
The behavior body completes; the approval lifecycle continues independently.

If the behavior tries `ctx.propose_object` outside a
runtime-bound context — typically a test fixture or a refactored
helper — it raises
[`runtime-context-required-error`](../reference/errors/runtime-context-required-error.md).

## The operator-facing recovery

Once a proposal is pending, the operator drives the lifecycle:

```python
for pa in rt.pending_approvals():
    print(pa.id, pa.kind, pa.object_type, pa.reason)

# Approve one:
rt.approve(approval_id, approved_by="operator-jane")

# Or deny:
rt.deny(approval_id, denied_by="operator-jane", reason="not yet")
```

The CLI surface for production approval workflows is in the
[operating guide](../guides/operating-in-production.md).

## The events-not-exceptions principle applied

A denied approval is an event (`approval.denied`), not an
exception. A behavior whose proposal gets denied doesn't see a
raised exception — it sees the denial in the event log if it
subscribes to `approval.denied`. The runtime continues; the
behavior author writes a retry-or-escalate behavior if denial
needs a response.

The exception case is misuse of the primitive — passing a
nonexistent approval id to `approve` / `deny` — which fires
`ApprovalNotFoundError`. See
[`failure-model`](failure-model.md) for the broader principle.

## What's related

- [`patches`](patches.md) — the durable-change primitive that
  policies gate. Approvals and patches share the proposed-and-
  decided shape; patches are the lower-level primitive.
- [`behaviors`](behaviors.md) — where `ctx.propose_object` is
  called from.
- [`failure-model`](failure-model.md) — why denials are events.
- [`approval-not-found-error`](../reference/errors/approval-not-found-error.md)
  — the exception for misuse of the approval API.
- [Operating in production](../guides/operating-in-production.md)
  — production workflows for the operator side.
