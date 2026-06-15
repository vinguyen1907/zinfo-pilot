# tests/test_indexer.py
from plan2.backend.indexer import make_chunk_id, split_page_into_chunks

def test_chunk_id_format():
    assert make_chunk_id("12345", 0) == "12345_0"
    assert make_chunk_id("12345", 7) == "12345_7"

def test_chunk_ids_unique_across_pages():
    assert make_chunk_id("111", 0) != make_chunk_id("222", 0)

def test_split_long_text_produces_multiple_chunks():
    text = "word " * 300  # ~1500 chars, exceeds 500-char chunk size
    chunks = split_page_into_chunks("999", "Test Page", "http://x", "DEV", "2024-01-01", text)
    assert len(chunks) > 1
    for i, (doc, meta) in enumerate(chunks):
        assert meta["page_id"] == "999"
        assert meta["chunk_index"] == i
        assert meta["page_title"] == "Test Page"
        assert meta["page_url"] == "http://x"
        assert meta["space_key"] == "DEV"
        assert "word" in doc

def test_split_short_text_single_chunk():
    chunks = split_page_into_chunks("1", "Short", "http://x", "DEV", "2024-01-01", "hello world")
    assert len(chunks) == 1
    assert chunks[0][1]["chunk_index"] == 0
