import os
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from dotenv import load_dotenv

load_dotenv()

_CHROMA_HOST = os.getenv("CHROMA_HOST", "")
_CHROMA_PORT = int(os.getenv("CHROMA_PORT", "443"))
_CHROMA_SSL = os.getenv("CHROMA_SSL", "true").lower() == "true"
_CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_db")

_ef = SentenceTransformerEmbeddingFunction(model_name="sentence-transformers/all-MiniLM-L6-v2")


def get_client() -> chromadb.ClientAPI:
    if _CHROMA_HOST:
        return chromadb.HttpClient(host=_CHROMA_HOST, port=_CHROMA_PORT, ssl=_CHROMA_SSL)
    return chromadb.PersistentClient(path=_CHROMA_PATH)


def get_collection() -> chromadb.Collection:
    return get_client().get_or_create_collection("confluence_pages", embedding_function=_ef)
