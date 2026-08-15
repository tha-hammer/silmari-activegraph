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

// Clients are built by helper functions, not top-level `client X = expr;`
// declarations. BAML 0.16 evaluates every top-level client declaration
// eagerly at module $init time -- and calling baml.env.get/get_or_panic
// during $init is rejected outright (confirmed empirically: fails even when
// the referenced env var IS set; this is a hard $init-time restriction, not
// a missing-value problem). A plain function's body only runs when the
// function is actually called, so building the client inside one (and
// referencing it from a function's `client:` field with a call expression)
// defers env resolution to real invocation time -- exactly the "session-
// scoped mock server, env var read at call time" behavior this file relied
// on under BAML 0.15's `client<llm> { options: { base_url: env.X } }` config
// blocks, which resolved lazily rather than executing a real syscall at
// declaration time.

class _OpenAiCompatMessage {
    role: string,
    content: string,
}

class _OpenAiCompatChatRequest {
    model: string,
    messages: _OpenAiCompatMessage[],
}

class _OpenAiCompatChoiceMessage {
    content: string?,
}

class _OpenAiCompatChoice {
    message: _OpenAiCompatChoiceMessage,
}

class _OpenAiCompatChatResponse {
    choices: _OpenAiCompatChoice[],
}

/// A minimal ai.Client implementing the classic OpenAI-compatible chat
/// completions wire format (`POST {base_url}/v1/chat/completions`). BAML
/// 0.16's builtin openai.OpenAiClient targets OpenAI's newer Responses API
/// exclusively, with no toggle back to Chat Completions (confirmed via
/// `baml describe` and by empirically reproducing a real ParseFailed against
/// a Chat-Completions-shaped 200 response) -- but OpenRouter, this file's
/// actual reason for having an OpenAI-compatible client at all, only speaks
/// Chat Completions. Using the builtin client for OpenRouter would fail
/// against real production traffic, not just these mocks.
class OpenAiCompatClient {
    model: string,
    api_key: string | null,
    api_key_env: string | null,
    base_url: string,
    provider_label: string,

    implements ai.Client {
        function id(self) -> string { `openai-compat:${self.model}` }

        function invoke(self, input: ai.ModelTurnInput) -> ai.ModelTurn throws ai.errors.Failure {
            // api_key_env is resolved here, at call time, not at client
            // construction -- lenient for any future offline/render path,
            // strict (panics) only when a real call actually needs it,
            // mirroring the builtin OpenAiClient's own api_key/api_key_env
            // split this class otherwise replaces.
            let key = self.api_key ?? (
                if let env_name: string = self.api_key_env {
                    baml.env.get_or_panic(env_name) catch (e) {
                        _ => throw ai.errors.InvalidRequest { provider: self.provider_label, status_code: null, detail: `${e}`, raw_body: null }
                    }
                } else {
                    ""
                }
            );
            // This codebase's BAML functions render one flat, untagged
            // prompt string (system instructions and conversation both
            // interpolated into a single template, no [system]/[user]
            // markup) -- so PromptMessage.role comes back "" for it. Chat
            // Completions requires a role on every message; default the
            // unspecified case to "user", the conventional default for a
            // single untagged turn.
            let req_messages = input.prompt("[unsupported media]").messages().map((m: ai.PromptMessage) -> {
                _OpenAiCompatMessage { role: if (m.role == "") { "user" } else { m.role }, content: m.content }
            });
            let body = baml.json.to_string(_OpenAiCompatChatRequest { model: self.model, messages: req_messages }) catch (e) {
                _ => throw ai.errors.InvalidRequest { provider: self.provider_label, status_code: null, detail: `${e}`, raw_body: null }
            };
            let headers: map<string, string> = {};
            let _ = headers.set("Authorization", `Bearer ${key}`);
            let _ = headers.set("Content-Type", "application/json");
            let request = baml.http.Request {
                method: "POST",
                url: `${self.base_url}/v1/chat/completions`,
                headers: headers,
                body: body,
            };
            let response = baml.http.send(request) catch (e) {
                _ => throw ai.errors.NetworkFailure { provider: self.provider_label, detail: `${e}`, raw_body: null }
            };
            let response_text = response.text() catch (e) {
                _ => throw ai.errors.NetworkFailure { provider: self.provider_label, detail: `${e}`, raw_body: null }
            };
            if (!response.ok()) {
                throw ai.errors.classify_http(self.provider_label, response.status_code, response_text)
            }
            let parsed = baml.json.from_string<_OpenAiCompatChatResponse>(response_text) catch (e) {
                _ => throw ai.errors.ParseFailed { provider: self.provider_label, raw_output: response_text }
            };
            let content = parsed.choices.at(0)?.message.content;
            if let text: string = content {
                ai.ModelTurn {
                    content: [ai.content.Text { text: text }],
                    stop_reason: ai.content.StopReason.Complete,
                    usage: null,
                }
            } else {
                throw ai.errors.ParseFailed { provider: self.provider_label, raw_output: response_text }
            }
        }
    }
}

/// Debug/test-only: builds (without sending) the request
/// _activegraph_openrouter_live_client would issue for a given
/// system/messages_text pair. Generated LLM functions get an auto
/// `<fn>__build_request` introspection helper (BAML 0.15/skill docs), but
/// that's only wired up for BAML's own builtin client types -- it isn't
/// generated for functions using a custom ai.Client implementation like
/// OpenAiCompatClient (confirmed: `complete_openrouter_live__build_request`
/// doesn't exist in the generated SDK, only `__parse`/`__render_prompt` do).
/// This mirrors the request-shaping half of OpenAiCompatClient.invoke() --
/// model/credential/URL resolution, method, headers -- without needing a
/// real ai.ModelTurnInput (which needs framework-internal journal/toolbox
/// wiring only a real function call provides) or making a live call.
function complete_openrouter_live__test_build_request(
    system: string,
    messages_text: string,
) -> baml.http.Request {
    let key = baml.env.get_or_panic("OPENROUTER_API_KEY") catch (e) {
        _ => throw baml.errors.DevOther { message: `${e}` }
    };
    let model = baml.env.get("OPENROUTER_FREE_MODEL") ?? "openrouter/free";
    let body = baml.json.to_string(_OpenAiCompatChatRequest {
        model: model,
        messages: [_OpenAiCompatMessage {
            role: "user",
            content: `System instructions:\n${system}\n\nConversation (JSON):\n${messages_text}`,
        }],
    }) catch (e) {
        _ => throw baml.errors.DevOther { message: `${e}` }
    };
    let headers: map<string, string> = {};
    let _ = headers.set("Authorization", `Bearer ${key}`);
    let _ = headers.set("Content-Type", "application/json");
    baml.http.Request {
        method: "POST",
        url: "https://openrouter.ai/api/v1/chat/completions",
        headers: headers,
        body: body,
    }
}

function _activegraph_anthropic_client() -> ai.Client {
    anthropic.AnthropicClient.new(
        model = "claude-sonnet-4-5",
        api_key = "activegraph-test-key",
        base_url = baml.env.get_or_panic("ACTIVEGRAPH_BAML_MOCK_ANTHROPIC_URL"),
    )
}

/// Isolated from _activegraph_anthropic_client so retry tests cannot perturb
/// the single-attempt accounting asserted by Behaviors 6 and 7. Test-focused
/// bounded retry: max_attempts=3 is 1 initial attempt + 2 retries (the old
/// BAML 0.15 retry_policy's `max_retries: 2` counted retries only, not the
/// initial attempt — this client-boundary composition counts total
/// attempts). Constant 1ms backoff keeps the loopback integration suite
/// fast; production tuning is deliberately out of scope.
function _activegraph_anthropic_retry_client() -> ai.Client {
    ai.clients.Retry.new(
        anthropic.AnthropicClient.new(
            model = "claude-sonnet-4-5",
            api_key = "activegraph-test-key",
            base_url = baml.env.get_or_panic("ACTIVEGRAPH_BAML_MOCK_RETRY_URL"),
        ),
        max_attempts = 3,
        backoff = ai.clients.Backoff.new(initial_ms = 1, multiplier = 1, max_ms = 1),
    )
}

function _activegraph_openai_client() -> ai.Client {
    OpenAiCompatClient {
        model: "gpt-4o-mini",
        api_key: "activegraph-test-key",
        api_key_env: null,
        base_url: baml.env.get_or_panic("ACTIVEGRAPH_BAML_MOCK_OPENAI_URL"),
        provider_label: "openai",
    }
}

/// OpenRouter implements the OpenAI-compatible chat-completions API.
function _activegraph_openrouter_client() -> ai.Client {
    OpenAiCompatClient {
        model: "openai/gpt-4o-mini",
        api_key: "activegraph-test-key",
        api_key_env: null,
        base_url: baml.env.get_or_panic("ACTIVEGRAPH_BAML_MOCK_OPENROUTER_URL"),
        provider_label: "openai",
    }
}

/// Real OpenRouter client, deliberately separate from the mock-routed
/// _activegraph_openrouter_client. `openrouter/free` is OpenRouter's stable
/// dynamic selector for currently available free models, avoiding
/// individual model slug churn; OPENROUTER_FREE_MODEL can select a specific
/// current `:free` slug when callers need deterministic routing.
/// api_key_env defers credential resolution to actual invocation (lenient
/// for offline paths like render_prompt, strict only for a real network
/// call) -- on top of this whole client already being built lazily inside a
/// function body, so it can be referenced without a live credential set.
function _activegraph_openrouter_live_client() -> ai.Client {
    OpenAiCompatClient {
        model: baml.env.get("OPENROUTER_FREE_MODEL") ?? "openrouter/free",
        api_key: null,
        api_key_env: "OPENROUTER_API_KEY",
        base_url: "https://openrouter.ai/api/v1",
        provider_label: "openai",
    }
}

/// The vendor priority is intentional: prefer Anthropic, then OpenAI, then
/// the OpenRouter-compatible route. Each primitive remains independently
/// mockable and retains its own wire protocol. Composed from the real
/// client values the helper functions above build (no more string-literal
/// strategy list — BAML 0.16 client values are real objects, not named
/// config blocks referenced by name).
function _activegraph_fallback_client() -> ai.Client {
    ai.clients.Fallback {
        members: [
            _activegraph_anthropic_client(),
            _activegraph_openai_client(),
            _activegraph_openrouter_client(),
        ],
    }
}

function complete_anthropic(
    system: string,
    messages_text: string,
    timeout_ms: int,
) -> BamlCompletionEnvelope {
    client: _activegraph_anthropic_client()
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
    client: _activegraph_anthropic_retry_client()
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
    client: _activegraph_openai_client()
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
    client: _activegraph_openrouter_client()
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
    client: _activegraph_fallback_client()
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
    client: _activegraph_openrouter_live_client()
    prompt: `
        System instructions:
        ${system}

        Conversation (JSON):
        ${messages_text}

        Return the completion and usage in this exact typed shape:
        ${ctx.output_format}
    `
}
