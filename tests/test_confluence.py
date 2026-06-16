# tests/test_confluence.py
import hashlib, hmac as _hmac, os
import pytest
from unittest.mock import patch, MagicMock

os.environ.setdefault("CONFLUENCE_BASE_URL", "https://test.atlassian.net")
os.environ.setdefault("CONFLUENCE_ADMIN_TOKEN", "tok")
os.environ.setdefault("CONFLUENCE_ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("CONFLUENCE_WEBHOOK_SECRET", "secret123")
os.environ.setdefault("CONFLUENCE_SPACE_KEYS", "SD,~abc123")

from backend.confluence import validate_webhook, _parse_accessible_page_ids, _user_has_space_read, get_accessible_space_keys

def _sign(body: bytes, secret: str) -> str:
    digest = _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return "sha256=" + digest

def test_valid_signature():
    body = b'{"pageId":"123"}'
    sig = _sign(body, "secret123")
    assert validate_webhook({"x-hub-signature": sig}, body) is True

def test_invalid_signature():
    body = b'{"pageId":"123"}'
    assert validate_webhook({"x-hub-signature": "sha256=badhash"}, body) is False

def test_missing_signature_header():
    assert validate_webhook({}, b"payload") is False

def test_wrong_prefix():
    assert validate_webhook({"x-hub-signature": "md5=abc"}, b"payload") is False

def test_parse_page_ids():
    fake = {"results": [{"id": "111"}, {"id": "222"}]}
    assert _parse_accessible_page_ids(fake) == {"111", "222"}

def test_parse_empty_results():
    assert _parse_accessible_page_ids({"results": []}) == set()


# --- helpers ---

def _perm(operation: str, account_ids: list, group_names: list, anon: bool = False) -> dict:
    return {
        "operation": {"operation": operation, "targetType": "space"},
        "subjects": {
            "user": {"results": [{"accountId": a} for a in account_ids]},
            "group": {"results": [{"name": g} for g in group_names]},
        },
        "anonymousAccess": anon,
    }


# --- _user_has_space_read ---

def test_user_has_space_read_by_account_id():
    perms = [_perm("read", ["uid-123"], [])]
    assert _user_has_space_read(perms, "uid-123") is True

def test_user_not_in_space():
    perms = [_perm("read", ["uid-999"], [])]
    assert _user_has_space_read(perms, "uid-123") is False

def test_anonymous_access_grants_all():
    perms = [_perm("read", [], [], anon=True)]
    assert _user_has_space_read(perms, "uid-any") is True

def test_confluence_users_group_grants_access():
    perms = [_perm("read", [], ["confluence-users"])]
    assert _user_has_space_read(perms, "uid-123") is True

def test_no_read_operation_grants_access():
    perms = [_perm("write", ["uid-123"], [])]
    assert _user_has_space_read(perms, "uid-123") is True

def test_empty_permissions_grants_access():
    assert _user_has_space_read([], "uid-any") is True


# --- get_accessible_space_keys ---

def test_get_accessible_space_keys_filters_correctly():
    with patch("backend.confluence.httpx.get") as mock_get:
        user_resp = MagicMock()
        user_resp.json.return_value = {"results": [{"accountId": "uid-me", "email": "me@test.com"}]}
        user_resp.raise_for_status = MagicMock()

        sd_resp = MagicMock()
        sd_resp.status_code = 200
        sd_resp.json.return_value = [_perm("read", ["uid-me"], [])]

        personal_resp = MagicMock()
        personal_resp.status_code = 200
        personal_resp.json.return_value = [_perm("read", ["uid-other"], [])]

        mock_get.side_effect = [user_resp, sd_resp, personal_resp]

        from backend.confluence import _space_cache
        _space_cache.clear()

        result = get_accessible_space_keys("me@test.com")
        assert result == {"SD"}
