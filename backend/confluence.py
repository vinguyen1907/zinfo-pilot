import hashlib, hmac, os, time
from typing import Any
import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("CONFLUENCE_BASE_URL", "").rstrip("/")
ADMIN_TOKEN = os.getenv("CONFLUENCE_ADMIN_TOKEN", "")
ADMIN_EMAIL = os.getenv("CONFLUENCE_ADMIN_EMAIL", "")
WEBHOOK_SECRET = os.getenv("CONFLUENCE_WEBHOOK_SECRET", "")

SPACE_KEYS = [k.strip() for k in os.getenv("CONFLUENCE_SPACE_KEYS", "").split(",") if k.strip()]

_acl_cache: dict[str, tuple[set[str], float]] = {}
_space_cache: dict[str, tuple[set[str], float]] = {}
_CACHE_TTL = 120  # seconds
_OPEN_GROUPS = {"confluence-users", "all-users", "site-admins"}

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

def _get_account_id(email: str) -> str | None:
    resp = httpx.get(
        f"{BASE_URL}/wiki/rest/api/user/search",
        params={"query": email, "limit": 10},
        auth=_auth(),
        timeout=10,
    )
    resp.raise_for_status()
    for user in resp.json().get("results", []):
        if user.get("email") == email:
            return user.get("accountId")
    return None


def _user_has_space_read(perms: list[dict], account_id: str | None) -> bool:
    read_entries = [
        p for p in perms
        if p.get("operation", {}).get("operation") == "read"
        and p.get("operation", {}).get("targetType") == "space"
    ]
    if not read_entries:
        return True  # No explicit read restriction = open space
    for entry in read_entries:
        if entry.get("anonymousAccess"):
            return True
        subjects = entry.get("subjects", {})
        if account_id:
            for u in subjects.get("user", {}).get("results", []):
                if u.get("accountId") == account_id:
                    return True
        for g in subjects.get("group", {}).get("results", []):
            if g.get("name") in _OPEN_GROUPS:
                return True
    return False


def get_accessible_space_keys(email: str) -> set[str]:
    cached = _space_cache.get(email)
    if cached and (time.time() - cached[1]) < _CACHE_TTL:
        return cached[0]

    account_id = _get_account_id(email)
    accessible: set[str] = set()

    for space_key in SPACE_KEYS:
        resp = httpx.get(
            f"{BASE_URL}/wiki/rest/api/space/{space_key}/permission",
            auth=_auth(),
            timeout=10,
        )
        if resp.status_code != 200:
            continue
        if _user_has_space_read(resp.json(), account_id):
            accessible.add(space_key)

    _space_cache[email] = (accessible, time.time())
    return accessible


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

def list_all_pages_in_space(space_key: str) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    start, limit = 0, 50
    while True:
        resp = httpx.get(
            f"{BASE_URL}/wiki/rest/api/content",
            params={
                "type": "page",
                "spaceKey": space_key,
                "start": start,
                "limit": limit,
                "expand": "body.storage,space,version,_links",
            },
            auth=_auth(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        pages.extend(results)
        if len(results) < limit:
            break
        start += limit
    return pages
