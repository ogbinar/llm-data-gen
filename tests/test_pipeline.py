from pathlib import Path

from llm_data_gen.client import StaticClient
from llm_data_gen.config import AppConfig
from llm_data_gen.pipeline import run_pipeline


def test_pipeline_writes_valid_rows(tmp_path):
    input_path = tmp_path / "sample.jsonl"
    input_path.write_text(
        '{"source_id":"doc-1","title":"Example","text":"The ID number is 12345. The support team should check it."}\n',
        encoding="utf-8",
    )
    output_path = tmp_path / "rows.jsonl"
    responses = [
        '{"task_type":"Grounded QA","input_text":"What is the ID number?","output_text":"The ID number is 12345.","evidence":"ID number is 12345"}',
        '{"task_type":"Scenario -> response","input_text":"A user needs the ID checked.","output_text":"Check the ID number before proceeding.","evidence":"support team should check it"}',
        '{"task_type":"Intent -> response","input_text":"I want to verify the ID.","output_text":"Verify the ID number against the record.","evidence":"ID number is 12345"}',
        '{"task_type":"Multi-turn dialogue","input_text":"A: Can you verify it? B: Yes.","output_text":"A: Can you verify it? B: Yes, I can check the ID number.","evidence":"support team should check it"}',
        '{"task_type":"Troubleshooting / resolution","input_text":"The record looks wrong.","output_text":"Check the ID number before proceeding.","evidence":"check it"}',
    ]
    rows = run_pipeline(
        AppConfig(input_path=input_path, output_path=output_path, chunk_size=500),
        client=StaticClient(responses),
    )
    assert rows
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8").strip()

