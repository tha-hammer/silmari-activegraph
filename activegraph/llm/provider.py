"""The `LLMProvider` Protocol every provider implements.

CONTRACT v0.6 #3, extended in v0.7, additively widened in v1.0.2 #1.
Narrow, explicit, keyword-only. Shipped concrete implementations are
`AnthropicProvider`, `OpenAIProvider`, `ClaudeCodeProvider`, and
`OpenRouterProvider`. Tests use
`RecordedLLMProvider` + `RecordingLLMProvider`. The demo ships its
own scripted provider.

A provider does three things plus two declarations:

  * `complete()`: run a single non-streaming completion. v0.7 adds
    an optional `tools=` parameter; when non-empty, the model is
    allowed to return tool_use blocks in the response.
  * `estimate_cost()`: turn token counts into USD (Decimal).
  * `count_tokens()`: an input token count for the prompt that's about
    to be sent — used for pre-call budget gating when
    `budget.max_cost_usd` is set; otherwise skipped (see CONTRACT v0.6
    #4 / decision 10). Whether the count is provider-official or a
    local estimate is declared by `llm_capabilities.input_token_count`
    (v1.11 #1) — `Runtime`'s hard-cost-budget gate requires an
    `"official"` count (plus `enforces_max_tokens=True`) before it will
    bind a provider to a run with `max_cost_usd` set, since a heuristic
    count paired with an unenforceable output ceiling can't back the
    gate's promise.
  * `default_model`: the model name to use when an `@llm_behavior`
    didn't pin one (v1.0.2 #1).
  * `recognizes_model(name)`: True when `name` belongs to a model
    family this provider serves. Used by the runtime at
    registration time to flag cross-provider mismatches before the
    first network call (v1.0.2 #1).

`default_model` and `recognizes_model` are additive: the runtime
guards their use with `getattr(...)`, so custom providers that
pre-date v1.0.2 keep working — they just require an explicit
`model=` on every `@llm_behavior` and don't participate in
cross-provider validation. `llm_capabilities` (v1.11 #1,
:class:`LLMProviderCapabilities`) is additive the same way: absent on
a provider, it resolves to :data:`FULL_LLM_PROVIDER_CAPABILITIES` via
:func:`get_llm_provider_capabilities`, so every provider written before
this model existed keeps its historical full-parity behavior
unchanged. The locked `complete()`/`count_tokens()` signatures below
never gain new parameters for a capability-limited provider to accept
(no `deterministic=` on `complete()`, no `tools=` on `count_tokens()`)
— capability limits are declared data, checked by `Runtime` before a
provider is ever called, not per-call keyword arguments a provider
must recognize.

No streaming, no multi-model orchestration — those are deferred to
v0.8+. Tool use IS in v0.7, but the loop is orchestrated by the
runtime, not the provider: provider returns `tool_calls`, runtime
invokes the tool, runtime re-calls `complete()` with the result
echoed back as a `role="tool"` message.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Optional, Protocol, runtime_checkable

from activegraph.llm.types import LLMMessage, LLMResponse


@dataclass(frozen=True)
class LLMProviderCapabilities:
    """Additive, provider-declared capability descriptor. CONTRACT
    v1.11 #1.

    Not a member of the :class:`LLMProvider` Protocol — a provider that
    doesn't declare ``llm_capabilities`` (every provider that pre-dates
    this) resolves to :data:`FULL_LLM_PROVIDER_CAPABILITIES` via
    :func:`get_llm_provider_capabilities`, so existing custom providers
    stay structurally compatible and behave exactly as before. Declared
    by a provider (e.g. `ClaudeCodeProvider`) whose backing SDK cannot
    actually enforce everything the locked `complete()`/`count_tokens()`
    signatures imply; `Runtime` reads this at registration time
    (`runtime.py`'s capability-binding validation, alongside
    `_live.py`'s cross-provider mismatch check) to reject configurations
    the provider cannot honor — before any subprocess or network call.

    Fields:
      enforces_max_tokens: True when the provider can actually cap a
        single turn's output length. False means `max_tokens` is
        accepted for Protocol conformance but not enforceable.
      supports_sampling_controls: True when `temperature`/`top_p`
        actually constrain generation. False means an
        `@llm_behavior(deterministic=True)` cannot be honored and
        `Runtime` must reject binding it to this provider rather than
        silently degrading to non-deterministic output that claims
        determinism.
      input_token_count: `"official"` when `count_tokens()` is backed by
        a real provider-side counting endpoint; `"estimate"` when it's
        a local heuristic. `Runtime`'s hard `max_cost_usd` pre-call
        budget gate requires `"official"` counts plus an enforceable
        output bound — both must hold for the gate's promise to mean
        anything.
      max_tool_calls_per_completion: `None` means no declared limit
        (the historical, unlimited-batch shape every existing provider
        implicitly claims). An integer declares a hard per-`complete()`
        ceiling (e.g. `1` for a provider whose backing SDK can only
        defer a single tool call at a time).
      requires_generation_control_acknowledgement: True means a caller
        must explicitly opt in (a provider-specific constructor flag)
        before `Runtime` will bind this provider to any `LLMBehavior` —
        used when `enforces_max_tokens`/`supports_sampling_controls`
        are `False`, so silently accepting unenforceable generation
        controls is never the default.
    """

    enforces_max_tokens: bool = True
    supports_sampling_controls: bool = True
    input_token_count: Literal["official", "estimate"] = "official"
    max_tool_calls_per_completion: Optional[int] = None
    requires_generation_control_acknowledgement: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "enforces_max_tokens": self.enforces_max_tokens,
            "supports_sampling_controls": self.supports_sampling_controls,
            "input_token_count": self.input_token_count,
            "max_tool_calls_per_completion": self.max_tool_calls_per_completion,
            "requires_generation_control_acknowledgement": (
                self.requires_generation_control_acknowledgement
            ),
        }


# The full-parity default every provider implicitly claimed before this
# capability model existed. `AnthropicProvider`/`OpenAIProvider` never
# declare `llm_capabilities` and resolve to this via
# `get_llm_provider_capabilities` — zero behavior change for them.
FULL_LLM_PROVIDER_CAPABILITIES = LLMProviderCapabilities()


def get_llm_provider_capabilities(provider: object) -> LLMProviderCapabilities:
    """Read a provider's declared capabilities, defaulting to full
    parity when absent. `Runtime` calls this at every capability-
    binding point instead of reading ``llm_capabilities`` directly, so
    a missing attribute never raises ``AttributeError``.
    """
    return getattr(provider, "llm_capabilities", FULL_LLM_PROVIDER_CAPABILITIES)


@runtime_checkable
class LLMProvider(Protocol):
    # v1.0.2 #1: declared as an attribute on the Protocol. Concrete
    # providers set it as a class attribute; custom providers may
    # omit it (the runtime falls back to getattr-with-default).
    default_model: str

    def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
        output_schema: Optional[type],
        timeout_seconds: float,
        tools: Optional[list[dict[str, Any]]] = None,
        structured_output_mode: str = "prompt",
    ) -> LLMResponse:
        ...

    def estimate_cost(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> Decimal:
        ...

    def count_tokens(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
    ) -> int:
        ...

    def supports_native_structured_output(self, model: str) -> bool:
        """True when the provider can enforce ``output_schema`` natively
        for ``model`` (constrained decoding). CONTRACT v1.3 #1.

        Additive like ``default_model`` / ``recognizes_model``: the
        runtime guards the lookup with ``getattr(...)``, so custom
        providers that pre-date v1.3 keep working and simply resolve to
        the prompt-embedded path. When this returns True for the
        resolved model (and the schema passes the offline pre-flight),
        the runtime calls ``complete()`` with
        ``structured_output_mode="native"``; prompt-mode calls omit the
        parameter entirely, staying byte-identical to pre-v1.3 calls.
        """
        ...

    def recognizes_model(self, name: str) -> bool:
        """True when `name` belongs to a model family this provider serves.

        v1.0.2 #1. Used at registration time to flag cross-provider
        mismatches (e.g. ``model="claude-sonnet-4-5"`` on a runtime
        configured with ``OpenAIProvider``). Permissive by default:
        unknown names — fine-tuned models, internal deployment names,
        experimental prefixes — should return False so the runtime
        passes them through without a diagnostic.
        """
        ...
