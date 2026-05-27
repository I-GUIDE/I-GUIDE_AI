"""Vendored TelecouplingAI toolbox (InVEST + telecoupling models).

This package is a namespaced copy of the TelecouplingAI ``backend`` tool code
(``tools/``, ``shared/``, ``renderers/``, ``r_scripts/``).  Heavy scientific
dependencies (``natcap.invest``, R, GDAL/geopandas, QGIS) are imported lazily
inside each tool, so importing this package never requires them to be present.

Tools are surfaced to the i-GUIDE LangGraph analysis agent as LangChain
StructuredTools by ``agent_runtime.langchain_telecoupling_tools``.
"""
