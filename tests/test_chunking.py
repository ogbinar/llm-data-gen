from pathlib import Path

from llm_data_gen.chunking import chunk_document
from llm_data_gen.models import SourceDocument


def test_chunking_is_deterministic():
    doc = SourceDocument(
        source_id="doc-1",
        title="Example",
        text="Paragraph one.\n\nParagraph two.\n\nParagraph three.",
        language="english",
        provenance_path="/tmp/example.md",
    )
    first = chunk_document(doc, 20)
    second = chunk_document(doc, 20)
    assert [chunk.text for chunk in first] == [chunk.text for chunk in second]
    assert first[0].chunk_id == "doc-1-chunk-001"


def test_short_fixture_stays_single_chunk():
    text = (Path(__file__).parent / "fixtures" / "short_doc.txt").read_text(encoding="utf-8")
    doc = SourceDocument(
        source_id="fixture-short",
        title="Short",
        text=text,
        language="english",
        provenance_path="/tmp/short.txt",
    )
    chunks = chunk_document(doc, 500)
    assert len(chunks) == 1


def test_long_fixture_splits():
    text = (Path(__file__).parent / "fixtures" / "long_doc.txt").read_text(encoding="utf-8")
    doc = SourceDocument(
        source_id="fixture-long",
        title="Long",
        text=text * 30,
        language="english",
        provenance_path="/tmp/long.txt",
    )
    chunks = chunk_document(doc, 120)
    assert len(chunks) > 1
