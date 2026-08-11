/// Stable wire result used by the Python provider adapter. BAML 0.15's
/// generated top-level functions return the declared value but do not expose
/// the underlying HTTP response metadata, so usage is part of the typed
/// result contract rather than recovered from private runtime state.
class BamlCompletionEnvelope {
  text string
  input_tokens int
  output_tokens int
  finish_reason string
}

client<llm> ActivegraphAnthropic {
  provider anthropic
  options {
    model "claude-sonnet-4-5"
    api_key "activegraph-test-key"
    base_url env.ACTIVEGRAPH_BAML_MOCK_ANTHROPIC_URL
  }
}

client<llm> ActivegraphOpenAI {
  provider openai
  options {
    model "gpt-4o-mini"
    api_key "activegraph-test-key"
    base_url env.ACTIVEGRAPH_BAML_MOCK_OPENAI_URL
  }
}

/// OpenRouter implements the OpenAI-compatible chat-completions API.
client<llm> ActivegraphOpenRouter {
  provider openai
  options {
    model "openai/gpt-4o-mini"
    api_key "activegraph-test-key"
    base_url env.ACTIVEGRAPH_BAML_MOCK_OPENROUTER_URL
  }
}

function complete_anthropic(system: string, messages_text: string, timeout_ms: int) -> BamlCompletionEnvelope {
  client: ActivegraphAnthropic
  prompt: `System instructions:
${system}

Conversation (JSON):
${messages_text}

Return the completion and usage in this exact typed shape:
${ctx.output_format}`
}

function complete_openai(system: string, messages_text: string, timeout_ms: int) -> BamlCompletionEnvelope {
  client: ActivegraphOpenAI
  prompt: `System instructions:
${system}

Conversation (JSON):
${messages_text}

Return the completion and usage in this exact typed shape:
${ctx.output_format}`
}

function complete_openrouter(system: string, messages_text: string, timeout_ms: int) -> BamlCompletionEnvelope {
  client: ActivegraphOpenRouter
  prompt: `System instructions:
${system}

Conversation (JSON):
${messages_text}

Return the completion and usage in this exact typed shape:
${ctx.output_format}`
}
