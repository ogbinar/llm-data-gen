import csv
import json

import pyarrow as pa
import pyarrow.parquet as parquet

from llm_data_gen.chunking import chunk_document
from llm_data_gen.config import ChunkingConfig, InputConfig
from llm_data_gen.models import SourceDocument
from llm_data_gen.readers import read_corpus


def test_directory_corpus_is_sorted_and_jsonl_iterates_all_records(tmp_path):
    (tmp_path / "b.txt").write_text("Second document.", encoding="utf-8")
    (tmp_path / "a.jsonl").write_text(
        '{"source_id":"one","text":"First record.","language":"filipino"}\n'
        'not-json\n'
        '{"source_id":"two","text":"Second record.","language":"taglish"}\n',
        encoding="utf-8",
    )
    documents, failures = read_corpus(
        InputConfig(path=tmp_path, language="english", language_field="language")
    )
    assert [document.source_id for document in documents] == ["one", "two", documents[-1].source_id]
    assert documents[-1].title == "b"
    assert [document.language for document in documents] == ["filipino", "taglish", "english"]
    assert [document.language_origin for document in documents] == [
        "record_field", "record_field", "input_default"
    ]
    assert len(failures) == 1
    assert failures[0].record_index == 2


def test_csv_and_parquet_readers(tmp_path):
    csv_path = tmp_path / "items.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_id", "text", "topic"])
        writer.writeheader()
        writer.writerow({"source_id": "csv-1", "text": "CSV text", "topic": "billing"})
    csv_docs, csv_failures = read_corpus(InputConfig(path=csv_path, language="english"))
    assert not csv_failures
    assert csv_docs[0].metadata["topic"] == "billing"

    parquet_path = tmp_path / "items.parquet"
    parquet.write_table(
        pa.Table.from_pylist([{"source_id": "pq-1", "text": "Parquet text"}]),
        parquet_path,
    )
    parquet_docs, parquet_failures = read_corpus(
        InputConfig(path=parquet_path, language="english")
    )
    assert not parquet_failures
    assert parquet_docs[0].source_id == "pq-1"


def test_duplicate_source_ids_are_isolated(tmp_path):
    path = tmp_path / "duplicates.jsonl"
    path.write_text(
        '{"source_id":"same","text":"First"}\n'
        '{"source_id":"same","text":"Second"}\n',
        encoding="utf-8",
    )
    documents, failures = read_corpus(InputConfig(path=path, language="english"))
    assert [document.text for document in documents] == ["First"]
    assert len(failures) == 1
    assert "duplicate source_id" in failures[0].failure_reason


def make_document(text: str) -> SourceDocument:
    return SourceDocument(
        source_id="doc",
        title="Document",
        text=text,
        language="english",
        provenance_path="/tmp/doc.md",
        source_format="md",
    )


def test_document_and_recursive_chunkers():
    document = make_document("One paragraph.\n\nTwo paragraph.\n\nThree paragraph.")
    whole = chunk_document(document, ChunkingConfig(strategy="document", chunk_size=100))
    split = chunk_document(
        document,
        ChunkingConfig(strategy="recursive_text", chunk_size=25, chunk_overlap=5),
    )
    assert len(whole) == 1
    assert len(split) > 1
    assert split[0].chunk_id == "doc::recursive_text::v1::0001"
    assert split[1].start_offset < split[0].end_offset


def test_markdown_sections_and_fixed_tokens():
    document = make_document("# One\nAlpha beta.\n\n# Two\nGamma delta epsilon.")
    sections = chunk_document(
        document,
        ChunkingConfig(strategy="markdown_sections", chunk_size=100),
    )
    tokens = chunk_document(
        document,
        ChunkingConfig(
            strategy="fixed_tokens",
            chunk_size=3,
            chunk_overlap=1,
            unit="tokens",
        ),
    )
    assert len(sections) == 2
    assert sections[0].text.startswith("# One")
    assert len(tokens) >= 2
    assert all(chunk.token_estimate <= 3 for chunk in tokens)


def test_structured_language_default_override_and_invalid_label(tmp_path):
    path = tmp_path / "mixed.jsonl"
    path.write_text(
        '{"source_id":"default","text":"English row."}\n'
        '{"source_id":"override","text":"Kumusta po.","lang":"tagalog"}\n'
        '{"source_id":"bad","text":"Bad label.","lang":"klingon"}\n',
        encoding="utf-8",
    )
    documents, failures = read_corpus(
        InputConfig(path=path, language="english", language_field="lang")
    )
    assert [(item.source_id, item.language, item.language_origin) for item in documents] == [
        ("default", "english", "input_default"),
        ("override", "tagalog", "record_field"),
    ]
    assert len(failures) == 1
    assert failures[0].record_index == 3
    assert "unknown source language" in failures[0].failure_reason

    chunks = [chunk_document(item, ChunkingConfig(strategy="document"))[0] for item in documents]
    assert [(item.language, item.language_origin) for item in chunks] == [
        ("english", "input_default"),
        ("tagalog", "record_field"),
    ]


def test_json_csv_and_parquet_language_field_overrides(tmp_path):
    json_path = tmp_path / "items.json"
    json_path.write_text(
        '[{"source_id":"json-1","text":"Kumusta.","lang":"filipino"}]',
        encoding="utf-8",
    )
    csv_path = tmp_path / "items.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_id", "text", "lang"])
        writer.writeheader()
        writer.writerow({"source_id": "csv-1", "text": "Hello.", "lang": "english"})
    parquet_path = tmp_path / "items.parquet"
    parquet.write_table(
        pa.Table.from_pylist(
            [{"source_id": "pq-1", "text": "Hello po.", "lang": "taglish"}]
        ),
        parquet_path,
    )

    expected = [
        (json_path, "filipino"),
        (csv_path, "english"),
        (parquet_path, "taglish"),
    ]
    for path, language in expected:
        documents, failures = read_corpus(
            InputConfig(path=path, language="tagalog", language_field="lang")
        )
        assert not failures
        assert documents[0].language == language
        assert documents[0].language_origin == "record_field"
        assert "lang" not in documents[0].metadata
