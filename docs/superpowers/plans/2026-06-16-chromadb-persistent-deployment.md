# ChromaDB Persistent Deployment Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy ChromaDB as a separate AgentBase runtime so vector data survives main app (`zinfo-rag`) rebuilds and restarts.

**Architecture:** Two AgentBase runtimes — `zinfo-rag` (main FastAPI app) và `zinfo-chroma` (ChromaDB HTTP server). Main app dùng `chromadb.HttpClient` thay vì `PersistentClient` để kết nối sang runtime ChromaDB. Auto-reindex khi ChromaDB collection rỗng (backup cho trường hợp chroma runtime restart).

**Tech Stack:** chromadb==0.5.3 (HTTP server mode), FastAPI proxy wrapper, AgentBase CR, AgentBase Runtime

---

## File Map

| Action | File | Mục đích |
|--------|------|----------|
| Create | `docker/chroma_wrapper.py` | FastAPI proxy: `/health` + forward `/api/*` → ChromaDB |
| Create | `Dockerfile.chroma` | Docker image cho `zinfo-chroma` runtime |
| Create | `plan2/backend/chroma_client.py` | Singleton HttpClient factory (dùng chung cho indexer + retriever) |
| Modify | `plan2/backend/indexer.py` | Thay `PersistentClient` → `HttpClient` từ `chroma_client` |
| Modify | `plan2/backend/retriever.py` | Thay `PersistentClient` → `HttpClient` từ `chroma_client` |
| Modify | `plan2/backend/main.py` | Thêm auto-reindex khi startup nếu collection rỗng |
| Modify | `.env` | Thêm `CHROMA_HOST`, `CHROMA_PORT`, `CHROMA_SSL` |

---

## Task 1: Tạo ChromaDB wrapper (`docker/chroma_wrapper.py` + `Dockerfile.chroma`)

ChromaDB HTTP server không có endpoint `/health`. Wrapper này chạy ChromaDB trên cổng 8000 và expose FastAPI trên 8080 với `/health` và proxy `/api/*`.

**Files:**
- Create: `docker/chroma_wrapper.py`
- Create: `Dockerfile.chroma`

- [ ] **Step 1: Tạo thư mục docker**

```bash
mkdir -p /Users/sol/Projects/zinfo-pilot/docker
```

- [ ] **Step 2: Tạo `docker/chroma_wrapper.py`**

```python
import subprocess
import threading
import time

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response

CHROMA_INTERNAL_PORT = 8000
STARTUP_WAIT_SECONDS = 5

app = FastAPI()


def _start_chroma():
    subprocess.run(
        [
            "chroma", "run",
            "--path", "/chroma/data",
            "--host", "0.0.0.0",
            "--port", str(CHROMA_INTERNAL_PORT),
        ],
        check=True,
    )


threading.Thread(target=_start_chroma, daemon=True).start()


def _wait_for_chroma():
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            httpx.get(f"http://localhost:{CHROMA_INTERNAL_PORT}/api/v1/heartbeat", timeout=2)
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("ChromaDB did not start within 30 seconds")


_wait_for_chroma()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(path: str, request: Request) -> Response:
    async with httpx.AsyncClient() as client:
        url = f"http://localhost:{CHROMA_INTERNAL_PORT}/api/{path}"
        resp = await client.request(
            method=request.method,
            url=url,
            content=await request.body(),
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            params=dict(request.query_params),
            timeout=60,
        )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
```

- [ ] **Step 3: Tạo `Dockerfile.chroma`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir \
    "chromadb==0.5.3" \
    "fastapi==0.111.0" \
    "uvicorn[standard]==0.29.0" \
    "httpx==0.27.0"
RUN mkdir -p /chroma/data
COPY docker/chroma_wrapper.py .
EXPOSE 8080
CMD ["python", "chroma_wrapper.py"]
```

- [ ] **Step 4: Commit**

```bash
git add docker/chroma_wrapper.py Dockerfile.chroma
git commit -m "feat: add ChromaDB HTTP wrapper and Dockerfile for separate runtime"
```

---

## Task 2: Tạo shared ChromaDB client factory (`plan2/backend/chroma_client.py`)

Thay vì mỗi file tự tạo `PersistentClient`, tập trung logic vào một nơi. Biến `CHROMA_HOST` quyết định dùng `HttpClient` (production) hay `PersistentClient` (local dev không có chroma server).

**Files:**
- Create: `plan2/backend/chroma_client.py`

- [ ] **Step 1: Tạo `plan2/backend/chroma_client.py`**

```python
import os
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from dotenv import load_dotenv

load_dotenv()

_CHROMA_HOST = os.getenv("CHROMA_HOST", "")
_CHROMA_PORT = int(os.getenv("CHROMA_PORT", "443"))
_CHROMA_SSL = os.getenv("CHROMA_SSL", "true").lower() == "true"
_CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")

_ef = SentenceTransformerEmbeddingFunction(model_name="sentence-transformers/all-MiniLM-L6-v2")


def get_client() -> chromadb.ClientAPI:
    if _CHROMA_HOST:
        return chromadb.HttpClient(host=_CHROMA_HOST, port=_CHROMA_PORT, ssl=_CHROMA_SSL)
    return chromadb.PersistentClient(path=_CHROMA_PATH)


def get_collection() -> chromadb.Collection:
    return get_client().get_or_create_collection("confluence_pages", embedding_function=_ef)
```

- [ ] **Step 2: Commit**

```bash
git add plan2/backend/chroma_client.py
git commit -m "feat: add shared ChromaDB client factory with HttpClient support"
```

---

## Task 3: Cập nhật `indexer.py` và `retriever.py` dùng `chroma_client`

**Files:**
- Modify: `plan2/backend/indexer.py:66-68`
- Modify: `plan2/backend/retriever.py:9-14`

- [ ] **Step 1: Cập nhật `plan2/backend/indexer.py`**

Xóa các import và hàm sau trong `indexer.py`:

```python
# XÓA các dòng này (hiện tại ở đầu file):
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")
_ef = SentenceTransformerEmbeddingFunction(model_name="sentence-transformers/all-MiniLM-L6-v2")

def _collection() -> chromadb.Collection:
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection("confluence_pages", embedding_function=_ef)
```

Thay bằng:

```python
from plan2.backend.chroma_client import get_collection as _collection
```

File `indexer.py` sau khi sửa (phần đầu):

```python
import os
import re
from typing import Any

from dotenv import load_dotenv
from plan2.backend.chroma_client import get_collection as _collection

load_dotenv()

SPACE_KEYS = [k.strip() for k in os.getenv("CONFLUENCE_SPACE_KEYS", "").split(",") if k.strip()]

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
```

Các hàm còn lại (`_split_text`, `_splitter_split`, `make_chunk_id`, `split_page_into_chunks`, `_strip_html`, `index_page`, `delete_page_chunks`, `run_full_index`) giữ nguyên không đổi. Chỉ thay `col = _collection()` — lúc này `_collection` đã là function từ `chroma_client`.

- [ ] **Step 2: Cập nhật `plan2/backend/retriever.py`**

File `retriever.py` sau khi sửa:

```python
import os

from dotenv import load_dotenv
from plan2.backend.chroma_client import get_collection as _collection
from plan2.backend.confluence import get_accessible_page_ids

load_dotenv()


def filter_by_acl(candidates: list[dict], allowed_page_ids: set[str], top_k: int = 5) -> list[dict]:
    return [c for c in candidates if c["metadata"]["page_id"] in allowed_page_ids][:top_k]


def retrieve(query: str, email: str, top_k: int = 5) -> list[dict]:
    col = _collection()
    results = col.query(query_texts=[query], n_results=20, include=["documents", "metadatas"])

    candidates = [
        {
            "document": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
        }
        for i in range(len(results["ids"][0]))
    ]

    allowed = get_accessible_page_ids(email)
    return filter_by_acl(candidates, allowed, top_k=top_k)
```

- [ ] **Step 3: Commit**

```bash
git add plan2/backend/indexer.py plan2/backend/retriever.py
git commit -m "refactor: use shared chroma_client factory in indexer and retriever"
```

---

## Task 4: Thêm auto-reindex khi startup (`main.py`)

Khi `zinfo-chroma` restart, collection sẽ rỗng. `zinfo-rag` detect điều này khi startup và tự trigger re-index.

**Files:**
- Modify: `plan2/backend/main.py:20-22`

- [ ] **Step 1: Cập nhật hàm `startup` trong `plan2/backend/main.py`**

Thay đổi hàm `startup` hiện tại:

```python
@app.on_event("startup")
async def startup():
    await init_db()
```

Thành:

```python
@app.on_event("startup")
async def startup():
    await init_db()
    await asyncio.to_thread(_reindex_if_empty)


def _reindex_if_empty():
    try:
        from plan2.backend.chroma_client import get_collection
        col = get_collection()
        count = col.count()
        if count == 0:
            print("[startup] ChromaDB collection is empty — starting full re-index")
            from plan2.backend.indexer import run_full_index
            run_full_index()
        else:
            print(f"[startup] ChromaDB OK — {count} chunks already indexed")
    except Exception as exc:
        print(f"[startup] Could not check ChromaDB: {exc}")
```

- [ ] **Step 2: Commit**

```bash
git add plan2/backend/main.py
git commit -m "feat: auto-reindex Confluence on startup if ChromaDB collection is empty"
```

---

## Task 5: Cập nhật `.env` với ChromaDB connection vars

**Files:**
- Modify: `.env`

- [ ] **Step 1: Thêm placeholder vào `.env`**

Thêm 3 dòng vào cuối file `.env` (sẽ điền `CHROMA_HOST` thật sau khi deploy runtime ở Task 6):

```
CHROMA_HOST=<điền sau khi deploy zinfo-chroma runtime>
CHROMA_PORT=443
CHROMA_SSL=true
```

Ghi chú: `CHROMA_PATH` hiện có trong `.env` (dùng cho local dev) — giữ nguyên, không xóa. Khi `CHROMA_HOST` được set, code sẽ dùng `HttpClient` thay vì `PersistentClient`.

- [ ] **Step 2: Cập nhật `.env.example`**

```
CHROMA_HOST=endpoint-xxx.agentbase-runtime.aiplatform.vngcloud.vn
CHROMA_PORT=443
CHROMA_SSL=true
CHROMA_PATH=./chroma_db
```

- [ ] **Step 3: Commit `.env.example`** (không commit `.env`)

```bash
git add .env.example
git commit -m "docs: add CHROMA_HOST env vars to .env.example"
```

---

## Task 6: Build & deploy `zinfo-chroma` runtime trên AgentBase

**Yêu cầu**: AgentBase CR đã được login từ trước (session `.greennode.json` còn hiệu lực).

- [ ] **Step 1: Login vào AgentBase CR**

```bash
bash .claude/skills/agentbase/scripts/cr.sh credentials docker-login
```

Expected output: `Login Succeeded`

- [ ] **Step 2: Build image ChromaDB**

```bash
TAG="v$(date +%Y%m%d%H%M%S)"
docker build --platform linux/amd64 \
  -f Dockerfile.chroma \
  -t vcr.vngcloud.vn/111480-abp111968/zinfo-chroma:${TAG} .
echo "Tag: ${TAG}"
```

Build sẽ mất ~3-5 phút (download chromadb + deps).

- [ ] **Step 3: Push image**

```bash
docker push vcr.vngcloud.vn/111480-abp111968/zinfo-chroma:${TAG}
```

- [ ] **Step 4: Tạo runtime `zinfo-chroma`**

```bash
bash .claude/skills/agentbase/scripts/runtime.sh create \
  --name "zinfo-chroma" \
  --image "vcr.vngcloud.vn/111480-abp111968/zinfo-chroma:${TAG}" \
  --flavor "2x4-general" \
  --min-replicas 1 \
  --max-replicas 1 \
  --cpu-scale 70 \
  --mem-scale 70 \
  --from-cr
```

Expected: JSON response với `id`, `name`, `status`.

- [ ] **Step 5: Chờ runtime ACTIVE và lấy endpoint URL**

```bash
bash .claude/skills/agentbase/scripts/runtime.sh list
```

Tìm `zinfo-chroma` trong `listData`, copy giá trị `endpointUrl`. Format: `https://endpoint-xxx.agentbase-runtime.aiplatform.vngcloud.vn`

- [ ] **Step 6: Verify health check**

```bash
curl https://<endpoint-url-của-zinfo-chroma>/health
```

Expected: `{"status":"ok"}`

---

## Task 7: Cập nhật `.env` và rebuild `zinfo-rag`

- [ ] **Step 1: Điền `CHROMA_HOST` thật vào `.env`**

Lấy hostname từ endpoint URL của `zinfo-chroma` (bỏ `https://`):

```
CHROMA_HOST=endpoint-xxx.agentbase-runtime.aiplatform.vngcloud.vn
CHROMA_PORT=443
CHROMA_SSL=true
```

- [ ] **Step 2: Build image mới cho `zinfo-rag`**

```bash
TAG="v$(date +%Y%m%d%H%M%S)"
docker build --platform linux/amd64 \
  -t vcr.vngcloud.vn/111480-abp111968/zinfo-rag:${TAG} .
docker push vcr.vngcloud.vn/111480-abp111968/zinfo-rag:${TAG}
```

- [ ] **Step 3: Update runtime `zinfo-rag`**

```bash
bash .claude/skills/agentbase/scripts/runtime.sh update \
  --name "zinfo-rag" \
  --image "vcr.vngcloud.vn/111480-abp111968/zinfo-rag:${TAG}" \
  --env-file .env \
  --from-cr
```

- [ ] **Step 4: Verify end-to-end**

```bash
# 1. Health check main app
curl https://endpoint-4a93c7c8-2add-486c-91b9-48690408a847.agentbase-runtime.aiplatform.vngcloud.vn/health

# 2. Kiểm tra log startup — nên thấy "ChromaDB collection is empty — starting full re-index"
# (xem trong AgentBase console)

# 3. Sau khi re-index xong (~5-10 phút), test chat
curl -X POST \
  https://endpoint-4a93c7c8-2add-486c-91b9-48690408a847.agentbase-runtime.aiplatform.vngcloud.vn/chat \
  -H "Content-Type: application/json" \
  -d '{"email":"test@vng.com.vn","message":"quy trình onboarding như thế nào?"}'
```

Expected: SSE stream với token trả lời từ Qwen.

---

## Kết quả sau khi hoàn thành

| Trước | Sau |
|-------|-----|
| ChromaDB embedded trong `zinfo-rag` container | ChromaDB chạy trong runtime riêng `zinfo-chroma` |
| Mất data khi rebuild/restart `zinfo-rag` | Data ChromaDB độc lập, chỉ mất khi `zinfo-chroma` restart |
| `zinfo-chroma` restart → auto-reindex khi `zinfo-rag` startup | ✓ |
| Không cần chạy `POST /index` thủ công sau deploy | ✓ (tự động) |
