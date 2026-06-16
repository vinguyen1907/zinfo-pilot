import asyncio, json, os
from contextlib import asynccontextmanager
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from backend.db import init_db, save_message, get_history
from backend.retriever import retrieve
from backend.llm import build_llm, build_messages, extract_citations
from backend.confluence import validate_webhook

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

@app.get("/status")
async def status():
    import chromadb
    chroma_path = os.getenv("CHROMA_PATH", "./chroma_db")
    try:
        client = chromadb.PersistentClient(path=chroma_path)
        col = client.get_or_create_collection("confluence_pages")
        return {"indexed_chunks": col.count()}
    except Exception as exc:
        return {"indexed_chunks": 0, "error": str(exc)}

@app.post("/index")
async def index(background_tasks: BackgroundTasks):
    from backend.indexer import run_full_index
    background_tasks.add_task(run_full_index)
    return {"status": "indexing started"}

# Serve frontend — mount last so API routes take priority
_frontend = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_frontend):
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="static")
