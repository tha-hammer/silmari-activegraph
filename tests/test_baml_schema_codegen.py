def test_baml_question_list_schema_generates_real_pydantic_model():
    from activegraph.baml_client.baml_sdk import QuestionListBaml

    schema = QuestionListBaml.model_json_schema()
    assert schema["properties"]["questions"]["type"] == "array"
