# tests/test_retriever.py
from unittest.mock import MagicMock
from backend.retriever import filter_by_acl

def test_filter_keeps_allowed_pages():
    candidates = [
        {"document": "doc1", "metadata": {"page_id": "p1", "page_title": "A", "page_url": "http://a"}},
        {"document": "doc2", "metadata": {"page_id": "p2", "page_title": "B", "page_url": "http://b"}},
        {"document": "doc3", "metadata": {"page_id": "p3", "page_title": "C", "page_url": "http://c"}},
    ]
    result = filter_by_acl(candidates, {"p1", "p3"}, top_k=5)
    assert len(result) == 2
    assert {r["metadata"]["page_id"] for r in result} == {"p1", "p3"}

def test_filter_respects_top_k():
    candidates = [
        {"document": f"doc{i}", "metadata": {"page_id": f"p{i}", "page_title": f"T{i}", "page_url": f"http://{i}"}}
        for i in range(10)
    ]
    result = filter_by_acl(candidates, {f"p{i}" for i in range(10)}, top_k=3)
    assert len(result) == 3

def test_filter_no_access_returns_empty():
    candidates = [
        {"document": "doc", "metadata": {"page_id": "p1", "page_title": "A", "page_url": "http://a"}},
    ]
    assert filter_by_acl(candidates, set(), top_k=5) == []

def test_filter_preserves_order():
    candidates = [
        {"document": f"doc{i}", "metadata": {"page_id": f"p{i}", "page_title": f"T{i}", "page_url": ""}}
        for i in [3, 1, 2]
    ]
    result = filter_by_acl(candidates, {"p3", "p1", "p2"}, top_k=5)
    assert [r["metadata"]["page_id"] for r in result] == ["p3", "p1", "p2"]


def test_retrieve_pre_filters_by_space(monkeypatch):
    mock_col = MagicMock()
    # Mock simulates ChromaDB applying the where filter — only SD space chunk returned
    mock_col.query.return_value = {
        "ids": [["id1"]],
        "documents": [["doc for p1"]],
        "metadatas": [[
            {"page_id": "p1", "space_key": "SD", "page_title": "T1", "page_url": ""},
        ]],
    }
    monkeypatch.setattr("backend.retriever._collection", lambda: mock_col)
    monkeypatch.setattr("backend.retriever.get_accessible_space_keys", lambda email: {"SD"})
    monkeypatch.setattr("backend.retriever.get_accessible_page_ids", lambda email: {"p1"})

    from backend.retriever import retrieve
    result = retrieve("query", "user@test.com", top_k=5)

    call_kwargs = mock_col.query.call_args[1]
    assert "where" in call_kwargs
    assert call_kwargs["where"] == {"space_key": {"$in": ["SD"]}}
    assert len(result) == 1
    assert result[0]["metadata"]["page_id"] == "p1"


def test_retrieve_returns_empty_when_no_spaces(monkeypatch):
    monkeypatch.setattr("backend.retriever.get_accessible_space_keys", lambda email: set())
    monkeypatch.setattr("backend.retriever._collection", MagicMock)

    from backend.retriever import retrieve
    assert retrieve("query", "no-access@test.com") == []
