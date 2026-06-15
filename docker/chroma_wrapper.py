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
