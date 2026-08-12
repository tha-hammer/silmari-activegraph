"""LLM behaviors subpackage. CONTRACT v0.6; OpenAIProvider added v1.0.1 #5.

Public surface:

  LLMProvider            — Protocol every provider implements
  LLMMessage             — single role-tagged message
  LLMResponse            — what `complete()` returns
  AnthropicProvider      — reference implementation
  OpenAIProvider         — second concrete provider, surface parity
                           with AnthropicProvider (v1.0.1 #5)
  ClaudeCodeProvider     — third provider, backed by the Claude Agent
                           SDK; bills against the caller's Claude
                           subscription instead of ANTHROPIC_API_KEY
                           metered billing. Capability-limited — see
                           its module docstring (v1.11 #1)
  EmbeddingCache         — content-keyed replay cache for Runtime.embed
  EmbeddingProvider      — Protocol for text-embedding providers
  HashEmbeddingProvider  — deterministic, dependency-free test double
                           for embedding plumbing
  RecordedLLMProvider    — fixture-backed provider for tests
  RecordingLLMProvider   — wraps another provider, persists responses
                           as fixtures (for first-time test seed)
  LLMCache               — content-keyed replay cache
  AssembledPrompt        — the deterministic prompt + params blob
                           returned by `LLMBehavior.build_prompt`
  MissingProviderError   — raised when an @llm_behavior is registered
                           without a runtime provider
  LLMBehaviorError       — structured failure carrier from @llm_behavior
                           wrappers; runtime turns it into
                           `behavior.failed` with a `reason`
  LLMProviderCapabilities — additive provider-declared capability
                           descriptor (v1.11 #1); absent on a provider
                           resolves to full parity
  get_llm_provider_capabilities — read a provider's capabilities,
                           defaulting to full parity when absent
  parse_structured_response — JSON-extraction-then-Pydantic-validate
                           helper shared by every provider that uses
                           the framework's instruction-based path
"""

from activegraph.llm.anthropic import AnthropicProvider
from activegraph.llm.cache import LLMCache
from activegraph.llm.claude_code import ClaudeCodeProvider
from activegraph.llm.embedding import EmbeddingProvider, HashEmbeddingProvider
from activegraph.llm.embedding_cache import EmbeddingCache
from activegraph.llm.errors import LLMBehaviorError, MissingProviderError
from activegraph.llm.native import native_schema_compatible
from activegraph.llm.openai import OpenAIProvider
from activegraph.llm.parsing import parse_structured_response
from activegraph.llm.prompt import (
    AssembledPrompt,
    assemble_prompt,
    schema_to_json,
    serialize_view,
)
from activegraph.llm.provider import (
    FULL_LLM_PROVIDER_CAPABILITIES,
    LLMProvider,
    LLMProviderCapabilities,
    get_llm_provider_capabilities,
)
from activegraph.llm.recorded import RecordedLLMProvider, RecordingLLMProvider
from activegraph.llm.types import LLMMessage, LLMResponse, ToolCall
from activegraph.llm.wire import sanitize_tool_name


__all__ = [
    "AnthropicProvider",
    "AssembledPrompt",
    "ClaudeCodeProvider",
    "EmbeddingCache",
    "EmbeddingProvider",
    "FULL_LLM_PROVIDER_CAPABILITIES",
    "HashEmbeddingProvider",
    "LLMBehaviorError",
    "LLMCache",
    "LLMMessage",
    "LLMProvider",
    "LLMProviderCapabilities",
    "LLMResponse",
    "MissingProviderError",
    "OpenAIProvider",
    "RecordedLLMProvider",
    "RecordingLLMProvider",
    "ToolCall",
    "assemble_prompt",
    "get_llm_provider_capabilities",
    "native_schema_compatible",
    "parse_structured_response",
    "sanitize_tool_name",
    "schema_to_json",
    "serialize_view",
]
