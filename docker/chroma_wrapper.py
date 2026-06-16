import subprocess
import threading
import time
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response

CHROMA_INTERNAL_PORT = 8000

_HOP_BY_HOP = frozenset({
    "transfer-encoding", "connection", "keep-alive", "te",
    "trailers", "upgrade", "proxy-authenticate", "proxy-authorization",
})

_chroma_error: Exception | None = None
_http_client: httpx.AsyncClient | None = None


def _start_chroma():
    global _chroma_error
    try:
        subprocess.run(
            [
                "chroma", "run",
                "--path", "/chroma/data",
                "--host", "0.0.0.0",
                "--port", str(CHROMA_INTERNAL_PORT),
            ],
            check=True,
        )
    except Exception as exc:
        _chroma_error = exc


def _wait_for_chroma():
    deadline = time.time() + 30
    while time.time() < deadline:
        if _chroma_error:
            raise RuntimeError(f"ChromaDB failed to start: {_chroma_error}")
        try:
            httpx.get(f"http://localhost:{CHROMA_INTERNAL_PORT}/api/v1/heartbeat", timeout=2)
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("ChromaDB did not start within 30 seconds")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    threading.Thread(target=_start_chroma, daemon=True).start()
    import asyncio
    await asyncio.to_thread(_wait_for_chroma)
    _http_client = httpx.AsyncClient(timeout=60)
    yield
    await _http_client.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    if _chroma_error:
        raise HTTPException(status_code=503, detail=str(_chroma_error))
    return {"status": "ok"}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(path: str, request: Request) -> Response:
    url = f"http://localhost:{CHROMA_INTERNAL_PORT}/api/{path}"
    resp = await _http_client.request(
        method=request.method,
        url=url,
        content=await request.body(),
        headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
        params=dict(request.query_params),
    )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
