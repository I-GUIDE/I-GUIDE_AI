"""Lazy name -> implementation map for the vendored Telecoupling toolbox.

Mirrors the original TelecouplingAI ``workers/task_queue.execute_tool`` dispatch
table, but resolves each tool's coroutine *lazily* (per call).  This keeps heavy
scientific dependencies (``natcap.invest``, R, GDAL, geopandas, PyQGIS) out of
import time: a tool whose dependency is missing raises ``ImportError`` only when
that specific tool is invoked, which the LangChain wrapper turns into a clean
"dependency not installed" message instead of crashing the whole agent.
"""
from __future__ import annotations

import importlib
from typing import Awaitable, Callable, Dict, Tuple

# tool name (as the agent calls it) -> (module path, coroutine function name)
TOOL_FUNCTIONS: Dict[str, Tuple[str, str]] = {
    "run_network_analysis_grouping":        ("agent_runtime.telecoupling.tools.network_analysis", "run_network_analysis"),
    "run_coastal_blue_carbon_preprocessor": ("agent_runtime.telecoupling.tools.cbc_preprocessor", "run_cbc_preprocessor"),
    "run_coastal_blue_carbon":              ("agent_runtime.telecoupling.tools.cbc_main", "run_cbc_main"),
    "run_seasonal_water_yield":             ("agent_runtime.telecoupling.tools.seasonal_water_yield", "run_seasonal_water_yield"),
    "run_crop_production_percentile":       ("agent_runtime.telecoupling.tools.crop_percentile", "run_crop_percentile"),
    "run_crop_production_regression":       ("agent_runtime.telecoupling.tools.crop_regression", "run_crop_regression"),
    "render_spatial_file":                  ("agent_runtime.telecoupling.tools.render_tif", "run_render_tif"),
    "read_file_content":                    ("agent_runtime.telecoupling.tools.read_file", "run_read_file"),
    "run_carbon_storage":                   ("agent_runtime.telecoupling.tools.carbon", "run_carbon"),
    "run_habitat_quality":                  ("agent_runtime.telecoupling.tools.habitat_quality", "run_habitat_quality"),
    "run_annual_water_yield":               ("agent_runtime.telecoupling.tools.annual_water_yield", "run_annual_water_yield"),
    "run_forest_carbon_edge_effect":        ("agent_runtime.telecoupling.tools.forest_carbon_edge_effect", "run_forest_carbon_edge"),
    "run_crop_pollination":                 ("agent_runtime.telecoupling.tools.pollination", "run_pollination"),
    "run_delineateit":                      ("agent_runtime.telecoupling.tools.delineateit", "run_delineateit"),
    "run_routedem":                         ("agent_runtime.telecoupling.tools.routedem", "run_routedem"),
    "run_Sediment_Delivery_Ratio_SDR":      ("agent_runtime.telecoupling.tools.sdr", "run_sdr"),
    "run_ndr":                              ("agent_runtime.telecoupling.tools.ndr", "run_ndr"),
    "run_urban_cooling":                    ("agent_runtime.telecoupling.tools.urban_cooling", "run_urban_cooling"),
    "run_urban_flood_risk_mitigation":      ("agent_runtime.telecoupling.tools.urban_flood", "run_urban_flood"),
    "run_urban_stormwater_retention":       ("agent_runtime.telecoupling.tools.urban_stormwater", "run_urban_stormwater"),
    "run_urban_nature_access":              ("agent_runtime.telecoupling.tools.urban_nature_access", "run_urban_nature_access"),
    "run_urban_mental_health":              ("agent_runtime.telecoupling.tools.urban_mental_health", "run_urban_mental_health"),
    "run_scenic_quality":                   ("agent_runtime.telecoupling.tools.scenic_quality", "run_scenic_quality"),
    "run_habitat_risk_assessment":          ("agent_runtime.telecoupling.tools.hra", "run_hra"),
    "run_wave_energy_production":           ("agent_runtime.telecoupling.tools.wave_energy", "run_wave_energy"),
    "run_scenario_gen_proximity":           ("agent_runtime.telecoupling.tools.scenario_gen_proximity", "run_scenario_gen_proximity"),
    "run_coastal_vulnerability":            ("agent_runtime.telecoupling.tools.coastal_vulnerability", "run_coastal_vulnerability"),
    "run_offshore_wind_energy":             ("agent_runtime.telecoupling.tools.wind_energy", "run_offshore_wind_energy"),
    "run_recreation_tourism":               ("agent_runtime.telecoupling.tools.recreation", "run_recreation"),
    "run_model_selection_ols":              ("agent_runtime.telecoupling.tools.ols", "run_ols"),
    "run_factor_analysis_mixed_data":       ("agent_runtime.telecoupling.tools.famd", "run_factor_analysis_mixed_data"),
    "run_co2_emissions":                    ("agent_runtime.telecoupling.tools.co2_emissions", "run_co2_emissions"),
    "run_cost_benefit_analysis":            ("agent_runtime.telecoupling.tools.cost_benefit_analysis", "run_cost_benefit_analysis"),
    "run_population_count_density":         ("agent_runtime.telecoupling.tools.population_density", "run_population_count_density"),
    "run_draw_radial_flows":                ("agent_runtime.telecoupling.tools.radial_flows", "run_draw_radial_flows"),
    "run_commodity_trade":                  ("agent_runtime.telecoupling.tools.commodity_trade", "run_commodity_trade"),
    "run_add_agents_interactively":         ("agent_runtime.telecoupling.tools.add_agents", "run_add_agents_interactively"),
    "run_draw_agents_from_table":           ("agent_runtime.telecoupling.tools.draw_agents_table", "run_draw_agents_from_table"),
    "run_add_causes_interactively":         ("agent_runtime.telecoupling.tools.add_causes", "run_add_causes_interactively"),
    "run_add_systems_interactively":        ("agent_runtime.telecoupling.tools.add_systems", "run_add_systems_interactively"),
    "run_draw_systems_from_table":          ("agent_runtime.telecoupling.tools.draw_systems_table", "run_draw_systems_from_table"),
    "run_add_media_flows":                  ("agent_runtime.telecoupling.tools.add_media_flows", "run_add_media_flows"),
    "run_food_security":                    ("agent_runtime.telecoupling.tools.food_security", "run_food_security"),
    "run_nutrition_metrics":                ("agent_runtime.telecoupling.tools.nutrition_metrics", "run_nutrition_metrics"),
}


def resolve_tool(name: str) -> Callable[..., Awaitable[dict]]:
    """Import and return the coroutine implementing *name*.

    Raises ``KeyError`` for unknown tools and ``ImportError`` when the tool's
    scientific dependencies are not installed in this environment.
    """
    if name not in TOOL_FUNCTIONS:
        raise KeyError(name)
    module_path, func_name = TOOL_FUNCTIONS[name]
    module = importlib.import_module(module_path)
    return getattr(module, func_name)


__all__ = ["TOOL_FUNCTIONS", "resolve_tool"]
