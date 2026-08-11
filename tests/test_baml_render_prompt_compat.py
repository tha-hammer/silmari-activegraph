from activegraph.llm.prompt import assemble_prompt
from activegraph.packs.diligence.behaviors import QuestionList

from tests._baml_helpers import shared_fixture_kwargs


def test_baml_render_prompt_vs_assemble_prompt_same_fixture():
    from activegraph.baml_client.baml_sdk import render_question_generator_prompt

    fixture = shared_fixture_kwargs()
    activegraph_prompt = assemble_prompt(
        behavior_name="question_generator",
        description="Generate diligence questions",
        model="claude-sonnet-4-5",
        output_schema=QuestionList,
        creates=["question_list"],
        view=fixture["view"],
        event=fixture["event"],
        frame=fixture["frame"],
        around=None,
        depth=None,
        max_tokens=512,
        temperature=0.0,
        top_p=1.0,
        deterministic=True,
    )

    baml_rendered = render_question_generator_prompt(
        view_block=activegraph_prompt.sections["view"],
        event_block=activegraph_prompt.sections["event"],
        instruction=activegraph_prompt.sections["instruction"],
    )

    # Comparative, not pass/fail (plan's Property: N/A) -- both renderers must
    # produce real, non-empty text on the shared fixture; the diff itself is
    # the record, not a strict-equality assertion.
    assert baml_rendered
    assert activegraph_prompt.sections["user"]

    print("=== activegraph assemble_prompt (user message) ===")
    print(activegraph_prompt.sections["user"])
    print("=== BAML render_prompt (question_generator_prompt) ===")
    print(baml_rendered)


def test_baml_render_prompt_schema_free_variant():
    # Edge case: a schema-free behavior (structured_output_mode default is
    # "prompt", but with output_schema=None, build_system_prompt branches
    # differently, prompt.py:224-253).
    from activegraph.baml_client.baml_sdk import render_question_generator_prompt

    fixture = shared_fixture_kwargs()
    activegraph_prompt = assemble_prompt(
        behavior_name="question_generator",
        description="Generate diligence questions",
        model="claude-sonnet-4-5",
        output_schema=None,
        creates=["question_list"],
        view=fixture["view"],
        event=fixture["event"],
        frame=fixture["frame"],
        around=None,
        depth=None,
        max_tokens=512,
        temperature=0.0,
        top_p=1.0,
        deterministic=True,
    )

    baml_rendered = render_question_generator_prompt(
        view_block=activegraph_prompt.sections["view"],
        event_block=activegraph_prompt.sections["event"],
        instruction=activegraph_prompt.sections["instruction"],
    )

    assert baml_rendered
    assert activegraph_prompt.sections["user"]
