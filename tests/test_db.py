# tests/test_db.py
import asyncio, os, pytest
os.environ["DATABASE_PATH"] = ":memory:"

from backend.db import init_db, save_message, get_history

@pytest.fixture(autouse=True)
async def setup():
    await init_db()

@pytest.mark.asyncio
async def test_save_and_retrieve():
    await save_message("alice@x.com", "user", "hello", None)
    await save_message("alice@x.com", "assistant", "hi", [{"title": "Pg", "url": "http://x"}])
    rows = await get_history("alice@x.com", limit=50)
    assert len(rows) == 2
    assert rows[0]["role"] == "user"
    assert rows[0]["content"] == "hello"
    assert rows[1]["citations"] == [{"title": "Pg", "url": "http://x"}]

@pytest.mark.asyncio
async def test_limit_is_respected():
    for i in range(15):
        await save_message("bob@x.com", "user", f"msg {i}", None)
    rows = await get_history("bob@x.com", limit=10)
    assert len(rows) == 10

@pytest.mark.asyncio
async def test_emails_are_isolated():
    await save_message("a@x.com", "user", "a msg", None)
    await save_message("b@x.com", "user", "b msg", None)
    assert len(await get_history("a@x.com", limit=50)) == 1
    assert len(await get_history("b@x.com", limit=50)) == 1

@pytest.mark.asyncio
async def test_null_citations_stored_as_none():
    await save_message("c@x.com", "user", "question", None)
    rows = await get_history("c@x.com", limit=10)
    assert rows[0]["citations"] is None
