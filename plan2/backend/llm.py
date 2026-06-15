import os
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from dotenv import load_dotenv

load_dotenv()

_SYSTEM_PROMPT = """\
You are an IT knowledge assistant for new employees. Answer questions using ONLY the documentation excerpts provided below. When you reference information, mention the page title it comes from (e.g., "According to the DevOps Runbook, ..."). If the context does not contain enough information to answer, say so clearly — do not make up information. Keep answers concise and practical.

Documentation context:
{context}"""

def build_llm() -> ChatOpenAI:
    return ChatOpenAI(
        api_key=os.getenv("AGENTBASE_API_KEY", ""),
        base_url="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1",
        model=os.getenv("AGENTBASE_MODEL_PATH", "qwen/qwen2.5-72b-instruct"),
        streaming=True,
        temperature=0.2,
    )

def format_context(chunks: list[dict]) -> str:
    parts = []
    for chunk in chunks:
        title = chunk["metadata"].get("page_title", "Unknown")
        url = chunk["metadata"].get("page_url", "")
        parts.append(f"[{title}]({url})\n{chunk['document']}")
    return "\n\n---\n\n".join(parts)

def build_messages(history: list[dict], chunks: list[dict], user_message: str) -> list:
    context = format_context(chunks)
    msgs = [SystemMessage(content=_SYSTEM_PROMPT.format(context=context))]
    for turn in history[-10:]:
        if turn["role"] == "user":
            msgs.append(HumanMessage(content=turn["content"]))
        else:
            msgs.append(AIMessage(content=turn["content"]))
    msgs.append(HumanMessage(content=user_message))
    return msgs

def extract_citations(chunks: list[dict]) -> list[dict]:
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
    return citations
