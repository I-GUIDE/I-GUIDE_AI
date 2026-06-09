"""Evidence-quality utilities for the agent path: LLM rerank + grounding audit.

These port the capabilities that previously lived ONLY in the deprecated
``rag_pipeline`` (``reranker_llm`` / ``hallucination_check``) into
``agent_runtime`` so the agent is a true *superset* of the RAG pipeline — with
two differences that fit the agent architecture:

* no dependency on ``rag_pipeline`` (operates on normalized shapes here);
* uses the *agent's own* LLM (``build_default_llm``), injectable for testing.

Both functions accept an optional ``llm`` that is either a chat model exposing
``.invoke(...)`` (returns a message with ``.content``) or a plain ``str -> str``
callable (handy in tests).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Union

LLMLike = Union[Any, Callable[[str], str]]


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------

def _content_to_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
            else:
                parts.append(str(getattr(part, "text", part)))
        return "".join(parts)
    return str(content or "")


def _invoke_llm(llm: Optional[LLMLike], prompt: str) -> str:
    if llm is None:
        from agent_runtime.executor_factory import build_default_llm

        llm = build_default_llm()
    if hasattr(llm, "invoke"):
        return _content_to_text(llm.invoke(prompt))
    if callable(llm):
        return str(llm(prompt))
    raise TypeError("llm must expose .invoke() or be a str->str callable")


def _normalize_document(entry: Any, index: int) -> Dict[str, Any]:
    """Coerce a document into ``{doc_id, title, contents}`` regardless of source."""
    if isinstance(entry, str):
        return {"doc_id": f"doc-{index}", "title": "Untitled", "contents": entry}
    if not isinstance(entry, dict):
        return {"doc_id": f"doc-{index}", "title": "Untitled", "contents": str(entry)}
    # Support both the agent's flat docs and the rag_pipeline {"document": {...}} shape.
    document = entry.get("document") if isinstance(entry.get("document"), dict) else entry
    meta = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    doc_id = str(
        document.get("doc_id")
        or entry.get("doc_id")
        or meta.get("hit_id")
        or f"doc-{index}"
    )
    title = str(document.get("title") or document.get("element_type") or "Untitled").strip()
    contents = str(document.get("contents") or document.get("snippet") or document.get("text") or "").strip()
    return {"doc_id": doc_id, "title": title, "contents": contents, "_entry": entry}


def _extract_json_object(text: str) -> Optional[Any]:
    if not text:
        return None
    if "```" in text:
        start = text.find("```")
        end = text.find("```", start + 3)
        if end != -1:
            candidate = text[start + 3 : end].strip()
            if candidate.lower().startswith("json"):
                candidate = candidate[4:].strip()
            try:
                return json.loads(candidate)
            except Exception:
                pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Rerank
# ---------------------------------------------------------------------------

def _rerank_prompt(query: str, docs: List[Dict[str, Any]]) -> str:
    items = "\n\n".join(
        f"{i+1}. doc_id={d['doc_id']}\ntitle={d['title']}\nexcerpt={(d['contents'] or 'No excerpt available.')[:400]}"
        for i, d in enumerate(docs)
    )
    return (
        "You are an expert relevance judge for a retrieval-augmented QA system.\n"
        "Re-rank the candidate documents by true semantic relevance to the query.\n"
        "Assign each a score in [0,1] with meaningful variance; reward documents that\n"
        "directly answer the query, penalize off-topic/redundant ones.\n"
        "Return JSON ONLY: {\"ranking\": [{\"doc_id\": \"<id>\", \"score\": <float>, "
        "\"reason\": \"<brief>\"}, ...]} including every doc_id exactly once.\n\n"
        f"User query:\n{query}\n\nCandidate documents:\n{items}\n"
    )


def rerank_documents(
    query: str,
    documents: List[Any],
    *,
    top_k: Optional[int] = None,
    llm: Optional[LLMLike] = None,
) -> List[Any]:
    """Reorder *documents* by LLM-judged relevance to *query*.

    Returns the original document objects, reordered (and truncated to ``top_k``).
    A failed/unparseable LLM call leaves the order unchanged. <=1 doc is a no-op.
    """
    if not isinstance(documents, list) or len(documents) <= 1 or not (query or "").strip():
        return documents

    normalized = [_normalize_document(entry, i) for i, entry in enumerate(documents)]
    by_id = {d["doc_id"]: d for d in normalized}

    try:
        raw = _invoke_llm(llm, _rerank_prompt(query, normalized))
        parsed = _extract_json_object(raw) or {}
        ranking = parsed.get("ranking") if isinstance(parsed, dict) else None
    except Exception:
        ranking = None
    if not isinstance(ranking, list) or not ranking:
        return documents

    ordered: List[Any] = []
    seen = set()
    for item in ranking:
        if not isinstance(item, dict):
            continue
        doc_id = str(item.get("doc_id") or "").strip()
        d = by_id.get(doc_id)
        if not d or doc_id in seen:
            continue
        seen.add(doc_id)
        ordered.append(d.get("_entry", d))
    # Append any documents the model omitted, preserving their original order.
    for d in normalized:
        if d["doc_id"] not in seen:
            ordered.append(d.get("_entry", d))

    if top_k is not None:
        ordered = ordered[:top_k]
    return ordered


# ---------------------------------------------------------------------------
# Grounding / hallucination audit
# ---------------------------------------------------------------------------

_AUDIT_PROMPT = (
    "You are auditing a retrieval-augmented answer.\n"
    "Given the question, the generated answer, and the supporting evidence, decide\n"
    "whether the answer contains hallucinations (claims not grounded in the evidence).\n"
    "Respond ONLY with JSON: {{\"hallucination_detected\": true|false, "
    "\"severity\": \"none\"|\"low\"|\"medium\"|\"high\", "
    "\"issues\": [{{\"claim\": \"...\", \"reason\": \"...\"}}], "
    "\"summary\": \"one sentence verdict\"}}\n"
    "Each issue must refer to a specific unsupported sentence.\n\n"
    "Question:\n{question}\n\nAnswer:\n{answer}\n\nEvidence:\n{evidence}\n"
)


def _format_evidence(evidence: Any, *, limit: int = 5, max_chars: int = 600) -> str:
    if isinstance(evidence, str):
        return evidence[: max_chars * limit].strip() or "(no evidence supplied)"
    if not isinstance(evidence, list) or not evidence:
        return "(no evidence supplied)"
    lines: List[str] = []
    for i, entry in enumerate(evidence[:limit]):
        d = _normalize_document(entry, i)
        lines.append(f"[{d['doc_id']}] {d['title']}\n{d['contents'][:max_chars]}")
    return "\n\n".join(lines) if lines else "(no evidence supplied)"


def _default_audit_verdict(summary: str, severity: str = "none") -> Dict[str, Any]:
    return {
        "hallucination_detected": False,
        "severity": severity,
        "issues": [],
        "summary": summary,
    }


def audit_answer_grounding(
    question: str,
    answer: str,
    evidence: Any,
    *,
    llm: Optional[LLMLike] = None,
    evidence_limit: int = 5,
    snippet_chars: int = 600,
) -> Dict[str, Any]:
    """Audit whether *answer* is grounded in *evidence*; returns a verdict dict.

    *evidence* may be a string, a list of docs, or a list of text snippets.
    Missing inputs return a benign "insufficient data" verdict (never raises).
    """
    if not (question or "").strip() or not (answer or "").strip() or not evidence:
        return _default_audit_verdict("Insufficient data to evaluate hallucinations.")

    prompt = _AUDIT_PROMPT.format(
        question=question,
        answer=answer,
        evidence=_format_evidence(evidence, limit=evidence_limit, max_chars=snippet_chars),
    )
    try:
        parsed = _extract_json_object(_invoke_llm(llm, prompt))
    except Exception:
        return _default_audit_verdict("Audit LLM call failed.", severity="unknown")
    if not isinstance(parsed, dict):
        return _default_audit_verdict("LLM response could not be parsed.", severity="unknown")
    parsed.setdefault("hallucination_detected", False)
    parsed.setdefault("severity", "none")
    parsed.setdefault("issues", [])
    parsed.setdefault("summary", "")
    return parsed


__all__ = ["rerank_documents", "audit_answer_grounding"]
