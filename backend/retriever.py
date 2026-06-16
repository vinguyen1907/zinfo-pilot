from backend.chroma_client import get_collection as _collection
from backend.confluence import get_accessible_page_ids, get_accessible_space_keys


def filter_by_acl(candidates: list[dict], allowed_page_ids: set[str], top_k: int = 5) -> list[dict]:
    return [c for c in candidates if c["metadata"]["page_id"] in allowed_page_ids][:top_k]


def retrieve(query: str, email: str, top_k: int = 5) -> list[dict]:
    allowed_spaces = get_accessible_space_keys(email)
    if not allowed_spaces:
        return []

    col = _collection()
    results = col.query(
        query_texts=[query],
        n_results=20,
        include=["documents", "metadatas"],
        where={"space_key": {"$in": sorted(allowed_spaces)}},
    )

    candidates = [
        {
            "document": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
        }
        for i in range(len(results["ids"][0]))
    ]

    allowed_pages = get_accessible_page_ids(email)
    return filter_by_acl(candidates, allowed_pages, top_k=top_k)
