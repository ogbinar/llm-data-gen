from pathlib import Path
from tempfile import TemporaryDirectory

from llm_data_gen.client import StaticClient
from llm_data_gen.config import AppConfig
from llm_data_gen.output import load_existing_example_ids
from llm_data_gen.pipeline import run_pipeline


def test_pipeline_writes_skipped_sidecar_on_bad_output():
    with TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "one.jsonl"
        input_path.write_text(
            '{"source_id":"doc-1","title":"Example","text":"The ID number is 12345. The support team should check it."}\n',
            encoding="utf-8",
        )
        output_path = Path(tmp) / "rows.jsonl"
        rows = run_pipeline(
            AppConfig(input_path=input_path, output_path=output_path, chunk_size=500),
            client=StaticClient(["not json"]),
        )
        assert rows == []
        skipped = output_path.with_suffix(".skipped.jsonl")
        assert skipped.exists()
        assert skipped.read_text(encoding="utf-8").strip()


def test_load_existing_example_ids_ignores_partial_lines(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text('{"example_id":"ok"}\n{"example_id"', encoding="utf-8")
    ids = load_existing_example_ids(path)
    assert ids == {"ok"}
