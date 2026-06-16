# tests/test_confluence.py
import hashlib, hmac as _hmac, os
import pytest

os.environ.setdefault("CONFLUENCE_BASE_URL", "https://test.atlassian.net")
os.environ.setdefault("CONFLUENCE_ADMIN_TOKEN", "tok")
os.environ.setdefault("CONFLUENCE_ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("CONFLUENCE_WEBHOOK_SECRET", "secret123")

from backend.confluence import validate_webhook, _parse_accessible_page_ids

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
