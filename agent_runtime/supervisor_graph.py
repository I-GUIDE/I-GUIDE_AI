"""Back-compat alias — implementation moved to ``agent_runtime.supervisor.graph``.

This module *is* that module (``sys.modules`` aliasing), so existing imports
(``import agent_runtime.supervisor_graph as sg``) and monkeypatch targets keep working.
New code should import from ``agent_runtime.supervisor.graph``.
"""
import sys as _sys
from agent_runtime.supervisor import graph as _graph
_sys.modules[__name__] = _graph
