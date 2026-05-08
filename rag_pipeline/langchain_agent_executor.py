import sys

from agent_runtime import langchain_agent_executor as _module

sys.modules[__name__] = _module
