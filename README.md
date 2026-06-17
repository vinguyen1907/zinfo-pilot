# zInfo Pilot

An IT knowledge assistant that answers employee questions using your organization's Confluence documentation. Built on RAG (Retrieval-Augmented Generation): it indexes Confluence pages into a vector database, retrieves the most relevant chunks per query, and streams a cited answer from an LLM — while enforcing Confluence access control so users only see content they're permitted to read.

---

## Purpose

Internal knowledge is scattered across hundreds of Confluence pages that are hard to search and slow to navigate. zInfo Pilot gives employees a chat interface where they ask questions in plain language and get direct, sourced answers drawn from your actual documentation — respecting the same space- and page-level permissions already configured in Confluence.

**Target users:** Employees at VNG/Zalo who need fast answers from internal IT documentation without digging through Confluence manually.

**Demo Confluence instance:** https://zalo-info-pilot.atlassian.net
**Demo email:** tindpt@vng.com.vn

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│              Browser (index.html)                    │
│  Email gate → Chat UI → Server-Sent Events stream    │
└────────────────────┬─────────────────────────────────┘
                     │ POST /chat
                     ▼
┌──────────────────────────────────────────────────────┐
│              FastAPI Backend (zinfo-rag)              │
│                                                      │
│  /chat ──► retriever.py ──► ACL check ──► llm.py    │
│  /webhook ──► indexer.py (on page change)            │
│  /index ──► indexer.py (full re-index)               │
│  /stats, /conversations, /health                     │
└──────┬─────────────────┬────────────────┬────────────┘
       │                 │                │
       ▼                 ▼                ▼
 ChromaDB          Confluence API      SQLite
 (vector search)   (content + ACL)   (chat history)
```

### Request Flow

1. User submits a question with their email via `POST /chat`
2. **ACL resolution** — email is resolved to a Confluence account ID; space and page permissions are fetched and cached (120 s TTL)
3. **Retrieval** — semantic search across ChromaDB returns the top 20 chunks; results are filtered to pages the user can access, keeping the top 5
4. **Generation** — LLM (Qwen 2.5 72B via AgentBase) receives a system prompt containing the retrieved chunks and the last 10 turns of conversation history; response is streamed token-by-token via Server-Sent Events
5. **Persistence** — the exchange (message, response, cited page URLs) is saved to SQLite

### Indexing Flow

- **On startup** — if the ChromaDB collection is empty, a full index is triggered automatically
- **On demand** — `POST /index` re-indexes all configured Confluence spaces
- **On change** — Confluence webhooks (`page:created`, `page:updated`, `page:deleted`) trigger incremental re-indexing of the changed page

Pages are split into 500-character chunks with 50-character overlap using LangChain's `RecursiveCharacterTextSplitter`. Embeddings are generated with `sentence-transformers/all-MiniLM-L6-v2`. ACL enforcement happens at **retrieval time**, not indexing time — all pages are indexed, but only accessible ones are returned per user.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI + Uvicorn (Python 3.12) |
| Vector database | ChromaDB |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` |
| LLM | Qwen 2.5 72B Instruct (OpenAI-compatible API via AgentBase) |
| RAG orchestration | LangChain |
| Confluence client | httpx + BeautifulSoup4 |
| Conversation storage | SQLite (aiosqlite) |
| Containerization | Docker + Docker Compose |
| Cloud runtime | GreenNode AgentBase |

---

## Project Structure

```
zinfo-pilot/
├── backend/
│   ├── main.py           # FastAPI app, all HTTP endpoints
│   ├── confluence.py     # Confluence REST client + ACL resolver
│   ├── chroma_client.py  # ChromaDB client factory (local or HTTP)
│   ├── indexer.py        # Page chunking, embedding, upsert
│   ├── retriever.py      # Vector search + ACL filtering
│   ├── llm.py            # Prompt building + streaming LLM calls
│   └── db.py             # SQLite conversation history
├── frontend/
│   └── index.html        # Single-page chat UI (email gate + chat)
├── docker/
│   └── chroma_wrapper.py # FastAPI proxy for standalone ChromaDB
├── tests/                # pytest test suite
├── docs/                 # Deployment checklist + planning docs
├── Dockerfile            # RAG service image
├── Dockerfile.chroma     # ChromaDB service image
├── docker-compose.yml    # Local orchestration
└── requirements.txt
```

---

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Serve frontend |
| `/chat` | POST | Stream a chat response (`{email, message}`) |
| `/conversations/{email}` | GET | Fetch last 50 messages for a user |
| `/webhook` | POST | Receive Confluence page-change events |
| `/index` | POST | Trigger a full Confluence re-index |
| `/stats` | GET | Collection stats (chunks, pages, spaces) |
| `/health` | GET | Health check |

---

## Setup

### Prerequisites

- Docker and Docker Compose
- A Confluence Cloud instance with admin API access
- An AgentBase API key (for LLM access)

### Environment Variables

Copy `.env.example` to `.env` and fill in:

```env
# Confluence
CONFLUENCE_BASE_URL=https://your-org.atlassian.net
CONFLUENCE_ADMIN_EMAIL=admin@your-org.com
CONFLUENCE_ADMIN_TOKEN=<confluence-api-token>
CONFLUENCE_SPACE_KEYS=SPACE1,SPACE2,SPACE3
CONFLUENCE_WEBHOOK_SECRET=<hmac-secret>

# LLM (AgentBase)
AGENTBASE_API_KEY=<api-key>
AGENTBASE_MODEL_PATH=qwen/qwen2.5-72b-instruct

# ChromaDB — choose one mode:
# Local persistent (development)
CHROMA_PATH=./chroma_db

# Remote HTTP client (production)
CHROMA_HOST=<chroma-runtime-endpoint>
CHROMA_PORT=443
CHROMA_SSL=true

# SQLite
DATABASE_PATH=./data/conversations.db
```

### Run Locally

```bash
# Start both services
docker-compose up --build

# Trigger initial indexing (runs automatically if ChromaDB is empty)
curl -X POST http://localhost:8080/index

# Check stats
curl http://localhost:8080/stats

# Open the chat UI
open http://localhost:8080
```

### Run Tests

```bash
pip install -r requirements.txt
pytest tests/
```

---

## Deployment (AgentBase)

Two runtimes are required:

1. **zinfo-chroma** — ChromaDB service (2 vCPU / 4 GB). No public endpoint needed; accessed internally by zinfo-rag.
2. **zinfo-rag** — FastAPI RAG service. Requires a public endpoint for the Confluence webhook.

After deploying both runtimes, register a webhook in Confluence Admin:

- **URL:** `https://<zinfo-rag-endpoint>/webhook`
- **Secret:** value of `CONFLUENCE_WEBHOOK_SECRET`
- **Events:** `page:created`, `page:updated`, `page:deleted`

See `docs/deployment-checklist.md` for step-by-step instructions and runtime IDs.
