import asyncio
import json
import os
import aiosqlite
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DATABASE_PATH", "./data/conversations.db")

async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                citations_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_email_created ON conversations(email, created_at)
        """)
        await db.commit()

async def save_message(email: str, role: str, content: str, citations: list | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversations (email, role, content, citations_json) VALUES (?, ?, ?, ?)",
            (email, role, content, json.dumps(citations) if citations else None)
        )
        await db.commit()

async def get_history(email: str, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT role, content, citations_json, created_at FROM conversations WHERE email = ? ORDER BY created_at DESC LIMIT ?",
            (email, limit)
        ) as cursor:
            rows = await cursor.fetchall()
    result = []
    for row in reversed(rows):
        entry = {"role": row["role"], "content": row["content"]}
        if row["citations_json"]:
            entry["citations"] = json.loads(row["citations_json"])
        result.append(entry)
    return result
