"""Shared Claude-model-family facts. Module-private — not re-exported,
not part of the public Protocol surface.

CONTRACT v1.11 #1 (review CC4). `AnthropicProvider` and `ClaudeCodeProvider`
both target the identical underlying Claude model family, so pricing, the
default model, and the native-structured-output model-prefix table live
here once instead of as two independently-maintained copies that could
silently drift. `OpenAIProvider` keeps its own independent tables — it
targets a genuinely different model family with no facts to share.

Both consuming providers import these objects directly (not copies) so
`provider._pricing_table() is DEFAULT_PRICING` holds for the default
(no `pricing=` override) construction path.
"""

from __future__ import annotations


# Per-million-token pricing in USD. Tracks the rates of the Claude 4.x
# family available in May 2026. Providers accept their own `pricing=`
# constructor kwarg to override.
DEFAULT_PRICING: dict[str, dict[str, str]] = {
    "claude-opus-4": {"input": "15", "output": "75"},
    "claude-sonnet-4": {"input": "3", "output": "15"},
    "claude-haiku-4-5": {"input": "1", "output": "5"},
}

# v1.0.2 #1: the model name used when an @llm_behavior doesn't pin one.
DEFAULT_MODEL: str = "claude-sonnet-4-5"

# Model families with GA structured-output support on the Claude API
# (CONTRACT v1.3 #1 #3). Claude 4.0-era families are deliberately absent.
NATIVE_STRUCTURED_OUTPUT_PREFIXES: tuple[str, ...] = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-sonnet-5",
    "claude-sonnet-4-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-opus-4-5",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-opus-4-8",
)
