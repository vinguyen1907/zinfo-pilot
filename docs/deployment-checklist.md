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
- [ ] Chạy initial index: `POST /index` để crawl toàn bộ Confluence vào ChromaDB
- [ ] Test chat: gửi câu hỏi qua `POST /chat` và kiểm tra RAG trả lời đúng
- [ ] Test webhook: sửa 1 page Confluence, kiểm tra re-index tự động
