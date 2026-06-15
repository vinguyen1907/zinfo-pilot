# Stub implementation of retrieve().
# Plan 2 replaces this with backend/retriever.py (real ChromaDB + Confluence ACL).
# Interface contract: returns list[dict] where each dict has:
#   document: str          — the text chunk
#   metadata: dict         — must contain page_id, page_title, page_url

_FAKE_CHUNKS = [
    {
        "document": "To deploy a new service, push your Docker image to the internal registry, update the Helm values file with the new tag, and open a PR to the infra-config repo for Platform team review.",
        "metadata": {
            "page_id": "stub-001",
            "page_title": "DevOps Runbook",
            "page_url": "https://example.atlassian.net/wiki/spaces/DEV/pages/stub-001",
        },
    },
    {
        "document": "New employees get read access to the ONBOARD and DEV spaces automatically. INFRA space requires manager approval via the Access Request Form.",
        "metadata": {
            "page_id": "stub-002",
            "page_title": "Onboarding Guide",
            "page_url": "https://example.atlassian.net/wiki/spaces/ONBOARD/pages/stub-002",
        },
    },
]

def retrieve(query: str, email: str, top_k: int = 5) -> list[dict]:
    """Stub: always returns the same two fake chunks regardless of query or email."""
    return _FAKE_CHUNKS[:top_k]
