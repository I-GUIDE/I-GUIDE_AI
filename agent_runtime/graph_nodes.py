"""Back-compat alias — implementation moved to ``agent_runtime.legacy.graph_nodes``.

The legacy agent-as-tools graph wiring (collect_orchestration_tools, make_*_tool) now lives
in the ``agent_runtime.legacy`` package. This module *is* that module (``sys.modules``
aliasing), so existing imports (``import agent_runtime.graph_nodes as gn``) and monkeypatch
targets keep working. New code should import from ``agent_runtime.legacy.graph_nodes``.
"""
import sys as _sys
from agent_runtime.legacy import graph_nodes as _graph_nodes
_sys.modules[__name__] = _graph_nodes
