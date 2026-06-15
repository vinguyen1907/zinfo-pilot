import asyncio, json, os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from backend.db import init_db, save_message, get_history
from backend.retriever_stub import retrieve  # swap to backend.retriever once Plan 2 is done

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
        async def no_access_stream():
            msg = "I couldn't find any documentation you have access to for that question."
            yield f"data: {json.dumps({'token': msg})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(no_access_stream(), media_type="text/event-stream")

    # Build citations from chunk metadata
    seen: set[str] = set()
    citations: list[dict] = []
    for chunk in chunks:
        pid = chunk["metadata"].get("page_id", "")
        if pid not in seen:
            seen.add(pid)
            citations.append({
                "title": chunk["metadata"].get("page_title", ""),
                "url": chunk["metadata"].get("page_url", ""),
            })

    # Stub response: echo the top chunk content as a streaming response
    # Plan 2 will replace this block with a real LLM call
    stub_answer = (
        f"[STUB — RAG not yet connected] "
        f"Based on '{chunks[0]['metadata']['page_title']}': "
        f"{chunks[0]['document'][:300]}"
    )

    await save_message(req.email, "user", req.message, None)

    full_response: list[str] = []

    async def stream():
        try:
            for word in stub_answer.split(" "):
                token = word + " "
                full_response.append(token)
                yield f"data: {json.dumps({'token': token})}\n\n"
                await asyncio.sleep(0.02)  # simulate streaming
            response_text = "".join(full_response)
            await save_message(req.email, "assistant", response_text, citations)
            yield f"data: {json.dumps({'citations': citations})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': 'Something went wrong. Please try again.'})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")

@app.get("/conversations/{email}")
async def conversations(email: str):
    rows = await get_history(email, limit=50)
    return {"messages": rows}

# Serve frontend static files
_frontend = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_frontend):
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="static")
