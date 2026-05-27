"""
Output router — classify tool output files and decide render type.
SHP/TIF files are classified as 'qgis' but returned as 'download' only.
Preview generation is triggered on-demand via render_spatial_file tool, not automatically.
"""
from __future__ import annotations
import fnmatch
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Directories to always skip during output scanning
# Covers various InVEST naming conventions for intermediate/cache directories
SKIP_DIRS = {
    "intermediate_outputs",   # SWY, CBC Preprocessor etc.
    "intermediate_output",    # Crop Percentile, Crop Regression
    "intermediate",           # CBC Main
    "taskgraph_cache",        # All InVEST models
}

PATTERNS: dict[str, dict[str, list[str]]] = {
    "seasonal_water_yield": {
        "qgis": ["QF_*.tif", "B_*.tif", "L_avail_*.tif", "L_*.tif", "P_*.tif",
                 "aggregated_results_swy_*.shp"],
        "csv": [],
    },
    "coastal_blue_carbon_preprocessor": {
        "qgis": ["aligned_lulc_*.tif"],
        "csv": ["transitions_*.csv", "carbon_pool_transient_template_*.csv"],
    },
    "coastal_blue_carbon": {
        "qgis": ["carbon-stock-at-*.tif", "carbon-accumulation-*.tif",
                 "carbon-emissions-*.tif", "total-net-carbon-sequestration*.tif",
                 "net-present-value*.tif"],
        "csv": [],
    },
    "crop_production_percentile": {
        "qgis": ["*_yield_*percentile_*.tif", "*_yield_*production*.tif"],
        "csv": ["result_table_*.csv", "result_table.csv",
                "aggregate_results_*.csv", "aggregate_results.csv"],
    },
    "crop_production_regression": {
        "qgis": ["*_regression_production_*.tif"],
        "csv": ["result_table_*.csv", "result_table.csv",
                "aggregate_results_*.csv", "aggregate_results.csv"],
    },
    "network_analysis": {
        "qgis": ["output_*.shp"],
        "csv": ["network_stats_*.csv"],
        "download": ["network_plot_*.pdf"],
    },
    "carbon": {
        "qgis": ["tot_c_cur*.tif", "tot_c_fut*.tif", "tot_c_redd*.tif",
                 "delta_cur_fut*.tif", "delta_cur_redd*.tif",
                 "npv_fut*.tif", "npv_redd*.tif",
                 "c_above_*.tif", "c_below_*.tif", "c_soil_*.tif", "c_dead_*.tif"],
        "download": ["carbon_results*.html"],
        "csv": [],
    },
    "habitat_quality": {
        "qgis": ["deg_sum_c*.tif", "deg_sum_f*.tif", "deg_sum_b*.tif",
                 "quality_c*.tif", "quality_f*.tif", "quality_b*.tif",
                 "rarity_c*.tif", "rarity_f*.tif"],
        "csv": [],
    },
    "annual_water_yield": {
        "qgis": ["per_pixel_aet*.tif", "per_pixel_wyield*.tif"],
        "csv": ["watershed_results_wyield*.csv", "subwatershed_results_wyield*.csv"],
        "download": ["watershed_results_wyield*.shp", "subwatershed_results_wyield*.shp"],
    },
    "forest_carbon_edge_effect": {
        "qgis": ["tropical_forest_edge_carbon_stocks*.tif",
                 "biomass_per_pixel*.tif", "carbon_map*.tif"],
        "csv": ["aggregated_carbon_stocks*.csv"],
    },
    "pollination": {
        "qgis": ["pollinator_abundance_*.tif", "total_pollinator_abundance*.tif",
                 "pollinator_supply_*.tif"],
        "csv": ["farm_results*.csv"],
    },
    "delineateit": {
        "qgis": ["watersheds*.gpkg", "watersheds*.shp",
                 "snapped_outlets*.gpkg", "snapped_outlets*.shp",
                 "detected_pour_points*.gpkg"],
        "csv": [],
    },
    "routedem": {
        "qgis": ["flow_direction*.tif", "flow_accumulation*.tif",
                 "slope*.tif", "stream*.tif", "stream_order*.tif",
                 "downstream_distance*.tif"],
        "csv": [],
    },
    "sdr": {
        "qgis": ["rkls*.tif", "usle*.tif", "sed_deposition*.tif",
                 "sed_export*.tif", "avoided_export*.tif",
                 "avoided_erosion*.tif", "stream*.tif", "e_prime*.tif",
                 "ic*.tif", "sdr_factor*.tif", "watershed_results_sdr*.shp"],
        "csv": ["watershed_results_sdr*.csv"],
    },
    "ndr": {
        "qgis": ["n_export*.tif", "p_export*.tif",
                 "n_surface_export*.tif", "n_subsurface_export*.tif",
                 "p_surface_export*.tif", "runoff_proxy_index*.tif",
                 "watershed_results_ndr*.shp"],
        "csv": ["watershed_results_ndr*.csv"],
    },
    "urban_cooling": {
        "qgis": ["uhi_results_*.tif", "hm_*.tif", "cc_*.tif",
                 "T_air*.tif", "T_air_nomix*.tif", "uhi_index*.tif"],
        "csv": ["uhi_results_*.csv"],
        "download": ["uhi_results_*.html"],
    },
    "urban_flood": {
        "qgis": ["Runoff_retention_m3*.tif", "Runoff_retention*.tif",
                 "Q_mm*.tif", "flood_risk_service*.tif"],
        "csv": ["flood_risk_service*.csv"],
    },
    "urban_stormwater": {
        "qgis": ["retention_ratio*.tif", "runoff_ratio*.tif",
                 "retention_volume*.tif", "runoff_volume*.tif",
                 "infiltration_ratio*.tif", "infiltration_volume*.tif",
                 "avoided_pollutant_load_*.tif", "pollutant_load_*.tif"],
        "csv": ["aggregate_results*.csv"],
    },
    "urban_nature_access": {
        "qgis": ["accessible_urban_nature*.tif", "urban_nature_supply_percapita*.tif",
                 "urban_nature_balance_percapita*.tif",
                 "urban_nature_balance_totalpop*.tif"],
        "csv": ["admin_boundaries*.csv"],
        "download": ["admin_boundaries*.gpkg"],
    },
    "urban_mental_health": {
        "qgis": ["accessible_urban_nature*.tif", "urban_nature_supply_percapita*.tif",
                 "urban_nature_balance_percapita*.tif",
                 "urban_nature_balance_totalpop*.tif"],
        "csv": ["admin_boundaries*.csv"],
        "download": ["admin_boundaries*.gpkg"],
    },
    "scenario_gen_proximity": {
        "qgis": ["nearest_to_edge*.tif", "farthest_from_edge*.tif"],
        "csv": ["scenario_proximity_stats*.csv"],
    },
    "scenic_quality": {
        "qgis": ["vshed*.tif", "vshed_qual*.tif", "viewshed*.tif"],
        "csv": ["vshed_stats*.csv"],
    },
    "hra": {
        "qgis": ["RECLASS_RISK_*.tif", "RISK_*.tif", "EXPOSURE_*.tif",
                 "CONSEQUENCE_*.tif", "ECOSYSTEM_RISK*.tif"],
        "csv": ["SUMMARY_STATISTICS*.csv"],
        "download": ["RECLASS_RISK_*.geojson", "RISK_*.geojson"],
    },
    "wave_energy": {
        "qgis": ["wp_kw*.tif", "capwe_mwh*.tif", "npv_usd*.tif",
                 "wp_rc*.tif", "capwe_rc*.tif", "npv_rc*.tif"],
        "csv": ["capwe_mwh*.csv", "wp_kw*.csv"],
    },
    "coastal_vulnerability": {
        "qgis": ["coastal_exposure*.gpkg", "coastal_exposure*.shp"],
        "download": ["coastal_vulnerability_report*.html"],
        "csv": [],
    },
    "wind_energy": {
        "qgis": ["wind_energy_points*.shp", "wind_energy_points*.gpkg",
                 "harvested_energy_MWhr_per_yr*.tif",
                 "carbon_emissions_tons*.tif"],
        "csv": [],
    },
    "ols": {
        "csv": ["ols_coefficients.csv", "ols_diagnostics.csv", "ols_residuals.csv",
                "model_selection_results.csv"],
        "download": [],
    },
    "famd": {
        "csv": ["famd_eigenvalues.csv", "famd_individual_coordinates.csv"],
        "download": ["famd_plots.pdf"],
    },
    "co2_emissions": {
        "csv": ["co2_emissions_results.csv", "co2_emissions_summary.csv"],
    },
    "cost_benefit_analysis": {
        "csv": ["cba_results.csv", "cba_summary.csv"],
    },
    "population_density": {
        "csv": ["population_density_results.csv", "population_density_summary.csv"],
    },
    "radial_flows": {
        "qgis": ["radial_flows.shp"],
        "csv": ["radial_flows_summary.csv"],
        "download": ["radial_flows.geojson"],
    },
    "commodity_trade": {
        "qgis": ["commodity_trade_flows.shp"],
        "csv": ["commodity_trade_summary.csv"],
        "download": ["commodity_trade_flows.geojson"],
    },
    "add_agents": {
        "qgis": ["agents.shp"],
        "csv": ["agents_table.csv"],
        "download": ["agents.geojson"],
    },
    "draw_agents_table": {
        "qgis": ["agents_from_table.shp"],
        "csv": ["agents_from_table.csv"],
        "download": ["agents_from_table.geojson"],
    },
    "add_causes": {
        "qgis": ["causes.shp"],
        "csv": ["causes_table.csv"],
        "download": ["causes.geojson"],
    },
    "add_systems": {
        "qgis": ["systems.shp"],
        "csv": ["systems_table.csv"],
        "download": ["systems.geojson"],
    },
    "draw_systems_table": {
        "qgis": ["systems_from_table.shp"],
        "csv": ["systems_from_table.csv"],
        "download": ["systems_from_table.geojson"],
    },
    "add_media_flows": {
        "qgis": ["media_flows.shp"],
        "csv": ["media_mention_frequency.csv"],
        "download": ["media_flows.geojson"],
    },
    "food_security": {
        "csv": ["food_security_data.csv", "food_security_pivot.csv"],
        "image": ["food_security_trend.png", "food_security_comparison.png"],
    },
    "nutrition_metrics": {
        "csv": ["nutrition_metrics_results.csv", "nutrition_metrics_summary.csv"],
        "image": ["nutrition_ller_chart.png"],
    },
    "recreation": {
        "qgis": ["pud_results*.shp", "pud_results*.gpkg",
                 "tud_results*.shp", "tud_results*.gpkg",
                 "regression_data*.shp", "regression_data*.gpkg",
                 "scenario_results*.shp", "scenario_results*.gpkg"],
        "csv": ["monthly_table*.csv", "PUD_monthly_table*.csv",
                "TUD_monthly_table*.csv", "regression_coefficients*.csv"],
        "download": ["regression_summary*.txt"],
    },
}

EXT_FALLBACK: dict[str, str] = {
    ".tif": "qgis", ".tiff": "qgis", ".shp": "qgis",
    ".csv": "csv", ".png": "image", ".jpg": "image",
}


def classify_file(filename: str, tool_name: str) -> str:
    """Return render type ('qgis', 'csv', 'image', 'download') for a given output file."""
    tool_patterns = PATTERNS.get(tool_name, {})
    for render_type, patterns in tool_patterns.items():
        for pattern in patterns:
            if fnmatch.fnmatch(filename, pattern):
                return render_type
    ext = Path(filename).suffix.lower()
    return EXT_FALLBACK.get(ext, "download")




async def route_outputs_async(workspace_dir: str, tool_name: str) -> list[dict]:
    """
    Async version of route_outputs. Use this when already inside an async context.
    Scan workspace_dir, classify each output file, and return a list of
    {"filename": ..., "path": ..., "render_type": ...} dicts.

    For files classified as 'qgis' (SHP/TIF):
      - The original file is returned with render_type='download'
      - Preview generation is NOT automatic — use render_spatial_file tool on-demand

    Skips directories in SKIP_DIRS and existing *_preview.png files.
    """
    raw_files = []
    for root, dirs, files in os.walk(workspace_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for filename in files:
            if filename.endswith("_preview.png"):
                continue
            full_path = os.path.join(root, filename)
            render_type = classify_file(filename, tool_name)
            # qgis files (TIF/SHP) are returned as 'download' only
            if render_type == "qgis":
                render_type = "download"
            raw_files.append({
                "filename": filename,
                "path": full_path,
                "render_type": render_type,
            })

    return raw_files


def route_outputs(workspace_dir: str, tool_name: str) -> list[dict]:
    """
    Scan workspace_dir and classify output files without generating previews.
    Works in both sync and async contexts via scan_output_directory.
    """
    raw_files = []
    for root, dirs, files in os.walk(workspace_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for filename in files:
            if filename.endswith("_preview.png"):
                continue
            full_path = os.path.join(root, filename)
            render_type = classify_file(filename, tool_name)
            if render_type == "qgis":
                render_type = "download"
            raw_files.append({
                "filename": filename,
                "path": full_path,
                "render_type": render_type,
            })
    return raw_files
