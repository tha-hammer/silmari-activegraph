/// Mirrors build_user_message's shape (prompt.py:347-360) so its output can
/// be compared, offline, against activegraph's real assemble_prompt().
/// Never dialed over the network -- render_question_generator_prompt below
/// only exercises BAML's offline render_prompt(), so this client's
/// model/base_url are placeholders, not live credentials.
client<llm> PromptPreviewClient {
  provider anthropic
  options {
    model "claude-sonnet-4-5"
  }
}

function question_generator_prompt(view_block: string, event_block: string, instruction: string) -> string {
  client: PromptPreviewClient
  prompt: `${view_block}

## Triggering event
${event_block}

## Task
${instruction}`
}

/// Offline (no network) render of question_generator_prompt's real Jinja
/// template, for direct comparison against assemble_prompt()'s output on
/// the same inputs (Behavior 4). Uses the `<Fn>$render_prompt(...)`
/// debugging syntax (SKILL.md), NOT the generic `baml.llm.render_prompt
/// (client, "name", args_map)` builtin -- confirmed empirically that the
/// generic form silently drops all interpolated content (every arg
/// renders as an empty string, `.messages()` shows `content: ""`) while
/// `$render_prompt` renders correctly. `.text()` is called here, inside
/// BAML, rather than in Python: the auto-generated top-level
/// `<fn>__render_prompt` helper returns an opaque native PromptAst handle
/// that Python cannot call methods on directly (no
/// `_from_pyhandle`/class-registration path exists for a bare top-level
/// ADT_PROMPT_AST return -- confirmed empirically, `baml.panics.SdkPanic:
/// "Cannot convert object of type PromptAst"` on any attempt to
/// reconstruct and call back into it from Python). Doing the `.text()`
/// conversion natively means only a plain string crosses the FFI
/// boundary.
function render_question_generator_prompt(view_block: string, event_block: string, instruction: string) -> string {
  question_generator_prompt$render_prompt(
    view_block = view_block,
    event_block = event_block,
    instruction = instruction,
  ).text()
}
