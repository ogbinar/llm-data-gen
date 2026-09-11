from llm_data_gen.loader import load_source_document


def test_load_jsonl_selects_first_record(tmp_path):
    path = tmp_path / "sample.jsonl"
    path.write_text(
        '{"source_id":"a","title":"One","text":"Hello"}\n{"source_id":"b","title":"Two","text":"World"}\n',
        encoding="utf-8",
    )
    doc = load_source_document(path)
    assert doc.source_id == "a"
    assert doc.text == "Hello"

