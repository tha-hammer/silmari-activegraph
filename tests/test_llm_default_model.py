"""Provider-aware default model + cross-provider validation. CONTRACT v1.0.2 #1.

Covers:

  * ``@llm_behavior`` with no ``model=`` resolves to the configured
    provider's ``default_model`` at registration time.
  * Both shipped providers declare ``default_model``.
  * Cross-provider mismatches (claude-* on OpenAI, gpt-* on Anthropic)
    raise ``InvalidRuntimeConfiguration`` at registration time, with
    a structured error naming the configured provider and the
    claiming provider.
  * Unknown model names (custom, fine-tuned, internal-deployment)
    pass through silently — the validation is permissive by default.
  * Explicit ``model=`` strings that match the configured provider's
    family pass through unchanged.
  * ``LLMBehavior.build_prompt`` keeps working without a Runtime
    (CONTRACT v0.6 #20), falling back to the v1.0.1 default for
    inspection-time hash stability.
"""

from __future__ import annotations

import pytest

from activegraph import (
    Graph,
    InvalidRuntimeConfiguration,
    Runtime,
    behavior,
    llm_behavior,
)
from activegraph.llm import AnthropicProvider, ClaudeCodeProvider, OpenAIProvider

from tests._llm_helpers import ClaimList, ScriptedProvider


def _scripted(default_model: str = "claude-sonnet-4-5"):
    p = ScriptedProvider(
        respond_fn=lambda messages, schema: ClaimList(claims=[]),
    )
    p.default_model = default_model
    return p


# ---- (a) default_model resolution ------------------------------------------


def test_default_model_resolves_at_registration_when_decorator_omits_it():
    """@llm_behavior with no model= picks up the provider's default."""

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    # Decorator did not pin a model.
    assert extractor.model is None

    provider = _scripted(default_model="claude-haiku-4-5")
    g = Graph()
    rt = Runtime(g, llm_provider=provider)
    rt._ensure_registry()

    # Registration stamped the provider default onto the behavior.
    assert extractor.model == "claude-haiku-4-5"


def test_anthropic_provider_default_model_is_claude_family():
    """AnthropicProvider declares a claude-family default. v1.0.2 #1 (a)."""
    p = AnthropicProvider()
    assert p.default_model.startswith("claude-")


def test_openai_provider_default_model_is_gpt_family():
    """OpenAIProvider declares a gpt-family default. v1.0.2 #1 (a)."""
    p = OpenAIProvider()
    assert p.default_model.startswith("gpt-")


def test_explicit_model_string_still_works_unchanged():
    """Pre-v1.0.2 call sites with model='...' stay byte-identical."""

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
        model="claude-sonnet-4-5",
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    assert extractor.model == "claude-sonnet-4-5"

    provider = _scripted(default_model="claude-haiku-4-5")
    g = Graph()
    rt = Runtime(g, llm_provider=provider)
    rt._ensure_registry()

    # Explicit string overrides the provider default.
    assert extractor.model == "claude-sonnet-4-5"


# ---- (b) cross-provider mismatch validation ---------------------------------


def test_claude_model_on_openai_runtime_raises_at_runtime_construction():
    """The v1.0.1-user-test finding: claude-sonnet-4-5 + OpenAIProvider
    silently 404s. v1.0.2.post1 surfaces it at Runtime construction
    when the behavior is already registered."""

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
        model="claude-sonnet-4-5",
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    # Use a fake OpenAI client so the provider doesn't try to load the SDK.
    provider = OpenAIProvider(client=object())
    g = Graph()

    with pytest.raises(InvalidRuntimeConfiguration) as excinfo:
        Runtime(g, llm_provider=provider)

    msg = str(excinfo.value)
    assert "claude-sonnet-4-5" in msg
    assert "OpenAIProvider" in msg
    assert "AnthropicProvider" in msg
    # The error names the way out: swap providers or use the default.
    assert "gpt-4o-mini" in msg or "OpenAIProvider's model families" in msg


def test_claude_model_on_openai_runtime_hint_names_both_claude_family_providers():
    """v1.11 #1 (Behavior 14, review I1): AnthropicProvider and
    ClaudeCodeProvider both legitimately claim claude-* names.
    _which_shipped_provider_claims must return (and the hint must
    name) both — silently picking one, or letting ClaudeCodeProvider's
    late position in the candidates list make it unreachable, was the
    original bug this behavior fixes."""

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
        model="claude-sonnet-4-5",
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    provider = OpenAIProvider(client=object())
    g = Graph()

    with pytest.raises(InvalidRuntimeConfiguration) as excinfo:
        Runtime(g, llm_provider=provider)

    msg = str(excinfo.value)
    assert "AnthropicProvider" in msg
    assert "ClaudeCodeProvider" in msg


def test_gpt_model_on_anthropic_runtime_raises_at_runtime_construction():
    """Symmetric: gpt-4o-mini against AnthropicProvider."""

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
        model="gpt-4o-mini",
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    provider = AnthropicProvider(client=object())
    g = Graph()

    with pytest.raises(InvalidRuntimeConfiguration) as excinfo:
        Runtime(g, llm_provider=provider)

    msg = str(excinfo.value)
    assert "gpt-4o-mini" in msg
    assert "AnthropicProvider" in msg
    assert "OpenAIProvider" in msg


def test_o3_model_on_anthropic_runtime_raises_at_runtime_construction():
    """OpenAI's reasoning-model prefixes (o1-/o3-/o4-) also fire."""

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
        model="o3-mini",
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    provider = AnthropicProvider(client=object())
    g = Graph()

    with pytest.raises(InvalidRuntimeConfiguration) as excinfo:
        Runtime(g, llm_provider=provider)

    assert "o3-mini" in str(excinfo.value)


# ---- (c) permissive default: unknown names pass ----------------------------


def test_unknown_model_name_does_not_raise():
    """Custom, fine-tuned, and internal-deployment names pass validation
    silently — only recognized cross-provider mismatches raise."""

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
        model="my-custom-model",
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    # Either shipped provider accepts the unrecognized name.
    for provider in (
        AnthropicProvider(client=object()),
        OpenAIProvider(client=object()),
    ):
        g = Graph()
        rt = Runtime(g, llm_provider=provider)
        rt._ensure_registry()  # must not raise
        assert extractor.model == "my-custom-model"


def test_finetuned_openai_model_name_passes_on_openai_runtime():
    """A fine-tune like ft:gpt-4o-mini:org::id starts with 'ft:', not
    'gpt-', so recognizes_model returns False. The runtime passes it
    through because no *other* shipped provider claims it either."""

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
        model="ft:gpt-4o-mini:my-org::abc123",
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    provider = OpenAIProvider(client=object())
    g = Graph()
    rt = Runtime(g, llm_provider=provider)
    rt._ensure_registry()

    assert extractor.model == "ft:gpt-4o-mini:my-org::abc123"


# ---- (d) recognizes_model unit checks --------------------------------------


def test_anthropic_recognizes_model_claude_family():
    p = AnthropicProvider(client=object())
    assert p.recognizes_model("claude-sonnet-4-5")
    assert p.recognizes_model("claude-opus-4-7")
    assert p.recognizes_model("claude-haiku-4-5-20251001")
    assert not p.recognizes_model("gpt-4o-mini")
    assert not p.recognizes_model("o3-mini")
    assert not p.recognizes_model("my-custom-model")


def test_claude_code_recognizes_model_claude_family():
    p = ClaudeCodeProvider()
    assert p.recognizes_model("claude-sonnet-4-5")
    assert p.recognizes_model("claude-opus-4-7")
    assert not p.recognizes_model("gpt-4o-mini")
    assert not p.recognizes_model("my-custom-model")


def test_openai_recognizes_model_gpt_and_reasoning_families():
    p = OpenAIProvider(client=object())
    assert p.recognizes_model("gpt-4o-mini")
    assert p.recognizes_model("gpt-4o")
    assert p.recognizes_model("gpt-4-turbo")
    assert p.recognizes_model("gpt-3.5-turbo")
    assert p.recognizes_model("o1-preview")
    assert p.recognizes_model("o3-mini")
    assert p.recognizes_model("o4-mini")
    assert not p.recognizes_model("claude-sonnet-4-5")
    assert not p.recognizes_model("my-custom-model")
    # Fine-tunes have an ft: prefix; not recognized as base family.
    assert not p.recognizes_model("ft:gpt-4o-mini:org::id")


# ---- (e) build_prompt without Runtime --------------------------------------


def test_build_prompt_without_runtime_uses_inspection_default():
    """CONTRACT v0.6 #20: build_prompt is callable without a Runtime.
    When model is None, falls back to the v1.0.1 default for hash
    stability against pre-v1.0.2 snapshots."""

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    g = Graph()
    g.add_object("document", {"title": "T", "body": "B"})
    ev = next(e for e in g.events if e.type == "object.created")
    prompt = extractor.build_prompt(ev, g)

    # No Runtime was constructed, so model is still None on the behavior.
    assert extractor.model is None
    # build_prompt falls back to the v1.0.1 hardcoded default.
    assert prompt.model == "claude-sonnet-4-5"


# ---- (f) end-to-end: the user-test reproducer is now caught early ----------


# ---- (g) v1.0.2.post1: both binding moments fire validation ----------------


def test_decorator_after_runtime_construction_raises_at_decoration_time():
    """The README quickstart pattern: construct Runtime first (empty
    registry), then decorate behaviors. v1.0.2.post1 catches the
    mismatch at the @llm_behavior line via the live-Runtimes WeakSet."""

    provider = OpenAIProvider(client=object())
    g = Graph()
    Runtime(g, llm_provider=provider)  # registry is empty; construction OK

    with pytest.raises(InvalidRuntimeConfiguration) as excinfo:

        @llm_behavior(
            name="extractor",
            on=["object.created"],
            description="extract",
            output_schema=ClaimList,
            model="claude-sonnet-4-5",
        )
        def extractor(event, graph, ctx, llm_output):
            pass

    msg = str(excinfo.value)
    assert "claude-sonnet-4-5" in msg
    assert "OpenAIProvider" in msg
    assert "AnthropicProvider" in msg


def test_register_after_runtime_construction_raises_at_register_time():
    """Same shape as the decorator path, but via the public register()
    function: construct Runtime first, then register a behavior with a
    cross-provider model name. The raise lands at register() time."""

    provider = OpenAIProvider(client=object())
    g = Graph()
    Runtime(g, llm_provider=provider)

    # Build a behavior in isolation — decorate against a runtime-free
    # registry, then clear it so the next call site re-registers.
    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
        # Use a name no shipped provider claims so decoration itself
        # passes; the test exercises register(), not the decorator path.
        model="my-custom-model",
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    # Now mutate the model to a cross-provider name and try to
    # re-register via the public API. (Direct mutation models a script
    # that captured behaviors and is re-registering them after a
    # clear_registry() — the v1.0.1 multi-run pattern.)
    extractor.model = "claude-sonnet-4-5"

    from activegraph import clear_registry, register
    clear_registry()

    with pytest.raises(InvalidRuntimeConfiguration) as excinfo:
        register(extractor)

    assert "claude-sonnet-4-5" in str(excinfo.value)
    assert "OpenAIProvider" in str(excinfo.value)


def test_readme_quickstart_pattern_works_when_models_match():
    """Construct Runtime first against an empty registry, then decorate
    a behavior with a model the configured provider recognizes. No
    exception — the README quickstart's intended ordering is preserved
    for the matching case."""

    provider = OpenAIProvider(client=object())
    g = Graph()
    rt = Runtime(g, llm_provider=provider)

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
        model="gpt-4o-mini",
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    # Decoration succeeded; behavior carries its explicit model.
    assert extractor.model == "gpt-4o-mini"
    # And the lazy path stays clean too.
    rt._ensure_registry()


def test_decorator_without_model_after_runtime_passes_unchanged():
    """README quickstart pattern with `model=` omitted on the decorator:
    no Runtime to validate against at decoration time (the behavior's
    model is still None), so the decorator passes. The runtime stamps
    the provider default at _ensure_registry time, same as today."""

    provider = OpenAIProvider(client=object())
    g = Graph()
    rt = Runtime(g, llm_provider=provider)

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    # Decoration succeeded with model=None.
    assert extractor.model is None
    # _ensure_registry stamps the provider's default_model.
    rt._ensure_registry()
    assert extractor.model == "gpt-4o-mini"


def test_two_runtimes_with_different_providers_validate_against_both():
    """Construct two Runtimes with different providers. A behavior that
    conflicts with one but matches the other raises at register-time,
    naming the conflicting provider."""

    a = AnthropicProvider(client=object())
    o = OpenAIProvider(client=object())
    g1 = Graph()
    g2 = Graph()
    Runtime(g1, llm_provider=a)
    Runtime(g2, llm_provider=o)

    # claude-* matches the Anthropic runtime but not the OpenAI one.
    # The OpenAI runtime's validation fires.
    with pytest.raises(InvalidRuntimeConfiguration) as excinfo:

        @llm_behavior(
            name="extractor",
            on=["object.created"],
            description="extract",
            output_schema=ClaimList,
            model="claude-sonnet-4-5",
        )
        def extractor(event, graph, ctx, llm_output):
            pass

    msg = str(excinfo.value)
    assert "OpenAIProvider" in msg
    assert "claude-sonnet-4-5" in msg


def test_failed_runtime_construction_does_not_leak_into_live_set():
    """A Runtime whose __init__ raises must not appear in the live-set.
    Otherwise the next test's @llm_behavior could validate against a
    half-constructed object whose exception traceback still holds a
    strong reference to it."""

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
        model="claude-sonnet-4-5",
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    bad_provider = OpenAIProvider(client=object())
    g = Graph()
    with pytest.raises(InvalidRuntimeConfiguration):
        Runtime(g, llm_provider=bad_provider)

    # The failed runtime didn't enter the live-set, so this second
    # decoration against the same conflicting model would only raise
    # if some OTHER live runtime had the OpenAI provider. None does
    # (the conftest cleared things between tests; the only attempted
    # Runtime above failed). So the decoration succeeds.
    from activegraph import clear_registry
    clear_registry()

    @llm_behavior(
        name="extractor2",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
        model="claude-sonnet-4-5",
    )
    def extractor2(event, graph, ctx, llm_output):
        pass

    assert extractor2.model == "claude-sonnet-4-5"


def test_fork_inherits_parent_provider_no_extra_validation_failures(tmp_path):
    """Fork constructors construct a fresh Runtime that re-runs
    validation. Since forks inherit the parent's provider, validation
    is a no-op on the happy path — verified cheap before adding to the
    fork constructor's hot path."""
    from activegraph import SQLiteEventStore

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
        model="claude-sonnet-4-5",
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    provider = AnthropicProvider(client=object())
    g = Graph()
    db = str(tmp_path / "fork.sqlite")
    rt = Runtime(g, llm_provider=provider, persist_to=db)

    # Drive at least one event so fork() has a target to anchor on.
    g.add_object("seed", {})
    seed_event_id = next(e.id for e in g.events if e.type == "object.created")

    # Fork. No raise — fork's Runtime construction inherits the parent
    # provider (AnthropicProvider) and the same registry, so validation
    # is a no-op.
    forked = rt.fork(seed_event_id)
    assert forked.llm_provider is provider


def test_user_test_reproducer_catches_default_model_mismatch_before_call():
    """The original v1.0.1-user-test bug: a user swaps
    AnthropicProvider() for OpenAIProvider() but their @llm_behavior
    still carries model='claude-...'. v1.0.2.post1 fires at Runtime
    construction — well before any run_goal — so the user sees the
    diagnostic at setup time rather than at first network call."""

    @llm_behavior(
        name="extractor",
        on=["object.created"],
        description="extract",
        output_schema=ClaimList,
        model="claude-sonnet-4-5",  # user copied this from an Anthropic example
    )
    def extractor(event, graph, ctx, llm_output):
        pass

    provider = OpenAIProvider(client=object())
    g = Graph()

    # The diagnostic fires at Runtime construction, before the registry
    # is ever exercised by a run. No HTTP 404, no behavior.failed event,
    # no need to call run_goal — the configuration error names the
    # cross-provider mismatch directly at the binding moment.
    with pytest.raises(InvalidRuntimeConfiguration) as excinfo:
        Runtime(g, llm_provider=provider)

    assert "claude-sonnet-4-5" in str(excinfo.value)
    assert "OpenAIProvider" in str(excinfo.value)
