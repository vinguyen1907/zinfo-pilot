import asyncio, json, os
from contextlib import asynccontextmanager
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from backend.db import init_db, save_message, get_history
from backend.retriever import retrieve
from backend.llm import build_llm, build_messages, extract_citations
from backend.confluence import validate_webhook

load_dotenv()


def _reindex_if_empty():
    try:
        from backend.chroma_client import get_collection
        col = get_collection()
        count = col.count()
        if count == 0:
            import logging
            logging.getLogger(__name__).info(
                "[startup] ChromaDB collection is empty — starting full re-index"
            )
            from backend.indexer import run_full_index
            run_full_index()
        else:
            import logging
            logging.getLogger(__name__).info(
                "[startup] ChromaDB OK — %d chunks already indexed", count
            )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("[startup] Could not check ChromaDB: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(asyncio.to_thread(_reindex_if_empty))
    yield

app = FastAPI(lifespan=lifespan)

class ChatRequest(BaseModel):
    email: str
    message: str

@app.post("/chat")
async def chat(req: ChatRequest):
    chunks = await asyncio.to_thread(retrieve, req.message, req.email)

    if not chunks:
        async def no_access_stream():
            msg = (
                "I couldn't find any documentation you have access to for that question. "
                "If you believe this is an error, contact your Confluence administrator to verify your space permissions."
            )
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
    rows = await get_history(email, limit=50)
    return {"messages": rows}

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
        from backend.indexer import delete_page_chunks, index_page
        from backend.confluence import get_page_content
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

@app.get("/stats")
async def stats():
    def _get_stats():
        from backend.chroma_client import get_collection
        col = get_collection()
        total_chunks = col.count()
        if total_chunks == 0:
            return {"total_chunks": 0, "total_pages": 0, "spaces": {}}

        result = col.get(include=["metadatas"])
        metadatas = result.get("metadatas", [])

        pages: dict[str, dict] = {}
        spaces: dict[str, int] = {}
        for m in metadatas:
            pid = m.get("page_id", "")
            if pid and pid not in pages:
                pages[pid] = {"title": m.get("page_title", ""), "url": m.get("page_url", "")}
            space = m.get("space_key", "unknown")
            spaces[space] = spaces.get(space, 0) + 1

        return {
            "total_chunks": total_chunks,
            "total_pages": len(pages),
            "spaces": spaces,
            "sample_pages": list(pages.values())[:5],
        }

    try:
        return await asyncio.to_thread(_get_stats)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))

@app.post("/index")
async def index(background_tasks: BackgroundTasks):
    from backend.indexer import run_full_index
    background_tasks.add_task(run_full_index)
    return {"status": "indexing started"}

# Serve frontend (single-file SPA — no separate static assets needed)
_frontend = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend"))
_index = os.path.join(_frontend, "index.html")
if os.path.isfile(_index):
    @app.get("/")
    async def root():
        return FileResponse(_index)
