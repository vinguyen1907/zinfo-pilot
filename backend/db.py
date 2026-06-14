import json, os
import aiosqlite
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DATABASE_PATH", "./data/conversations.db")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    citations_json TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_email_created ON conversations(email, created_at);
"""

# For :memory: databases, SQLite creates a new empty DB for each connection.
# We keep a single shared connection when running in-memory so that all
# operations within a test session see the same schema and data.
_shared_conn: aiosqlite.Connection | None = None


async def _get_conn() -> aiosqlite.Connection | None:
    """Return the shared connection if we're in :memory: mode, else None."""
    global _shared_conn
    if DB_PATH == ":memory:":
        if _shared_conn is None:
            _shared_conn = await aiosqlite.connect(":memory:")
        return _shared_conn
    return None


async def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = await _get_conn()
    if conn is not None:
        await conn.executescript(_CREATE_SQL)
        await conn.commit()
    else:
        async with aiosqlite.connect(DB_PATH) as c:
            await c.executescript(_CREATE_SQL)
            await c.commit()


async def save_message(email: str, role: str, content: str, citations: list | None):
    conn = await _get_conn()
    if conn is not None:
        await conn.execute(
            "INSERT INTO conversations (email, role, content, citations_json) VALUES (?,?,?,?)",
            (email, role, content, json.dumps(citations) if citations else None),
        )
        await conn.commit()
    else:
        async with aiosqlite.connect(DB_PATH) as c:
            await c.execute(
                "INSERT INTO conversations (email, role, content, citations_json) VALUES (?,?,?,?)",
                (email, role, content, json.dumps(citations) if citations else None),
            )
            await c.commit()


async def get_history(email: str, limit: int = 50) -> list[dict]:
    conn = await _get_conn()
    if conn is not None:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT role, content, citations_json, created_at "
            "FROM conversations WHERE email=? ORDER BY created_at ASC LIMIT ?",
            (email, limit),
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with aiosqlite.connect(DB_PATH) as c:
            c.row_factory = aiosqlite.Row
            async with c.execute(
                "SELECT role, content, citations_json, created_at "
                "FROM conversations WHERE email=? ORDER BY created_at ASC LIMIT ?",
                (email, limit),
            ) as cur:
                rows = await cur.fetchall()
    return [
        {
            "role": row["role"],
            "content": row["content"],
            "citations": json.loads(row["citations_json"]) if row["citations_json"] else None,
            "created_at": row["created_at"],
        }
        for row in rows
    ]
