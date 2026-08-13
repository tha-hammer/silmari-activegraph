"""Live-Runtime tracking + single-behavior cross-provider validation.

CONTRACT v1.0.2 #1 (b). The validation fires at *both* binding
moments: ``Runtime(graph, llm_provider=...)`` construction (against
the existing registry) and ``register()`` / ``@llm_behavior``
decoration (against any live Runtime via the WeakSet below).

The WeakSet is module-level so the registration decorators can
look it up without importing ``Runtime`` at module load. Runtimes
self-register inside ``__init__`` after wiring. The WeakSet auto-cleans GC'd
Runtimes, and successful deterministic ``Runtime.close()`` removes its entry
immediately.

The single-behavior validator here is intentionally narrow: it
only does the cross-provider mismatch check (recognized name
belonging to a different shipped provider). It does *not* stamp
provider defaults onto ``model=None`` behaviors — that side
effect lives in ``_resolve_and_validate_llm_models`` in
``runtime.py``, which still runs at first ``_ensure_registry``.
"""

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING, Any

from activegraph.behaviors.base import LLMBehavior

if TYPE_CHECKING:
    from activegraph.runtime.runtime import Runtime


# WeakSet so abandoned Runtimes don't leak validation calls. The
# fork/replay paths construct fresh Runtimes that self-register; the
# parent stays in the set until GC'd.
_LIVE_RUNTIMES: "weakref.WeakSet[Runtime]" = weakref.WeakSet()


def track_runtime(rt: "Runtime") -> None:
    """Register a live Runtime for cross-provider validation. Called
    by ``Runtime.__init__`` after the provider and graph are wired."""
    _LIVE_RUNTIMES.add(rt)


def untrack_runtime(rt: "Runtime") -> None:
    """Remove a successfully closed Runtime from live validation."""

    _LIVE_RUNTIMES.discard(rt)


def live_runtimes() -> list["Runtime"]:
    """Snapshot of currently-alive Runtimes (a list of strong refs that
    the caller releases at end-of-call). Used by ``register()`` and the
    decorators to validate a new behavior against each live provider."""
    return list(_LIVE_RUNTIMES)


def _clear_for_test() -> None:
    """Empty the live-Runtime set. Test-only — used by the conftest
    fixture that isolates the global registry between tests. Production
    code shouldn't call this: the WeakSet cleans itself on GC."""
    _LIVE_RUNTIMES.clear()


def validate_behavior_against_live_runtimes(behavior: Any) -> None:
    """Validate a freshly-registered behavior against every live
    Runtime's provider. Raises :class:`InvalidRuntimeConfiguration` on
    the first cross-provider mismatch or capability-binding violation.

    This is the third of the three capability/cross-provider binding
    paths (CONTRACT v1.11 #1): the Runtime constructor's eager pass and
    ``Runtime._ensure_registry()`` (both in ``runtime.py``, via
    ``_resolve_and_validate_llm_models``/``_validate_llm_capability_binding``)
    cover construction time and first-run; this covers a behavior
    registered *after* a Runtime is already live. The capability check
    runs even when ``behavior.model is None`` — a deterministic
    behavior or a hard-cost-budget-incompatible provider is invalid
    regardless of which model name (or provider default) eventually
    resolves, so the cross-provider mismatch check's "no explicit
    model" early return does not gate it.
    """
    if not isinstance(behavior, LLMBehavior):
        return
    for rt in live_runtimes():
        provider = getattr(rt, "llm_provider", None)
        if provider is None:
            continue
        _validate_capability(behavior, provider, getattr(rt, "budget", None))
        if behavior.model is not None:
            _validate_one(behavior, provider)


def _validate_capability(behavior: LLMBehavior, provider: Any, budget: Any) -> None:
    """Capability-aware rejection (CONTRACT v1.11 #1, Behavior 1).
    Raises :class:`InvalidRuntimeConfiguration`, before any I/O, when:

    1. the provider requires generation-control acknowledgement and the
       caller didn't pass it at construction time;
    2. this behavior is ``deterministic=True`` while the provider can't
       actually honor sampling controls; or
    3. the Runtime has a hard ``max_cost_usd`` budget while the
       provider lacks either an enforceable output bound or an official
       (non-estimated) input token count — the budget gate's promise
       can't hold without both.

    Pure: no side effects, no I/O, no subprocess. Shares this module
    with ``_validate_one``'s cross-provider check (both binding-time
    validators for the same three call sites) rather than living
    separately in ``runtime.py``, since the live-registration path here
    needs the identical logic ``runtime.py``'s bulk pass uses.
    """
    from activegraph.llm.provider import get_llm_provider_capabilities
    from activegraph.runtime.config_errors import InvalidRuntimeConfiguration

    caps = get_llm_provider_capabilities(provider)
    provider_class = type(provider).__name__

    if caps.requires_generation_control_acknowledgement and not getattr(
        provider, "allow_unenforced_generation_controls", False
    ):
        raise InvalidRuntimeConfiguration(
            f"{provider_class} requires an explicit acknowledgement before "
            f"it can be bound to any @llm_behavior",
            what_failed=(
                f"{provider_class} declares "
                f"llm_capabilities.requires_generation_control_acknowledgement=True "
                f"(it cannot enforce max_tokens/temperature/top_p), but was "
                f"constructed without allow_unenforced_generation_controls=True."
            ),
            why=(
                "A provider whose backing SDK can't actually enforce the "
                "generation controls @llm_behavior/Runtime pass through "
                "must never silently accept them — the caller has to "
                "explicitly acknowledge the gap before Runtime will bind "
                "any behavior to it."
            ),
            how_to_fix=(
                f"Construct {provider_class}(allow_unenforced_generation_controls=True) "
                f"after reading its capability-limited contract in "
                f"docs/reference/llm-providers.md."
            ),
            context={"configured_provider": provider_class},
        )

    if behavior.deterministic and not caps.supports_sampling_controls:
        raise InvalidRuntimeConfiguration(
            f"@llm_behavior(name={behavior.name!r}, deterministic=True) "
            f"cannot bind to {provider_class}",
            what_failed=(
                f"The behavior {behavior.name!r} is declared "
                f"deterministic=True, but {provider_class} declares "
                f"llm_capabilities.supports_sampling_controls=False — it "
                f"has no temperature/top_p control at all."
            ),
            why=(
                "Binding a deterministic behavior to a provider that "
                "cannot honor determinism would silently produce "
                "non-deterministic output while claiming determinism — "
                "Runtime refuses the binding instead."
            ),
            how_to_fix=(
                f"Use a provider with supports_sampling_controls=True "
                f"(AnthropicProvider, OpenAIProvider) for this behavior, "
                f"or set deterministic=False if determinism isn't "
                f"actually required."
            ),
            context={"behavior": behavior.name, "configured_provider": provider_class},
        )

    has_cost_limit = getattr(budget, "has_cost_limit", None)
    if (
        budget is not None
        and callable(has_cost_limit)
        and has_cost_limit()
        and (not caps.enforces_max_tokens or caps.input_token_count != "official")
    ):
        raise InvalidRuntimeConfiguration(
            f"a hard max_cost_usd budget cannot bind to {provider_class}",
            what_failed=(
                f"This Runtime has budget={{'max_cost_usd': ...}} set, but "
                f"{provider_class} declares enforces_max_tokens="
                f"{caps.enforces_max_tokens} and input_token_count="
                f"{caps.input_token_count!r} — the pre-call budget gate "
                f"needs both an enforceable output bound and an official "
                f"(non-estimated) input token count to back its promise."
            ),
            why=(
                "A hard cost ceiling that silently relies on a heuristic "
                "token count and an unenforceable output bound could be "
                "exceeded without Runtime ever knowing — refusing the "
                "binding is safer than a budget guarantee that doesn't "
                "hold."
            ),
            how_to_fix=(
                f"Drop max_cost_usd from this Runtime's budget= when using "
                f"{provider_class}, or switch to a provider with an "
                f"official token count and an enforceable output bound "
                f"for hard-cost-budgeted runs."
            ),
            context={"configured_provider": provider_class},
        )


def _validate_one(behavior: LLMBehavior, provider: Any) -> None:
    """Cross-provider mismatch check for a single behavior against a
    single provider. Pure: no side effects, no model-default stamping.

    Permissive by default per v1.0.2 #1 (b): names no shipped provider
    recognizes pass silently. Only recognized cross-provider mismatches
    raise. The runtime-side ``_resolve_and_validate_llm_models``
    delegates to this for its per-behavior pass so the check lives in
    one place.
    """
    from activegraph.runtime.config_errors import InvalidRuntimeConfiguration

    model = behavior.model
    if model is None:
        return
    recognizes = getattr(provider, "recognizes_model", None)
    if recognizes is None or recognizes(model):
        return
    claimed_by = _which_shipped_provider_claims(model, exclude=type(provider))
    if not claimed_by:
        return

    provider_class = type(provider).__name__
    provider_default = getattr(provider, "default_model", None) or "claude-sonnet-4-5"
    # v1.11 #1 (Behavior 12): name every matching provider, not just the
    # first — a model can legitimately belong to more than one shipped
    # provider's family (e.g. claude-* belongs to both AnthropicProvider
    # and ClaudeCodeProvider), and silently picking one would hide the
    # other from the diagnostic hint. Structured context uses plural
    # claiming_provider_names/claiming_provider_defaults; the deprecated
    # singular claimed_by_provider/claimed_by_default_model keys are
    # included only in the one-match case, for backward compatibility.
    claiming_provider_names = [c.__name__ for c in claimed_by]
    claiming_provider_defaults = [getattr(c, "default_model", "") for c in claimed_by]
    claimed_by_names = " or ".join(claiming_provider_names)
    provider_default_hint = (
        f"or remove the model= argument to use {provider_class}'s "
        f"default ({provider_default!r})"
        if provider_default
        else f"or set a {provider_class}-compatible model name"
    )
    context: dict[str, Any] = {
        "behavior": behavior.name,
        "model": model,
        "configured_provider": provider_class,
        "claiming_provider_names": claiming_provider_names,
        "claiming_provider_defaults": claiming_provider_defaults,
    }
    if len(claimed_by) == 1:
        context["claimed_by_provider"] = claiming_provider_names[0]
        context["claimed_by_default_model"] = claiming_provider_defaults[0]
    raise InvalidRuntimeConfiguration(
        (
            f"@llm_behavior(name={behavior.name!r}, model={model!r}) "
            f"names a {claimed_by_names}-family model, but the "
            f"runtime is configured with {provider_class}"
        ),
        what_failed=(
            f"The behavior {behavior.name!r} pinned model={model!r}. "
            f"That name belongs to {claimed_by_names}'s model "
            f"family, but this Runtime was constructed with a "
            f"{provider_class} instance. Sending the name to the "
            f"wrong provider produces an HTTP 404 (or equivalent "
            f"'unknown model' response) at first LLM call, with no "
            f"hint that the mismatch is the cause."
        ),
        why=(
            "v1.0.2 #1 validates explicit model names at both binding "
            "moments (Runtime construction and register()/decoration) "
            "against each shipped provider's recognizes_model() "
            "method. The configured provider doesn't claim this name, "
            "but another shipped provider does — that's a "
            "configuration mismatch worth surfacing before the first "
            "network call rather than after."
        ),
        how_to_fix=(
            f"Either swap the provider — did you mean {claimed_by_names}? "
            f"{provider_default_hint}, or pass an explicit name "
            f"from {provider_class}'s model families."
        ),
        context=context,
    )


def _which_shipped_provider_claims(name: str, *, exclude: type) -> list[type]:
    """Return every shipped provider class that recognizes `name`,
    excluding `exclude`. Returns `[]` when no other shipped provider
    claims the name (permissive default per v1.0.2 #1 (b)).

    v1.11 #1 (review I1): returns **all** matches, not the first.
    `AnthropicProvider` and `ClaudeCodeProvider` both legitimately claim
    every `claude-*` name — returning only the first match made
    `ClaudeCodeProvider` unreachable dead code once it was appended
    after `AnthropicProvider` in `candidates`, since the loop below
    never got past the first hit.
    """
    from activegraph.llm.anthropic import AnthropicProvider
    from activegraph.llm.claude_code import ClaudeCodeProvider
    from activegraph.llm.openai import OpenAIProvider
    from activegraph.llm.openrouter import OpenRouterProvider

    candidates = [
        AnthropicProvider,
        OpenAIProvider,
        ClaudeCodeProvider,
        OpenRouterProvider,
    ]
    matches: list[type] = []
    for cls in candidates:
        if cls is exclude:
            continue
        recognizes = getattr(cls, "recognizes_model", None)
        if recognizes is None:
            continue
        try:
            inst = cls()
        except Exception:
            # Defensive: if a provider's no-arg constructor ever requires
            # real credentials, skip it for the validation lookup.
            continue
        if inst.recognizes_model(name):
            matches.append(cls)
    return matches
