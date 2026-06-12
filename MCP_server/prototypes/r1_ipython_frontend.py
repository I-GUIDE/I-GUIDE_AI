"""Backward-compat shim. The R1 IPython-aware notebook front end now lives at
``extractors/r1_ipython_frontend.py`` (it graduated from prototype to the
supported extractor package). This module re-exports it so existing imports
(``import r1_ipython_frontend`` / ``MCP_server.prototypes.r1_ipython_frontend``)
keep working. Import from ``extractors.r1_ipython_frontend`` in new code.
"""

import sys

from extractors import r1_ipython_frontend as _module

sys.modules[__name__] = _module
