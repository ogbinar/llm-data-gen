from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from .config import InputConfig
from .models import SourceDocument, SourceFailure
from .languages import resolve_language

SUPPORTED_EXTENSIONS = {".txt", ".md", ".json", ".jsonl", ".csv", ".parquet"}


def read_corpus(config: InputConfig) -> tuple[list[SourceDocument], list[SourceFailure]]:
    documents: list[SourceDocument] = []
    failures: list[SourceFailure] = []
    seen_source_ids: set[str] = set()
    for path in _discover_paths(config):
        try:
            records = _read_records(path, config)
        except Exception as exc:
            failures.append(
                SourceFailure(
                    source_path=str(path),
                    failure_stage="source",
                    failure_reason=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        for index, record in records:
            try:
                document = _document_from_record(path, index, record, config)
                if document.source_id in seen_source_ids:
                    raise ValueError(f"duplicate source_id: {document.source_id}")
                seen_source_ids.add(document.source_id)
                documents.append(document)
            except Exception as exc:
                failures.append(
                    SourceFailure(
                        source_path=str(path),
                        record_index=index,
                        failure_stage="source",
                        failure_reason=f"{type(exc).__name__}: {exc}",
                    )
                )
    return documents, failures


def _discover_paths(config: InputConfig) -> list[Path]:
    path = config.path
    if path.is_file():
        return [path]
    if not path.exists():
        raise FileNotFoundError(f"corpus path does not exist: {path}")
    if not path.is_dir():
        raise ValueError(f"corpus path is not a file or directory: {path}")
    iterator = path.rglob("*") if config.recursive else path.glob("*")
    paths = [candidate for candidate in iterator if candidate.is_file()]
    if config.format == "auto":
        paths = [candidate for candidate in paths if candidate.suffix.lower() in SUPPORTED_EXTENSIONS]
    return sorted(paths, key=lambda candidate: candidate.as_posix())


def _read_records(path: Path, config: InputConfig) -> list[tuple[int, dict[str, Any]]]:
    source_format = config.format if config.format != "auto" else path.suffix.lower().lstrip(".")
    if source_format in {"txt", "md"}:
        return [(1, {"text": path.read_text(encoding="utf-8"), "title": path.stem})]
    if source_format == "json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else [payload]
        return [(index, _ensure_mapping(row)) for index, row in enumerate(rows, start=1)]
    if source_format == "jsonl":
        rows: list[tuple[int, dict[str, Any]]] = []
        with path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    rows.append((index, _ensure_mapping(json.loads(line))))
                except Exception as exc:
                    rows.append((index, {"__read_error__": f"{type(exc).__name__}: {exc}"}))
        return rows
    if source_format == "csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [(index, dict(row)) for index, row in enumerate(csv.DictReader(handle), start=1)]
    if source_format == "parquet":
        import pyarrow.parquet as parquet

        rows = parquet.read_table(path).to_pylist()
        return [(index, _ensure_mapping(row)) for index, row in enumerate(rows, start=1)]
    raise ValueError(f"unsupported input format: {source_format}")


def _document_from_record(
    path: Path,
    index: int,
    record: dict[str, Any],
    config: InputConfig,
) -> SourceDocument:
    if "__read_error__" in record:
        raise ValueError(record["__read_error__"])
    raw_text = record.get(config.text_field)
    text = "" if raw_text is None else str(raw_text).strip()
    if not text:
        raise ValueError(f"record has no non-empty {config.text_field!r} field")

    source_id_value = record.get(config.id_field)
    source_id = str(source_id_value).strip() if source_id_value is not None else ""
    if not source_id:
        source_id = _stable_source_id(path, index)
    title_value = record.get(config.title_field)
    title = str(title_value).strip() if title_value is not None else path.stem
    checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()

    source_format = config.format if config.format != "auto" else path.suffix.lower().lstrip(".")
    language = config.language
    language_origin = "input_default"
    if source_format in {"json", "jsonl", "csv", "parquet"} and config.language_field:
        record_language = record.get(config.language_field)
        if record_language is not None and str(record_language).strip():
            language = resolve_language(str(record_language)).name
            language_origin = "record_field"

    excluded = {config.id_field, config.text_field, config.title_field}
    if config.language_field:
        excluded.add(config.language_field)
    if config.metadata_fields is None:
        metadata = {key: _json_safe(value) for key, value in record.items() if key not in excluded}
    else:
        metadata = {
            key: _json_safe(record[key])
            for key in config.metadata_fields
            if key in record and key not in excluded
        }

    return SourceDocument(
        source_id=source_id,
        title=title or path.stem,
        text=text,
        language=language,
        language_origin=language_origin,
        doc_type=str(record.get("doc_type") or "document"),
        source_kind=str(record.get("source_kind") or "local"),
        source_format=source_format,
        source_url=_optional_string(record.get("source_url")),
        provenance_note=_optional_string(record.get("provenance_note")),
        provenance_path=str(path),
        source_checksum=checksum,
        domain=str(record.get("domain") or config.domain),
        metadata=metadata,
    )


def _stable_source_id(path: Path, index: int) -> str:
    digest = hashlib.sha256(f"{path.resolve()}::{index}".encode("utf-8")).hexdigest()[:16]
    return f"{path.stem}-{digest}"


def _ensure_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("source record must be an object")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _json_safe(value: Any) -> Any:
    if hasattr(value, "as_py"):
        return value.as_py()
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)
