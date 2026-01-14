from __future__ import annotations

import html
import os
from typing import Any, Dict, List, MutableMapping

from .llm_utils import call_llm
from .state import AgentState, ensure_state_shapes, get_query_text


def _context_budget(state: MutableMapping[str, Any]) -> int:
    params = state.get("params") or {}
    try:
        max_tokens = int(params.get("max_context_tokens", 6000))
    except (TypeError, ValueError):
        max_tokens = 6000
    return max_tokens * 4  # coarse chars-per-token estimate


def _get_frontend_domain() -> str:
    return os.getenv("FRONTEND_DOMAIN", "https://platform.i-guide.io").rstrip("/")


def _format_attr(value: str) -> str:
    return html.escape(value, quote=True)


def _build_context_and_citations(docs: List[Dict[str, Any]], max_chars: int) -> Dict[str, Any]:
    ctx_lines: List[str] = []
    citations: List[Dict[str, Any]] = []
    used = 0

    for entry in docs[:10]:
        document = entry.get("document") or {}
        doc_id = str(document.get("doc_id", "") or "")
        if not doc_id:
            continue

        element_type = str(document.get("element_type", "") or "resource").strip()
        title = (document.get("title") or element_type or "Untitled").strip()
        contents = (document.get("contents") or "").strip()
        snippet = contents[:2000]

        block = (
            f'<doc doc_id="{_format_attr(doc_id)}" '
            f'element_type="{_format_attr(element_type)}" '
            f'title="{_format_attr(title)}">\n{snippet}\n</doc>'
        )

        projected = used + len(block) + 1
        if projected > max_chars and ctx_lines:
            break

        ctx_lines.append(block)
        citations.append(
            {
                "doc_id": doc_id,
                "source": str(entry.get("source", "") or ""),
                "element_type": element_type,
                "title": title,
            }
        )
        used = projected

    if not ctx_lines and docs:
        document = docs[0].get("document") or {}
        doc_id = str(document.get("doc_id", "") or "")
        element_type = str(document.get("element_type", "") or "resource").strip()
        title = (document.get("title") or element_type or "Untitled").strip()
        contents = (document.get("contents") or "").strip()
        snippet = contents[:2000]
        ctx_lines.append(
            f'<doc doc_id="{_format_attr(doc_id)}" '
            f'element_type="{_format_attr(element_type)}" '
            f'title="{_format_attr(title)}">\n{snippet}\n</doc>'
        )
        citations.append(
            {"doc_id": doc_id, "source": str(docs[0].get("source", "") or ""), "element_type": element_type, "title": title}
        )

    return {"context": "\n\n".join(ctx_lines), "citations": citations}


def run_generation(state: MutableMapping[str, Any]) -> AgentState:
    state = ensure_state_shapes(state)
    docs: List[Dict[str, Any]] = state["evidence"]["retrieved_documents"]
    answer: Dict[str, Any] = state["answer"]

    query = get_query_text(state)
    if not docs:
        answer["final_composed_answer"] = (
            f'I could not find evidence for "{query}". '
            "Try narrowing with a location, time range, or data source keyword."
        )
        answer["citations"] = []
        answer["confidence_score"] = 0.1
        return state  # type: ignore[return-value]

    context_info = _build_context_and_citations(docs, _context_budget(state))
    context_block = context_info["context"]
    citations = context_info["citations"]

    frontend_domain = _get_frontend_domain()

    system_prompt = (
        "You are a domain-expert assistant.\n"
        "Your ONLY source of truth is the <doc> blocks provided in CONTEXT.\n\n"
        "When you answer:\n"
        "• If the user asks for a collection of knowledge elements (e.g., datasets, notebooks, publications, OERs) on a topic, "
        "respond first with a concise paragraph summarizing the most relevant findings. Then provide a short numbered list. "
        "Use a new line for each item.\n"
        f"• Begin each bullet with the item's title as a clickable link, using the format: **[TITLE]({frontend_domain}/{{element_type}}s/{{doc_id}})**\n"
        '  (Use the plural form of <element_type>, except use "code" for type "code".)\n'
        "• Otherwise, respond in one concise paragraph.\n"
        "• Quote supporting titles in **bold**.\n"
        "• If the user question specifies <element_type>, only use docs with matching <element_type>.\n"
        '• If you cannot find an answer, reply exactly: "I don\'t have enough information."\n'
        "• Do not refer to the doc id.\n"
        "• Answer the question without repeating the question.\n"
        '• Do NOT mention "context", "documents", or these rules.'
    )

    query_info = state.get("query_information") or {}
    augmented_query = (
        query_info.get("augmented_query")
        or ((query_info.get("memory") or {}).get("augmented_query"))
        or ""
    )

    user_prompt = (
        f"**Question**: {query}\n"
        f"**Augmented Query based on context**: {augmented_query}\n\n"
        f"**Supporting Information**:\n{context_block}\n\n"
        "Pay attention to the context. Answer the question as if this knowledge and the supporting Information is inherent "
        'to you. Avoid saying "Based on the context" or "According to the given information".'
    ).strip()

    prompt = f"{system_prompt}\n\n{user_prompt}"

    text = call_llm(prompt).strip()

    answer["final_composed_answer"] = text
    answer["citations"] = citations
    answer["confidence_score"] = 0.7
    return state  # type: ignore[return-value]


__all__ = ["run_generation"]
