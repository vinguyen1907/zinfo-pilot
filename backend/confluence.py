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

_CACHE_TTL = 120  # seconds

# email → accountId (permanent; accountIds don't change)
_account_id_cache: dict[str, str] = {}
# email → (set[group_id], timestamp)
_group_cache: dict[str, tuple[set[str], float]] = {}
# email → (set[space_key], timestamp)
_space_access_cache: dict[str, tuple[set[str], float]] = {}
# email → (set[page_id], timestamp)
_acl_cache: dict[str, tuple[set[str], float]] = {}


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
    """Resolve email → Confluence accountId.

    Two strategies:
    1. Scan space permission subjects — users with explicit grants have email+accountId.
    2. For personal spaces (~accountId), fetch user by accountId and compare email.
    Returns None if resolution fails (user hasn't been granted explicit space access).
    """
    if email in _account_id_cache:
        return _account_id_cache[email]

    # Strategy 1: find user in space permission subjects
    for space_key in SPACE_KEYS:
        resp = httpx.get(
            f"{BASE_URL}/wiki/rest/api/space/{space_key}",
            params={"expand": "permissions"},
            auth=_auth(),
            timeout=10,
        )
        if resp.status_code != 200:
            continue
        for perm in resp.json().get("permissions", []):
            for user in perm.get("subjects", {}).get("user", {}).get("results", []):
                if user.get("email") == email:
                    account_id = user.get("accountId", "")
                    if account_id:
                        _account_id_cache[email] = account_id
                        return account_id

    # Strategy 2: probe personal-space accountIds via GET /user?accountId=
    for space_key in SPACE_KEYS:
        if not space_key.startswith("~"):
            continue
        candidate_id = space_key[1:]
        resp = httpx.get(
            f"{BASE_URL}/wiki/rest/api/user",
            params={"accountId": candidate_id},
            auth=_auth(),
            timeout=10,
        )
        if resp.status_code == 200 and resp.json().get("email") == email:
            _account_id_cache[email] = candidate_id
            return candidate_id

    return None


def _get_user_group_ids(account_id: str) -> set[str]:
    """Return the set of group IDs the user belongs to."""
    groups: set[str] = set()
    start = 0
    while True:
        resp = httpx.get(
            f"{BASE_URL}/wiki/rest/api/user/memberof",
            params={"accountId": account_id, "start": start, "limit": 200},
            auth=_auth(),
            timeout=10,
        )
        if resp.status_code != 200:
            break
        data = resp.json()
        for g in data.get("results", []):
            gid = g.get("id") or g.get("groupId", "")
            if gid:
                groups.add(gid)
        if len(data.get("results", [])) < 200:
            break
        start += 200
    return groups


def _get_read_space_perms(space_key: str) -> list[dict]:
    """Fetch and return only the read/space permission entries for a space."""
    resp = httpx.get(
        f"{BASE_URL}/wiki/rest/api/space/{space_key}",
        params={"expand": "permissions"},
        auth=_auth(),
        timeout=10,
    )
    if resp.status_code != 200:
        return []
    return [
        p for p in resp.json().get("permissions", [])
        if p.get("operation", {}).get("operation") == "read"
        and p.get("operation", {}).get("targetType") == "space"
    ]


def _user_can_read_space(
    space_key: str,
    perms: list[dict],
    email: str,
    account_id: str | None,
    user_group_ids: set[str],
) -> bool:
    for perm in perms:
        if perm.get("anonymousAccess") or perm.get("unlicensedAccess"):
            return True

        subj = perm.get("subjects", {})

        # Direct user grant (check both email and accountId for robustness)
        users = subj.get("user", {}).get("results", [])
        for u in users:
            if u.get("email") == email:
                return True
            if account_id and u.get("accountId") == account_id:
                return True

        # Group grant
        if user_group_ids:
            groups = subj.get("group", {}).get("results", [])
            for g in groups:
                gid = g.get("id") or g.get("groupId", "")
                if gid and gid in user_group_ids:
                    return True

    return False


def get_accessible_space_keys(email: str) -> set[str]:
    """Return the subset of CONFLUENCE_SPACE_KEYS this user can read."""
    cached = _space_access_cache.get(email)
    if cached and (time.time() - cached[1]) < _CACHE_TTL:
        return cached[0]

    account_id = _get_account_id(email)

    # Fetch group membership once (requires accountId)
    group_cache_entry = _group_cache.get(account_id or "")
    if account_id and (not group_cache_entry or time.time() - group_cache_entry[1] >= _CACHE_TTL):
        groups = _get_user_group_ids(account_id)
        _group_cache[account_id] = (groups, time.time())
        user_group_ids = groups
    else:
        user_group_ids = group_cache_entry[0] if group_cache_entry else set()

    accessible: set[str] = set()
    for space_key in SPACE_KEYS:
        perms = _get_read_space_perms(space_key)
        if _user_can_read_space(space_key, perms, email, account_id, user_group_ids):
            accessible.add(space_key)

    _space_access_cache[email] = (accessible, time.time())
    return accessible


def get_accessible_page_ids(email: str) -> set[str]:
    """Return page IDs readable by this user, enforcing both space- and page-level ACL."""
    cached = _acl_cache.get(email)
    if cached and (time.time() - cached[1]) < _CACHE_TTL:
        return cached[0]

    accessible_spaces = get_accessible_space_keys(email)
    account_id = _account_id_cache.get(email)  # already resolved above

    if not accessible_spaces:
        _acl_cache[email] = (set(), time.time())
        return set()

    all_ids: set[str] = set()
    start, limit = 0, 200
    while True:
        resp = httpx.get(
            f"{BASE_URL}/wiki/rest/api/content",
            params={
                "type": "page",
                "start": start,
                "limit": limit,
                "expand": "restrictions.read.restrictions.user,space",
            },
            auth=_auth(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for page in data.get("results", []):
            space_key = page.get("space", {}).get("key", "")
            if space_key not in accessible_spaces:
                continue

            restr_users = (
                page.get("restrictions", {})
                .get("read", {})
                .get("restrictions", {})
                .get("user", {})
                .get("results", [])
            )
            # No page-level restriction → accessible to all space members
            # Page has restrictions → user must be listed (check accountId AND email)
            if not restr_users:
                all_ids.add(str(page["id"]))
            else:
                for u in restr_users:
                    if u.get("email") == email or (account_id and u.get("accountId") == account_id):
                        all_ids.add(str(page["id"]))
                        break

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
