from llm_data_gen.chunking import chunk_document
from llm_data_gen.models import ModelExample, SourceDocument
from llm_data_gen.parsing import parse_model_output
from llm_data_gen.validation import second_pass_validate


def test_parse_model_output_accepts_json():
    example = parse_model_output(
        '{"task_type":"Grounded QA","input_text":"Q","output_text":"A","evidence":"abc"}'
    )
    assert example.task_type == "Grounded QA"


def test_second_pass_validation_checks_evidence():
    doc = SourceDocument(
        source_id="doc-1",
        title="Example",
        text="The ID number is 12345.",
        language="english",
        provenance_path="/tmp/example.md",
    )
    chunk = chunk_document(doc, 200)[0]
    example = ModelExample(
        task_type="Grounded QA",
        input_text="What is the ID number?",
        output_text="The ID number is 12345.",
        evidence="ID number is 12345",
    )
    ok, note = second_pass_validate(example, chunk)
    assert ok is True
    assert note is None


def test_second_pass_validation_rejects_task_type_mismatch():
    doc = SourceDocument(
        source_id="doc-1",
        title="Example",
        text="The ID number is 12345.",
        language="english",
        provenance_path="/tmp/example.md",
    )
    chunk = chunk_document(doc, 200)[0]
    example = ModelExample(
        task_type="Intent -> response",
        input_text="What is the ID number?",
        output_text="The ID number is 12345.",
        evidence="ID number is 12345",
    )
    ok, note = second_pass_validate(example, chunk, expected_task_type="Grounded QA")
    assert ok is False
    assert "prompt-family mismatch" in note
