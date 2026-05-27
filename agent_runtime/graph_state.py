"""Shared type definitions and constants for the agent runtime.

Every other module in ``agent_runtime`` may import from here.
Nothing in this file should import from ``rag_pipeline`` so the
dependency direction stays clean.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Sequence

# ---------------------------------------------------------------------------
# Intent / role / route literals
# ---------------------------------------------------------------------------
AgentIntent = Literal["general_discovery", "analysis_task", "code_task", "hybrid"]
AgentRole = Literal["search", "analysis", "code", "direct_answer", "orchestrator"]
RouteType = Literal["search", "analysis", "code", "direct_answer"]

# ---------------------------------------------------------------------------
# Tool-name sets
#
# These are the *canonical* names that tool_policy and intent_classifier
# use to decide which tools an agent is allowed to call.  If a new MCP
# tool is added, register it in the appropriate set here.
# ---------------------------------------------------------------------------
# Telecoupling Toolbox (vendored InVEST + telecoupling models).  Mirrors the
# tool names in agent_runtime/telecoupling/tools_spec.json.  These are analysis
# tools, made available to the analysis agent only when the toolbox toggle is on.
TELECOUPLING_TOOL_NAMES: set[str] = {
    "run_network_analysis_grouping",
    "run_coastal_blue_carbon_preprocessor",
    "run_coastal_blue_carbon",
    "run_seasonal_water_yield",
    "run_crop_production_percentile",
    "run_crop_production_regression",
    "run_carbon_storage",
    "run_habitat_quality",
    "run_annual_water_yield",
    "run_forest_carbon_edge_effect",
    "run_crop_pollination",
    "run_delineateit",
    "run_routedem",
    "run_Sediment_Delivery_Ratio_SDR",
    "run_ndr",
    "run_urban_cooling",
    "run_urban_flood_risk_mitigation",
    "run_urban_stormwater_retention",
    "run_urban_nature_access",
    "run_urban_mental_health",
    "run_scenic_quality",
    "run_habitat_risk_assessment",
    "run_wave_energy_production",
    "run_scenario_gen_proximity",
    "run_coastal_vulnerability",
    "run_offshore_wind_energy",
    "run_recreation_tourism",
    "run_model_selection_ols",
    "run_factor_analysis_mixed_data",
    "run_co2_emissions",
    "run_cost_benefit_analysis",
    "run_population_count_density",
    "run_draw_radial_flows",
    "run_commodity_trade",
    "run_add_agents_interactively",
    "run_draw_agents_from_table",
    "run_add_causes_interactively",
    "run_add_systems_interactively",
    "run_draw_systems_from_table",
    "run_add_media_flows",
    "run_food_security",
    "run_nutrition_metrics",
    "read_file_content",
    "render_spatial_file",
}

ANALYSIS_TOOL_NAMES: set[str] = {
    "mcp_load_chicago_community_areas",
    "mcp_load_chicago_crime_data",
    "mcp_get_crime_statistics",
    "mcp_count_crimes_per_community",
    "mcp_generate_crime_map",
    "qgis_processing_help",
    "qgis_processing_run",
    "qgis_metric_buffer",
    "pyqgis_layer_summary",
    "pyqgis_render_map",
} | TELECOUPLING_TOOL_NAMES

DISCOVERY_TOOL_NAMES: set[str] = {
    "rag_tool",
    "mcp_search_geospatial_resources",
    "mcp_search_publications",
}

RAG_COMPONENT_TOOL_NAMES: set[str] = {
    "keyword_search",
    "semantic_search",
    "neo4j_search",
    "neo4j_get_element_by_id",
    "neo4j_explore_related_nodes",
    "spatial_search",
    "opengeodata_search",
}

FILE_TOOL_NAMES: set[str] = {
    "read_text_file",
    "inspect_file_for_analysis",
    "write_text_file",
    "write_output_file",
}

SKILL_TOOL_NAMES: set[str] = {
    "list_available_skills",
    "load_skill",
}

# Alias kept for backward compatibility — identical to RAG_COMPONENT_TOOL_NAMES.
IGUIDE_SEARCH_TOOL_NAMES: set[str] = RAG_COMPONENT_TOOL_NAMES.copy()
