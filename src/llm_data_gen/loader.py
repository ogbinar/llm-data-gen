from __future__ import annotations

from pathlib import Path

from .config import InputConfig
from .models import SourceDocument
from .readers import read_corpus


def load_source_document(path: Path, source_id: str | None = None) -> SourceDocument:
    documents, failures = read_corpus(InputConfig(path=path))
    if source_id is not None:
        for document in documents:
            if document.source_id == source_id:
                return document
        raise ValueError(f"source_id {source_id!r} not found in input")
    if documents:
        return documents[0]
    if failures:
        raise ValueError(failures[0].failure_reason)
    raise ValueError(f"No records found in {path}")

