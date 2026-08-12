"""`ClaudeCodeProvider` quickstart — LLM behaviors billed against your
Claude subscription instead of ANTHROPIC_API_KEY metered billing.

CONTRACT v1.11 #1. Requires `pip install "activegraph[claude-code]"`
and an active Claude Max/Pro/Team/Enterprise login (`claude login`),
with `ANTHROPIC_API_KEY` unset — see
`docs/reference/llm-providers.md`'s `ClaudeCodeProvider` section for
the full capability-limited contract before using this in anything
beyond local/ordinary use.

This mirrors `examples/llm_claim_extraction.py`'s shape (a `@behavior`
that seeds a document, an `@llm_behavior` that extracts structured
claims from it) but constructs `ClaudeCodeProvider` directly instead
of a scripted fake — a real, minimal, copy-pasteable entry point.

Two things this example deliberately does NOT do, because
`ClaudeCodeProvider` cannot honor either:

  - no `deterministic=True` on the `@llm_behavior` — the SDK has no
    temperature/top_p control, and `Runtime` refuses to bind a
    deterministic behavior to a provider that can't honor it;
  - no `budget={"max_cost_usd": ...}` on the `Runtime` — this
    provider's `count_tokens()` is a local heuristic, not an official
    pre-call count, so `Runtime` refuses to bind a hard cost budget to
    it either.

`allow_unenforced_generation_controls=True` is the explicit
acknowledgement `Runtime`'s capability-binding validation requires
before it will bind this provider to any `@llm_behavior` at all.

Run it: `python examples/claude_code_subscription.py`
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from activegraph import Graph, Runtime, behavior, clear_registry, llm_behavior


class Claim(BaseModel):
    """A single factual claim extracted from a document."""

    text: str = Field(description="The claim, in one short sentence.")
    confidence: float = Field(ge=0.0, le=1.0)


class ClaimList(BaseModel):
    claims: list[Claim]


ClaimList.model_rebuild()


def _register_behaviors() -> None:
    clear_registry()

    @behavior(name="seed_document", on=["goal.created"])
    def seed_document(event, graph, ctx):
        graph.add_object(
            "document",
            {
                "title": "Q3 sales summary",
                "body": (
                    "Q3 sales results show 14% YoY growth in the SMB "
                    "segment, while enterprise contracts declined 3% "
                    "over the same period."
                ),
            },
        )

    @llm_behavior(
        name="claim_extractor",
        on=["object.created"],
        where={"object.type": "document"},
        description="Extract verifiable factual claims from the document.",
        output_schema=ClaimList,
        view={"around": "event.payload.object.id", "depth": 1},
        creates=["claim"],
        # deterministic=True is NOT used here — see the module docstring.
    )
    def claim_extractor(event, graph, ctx, llm_output):
        doc_id = event.payload["object"]["id"]
        for claim in llm_output.claims:
            c = graph.add_object("claim", {"text": claim.text, "confidence": claim.confidence})
            graph.add_relation(c.id, doc_id, "supports")


def main() -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ClaudeCodeProvider refuses to run with ANTHROPIC_API_KEY set "
            "(it takes precedence over subscription auth). Run:\n"
            "    unset ANTHROPIC_API_KEY\n"
            "and make sure you're logged in with `claude login` first."
        )

    from activegraph.llm import ClaudeCodeProvider

    _register_behaviors()
    provider = ClaudeCodeProvider(allow_unenforced_generation_controls=True)
    rt = Runtime(Graph(), llm_provider=provider)  # capability-binding validation runs here
    rt.run_goal("Survey Q3 sales signals")

    print(f"run {rt.run_id}: {len(rt.graph.all_objects())} objects")
    rt.print_trace()


if __name__ == "__main__":
    main()
