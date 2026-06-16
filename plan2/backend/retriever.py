import os

from dotenv import load_dotenv
from plan2.backend.chroma_client import get_collection as _collection
from plan2.backend.confluence import get_accessible_page_ids

load_dotenv()


def filter_by_acl(candidates: list[dict], allowed_page_ids: set[str], top_k: int = 5) -> list[dict]:
    return [c for c in candidates if c["metadata"]["page_id"] in allowed_page_ids][:top_k]


def retrieve(query: str, email: str, top_k: int = 5) -> list[dict]:
    col = _collection()
    results = col.query(query_texts=[query], n_results=20, include=["documents", "metadatas"])

    candidates = [
        {
            "document": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
        }
        for i in range(len(results["ids"][0]))
    ]

    allowed = get_accessible_page_ids(email)
    return filter_by_acl(candidates, allowed, top_k=top_k)
