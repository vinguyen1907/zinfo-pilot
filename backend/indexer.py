import logging
import os
import re
from typing import Any

from dotenv import load_dotenv

from backend.chroma_client import get_collection as _collection

logger = logging.getLogger(__name__)

load_dotenv()

SPACE_KEYS = [k.strip() for k in os.getenv("CONFLUENCE_SPACE_KEYS", "").split(",") if k.strip()]

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def _split_text(text: str, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunk = text[start:]
            if chunk.strip():
                chunks.append(chunk)
            break
        split_pos = end
        for sep in ["\n\n", "\n", " "]:
            pos = text.rfind(sep, start, end)
            if pos > start:
                split_pos = pos + len(sep)
                break
        chunk = text[start:split_pos]
        if chunk.strip():
            chunks.append(chunk)
        start = split_pos - chunk_overlap
        if start <= 0 and split_pos > 0:
            start = split_pos
    return chunks


try:
    try:
        from langchain.text_splitter import RecursiveCharacterTextSplitter
    except ImportError:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    _lc_splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

    def _splitter_split(text: str) -> list[str]:
        return _lc_splitter.split_text(text)

except Exception:
    def _splitter_split(text: str) -> list[str]:
        return _split_text(text)


def make_chunk_id(page_id: str, chunk_index: int) -> str:
    return f"{page_id}_{chunk_index}"


def split_page_into_chunks(
    page_id: str, title: str, url: str, space_key: str, last_modified: str, text: str
) -> list[tuple[str, dict]]:
    raw = _splitter_split(text)
    return [
        (
            chunk,
            {
                "page_id": page_id,
                "page_title": title,
                "page_url": url,
                "space_key": space_key,
                "last_modified": last_modified,
                "chunk_index": i,
            },
        )
        for i, chunk in enumerate(raw)
    ]


def _strip_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "lxml").get_text(separator=" ").strip()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html).strip()


def index_page(page_data: dict[str, Any]):
    from backend.confluence import BASE_URL

    page_id = str(page_data["id"])
    title = page_data.get("title", "")
    webui = page_data.get("_links", {}).get("webui", "")
    url = f"{BASE_URL}/wiki{webui}" if webui else ""
    space_key = page_data.get("space", {}).get("key", "")
    last_modified = page_data.get("version", {}).get("when", "")
    raw_body = page_data.get("body", {}).get("storage", {}).get("value", "")
    text = _strip_html(raw_body)
    if not text:
        return

    chunks = split_page_into_chunks(page_id, title, url, space_key, last_modified, text)
    col = _collection()

    existing = col.get(where={"page_id": page_id})
    if existing["ids"]:
        col.delete(ids=existing["ids"])

    col.upsert(
        ids=[make_chunk_id(page_id, m["chunk_index"]) for _, m in chunks],
        documents=[doc for doc, _ in chunks],
        metadatas=[m for _, m in chunks],
    )


def delete_page_chunks(page_id: str):
    col = _collection()
    existing = col.get(where={"page_id": page_id})
    if existing["ids"]:
        col.delete(ids=existing["ids"])


def run_full_index():
    from backend.confluence import list_all_pages_in_space

    total = 0
    errors = 0
    for space_key in SPACE_KEYS:
        logger.info("[indexer] starting space %s", space_key)
        try:
            pages = list_all_pages_in_space(space_key)
        except Exception as exc:
            logger.error("[indexer] failed to list pages in space %s: %s", space_key, exc)
            errors += 1
            continue
        logger.info("[indexer] %d pages found in %s", len(pages), space_key)
        for page_data in pages:
            page_id = str(page_data.get("id", ""))
            try:
                index_page(page_data)
                total += 1
            except Exception as exc:
                logger.error("[indexer] failed to index page %s: %s", page_id, exc)
                errors += 1
    logger.info("[indexer] done — %d pages indexed, %d errors", total, errors)
