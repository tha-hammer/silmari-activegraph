/// Stable wire result used by the Python provider adapter. BAML 0.15's
/// generated top-level functions return the declared value but do not expose
/// the underlying HTTP response metadata, so usage is part of the typed
/// result contract rather than recovered from private runtime state.
class BamlCompletionEnvelope {
    text: string,
    input_tokens: int,
    output_tokens: int,
    finish_reason: string,
}

client<llm> ActivegraphAnthropic {
    provider: anthropic,
    options: {
        model: "claude-sonnet-4-5",
        api_key: "activegraph-test-key",
        base_url: env.ACTIVEGRAPH_BAML_MOCK_ANTHROPIC_URL,
    },
}

/// Test-focused bounded retry policy. The short delay keeps the loopback
/// integration suite fast; production tuning is deliberately out of scope.
retry_policy RetryTwice {
    max_retries: 2,
    initial_delay_ms: 1,
    multiplier: 1.0,
    max_delay_ms: 1,
}

/// Isolated from ActivegraphAnthropic so retry tests cannot perturb the
/// single-attempt accounting asserted by Behaviors 6 and 7.
client<llm> ActivegraphAnthropicRetryTwice {
    provider: anthropic,
    retry_policy: RetryTwice,
    options: {
        model: "claude-sonnet-4-5",
        api_key: "activegraph-test-key",
        base_url: env.ACTIVEGRAPH_BAML_MOCK_RETRY_URL,
    },
}

client<llm> ActivegraphOpenAI {
    provider: openai,
    options: {
        model: "gpt-4o-mini",
        api_key: "activegraph-test-key",
        base_url: env.ACTIVEGRAPH_BAML_MOCK_OPENAI_URL,
    },
}

/// OpenRouter implements the OpenAI-compatible chat-completions API.
client<llm> ActivegraphOpenRouter {
    provider: openai,
    options: {
        model: "openai/gpt-4o-mini",
        api_key: "activegraph-test-key",
        base_url: env.ACTIVEGRAPH_BAML_MOCK_OPENROUTER_URL,
    },
}

/// Real OpenRouter client, deliberately separate from the mock-routed
/// ActivegraphOpenRouter. `openrouter/free` is OpenRouter's stable dynamic
/// selector for currently available free models, avoiding individual model
/// slug churn; OPENROUTER_FREE_MODEL can select a specific current `:free`
/// slug when callers need deterministic routing.
client<llm> ActivegraphOpenRouterLive {
    provider: openai,
    options: {
        model: env.OPENROUTER_FREE_MODEL,
        api_key: env.OPENROUTER_API_KEY,
        base_url: "https://openrouter.ai/api/v1",
    },
}

/// The vendor priority is intentional: prefer Anthropic, then OpenAI, then
/// the OpenRouter-compatible route. Each primitive remains independently
/// mockable and retains its own wire protocol. BAML 0.15 compiler2 currently
/// lowers composite strategy entries only from string literals; bare client
/// identifiers silently produce an empty sub_clients list.
client<llm> ActivegraphFallbackCascade {
    provider: fallback,
    options: {
        strategy: ["ActivegraphAnthropic", "ActivegraphOpenAI", "ActivegraphOpenRouter"],
    },
}

function complete_anthropic(
    system: string,
    messages_text: string,
    timeout_ms: int,
) -> BamlCompletionEnvelope {
    client: ActivegraphAnthropic
    prompt: `
        System instructions:
        ${system}

        Conversation (JSON):
        ${messages_text}

        Return the completion and usage in this exact typed shape:
        ${ctx.output_format}
    `
}

/// The retry-bound LLM operation. The public function below wraps this call
/// in one deadline that covers every BAML-internal attempt.
function _complete_anthropic_with_retry_raw(
    system: string,
    messages_text: string,
) -> BamlCompletionEnvelope {
    client: ActivegraphAnthropicRetryTwice
    prompt: `
        System instructions:
        ${system}

        Conversation (JSON):
        ${messages_text}

        Return the completion and usage in this exact typed shape:
        ${ctx.output_format}
    `
}

/// Normalize the LLM function's generated error union so it can participate
/// in a homogeneous Future<T, E> race with the deadline future.
function _invoke_anthropic_with_retry(
    system: string,
    messages_text: string,
) -> BamlCompletionEnvelope throws baml.errors.DevOther {
    _complete_anthropic_with_retry_raw(system, messages_text) catch (e) {
        _ => {
            throw baml.errors.DevOther {
      message: `BAML retry sequence failed: ${e}`,
    }
        }
    }
}

function _anthropic_retry_deadline(
    timeout_ms: int,
) -> BamlCompletionEnvelope throws baml.errors.DevOther {
    baml.sys.sleep(baml.time.Duration.from_milliseconds(timeout_ms)) catch (e) {
        _ => {
            throw baml.errors.DevOther {
      message: `BAML retry timeout sleep failed: ${e}`,
    }
        }
    };
    throw baml.errors.DevOther {
    message: `BAML retry sequence timeout after ${timeout_ms}ms`,
  }
}

/// Generated public entrypoint used by BamlLLMProvider. Cancelling the losing
/// future ensures timeout_ms bounds the whole internal retry sequence.
function complete_anthropic_with_retry(
    system: string,
    messages_text: string,
    timeout_ms: int,
) -> BamlCompletionEnvelope throws baml.errors.DevOther {
    let work = spawn { _invoke_anthropic_with_retry(system, messages_text) };
    let deadline = spawn { _anthropic_retry_deadline(timeout_ms) };
    await baml.future.race([work, deadline])
}

function complete_openai(
    system: string,
    messages_text: string,
    timeout_ms: int,
) -> BamlCompletionEnvelope {
    client: ActivegraphOpenAI
    prompt: `
        System instructions:
        ${system}

        Conversation (JSON):
        ${messages_text}

        Return the completion and usage in this exact typed shape:
        ${ctx.output_format}
    `
}

function complete_openrouter(
    system: string,
    messages_text: string,
    timeout_ms: int,
) -> BamlCompletionEnvelope {
    client: ActivegraphOpenRouter
    prompt: `
        System instructions:
        ${system}

        Conversation (JSON):
        ${messages_text}

        Return the completion and usage in this exact typed shape:
        ${ctx.output_format}
    `
}

function complete_fallback_cascade(
    system: string,
    messages_text: string,
    timeout_ms: int,
) -> BamlCompletionEnvelope {
    client: ActivegraphFallbackCascade
    prompt: `
        System instructions:
        ${system}

        Conversation (JSON):
        ${messages_text}

        Return the completion and usage in this exact typed shape:
        ${ctx.output_format}
    `
}

function complete_openrouter_live(
    system: string,
    messages_text: string,
    timeout_ms: int,
) -> BamlCompletionEnvelope {
    client: ActivegraphOpenRouterLive
    prompt: `
        System instructions:
        ${system}

        Conversation (JSON):
        ${messages_text}

        Return the completion and usage in this exact typed shape:
        ${ctx.output_format}
    `
}
