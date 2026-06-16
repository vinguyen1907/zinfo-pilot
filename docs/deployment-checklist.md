# Deployment Checklist — zinfo-rag RAG System

## Setup & Cấu hình
- [x] Thiết lập IAM credentials AgentBase (`.greennode.json`)
- [x] Sửa `requirements.txt` — nới lỏng pydantic từ `==2.7.1` lên `>=2.7.4,<3.0.0` (conflict với langchain)

## Code changes
- [x] Thêm `GET /health` endpoint vào `plan2/backend/main.py` (bắt buộc để AgentBase đánh dấu runtime là ACTIVE)

## Docker
- [x] Tạo `Dockerfile` — python:3.12-slim, cài `build-essential` (cho chroma-hnswlib), torch CPU-only trước
- [x] Tạo `.dockerignore` — loại trừ `.env`, `.greennode.json`, `venv/`, `chroma_db/`...

## Environment
- [x] Xóa `GREENNODE_CLIENT_ID` và `GREENNODE_CLIENT_SECRET` khỏi `.env` (auto-injected bởi runtime, tên key có dấu cách gây lỗi 400)
- [x] Sửa `CONFLUENCE_WEBHOOK_SECRET` — đặt HMAC secret thật (`74c587...`), bỏ dấu cách thừa

## Deploy lên AgentBase
- [x] Build image `v20260614161205` (linux/amd64)
- [x] Push lên AgentBase CR `vcr.vngcloud.vn/111480-abp111968/zinfo-rag`
- [x] Tạo runtime `zinfo-rag` — flavor 2vCPU/4GB, PUBLIC network
- [x] Rebuild image `v20260614170805` với CONFLUENCE_WEBHOOK_SECRET mới
- [x] Update runtime thành công — endpoint ACTIVE

## Thông tin runtime
- **Runtime ID:** `runtime-64cbce6e-eb6e-4a1e-a2b5-eac31d5ee267`
- **Endpoint:** `https://endpoint-4a93c7c8-2add-486c-91b9-48690408a847.agentbase-runtime.aiplatform.vngcloud.vn`
- **CR image:** `vcr.vngcloud.vn/111480-abp111968/zinfo-rag:v20260614170805`
- **Console:** https://aiplatform.console.vngcloud.vn/agent-runtime?tab=runtime

## Còn lại để hệ thống hoạt động end-to-end
- [ ] Đăng ký webhook trong Confluence Admin
  - URL: `https://endpoint-4a93c7c8-2add-486c-91b9-48690408a847.agentbase-runtime.aiplatform.vngcloud.vn/webhook`
  - Secret: (xem `CONFLUENCE_WEBHOOK_SECRET` trong `.env`)
  - Events: Page created, Page updated, Page deleted
- [ ] Test chat: gửi câu hỏi qua `POST /chat` và kiểm tra RAG trả lời đúng
- [ ] Test webhook: sửa 1 page Confluence, kiểm tra re-index tự động

---

## 2026-06-16 — ChromaDB Persistent Deployment

### Vấn đề
ChromaDB chạy embedded bên trong container `zinfo-rag` → mất toàn bộ vector data mỗi khi rebuild/update runtime.

### Giải pháp
Tách ChromaDB thành runtime riêng (`zinfo-chroma`). `zinfo-rag` kết nối qua `chromadb.HttpClient`. Thêm auto-reindex khi startup nếu collection rỗng.

### Các thay đổi code (branch: feat-plan2)

| Commit | Nội dung |
|--------|----------|
| `c84d8776` | Tạo `docker/chroma_wrapper.py` + `Dockerfile.chroma` — FastAPI proxy `/health` + `/api/*` → ChromaDB subprocess |
| `e74f0e86` | Fix wrapper: lifespan handler, lọc hop-by-hop headers, HTTP 503 khi ChromaDB chết, singleton AsyncClient |
| `34fe4987` | Tạo `plan2/backend/chroma_client.py` — factory HttpClient (production) / PersistentClient (local dev) |
| `90d516b3` | Fix: singleton client, strip port env var |
| `b3d60860` | Refactor `indexer.py` + `retriever.py` — dùng `get_collection` từ factory |
| `464154f8` | Xóa dead import trong `retriever.py` |
| `5b1ee198` | `main.py`: auto-reindex khi startup nếu ChromaDB collection rỗng |
| `9be2f722` | Cập nhật `.env.example` với `CHROMA_HOST`, `CHROMA_PORT`, `CHROMA_SSL` |

### Deploy

- [x] Build `Dockerfile.chroma` (thêm `build-essential` cho `chroma-hnswlib`) → push `zinfo-chroma:v20260616092236`
- [x] Tạo runtime `zinfo-chroma` — ACTIVE
- [x] Rebuild `zinfo-rag` với `CHROMA_HOST` mới → update runtime — ACTIVE

### Thông tin runtime ChromaDB

- **Runtime ID:** `runtime-d63cfaea-69f1-4adf-808a-2325fa8d55ed`
- **Image:** `vcr.vngcloud.vn/111480-abp111968/zinfo-chroma:v20260616092236`
- **Endpoint:** `https://endpoint-1dbaeb61-a0ed-4bbd-885e-1e69e6512b9f.agentbase-runtime.aiplatform.vngcloud.vn`
- **CHROMA_HOST** (trong `.env` của `zinfo-rag`): `endpoint-1dbaeb61-a0ed-4bbd-885e-1e69e6512b9f.agentbase-runtime.aiplatform.vngcloud.vn`

### Hành vi sau khi deploy

| Tình huống | Kết quả |
|------------|---------|
| Rebuild `zinfo-rag` | ChromaDB data giữ nguyên trong `zinfo-chroma` |
| `zinfo-chroma` restart | `zinfo-rag` detect collection rỗng khi startup → tự re-index |
| Confluence page thay đổi | Webhook → `POST /webhook` → re-index page đó |
