import os
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from dotenv import load_dotenv

load_dotenv()

_CHROMA_HOST = os.getenv("CHROMA_HOST", "")
_CHROMA_PORT = int(os.getenv("CHROMA_PORT", "443").strip())
_CHROMA_SSL = os.getenv("CHROMA_SSL", "true").lower() == "true"
_CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")

_ef: SentenceTransformerEmbeddingFunction | None = None
_client: chromadb.ClientAPI | None = None


def _get_ef() -> SentenceTransformerEmbeddingFunction:
    global _ef
    if _ef is None:
        _ef = SentenceTransformerEmbeddingFunction(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )
    return _ef


def get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        if _CHROMA_HOST:
            _client = chromadb.HttpClient(
                host=_CHROMA_HOST, port=_CHROMA_PORT, ssl=_CHROMA_SSL
            )
        else:
            _client = chromadb.PersistentClient(path=_CHROMA_PATH)
    return _client


def get_collection() -> chromadb.Collection:
    return get_client().get_or_create_collection(
        "confluence_pages", embedding_function=_get_ef()
    )
