"""Back-compat alias — implementation moved to ``agent_runtime.supervisor.evidence_subgraph``.

This module *is* that module (``sys.modules`` aliasing); existing imports and monkeypatch
targets keep working. New code should import from ``agent_runtime.supervisor.evidence_subgraph``.
"""
import sys as _sys
from agent_runtime.supervisor import evidence_subgraph as _es
_sys.modules[__name__] = _es
