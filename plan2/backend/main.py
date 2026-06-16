import asyncio
import json
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from plan2.backend.db import init_db, save_message, get_history
from plan2.backend.retriever import retrieve
from plan2.backend.llm import build_llm, build_messages, extract_citations
from plan2.backend.confluence import validate_webhook

load_dotenv()

app = FastAPI()

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


class ChatRequest(BaseModel):
    email: str
    message: str

@app.post("/chat")
async def chat(req: ChatRequest):
    chunks = await asyncio.to_thread(retrieve, req.message, req.email)

    if not chunks:
        async def no_access_stream():
            msg = "I couldn't find any documentation you have access to for that question."
            yield f"data: {json.dumps({'token': msg})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(no_access_stream(), media_type="text/event-stream")

    history = await get_history(req.email, limit=10)
    messages = build_messages(history, chunks, req.message)
    citations = extract_citations(chunks)
    llm = build_llm()

    await save_message(req.email, "user", req.message, None)
    full_response: list[str] = []

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

@app.get("/conversations/{email}")
async def conversations(email: str):
    history = await get_history(email, limit=50)
    return {"messages": history}

@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    if not validate_webhook(dict(request.headers), body):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body)
    event = payload.get("eventType", "")
    page_id = str(
        payload.get("page", {}).get("id", "")
        or payload.get("pageId", "")
    )

    if not page_id:
        return {"status": "ignored"}

    async def reindex():
        from plan2.backend.indexer import delete_page_chunks, index_page
        from plan2.backend.confluence import get_page_content
        if event == "page:deleted":
            await asyncio.to_thread(delete_page_chunks, page_id)
        else:
            page_data = await asyncio.to_thread(get_page_content, page_id)
            await asyncio.to_thread(index_page, page_data)

    task = asyncio.create_task(reindex())
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    return {"status": "accepted"}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/index")
async def index():
    from plan2.backend.indexer import run_full_index
    task = asyncio.create_task(asyncio.to_thread(run_full_index))
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    return {"status": "indexing started"}

# Static files (frontend) — mount last so API routes take priority
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="static")
