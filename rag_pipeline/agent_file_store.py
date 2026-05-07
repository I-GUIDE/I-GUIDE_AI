import sys

from agent_runtime import file_store as _module

sys.modules[__name__] = _module
