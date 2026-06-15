# tests/test_retriever.py
from plan2.backend.retriever import filter_by_acl

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
