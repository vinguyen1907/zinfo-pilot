import hashlib, hmac, os, time
from typing import Any
import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("CONFLUENCE_BASE_URL", "").rstrip("/")
ADMIN_TOKEN = os.getenv("CONFLUENCE_ADMIN_TOKEN", "")
ADMIN_EMAIL = os.getenv("CONFLUENCE_ADMIN_EMAIL", "")
WEBHOOK_SECRET = os.getenv("CONFLUENCE_WEBHOOK_SECRET", "")

_acl_cache: dict[str, tuple[set[str], float]] = {}
_CACHE_TTL = 120  # seconds

def _auth() -> tuple[str, str]:
    return (ADMIN_EMAIL, ADMIN_TOKEN)

def validate_webhook(headers: dict, body: bytes) -> bool:
    if not WEBHOOK_SECRET:
        return False
    sig = headers.get("x-hub-signature", "")
    if not sig.startswith("sha256="):
        return False
    expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig[7:], expected)

def _parse_accessible_page_ids(response: dict) -> set[str]:
    return {str(r["id"]) for r in response.get("results", [])}

def get_accessible_page_ids(email: str) -> set[str]:
    cached = _acl_cache.get(email)
    if cached and (time.time() - cached[1]) < _CACHE_TTL:
        return cached[0]

    all_ids: set[str] = set()
    start, limit = 0, 200
    while True:
        resp = httpx.get(
            f"{BASE_URL}/wiki/rest/api/content",
            params={
                "type": "page",
                "start": start,
                "limit": limit,
                "expand": "restrictions.read.restrictions.user",
            },
            auth=_auth(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        for page in data.get("results", []):
            users = (
                page.get("restrictions", {})
                .get("read", {})
                .get("restrictions", {})
                .get("user", {})
                .get("results", [])
            )
            # No read restrictions = accessible to everyone; or email is in the allowed list
            if not users or any(u.get("email") == email for u in users):
                all_ids.add(str(page["id"]))
        if len(data.get("results", [])) < limit:
            break
        start += limit

    _acl_cache[email] = (all_ids, time.time())
    return all_ids

def get_page_content(page_id: str) -> dict[str, Any]:
    resp = httpx.get(
        f"{BASE_URL}/wiki/rest/api/content/{page_id}",
        params={"expand": "body.storage,space,version,_links"},
        auth=_auth(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
