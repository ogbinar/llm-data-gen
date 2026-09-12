import json

import pyarrow.parquet as parquet
import pytest

from llm_data_gen.client import StaticClient
from llm_data_gen.config import RunConfig
from llm_data_gen.output import export_csv, export_legacy_jsonl, export_parquet
from llm_data_gen.pipeline import inspect_run, run_config_pipeline


def make_config(tmp_path, *, model="test-model", num_examples=1):
    source = tmp_path / "source.txt"
    source.write_text(
        "The service ID is 12345. Restart the modem before contacting support.",
        encoding="utf-8",
    )
    return RunConfig.model_validate(
        {
            "version": 2,
            "name": "pipeline-test",
            "input": {"path": source, "language": "english", "domain": "support"},
            "chunking": {"strategy": "document"},
            "endpoint": {
                "profile": "local-qwen",
                "backend": "static",
                "base_url": "http://example.invalid/v1",
                "model": model,
            },
            "generation": {
                "recipes": [
                    {
                        "format": "factual_qa",
                        "num_examples": num_examples,
                    }
                ]
            },
            "output": {"directory": tmp_path / "output"},
        }
    )


def response(question="What is the service ID?"):
    return json.dumps(
        {
            "status": "generated",
            "qa_format": "factual_qa",
            "messages": [
                {"role": "user", "content": question},
                {"role": "assistant", "content": "The service ID is 12345."},
            ],
            "evidence": ["The service ID is 12345."],
            "metadata": {},
        }
    )


def test_inspect_has_no_inference_and_reports_jobs(tmp_path):
    config = make_config(tmp_path, num_examples=2)
    report = inspect_run(config)
    assert report["source_count"] == 1
    assert report["chunk_count"] == 1
    assert report["job_count"] == 2
    assert report["sources_by_language"] == {"english": 1}
    assert report["chunks_by_language"] == {"english": 1}
    assert "jobs_by_language" not in report


def test_pipeline_writes_standard_directory_and_resumes(tmp_path):
    config = make_config(tmp_path)
    rows = run_config_pipeline(config, client=StaticClient([response()]))
    assert len(rows) == 1
    output = config.output.directory
    assert {
        "manifest.json",
        "config.resolved.yaml",
        "sources.jsonl",
        "chunks.jsonl",
        "dataset.jsonl",
        "rejected.jsonl",
        "checkpoint.jsonl",
    }.issubset({path.name for path in output.iterdir()})
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_status"] == "completed"
    assert manifest["accepted_jobs"] == 1
    assert sum(manifest["accepted_by_source"].values()) == 1
    dataset = json.loads((output / "dataset.jsonl").read_text(encoding="utf-8"))
    assert dataset["language"] == "english"
    assert dataset["generation_parameters"]["source_language_origin"] == "input_default"
    source_manifest = json.loads((output / "sources.jsonl").read_text(encoding="utf-8"))
    chunk_manifest = json.loads((output / "chunks.jsonl").read_text(encoding="utf-8"))
    assert source_manifest["language"] == chunk_manifest["language"] == "english"
    assert source_manifest["language_origin"] == chunk_manifest["language_origin"] == "input_default"

    client = StaticClient([])
    resumed = run_config_pipeline(config, client=client)
    assert resumed == []
    assert client.calls == []


def test_pipeline_retries_selected_failure_stage(tmp_path):
    config = make_config(tmp_path)
    assert run_config_pipeline(config, client=StaticClient(["not json"])) == []
    rows = run_config_pipeline(
        config,
        client=StaticClient([response()]),
        retry_stages={"parsing"},
    )
    assert len(rows) == 1
    manifest = json.loads(
        (config.output.directory / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["accepted_jobs"] == 1
    assert manifest["rejected_jobs"] == 0


def test_non_recommendation_not_applicable_remains_safe_terminal_outcome(tmp_path):
    config = make_config(tmp_path)
    raw = json.dumps(
        {
            "status": "not_applicable",
            "qa_format": "factual_qa",
            "reason": "No example generated.",
        }
    )
    assert run_config_pipeline(config, client=StaticClient([raw])) == []
    rejection = json.loads(
        (config.output.directory / "rejected.jsonl").read_text(encoding="utf-8")
    )
    assert rejection["failure_stage"] == "not_applicable"
    manifest = json.loads((config.output.directory / "manifest.json").read_text())
    assert manifest["not_applicable_jobs"] == 1


def test_not_applicable_still_requires_matching_job_format(tmp_path):
    config = make_config(tmp_path)
    raw = json.dumps(
        {
            "status": "not_applicable",
            "qa_format": "cross_sell",
            "reason": "No example generated.",
        }
    )
    assert run_config_pipeline(config, client=StaticClient([raw])) == []
    rejection = json.loads(
        (config.output.directory / "rejected.jsonl").read_text(encoding="utf-8")
    )
    assert rejection["failure_stage"] == "validation"
    assert "format mismatch" in rejection["failure_reason"]


def test_pipeline_detects_duplicates_and_writes_rejected(tmp_path):
    config = make_config(tmp_path, num_examples=2)
    rows = run_config_pipeline(
        config,
        client=StaticClient([response(), response()]),
    )
    assert len(rows) == 1
    rejected = [
        json.loads(line)
        for line in (config.output.directory / "rejected.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rejected[0]["failure_stage"] == "duplicate"
    assert rejected[0]["duplicate_of"] == rows[0].example_id


def test_pipeline_rejects_config_hash_conflict(tmp_path):
    config = make_config(tmp_path)
    run_config_pipeline(config, client=StaticClient([response()]))
    changed = make_config(tmp_path, model="other-model")
    with pytest.raises(ValueError, match="different resolved configuration"):
        run_config_pipeline(changed, client=StaticClient([]))


def test_parquet_csv_and_legacy_exports(tmp_path):
    config = make_config(tmp_path)
    run_config_pipeline(config, client=StaticClient([response()]))
    dataset = config.output.directory / "dataset.jsonl"

    parquet_path = tmp_path / "export" / "dataset.parquet"
    csv_path = tmp_path / "export" / "dataset.csv"
    legacy_path = tmp_path / "export" / "legacy.jsonl"
    export_parquet(dataset, parquet_path)
    export_csv(dataset, csv_path)
    export_legacy_jsonl(dataset, legacy_path)

    assert parquet.read_table(parquet_path).num_rows == 1
    assert "qa_format" in csv_path.read_text(encoding="utf-8")
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert legacy["input_text"] == "What is the service ID?"


def test_mixed_structured_languages_propagate_through_outputs_and_exports(tmp_path):
    source = tmp_path / "mixed.jsonl"
    source.write_text(
        '{"source_id":"en","text":"Support is available.","lang":"english"}\n'
        '{"source_id":"tl","text":"May tulong na available.","lang":"tagalog"}\n',
        encoding="utf-8",
    )
    config = RunConfig.model_validate(
        {
            "version": 2,
            "name": "mixed",
            "input": {
                "path": source,
                "language": "english",
                "language_field": "lang",
            },
            "chunking": {"strategy": "document"},
            "generation": {"recipes": [{"format": "factual_qa"}]},
            "output": {"directory": tmp_path / "mixed-output"},
        }
    )
    responses = [
        json.dumps(
            {
                "status": "generated",
                "qa_format": "factual_qa",
                "messages": [
                    {"role": "user", "content": "Is support available?"},
                    {"role": "assistant", "content": "Support is available."},
                ],
                "evidence": ["Support is available."],
            }
        ),
        json.dumps(
            {
                "status": "generated",
                "qa_format": "factual_qa",
                "messages": [
                    {"role": "user", "content": "May available bang tulong?"},
                    {"role": "assistant", "content": "Oo, may tulong na available."},
                ],
                "evidence": ["May tulong na available."],
            }
        ),
    ]
    rows = run_config_pipeline(config, client=StaticClient(responses))
    assert [row.language for row in rows] == ["english", "tagalog"]
    assert [row.generation_parameters["source_language_origin"] for row in rows] == [
        "record_field", "record_field"
    ]
    sources = [json.loads(line) for line in (config.output.directory / "sources.jsonl").read_text().splitlines()]
    chunks = [json.loads(line) for line in (config.output.directory / "chunks.jsonl").read_text().splitlines()]
    assert [row["language"] for row in sources] == ["english", "tagalog"]
    assert [row["language"] for row in chunks] == ["english", "tagalog"]

    parquet_path = tmp_path / "mixed.parquet"
    csv_path = tmp_path / "mixed.csv"
    legacy_path = tmp_path / "mixed-legacy.jsonl"
    dataset = config.output.directory / "dataset.jsonl"
    export_parquet(dataset, parquet_path)
    export_csv(dataset, csv_path)
    export_legacy_jsonl(dataset, legacy_path)
    assert parquet.read_table(parquet_path).column("language").to_pylist() == ["english", "tagalog"]
    assert "english" in csv_path.read_text(encoding="utf-8")
    legacy = [json.loads(line) for line in legacy_path.read_text().splitlines()]
    assert [row["provenance"]["language"] for row in legacy] == ["english", "tagalog"]
