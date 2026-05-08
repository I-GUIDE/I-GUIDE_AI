"""In-memory trace storage and JSON export for agent execution traces."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class StoredTrace:
    trace_id: str
    query: Optional[str]
    events: List[Dict[str, Any]]
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "query": self.query,
            "events": self.events,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "StoredTrace":
        return cls(
            trace_id=str(payload.get("trace_id") or ""),
            query=payload.get("query"),
            events=list(payload.get("events") or []),
            metadata=dict(payload.get("metadata") or {}),
            created_at=str(payload.get("created_at") or datetime.now(timezone.utc).isoformat()),
        )


class TraceStore:
    def __init__(self, max_size: int = 100) -> None:
        self._max_size = max_size
        self._traces: deque[StoredTrace] = deque(maxlen=max_size)
        self._index: Dict[str, StoredTrace] = {}

    def add_trace(
        self,
        trace_id: str,
        query: Optional[str],
        events: Iterable[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StoredTrace:
        metadata = dict(metadata or {})
        trace = StoredTrace(trace_id=trace_id, query=query, events=list(events), metadata=metadata)
        if trace_id in self._index:
            self._traces.remove(self._index[trace_id])
        self._traces.append(trace)
        self._index[trace_id] = trace
        return trace

    def get_trace(self, trace_id: str) -> Optional[StoredTrace]:
        return self._index.get(trace_id)

    def list_traces(self) -> List[StoredTrace]:
        return list(self._traces)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "traces": [trace.to_dict() for trace in self._traces],
        }

    def save_to_file(self, path: str) -> None:
        payload = self.to_dict()
        with Path(path).expanduser().open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

    @classmethod
    def load_from_file(cls, path: str) -> "TraceStore":
        raw = Path(path).expanduser().read_text(encoding="utf-8")
        payload = json.loads(raw)
        store = cls()
        for item in payload.get("traces") or []:
            trace = StoredTrace.from_dict(item)
            store._traces.append(trace)
            store._index[trace.trace_id] = trace
        return store

    def export_trace(self, trace_id: str, path: str) -> None:
        trace = self.get_trace(trace_id)
        if trace is None:
            raise KeyError(f"Trace {trace_id} not found")
        with Path(path).expanduser().open("w", encoding="utf-8") as handle:
            json.dump(trace.to_dict(), handle, indent=2, ensure_ascii=False)

    @classmethod
    def load_trace_file(cls, path: str) -> StoredTrace:
        raw = Path(path).expanduser().read_text(encoding="utf-8")
        payload = json.loads(raw)
        return StoredTrace.from_dict(payload)
