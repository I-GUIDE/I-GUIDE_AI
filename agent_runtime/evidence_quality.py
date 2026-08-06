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
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

LLMLike = Union[Any, Callable[[str], str]]

logger = logging.getLogger(__name__)


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


# Reciprocal Rank Fusion constant. 60 is the value from the original TREC work and the de facto
# default; it damps the gap between adjacent top ranks so one source cannot dominate on position 1
# alone. Not sensitive enough to be worth tuning here.
_RRF_K = 60


def _doc_source(entry: Any) -> str:
    """Which retrieval method produced this doc, for per-source ranking."""
    if not isinstance(entry, dict):
        return "unknown"
    document = entry.get("document") if isinstance(entry.get("document"), dict) else entry
    return str(document.get("source") or document.get("source_system") or "unknown").strip().lower()


def _doc_score(entry: Any) -> float:
    """The doc's score AS REPORTED BY ITS OWN SOURCE.

    Only ever compared against scores from the SAME source: BM25 relevance (~4-9) and the catalog
    scorer's own scale (~0-1) are not commensurable, which is the whole reason this module fuses
    ranks rather than values.
    """
    if not isinstance(entry, dict):
        return 0.0
    document = entry.get("document") if isinstance(entry.get("document"), dict) else entry
    try:
        return float(document.get("score", entry.get("score", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def rrf_order(documents: List[Any], *, k: int = _RRF_K) -> List[Any]:
    """Order documents across sources by Reciprocal Rank Fusion.

    Each source is ranked internally by its own score — valid, because a score is comparable within
    the source that produced it — and the fused score is ``sum(1 / (k + rank))`` over the sources a
    document appears in. Only RANKS cross the source boundary, never score magnitudes, so a BM25
    8.58 and a catalog 1.0 no longer decide the order by scale.

    This is a fallback for when LLM rerank is unavailable, and it fuses on retrieval position only:
    it cannot judge topical fitness, so a hit that ranks high within its own source stays high here
    even if it is off-topic. That judgement is the reranker's job.
    """
    if not isinstance(documents, list) or len(documents) <= 1:
        return documents

    by_source: Dict[str, List[Tuple[int, Any]]] = {}
    for index, entry in enumerate(documents):
        by_source.setdefault(_doc_source(entry), []).append((index, entry))

    # index -> fused score. Keyed by position so unhashable/duplicate entries are still distinct.
    fused: Dict[int, float] = {index: 0.0 for index in range(len(documents))}
    for group in by_source.values():
        # Sort by the source's own score, keeping the original order as the tie-break so a source
        # that reports no scores at all retains the order it returned.
        ranked = sorted(group, key=lambda pair: (-_doc_score(pair[1]), pair[0]))
        for rank, (index, _entry) in enumerate(ranked, start=1):
            fused[index] += 1.0 / (k + rank)

    order = sorted(range(len(documents)), key=lambda i: (-fused[i], i))
    return [documents[i] for i in order]


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

    failure = ""
    try:
        raw = _invoke_llm(llm, _rerank_prompt(query, normalized))
        parsed = _extract_json_object(raw) or {}
        ranking = parsed.get("ranking") if isinstance(parsed, dict) else None
        if not isinstance(ranking, list) or not ranking:
            failure = "the model returned no usable ranking"
    except Exception as exc:
        ranking = None
        failure = f"{type(exc).__name__}: {exc}"

    if failure:
        # Degrading SILENTLY here was the real defect: the caller got the merged sweep order, which
        # is arbitrary across sources, AND kept every document because top_k was only applied on
        # the success path. Fall back to a deterministic rank fusion, still truncate, and say so.
        logger.warning(
            "Evidence rerank unavailable (%s); falling back to reciprocal rank fusion over %d docs",
            failure, len(documents),
        )
        fused = rrf_order(documents)
        return fused[:top_k] if top_k is not None else fused

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
    "You are auditing an answer produced by a TOOL-USING agent.\n"
    "You are given the question, the generated answer, the retrieved evidence, AND the\n"
    "agent's EXECUTION RECORD (the tools it actually ran and the artifacts it produced).\n"
    "Decide whether the answer contains hallucinations: claims grounded in NEITHER the\n"
    "retrieved evidence NOR the execution record.\n"
    "IMPORTANT: the execution record is FIRST-CLASS grounding. If the agent's tools\n"
    "actually produced a result or artifact — a generated map/plot/image file, a computed\n"
    "count, a filtered dataset, a written file_id — then an answer that presents that\n"
    "result is GROUNDED, even if no retrieved document mentions it. Do NOT flag an answer\n"
    "for describing outputs the tools genuinely produced or for offering to do so.\n"
    "PRECISION — avoid false positives: do NOT flag reasonable, non-contentious "
    "contextualization or domain framing that a reader would accept as a fair description of "
    "what the evidence/results cover (e.g. noting a heat-exposure dataset is 'useful for public "
    "health or urban planning'). Flag ONLY: (a) statements that CONTRADICT the evidence or "
    "execution record, or (b) specific, checkable facts asserted as true — concrete "
    "numbers/statistics, capabilities, results, dates, names, or citations — that appear in "
    "NEITHER. Severity: 'high' = a clear factual fabrication or contradiction; 'low' = minor "
    "over-reach; 'none' = grounded or merely interpretive framing. Reserve 'high' for real "
    "factual problems, never for reasonable elaboration.\n"
    "Respond ONLY with JSON: {{\"hallucination_detected\": true|false, "
    "\"severity\": \"none\"|\"low\"|\"medium\"|\"high\", "
    "\"issues\": [{{\"claim\": \"...\", \"reason\": \"...\"}}], "
    "\"summary\": \"one sentence verdict\"}}\n"
    "Each issue must refer to a specific unsupported sentence.\n\n"
    "Question:\n{question}\n\nAnswer:\n{answer}\n\n"
    "Retrieved evidence:\n{evidence}\n\nExecution record:\n{execution}\n"
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


def _format_execution_context(execution_context: Any, *, max_chars: int = 2200) -> str:
    """Render the agent's execution outcomes (tool calls/results, produced artifacts) so
    the auditor can treat genuinely-produced outputs as grounding."""
    if not execution_context:
        return "(no tools were executed)"
    if isinstance(execution_context, str):
        return execution_context[:max_chars]
    parts: List[str] = []
    if isinstance(execution_context, dict):
        for key in ("analysis_results", "code_result"):
            val = execution_context.get(key)
            if val:
                parts.append(f"{key}: {json.dumps(val, default=str)[:max_chars]}")
        arts = execution_context.get("artifacts")
        if arts:
            parts.append(f"produced artifacts (files/images generated by tools): "
                         f"{json.dumps(arts, default=str)[:max_chars]}")
    else:
        parts.append(json.dumps(execution_context, default=str)[:max_chars])
    return "\n".join(parts) if parts else "(no tools were executed)"


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
    execution_context: Any = None,
    evidence_limit: int = 5,
    snippet_chars: int = 600,
) -> Dict[str, Any]:
    """Audit whether *answer* is grounded in *evidence* and/or the agent's
    *execution_context* (tool outputs + produced artifacts); returns a verdict dict.

    *evidence* may be a string, a list of docs, or a list of text snippets.
    *execution_context* (optional) carries what the agent's tools actually produced, so
    answers that present genuinely-generated artifacts are not falsely flagged.
    Missing inputs return a benign "insufficient data" verdict (never raises).
    """
    if not (question or "").strip() or not (answer or "").strip() or (not evidence and not execution_context):
        return _default_audit_verdict("Insufficient data to evaluate hallucinations.")

    prompt = _AUDIT_PROMPT.format(
        question=question,
        answer=answer,
        evidence=_format_evidence(evidence, limit=evidence_limit, max_chars=snippet_chars),
        execution=_format_execution_context(execution_context),
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
