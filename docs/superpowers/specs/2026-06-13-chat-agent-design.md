# zinfo-pilot — Confluence Chat Agent Design

**Date:** 2026-06-13
**Context:** Hackathon project. Solves the problem of messy, overwhelming Confluence docs that confuse new hires during onboarding. Delivers a standalone chat agent that answers questions about the legacy IT system using real Confluence content, with per-user access control and source citations.

---

## Problem

Legacy IT documentation lives across many Confluence pages in different spaces. New hires cannot navigate it efficiently. There is no single entry point that understands questions in natural language, respects who can see what, and points to authoritative sources.

---

## Scope

**Phase 1 (this spec):** Confluence only.
**Future phases:** Jira tickets, GitLab READMEs, Slack threads.

---

## Architecture

Single FastAPI backend + plain HTML/JS frontend + ChromaDB (file-backed) + SQLite (chat history). No external queue, no Redis, no separate metadata DB.

```
Browser → FastAPI → ChromaDB (vector search)
                  → Confluence Cloud API (permission check)
                  → Qwen on AgentBase (LLM, OpenAI-compatible)
                  → SQLite (conversation history)

Confluence webhooks → FastAPI /webhook → re-index changed pages
```

---

## Components

### Frontend (`frontend/index.html`)

Plain HTML + vanilla JS. No build step — serves directly from FastAPI as a static file.

**Email screen:** Shown on first load (or if no session). User enters their company email. Below the input, show their last 10 messages as a preview (fetched from `GET /conversations/{email}`). Submitting the email takes them straight into the chat screen with that history already rendered.

**Chat screen:** Full-width layout. User messages on the right (indigo tinted), assistant messages on the left. Below each assistant message, render citation pills — pill-shaped links with a 📄 icon, page title, and href to the Confluence page URL. Responses stream via SSE.

### Backend (`backend/`)

**`main.py` — FastAPI routes:**

| Route | Description |
|---|---|
| `POST /chat` | Takes `{email, message}`. Loads last 10 messages for that email from SQLite. Runs retrieval pipeline. Calls LLM. Streams response + appends citations. Saves exchange to SQLite. |
| `POST /webhook` | Receives Confluence `page:created`, `page:updated`, `page:deleted` events. Validates HMAC signature. Triggers re-indexing of the affected page. Returns 200 immediately; re-indexing is async. |
| `POST /index` | Admin endpoint. Triggers full crawl and indexing of configured Confluence spaces. Run once before the demo. |
| `GET /conversations/{email}` | Returns list of recent messages for that email, ordered by `created_at` desc, limited to last 50. |

**`indexer.py` — Indexing pipeline:**

1. `ConfluenceLoader` (LangChain) fetches pages from configured space keys using admin API token.
2. `RecursiveCharacterTextSplitter` (chunk_size=500, overlap=50) splits content.
3. `HuggingFaceEmbeddings` (`sentence-transformers/all-MiniLM-L6-v2`) embeds each chunk — free, runs locally.
4. ChromaDB upsert. Each chunk document ID is `{page_id}_{chunk_index}` to enable clean replacement on re-index.

Each chunk's metadata: `page_id`, `page_title`, `page_url`, `space_key`, `last_modified`, `chunk_index`.

**`retriever.py` — Permission-filtered retrieval:**

1. Embed the user's question.
2. ChromaDB similarity search → top-20 candidates.
3. Look up user's accessible page IDs via `confluence.py` (cached in-memory per email, 2-min TTL).
4. Filter candidates to allowed pages → take top-5.
5. Return chunks + their metadata for use in the prompt.

**`confluence.py` — Confluence API client:**

Uses a single **admin API token** (env var `CONFLUENCE_ADMIN_TOKEN`) for all Confluence calls. Key operations:
- `get_accessible_page_ids(email)` — calls Confluence REST API to determine which pages the given email address can read. Caches result per email with 2-min TTL (in-memory dict).
- `get_page_content(page_id)` — fetches full page body for re-indexing.
- `validate_webhook(headers, body)` — verifies HMAC-SHA256 signature using `CONFLUENCE_WEBHOOK_SECRET`.

**`llm.py` — LLM chain:**

Uses LangChain `ChatOpenAI` with:
- `base_url = https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1`
- `api_key = AGENTBASE_API_KEY` (env var)
- `model = <Qwen model path from AgentBase>`

System prompt instructs the model to:
- Answer only from the provided context chunks.
- When referencing information, say which page it comes from by title.
- If the context does not contain enough information, say so — do not hallucinate.
- Keep answers concise and practical.

Citations are extracted post-generation: the backend appends the metadata (title + URL) of the chunks that were passed in the prompt as structured data alongside the response text.

**`db.py` — SQLite:**

Single table:

```sql
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    role TEXT NOT NULL,           -- 'user' or 'assistant'
    content TEXT NOT NULL,
    citations_json TEXT,          -- JSON array of {title, url}, null for user messages
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_email_created ON conversations(email, created_at);
```

At query time, load the last 10 rows for the email ordered by `created_at` and pass them as the conversation history to the LLM. The UI fetches up to 50 rows for display, but only the most recent 10 are sent to the LLM to keep the context window bounded.

---

## Data Flow — Query

```
1. User enters email → stored client-side (sessionStorage)
2. User sends message → POST /chat {email, message}
3. Backend: embed message → ChromaDB top-20 → filter by Confluence ACL → top-5 chunks
4. Build messages array: system prompt + last 10 history + retrieved context + user message
5. Call Qwen via AgentBase → stream tokens back via SSE
6. On stream complete: save user message + assistant message + citations to SQLite
7. Frontend renders response + citation pills
```

## Data Flow — Real-time Sync

```
1. Confluence fires webhook → POST /webhook
2. Validate HMAC signature → 401 if invalid
3. Extract page_id from payload
4. If page:deleted → delete all ChromaDB docs where metadata.page_id == page_id
5. If page:created or page:updated → fetch content → re-chunk → delete old → upsert new
6. Return 200 (processing is async, does not block webhook response)
```

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| No accessible chunks found after permission filter | Reply: "I couldn't find any documentation you have access to for that question." |
| Confluence API unavailable during permission check | Serve stale cache if age < 10 min. Otherwise return error: "Unable to verify your access rights. Please try again shortly." |
| Webhook HMAC validation fails | Return 401, log warning, take no action. |
| LLM timeout or error | Return: "Something went wrong generating a response. Please try again." |
| Email not found in Confluence | Treat as zero accessible pages — user sees the "no accessible docs" message. |

---

## Configuration (environment variables)

| Variable | Description |
|---|---|
| `CONFLUENCE_BASE_URL` | e.g. `https://yourorg.atlassian.net` |
| `CONFLUENCE_ADMIN_TOKEN` | Admin API token for permission checks + page fetches |
| `CONFLUENCE_ADMIN_EMAIL` | Email associated with the admin token (required by Confluence basic auth) |
| `CONFLUENCE_SPACE_KEYS` | Comma-separated space keys to index, e.g. `DEV,INFRA,ONBOARD` |
| `CONFLUENCE_WEBHOOK_SECRET` | HMAC secret registered on the Confluence webhook |
| `AGENTBASE_API_KEY` | GreenNode AI Platform API key |
| `AGENTBASE_MODEL_PATH` | Qwen model path from AgentBase, e.g. `qwen/qwen2.5-72b-instruct` |
| `DATABASE_PATH` | Path to SQLite file, default `./data/conversations.db` |
| `CHROMA_PATH` | Path to ChromaDB persist directory, default `./chroma_db` |

---

## Testing (pre-demo checklist)

1. Run `POST /index` → confirm ChromaDB is populated.
2. Query with admin email → verify answer is grounded in real Confluence content and citations are correct.
3. Query with a second email that has access to fewer spaces → verify it receives fewer/different results and does not see restricted content.
4. Update a page in Confluence → wait for webhook → re-query → verify answer reflects the change.

---

## Out of Scope (Phase 1)

- OAuth / SSO login (replaced by email-only input for demo)
- Jira, GitLab, Slack integration
- Multi-tenant or production deployment
- User feedback / thumbs up-down on answers
- Admin UI for managing indexed spaces
