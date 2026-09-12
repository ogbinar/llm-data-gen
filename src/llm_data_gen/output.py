from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import BaseModel

from .config import RunConfig, redacted_config_dict
from .models import (
    CheckpointRecord,
    Chunk,
    ParsedExample,
    RejectedRecord,
    RunManifest,
    SFTRecord,
    SkippedExample,
    SourceDocument,
)


def append_jsonl(path: Path, rows: list[ParsedExample]) -> None:
    _append_models(path, rows)


def append_skipped_jsonl(path: Path, rows: list[SkippedExample]) -> None:
    _append_models(path, rows)


def load_existing_example_ids(path: Path) -> set[str]:
    return {
        str(payload["example_id"])
        for payload in _read_jsonl(path)
        if payload.get("example_id")
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return _read_jsonl(path)


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    _write_jsonl_atomic(path, rows)


def write_text_atomic(path: Path, content: str) -> None:
    _atomic_text(path, content)


class DatasetWriter:
    def __init__(self, config: RunConfig, config_hash: str):
        self.config = config
        self.config_hash = config_hash
        self.directory = config.output.directory
        self.manifest_path = self.directory / "manifest.json"
        self.dataset_path = self.directory / "dataset.jsonl"
        self.rejected_path = self.directory / "rejected.jsonl"
        self.checkpoint_path = self.directory / "checkpoint.jsonl"
        self.resumed = self.manifest_path.exists() or self.checkpoint_path.exists()
        self._validate_existing_output()

    def initialize(
        self,
        manifest: RunManifest,
        sources: list[SourceDocument],
        chunks: list[Chunk],
    ) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        _atomic_text(
            self.directory / "config.resolved.yaml",
            yaml.safe_dump(redacted_config_dict(self.config), sort_keys=False, allow_unicode=True),
        )
        source_rows = [
            source.model_dump(mode="json", exclude={"text"})
            for source in sources
        ]
        chunk_rows = []
        for chunk in chunks:
            row = chunk.model_dump(mode="json")
            if not self.config.output.include_chunk_text:
                row["text_sha256"] = _sha256_text(row.pop("text"))
            chunk_rows.append(row)
        _write_jsonl_atomic(self.directory / "sources.jsonl", source_rows)
        _write_jsonl_atomic(self.directory / "chunks.jsonl", chunk_rows)
        for path in (self.dataset_path, self.rejected_path, self.checkpoint_path):
            if not path.exists():
                _atomic_text(path, "")
        self.write_manifest(manifest)

    def terminal_job_ids(self) -> set[str]:
        return {
            str(payload["job_id"])
            for payload in _read_jsonl(self.checkpoint_path)
            if payload.get("job_id")
        }

    def retryable_job_ids(self, stages: set[str]) -> set[str]:
        if not stages:
            return set()
        latest_stage: dict[str, str] = {}
        for payload in _read_jsonl(self.rejected_path):
            job_id = payload.get("job_id")
            if job_id:
                latest_stage[str(job_id)] = str(payload.get("failure_stage") or "")
        latest_outcome: dict[str, str] = {}
        for payload in _read_jsonl(self.checkpoint_path):
            job_id = payload.get("job_id")
            if job_id:
                latest_outcome[str(job_id)] = str(payload.get("outcome") or "")
        return {
            job_id
            for job_id, stage in latest_stage.items()
            if stage in stages and latest_outcome.get(job_id) != "accepted"
        }

    def accepted_signatures(self) -> dict[str, str]:
        signatures: dict[str, str] = {}
        for payload in _read_jsonl(self.dataset_path):
            messages = payload.get("messages") or []
            signature = _message_signature(messages)
            if signature:
                signatures[signature] = str(payload.get("example_id") or "")
        return signatures

    def append_record(self, record: SFTRecord) -> None:
        _append_models(self.dataset_path, [record])
        checkpoint = CheckpointRecord(
            job_id=record.job_id,
            outcome="accepted",
            example_id=record.example_id,
            recorded_at=record.generated_at,
        )
        _append_models(self.checkpoint_path, [checkpoint])

    def append_rejection(
        self,
        rejection: RejectedRecord,
        *,
        outcome: str = "rejected",
    ) -> None:
        _append_models(self.rejected_path, [rejection])
        if rejection.job_id:
            checkpoint = CheckpointRecord(
                job_id=rejection.job_id,
                outcome=outcome,
                reason=rejection.failure_reason,
                recorded_at=rejection.recorded_at,
            )
            _append_models(self.checkpoint_path, [checkpoint])

    def write_manifest(self, manifest: RunManifest) -> None:
        _atomic_text(
            self.manifest_path,
            json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        )

    def summarize(self) -> dict[str, Any]:
        checkpoints = _read_jsonl(self.checkpoint_path)
        accepted_rows = _read_jsonl(self.dataset_path)
        rejected_rows = _read_jsonl(self.rejected_path)
        latest_outcomes: dict[str, str] = {}
        for row in checkpoints:
            job_id = str(row.get("job_id") or "")
            if job_id:
                latest_outcomes[job_id] = str(row.get("outcome") or "unknown")
        outcomes: dict[str, int] = {}
        for outcome in latest_outcomes.values():
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        by_format: dict[str, int] = {}
        by_language: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for row in accepted_rows:
            qa_format = str(row.get("qa_format") or "unknown")
            language = str(row.get("language") or "unknown")
            by_format[qa_format] = by_format.get(qa_format, 0) + 1
            by_language[language] = by_language.get(language, 0) + 1
            source_id = str(row.get("source_id") or "unknown")
            by_source[source_id] = by_source.get(source_id, 0) + 1
        failures_by_stage: dict[str, int] = {}
        for row in rejected_rows:
            stage = str(row.get("failure_stage") or "unknown")
            failures_by_stage[stage] = failures_by_stage.get(stage, 0) + 1
        return {
            "accepted": outcomes.get("accepted", 0),
            "rejected": outcomes.get("rejected", 0),
            "not_applicable": outcomes.get("not_applicable", 0),
            "accepted_by_format": by_format,
            "accepted_by_language": by_language,
            "accepted_by_source": by_source,
            "failures_by_stage": failures_by_stage,
        }

    def _validate_existing_output(self) -> None:
        if not self.manifest_path.exists():
            return
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        existing_hash = payload.get("config_hash")
        if existing_hash and existing_hash != self.config_hash:
            raise ValueError(
                "output directory contains a different resolved configuration; choose a new directory"
            )
        if not self.config.output.resume and (
            self.dataset_path.exists() or self.checkpoint_path.exists()
        ):
            raise ValueError("output directory already contains run data and resume=false")


def export_parquet(dataset_path: Path, output_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as parquet

    rows = []
    for payload in _read_jsonl(dataset_path):
        row = dict(payload)
        for field in ("metadata", "generation_parameters"):
            row[field] = json.dumps(row.get(field) or {}, ensure_ascii=False, sort_keys=True)
        rows.append(row)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    parquet.write_table(pa.Table.from_pylist(rows), output_path)


def export_legacy_jsonl(dataset_path: Path, output_path: Path) -> None:
    rows: list[dict[str, Any]] = []
    for payload in _read_jsonl(dataset_path):
        messages = payload.get("messages") or []
        users = [message["content"] for message in messages if message.get("role") == "user"]
        assistants = [
            message["content"] for message in messages if message.get("role") == "assistant"
        ]
        rows.append(
            {
                "example_id": payload.get("example_id"),
                "source_id": payload.get("source_id"),
                "chunk_id": payload.get("chunk_id"),
                "task_type": payload.get("qa_format"),
                "input_text": users[0] if users else "",
                "output_text": assistants[-1] if assistants else "",
                "provenance": {
                    "schema_version": payload.get("schema_version"),
                    "language": payload.get("language"),
                    "prompt_version": payload.get("prompt_version"),
                },
            }
        )
    _write_jsonl_atomic(output_path, rows)


def export_csv(dataset_path: Path, output_path: Path) -> None:
    rows = _read_jsonl(dataset_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "example_id", "source_id", "chunk_id", "domain", "qa_format", "language",
        "messages", "evidence", "prompt_version", "generator_model", "inference_backend",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: json.dumps(row.get(field), ensure_ascii=False)
                    if isinstance(row.get(field), (dict, list))
                    else row.get(field)
                    for field in fields
                }
            )


def _append_models(path: Path, rows: Iterable[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(row.model_dump_json())
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    _atomic_text(path, content)


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _message_signature(messages: list[dict[str, Any]]) -> str:
    normalized = [
        {
            "role": message.get("role"),
            "content": " ".join(str(message.get("content") or "").split()).casefold(),
        }
        for message in messages
    ]
    return _sha256_text(json.dumps(normalized, sort_keys=True, separators=(",", ":")))


def _sha256_text(value: str) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
