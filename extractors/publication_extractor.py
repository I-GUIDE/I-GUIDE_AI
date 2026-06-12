"""Publication methodological + provenance extractor (#3) — webhook (upload) path.

Reads a document (.tex/.txt/.md/.rst directly; .pdf via pypdf if available; .docx via
python-docx if available), then LLM-extracts the described method/workflow into
{summary, steps, datasets_referenced, tools_referenced, params}. Emits ONE
PublicationMethodSpec AssetRecord (index-only) + provenance edges
(DESCRIBES_METHOD, USES). NEVER executable.

The LLM step (rag_pipeline.llm_utils.call_llm) is OPTIONAL: with no LLM endpoint it
degrades to a text-excerpt method-spec + a note, so ingestion still succeeds offline.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import (
    EMIT_OPENSEARCH,
    KIND_PUBLICATION,
    AssetRecord,
    ExtractContext,
    Extractor,
    ExtractionResult,
    ProvenanceEdge,
)
from .doc_ids import publication_methodspec_doc_id, resource_type_for

_PROMPT = (
    "You extract the computational METHOD/WORKFLOW described in a scientific document.\n"
    "Return JSON ONLY with keys: summary (1-2 sentences), steps (ordered list of short "
    "strings), datasets_referenced (list), tools_referenced (list), params (object).\n"
    "If the document does not describe a method, return empty lists.\n\nDOCUMENT:\n"
)


def _read_text(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in {".tex", ".txt", ".md", ".rst"}:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore
            return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
        except Exception:
            return ""
    if ext in {".docx", ".doc"}:
        try:
            import docx  # type: ignore
            return "\n".join(p.text for p in docx.Document(path).paragraphs)
        except Exception:
            return ""
    return ""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    m = re.search(r"\{.*\}", text or "", flags=re.DOTALL)
    for cand in ([text.strip()] + ([m.group(0)] if m else [])):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def extract_method(text: str, *, max_chars: int = 12000) -> Dict[str, Any]:
    """LLM method extraction; degrades to a text-excerpt spec with a note offline."""
    if not text.strip():
        return {"summary": "", "steps": [], "datasets_referenced": [], "tools_referenced": [],
                "params": {}, "note": "no_text_extracted"}
    try:
        from rag_pipeline.llm_utils import call_llm
        raw = call_llm(_PROMPT + text[:max_chars])
        parsed = _extract_json(raw)
        if parsed:
            parsed.setdefault("steps", []); parsed.setdefault("datasets_referenced", [])
            parsed.setdefault("tools_referenced", []); parsed.setdefault("params", {})
            parsed.setdefault("summary", "")
            return parsed
        return {"summary": text[:500].strip(), "steps": [], "datasets_referenced": [],
                "tools_referenced": [], "params": {}, "note": "llm_unparseable"}
    except Exception as exc:
        return {"summary": text[:500].strip(), "steps": [], "datasets_referenced": [],
                "tools_referenced": [], "params": {}, "note": f"llm_skipped: {type(exc).__name__}"}


class PublicationExtractor:
    name = "publication"

    def extract(self, path: str, *, ctx: ExtractContext) -> ExtractionResult:
        fname = os.path.basename(path)
        anchor = ctx.anchor() or fname
        f = ctx.fields or {}
        title = str(f.get("title") or fname)

        text = _read_text(path)
        method = extract_method(text)

        doc_id = publication_methodspec_doc_id(anchor)
        steps = method.get("steps") or []
        body = method.get("summary") or ""
        if steps:
            body += "\n\nSteps:\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
        contents = f"{title}\n{body}".strip()

        source_fields = {k: f[k] for k in ("authors", "contributor", "abstract", "tags", "license", "doi")
                         if f.get(k)}
        asset = AssetRecord(
            asset_id=doc_id, kind=KIND_PUBLICATION, resource_type=resource_type_for(KIND_PUBLICATION),
            doc_id=doc_id, emit_targets=[EMIT_OPENSEARCH], source_rel_path=fname, title=title,
            contents=contents, source_fields=source_fields,
            extracted={"steps": steps, "datasets_referenced": method.get("datasets_referenced") or [],
                       "tools_referenced": method.get("tools_referenced") or [],
                       "params": method.get("params") or {}, "note": method.get("note"),
                       "parent_type": "Publication", "parent_title": title},
        )
        edges: List[ProvenanceEdge] = [
            ProvenanceEdge(src=anchor, rel="DESCRIBES_METHOD", dst=doc_id)
        ]
        for ds in (method.get("datasets_referenced") or []):
            edges.append(ProvenanceEdge(src=doc_id, rel="USES", dst=str(ds),
                                        detail={"confidence": "low", "by": "name_match"}))
        warnings = [f"publication: {method['note']}"] if method.get("note") else []
        return ExtractionResult(assets=[asset], edges=edges, warnings=warnings)


_: Extractor = PublicationExtractor()  # type: ignore[assignment]

__all__ = ["PublicationExtractor", "extract_method"]
