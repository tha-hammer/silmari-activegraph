# SandboxStartupError

The sandbox preflight child could not start or could not return its
`preflight: ok` marker. This is a setup failure at the parent/child boundary,
before candidate code begins a trial. The message includes the child's stderr
tail when one is available.

## Quick fix

Run preflight once during service startup and keep the reported cause:

```python
from activegraph import ConfigurationError
from activegraph.sandbox import SandboxStartupError, preflight

try:
    warnings = preflight()
except SandboxStartupError as exc:
    print(exc)
```

An import failure usually means the child cannot discover the installed
`activegraph` package. Preserve the sandbox's computed code-location channel;
do not solve it by forwarding the parent's full environment or secrets.

## Compatibility and hierarchy

`SandboxStartupError` inherits from `ConfigurationError`, `ActiveGraphError`,
and the built-in `RuntimeError`. New framework-level handlers can catch
`ConfigurationError` or `ActiveGraphError`; existing `RuntimeError` handlers
continue to work. The class remains available only from `activegraph.sandbox`.

This leaf has a narrow transitional rendering waiver. It still uses the legacy
one-message constructor, so `str(exc)` is the exact startup message,
`exc.args == (str(exc),)`, its structured fields and context are empty, and the
message has no `More:` block. That rendering is deprecated, with conversion to
the structured five-block format tracked separately for the next major release
in AF-wse. This ancestry-only change does not add exit-code, timeout, or stderr
fields.

The documentation identity is already stable:

```text
https://docs.activegraph.ai/errors/sandbox-startup-error
```

## When does this fire

Only `preflight()` raises this leaf after a child result is interpreted as a
startup failure. A successfully started preflight returns a tuple of resource
degradation warnings, which is empty when every requested net is available.

Failures during an actual `run_forked_trial()` are different: timeout, import
crash, materialization failure, and scenario failure are returned as a
`TrialReport` outcome. They are not rewritten as `SandboxStartupError`.

## What's related

- [Sandbox API](../api/sandbox.md) — preflight, trial inputs, executor adapter,
  and result types.
- [Failure model](../../concepts/failure-model.md) — the framework exception
  hierarchy and event-versus-exception boundary.
