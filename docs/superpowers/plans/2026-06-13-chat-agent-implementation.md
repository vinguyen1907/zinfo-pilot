# Chat Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone web chat agent that answers natural-language questions about Confluence documentation with per-user access control, source citations, and real-time page sync.

**Architecture:** FastAPI backend serves a single HTML/JS frontend. User questions are embedded with `sentence-transformers/all-MiniLM-L6-v2`, retrieved from ChromaDB (top-20 candidates), filtered by Confluence ACL via admin API token (cached 2 min), and the top-5 chunks are passed to Qwen on GreenNode AgentBase. Confluence webhooks trigger async re-indexing on page changes. Conversation history is persisted per email in SQLite.

**Tech Stack:** Python 3.11+, FastAPI, LangChain, ChromaDB, SQLite (aiosqlite), `sentence-transformers`, `langchain-community` (ConfluenceLoader), `httpx`, `python-dotenv`

---

## File Map

| Path | Responsibility |
|---|---|
| `backend/main.py` | FastAPI app, routes: `/chat` (SSE), `/webhook`, `/index`, `/conversations/{email}`, static files |
| `backend/db.py` | SQLite init, `save_message`, `get_history` |
| `backend/confluence.py` | Confluence REST client: `get_accessible_page_ids`, `get_page_content`, `validate_webhook` |
| `backend/indexer.py` | Full crawl + single-page re-index using ConfluenceLoader → chunker → embedder → ChromaDB |
| `backend/retriever.py` | Embed query → ChromaDB top-20 → ACL filter → top-5 |
| `backend/llm.py` | LangChain ChatOpenAI chain pointing at AgentBase, citation system prompt |
| `frontend/index.html` | Email gate screen + chat screen, SSE streaming, citation pills |
| `.env.example` | All required env vars documented |
| `requirements.txt` | Pinned dependencies |
| `tests/test_db.py` | Unit tests for db.py |
| `tests/test_confluence.py` | Unit tests for ACL logic + webhook validation |
| `tests/test_retriever.py` | Unit tests for ACL filter step |
| `tests/test_indexer.py` | Unit tests for chunk ID generation |

---

## Task 1: Project scaffold + dependencies

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `backend/__init__.py`
- Create: `tests/__init__.py`
- Create: `frontend/` (empty dir placeholder)

- [ ] **Step 1: Create requirements.txt**

```
fastapi==0.111.0
uvicorn[standard]==0.29.0
python-dotenv==1.0.1
httpx==0.27.0
aiosqlite==0.20.0
chromadb==0.5.3
langchain==0.2.5
langchain-community==0.2.5
langchain-openai==0.1.8
sentence-transformers==3.0.1
```

- [ ] **Step 2: Create .env.example**

```
CONFLUENCE_BASE_URL=https://yourorg.atlassian.net
CONFLUENCE_ADMIN_TOKEN=your_admin_api_token
CONFLUENCE_ADMIN_EMAIL=admin@yourorg.com
CONFLUENCE_SPACE_KEYS=DEV,INFRA,ONBOARD
CONFLUENCE_WEBHOOK_SECRET=your_webhook_hmac_secret
AGENTBASE_API_KEY=your_greennode_api_key
AGENTBASE_MODEL_PATH=qwen/qwen2.5-72b-instruct
DATABASE_PATH=./data/conversations.db
CHROMA_PATH=./chroma_db
```

- [ ] **Step 3: Create empty init files and dirs**

```bash
mkdir -p backend tests frontend data
touch backend/__init__.py tests/__init__.py
```

- [ ] **Step 4: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: all packages install without error.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .env.example backend/__init__.py tests/__init__.py
git commit -m "chore: scaffold project structure and dependencies"
```

---

## Task 2: SQLite conversation store (`backend/db.py`)

**Files:**
- Create: `backend/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_db.py
import asyncio, os, pytest
os.environ.setdefault("DATABASE_PATH", ":memory:")

from backend.db import init_db, save_message, get_history

@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
async def db():
    await init_db()

@pytest.mark.asyncio
async def test_save_and_retrieve(db):
    await save_message("test@x.com", "user", "hello", None)
    await save_message("test@x.com", "assistant", "hi there", [{"title": "Page", "url": "http://x"}])
    rows = await get_history("test@x.com", limit=50)
    assert len(rows) == 2
    assert rows[0]["role"] == "user"
    assert rows[1]["citations"] == [{"title": "Page", "url": "http://x"}]

@pytest.mark.asyncio
async def test_history_limit(db):
    for i in range(15):
        await save_message("a@b.com", "user", f"msg {i}", None)
    rows = await get_history("a@b.com", limit=10)
    assert len(rows) == 10

@pytest.mark.asyncio
async def test_emails_isolated(db):
    await save_message("alice@x.com", "user", "alice msg", None)
    await save_message("bob@x.com", "user", "bob msg", None)
    assert len(await get_history("alice@x.com", limit=50)) == 1
    assert len(await get_history("bob@x.com", limit=50)) == 1
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_db.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` — `db` module doesn't exist yet.

- [ ] **Step 3: Implement `backend/db.py`**

```python
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

async def init_db():
    os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else ".", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(_CREATE_SQL)
        await conn.commit()

async def save_message(email: str, role: str, content: str, citations: list | None):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO conversations (email, role, content, citations_json) VALUES (?,?,?,?)",
            (email, role, content, json.dumps(citations) if citations else None),
        )
        await conn.commit()

async def get_history(email: str, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT role, content, citations_json, created_at FROM conversations "
            "WHERE email=? ORDER BY created_at ASC LIMIT ?",
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
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_db.py -v
```

Expected: 3 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/db.py tests/test_db.py
git commit -m "feat: add SQLite conversation store"
```

---

## Task 3: Confluence API client (`backend/confluence.py`)

**Files:**
- Create: `backend/confluence.py`
- Create: `tests/test_confluence.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_confluence.py
import hashlib, hmac, json, os, time
import pytest
os.environ.setdefault("CONFLUENCE_BASE_URL", "https://test.atlassian.net")
os.environ.setdefault("CONFLUENCE_ADMIN_TOKEN", "tok")
os.environ.setdefault("CONFLUENCE_ADMIN_EMAIL", "admin@test.com")
os.environ.setdefault("CONFLUENCE_WEBHOOK_SECRET", "secret123")

from backend.confluence import validate_webhook, _parse_accessible_page_ids

def _make_sig(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

def test_validate_webhook_valid():
    body = b'{"pageId":"123"}'
    sig = _make_sig(body, "secret123")
    assert validate_webhook({"x-hub-signature": sig}, body) is True

def test_validate_webhook_invalid():
    body = b'{"pageId":"123"}'
    assert validate_webhook({"x-hub-signature": "sha256=bad"}, body) is False

def test_validate_webhook_missing_header():
    assert validate_webhook({}, b"body") is False

def test_parse_accessible_page_ids():
    # Simulate Confluence GET /wiki/rest/api/content response
    fake_response = {
        "results": [
            {"id": "111", "title": "Page A"},
            {"id": "222", "title": "Page B"},
        ]
    }
    ids = _parse_accessible_page_ids(fake_response)
    assert ids == {"111", "222"}
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_confluence.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `backend/confluence.py`**

```python
import hashlib, hmac, os, time
from typing import Any
import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("CONFLUENCE_BASE_URL", "").rstrip("/")
ADMIN_TOKEN = os.getenv("CONFLUENCE_ADMIN_TOKEN", "")
ADMIN_EMAIL = os.getenv("CONFLUENCE_ADMIN_EMAIL", "")
WEBHOOK_SECRET = os.getenv("CONFLUENCE_WEBHOOK_SECRET", "")

# In-memory cache: email -> (set[page_id], timestamp)
_acl_cache: dict[str, tuple[set[str], float]] = {}
_CACHE_TTL = 120  # 2 minutes

def _auth() -> tuple[str, str]:
    return (ADMIN_EMAIL, ADMIN_TOKEN)

def validate_webhook(headers: dict, body: bytes) -> bool:
    sig_header = headers.get("x-hub-signature", "")
    if not sig_header.startswith("sha256="):
        return False
    expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig_header[7:], expected)

def _parse_accessible_page_ids(response: dict) -> set[str]:
    return {str(r["id"]) for r in response.get("results", [])}

def get_accessible_page_ids(email: str) -> set[str]:
    cached = _acl_cache.get(email)
    if cached and (time.time() - cached[1]) < _CACHE_TTL:
        return cached[0]

    all_ids: set[str] = set()
    start = 0
    limit = 200
    while True:
        resp = httpx.get(
            f"{BASE_URL}/wiki/rest/api/content",
            params={"type": "page", "start": start, "limit": limit, "expand": "restrictions.read.restrictions.user"},
            auth=_auth(),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        # Filter pages where the given email appears in read restrictions or has no restrictions
        for page in data.get("results", []):
            restrictions = page.get("restrictions", {}).get("read", {}).get("restrictions", {}).get("user", {}).get("results", [])
            if not restrictions or any(u.get("email") == email for u in restrictions):
                all_ids.add(str(page["id"]))
        if data.get("size", 0) < limit:
            break
        start += limit

    _acl_cache[email] = (all_ids, time.time())
    return all_ids

def get_page_content(page_id: str) -> dict[str, Any]:
    resp = httpx.get(
        f"{BASE_URL}/wiki/rest/api/content/{page_id}",
        params={"expand": "body.storage,space,version"},
        auth=_auth(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_confluence.py -v
```

Expected: 4 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/confluence.py tests/test_confluence.py
git commit -m "feat: add Confluence API client with ACL cache and webhook validation"
```

---

## Task 4: Indexer (`backend/indexer.py`)

**Files:**
- Create: `backend/indexer.py`
- Create: `tests/test_indexer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_indexer.py
import pytest
from backend.indexer import make_chunk_id, split_page_into_chunks

def test_chunk_id_format():
    assert make_chunk_id("12345", 0) == "12345_0"
    assert make_chunk_id("12345", 7) == "12345_7"

def test_chunk_id_unique_across_pages():
    assert make_chunk_id("111", 0) != make_chunk_id("222", 0)

def test_split_produces_chunks():
    long_text = "word " * 300  # 1500 chars, well above 500-char chunk size
    chunks = split_page_into_chunks("999", "Test Page", "http://x", "DEV", "2024-01-01", long_text)
    assert len(chunks) > 1
    for i, (doc, meta) in enumerate(chunks):
        assert meta["page_id"] == "999"
        assert meta["chunk_index"] == i
        assert meta["page_title"] == "Test Page"
        assert "word" in doc

def test_split_short_text_single_chunk():
    chunks = split_page_into_chunks("1", "Short", "http://x", "DEV", "2024-01-01", "hello world")
    assert len(chunks) == 1
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_indexer.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `backend/indexer.py`**

```python
import os
from typing import Any
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from langchain_community.document_loaders import ConfluenceLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from dotenv import load_dotenv
from backend.confluence import get_page_content, BASE_URL, _auth

load_dotenv()

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
SPACE_KEYS = [k.strip() for k in os.getenv("CONFLUENCE_SPACE_KEYS", "").split(",") if k.strip()]

_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
_ef = SentenceTransformerEmbeddingFunction(model_name="sentence-transformers/all-MiniLM-L6-v2")

def _get_collection() -> chromadb.Collection:
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection("confluence_pages", embedding_function=_ef)

def make_chunk_id(page_id: str, chunk_index: int) -> str:
    return f"{page_id}_{chunk_index}"

def split_page_into_chunks(
    page_id: str, title: str, url: str, space_key: str, last_modified: str, text: str
) -> list[tuple[str, dict]]:
    raw_chunks = _splitter.split_text(text)
    return [
        (
            chunk,
            {
                "page_id": page_id,
                "page_title": title,
                "page_url": url,
                "space_key": space_key,
                "last_modified": last_modified,
                "chunk_index": i,
            },
        )
        for i, chunk in enumerate(raw_chunks)
    ]

def index_page(page_data: dict[str, Any]):
    page_id = str(page_data["id"])
    title = page_data.get("title", "")
    url = f"{BASE_URL}/wiki{page_data.get('_links', {}).get('webui', '')}"
    space_key = page_data.get("space", {}).get("key", "")
    last_modified = page_data.get("version", {}).get("when", "")
    body = page_data.get("body", {}).get("storage", {}).get("value", "")

    # Strip HTML tags simply
    import re
    text = re.sub(r"<[^>]+>", " ", body).strip()
    if not text:
        return

    chunks = split_page_into_chunks(page_id, title, url, space_key, last_modified, text)
    collection = _get_collection()

    # Delete old chunks for this page
    existing = collection.get(where={"page_id": page_id})
    if existing["ids"]:
        collection.delete(ids=existing["ids"])

    # Upsert new chunks
    ids = [make_chunk_id(page_id, meta["chunk_index"]) for _, meta in chunks]
    documents = [doc for doc, _ in chunks]
    metadatas = [meta for _, meta in chunks]
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

def run_full_index():
    from langchain_community.document_loaders import ConfluenceLoader as CL
    import os
    loader = CL(
        url=BASE_URL,
        username=os.getenv("CONFLUENCE_ADMIN_EMAIL"),
        api_key=os.getenv("CONFLUENCE_ADMIN_TOKEN"),
        space_key=None,
    )
    for space_key in SPACE_KEYS:
        loader.space_key = space_key
        docs = loader.load()
        for doc in docs:
            page_id = doc.metadata.get("id", "")
            if not page_id:
                continue
            page_data = get_page_content(page_id)
            index_page(page_data)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_indexer.py -v
```

Expected: 4 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/indexer.py tests/test_indexer.py
git commit -m "feat: add indexer with chunking and ChromaDB upsert"
```

---

## Task 5: Retriever (`backend/retriever.py`)

**Files:**
- Create: `backend/retriever.py`
- Create: `tests/test_retriever.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_retriever.py
from backend.retriever import filter_by_acl

def test_filter_keeps_allowed():
    candidates = [
        {"id": "p1_0", "document": "doc1", "metadata": {"page_id": "p1", "page_title": "A", "page_url": "http://a"}},
        {"id": "p2_0", "document": "doc2", "metadata": {"page_id": "p2", "page_title": "B", "page_url": "http://b"}},
        {"id": "p3_0", "document": "doc3", "metadata": {"page_id": "p3", "page_title": "C", "page_url": "http://c"}},
    ]
    allowed = {"p1", "p3"}
    result = filter_by_acl(candidates, allowed, top_k=5)
    assert len(result) == 2
    assert all(c["metadata"]["page_id"] in allowed for c in result)

def test_filter_respects_top_k():
    candidates = [
        {"id": f"p{i}_0", "document": f"doc{i}", "metadata": {"page_id": f"p{i}", "page_title": f"T{i}", "page_url": f"http://{i}"}}
        for i in range(10)
    ]
    allowed = {f"p{i}" for i in range(10)}
    result = filter_by_acl(candidates, allowed, top_k=3)
    assert len(result) == 3

def test_filter_returns_empty_when_no_access():
    candidates = [
        {"id": "p1_0", "document": "doc", "metadata": {"page_id": "p1", "page_title": "A", "page_url": "http://a"}},
    ]
    result = filter_by_acl(candidates, set(), top_k=5)
    assert result == []
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_retriever.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `backend/retriever.py`**

```python
import os
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from dotenv import load_dotenv
from backend.confluence import get_accessible_page_ids

load_dotenv()

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
_ef = SentenceTransformerEmbeddingFunction(model_name="sentence-transformers/all-MiniLM-L6-v2")

def _get_collection() -> chromadb.Collection:
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection("confluence_pages", embedding_function=_ef)

def filter_by_acl(candidates: list[dict], allowed_page_ids: set[str], top_k: int = 5) -> list[dict]:
    return [c for c in candidates if c["metadata"]["page_id"] in allowed_page_ids][:top_k]

def retrieve(query: str, email: str, top_k: int = 5) -> list[dict]:
    collection = _get_collection()
    results = collection.query(query_texts=[query], n_results=20, include=["documents", "metadatas"])

    candidates = [
        {
            "id": results["ids"][0][i],
            "document": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
        }
        for i in range(len(results["ids"][0]))
    ]

    allowed = get_accessible_page_ids(email)
    return filter_by_acl(candidates, allowed, top_k=top_k)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_retriever.py -v
```

Expected: 3 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/retriever.py tests/test_retriever.py
git commit -m "feat: add retriever with ACL filtering"
```

---

## Task 6: LLM chain (`backend/llm.py`)

**Files:**
- Create: `backend/llm.py`

No unit tests for the LLM call itself (requires live API). Integration tested via `/chat` endpoint in Task 7.

- [ ] **Step 1: Implement `backend/llm.py`**

```python
import os
from langchain_openai import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage, AIMessage
from dotenv import load_dotenv

load_dotenv()

_SYSTEM_PROMPT = """You are an IT knowledge assistant for new employees. Answer questions using ONLY the provided documentation excerpts below. When you reference information, mention the page title it comes from. If the context doesn't contain enough information to answer, say so clearly — do not make up information. Keep answers concise and practical.

Documentation context:
{context}"""

def build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=os.getenv("AGENTBASE_API_KEY", ""),
        openai_api_key=os.getenv("AGENTBASE_API_KEY", ""),
        model=os.getenv("AGENTBASE_MODEL_PATH", "qwen/qwen2.5-72b-instruct"),
        streaming=True,
        temperature=0.2,
        openai_api_base="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1",
    )

def format_context(chunks: list[dict]) -> str:
    parts = []
    for chunk in chunks:
        title = chunk["metadata"].get("page_title", "Unknown")
        url = chunk["metadata"].get("page_url", "")
        parts.append(f"[{title}]({url})\n{chunk['document']}")
    return "\n\n---\n\n".join(parts)

def build_messages(history: list[dict], chunks: list[dict], user_message: str) -> list:
    context = format_context(chunks)
    messages = [SystemMessage(content=_SYSTEM_PROMPT.format(context=context))]
    for turn in history[-10:]:
        if turn["role"] == "user":
            messages.append(HumanMessage(content=turn["content"]))
        else:
            messages.append(AIMessage(content=turn["content"]))
    messages.append(HumanMessage(content=user_message))
    return messages

def extract_citations(chunks: list[dict]) -> list[dict]:
    seen = set()
    citations = []
    for chunk in chunks:
        page_id = chunk["metadata"].get("page_id", "")
        if page_id not in seen:
            seen.add(page_id)
            citations.append({
                "title": chunk["metadata"].get("page_title", ""),
                "url": chunk["metadata"].get("page_url", ""),
            })
    return citations
```

- [ ] **Step 2: Fix the `base_url` typo — it should use the endpoint, not the key**

Edit `build_llm` to:

```python
def build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        openai_api_key=os.getenv("AGENTBASE_API_KEY", ""),
        openai_api_base="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1",
        model=os.getenv("AGENTBASE_MODEL_PATH", "qwen/qwen2.5-72b-instruct"),
        streaming=True,
        temperature=0.2,
    )
```

- [ ] **Step 3: Commit**

```bash
git add backend/llm.py
git commit -m "feat: add LLM chain with citation extraction"
```

---

## Task 7: FastAPI backend (`backend/main.py`)

**Files:**
- Create: `backend/main.py`

- [ ] **Step 1: Implement `backend/main.py`**

```python
import asyncio, json, os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from backend.db import init_db, save_message, get_history
from backend.retriever import retrieve
from backend.llm import build_llm, build_messages, extract_citations
from backend.indexer import run_full_index, index_page
from backend.confluence import validate_webhook, get_page_content

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(lifespan=lifespan)

class ChatRequest(BaseModel):
    email: str
    message: str

@app.post("/chat")
async def chat(req: ChatRequest):
    chunks = retrieve(req.message, req.email)

    if not chunks:
        async def no_access():
            data = json.dumps({"token": "I couldn't find any documentation you have access to for that question."})
            yield f"data: {data}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(no_access(), media_type="text/event-stream")

    history = await get_history(req.email, limit=10)
    messages = build_messages(history, chunks, req.message)
    citations = extract_citations(chunks)
    llm = build_llm()

    await save_message(req.email, "user", req.message, None)

    full_response = []

    async def stream():
        try:
            async for chunk in llm.astream(messages):
                token = chunk.content
                if token:
                    full_response.append(token)
                    yield f"data: {json.dumps({'token': token})}\n\n"
            response_text = "".join(full_response)
            await save_message(req.email, "assistant", response_text, citations)
            yield f"data: {json.dumps({'citations': citations})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception:
            yield f"data: {json.dumps({'error': 'Something went wrong generating a response. Please try again.'})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    headers = dict(request.headers)
    if not validate_webhook(headers, body):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body)
    event = payload.get("eventType", "")
    page_id = str(payload.get("page", {}).get("id", "") or payload.get("pageId", ""))

    if not page_id:
        return {"status": "ignored"}

    async def reindex():
        if event == "page:deleted":
            from backend.indexer import _get_collection
            col = _get_collection()
            existing = col.get(where={"page_id": page_id})
            if existing["ids"]:
                col.delete(ids=existing["ids"])
        else:
            page_data = get_page_content(page_id)
            index_page(page_data)

    asyncio.create_task(reindex())
    return {"status": "accepted"}

@app.post("/index")
async def index():
    asyncio.create_task(asyncio.to_thread(run_full_index))
    return {"status": "indexing started"}

@app.get("/conversations/{email}")
async def conversations(email: str):
    rows = await get_history(email, limit=50)
    return {"messages": rows}

# Serve frontend
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="static")
```

- [ ] **Step 2: Smoke test — start the server**

```bash
uvicorn backend.main:app --reload --port 8000
```

Expected: server starts with no import errors. Visit `http://localhost:8000/docs` — FastAPI Swagger UI loads.

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: add FastAPI routes for chat, webhook, index, and conversations"
```

---

## Task 8: Frontend (`frontend/index.html`)

**Files:**
- Create: `frontend/index.html`

- [ ] **Step 1: Create `frontend/index.html`**

Copy the full HTML from the high-fidelity mockup at `.superpowers/brainstorm/23747-1781331046/content/chat-ui-hifi.html` and wire up the JavaScript logic:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>zinfo — IT Knowledge Assistant</title>
<style>
/* ... (paste full CSS from the mockup file) ... */
</style>
</head>
<body>

<!-- Email gate screen -->
<div id="gate-screen" style="display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:16px">
  <div style="font-size:1.8rem;font-weight:700;letter-spacing:-0.03em">⚡ zinfo</div>
  <p style="color:#64748b;font-size:0.9rem">Enter your company email to continue</p>
  <div style="display:flex;gap:8px;width:100%;max-width:360px">
    <input id="email-input" type="email" placeholder="you@company.com"
      style="flex:1;background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.12);border-radius:10px;padding:10px 14px;color:#e2e8f0;font-size:0.93rem;outline:none"
      onkeydown="if(event.key==='Enter')enterChat()">
    <button onclick="enterChat()"
      style="background:linear-gradient(135deg,#6366f1,#8b5cf6);border:none;border-radius:10px;padding:10px 20px;color:white;font-weight:600;cursor:pointer">
      Continue
    </button>
  </div>
  <div id="gate-error" style="color:#f87171;font-size:0.82rem;display:none"></div>
</div>

<!-- Chat screen (hidden until email entered) -->
<div id="chat-screen" style="display:none;height:100vh;flex-direction:column">
  <!-- Top bar -->
  <div class="topbar">
    <div class="logo"><div class="logo-icon">⚡</div> zinfo</div>
    <div style="font-size:0.78rem;color:#475569">IT Knowledge Assistant</div>
    <div class="user-pill">
      <div class="avatar" id="avatar-initials">?</div>
      <span id="user-email-display"></span>
    </div>
  </div>

  <!-- Messages -->
  <div class="messages" id="messages"></div>

  <!-- Input -->
  <div class="inputbar">
    <div class="input-wrap">
      <textarea id="chat-input" class="chat-input" placeholder="Ask about the IT system…" rows="1"
        oninput="this.style.height='auto';this.style.height=this.scrollHeight+'px'"
        onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage()}"></textarea>
      <button class="send-btn" onclick="sendMessage()">↑</button>
    </div>
    <div class="input-hint">Answers are grounded in your Confluence docs · Sources shown below each response</div>
  </div>
</div>

<script>
let currentEmail = sessionStorage.getItem("email") || "";
if (currentEmail) showChatScreen(currentEmail);

function enterChat() {
  const val = document.getElementById("email-input").value.trim();
  if (!val || !val.includes("@")) {
    document.getElementById("gate-error").textContent = "Please enter a valid email.";
    document.getElementById("gate-error").style.display = "block";
    return;
  }
  sessionStorage.setItem("email", val);
  showChatScreen(val);
}

async function showChatScreen(email) {
  currentEmail = email;
  document.getElementById("gate-screen").style.display = "none";
  const chatScreen = document.getElementById("chat-screen");
  chatScreen.style.display = "flex";
  document.getElementById("user-email-display").textContent = email;
  document.getElementById("avatar-initials").textContent = email[0].toUpperCase();

  // Load history
  const res = await fetch(`/conversations/${encodeURIComponent(email)}`);
  const data = await res.json();
  for (const msg of data.messages) {
    appendMessage(msg.role, msg.content, msg.citations);
  }
  scrollToBottom();
}

function appendMessage(role, content, citations) {
  const messages = document.getElementById("messages");
  const row = document.createElement("div");
  row.className = `msg-row ${role}`;

  if (role === "user") {
    row.innerHTML = `<div class="bubble">${escHtml(content)}</div>`;
  } else {
    let citeHtml = "";
    if (citations && citations.length > 0) {
      citeHtml = `<div class="citations">${citations.map(c =>
        `<a class="cite-pill" href="${escHtml(c.url)}" target="_blank"><span class="cite-icon">📄</span>${escHtml(c.title)}</a>`
      ).join("")}</div>`;
    }
    row.innerHTML = `<div class="bubble-wrap"><div class="bubble" id="bubble-${Date.now()}">${escHtml(content)}</div>${citeHtml}</div>`;
  }
  messages.appendChild(row);
  return row;
}

function appendTypingIndicator() {
  const messages = document.getElementById("messages");
  const row = document.createElement("div");
  row.className = "typing-row";
  row.id = "typing-indicator";
  row.innerHTML = `<div class="typing-bubble"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>`;
  messages.appendChild(row);
  scrollToBottom();
  return row;
}

async function sendMessage() {
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  input.style.height = "auto";

  appendMessage("user", text, null);
  const typing = appendTypingIndicator();
  scrollToBottom();

  const messages = document.getElementById("messages");
  const assistantRow = document.createElement("div");
  assistantRow.className = "msg-row assistant";
  const bubbleWrap = document.createElement("div");
  bubbleWrap.className = "bubble-wrap";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubbleWrap.appendChild(bubble);
  assistantRow.appendChild(bubbleWrap);

  const es = new EventSource(`/chat-stream?email=${encodeURIComponent(currentEmail)}&message=${encodeURIComponent(text)}`);

  // Switch to POST + SSE via fetch streaming instead (EventSource is GET only)
  es.close();

  // Use fetch + ReadableStream for POST SSE
  typing.remove();
  messages.appendChild(assistantRow);

  const response = await fetch("/chat", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({email: currentEmail, message: text}),
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const raw = line.slice(6).trim();
      if (raw === "[DONE]") break;
      try {
        const parsed = JSON.parse(raw);
        if (parsed.token) {
          bubble.textContent += parsed.token;
          scrollToBottom();
        }
        if (parsed.citations) {
          const citeDiv = document.createElement("div");
          citeDiv.className = "citations";
          citeDiv.innerHTML = parsed.citations.map(c =>
            `<a class="cite-pill" href="${escHtml(c.url)}" target="_blank"><span class="cite-icon">📄</span>${escHtml(c.title)}</a>`
          ).join("");
          bubbleWrap.appendChild(citeDiv);
          scrollToBottom();
        }
        if (parsed.error) {
          bubble.textContent = parsed.error;
        }
      } catch {}
    }
  }
}

function scrollToBottom() {
  const el = document.getElementById("messages");
  el.scrollTop = el.scrollHeight;
}

function escHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
</script>
</body>
</html>
```

- [ ] **Step 2: Manual test — email gate**

Start the server (`uvicorn backend.main:app --reload`), open `http://localhost:8000`, enter a test email, verify the chat screen appears.

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat: add chat frontend with email gate, SSE streaming, and citation pills"
```

---

## Task 9: End-to-end smoke test

Prerequisites: `.env` file populated with real Confluence and AgentBase credentials.

- [ ] **Step 1: Create `.env` from template**

```bash
cp .env.example .env
# Edit .env and fill in all values
```

- [ ] **Step 2: Trigger full index**

```bash
curl -X POST http://localhost:8000/index
```

Expected: `{"status":"indexing started"}`. Wait ~1-2 min for indexing to complete (watch uvicorn logs).

- [ ] **Step 3: Verify ChromaDB has data**

```python
# Run in a Python REPL
import chromadb
c = chromadb.PersistentClient(path="./chroma_db")
col = c.get_collection("confluence_pages")
print(col.count())  # Should be > 0
```

- [ ] **Step 4: Test a query as admin**

Open `http://localhost:8000`, enter the admin email, ask a question about something you know is in Confluence.

Expected: answer grounded in real content, citations appear as pill links.

- [ ] **Step 5: Test permission filtering**

Enter a second email (a user with fewer Confluence permissions) and ask the same question.

Expected: different (fewer) results or "I couldn't find any documentation you have access to."

- [ ] **Step 6: Test webhook**

Update a Confluence page, then watch the uvicorn logs for re-indexing activity. Re-ask about that page's content.

Expected: answer reflects the updated content.

---

## Task 10: Run full test suite + final commit

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 2: Final commit**

```bash
git add -A
git commit -m "chore: complete chat agent implementation"
```
