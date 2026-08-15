from hypothesis import HealthCheck, given, settings, strategies as st

from activegraph.llm.native import native_schema_compatible
from activegraph.llm.prompt import schema_to_json
from activegraph.packs.diligence.behaviors import QuestionList


def test_baml_generated_schema_native_compat_matches_or_diverges_from_handwritten(
    baml_question_list_schema,
):
    handwritten_schema = schema_to_json(QuestionList)

    baml_result = native_schema_compatible(baml_question_list_schema)
    handwritten_result = native_schema_compatible(handwritten_schema)

    assert handwritten_result is False  # already-known baseline
    # Empirically observed against the real baml-generated schema (Behavior
    # 2): every keyword it emits (additionalProperties, description, items,
    # properties, required, title, type) is in the 15-keyword allowlist, and
    # `questions` is listed as required with additionalProperties already
    # false -- unlike hand-written QuestionList, which adds `minItems` via
    # Field(min_length=1), outside the allowlist. So the two genuinely
    # diverge: BAML's codegen doesn't carry the length-constraint that
    # trips native mode's pre-flight.
    assert baml_result is True


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(extra_keyword=st.sampled_from(["minItems", "maxLength", "pattern", "minimum", "not", "oneOf"]))
def test_native_schema_compatible_property_never_raises_on_schema_mutations(
    baml_question_list_schema, extra_keyword
):
    # Read-only use of the fixture (copied, never mutated in place), so
    # it's safe for it not to reset between Hypothesis-generated inputs.
    mutated = dict(baml_question_list_schema)
    mutated[extra_keyword] = 1
    result = native_schema_compatible(mutated)
    assert isinstance(result, bool)
