"""
PowerGenome Region Clustering - PyScript Web App

This module runs in the browser via PyScript and handles:
1. Loading and displaying the BA map
2. Managing BA selection state
3. Running clustering algorithms (agglomerative and Louvain)
4. Generating YAML output

Related modules:
- clustering_algorithms.py: Pure network clustering algorithm functions
- esr_utils.py: ESR (Energy Share Requirement) policy functions
- visualization_utils.py: Color palette constants and color utility functions
- renewables_utils.py: Renewables clustering utilities
- fast_interconnection/: Interconnection resource group utilities
"""

import asyncio
import html
import json
import math
import re
import time
import warnings
import zipfile
from io import BytesIO, StringIO

from js import L, Uint8Array, document, fetch, globalThis, window
from pyodide.ffi import create_proxy, to_js
from renewables_utils import optimize_cluster_allocation

# Suppress pandas pyarrow deprecation warning
warnings.filterwarnings("ignore", message=".*pyarrow.*", category=DeprecationWarning)

import networkx as nx
import numpy as np

# Will be imported after PyScript loads packages
import pandas as pd
import yaml
from calc_network import calculate_network_from_frames
from clustering_algorithms import (
    agglomerative_cluster,
    build_transmission_graph,
    calculate_modularity,
    find_optimal_clusters,
    generate_cluster_names,
    get_regional_groups,
    hierarchical_cluster,
    louvain_cluster,
    run_kmeans_simple,
    spectral_cluster,
    standardize_features,
)
from esr_utils import (
    ESRGenerationError,
    aggregate_policy_for_region,
    build_esr_zones,
    build_state_to_interconnect_map,
    build_state_trading_zones,
    can_generator_satisfy_policy,
    can_states_trade,
    can_states_trade_transitively,
    compute_state_demand_fractions,
    extract_state_for_region,
    generate_emission_policies_csv,
    get_qualified_technologies,
    get_state_policy_value,
    get_states_in_region,
    split_bas_by_trading_zones,
)
from fast_interconnection.fast_assign import fast_assign_cpas
from fast_interconnection.resource_groups import (
    DEFAULT_PROFILE_PATHS,
    build_assigned_df,
    build_resource_group_json,
)
from visualization_utils import (
    CLUSTER_COLORS,
    GROUP_OUTLINE_COLORS,
    lighten_color,
)

# ============================================================================
# Global State
# ============================================================================

# Battery storage default variable O&M values ($/MWh).
# Single source of truth for battery defaults used in _DEFAULT_NEW_RESOURCES
# and _get_default_resource_modifiers().
_BATTERY_DEFAULT_VAR_OM = 0.15
_BATTERY_DEFAULT_VAR_OM_IN = 0.15
# ATB does not include a WACC for batteries; use this default value (real).
_BATTERY_DEFAULT_WACC = 0.05

# Default new-build resources (ATB 2024, planning_year="all").
# Sizes are taken from web/data/atb_size.json where available.
_DEFAULT_NEW_RESOURCES = [
    {
        "technology": "NaturalGas",
        "tech_detail": "2-on-1 Combined Cycle (H-Frame)",
        "cost_case": "Moderate",
        "size_mw": 992,
        "planning_year": "all",
        "data_year": 2024,
    },
    {
        "technology": "NaturalGas",
        "tech_detail": "Combustion Turbine (F-Frame)",
        "cost_case": "Moderate",
        "size_mw": 233,
        "planning_year": "all",
        "data_year": 2024,
    },
    {
        "technology": "LandbasedWind",
        "tech_detail": "Class3",
        "cost_case": "Moderate",
        "size_mw": 200,
        "planning_year": "all",
        "data_year": 2024,
    },
    {
        "technology": "UtilityPV",
        "tech_detail": "Class1",
        "cost_case": "Moderate",
        "size_mw": 100,
        "planning_year": "all",
        "data_year": 2024,
    },
    {
        "technology": "Utility-Scale Battery Storage",
        "tech_detail": "Lithium Ion",
        "cost_case": "Moderate",
        "size_mw": 60,
        "variable_o_m_mwh": _BATTERY_DEFAULT_VAR_OM,
        "variable_o_m_mwh_in": _BATTERY_DEFAULT_VAR_OM_IN,
        "wacc_real": _BATTERY_DEFAULT_WACC,
        "planning_year": "all",
        "data_year": 2024,
    },
    {
        "technology": "Nuclear",
        "tech_detail": "Nuclear - Large",
        "cost_case": "Moderate",
        "size_mw": 1000,
        "planning_year": "all",
        "data_year": 2024,
    },
]


class AppState:
    def __init__(self):
        self.map = None
        self.geojson_layer = None
        self.geojson_data = None
        self.hierarchy_df = None
        self.transmission_df = None
        self.plants_df = None  # generator-level data
        self.plant_region_map = None  # plant_id -> BA mapping
        self.plant_candidates = []  # cache of last candidate list
        self.plant_groups = []  # full groups list from last suggest_plant_clusters call
        self.plant_candidate_overrides = (
            {}
        )  # (model_region, tech_group) -> num_clusters
        self.selected_bas = set()
        self.ba_layers = {}  # ba_id -> layer
        self.all_bas = set()
        self.ba_centroids = {}  # ba_id -> (lat, lng) for box selection
        self.box_select_mode = True  # Default to box select mode
        self.box_start = None
        self.group_colors = {}  # group_value -> outline color
        self.group_fill_colors = {}  # group_value -> light fill color
        self.ba_to_group = {}  # ba_id -> group_value
        self.current_grouping = None  # current grouping column
        self.cluster_colors = {}  # ba_id -> cluster fill color (set after clustering)
        self.is_clustered = False  # True after clustering has been run
        self.ba_to_region = {}  # ba_id -> model_region name (set after clustering)
        self.transmission_lines_layer = (
            None  # Leaflet layer group for transmission lines
        )
        self.show_transmission_lines = False  # Toggle state for transmission lines
        self.region_aggregations = (
            None  # Store last clustering result for redrawing lines
        )
        self.custom_tech_groups = {}  # user-editable tech grouping map
        self.available_techs = set()  # techs not currently assigned to a group
        self.current_group = None  # currently selected group in UI
        self.omit_selected = set()  # technologies to omit (dual-list UI)
        self.omit_available = set()  # technologies available to include

        # Manual region definition
        self.is_manual_mode = False  # True when in manual region definition mode
        self.manual_regions = {}  # region_name -> list[ba_id]
        self.selected_manual_region = None  # currently selected region for assignment

        # Settings generation (Settings tab)
        self.settings_yamls = {}  # filename -> yaml string
        self.new_resources = [
            dict(r) for r in _DEFAULT_NEW_RESOURCES
        ]  # list of dicts: {technology, tech_detail, cost_case, size_mw, planning_year}
        self.modified_new_resources = {}  # key -> metadata + schema for resources.yml
        self.atb_options = []  # list[dict] loaded from web/data/atb_options.json
        self.atb_index = {}  # year -> tech -> detail -> sorted(list(cost_case))
        self.atb_years = []  # sorted list of years
        self.atb_size_map = (
            {}
        )  # year -> {(tech, tech_detail): size_mw}, with (tech, None) as fallback key
        self.plant_cluster_settings = (
            None  # parsed YAML dict from plant clustering output
        )
        self.ccs_disposal_cost = 20  # Default CCS disposal cost in $/metric ton
        self.ccs_disposal_cost_map = {}  # tech_name -> disposal cost override

        # Resource groups (Fast interconnection)
        self.fast_interconnection_data = None  # cached parquet/csv dataframes
        self.resource_group_files = {}  # filename -> bytes or str
        self.resource_group_assignments = None  # cached assignments dataframe
        self.uploaded_lcoe_onshorewind = None  # user-uploaded wind LCOE DataFrame
        self.uploaded_lcoe_solar = None  # user-uploaded solar LCOE DataFrame

        # Renewables clustering inputs
        self.reeds_annual_demand_df = None  # BA-level annual demand by weather year
        self.reeds_annual_demand_avg = {}  # ba_id -> avg annual demand (MWh)
        self.renewables_clusters = None  # computed renewables_clusters settings
        self.renewables_clusters_info = None  # summary for UI
        self.renewables_region_capacity_mw = (
            {}
        )  # tech -> region -> selected capacity MW
        self.renewables_region_base_capacity_mw = (
            {}
        )  # tech -> region -> baseline selected capacity MW from share target
        self.renewables_pending_region_capacity_mw = (
            {}
        )  # tech -> region -> current advanced slider capacity MW
        self.renewables_region_available_mw = (
            {}
        )  # tech -> region -> total available capacity MW
        self.renewables_capacity_overrides_mw = {
            "landbasedwind": {},
            "utilitypv": {},
        }  # session-only tech -> region -> override capacity MW
        self.renewables_curve_data = {}  # tech -> region -> arrays + selection metadata
        self.renewables_selected_region = None
        self.renewables_selected_tech = "landbasedwind"
        self.renewables_maps = {}  # tech -> Leaflet map
        self.renewables_map_layers = {}  # tech -> Leaflet geojson layer
        self.renewables_map_initialized = False
        self.renewables_regions_geojson_cache = None  # dissolved model-region GeoJSON
        self.renewables_regions_geojson_key = None  # cache key for dissolved geometry

        # Fuel scenario options (Settings tab)
        self.fuel_prices_df = None  # fuel price scenarios from PowerGenome-data
        self.fuel_scenario_index = {}  # data_year -> fuel -> sorted(list(scenario))

        # ESR (Energy Share Requirements) data
        self.rps_df = None  # RPS policy data
        self.ces_df = None  # CES policy data
        self.rectable_df = None  # State trading rules
        self.pop_fraction_df = None  # Population fractions for BA/state
        self.allowed_techs_df = None  # Allowed techs for RPS/CES
        # ============================================================================
        # ESR Generator (Energy Share Requirements)
        # ============================================================================

        self.esr_zones = None  # Computed ESR zones
        self.esr_map = None  # ESR constraint name -> regions mapping
        self.esr_type_map = None  # ESR constraint name -> "RPS" or "CES"
        self.esr_policy_states = None  # ESR constraint name -> set of policy states
        self.esr_rps_techs = set()
        self.esr_ces_techs = set()
        self.emission_policies_df = None  # Generated emission_policies.csv

        # Network cost calculation
        self.network_costs_df = None  # pd.DataFrame result from calc_network
        self.network_data_cache = None  # (nodes_df, edges_df, topo_df) loaded once

        # Last auto-derived default for the Step 7 "Region Name" input, used to
        # refresh it when regions change while preserving custom user names.
        self.resource_group_name_default = ""


state = AppState()


SETTINGS_FILENAMES = [
    "data.yml",
    "model_definition.yml",
    "resources.yml",
    "fuels.yml",
    "transmission.yml",
    "distributed_gen.yml",
    "resource_tags.yml",
    "startup_costs.yml",
]

WORKFLOW_STATE_FILENAME = "workflow_state.yml"
WORKFLOW_STATE_SCHEMA = "powergenome-tools-workflow-state"
WORKFLOW_STATE_VERSION = 1

# ============================================================================
# Input data download guidance (Zenodo deposits)
# ============================================================================
#
# The generated data.yml references input data that is published on Zenodo from
# the PowerGenome-data repo (https://github.com/gschivley/PowerGenome-data).
# There are three Zenodo deposits. UPDATE THE url FIELDS BELOW once the
# production Zenodo records exist (currently only the core-data deposit has a
# sandbox record). This block is the single place to update; the Step 9 UI
# section and the DATA_SOURCES.md bundled in the export ZIP are both rendered
# from it.

# Suggested local data root used by the example data.yml snippet.
DATA_ROOT_EXAMPLE = "~/PowerGenome-data"

# Project-relative folder (beside data.yml, in the project folder) where the
# web app-generated new-build wind/solar resource group files (Step 7
# downloads) are saved. Unlike the Zenodo deposits, these belong in the project
# folder, not in ~/PowerGenome-data.
WEB_APP_RESOURCE_GROUPS_FOLDER = "resource_groups"

# Core input data deposit: files referenced by data_location + the per-table
# *_table settings keys in data.yml.
_CORE_DATA_FILES = [
    "reeds_generators_transformed.csv",
    "plant_region_map.csv",
    "technology_costs_atb.parquet",
    "technology_heat_rates_nrelatb.csv",
    "operational_constraints_reeds.csv",
    "transmission_capacity_reeds.csv",
    "fuel_prices.parquet",
    "dollar_year_adjustment.csv",
    "reeds_load_transformed.parquet",
    "regional_cost_multipliers.csv",
    "distributed_capacity.parquet",
    "distributed_profiles.parquet",
    "reserve_margins.csv",
    "nerc_reserve_margins.csv",
    "cpi_data.csv",
]

DATA_SOURCES = [
    {
        "id": "core",
        "title": "PowerGenome Input Data (core)",
        "description": (
            "Core tabular input data referenced by data.yml: generators, costs, "
            "heat rates, operational/transmission constraints, fuel prices, load, "
            "distributed generation, reserve margins, and dollar-year adjustment."
        ),
        "url": "https://sandbox.zenodo.org/records/590994",  # TODO: production DOI pending
        "doi": "10.5072/zenodo.590994 (sandbox; production DOI pending)",
        "target_folder": "data",
        "settings_keys": ["data_location"],
        "files": _CORE_DATA_FILES,
    },
    {
        "id": "profiles",
        "title": "PowerGenome Renewable Resource Profiles",
        "description": (
            "Hourly capacity-factor profiles for new-build renewable resource "
            "groups. Point RESOURCE_GROUP_PROFILES at the folder containing these "
            "profile files."
        ),
        "url": "https://zenodo.org/",  # TODO: replace with deposit URL when published
        "doi": "DOI pending (first publish upcoming)",
        "target_folder": "resource_profiles",
        "settings_keys": ["RESOURCE_GROUP_PROFILES"],
        "files": [],
    },
    {
        "id": "resource_groups",
        "title": "PowerGenome Existing Renewable Resource Groups",
        "description": (
            "Pre-built resource group metadata for existing renewable generators. "
            "The RESOURCE_GROUPS setting accepts a list of folders; include this "
            "deposit's folder as one entry and add a second entry for the "
            "new-build wind/solar resource groups generated in Step 7 "
            "(Interconnection) of this web app, saved in your project folder."
        ),
        "url": "https://zenodo.org/",  # TODO: replace with deposit URL when published
        "doi": "DOI pending (first publish upcoming)",
        "target_folder": "existing_resource_groups",
        "settings_keys": ["RESOURCE_GROUPS"],
        "files": [],
    },
]

# Name of the instructions file bundled into the export ZIP.
DATA_SOURCES_FILENAME = "DATA_SOURCES.md"

# Static explanation of data versioning, shared by the UI section and the
# generated DATA_SOURCES.md.
DATA_VERSIONING_NOTES = (
    "Data releases are versioned by date (e.g. data_version 2026.08.14; "
    "multiple releases on the same day get a suffix). Each file also carries its "
    "own element version recording when that data was last updated. Zenodo keeps "
    "every version of a deposit, and the release notes list files added, updated, "
    "or removed. Settings generated by this tool target the latest release; if "
    "you already have an older copy of the data, check the release notes on "
    "Zenodo for changed files before reusing it."
)

# Explains where the second RESOURCE_GROUPS entry (new-build wind/solar
# resource groups) comes from. Shown in the Step 9 section and the bundled
# DATA_SOURCES.md.
NEW_BUILD_RESOURCE_GROUPS_NOTE = (
    "The second RESOURCE_GROUPS entry is the folder where you save the resource "
    "group files you download in Step 7 (Interconnection) of this web app — "
    "these are the new-build wind/solar resource groups. Unlike the data "
    "deposits, keep this folder inside your project, for example a "
    "`resource_groups` folder in the same directory as data.yml, rather than in "
    "~/PowerGenome-data. The first entry points to the existing resource groups "
    "deposit listed above."
)


def build_data_yml_snippet():
    """Return a copy-paste-ready data.yml path snippet using the suggested root."""
    paths = {d["id"]: d["target_folder"] for d in DATA_SOURCES}
    return (
        "# data.yml path settings (example)\n"
        f'data_location: ["{DATA_ROOT_EXAMPLE}/{paths["core"]}"]\n'
        "RESOURCE_GROUPS:\n"
        f'  - "{DATA_ROOT_EXAMPLE}/{paths["resource_groups"]}"\n'
        f'  - "{WEB_APP_RESOURCE_GROUPS_FOLDER}"  # new-build groups, in your project folder\n'
        f'RESOURCE_GROUP_PROFILES: "{DATA_ROOT_EXAMPLE}/{paths["profiles"]}"\n'
    )


def render_data_sources_md():
    """Render the download instructions as Markdown (bundled into the ZIP)."""
    lines = [
        "# PowerGenome input data downloads",
        "",
        "The generated `data.yml` references input data published on Zenodo from ",
        "the [PowerGenome-data](https://github.com/gschivley/PowerGenome-data) "
        "repo. Download the deposits below and place them in matching local "
        "folders.",
        "",
    ]
    for d in DATA_SOURCES:
        lines.append(f"## {d['title']}")
        lines.append("")
        lines.append(d["description"])
        lines.append("")
        lines.append(f"- Zenodo: {d['url']}")
        lines.append(f"- DOI: {d['doi']}")
        lines.append(
            f"- Suggested local folder: `{DATA_ROOT_EXAMPLE}/{d['target_folder']}`"
        )
        keys = ", ".join(f"`{k}`" for k in d["settings_keys"])
        lines.append(f"- Feeds settings key(s): {keys}")
        if d["files"]:
            lines.append("- Contains files: " + ", ".join(f"`{f}`" for f in d["files"]))
        lines.append("")
    lines.extend(
        [
            "## New-build resource groups (Step 7)",
            "",
            NEW_BUILD_RESOURCE_GROUPS_NOTE,
            "",
            "## Example data.yml paths",
            "",
            "```yaml",
            build_data_yml_snippet().rstrip("\n"),
            "```",
            "",
            "## Data versioning",
            "",
            DATA_VERSIONING_NOTES,
            "",
        ]
    )
    return "\n".join(lines)


def render_data_sources_html():
    """Render the download instructions as an HTML fragment for Step 9."""
    parts = []
    parts.append(
        "<p>The generated <code>data.yml</code> references input data published "
        "on Zenodo from the "
        '<a href="https://github.com/gschivley/PowerGenome-data" target="_blank" '
        'rel="noopener">PowerGenome-data</a> repo. Download each deposit below '
        "and place it in a matching local folder.</p>"
    )
    for d in DATA_SOURCES:
        title = html.escape(d["title"])
        desc = html.escape(d["description"])
        url = html.escape(d["url"])
        doi = html.escape(d["doi"])
        folder = html.escape(f"{DATA_ROOT_EXAMPLE}/{d['target_folder']}")
        keys = ", ".join(f"<code>{html.escape(k)}</code>" for k in d["settings_keys"])
        parts.append(f"<h4>{title}</h4>")
        parts.append(f"<p>{desc}</p>")
        parts.append("<ul>")
        parts.append(
            f'<li>Zenodo: <a href="{url}" target="_blank" rel="noopener">{url}</a> '
            f"(DOI: {doi})</li>"
        )
        parts.append(f"<li>Suggested local folder: <code>{folder}</code></li>")
        parts.append(f"<li>Feeds settings key(s): {keys}</li>")
        if d["files"]:
            file_items = "".join(
                f"<li><code>{html.escape(f)}</code></li>" for f in d["files"]
            )
            parts.append(
                '<li>Contains files:<ul style="margin-top:4px;">'
                f"{file_items}</ul></li>"
            )
        parts.append("</ul>")
    parts.append("<h4>New-build resource groups (Step 7)</h4>")
    parts.append(f"<p>{html.escape(NEW_BUILD_RESOURCE_GROUPS_NOTE)}</p>")
    parts.append("<h4>Example data.yml paths</h4>")
    parts.append(
        '<textarea id="dataYmlSnippet" readonly title="Copy-paste example data.yml '
        'path settings" style="width: 100%; height: 90px; font-family: monospace; '
        "font-size: 11px; border: 1px solid #ddd; border-radius: 4px; padding: "
        '8px;">' + html.escape(build_data_yml_snippet()) + "</textarea>"
    )
    parts.append("<h4>Data versioning</h4>")
    parts.append(f"<p>{html.escape(DATA_VERSIONING_NOTES)}</p>")
    parts.append(
        "<p><em>These instructions are also included as "
        f"{DATA_SOURCES_FILENAME} in the downloaded settings ZIP.</em></p>"
    )
    return "".join(parts)


def populate_data_sources_section():
    """Populate the Step 9 data-download guidance section from DATA_SOURCES."""
    el = document.getElementById("dataSourcesContent")
    if el is not None:
        el.innerHTML = render_data_sources_html()


# These are the editable controls whose values are not fully represented in the
# generated PowerGenome settings files.
_WORKFLOW_FORM_IDS = (
    "groupingColumn",
    "esrCompatibleClustering",
    "autoOptimize",
    "targetRegions",
    "minRegions",
    "maxRegions",
    "clusteringMethod",
    "demandWeightMethod",
    "targetUsdYear",
    "utcOffset",
    "vollValue",
    "modelYears",
    "planningYears",
    "groupTechDefault",
    "plantBudget",
    "capThreshold",
    "hrThreshold",
    "atbYearSelect",
    "atbTechSelect",
    "atbTechDetailSelect",
    "atbCostCaseSelect",
    "atbSizeMw",
    "atbCcsDisposalCost",
    "atbOverrideCapex",
    "atbOverrideCapexMwh",
    "atbOverrideHeatRate",
    "atbOverrideFixedOM",
    "atbOverrideVarOM",
    "atbOverrideVarOMIn",
    "atbOverrideWacc",
    "modBaseTech",
    "modBaseTechDetail",
    "modBaseCostCase",
    "modNewTech",
    "modNewTechDetail",
    "modSizeMw",
    "modOverrideCapexMw",
    "modOverrideCapexMwh",
    "modOverrideHeatRate",
    "modOverrideFixedOM",
    "modOverrideVarOM",
    "modOverrideVarOMIn",
    "modOverrideWacc",
    "modFuelType",
    "modStandardFuel",
    "modNewFuelName",
    "modNewFuelPrice",
    "modNewFuelEf",
    "modTagClass",
    "modIsCommit",
    "newResourceYearSelect",
    "modResourceYearSelect",
    "fuelDataYear",
    "esrIncludeCheckbox",
    "esrIncludeRPS",
    "esrIncludeCES",
    "interconnectCapexMw",
    "resourceGroupName",
    "resourceGroupPenalty",
    "renewablesWindShare",
    "renewablesSolarShare",
    "renewablesWindAvgResourceMw",
    "renewablesSolarAvgResourceMw",
    "renewablesWindBudgetCount",
    "renewablesSolarBudgetCount",
    "showTransmissionLines",
)


FUEL_PRICES_URLS = [
    # Prefer local copy if present
    "./data/fuel_prices.csv",
    # Fallback to PowerGenome-data (raw content; should be CORS-friendly)
    "https://raw.githubusercontent.com/gschivley/PowerGenome-data/main/data/fuel_prices.csv",
]


DEFAULT_RENEWABLES_CLUSTERS = [
    {
        "region": "all",
        "technology": "landbasedwind",
        "filter": [{"feature": "lcoe", "max": 70}],
        "bin": [
            {
                "feature": "lcoe",
                "weights": "capacity_mw",
                "q": 10,
                "mw_per_bin": 10000,
            }
        ],
        "cluster": [{"feature": "cf", "n_clusters": 5, "method": "agg"}],
    },
    {
        "region": "all",
        "technology": "utilitypv",
        "filter": [{"feature": "lcoe", "max": 40}],
        "bin": [
            {
                "feature": "lcoe",
                "weights": "capacity_mw",
                "q": 10,
                "mw_per_bin": 50000,
            }
        ],
        "cluster": [{"feature": "lcoe", "n_clusters": 5, "method": "agg"}],
    },
]

RENEWABLES_TECH_CONFIG = {
    "landbasedwind": {
        "resource_key": "onshorewind",
        "mw_per_bin": 10000,
        "cluster_feature": "cf",
        "n_clusters": 5,
        "avg_resource_mw": 2000,
    },
    "utilitypv": {
        "resource_key": "solar",
        "mw_per_bin": 50000,
        "cluster_feature": "lcoe",
        "n_clusters": 5,
        "avg_resource_mw": 5000,
    },
}

RENEWABLES_TECH_STYLES = {
    "landbasedwind": {
        "label": "Wind",
        "base_color": "#1f4e79",
        "bar_color": "#1f4e79",
    },
    "utilitypv": {
        "label": "Solar",
        "base_color": "#b87f00",
        "bar_color": "#b87f00",
    },
}


DEFAULT_GENERATOR_COLUMNS = [
    "region",
    "Resource",
    "technology",
    "cluster",
    "R_ID",
    "Zone",
    "Num_VRE_Bins",
    "CapRes_1",
    "CapRes_2",
    "THERM",
    "VRE",
    "MUST_RUN",
    "STOR",
    "FLEX",
    "LDS",
    "HYDRO",
    "ESR_1",
    "ESR_2",
    "MinCapTag_1",
    "MinCapTag_2",
    "Min_Share",
    "Max_Share",
    "Existing_Cap_MWh",
    "Existing_Cap_MW",
    "Existing_Charge_Cap_MW",
    "num_units",
    "unmodified_existing_cap_mw",
    "New_Build",
    "Cap_Size",
    "Min_Cap_MW",
    "Max_Cap_MW",
    "Max_Cap_MWh",
    "Min_Cap_MWh",
    "Max_Charge_Cap_MW",
    "Min_Charge_Cap_MW",
    "Min_Share_percent",
    "Max_Share_percent",
    "capex_mw",
    "Inv_Cost_per_MWyr",
    "Fixed_OM_Cost_per_MWyr",
    "capex_mwh",
    "Inv_Cost_per_MWhyr",
    "Fixed_OM_Cost_per_MWhyr",
    "Var_OM_Cost_per_MWh",
    "Var_OM_Cost_per_MWh_In",
    "Inv_Cost_Charge_per_MWyr",
    "Fixed_OM_Cost_Charge_per_MWyr",
    "Start_Cost_per_MW",
    "Start_Fuel_MMBTU_per_MW",
    "Heat_Rate_MMBTU_per_MWh",
    "heat_rate_mmbtu_mwh_iqr",
    "heat_rate_mmbtu_mwh_std",
    "Fuel",
    "Min_Power",
    "Self_Disch",
    "Eff_Up",
    "Eff_Down",
    "Hydro_Energy_to_Power_Ratio",
    "Ratio_power_to_energy",
    "Min_Duration",
    "Max_Duration",
    "Max_Flexible_Demand_Delay",
    "Max_Flexible_Demand_Advance",
    "Flexible_Demand_Energy_Eff",
    "Ramp_Up_Percentage",
    "Ramp_Dn_Percentage",
    "Up_Time",
    "Down_Time",
    "NACC_Eff",
    "NACC_Peak_to_Base",
    "Reg_Max",
    "Rsv_Max",
    "Reg_Cost",
    "Rsv_Cost",
    "spur_miles",
    "spur_capex",
    "offshore_spur_miles",
    "offshore_spur_capex",
    "tx_miles",
    "tx_capex",
    "interconnect_annuity",
    "Min_Retired_Cap_MW",
    "Min_Retired_Energy_Cap_MW",
    "Min_Retired_Charge_Cap_MW",
]


def get_outline_color(ba_id):
    """Get the outline color for a BA based on its group."""
    group = state.ba_to_group.get(ba_id)
    if group and group in state.group_colors:
        return state.group_colors[group]
    return "#666666"  # default gray


def get_fill_color(ba_id):
    """Get the fill color for an unselected BA based on its group (lighter version)."""
    group = state.ba_to_group.get(ba_id)
    if group and group in state.group_fill_colors:
        return state.group_fill_colors[group]
    return "#cccccc"  # default gray


# Styling defaults
STYLE_UNSELECTED = {
    "fillColor": "#cccccc",
    "fillOpacity": 0.4,
    "color": "#666666",
    "weight": 1,
}

STYLE_SELECTED = {
    "fillColor": "#2196F3",
    "fillOpacity": 0.6,
    "color": "#1565C0",
    "weight": 2,
}

STYLE_HOVER = {
    "fillOpacity": 0.8,
    "weight": 3,
}

# ============================================================================
# Map Functions
# ============================================================================


def update_loading_text(text):
    """Update the loading indicator text."""
    el = document.getElementById("loadingText")
    if el:
        el.textContent = text


def hide_loading():
    """Hide the loading overlay and show welcome overlay."""
    el = document.getElementById("loading")
    if el:
        el.classList.add("hidden")

    # Show welcome overlay on first load
    welcome_el = document.getElementById("welcomeOverlay")
    if welcome_el:
        welcome_el.classList.remove("hidden")


def set_status(message, status_type="info"):
    """Update the status box."""
    el = document.getElementById("statusBox")
    if el:
        el.textContent = message
        el.className = f"status {status_type}"


def update_selected_display():
    """Update the selected BAs display."""
    count_el = document.getElementById("selectedCount")
    list_el = document.getElementById("selectedList")

    if count_el:
        count_el.textContent = str(len(state.selected_bas))

    if list_el:
        if state.selected_bas:
            sorted_bas = sorted(state.selected_bas)
            html_list = "".join(
                f'<span class="ba-tag">{ba}</span>' for ba in sorted_bas
            )
            list_el.innerHTML = html_list
        else:
            list_el.innerHTML = "<em>None selected</em>"

    # Enable/disable run button for clustering mode
    run_btn = document.getElementById("runBtn")
    if run_btn:
        run_btn.disabled = len(state.selected_bas) < 2

    # Update manual mode displays if in manual mode
    if state.is_manual_mode:
        update_unassigned_display()
        update_manual_regions_display()


def toggle_ba_selection(ba_id, layer):
    """Toggle selection state of a BA."""
    outline_color = get_outline_color(ba_id)
    fill_color = get_fill_color(ba_id)

    if ba_id in state.selected_bas:
        state.selected_bas.remove(ba_id)
        layer.setStyle(
            to_js(
                {
                    "fillColor": fill_color,
                    "fillOpacity": 0.5,
                    "color": outline_color,
                    "weight": 2,
                }
            )
        )
    else:
        state.selected_bas.add(ba_id)
        layer.setStyle(
            to_js(
                {
                    "fillColor": "#2196F3",
                    "fillOpacity": 0.6,
                    "color": outline_color,
                    "weight": 3,
                }
            )
        )

    update_selected_display()


def on_feature_click(e):
    """Handle click on a BA feature."""
    layer = e.target
    props = layer.feature.properties
    ba_id = props.rb
    toggle_ba_selection(ba_id, layer)


def on_feature_mouseover(e):
    """Handle mouseover on a BA feature - subtle highlight without changing colors."""
    layer = e.target
    # Only increase weight slightly, don't change fill
    layer.setStyle(to_js({"weight": 4}))
    layer.bringToFront()


def on_feature_mouseout(e):
    """Handle mouseout on a BA feature - restore original style."""
    layer = e.target
    props = layer.feature.properties
    ba_id = props.rb

    outline_color = get_outline_color(ba_id)

    # If clustering has been run, use cluster colors for selected BAs
    if state.is_clustered and ba_id in state.cluster_colors:
        layer.setStyle(
            to_js(
                {
                    "fillColor": state.cluster_colors[ba_id],
                    "fillOpacity": 0.7,
                    "color": outline_color,
                    "weight": 3,
                }
            )
        )
    elif ba_id in state.selected_bas:
        layer.setStyle(
            to_js(
                {
                    "fillColor": "#2196F3",
                    "fillOpacity": 0.6,
                    "color": outline_color,
                    "weight": 3,
                }
            )
        )
    else:
        fill_color = get_fill_color(ba_id)
        layer.setStyle(
            to_js(
                {
                    "fillColor": fill_color,
                    "fillOpacity": 0.5,
                    "color": outline_color,
                    "weight": 2,
                }
            )
        )


def on_each_feature(feature, layer):
    """Attach event handlers to each feature."""
    props = feature.properties
    ba_id = props.rb

    state.ba_layers[ba_id] = layer
    state.all_bas.add(ba_id)

    # Calculate centroid for box selection
    bounds = layer.getBounds()
    center = bounds.getCenter()
    state.ba_centroids[ba_id] = (center.lat, center.lng)

    # Initial tooltip (will be updated when data loads)
    tooltip = f"<b>{ba_id}</b><br>State: {props.st}"
    layer.bindTooltip(tooltip)

    # Events
    layer.on("click", create_proxy(on_feature_click))
    layer.on("mouseover", create_proxy(on_feature_mouseover))
    layer.on("mouseout", create_proxy(on_feature_mouseout))


def style_feature(feature):
    """Return initial style for a feature."""
    return to_js(STYLE_UNSELECTED)


async def init_map():
    """Initialize the Leaflet map."""
    update_loading_text("Initializing map...")

    # Create map centered on US
    state.map = L.map("map").setView(to_js([39.8, -98.5]), 4)
    # Expose map on window for resize/invalidate hooks
    try:
        window.appMap = state.map
    except Exception:
        pass

    # Add tile layer
    L.tileLayer(
        "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        to_js(
            {
                "attribution": '&copy; <a href="https://openstreetmap.org">OpenStreetMap</a>',
                "maxZoom": 18,
            }
        ),
    ).addTo(state.map)

    # Create custom pane for transmission lines (above overlays, z-index 450)
    state.map.createPane("transmissionPane")
    state.map.getPane("transmissionPane").style.zIndex = 450

    # Load GeoJSON
    update_loading_text("Loading BA boundaries...")

    response = await fetch("./data/US_PCA.geojson")
    geojson_text = await response.text()
    state.geojson_data = json.loads(geojson_text)

    # Add GeoJSON layer
    state.geojson_layer = L.geoJSON(
        to_js(state.geojson_data),
        to_js(
            {
                "style": create_proxy(style_feature),
                "onEachFeature": create_proxy(on_each_feature),
            }
        ),
    ).addTo(state.map)

    # Fit bounds
    state.map.fitBounds(state.geojson_layer.getBounds())

    # Update total count
    total_el = document.getElementById("totalCount")
    if total_el:
        total_el.textContent = str(len(state.all_bas))


# ============================================================================
# Data Loading
# ============================================================================


async def load_data():
    """Load hierarchy, transmission, and plant CSVs."""
    update_loading_text("Loading hierarchy data...")

    response = await fetch("./data/hierarchy.csv")
    if not response.ok:
        raise Exception(
            f"Failed to load hierarchy.csv: {response.status} {response.statusText}"
        )
    hierarchy_text = await response.text()

    # Debug: check what we got
    if hierarchy_text.startswith("<!"):
        raise Exception(
            f"Got HTML instead of CSV. First 100 chars: {hierarchy_text[:100]}"
        )

    state.hierarchy_df = pd.read_csv(StringIO(hierarchy_text))

    update_loading_text("Loading transmission data...")

    response = await fetch("./data/transmission_capacity_reeds.csv")
    if not response.ok:
        raise Exception(
            f"Failed to load transmission CSV: {response.status} {response.statusText}"
        )
    transmission_text = await response.text()
    state.transmission_df = pd.read_csv(StringIO(transmission_text))

    # Load dummy transmission capacity (used for clustering connectivity only;
    # not included in network cost calculations or exports).
    try:
        response = await fetch("./data/dummy_transmission_capacity.csv")
        if response.ok:
            dummy_text = await response.text()
            dummy_df = pd.read_csv(StringIO(dummy_text))
            state.transmission_df = pd.concat(
                [state.transmission_df, dummy_df], ignore_index=True
            )
    except Exception:
        pass  # Dummy file is optional

    update_loading_text("Loading plant data...")

    response = await fetch("./data/reeds_generators_transformed.csv")
    if not response.ok:
        raise Exception(
            f"Failed to load plant data CSV: {response.status} {response.statusText}"
        )
    plant_text = await response.text()
    state.plants_df = pd.read_csv(StringIO(plant_text))

    response = await fetch("./data/plant_region_map.csv")
    if not response.ok:
        raise Exception(
            f"Failed to load plant-region map CSV: {response.status} {response.statusText}"
        )
    plant_map_text = await response.text()
    state.plant_region_map = pd.read_csv(StringIO(plant_map_text))

    update_loading_text("Loading annual demand data...")
    try:
        response = await fetch("./data/reeds_annual_demand_2050.csv")
        if response.ok:
            demand_text = await response.text()
            demand_df = pd.read_csv(StringIO(demand_text))
            if {"region", "weather_year", "annual_demand_mwh"} <= set(
                demand_df.columns
            ):
                demand_df["region"] = demand_df["region"].astype(str).str.lower()
                state.reeds_annual_demand_df = demand_df
                state.reeds_annual_demand_avg = (
                    demand_df.groupby("region")["annual_demand_mwh"].mean().to_dict()
                )
    except Exception:
        state.reeds_annual_demand_df = None
        state.reeds_annual_demand_avg = {}

    # Load rectable for ESR-compatible clustering (optional but needed if checkbox is checked)
    update_loading_text("Loading trading rules...")
    try:
        response = await fetch("./data/state_policies/rectable.csv")
        if response.ok:
            rectable_text = await response.text()
            state.rectable_df = pd.read_csv(StringIO(rectable_text), index_col=0)
    except Exception:
        pass  # Will be loaded later in ESR step if needed


def update_group_colors():
    """Update group colors based on current grouping column and apply to map."""
    grouping_col = document.getElementById("groupingColumn").value

    if state.hierarchy_df is None:
        return

    # Skip if same grouping column (unless first time)
    if state.current_grouping == grouping_col and state.group_colors:
        return

    state.current_grouping = grouping_col

    # Get unique groups and assign colors
    unique_groups = sorted(state.hierarchy_df[grouping_col].unique())
    state.group_colors = {}
    state.group_fill_colors = {}
    for i, group in enumerate(unique_groups):
        outline_color = GROUP_OUTLINE_COLORS[i % len(GROUP_OUTLINE_COLORS)]
        state.group_colors[group] = outline_color
        # Create a light fill color (70% toward white)
        state.group_fill_colors[group] = lighten_color(outline_color, 0.75)

    # Build BA to group mapping
    state.ba_to_group = {}
    for _, row in state.hierarchy_df.iterrows():
        ba = row["ba"]
        state.ba_to_group[ba] = row[grouping_col]

    # Apply colors to all BA layers
    apply_group_colors_to_map()


def apply_group_colors_to_map():
    """Apply group outline and fill colors to all BA layers on the map."""
    for ba_id, layer in state.ba_layers.items():
        outline_color = get_outline_color(ba_id)
        fill_color = get_fill_color(ba_id)

        if ba_id in state.selected_bas:
            layer.setStyle(
                to_js(
                    {
                        "fillColor": "#2196F3",
                        "fillOpacity": 0.6,
                        "color": outline_color,
                        "weight": 3,
                    }
                )
            )
        else:
            layer.setStyle(
                to_js(
                    {
                        "fillColor": fill_color,
                        "fillOpacity": 0.5,
                        "color": outline_color,
                        "weight": 2,
                    }
                )
            )


def update_tooltips():
    """Update all BA tooltips to show the current grouping column value."""
    if state.hierarchy_df is None:
        return

    grouping_col = document.getElementById("groupingColumn").value

    # Get friendly name for the grouping column from the dropdown text
    grouping_select = document.getElementById("groupingColumn")
    selected_option = grouping_select.options.item(grouping_select.selectedIndex)
    grouping_label = selected_option.text.split(" (")[
        0
    ]  # Get just the column name part

    # Build a lookup from BA to hierarchy row
    ba_data = {}
    for _, row in state.hierarchy_df.iterrows():
        ba_data[row["ba"]] = row

    # Update each layer's tooltip
    for ba_id, layer in state.ba_layers.items():
        if ba_id in ba_data:
            row = ba_data[ba_id]
            state_val = row.get("st", "N/A")
            group_val = row.get(grouping_col, "N/A")

            # Include model region if clustering has been done
            if state.is_clustered and ba_id in state.ba_to_region:
                region_name = state.ba_to_region[ba_id]
                tooltip = f"<b>{ba_id}</b><br>State: {state_val}<br>{grouping_label}: {group_val}<br><b>Region: {region_name}</b>"
            else:
                tooltip = f"<b>{ba_id}</b><br>State: {state_val}<br>{grouping_label}: {group_val}"
            layer.unbindTooltip()
            layer.bindTooltip(tooltip)


def update_no_cluster_options():
    """Update the no-cluster checkbox options based on grouping column."""
    grouping_col = document.getElementById("groupingColumn").value
    container = document.getElementById("noClusterContainer")

    if state.hierarchy_df is None or container is None:
        return

    # Update group colors when grouping changes
    update_group_colors()

    # Update tooltips to show new grouping column
    update_tooltips()

    # Get unique values for this column
    unique_values = sorted(state.hierarchy_df[grouping_col].unique())

    # Build checkboxes with color indicators
    html = ""
    for val in unique_values:
        color = state.group_colors.get(val, "#666666")
        html += f"""
            <label>
                <input type="checkbox" name="noCluster" value="{val}">
                <span style="display:inline-block;width:12px;height:12px;background:{color};border-radius:2px;margin-right:4px;vertical-align:middle;"></span>
                {val}
            </label>
        """

    container.innerHTML = html


def run_clustering(
    selected_bas,
    grouping_column,
    target_regions,
    no_cluster_groups,
    auto_optimize=False,
    min_regions=None,
    max_regions=None,
    method="hierarchical-sum",
    esr_compatible=False,
    demand_weight_method=None,
):
    """
    Run the clustering algorithm.

    Returns a tuple of (model_regions, region_aggregations, error_message, info)
    where info is a dict with optional metadata like chosen_n and modularity.

    If esr_compatible=True, BAs are first split by trading zone connectivity
    to ensure all states in a resulting region can trade with each other.
    """
    try:
        info = {}

        # Filter hierarchy to selected BAs
        hierarchy = state.hierarchy_df[
            state.hierarchy_df["ba"].isin(selected_bas)
        ].copy()

        if len(hierarchy) == 0:
            return None, None, "No valid BAs selected", info

        # Identify BAs to keep unclustered
        unclustered_bas = set()
        if no_cluster_groups:
            for group in no_cluster_groups:
                group_bas = set(hierarchy[hierarchy[grouping_column] == group]["ba"])
                unclustered_bas.update(group_bas)

        # BAs to cluster
        cluster_bas = selected_bas - unclustered_bas

        # If ESR-compatible clustering is enabled, split BAs by trading zones
        # This ensures that BAs whose states can't trade (even transitively) are kept separate
        if esr_compatible and state.rectable_df is not None and len(cluster_bas) > 1:
            trading_groups = split_bas_by_trading_zones(
                cluster_bas, state.hierarchy_df, state.rectable_df
            )
            # If trading creates multiple disjoint groups, treat the smaller groups as "unclustered"
            # so they don't get merged with incompatible BAs
            if len(trading_groups) > 1:
                info["trading_zone_splits"] = len(trading_groups)

        if len(cluster_bas) < 2:
            # Only unclustered BAs - use state abbreviation naming
            region_aggregations = {}
            name_counts = {}

            for ba in sorted(selected_bas):
                ba_row = state.hierarchy_df[state.hierarchy_df["ba"] == ba]
                if not ba_row.empty:
                    st = ba_row.iloc[0]["st"]
                    base_name = st
                else:
                    base_name = ba

                if base_name in name_counts:
                    name_counts[base_name] += 1
                    region_name = f"{base_name}{name_counts[base_name]}"
                else:
                    name_counts[base_name] = 1
                    region_name = f"{base_name}1"

                region_aggregations[region_name] = [ba]

            model_regions = sorted(region_aggregations.keys())
            return model_regions, region_aggregations, None, info

        # Get regional groups
        groups = get_regional_groups(hierarchy, grouping_column, cluster_bas)

        # Determine number of unclustered regions
        num_unclustered = len(unclustered_bas)

        if auto_optimize and min_regions is not None and max_regions is not None:
            # Auto-optimize mode: find best number of clusters
            actual_min = max(1, min_regions - num_unclustered)
            actual_max = max(1, max_regions - num_unclustered)
            actual_max = min(actual_max, len(cluster_bas))
            actual_min = min(actual_min, actual_max)

            # Resolve demand weights for optional demand-based edge weighting
            demand_weights = (
                state.reeds_annual_demand_avg
                if demand_weight_method and demand_weight_method != "none"
                else None
            )

            clusters, chosen_n, modularity, all_scores, optimal_clusters = (
                find_optimal_clusters(
                    hierarchy,
                    state.transmission_df,
                    cluster_bas,
                    grouping_column,
                    actual_min,
                    actual_max,
                    demand_weights=demand_weights,
                    demand_weight_method=demand_weight_method,
                )
            )

            info["chosen_n"] = chosen_n + num_unclustered
            info["modularity"] = modularity
            info["all_scores"] = all_scores
            info["optimal_n"] = len(optimal_clusters) + num_unclustered
            info["optimal_clusters"] = optimal_clusters
        else:
            # Fixed target mode
            actual_target = max(1, target_regions - num_unclustered)
            actual_target = min(actual_target, len(cluster_bas))

            # Resolve demand weights for optional demand-based edge weighting
            demand_weights = (
                state.reeds_annual_demand_avg
                if demand_weight_method and demand_weight_method != "none"
                else None
            )

            if method == "louvain":
                clusters, _, modularity_val, _, _ = find_optimal_clusters(
                    hierarchy,
                    state.transmission_df,
                    cluster_bas,
                    grouping_column,
                    actual_target,  # min
                    actual_target,  # max
                    demand_weights=demand_weights,
                    demand_weight_method=demand_weight_method,
                )
                # find_optimal_clusters calculates modularity, but we'll recalculate it below
                # to be consistent with other methods.
            else:
                # Run hierarchical clustering that respects grouping column boundaries
                clusters = hierarchical_cluster(
                    hierarchy,
                    state.transmission_df,
                    cluster_bas,
                    grouping_column,
                    actual_target,
                    method=method,
                    esr_rectable_df=state.rectable_df if esr_compatible else None,
                    demand_weights=demand_weights,
                    demand_weight_method=demand_weight_method,
                )

            # Calculate modularity for info
            graph = build_transmission_graph(state.transmission_df, cluster_bas)
            modularity = calculate_modularity(graph, clusters)
            info["modularity"] = modularity

        # Generate names
        cluster_names = generate_cluster_names(clusters, state.hierarchy_df)

        # Build output
        region_aggregations = {}
        name_counts = {}  # Track name counts from cluster names

        # First, collect all base names used in cluster names to track counts
        for label, name in cluster_names.items():
            # Extract base name (remove trailing digits)
            base = name.rstrip("0123456789")
            num_str = name[len(base) :]
            if num_str:
                num = int(num_str)
                name_counts[base] = max(name_counts.get(base, 0), num)

        for label, nodes in clusters.items():
            name = cluster_names[label]
            region_aggregations[name] = sorted(nodes)

        # If we have optimal cluster info (from auto-optimize splitting), map it to region names
        if "optimal_clusters" in info and len(info["optimal_clusters"]) < len(clusters):
            optimal_combinations = []

            # Map each BA to its final region name
            ba_to_final_region = {}
            for region_name, bas in region_aggregations.items():
                for ba in bas:
                    ba_to_final_region[ba] = region_name

            # Check each optimal cluster
            for _, opt_nodes in info["optimal_clusters"].items():
                # Find which final regions are contained in this optimal cluster
                contained_regions = set()
                for ba in opt_nodes:
                    if ba in ba_to_final_region:
                        contained_regions.add(ba_to_final_region[ba])

                # If more than one final region is in this optimal cluster, they would have been combined
                if len(contained_regions) > 1:
                    optimal_combinations.append(sorted(list(contained_regions)))

            if optimal_combinations:
                info["optimal_combinations"] = optimal_combinations

            # Clean up large objects from info
            del info["optimal_clusters"]

        # Add unclustered BAs with state abbreviation naming
        for ba in sorted(unclustered_bas):
            # Get state for this BA
            ba_row = state.hierarchy_df[state.hierarchy_df["ba"] == ba]
            if not ba_row.empty:
                st = ba_row.iloc[0]["st"]
                base_name = st
            else:
                base_name = ba

            # Add counter
            if base_name in name_counts:
                name_counts[base_name] += 1
                region_name = f"{base_name}{name_counts[base_name]}"
            else:
                name_counts[base_name] = 1
                region_name = f"{base_name}1"

            region_aggregations[region_name] = [ba]

        model_regions = sorted(region_aggregations.keys())

        return model_regions, region_aggregations, None, info

    except Exception as e:
        return None, None, str(e), {}


def generate_yaml(model_regions, region_aggregations):
    """Generate YAML output."""
    output = {
        "model_regions": model_regions,
        "region_aggregations": region_aggregations,
    }
    return yaml.dump(output, default_flow_style=False, sort_keys=False)


# ============================================================================
# Plant Clustering
# ============================================================================


# Lightweight technology grouping for clustering heuristics


ALWAYS_ONE_TECHS = {
    "Conventional Hydroelectric",
    "Run of River Hydroelectric",
    "Solar Photovoltaic",
    "Onshore Wind Turbine",
    "Offshore Wind Turbine",
    "Batteries",
    "Hydroelectric Pumped Storage",
}


# Default grouping and omit behavior for plant clustering UI
DEFAULT_TECH_GROUPS = {
    "Biomass": {
        "Biomass",
        "Landfill Gas",
        "Municipal Solid Waste",
        "Other Waste Biomass",
        "Wood/Wood Waste Biomass",
    },
    "Other_peaker": {
        "Natural Gas Internal Combustion Engine",
        "Other Natural Gas",
        "Other Gases",
        "Petroleum Liquids",
    },
}

DEFAULT_OMIT_TOKENS = {
    "all other",
    "flywheel",
    "solar thermal with energy storage",
    "solar thermal without energy storage",
}


def clone_group_map(group_map):
    """Shallow clone of group map with set copies."""
    return {name: set(values) for name, values in group_map.items()}


def normalize_technology(tech_name, omit_tokens=None):
    """Map technology names to canonical groups; return None to exclude."""
    if not isinstance(tech_name, str):
        return None

    name = tech_name.lower()

    default_omit = ["solar thermal", "all other", "flywheel"]
    tokens = [
        t.lower() for t in (omit_tokens if omit_tokens is not None else default_omit)
    ]

    if any(token in name for token in tokens):
        return None

    # Specific matches first
    if "pumped storage" in name:
        return "Hydroelectric Pumped Storage"
    if "run of river" in name:
        return "Run of River Hydroelectric"
    if "conventional hydro" in name or "hydroelectric" in name:
        return "Conventional Hydroelectric"
    if "landfill gas" in name:
        return "Landfill Gas"
    if "municipal solid waste" in name:
        return "Municipal Solid Waste"
    if "other waste biomass" in name:
        return "Other Waste Biomass"
    if "wood" in name:
        return "Wood/Wood Waste Biomass"
    if "biomass" in name:
        return "Biomass"
    if "geothermal" in name:
        return "Geothermal"
    if "nuclear" in name:
        return "Nuclear"
    if "combined cycle" in name:
        return "Natural Gas Fired Combined Cycle"
    if "combustion turbine" in name:
        return "Natural Gas Fired Combustion Turbine"
    if "steam turbine" in name:
        return "Natural Gas Steam Turbine"
    if "internal combustion" in name:
        return "Natural Gas Internal Combustion Engine"
    if "steam coal" in name or "coal" in name:
        return "Conventional Steam Coal"
    if "photovoltaic" in name:
        return "Solar Photovoltaic"
    if "offshore wind" in name:
        return "Offshore Wind Turbine"
    if "wind" in name:
        return "Onshore Wind Turbine"
    # Ensure solar thermal variants are classified before generic storage/battery
    if "solar thermal with energy storage" in name:
        return "Solar Thermal with Energy Storage"
    if "solar thermal without energy storage" in name:
        return "Solar Thermal without Energy Storage"
    if "battery" in name or "storage" in name:
        return "Batteries"
    if "petroleum" in name or "oil" in name:
        return "Petroleum Liquids"

    return tech_name


def weighted_quantile(values, quantile, weights):
    """Compute weighted quantile; expects numpy arrays."""
    sorter = np.argsort(values)
    v_sorted = values[sorter]
    w_sorted = weights[sorter]
    cum_weights = np.cumsum(w_sorted)
    cutoff = quantile * cum_weights[-1]
    return v_sorted[np.searchsorted(cum_weights, cutoff)]


def weighted_iqr(values, weights):
    """Weighted interquartile range (Q3 - Q1)."""
    if len(values) == 0:
        return 0.0
    return float(
        weighted_quantile(values, 0.75, weights)
        - weighted_quantile(values, 0.25, weights)
    )


def inertia_single_cluster(features, weights=None):
    """Inertia for a single cluster (k=1)."""
    center = features.mean(axis=0)
    sq_dists = np.sum((features - center) ** 2, axis=1)
    if weights is not None:
        return float((sq_dists * weights).sum())
    return float(sq_dists.sum())


def build_ba_to_model_region_map():
    """Return BA -> model region lookup using current clustering (or selected BAs).

    If clustering has been run, uses the region_aggregations.
    If not, but BAs are selected, maps selected BAs to themselves and excludes others.
    If nothing is selected, maps all BAs to themselves (fallback).

    Note: Only BAs that are part of the clustering (i.e., in region_aggregations)
    are included. Plants in other BAs are excluded from clustering.
    """
    if state.region_aggregations:
        mapping = {}
        for region_name, bas in state.region_aggregations.items():
            for ba in bas:
                mapping[ba] = region_name
        # Only return mapping for clustered BAs - plants in other BAs will be
        # dropped during the merge (their model_region will be NaN)
        return mapping

    # If BAs are selected but clustering hasn't been run, only include selected BAs
    if state.selected_bas:
        return {ba: ba for ba in state.selected_bas}

    # Fallback to identity mapping (each BA is its own model region)
    return {ba: ba for ba in state.all_bas}


def apply_default_grouping(tech_group, enabled=True, group_map=None):
    """Collapse technologies into groups using provided map when enabled."""
    if not enabled:
        return tech_group
    mapping = group_map if group_map is not None else DEFAULT_TECH_GROUPS
    for group_name, members in mapping.items():
        if tech_group in members:
            return group_name
    return tech_group


def prepare_plants_dataframe(
    *,
    group_enabled=True,
    omit_tokens=None,
    group_map=None,
):
    """Merge plant data with BA mapping and apply technology grouping."""
    if state.plants_df is None or state.plant_region_map is None:
        raise Exception("Plant data not loaded yet")

    ba_to_region = build_ba_to_model_region_map()

    df = state.plants_df.merge(state.plant_region_map, on="plant_id", how="left")

    # Map BA regions to model regions
    df["model_region"] = df["region"].map(ba_to_region)
    df = df.dropna(subset=["model_region"])

    # Normalize technologies and optionally group/omit
    df["tech_group"] = df["technology"].apply(
        lambda t: normalize_technology(t, omit_tokens=omit_tokens)
    )
    df = df[df["tech_group"].notna()].copy()
    df["tech_group"] = df["tech_group"].apply(
        lambda t: apply_default_grouping(t, enabled=group_enabled, group_map=group_map)
    )

    # Ensure numeric columns are present
    df["capacity_mw"] = pd.to_numeric(df["capacity_mw"], errors="coerce").fillna(0)
    df["heat_rate_mmbtu_mwh"] = pd.to_numeric(
        df["heat_rate_mmbtu_mwh"], errors="coerce"
    )
    df["fom_per_mwyr"] = pd.to_numeric(df["fom_per_mwyr"], errors="coerce")

    return df


def suggest_plant_clusters(
    budget=200,
    cap_threshold=1500.0,
    hr_iqr_threshold=0.8,
    *,
    group_enabled=True,
    omit_tokens=None,
    group_map=None,
):
    """Suggest cluster counts per (model_region, tech_group) under a hard budget."""
    df = prepare_plants_dataframe(
        group_enabled=group_enabled,
        omit_tokens=omit_tokens,
        group_map=group_map,
    )

    groups = []
    raw_tech_map = {}  # normalized -> set(raw tech names)

    for (model_region, tech_group), sub in df.groupby(["model_region", "tech_group"]):
        n_units = len(sub)
        total_cap = float(sub["capacity_mw"].sum())

        # Features
        heat_rate = sub["heat_rate_mmbtu_mwh"].to_numpy()
        fom = sub["fom_per_mwyr"].to_numpy()
        weights = sub["capacity_mw"].replace(0, 1e-6).to_numpy()

        # Fill missing values with medians to keep clustering stable
        hr_median = np.nanmedian(heat_rate) if not np.all(np.isnan(heat_rate)) else 0.0
        fom_median = np.nanmedian(fom) if not np.all(np.isnan(fom)) else 0.0
        hr_filled = np.where(np.isnan(heat_rate), hr_median, heat_rate)
        fom_filled = np.where(np.isnan(fom), fom_median, fom)
        features = np.column_stack([hr_filled, fom_filled])
        standardized = standardize_features(features)

        hr_iqr_val = weighted_iqr(hr_filled, weights)

        # K-means improvement for k=2
        improvement2 = 0.0
        if n_units >= 2:
            inertia1 = inertia_single_cluster(standardized, weights)
            inertia2, _, _ = run_kmeans_simple(standardized, 2, weights=weights)
            if inertia1 > 0:
                improvement2 = max(0.0, (inertia1 - inertia2) / inertia1)

        desired = 1
        # Only suggest splitting if there is actual variation in efficiency/performance
        has_variance = hr_iqr_val > 0.01 or improvement2 > 0.01

        if (
            n_units >= 2
            and has_variance
            and (
                total_cap >= cap_threshold
                or hr_iqr_val >= hr_iqr_threshold
                or improvement2 >= 0.15
            )
        ):
            desired = min(2, n_units)

        if n_units >= 3 and improvement2 >= 0.3:
            desired = min(3, n_units)

        # Allow up to 5 clusters if budget permits and variance is high
        if n_units >= 4 and improvement2 >= 0.4:
            desired = min(4, n_units)

        if n_units >= 5 and improvement2 >= 0.5:
            desired = min(5, n_units)

        # Priority scales with variance, so identical units (IQR=0) get low priority
        priority = total_cap * (hr_iqr_val + improvement2)

        raw_tech_map.setdefault(tech_group, set()).update(
            sub["technology"].dropna().unique()
        )

        groups.append(
            {
                "model_region": model_region,
                "tech_group": tech_group,
                "n_units": n_units,
                "total_capacity": total_cap,
                "hr_iqr": hr_iqr_val,
                "improvement2": improvement2,
                "desired": desired,
                "priority": priority,
                "plant_data": [
                    {"heat_rate": float(hr), "capacity": float(cap)}
                    for hr, cap in zip(
                        hr_filled.tolist(),
                        sub["capacity_mw"].fillna(0).tolist(),
                    )
                ],
            }
        )

    # Enforce single-cluster techs
    for g in groups:
        if g["tech_group"] in ALWAYS_ONE_TECHS:
            g["desired"] = 1
            g["num_clusters"] = 1
            g["alt_num_clusters"] = 1

    if not groups:
        raise Exception("No plants found for current model regions")

    # Allocate clusters within budget
    num_groups = len(groups)
    min_possible = num_groups  # at least 1 per group
    effective_budget = max(budget, min_possible)

    for g in groups:
        g["num_clusters"] = 1

    remaining = effective_budget - num_groups

    for g in sorted(groups, key=lambda x: x["priority"], reverse=True):
        if g["tech_group"] in ALWAYS_ONE_TECHS:
            continue
        extra_needed = max(0, g["desired"] - g["num_clusters"])
        if remaining <= 0 or extra_needed == 0:
            continue
        extra = min(extra_needed, remaining)
        g["num_clusters"] += extra
        remaining -= extra

    # Alt clusters are a gentle +1 where possible
    for g in groups:
        if g["tech_group"] in ALWAYS_ONE_TECHS:
            g["alt_num_clusters"] = 1
        else:
            g["alt_num_clusters"] = min(g["num_clusters"] + 1, g["n_units"])

    # Compute defaults and overrides
    defaults = {}
    tech_to_counts = {}
    tech_to_alt = {}
    for g in groups:
        tech_to_counts.setdefault(g["tech_group"], []).append(g["num_clusters"])
        tech_to_alt.setdefault(g["tech_group"], []).append(g["alt_num_clusters"])

    for tech, counts in tech_to_counts.items():
        min_count = min(counts) if counts else 1
        default_num = min_count if min_count > 1 else 1
        alt_counts = tech_to_alt.get(tech, [])
        default_alt = min(alt_counts) if alt_counts else default_num
        default_alt = max(default_alt, default_num)
        defaults[tech] = int(default_num)

    overrides = {}
    total_clusters = 0
    for g in groups:
        total_clusters += g["num_clusters"]
        d = defaults[g["tech_group"]]
        if g["num_clusters"] != d:
            overrides.setdefault(g["model_region"], {})[g["tech_group"]] = int(
                g["num_clusters"]
            )

    # Top candidates for further splitting (where desired > assigned)
    candidates = [
        g for g in groups if g["num_clusters"] < g["desired"] and g["desired"] > 1
    ]
    candidates = sorted(candidates, key=lambda x: x["priority"], reverse=True)[:10]

    state.plant_candidates = candidates
    state.plant_groups = groups
    state.plant_candidate_overrides = {}

    if group_enabled:
        active_map = group_map if group_map is not None else DEFAULT_TECH_GROUPS
        tech_groups = {
            name: sorted(list(members)) for name, members in active_map.items()
        }
    else:
        tech_groups = {
            tech: sorted(list(raw_tech_map.get(tech, []))) for tech in sorted(defaults)
        }

    overrides_sorted = {
        region: {tech: int(val) for tech, val in sorted(tech_map.items())}
        for region, tech_map in sorted(overrides.items())
    }

    group_flag = group_enabled and any(len(v) > 0 for v in tech_groups.values())

    output = {
        "num_clusters": {tech: int(val) for tech, val in sorted(defaults.items())},
        "group_technologies": bool(group_flag),
        "tech_groups": tech_groups,
        "alt_num_clusters": overrides_sorted,
    }

    yaml_str = yaml.dump(output, default_flow_style=False, sort_keys=False)

    return yaml_str, total_clusters, effective_budget


# ============================================================================
# Event Handlers
# ============================================================================


def on_run_clustering(event):
    """Handle Run Clustering button click."""
    set_status("Running clustering...", "info")

    grouping_col = document.getElementById("groupingColumn").value
    method = document.getElementById("clusteringMethod").value

    # Check if auto-optimize mode is enabled
    auto_optimize_el = document.getElementById("autoOptimize")
    auto_optimize = auto_optimize_el.checked if auto_optimize_el else False

    if auto_optimize:
        min_regions = int(document.getElementById("minRegions").value)
        max_regions = int(document.getElementById("maxRegions").value)
        target_regions = None  # Will be determined by optimization
    else:
        target_regions = int(document.getElementById("targetRegions").value)
        min_regions = None
        max_regions = None

    # Get no-cluster selections
    no_cluster_groups = []
    checkboxes = document.querySelectorAll('input[name="noCluster"]:checked')
    for cb in checkboxes:
        no_cluster_groups.append(cb.value)

    # Check if ESR-compatible clustering is enabled
    esr_compat_el = document.getElementById("esrCompatibleClustering")
    esr_compatible = esr_compat_el.checked if esr_compat_el else False

    # Read demand weighting method
    demand_weight_el = document.getElementById("demandWeightMethod")
    demand_weight_method = demand_weight_el.value if demand_weight_el else "none"

    # Run clustering
    model_regions, region_aggregations, error, info = run_clustering(
        state.selected_bas,
        grouping_col,
        target_regions if not auto_optimize else min_regions,  # Use min as fallback
        no_cluster_groups,
        auto_optimize=auto_optimize,
        min_regions=min_regions,
        max_regions=max_regions,
        method=method,
        esr_compatible=esr_compatible,
        demand_weight_method=demand_weight_method,
    )

    if error:
        set_status(f"Error: {error}", "error")
        return

    # Generate YAML
    yaml_output = generate_yaml(model_regions, region_aggregations)

    # Display
    yaml_el = document.getElementById("yamlOut")
    if yaml_el:
        yaml_el.value = yaml_output

    # Build status message
    num_regions = len(model_regions)
    modularity = info.get("modularity", 0)

    if auto_optimize:
        chosen_n = info.get("chosen_n", num_regions)
        msg = f"Clustering complete! {num_regions} regions (optimal from {min_regions}-{max_regions}). Modularity: {modularity:.3f}"

        # Add info about optimal combinations if we forced splits
        if "optimal_combinations" in info:
            optimal_n = info.get("optimal_n", "unknown")
            msg += f"\n\nOptimal number of clusters was {optimal_n}. The following regions would be combined in the optimal solution:\n"

            # Format the combinations
            combo_strs = []
            for combo in info["optimal_combinations"]:
                combo_strs.append(f"• {', '.join(combo)}")

            msg += "\n".join(combo_strs)

        set_status(msg, "success")
    else:
        esr_note = " or ESR-compatible trading constraints" if esr_compatible else ""
        if num_regions > target_regions:
            set_status(
                f"Warning: Created {num_regions} regions, which is more than the target of {target_regions}. "
                f"This can happen when 'unclustered' groups{esr_note} or disconnected BAs exceed the target. "
                f"Modularity: {modularity:.3f}",
                "error",
            )
        else:
            set_status(
                f"Clustering complete! {num_regions} regions created. Modularity: {modularity:.3f}",
                "success",
            )

    # Store region aggregations for transmission line drawing
    state.region_aggregations = region_aggregations

    # Reset all downstream state that depends on regions (before setting new values)
    reset_region_dependent_state()

    # Update map colors to show clusters
    update_map_cluster_colors(region_aggregations)

    # Update tooltips to show model region names
    update_tooltips()

    # Update transmission lines if enabled
    update_transmission_lines()

    # Refresh plant cluster defaults based on new region mapping
    update_default_cluster_budget()

    # Compute network upgrade costs for the new region aggregation
    asyncio.create_task(_run_network_cost_calculation())


def update_map_cluster_colors(region_aggregations):
    """Update map to show cluster assignments with group outline colors preserved."""
    # Build BA -> cluster color mapping and BA -> region name mapping
    state.cluster_colors = {}
    state.ba_to_region = {}
    state.is_clustered = True

    for i, (cluster_name, bas) in enumerate(region_aggregations.items()):
        color = CLUSTER_COLORS[i % len(CLUSTER_COLORS)]
        for ba in bas:
            state.cluster_colors[ba] = color
            state.ba_to_region[ba] = cluster_name

    # Update layer styles - keep group outline color
    for ba_id, layer in state.ba_layers.items():
        if ba_id in state.selected_bas:
            fill_color = state.cluster_colors.get(ba_id, "#999999")
            outline_color = get_outline_color(ba_id)
            layer.setStyle(
                to_js(
                    {
                        "fillColor": fill_color,
                        "fillOpacity": 0.7,
                        "color": outline_color,
                        "weight": 3,
                    }
                )
            )


# ============================================================================
# Transmission Lines Visualization
# ============================================================================


def get_line_weight(capacity_mw):
    """Calculate line weight based on transmission capacity."""
    # Scale: 1-8 pixels based on capacity
    # Typical range is ~100 MW to ~15000 MW
    min_weight = 1
    max_weight = 8
    min_cap = 100
    max_cap = 12000

    # Clamp and scale
    clamped = max(min_cap, min(max_cap, capacity_mw))
    normalized = (clamped - min_cap) / (max_cap - min_cap)
    return min_weight + normalized * (max_weight - min_weight)


def draw_ba_transmission_lines():
    """Draw transmission lines between BA centroids."""
    if state.transmission_df is None or not state.ba_centroids:
        return

    lines = []

    # Only show lines for selected BAs (or all if none selected)
    relevant_bas = state.selected_bas if state.selected_bas else state.all_bas

    for _, row in state.transmission_df.iterrows():
        ba_from = row["region_from"]
        ba_to = row["region_to"]
        capacity = row["firm_ttc_mw"]

        # Only draw if both BAs are in the relevant set and have centroids
        if ba_from in relevant_bas and ba_to in relevant_bas:
            if ba_from in state.ba_centroids and ba_to in state.ba_centroids:
                lat1, lng1 = state.ba_centroids[ba_from]
                lat2, lng2 = state.ba_centroids[ba_to]

                weight = get_line_weight(capacity)

                # Create polyline
                line = L.polyline(
                    to_js([[lat1, lng1], [lat2, lng2]]),
                    to_js(
                        {
                            "color": "#ff6600",
                            "weight": weight,
                            "opacity": 0.6,
                            "pane": "transmissionPane",
                        }
                    ),
                )
                line.bindTooltip(f"{ba_from} ↔ {ba_to}<br>{capacity:,.0f} MW")
                lines.append(line)

    return lines


def draw_region_transmission_lines(region_aggregations):
    """Draw transmission lines showing capacity between model regions."""
    if state.transmission_df is None or not state.ba_centroids:
        return

    # Build BA -> region mapping
    ba_to_region = {}
    for region_name, bas in region_aggregations.items():
        for ba in bas:
            ba_to_region[ba] = region_name

    # Calculate region centroids (average of BA centroids)
    region_centroids = {}
    for region_name, bas in region_aggregations.items():
        lats = []
        lngs = []
        for ba in bas:
            if ba in state.ba_centroids:
                lat, lng = state.ba_centroids[ba]
                lats.append(lat)
                lngs.append(lng)
        if lats:
            region_centroids[region_name] = (
                sum(lats) / len(lats),
                sum(lngs) / len(lngs),
            )

    # Aggregate transmission capacity between regions
    region_capacity = {}  # (region1, region2) -> total capacity

    for _, row in state.transmission_df.iterrows():
        ba_from = row["region_from"]
        ba_to = row["region_to"]
        capacity = row["firm_ttc_mw"]

        if ba_from in ba_to_region and ba_to in ba_to_region:
            region_from = ba_to_region[ba_from]
            region_to = ba_to_region[ba_to]

            # Only count inter-region connections
            if region_from != region_to:
                # Use sorted tuple as key for bidirectional
                key = tuple(sorted([region_from, region_to]))
                region_capacity[key] = region_capacity.get(key, 0) + capacity

    lines = []

    for (region1, region2), capacity in region_capacity.items():
        if region1 in region_centroids and region2 in region_centroids:
            lat1, lng1 = region_centroids[region1]
            lat2, lng2 = region_centroids[region2]

            weight = get_line_weight(capacity)

            line = L.polyline(
                to_js([[lat1, lng1], [lat2, lng2]]),
                to_js(
                    {
                        "color": "#cc0000",
                        "weight": weight,
                        "opacity": 0.8,
                        "pane": "transmissionPane",
                    }
                ),
            )
            line.bindTooltip(f"{region1} ↔ {region2}<br>{capacity:,.0f} MW")
            lines.append(line)

    return lines


def update_transmission_lines():
    """Update transmission lines based on current state."""
    # Remove existing layer if present
    if state.transmission_lines_layer is not None:
        state.map.removeLayer(state.transmission_lines_layer)
        state.transmission_lines_layer = None

    if not state.show_transmission_lines:
        return

    # Decide which type of lines to draw
    if state.is_clustered and state.region_aggregations:
        lines = draw_region_transmission_lines(state.region_aggregations)
    else:
        lines = draw_ba_transmission_lines()

    if lines:
        # Create layer group and add to map
        state.transmission_lines_layer = L.layerGroup(to_js(lines))
        state.transmission_lines_layer.addTo(state.map)


def on_toggle_transmission_lines(event):
    """Handle toggle of transmission lines checkbox."""
    checkbox = document.getElementById("showTransmissionLines")
    state.show_transmission_lines = checkbox.checked
    update_transmission_lines()


def on_select_all(event):
    """Select all BAs."""
    for ba_id, layer in state.ba_layers.items():
        if ba_id not in state.selected_bas:
            state.selected_bas.add(ba_id)
            outline_color = get_outline_color(ba_id)
            layer.setStyle(
                to_js(
                    {
                        "fillColor": "#2196F3",
                        "fillOpacity": 0.6,
                        "color": outline_color,
                        "weight": 3,
                    }
                )
            )
    update_selected_display()


# ============================================================================
# State Reset Helpers
# ============================================================================


def reset_region_dependent_state():
    """Reset all state attributes that depend on region clustering.

    This should be called whenever regions are cleared or re-clustered,
    as many downstream features depend on the region aggregations.
    """
    # Plant clustering settings depend on region mapping
    state.plant_cluster_settings = None
    state.plant_candidates = []
    state.plant_groups = []
    state.plant_candidate_overrides = {}

    # Resource groups depend on region aggregations
    state.resource_group_files = {}
    state.resource_group_assignments = None
    state.uploaded_lcoe_onshorewind = None
    state.uploaded_lcoe_solar = None

    # Renewables clustering depends on regions and resource groups
    state.renewables_clusters = None
    state.renewables_clusters_info = None
    state.renewables_region_capacity_mw = {}
    state.renewables_region_base_capacity_mw = {}
    state.renewables_pending_region_capacity_mw = {}
    state.renewables_region_available_mw = {}
    state.renewables_capacity_overrides_mw = {
        "landbasedwind": {},
        "utilitypv": {},
    }
    state.renewables_curve_data = {}
    state.renewables_selected_region = None
    state.renewables_regions_geojson_cache = None
    state.renewables_regions_geojson_key = None

    # ESR zones and policies depend on region aggregations
    state.esr_zones = None
    state.esr_map = None
    state.esr_type_map = None
    state.esr_policy_states = None
    state.esr_rps_techs = set()
    state.esr_ces_techs = set()
    state.emission_policies_df = None

    # Network costs depend on region aggregations; cache of raw data files is kept
    state.network_costs_df = None

    # After resetting region-dependent state, refresh UI panels that depend
    # on these values so they don't display stale data.
    _update_rg = globals().get("_update_resource_group_list")
    if callable(_update_rg):
        _update_rg()

    _render_renew = globals().get("_render_renewables_preview")
    if callable(_render_renew):
        _render_renew()

    _render_esr = globals().get("render_esr_results")
    if callable(_render_esr):
        _render_esr()

    _render_candidates = globals().get("render_plant_candidates")
    if callable(_render_candidates):
        _render_candidates()

    # Refresh the Step 7 "Region Name" default when regions change (the derived
    # name mirrors the network costs filename, e.g. resource_groups_10r_...).
    _refresh_rg_name = globals().get("update_resource_group_name_default")
    if callable(_refresh_rg_name):
        _refresh_rg_name()


def reset_planning_year_dependent_state():
    """Reset all state attributes that depend on planning years.

    This should be called whenever model years are changed in the Model Setup step,
    as ESR policies depend on the planning years.
    """
    # ESR policies depend on model years (planning years)
    state.esr_map = None
    state.esr_type_map = None
    state.esr_policy_states = None
    state.emission_policies_df = None


def on_model_years_change(event):
    """Handle changes to model years input.

    This is called when the user modifies model years in the Model Setup step.
    It resets downstream state that depends on planning years and refreshes the ESR UI.
    Also updates the planning-year dropdowns in the New Resources step.
    """
    # Clear ESR-related state that depends on planning years
    reset_planning_year_dependent_state()

    # Update planning-year dropdowns for new resources
    populate_resource_year_selects()

    # Warn if any existing resources reference a year that was removed
    model_years = _get_model_years_from_dom()
    model_years_set = set(model_years)
    orphaned_years = set()
    for r in state.new_resources:
        py = r.get("planning_year")
        if py == "all":
            continue
        try:
            py_int = int(py)
        except (TypeError, ValueError):
            # If planning_year is not a valid integer, skip orphaned-year checks for this resource
            continue
        if py_int not in model_years_set:
            orphaned_years.add(py_int)
    for v in state.modified_new_resources.values():
        py = v.get("planning_year", "all")
        if py == "all":
            continue
        try:
            py_int = int(py)
        except (TypeError, ValueError):
            # If planning_year is not a valid integer, skip orphaned-year checks for this resource
            continue
        if py_int not in model_years_set:
            orphaned_years.add(py_int)

    # Refresh ESR results UI so that any previously displayed constraints/zones/CSV
    # are cleared or updated to reflect the reset state.
    try:
        render_esr_results()
    except NameError:
        # If ESR UI rendering is not available in this context, skip UI refresh.
        pass

    # Inform the user about resets
    msg = "ESR policy state has been reset because model years changed. Please rerun ESR analysis."
    if orphaned_years:
        years_str = ", ".join(str(y) for y in sorted(orphaned_years))
        msg += f" Warning: some resources target year(s) {years_str} which are no longer in your model years."
    try:
        set_status(msg, status_type="info")
    except NameError:
        pass


def on_clear_selection(event):
    """Clear all selections."""
    # Reset cluster state
    state.cluster_colors = {}
    state.ba_to_region = {}
    state.is_clustered = False
    state.region_aggregations = None

    # Reset all downstream state that depends on regions
    reset_region_dependent_state()

    for ba_id, layer in state.ba_layers.items():
        if ba_id in state.selected_bas:
            outline_color = get_outline_color(ba_id)
            fill_color = get_fill_color(ba_id)
            layer.setStyle(
                to_js(
                    {
                        "fillColor": fill_color,
                        "fillOpacity": 0.5,
                        "color": outline_color,
                        "weight": 2,
                    }
                )
            )
    state.selected_bas.clear()
    update_selected_display()

    # Update tooltips to remove region names
    update_tooltips()

    # Update transmission lines (will show BA lines if toggle is on)
    update_transmission_lines()

    # Reset plant cluster defaults to BA-level mapping
    update_default_cluster_budget()


# ============================================================================
# Manual Region Definition
# ============================================================================


def on_region_mode_change(is_manual):
    """Handle switch between clustering and manual modes."""
    state.is_manual_mode = is_manual
    if is_manual:
        # Clear any existing clustering results
        state.cluster_colors = {}
        state.ba_to_region = {}
        state.is_clustered = False
        state.region_aggregations = None
        # Reset all downstream state that depends on regions
        reset_region_dependent_state()
        # Update UI
        update_manual_regions_display()
        update_unassigned_display()
    else:
        # Clear manual regions when switching back
        state.manual_regions = {}
        state.selected_manual_region = None
        # Clear YAML output
        yaml_out = document.getElementById("yamlOut")
        if yaml_out:
            yaml_out.value = ""


def on_add_manual_region(event):
    """Handle Add Region button click."""
    name_input = document.getElementById("newRegionName")
    if not name_input:
        return

    region_name = name_input.value.strip()
    if not region_name:
        set_status("Please enter a region name", "error")
        return

    # Check for duplicate names
    if region_name in state.manual_regions:
        set_status(f"Region '{region_name}' already exists", "error")
        return

    # Add empty region
    state.manual_regions[region_name] = []
    state.selected_manual_region = region_name

    # Clear input
    name_input.value = ""

    # Update display
    update_manual_regions_display()
    set_status(
        f"Region '{region_name}' created. Select BAs on the map and click 'Assign Selected BAs'.",
        "success",
    )


def on_assign_bas(event):
    """Assign selected BAs to the selected manual region."""
    if not state.selected_manual_region:
        set_status("Please select a region first", "error")
        return

    if not state.selected_bas:
        set_status("Please select BAs on the map first", "error")
        return

    # Get BAs that are not already assigned
    assigned_bas = set()
    for bas in state.manual_regions.values():
        assigned_bas.update(bas)

    new_bas = state.selected_bas - assigned_bas
    if not new_bas:
        set_status("All selected BAs are already assigned to regions", "error")
        return

    # Assign BAs to selected region
    state.manual_regions[state.selected_manual_region].extend(new_bas)

    # Clear selection
    state.selected_bas.clear()
    update_selected_display()

    # Update displays
    update_manual_regions_display()
    update_unassigned_display()

    # Update map colors to show assignments
    update_manual_region_colors()

    set_status(
        f"Assigned {len(new_bas)} BAs to region '{state.selected_manual_region}'",
        "success",
    )


def on_finalize_manual(event):
    """Finalize manual region definitions and generate YAML."""
    if not state.manual_regions:
        set_status("Please define at least one region", "error")
        return

    # Check that all regions have BAs
    empty_regions = [name for name, bas in state.manual_regions.items() if not bas]
    if empty_regions:
        set_status(f"Regions {empty_regions} have no BAs assigned", "error")
        return

    # Convert to region_aggregations format
    state.region_aggregations = {
        name: list(bas) for name, bas in state.manual_regions.items()
    }
    state.is_clustered = True

    # Reset all downstream state that depends on regions (before setting new values)
    reset_region_dependent_state()

    # Build ba_to_region mapping
    state.ba_to_region = {}
    for region_name, bas in state.region_aggregations.items():
        for ba in bas:
            state.ba_to_region[ba] = region_name

    # Generate YAML (need model_regions list for generate_yaml function)
    model_regions = sorted(state.region_aggregations.keys())
    yaml_str = generate_yaml(model_regions, state.region_aggregations)
    yaml_out = document.getElementById("yamlOut")
    if yaml_out:
        yaml_out.value = yaml_str

    # Update transmission lines if enabled
    update_transmission_lines()

    # Update tooltips
    update_tooltips()

    # Refresh plant cluster defaults
    update_default_cluster_budget()

    num_regions = len(state.region_aggregations)
    total_bas = sum(len(bas) for bas in state.region_aggregations.values())
    set_status(
        f"Manual regions finalized! {num_regions} regions created with {total_bas} BAs.",
        "success",
    )

    # Compute network upgrade costs for the new region aggregation.
    # Cancel any in-flight calculation to avoid concurrent updates to state.network_costs_df.
    existing_task = getattr(state, "network_costs_task", None)
    if existing_task is not None and not existing_task.done():
        existing_task.cancel()

    state.network_costs_task = asyncio.create_task(_run_network_cost_calculation())


def on_clear_manual_regions(event):
    """Clear all manual region definitions."""
    state.manual_regions = {}
    state.selected_manual_region = None
    state.cluster_colors = {}
    state.ba_to_region = {}
    state.is_clustered = False
    state.region_aggregations = None

    # Reset all downstream state that depends on regions
    reset_region_dependent_state()

    # Reset map colors
    for ba_id, layer in state.ba_layers.items():
        outline_color = get_outline_color(ba_id)
        fill_color = get_fill_color(ba_id)
        layer.setStyle(
            to_js(
                {
                    "fillColor": fill_color,
                    "fillOpacity": 0.5,
                    "color": outline_color,
                    "weight": 2,
                }
            )
        )

    # Update displays
    update_manual_regions_display()
    update_unassigned_display()
    update_tooltips()

    # Clear YAML
    yaml_out = document.getElementById("yamlOut")
    if yaml_out:
        yaml_out.value = ""

    set_status("All manual regions cleared", "info")


def update_manual_regions_display():
    """Update the list of manual regions in the UI."""
    count_el = document.getElementById("manualRegionCount")
    list_el = document.getElementById("manualRegionsList")
    assign_btn = document.getElementById("assignBAsBtn")
    finalize_btn = document.getElementById("finalizeManualBtn")

    if count_el:
        count_el.textContent = str(len(state.manual_regions))

    if list_el:
        if not state.manual_regions:
            list_el.innerHTML = "<em>No regions defined yet</em>"
        else:
            html_parts = []
            for region_name, bas in sorted(state.manual_regions.items()):
                is_selected = region_name == state.selected_manual_region
                selected_class = (
                    ' style="background: #e3f2fd; font-weight: 500;"'
                    if is_selected
                    else ""
                )
                ba_count = len(bas)
                ba_list = ", ".join(sorted(bas)[:5])
                if len(bas) > 5:
                    ba_list += f", ... ({ba_count - 5} more)"

                # Add click handler to select region
                html_parts.append(
                    f'<div class="candidate-item"{selected_class} '
                    f"onclick=\"window.selectManualRegion('{region_name}')\" "
                    f'style="cursor: pointer;">'
                    f"<strong>{html.escape(region_name)}</strong> ({ba_count} BAs)<br>"
                    f'<small style="color: #666;">{html.escape(ba_list)}</small><br>'
                    f"<button onclick=\"event.stopPropagation(); window.removeManualRegion('{region_name}')\" "
                    f'style="margin-top: 4px; padding: 2px 6px; font-size: 11px;">Remove</button>'
                    f"</div>"
                )
            list_el.innerHTML = "".join(html_parts)

    # Enable/disable assign button
    if assign_btn:
        assign_btn.disabled = (
            state.selected_manual_region is None or not state.selected_bas
        )

    # Enable finalize button if we have regions with BAs
    if finalize_btn:
        has_complete_regions = any(
            len(bas) > 0 for bas in state.manual_regions.values()
        )
        finalize_btn.disabled = not has_complete_regions


def update_unassigned_display():
    """Update the list of unassigned BAs."""
    count_el = document.getElementById("unassignedCount")
    list_el = document.getElementById("unassignedList")

    # Get all assigned BAs
    assigned_bas = set()
    for bas in state.manual_regions.values():
        assigned_bas.update(bas)

    # Unassigned are selected BAs minus assigned BAs
    unassigned_bas = state.selected_bas - assigned_bas

    if count_el:
        count_el.textContent = str(len(unassigned_bas))

    if list_el:
        if not unassigned_bas:
            list_el.innerHTML = "<em>No unassigned BAs</em>"
        else:
            sorted_bas = sorted(unassigned_bas)
            html_list = "".join(
                f'<span class="ba-tag unclustered">{ba}</span>' for ba in sorted_bas
            )
            list_el.innerHTML = html_list


def update_manual_region_colors():
    """Update map colors to show manual region assignments."""
    # Build color mapping
    state.cluster_colors = {}
    for i, (region_name, bas) in enumerate(sorted(state.manual_regions.items())):
        color = CLUSTER_COLORS[i % len(CLUSTER_COLORS)]
        for ba in bas:
            state.cluster_colors[ba] = color

    # Update layer styles
    for ba_id, layer in state.ba_layers.items():
        if ba_id in state.cluster_colors:
            # Assigned BA
            fill_color = state.cluster_colors[ba_id]
            outline_color = get_outline_color(ba_id)
            layer.setStyle(
                to_js(
                    {
                        "fillColor": fill_color,
                        "fillOpacity": 0.7,
                        "color": outline_color,
                        "weight": 3,
                    }
                )
            )
        elif ba_id in state.selected_bas:
            # Selected but unassigned
            outline_color = get_outline_color(ba_id)
            layer.setStyle(
                to_js(
                    {
                        "fillColor": "#2196F3",
                        "fillOpacity": 0.6,
                        "color": outline_color,
                        "weight": 3,
                    }
                )
            )
        else:
            # Unselected
            outline_color = get_outline_color(ba_id)
            fill_color = get_fill_color(ba_id)
            layer.setStyle(
                to_js(
                    {
                        "fillColor": fill_color,
                        "fillOpacity": 0.5,
                        "color": outline_color,
                        "weight": 2,
                    }
                )
            )


def select_manual_region(region_name):
    """Select a manual region for BA assignment."""
    state.selected_manual_region = region_name
    update_manual_regions_display()


def remove_manual_region(region_name):
    """Remove a manual region."""
    if region_name in state.manual_regions:
        del state.manual_regions[region_name]
        if state.selected_manual_region == region_name:
            state.selected_manual_region = None
        update_manual_regions_display()
        update_unassigned_display()
        update_manual_region_colors()
        set_status(f"Region '{region_name}' removed", "info")


def on_parse_yaml_regions(event):
    """Parse YAML region definitions and load them into manual regions."""
    yaml_input = document.getElementById("yamlRegionInput")
    if not yaml_input:
        return

    yaml_text = yaml_input.value.strip()
    if not yaml_text:
        set_status("Please paste YAML region definitions first", "error")
        return

    try:
        # Parse YAML
        parsed = yaml.safe_load(yaml_text)

        if not isinstance(parsed, dict):
            set_status("YAML must be a dictionary/mapping", "error")
            return

        # Determine format and extract region_aggregations
        region_aggregations = None

        # Format 1: Full definition with model_regions and region_aggregations
        if "region_aggregations" in parsed:
            region_aggregations = parsed["region_aggregations"]
            # Note: model_regions is extracted for format validation but not used in manual mode
            # since manual regions are always derived from region_aggregations keys

            if not isinstance(region_aggregations, dict):
                set_status("region_aggregations must be a dictionary", "error")
                return
        # Format 2 & 3: Direct region mappings (could be at top level or wrapped)
        else:
            # All keys should map to lists of BAs
            region_aggregations = parsed

        # Validate region_aggregations structure
        if not region_aggregations:
            set_status("No region definitions found in YAML", "error")
            return

        for region_name, bas in region_aggregations.items():
            if not isinstance(bas, list):
                set_status(f"Region '{region_name}' must map to a list of BAs", "error")
                return
            if not all(isinstance(ba, str) for ba in bas):
                set_status(
                    f"All BAs in region '{region_name}' must be strings", "error"
                )
                return

        # Validate that all BAs exist in our data
        invalid_bas = []
        for region_name, bas in region_aggregations.items():
            for ba in bas:
                if ba not in state.all_bas:
                    invalid_bas.append(ba)

        if invalid_bas:
            unique_invalid = sorted(set(invalid_bas))
            set_status(
                f"Invalid BA codes in YAML: {', '.join(unique_invalid[:10])}"
                + (
                    f" and {len(unique_invalid) - 10} more"
                    if len(unique_invalid) > 10
                    else ""
                ),
                "error",
            )
            return

        # Check for duplicate BAs across regions
        all_bas = []
        for bas in region_aggregations.values():
            all_bas.extend(bas)

        if len(all_bas) != len(set(all_bas)):
            duplicates = [ba for ba in set(all_bas) if all_bas.count(ba) > 1]
            set_status(
                f"Duplicate BAs found in multiple regions: {', '.join(duplicates[:5])}",
                "error",
            )
            return

        # Clear existing manual regions
        state.manual_regions = {}
        state.selected_manual_region = None

        # Load regions from YAML
        for region_name, bas in region_aggregations.items():
            state.manual_regions[region_name] = list(bas)

        # Select all BAs that are in the YAML
        state.selected_bas.clear()
        for bas in region_aggregations.values():
            state.selected_bas.update(bas)

        # Update displays
        update_selected_display()
        update_manual_regions_display()
        update_unassigned_display()
        update_manual_region_colors()

        # Clear YAML input
        yaml_input.value = ""

        num_regions = len(state.manual_regions)
        total_bas = sum(len(bas) for bas in state.manual_regions.values())
        region_word = "region" if num_regions == 1 else "regions"
        ba_word = "BA" if total_bas == 1 else "BAs"
        set_status(
            f"Successfully loaded {num_regions} {region_word} with {total_bas} {ba_word} from YAML",
            "success",
        )

    except yaml.YAMLError as e:
        set_status(f"Invalid YAML format: {str(e)}", "error")
    except Exception as e:
        set_status(f"Error parsing YAML: {str(e)}", "error")


# Export functions to JavaScript
window.selectManualRegion = create_proxy(select_manual_region)
window.removeManualRegion = create_proxy(remove_manual_region)
window.on_region_mode_change = create_proxy(on_region_mode_change)


# ============================================================================
# Box Selection Mode
# ============================================================================


def set_selection_mode(mode):
    """Set selection mode: 'click' or 'box'."""
    state.box_select_mode = mode == "box"

    # Update button styles
    click_btn = document.getElementById("clickModeBtn")
    box_btn = document.getElementById("boxModeBtn")
    hint = document.getElementById("selectionHint")
    map_el = document.getElementById("map")

    if click_btn and box_btn:
        if state.box_select_mode:
            click_btn.classList.remove("active")
            box_btn.classList.add("active")
            if hint:
                hint.textContent = "Drag on the map to select multiple BAs"
            if map_el:
                map_el.classList.add("box-select-mode")
            # Disable map dragging
            state.map.dragging.disable()
        else:
            click_btn.classList.add("active")
            box_btn.classList.remove("active")
            if hint:
                hint.textContent = "Click on BAs to toggle selection"
            if map_el:
                map_el.classList.remove("box-select-mode")
            # Enable map dragging
            state.map.dragging.enable()


def on_click_mode(event):
    """Switch to click selection mode."""
    set_selection_mode("click")


def on_box_mode(event):
    """Switch to box selection mode."""
    set_selection_mode("box")


def on_map_mousedown(e):
    """Handle mousedown for box selection."""
    if not state.box_select_mode:
        return

    state.box_start = e.latlng

    # Create visual selection box
    box = document.createElement("div")
    box.id = "selectionBox"
    box.className = "selection-box"

    # Position at mouse
    container_point = state.map.latLngToContainerPoint(e.latlng)
    box.style.left = f"{container_point.x}px"
    box.style.top = f"{container_point.y}px"
    box.style.width = "0px"
    box.style.height = "0px"

    map_container = document.getElementById("map")
    map_container.appendChild(box)


def on_map_mousemove(e):
    """Handle mousemove for box selection."""
    if not state.box_select_mode or not state.box_start:
        return

    box = document.getElementById("selectionBox")
    if not box:
        return

    # Get start and current container points
    start_point = state.map.latLngToContainerPoint(state.box_start)
    current_point = state.map.latLngToContainerPoint(e.latlng)

    # Calculate box dimensions
    min_x = min(start_point.x, current_point.x)
    min_y = min(start_point.y, current_point.y)
    width = abs(current_point.x - start_point.x)
    height = abs(current_point.y - start_point.y)

    # Update box position and size
    box.style.left = f"{min_x}px"
    box.style.top = f"{min_y}px"
    box.style.width = f"{width}px"
    box.style.height = f"{height}px"


def on_map_mouseup(e):
    """Handle mouseup for box selection - select BAs in box."""
    if not state.box_select_mode or not state.box_start:
        return

    # Remove visual box
    box = document.getElementById("selectionBox")
    if box:
        box.remove()

    # Create bounds from start and end points
    end_latlng = e.latlng
    bounds = L.latLngBounds(state.box_start, end_latlng)

    # Find all BAs whose centroid is within bounds
    selected_count = 0
    for ba_id, (lat, lng) in state.ba_centroids.items():
        point = L.latLng(lat, lng)
        if bounds.contains(point):
            # Add to selection if not already selected
            if ba_id not in state.selected_bas:
                state.selected_bas.add(ba_id)
                layer = state.ba_layers.get(ba_id)
                if layer:
                    outline_color = get_outline_color(ba_id)
                    layer.setStyle(
                        to_js(
                            {
                                "fillColor": "#2196F3",
                                "fillOpacity": 0.6,
                                "color": outline_color,
                                "weight": 3,
                            }
                        )
                    )
                selected_count += 1

    state.box_start = None
    update_selected_display()

    if selected_count > 0:
        set_status(f"Added {selected_count} BAs to selection.", "info")


def on_copy_yaml(event):
    """Copy YAML to clipboard."""
    yaml_el = document.getElementById("yamlOut")
    if yaml_el and yaml_el.value:
        window.navigator.clipboard.writeText(yaml_el.value)
        set_status("YAML copied to clipboard!", "success")


def on_download_yaml(event):
    """Download YAML file."""
    yaml_el = document.getElementById("yamlOut")
    if not yaml_el or not yaml_el.value:
        set_status("No YAML to download. Run clustering first.", "error")
        return

    # Create blob and download
    blob = window.Blob.new([yaml_el.value], to_js({"type": "text/yaml"}))
    url = window.URL.createObjectURL(blob)

    a = document.createElement("a")
    a.href = url
    a.download = "region_aggregations.yml"
    a.click()

    window.URL.revokeObjectURL(url)
    set_status("YAML downloaded!", "success")


# Cluster assignment colors for bubble charts (up to 5 clusters)
_BUBBLE_COLORS = ["#4472c4", "#ed7d31", "#a9d18e", "#e85f5f", "#ffd966"]


def _candidate_svg(plant_data, k):
    """Generate an inline SVG bubble chart for a plant candidate group.

    X-axis is heat rate; all bubbles share the same y-position; bubble radius is
    proportional to sqrt(capacity); color indicates k-means cluster assignment.
    """
    if not plant_data:
        return ""

    heat_rates = [p["heat_rate"] for p in plant_data]
    capacities = [p["capacity"] for p in plant_data]
    n = len(plant_data)

    k_eff = min(k, n)
    if k_eff <= 1 or len(set(heat_rates)) <= 1:
        labels = [0] * n
    else:
        features = np.array([[hr] for hr in heat_rates], dtype=float)
        weights = np.array([max(c, 1e-6) for c in capacities], dtype=float)
        _, _, raw_labels = run_kmeans_simple(features, k_eff, weights=weights)
        labels = list(raw_labels) if raw_labels is not None else [0] * n

    W, H = 240, 50
    pad = 15

    hr_min = min(heat_rates)
    hr_max = max(heat_rates)
    hr_range = hr_max - hr_min if hr_max > hr_min else 1.0

    max_cap = max(capacities) if max(capacities) > 0 else 1.0
    max_r = 12

    circles = []
    for hr, cap, lbl in zip(heat_rates, capacities, labels):
        cx = pad + (hr - hr_min) / hr_range * (W - 2 * pad)
        cy = H / 2
        r = max(3.0, math.sqrt(cap / max_cap) * max_r)
        color = _BUBBLE_COLORS[lbl % len(_BUBBLE_COLORS)]
        tooltip = f"{cap:.0f} MW, {hr:.2f} MMBtu/MWh"
        circles.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{color}" '
            f'fill-opacity="0.75" stroke="white" stroke-width="0.8">'
            f"<title>{tooltip}</title></circle>"
        )

    return (
        f'<svg width="{W}" height="{H}" style="display:block;overflow:visible">'
        + "".join(circles)
        + "</svg>"
    )


def render_plant_candidates():
    """Render the top plant split candidates list."""
    container = document.getElementById("plantCandidateList")
    if not container:
        return

    if not state.plant_candidates:
        container.innerHTML = (
            "<em>No additional splits recommended within the current budget.</em>"
        )
        return

    parts = []
    for i, g in enumerate(state.plant_candidates):
        key = (g["model_region"], g["tech_group"])
        current_k = state.plant_candidate_overrides.get(key, g["num_clusters"])
        max_k = g["n_units"]
        svg = _candidate_svg(g.get("plant_data", []), current_k)
        row = (
            f'<div class="candidate-item" '
            f'style="display:flex;align-items:center;gap:10px;padding:8px 10px;">'
            f'<div style="flex:1;min-width:0;">'
            f'<div><strong>{html.escape(g["model_region"])}</strong> &mdash; '
            f'{html.escape(g["tech_group"])}'
            f'<span style="color:#888;font-size:11px;"> (desired {g["desired"]}; '
            f'{g["total_capacity"]:.0f}\u202fMW, HR IQR {g["hr_iqr"]:.2f})</span></div>'
            f'<div style="display:flex;align-items:center;gap:5px;margin-top:5px;">'
            f'<label style="font-size:12px;white-space:nowrap;" for="candidateK{i}">Clusters:</label>'
            f'<input id="candidateK{i}" type="number" min="1" max="{max_k}" value="{current_k}" '
            f'style="width:55px;font-size:12px;" '
            f'aria-label="Number of clusters for {html.escape(g["model_region"])} {html.escape(g["tech_group"])}" '
            f'oninput="window.onCandidateClusterChange(event,{i})">'
            f"</div>"
            f"</div>"
            f'<div id="candidateChart{i}" style="flex-shrink:0;">{svg}</div>'
            f"</div>"
        )
        parts.append(row)

    container.innerHTML = "".join(parts)


def on_candidate_cluster_change(event, idx):
    """Update bubble chart and YAML when a candidate's cluster count changes."""
    if not state.plant_candidates:
        return
    try:
        idx = int(idx)
        new_k = int(float(event.target.value))
    except (ValueError, TypeError):
        return
    if idx < 0 or idx >= len(state.plant_candidates) or new_k < 1:
        return

    g = state.plant_candidates[idx]
    new_k = min(new_k, g["n_units"])
    event.target.value = str(new_k)
    key = (g["model_region"], g["tech_group"])
    state.plant_candidate_overrides[key] = new_k

    # Update the bubble chart for this candidate
    chart_el = document.getElementById(f"candidateChart{idx}")
    if chart_el:
        chart_el.innerHTML = _candidate_svg(g.get("plant_data", []), new_k)

    regenerate_plant_yaml_with_overrides()


def regenerate_plant_yaml_with_overrides():
    """Rebuild plant YAML applying any per-candidate cluster count overrides."""
    if not state.plant_groups or state.plant_cluster_settings is None:
        return

    # Apply overrides to a working copy of the groups
    groups = [dict(g) for g in state.plant_groups]
    for (model_region, tech_group), new_k in state.plant_candidate_overrides.items():
        for g in groups:
            if g["model_region"] == model_region and g["tech_group"] == tech_group:
                g["num_clusters"] = new_k
                break

    # Recompute tech-level defaults (minimum assigned count per tech across regions)
    tech_to_counts = {}
    for g in groups:
        tech_to_counts.setdefault(g["tech_group"], []).append(g["num_clusters"])

    defaults = {}
    for tech, counts in tech_to_counts.items():
        min_count = min(counts) if counts else 1
        defaults[tech] = int(min_count if min_count > 1 else 1)

    # Region-specific overrides are entries that differ from the tech default
    overrides = {}
    for g in groups:
        d = defaults[g["tech_group"]]
        if g["num_clusters"] != d:
            overrides.setdefault(g["model_region"], {})[g["tech_group"]] = int(
                g["num_clusters"]
            )

    overrides_sorted = {
        region: {tech: int(val) for tech, val in sorted(tech_map.items())}
        for region, tech_map in sorted(overrides.items())
    }

    # Preserve group_technologies and tech_groups from the original run
    group_flag = state.plant_cluster_settings.get("group_technologies", True)
    tech_groups = state.plant_cluster_settings.get("tech_groups", {})

    output = {
        "num_clusters": {tech: int(val) for tech, val in sorted(defaults.items())},
        "group_technologies": bool(group_flag),
        "tech_groups": tech_groups,
        "alt_num_clusters": overrides_sorted,
    }

    yaml_str = yaml.dump(output, default_flow_style=False, sort_keys=False)

    yaml_el = document.getElementById("plantYamlOut")
    if yaml_el is not None:
        yaml_el.value = yaml_str

    try:
        state.plant_cluster_settings = yaml.safe_load(yaml_str)
    except Exception:
        pass


# Export candidate cluster change handler to JavaScript
window.onCandidateClusterChange = create_proxy(on_candidate_cluster_change)


# --------------------------------------------------------------------------
# Interactive tech grouping UI helpers
# --------------------------------------------------------------------------


def get_normalized_techs(omit_tokens=None):
    """Return sorted list of normalized technologies from the plant data."""
    if state.plants_df is None:
        return []

    techs = set()
    for tech in state.plants_df.get("technology", []):
        normalized = normalize_technology(tech, omit_tokens=omit_tokens)
        if normalized:
            techs.add(normalized)
    return sorted(techs)


def get_selected_omit_tokens():
    """Return list of technologies currently marked for omission."""
    # Prefer state cache populated by the dual-list UI
    if state.omit_selected:
        return sorted(state.omit_selected)

    selected_el = document.getElementById("omitSelectedList")
    tokens = []
    if selected_el and hasattr(selected_el, "options"):
        tokens = [opt.value for opt in selected_el.options]

    if not tokens:
        tokens = sorted(DEFAULT_OMIT_TOKENS)

    return tokens


def calculate_min_clusters_and_default():
    """Return (min_clusters, default_clusters) based on current tech grouping/omits."""
    group_checkbox = document.getElementById("groupTechDefault")
    group_enabled = group_checkbox.checked if group_checkbox else True
    omit_tokens = get_selected_omit_tokens()

    active_group_map = None
    if group_enabled:
        active_group_map = (
            clone_group_map(state.custom_tech_groups)
            if state.custom_tech_groups
            else clone_group_map(DEFAULT_TECH_GROUPS)
        )

    df = prepare_plants_dataframe(
        group_enabled=group_enabled,
        omit_tokens=omit_tokens,
        group_map=active_group_map,
    )

    # One cluster per (model_region, tech_group) is the floor
    min_clusters = int(df.groupby(["model_region", "tech_group"]).ngroups)
    default_clusters = max(1, math.ceil(min_clusters * 1.15))
    return min_clusters, default_clusters


def update_default_cluster_budget(event=None):
    """Compute minimum clusters and set the default budget to +15%."""
    budget_input = document.getElementById("plantBudget")
    helper_text = document.getElementById("plantBudgetInfo")
    try:
        min_clusters, default_clusters = calculate_min_clusters_and_default()
    except Exception:
        # Skip updates if data not ready
        return

    if budget_input:
        budget_input.value = str(default_clusters)

    if helper_text:
        helper_text.textContent = f"Minimum clusters: {min_clusters}. Default set to {default_clusters} (+15%)."


def ensure_current_group():
    """Ensure the currently selected group exists."""
    if state.current_group and state.current_group in state.custom_tech_groups:
        return
    if state.custom_tech_groups:
        state.current_group = sorted(state.custom_tech_groups.keys())[0]
    else:
        state.current_group = None


def reset_custom_groups(omit_tokens=None):
    """Reset custom grouping to defaults and recompute available tech list."""
    state.custom_tech_groups = clone_group_map(DEFAULT_TECH_GROUPS)
    state.current_group = sorted(state.custom_tech_groups.keys())[0]
    normalized = set(get_normalized_techs(omit_tokens=omit_tokens))
    grouped = set()
    for members in state.custom_tech_groups.values():
        grouped.update(members)
    state.available_techs = normalized - grouped
    render_group_editor()
    update_default_cluster_budget()


def clear_custom_groups(omit_tokens=None):
    """Clear all groupings; make all techs available."""
    state.custom_tech_groups = {}
    state.current_group = None
    state.available_techs = set(get_normalized_techs(omit_tokens=omit_tokens))
    render_group_editor()
    update_default_cluster_budget()


def render_group_editor():
    """Render dual-list grouping UI (available vs selected for current group)."""
    group_select = document.getElementById("groupSelectDual")
    avail_list = document.getElementById("availableList")
    group_list = document.getElementById("groupList")
    empty_notice = document.getElementById("groupEmptyNotice")

    ensure_current_group()

    # Populate group dropdown
    if group_select:
        group_select.innerHTML = "".join(
            [
                f"<option value='{html.escape(name)}' {'selected' if name == state.current_group else ''}>{html.escape(name)}</option>"
                for name in sorted(state.custom_tech_groups.keys())
            ]
        )

    # Show empty notice when no groups
    if empty_notice:
        empty_notice.style.display = "block" if not state.custom_tech_groups else "none"

    # Available list
    if avail_list:
        avail_list.innerHTML = "".join(
            [
                f"<option value='{tech}'>{html.escape(tech)}</option>"
                for tech in sorted(state.available_techs)
            ]
        )

    # Current group list
    if group_list:
        members = (
            state.custom_tech_groups.get(state.current_group, set())
            if state.current_group
            else set()
        )
        group_list.innerHTML = "".join(
            [
                f"<option value='{tech}'>{html.escape(tech)}</option>"
                for tech in sorted(members)
            ]
        )


def render_omit_editor():
    """Render dual-list UI for selecting omitted technologies."""
    avail_el = document.getElementById("omitAvailableList")
    selected_el = document.getElementById("omitSelectedList")

    if state.plants_df is None or (avail_el is None and selected_el is None):
        return

    # Initialize omit sets if empty
    if not state.omit_selected and not state.omit_available:
        all_techs = set(get_normalized_techs(omit_tokens=[]))
        default_selected = {
            tech
            for tech in all_techs
            if any(tok in tech.lower() for tok in DEFAULT_OMIT_TOKENS)
        }
        state.omit_selected = default_selected
        state.omit_available = all_techs - default_selected

    if avail_el:
        avail_el.innerHTML = "".join(
            [
                f"<option value='{html.escape(tech)}'>{html.escape(tech)}</option>"
                for tech in sorted(state.omit_available)
            ]
        )

    if selected_el:
        selected_el.innerHTML = "".join(
            [
                f"<option value='{html.escape(tech)}'>{html.escape(tech)}</option>"
                for tech in sorted(state.omit_selected)
            ]
        )


def on_omit_move_to_selected(event):
    """Move technologies from available to omitted list."""
    avail_el = document.getElementById("omitAvailableList")
    if not avail_el:
        return
    chosen = [opt.value for opt in avail_el.selectedOptions]
    if not chosen:
        return
    state.omit_available -= set(chosen)
    state.omit_selected |= set(chosen)
    render_omit_editor()
    refresh_groups_for_omit_change()


def on_omit_move_to_available(event):
    """Move technologies from omitted back to available list."""
    selected_el = document.getElementById("omitSelectedList")
    if not selected_el:
        return
    chosen = [opt.value for opt in selected_el.selectedOptions]
    if not chosen:
        return
    state.omit_selected -= set(chosen)
    state.omit_available |= set(chosen)
    render_omit_editor()
    refresh_groups_for_omit_change()


def on_reset_omit_defaults(event=None):
    """Reset omitted technologies to defaults."""
    all_techs = set(get_normalized_techs(omit_tokens=[]))
    default_selected = {
        tech
        for tech in all_techs
        if any(tok in tech.lower() for tok in DEFAULT_OMIT_TOKENS)
    }
    state.omit_selected = default_selected
    state.omit_available = all_techs - default_selected
    render_omit_editor()
    refresh_groups_for_omit_change()


def on_add_group(event):
    name_input = document.getElementById("newGroupName")
    omit_tokens = get_selected_omit_tokens()
    if not name_input:
        return
    group_name = name_input.value.strip()
    if not group_name:
        return
    if group_name not in state.custom_tech_groups:
        state.custom_tech_groups[group_name] = set()
    state.current_group = group_name
    name_input.value = ""
    # Ensure available list is up to date
    state.available_techs = set(get_normalized_techs(omit_tokens=omit_tokens))
    for members in state.custom_tech_groups.values():
        state.available_techs -= members
    render_group_editor()
    update_default_cluster_budget()


def on_add_tech_to_group(event):
    avail_list = document.getElementById("availableList")
    if not avail_list:
        return
    ensure_current_group()
    if not state.current_group:
        return
    selected = [opt.value for opt in avail_list.selectedOptions]
    if not selected:
        return
    state.custom_tech_groups.setdefault(state.current_group, set()).update(selected)
    state.available_techs -= set(selected)
    render_group_editor()
    update_default_cluster_budget()


def on_remove_tech_from_group(event):
    group_list = document.getElementById("groupList")
    if not group_list:
        return
    ensure_current_group()
    if not state.current_group:
        return
    selected = [opt.value for opt in group_list.selectedOptions]
    if not selected:
        return
    for tech in selected:
        if tech in state.custom_tech_groups.get(state.current_group, set()):
            state.custom_tech_groups[state.current_group].remove(tech)
            state.available_techs.add(tech)
    render_group_editor()
    update_default_cluster_budget()


def on_group_change(event):
    select = document.getElementById("groupSelectDual")
    if select:
        state.current_group = select.value or None
    render_group_editor()


def on_reset_groups(event):
    omit_tokens = get_selected_omit_tokens()
    reset_custom_groups(omit_tokens=omit_tokens)


def on_clear_groups(event):
    omit_tokens = get_selected_omit_tokens()
    clear_custom_groups(omit_tokens=omit_tokens)


def refresh_groups_for_omit_change(event=None):
    """Recompute available techs when omit setting changes."""
    omit_tokens = get_selected_omit_tokens()
    normalized = set(get_normalized_techs(omit_tokens=omit_tokens))
    # Keep omit state in sync with current tech universe
    all_techs = set(get_normalized_techs(omit_tokens=[]))
    state.omit_selected = {t for t in state.omit_selected if t in all_techs}
    state.omit_available = all_techs - state.omit_selected
    render_omit_editor()
    # Keep existing assignments if still valid
    for group, members in list(state.custom_tech_groups.items()):
        state.custom_tech_groups[group] = {m for m in members if m in normalized}
    assigned = (
        set().union(*state.custom_tech_groups.values())
        if state.custom_tech_groups
        else set()
    )
    state.available_techs = normalized - assigned
    ensure_current_group()
    render_group_editor()
    update_default_cluster_budget()


def on_run_plant_clustering(event):
    """Handle plant clustering run."""
    try:
        budget_val = int(document.getElementById("plantBudget").value)
        cap_thresh = float(document.getElementById("capThreshold").value)
        hr_thresh = float(document.getElementById("hrThreshold").value)
        group_checkbox = document.getElementById("groupTechDefault")
        omit_tokens = get_selected_omit_tokens()
        group_enabled = group_checkbox.checked if group_checkbox else True

        active_group_map = None
        if group_enabled:
            # If user customized groups, prefer those; otherwise fall back to defaults
            active_group_map = (
                clone_group_map(state.custom_tech_groups)
                if state.custom_tech_groups
                else clone_group_map(DEFAULT_TECH_GROUPS)
            )

        yaml_str, total_clusters, effective_budget = suggest_plant_clusters(
            budget=budget_val,
            cap_threshold=cap_thresh,
            hr_iqr_threshold=hr_thresh,
            group_enabled=group_enabled,
            omit_tokens=omit_tokens,
            group_map=active_group_map,
        )

        try:
            state.plant_cluster_settings = yaml.safe_load(yaml_str)
        except Exception:
            state.plant_cluster_settings = None
    except Exception as exc:
        set_status(f"Plant clustering error: {exc}", "error")
        result_el = document.getElementById("plantResultText")
        if result_el:
            result_el.textContent = f"Plant clustering error: {exc}"
            result_el.className = "status error"
        return

    yaml_el = document.getElementById("plantYamlOut")
    if yaml_el is not None:
        yaml_el.value = yaml_str

    render_plant_candidates()

    note = ""
    if effective_budget > budget_val:
        note = " (budget raised to minimum needed for one cluster per tech/region)"

    result_el = document.getElementById("plantResultText")
    if result_el:
        result_el.textContent = f"Plant clustering ready: {total_clusters} clusters across technologies{note}."
        result_el.className = "status success"

    set_status(
        f"Plant clustering ready: {total_clusters} clusters across techs{note}.",
        "success",
    )


# =========================================================================
# Settings Generation (Settings tab)
# =========================================================================


async def load_atb_options():
    """Load ATB new-build options index (if present).

    Expected to live at web/data/atb_options.parquet. This is designed to be
    regenerated offline from technology_costs_atb.parquet; the web app only
    consumes the index. Columns: data_year, technology, tech_detail, cost_case.
    """
    try:
        df = await _fetch_parquet_df("./data/atb_options.parquet")

        # Normalize to list of dicts containing at least data_year/technology/tech_detail/cost_case
        required_cols = {"data_year", "technology", "tech_detail", "cost_case"}
        if not required_cols.issubset(set(df.columns)):
            state.atb_options = []
            state.atb_index = {}
            state.atb_years = []
            return

        df["data_year"] = df["data_year"].astype(int)
        df["technology"] = df["technology"].astype(str).str.strip()
        df["tech_detail"] = df["tech_detail"].astype(str).str.strip()
        df["cost_case"] = df["cost_case"].astype(str).str.strip()

        df = df[
            (df["technology"] != "")
            & (df["tech_detail"] != "")
            & (df["cost_case"] != "")
        ]

        normalized = df.to_dict(orient="records")

        state.atb_options = normalized
        years = sorted({int(r["data_year"]) for r in normalized})
        state.atb_years = years

        idx = {}
        for r in normalized:
            try:
                year = int(r["data_year"])
            except Exception:
                continue
            tech = str(r.get("technology", "")).strip()
            detail = str(r.get("tech_detail", "")).strip()
            case = str(r.get("cost_case", "")).strip()
            if not tech or not detail or not case:
                continue
            idx.setdefault(year, {}).setdefault(tech, {}).setdefault(detail, set()).add(
                case
            )

        # Convert sets to sorted lists
        state.atb_index = {
            y: {
                t: {d: sorted(list(cases)) for d, cases in details.items()}
                for t, details in techs.items()
            }
            for y, techs in idx.items()
        }
    except Exception:
        state.atb_options = []
        state.atb_index = {}
        state.atb_years = []


async def load_atb_size():
    """Load ATB technology sizes (if present).

    Expected to live at web/data/atb_size.json. Maps data_year to a dict of
    (technology, tech_detail) pairs to representative plant sizes in MW.
    """
    try:
        response = await fetch("./data/atb_size.json")
        if not response.ok:
            state.atb_size_map = {}
            return

        txt = await response.text()
        payload = json.loads(txt)
        sizes = payload.get("size", []) if isinstance(payload, dict) else []

        size_map = {}
        for row in sizes:
            if not isinstance(row, dict):
                continue
            tech = str(row.get("technology", "")).strip()
            if not tech:
                continue

            size_mw = row.get("size")
            if size_mw is None:
                continue

            data_year = row.get("data_year")
            year_map = size_map.setdefault(data_year, {})

            tech_detail = row.get("tech_detail")
            if tech_detail:
                # Store with tech_detail
                key = (tech, str(tech_detail).strip())
                year_map[key] = float(size_mw)
            else:
                # Store fallback without tech_detail
                key = (tech, None)
                year_map[key] = float(size_mw)

        state.atb_size_map = size_map
    except Exception:
        state.atb_size_map = {}


async def load_fuel_prices():
    """Load fuel scenario options for the Settings tab.

    Tries a local `./data/fuel_prices.csv` first, then falls back to PowerGenome-data.
    Only scenario availability is used (data_year/fuel/scenario), not prices.
    """
    for url in FUEL_PRICES_URLS:
        try:
            response = await fetch(url)
            if not response.ok:
                continue
            txt = await response.text()
            if txt.startswith("<!"):
                continue
            df = pd.read_csv(StringIO(txt))
            # Must include these columns
            required = {"data_year", "fuel", "scenario"}
            if not required.issubset({c.lower() for c in df.columns}):
                # Normalize case then check
                lower_map = {c.lower(): c for c in df.columns}
                if not required.issubset(set(lower_map.keys())):
                    continue
                df = df.rename(
                    columns={
                        lower_map["data_year"]: "data_year",
                        lower_map["fuel"]: "fuel",
                        lower_map["scenario"]: "scenario",
                    }
                )
            else:
                # Normalize to expected names while preserving existing casing
                lower_map = {c.lower(): c for c in df.columns}
                df = df.rename(
                    columns={
                        lower_map.get("data_year", "data_year"): "data_year",
                        lower_map.get("fuel", "fuel"): "fuel",
                        lower_map.get("scenario", "scenario"): "scenario",
                    }
                )

            df["data_year"] = pd.to_numeric(df["data_year"], errors="coerce").astype(
                "Int64"
            )
            df["fuel"] = df["fuel"].astype(str).str.strip()
            df["scenario"] = df["scenario"].astype(str).str.strip()
            df = df.dropna(subset=["data_year"])
            df = df[(df["fuel"] != "") & (df["scenario"] != "")]

            state.fuel_prices_df = df
            state.fuel_scenario_index = build_fuel_scenario_index(df)
            return
        except Exception:
            continue

    state.fuel_prices_df = None
    state.fuel_scenario_index = {}


def build_fuel_scenario_index(df: pd.DataFrame) -> dict:
    """Build index: data_year -> fuel -> sorted scenarios."""
    idx: dict[int, dict[str, list[str]]] = {}
    if df is None or df.empty:
        return idx

    for (data_year, fuel), sub in df.groupby(["data_year", "fuel"]):
        try:
            y = int(data_year)
        except Exception:
            continue
        scenarios = sorted(
            set(sub["scenario"].dropna().astype(str).str.strip().tolist())
        )
        scenarios = [s for s in scenarios if s]
        if not scenarios:
            continue
        idx.setdefault(y, {})[str(fuel)] = scenarios

    return idx


async def load_esr_data():
    """Load ESR-related CSV files. Called when user accesses ESR step."""
    try:
        update_loading_text("Loading ESR data...")

        # Load RPS data
        response = await fetch("./data/state_policies/rps_fraction.csv")
        if response.ok:
            rps_text = await response.text()
            state.rps_df = pd.read_csv(StringIO(rps_text))
            state.rps_df["st"] = state.rps_df["st"].str.lower()
            # Column is 't' not 'year'
            if "t" in state.rps_df.columns:
                state.rps_df = state.rps_df.rename(columns={"t": "year"})
            state.rps_df["year"] = pd.to_numeric(
                state.rps_df["year"], errors="coerce"
            ).astype("Int64")

        # Load CES data
        response = await fetch("./data/state_policies/ces_fraction.csv")
        if response.ok:
            ces_text = await response.text()
            state.ces_df = pd.read_csv(StringIO(ces_text))
            state.ces_df["st"] = state.ces_df["st"].str.lower()
            # Column might be '*t' or 't' - rename to 'year'
            if "*t" in state.ces_df.columns:
                state.ces_df = state.ces_df.rename(columns={"*t": "year"})
            elif "t" in state.ces_df.columns:
                state.ces_df = state.ces_df.rename(columns={"t": "year"})
            state.ces_df["year"] = pd.to_numeric(
                state.ces_df["year"], errors="coerce"
            ).astype("Int64")

        # Load rectable (trading rules)
        response = await fetch("./data/state_policies/rectable.csv")
        if response.ok:
            rectable_text = await response.text()
            state.rectable_df = pd.read_csv(StringIO(rectable_text), index_col=0)

        # Load population fractions
        response = await fetch("./data/state_policies/state-pop-fraction.csv")
        if response.ok:
            pop_text = await response.text()
            state.pop_fraction_df = pd.read_csv(StringIO(pop_text))
            state.pop_fraction_df["st"] = state.pop_fraction_df["st"].str.lower()
            state.pop_fraction_df["region"] = (
                state.pop_fraction_df["region"].astype(str).str.lower()
            )

        # Load allowed techs
        response = await fetch("./data/state_policies/allowed_techs.csv")
        if response.ok:
            allowed_text = await response.text()
            state.allowed_techs_df = pd.read_csv(StringIO(allowed_text))

    except Exception as e:
        raise Exception(f"Error loading ESR data: {e}")


def _set_select_options_simple(
    select_el, values, *, selected_value=None, empty_label=None
):
    if not select_el:
        return
    vals = [str(v) for v in (values or []) if str(v).strip()]
    if not vals and empty_label:
        vals = [empty_label]
    if selected_value is None and vals:
        selected_value = vals[0]

    parts = []
    for v in vals:
        sel = "selected" if str(v) == str(selected_value) else ""
        parts.append(
            f"<option value='{html.escape(str(v))}' {sel}>{html.escape(str(v))}</option>"
        )
    select_el.innerHTML = "".join(parts)


def _default_scenario_for_fuel(fuel: str, scenarios: list[str]) -> str | None:
    scenarios_set = {str(s) for s in (scenarios or [])}
    if fuel == "coal" and "no_111d" in scenarios_set:
        return "no_111d"
    if "reference" in scenarios_set:
        return "reference"
    elif "baseline" in scenarios_set:
        return "baseline"
    return scenarios[0] if scenarios else None


def populate_fuel_scenario_selects(event=None):
    """Dynamically populate fuel scenario rows based on the selected fuel data year.

    Reads the set of fuels available for the selected year from
    ``state.fuel_scenario_index`` and rebuilds the ``fuelScenariosContainer``
    with one row per fuel (select + mini-chart).  New fuels introduced in a
    future AEO release (e.g. hydrogen) will appear automatically without any
    code changes.
    """
    # Human-readable labels for well-known fuel keys; unknown fuels are
    # capitalised as a reasonable fallback.
    FUEL_LABELS: dict[str, str] = {
        "coal": "Coal",
        "naturalgas": "Natural gas",
        "distillate": "Distillate",
        "uranium": "Uranium",
        "hydrogen": "Hydrogen",
    }
    # Preferred display order for standard fuels; any extra fuels sort after.
    STANDARD_ORDER = ["coal", "naturalgas", "distillate", "uranium"]

    year_el = document.getElementById("fuelDataYear")
    help_el = document.getElementById("fuelScenarioHelp")
    container = document.getElementById("fuelScenariosContainer")

    try:
        selected_year = int(_get_select_value(year_el, 0) or 0)
    except Exception:
        selected_year = 0

    if not state.fuel_scenario_index or selected_year not in state.fuel_scenario_index:
        year_map = {f: ["reference"] for f in STANDARD_ORDER}
        if help_el:
            help_el.textContent = (
                "Fuel scenario options not available for this year; using 'reference'."
            )
    else:
        year_map = state.fuel_scenario_index.get(selected_year, {})
        if help_el:
            coal_scenarios = year_map.get("coal", [])
            if "no_111d" in set(coal_scenarios):
                help_el.textContent = (
                    "Coal defaults to 'no_111d' for this year (available)."
                )
            else:
                help_el.textContent = "Coal 'no_111d' not available for this year; defaulting to 'reference'."

    # Stable ordering: standard fuels first, then any extras alphabetically.
    extras = sorted(k for k in year_map if k not in STANDARD_ORDER)
    ordered_fuels = [f for f in STANDARD_ORDER if f in year_map] + extras

    if container:
        # Preserve existing selections before rebuilding the container.
        existing_selections: dict[str, str | None] = {}
        for fuel_key in ordered_fuels:
            sel_el = document.getElementById(f"fuelScenario_{fuel_key}")
            if sel_el:
                existing_selections[fuel_key] = _get_select_value(sel_el, None)

        # Rebuild inner HTML with one row per fuel.
        rows_html = []
        for fuel_key in ordered_fuels:
            label_text = FUEL_LABELS.get(fuel_key, fuel_key.capitalize())
            sel_id = f"fuelScenario_{fuel_key}"
            chart_id = f"fuelChart_{fuel_key}"
            rows_html.append(
                f'<div class="fuel-scenario-row">'
                f'<div class="fuel-scenario-control">'
                f'<label style="font-size: 12px;">{label_text}</label>'
                f'<select id="{sel_id}"></select>'
                f"</div>"
                f'<div id="{chart_id}" class="fuel-price-chart"></div>'
                f"</div>"
            )
        container.innerHTML = "".join(rows_html)

        # Populate each select and attach a change listener for the chart.
        for fuel_key in ordered_fuels:
            scenarios = year_map.get(fuel_key, ["reference"])
            current = existing_selections.get(fuel_key)
            default_val = _default_scenario_for_fuel(fuel_key, scenarios)
            chosen = current if current in scenarios else default_val
            sel_el = document.getElementById(f"fuelScenario_{fuel_key}")
            if sel_el:
                _set_select_options_simple(sel_el, scenarios, selected_value=chosen)
                sel_el.addEventListener(
                    "change", create_proxy(render_fuel_price_charts)
                )

    render_fuel_price_charts()


def _build_fuel_chart_data(data_year: int) -> dict:
    """Build chart data for a given data_year.

    Expects ``state.fuel_prices_df`` to have columns: data_year, fuel, scenario,
    year (planning year), price, and optionally region and dollar_year.

    Returns a dict: fuel -> scenario -> list of (year, avg_price) sorted by year,
    where avg_price is the mean price across regions for that planning year.
    Returns an empty dict if no data is available.
    """
    df = state.fuel_prices_df
    if df is None or df.empty:
        return {}

    # Filter to selected data_year
    mask = df["data_year"] == data_year
    sub = df[mask].copy()
    if sub.empty:
        return {}

    # Need year and price columns
    if "year" not in sub.columns or "price" not in sub.columns:
        return {}

    sub["year"] = pd.to_numeric(sub["year"], errors="coerce")
    sub["price"] = pd.to_numeric(sub["price"], errors="coerce")
    sub = sub.dropna(subset=["year", "price"])
    if sub.empty:
        return {}

    result: dict = {}
    for (fuel, scenario), grp in sub.groupby(["fuel", "scenario"]):
        # Average price across regions for each planning year
        avg_by_year = grp.groupby("year")["price"].mean().reset_index()
        avg_by_year = avg_by_year.sort_values("year")
        pts = [
            (int(row["year"]), float(row["price"])) for _, row in avg_by_year.iterrows()
        ]
        if pts:
            result.setdefault(str(fuel), {})[str(scenario)] = pts
    return result


def _render_fuel_price_chart_svg(fuel_data: dict, selected_scenario: str | None) -> str:
    """Render a minimal SVG line chart for a single fuel.

    Args:
        fuel_data: dict mapping scenario name -> list of (year, price) tuples
            sorted by year.  All scenarios sharing this chart.
        selected_scenario: the currently selected scenario name, drawn in blue
            on top; all others are drawn in light gray.

    Returns:
        SVG markup string (320×65 px), or empty string if ``fuel_data`` is empty.
    """
    if not fuel_data:
        return ""

    width = 320
    height = 65
    ml = 36  # margin left (for y-axis labels)
    mr = 6  # margin right
    mt = 6  # margin top
    mb = 18  # margin bottom (for x-axis labels)
    pw = width - ml - mr
    ph = height - mt - mb

    # Collect all points to determine axis ranges
    all_years = []
    all_prices = []
    for pts in fuel_data.values():
        for yr, pr in pts:
            all_years.append(yr)
            all_prices.append(pr)

    if not all_years:
        return ""

    x_min = min(all_years)
    x_max = max(all_years)
    y_min = min(all_prices)
    y_max = max(all_prices)

    # Pad y-range slightly
    y_range = y_max - y_min
    if y_range < 1e-6:
        y_min = max(0.0, y_min - 1.0)
        y_max = y_max + 1.0
        y_range = y_max - y_min
    else:
        pad = y_range * 0.08
        y_min = max(0.0, y_min - pad)
        y_max = y_max + pad
        y_range = y_max - y_min

    x_range = max(1, x_max - x_min)

    def to_x(yr):
        return ml + (yr - x_min) / x_range * pw

    def to_y(pr):
        return mt + ph - (pr - y_min) / y_range * ph

    svg = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="Fuel price scenarios" style="display:block;">',
        # Axes
        f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="#ccc" stroke-width="1"/>',
        f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" stroke="#ccc" stroke-width="1"/>',
    ]

    # Y-axis labels (min and max)
    svg.append(
        f'<text x="{ml - 3}" y="{mt + ph}" text-anchor="end" font-size="9" fill="#666">'
        f"{y_min:.1f}</text>"
    )
    svg.append(
        f'<text x="{ml - 3}" y="{mt + 6}" text-anchor="end" font-size="9" fill="#666">'
        f"{y_max:.1f}</text>"
    )

    # X-axis labels (first and last year)
    svg.append(
        f'<text x="{ml}" y="{mt + ph + 11}" text-anchor="middle" font-size="9" fill="#666">'
        f"{x_min}</text>"
    )
    if x_max != x_min:
        svg.append(
            f'<text x="{ml + pw}" y="{mt + ph + 11}" text-anchor="end" font-size="9" fill="#666">'
            f"{x_max}</text>"
        )

    # Draw scenario lines — non-selected first (background), selected last (foreground)
    SELECTED_COLOR = "#1a56c4"  # --blue
    GRAY = "#c8cdd8"
    SELECTED_WIDTH = "2"
    GRAY_WIDTH = "1.25"

    scenarios_sorted = sorted(
        fuel_data.keys(),
        key=lambda s: (0 if s == selected_scenario else 1),
        reverse=True,  # non-selected first
    )
    for scenario in scenarios_sorted:
        pts = fuel_data[scenario]
        if len(pts) < 1:
            continue
        is_sel = scenario == selected_scenario
        color = SELECTED_COLOR if is_sel else GRAY
        stroke_w = SELECTED_WIDTH if is_sel else GRAY_WIDTH
        opacity = "1" if is_sel else "0.85"

        if len(pts) == 1:
            # Single point: draw a dot
            cx = to_x(pts[0][0])
            cy = to_y(pts[0][1])
            svg.append(
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="2.5" fill="{color}" opacity="{opacity}"/>'
            )
        else:
            coords = " ".join(f"{to_x(yr):.2f},{to_y(pr):.2f}" for yr, pr in pts)
            title = html.escape(scenario)
            svg.append(
                f'<polyline points="{coords}" fill="none" stroke="{color}" '
                f'stroke-width="{stroke_w}" stroke-linejoin="round" '
                f'stroke-linecap="round" opacity="{opacity}">'
                f"<title>{title}</title></polyline>"
            )

    svg.append("</svg>")
    return "".join(svg)


def render_fuel_price_charts(event=None):
    """Update fuel price mini-charts for all fuels currently in the container.

    Reads the selected fuel data year and each fuel scenario select from
    ``fuelScenariosContainer``, builds SVG line charts via
    ``_render_fuel_price_chart_svg``, and injects them into the corresponding
    chart container elements.

    Safe to call with an optional event argument (e.g., as a DOM event handler).
    """
    year_el = document.getElementById("fuelDataYear")
    try:
        selected_year = int(_get_select_value(year_el, 0) or 0)
    except (ValueError, TypeError):
        selected_year = 0

    chart_data = _build_fuel_chart_data(selected_year)

    container = document.getElementById("fuelScenariosContainer")
    if not container:
        return

    # Discover all fuel selects rendered by populate_fuel_scenario_selects.
    for sel_el in container.querySelectorAll("select"):
        sel_id = sel_el.id
        if not sel_id.startswith("fuelScenario_"):
            continue
        fuel_key = sel_id[len("fuelScenario_") :]
        chart_id = f"fuelChart_{fuel_key}"
        chart_el = document.getElementById(chart_id)
        if not chart_el:
            continue
        selected_scenario = _get_select_value(sel_el, None)
        fuel_data = chart_data.get(fuel_key, {})
        svg = _render_fuel_price_chart_svg(fuel_data, selected_scenario)
        chart_el.innerHTML = svg


def _get_default_cost_case(cases):
    """Get default cost case, preferring 'Moderate' if available, otherwise first in list."""
    if not cases:
        return None
    if "Moderate" in cases:
        return "Moderate"
    return cases[0]


def populate_fuel_data_year_select(event=None):
    """Populate the Fuel Data Year dropdown from loaded fuel_prices.csv."""
    year_el = document.getElementById("fuelDataYear")
    if not year_el:
        return

    # Gather available years from loaded index
    if not state.fuel_scenario_index:
        # Fallback to a reasonable default when fuel_prices.csv can't be loaded
        _set_select_options_simple(year_el, [2026], selected_value="2026")
        return

    years = sorted(state.fuel_scenario_index.keys())
    current = _get_select_value(year_el, None)

    # Choose a default: keep current if valid; else latest.
    selected = None
    try:
        current_int = int(current) if current is not None else None
    except Exception:
        current_int = None

    if current_int in years:
        selected = current_int
    elif years:
        selected = years[-1]

    _set_select_options_simple(
        year_el, years, selected_value=str(selected) if selected is not None else None
    )


def _set_select_options(select_el, values, *, selected_value=None):
    if not select_el:
        return
    safe_values = [str(v) for v in values]
    if selected_value is None and safe_values:
        selected_value = safe_values[0]
    parts = []
    for v in safe_values:
        sel = "selected" if v == str(selected_value) else ""
        parts.append(
            f"<option value='{html.escape(v)}' {sel}>{html.escape(v)}</option>"
        )
    select_el.innerHTML = "".join(parts)


def _get_select_value(el, default=None):
    if not el:
        return default
    try:
        return el.value
    except Exception:
        return default


def _get_selected_atb_data_year():
    """Read the currently selected ATB data year from the picker."""
    year_el = document.getElementById("atbYearSelect")
    try:
        return int(_get_select_value(year_el, 0) or 0)
    except (ValueError, TypeError):
        return 0


def populate_atb_picker():
    """Populate the ATB picker selects in the Settings tab."""
    year_el = document.getElementById("atbYearSelect")
    tech_el = document.getElementById("atbTechSelect")
    detail_el = document.getElementById("atbTechDetailSelect")
    case_el = document.getElementById("atbCostCaseSelect")

    if not (year_el and tech_el and detail_el and case_el):
        return

    years = state.atb_years
    if not years:
        _set_select_options(year_el, ["(no ATB index found)"])
        _set_select_options(tech_el, [])
        _set_select_options(detail_el, [])
        _set_select_options(case_el, [])
        return

    latest_year = max(years)
    selected_year = int(_get_select_value(year_el, latest_year) or latest_year)
    if selected_year not in state.atb_index:
        selected_year = latest_year

    _set_select_options(year_el, years, selected_value=str(selected_year))

    techs = sorted(state.atb_index.get(selected_year, {}).keys())
    selected_tech = _get_select_value(tech_el, techs[0] if techs else None)
    if selected_tech not in techs and techs:
        selected_tech = techs[0]
    _set_select_options(tech_el, techs, selected_value=selected_tech)

    details = sorted(
        state.atb_index.get(selected_year, {}).get(selected_tech, {}).keys()
    )
    selected_detail = _get_select_value(detail_el, details[0] if details else None)
    if selected_detail not in details and details:
        selected_detail = details[0]
    _set_select_options(detail_el, details, selected_value=selected_detail)

    cases = (
        state.atb_index.get(selected_year, {})
        .get(selected_tech, {})
        .get(selected_detail, [])
    )
    selected_case = _get_select_value(case_el, _get_default_cost_case(cases))
    if selected_case not in cases and cases:
        selected_case = _get_default_cost_case(cases)
    _set_select_options(case_el, cases, selected_value=selected_case)
    update_atb_ccs_cost_visibility()
    update_size_field_from_atb_size()
    populate_default_battery_attributes()


def on_atb_picker_change(event=None):
    populate_atb_picker()
    populate_mod_resource_pickers()


def populate_mod_resource_pickers():
    """Populate the 'Copy from' selects in the Modified Resources form using the ATB index."""
    tech_el = document.getElementById("modBaseTech")
    detail_el = document.getElementById("modBaseTechDetail")
    case_el = document.getElementById("modBaseCostCase")

    if not (tech_el and detail_el and case_el):
        return

    # Use the same year as the ATB picker
    year_el = document.getElementById("atbYearSelect")
    try:
        selected_year = int(_get_select_value(year_el, None) or 0)
    except Exception:
        selected_year = 0

    year_data = state.atb_index.get(selected_year, {})
    if not year_data:
        _set_select_options(tech_el, [])
        _set_select_options(detail_el, [])
        _set_select_options(case_el, [])
        return

    techs = sorted(year_data.keys())
    selected_tech = _get_select_value(tech_el, techs[0] if techs else None)
    if selected_tech not in techs and techs:
        selected_tech = techs[0]
    _set_select_options(tech_el, techs, selected_value=selected_tech)

    details = sorted(year_data.get(selected_tech, {}).keys())
    selected_detail = _get_select_value(detail_el, details[0] if details else None)
    if selected_detail not in details and details:
        selected_detail = details[0]
    _set_select_options(detail_el, details, selected_value=selected_detail)

    cases = year_data.get(selected_tech, {}).get(selected_detail, [])
    selected_case = _get_select_value(case_el, _get_default_cost_case(cases))
    if selected_case not in cases and cases:
        selected_case = _get_default_cost_case(cases)
    _set_select_options(case_el, cases, selected_value=selected_case)

    # Update size field to reflect the new ATB year/tech/detail selection
    update_mod_size_field_from_atb_size()


def on_mod_base_picker_change(event=None):
    populate_mod_resource_pickers()
    update_atb_ccs_cost_visibility()
    update_mod_size_field_from_atb_size()


def _lookup_atb_size(tech, detail, selected_year):
    """Lookup size_mw from atb_size_map for a given technology, detail, and year.

    Args:
        tech: Technology name (e.g., "NaturalGas")
        detail: Tech detail (e.g., "2-on-1 Combined Cycle (F-Frame)")
        selected_year: ATB year to lookup

    Returns:
        size_mw value if found, None otherwise.
    """
    # Resolve the year-specific size map; fall back to the first available year
    year_size_map = state.atb_size_map.get(selected_year)
    if year_size_map is None and state.atb_size_map:
        year_size_map = next(iter(state.atb_size_map.values()))
    if year_size_map is None:
        year_size_map = {}

    # Try to find size: first with (tech, detail), then with (tech, None)
    size_mw = None
    if detail:
        size_mw = year_size_map.get((tech, detail))
    if size_mw is None:
        size_mw = year_size_map.get((tech, None))

    return size_mw


def _format_size_for_field(size_mw):
    """Format a size_mw value for display in an input field.

    Args:
        size_mw: Numeric size value or None

    Returns:
        String representation suitable for input field value.
    """
    if size_mw is None:
        return "100"

    # For sub-1 MW sizes, preserve the decimal value instead of truncating to 0.
    # For >=1 MW, use a rounded integer representation.
    if size_mw < 1:
        return str(size_mw)
    else:
        return str(int(round(size_mw)))


def update_size_field_from_atb_size():
    """Auto-populate Size (MW) field from atb_size.json based on selected technology and tech_detail."""
    size_el = document.getElementById("atbSizeMw")
    tech_el = document.getElementById("atbTechSelect")
    detail_el = document.getElementById("atbTechDetailSelect")
    year_el = document.getElementById("atbYearSelect")

    if not (size_el and tech_el and detail_el):
        return

    tech = _get_select_value(tech_el, "").strip()
    detail = _get_select_value(detail_el, "").strip()

    if not tech:
        return

    try:
        selected_year = int(_get_select_value(year_el, None) or 0)
    except Exception:
        selected_year = 0

    size_mw = _lookup_atb_size(tech, detail, selected_year)
    size_el.value = _format_size_for_field(size_mw)


def update_mod_size_field_from_atb_size():
    """Auto-populate Size (MW) field for modified resources from atb_size.json based on selected technology and tech_detail."""
    size_el = document.getElementById("modSizeMw")
    tech_el = document.getElementById("modBaseTech")
    detail_el = document.getElementById("modBaseTechDetail")
    year_el = document.getElementById("atbYearSelect")

    if not (size_el and tech_el and detail_el):
        return

    tech = _get_select_value(tech_el, "").strip()
    detail = _get_select_value(detail_el, "").strip()

    if not tech:
        return

    try:
        selected_year = int(_get_select_value(year_el, None) or 0)
    except Exception:
        selected_year = 0

    size_mw = _lookup_atb_size(tech, detail, selected_year)
    size_el.value = _format_size_for_field(size_mw)


def populate_default_battery_attributes():
    """Auto-populate default battery attributes in the override panel.

    For any battery/storage technology, sets default values for:
    - WACC real (0–1): 0.05 (ATB does not provide a WACC for batteries)

    For battery storage with Lithium Ion, also sets:
    - Variable O&M ($/MWh): 0.15
    - Variable O&M In ($/MWh): 0.15

    Clears these fields for non-battery technologies.
    """
    tech_el = document.getElementById("atbTechSelect")
    detail_el = document.getElementById("atbTechDetailSelect")

    if not (tech_el and detail_el):
        return

    tech = _get_select_value(tech_el, "").strip()
    detail = _get_select_value(detail_el, "").strip()

    # Get default modifiers
    defaults = _get_default_resource_modifiers(tech, detail)

    # Map internal/ATB keys to input element IDs
    atb_key_to_element_id = {
        "wacc_real": "atbOverrideWacc",
        "Var_OM_Cost_per_MWh": "atbOverrideVarOM",
        "Var_OM_Cost_per_MWh_In": "atbOverrideVarOMIn",
    }
    # Known default values used for clearing stale defaults when switching technology
    _default_values = {
        "wacc_real": str(_BATTERY_DEFAULT_WACC),
        "Var_OM_Cost_per_MWh": "0.15",
        "Var_OM_Cost_per_MWh_In": "0.15",
    }

    # First, clear any non-default values in battery fields
    for atb_key, elem_id in atb_key_to_element_id.items():
        elem = document.getElementById(elem_id)
        if elem and atb_key not in defaults:
            # Only clear if field is empty or contains a default value
            if not elem.value or elem.value.strip() in [
                "",
                _default_values.get(atb_key, ""),
            ]:
                elem.value = ""

    # Then populate fields that have defaults
    for atb_key, value in defaults.items():
        elem_id = atb_key_to_element_id.get(atb_key)
        if elem_id:
            elem = document.getElementById(elem_id)
            if elem:
                # Only set if the field is currently empty
                if not elem.value or elem.value.strip() == "":
                    elem.value = str(value)


def update_atb_ccs_cost_visibility():
    """Show CCS disposal cost input only for CCS technologies in ATB picker."""
    detail_el = document.getElementById("atbTechDetailSelect")
    row_el = document.getElementById("atbCcsCostRow")
    cost_el = document.getElementById("atbCcsDisposalCost")
    if not row_el:
        return
    detail = _get_select_value(detail_el, "")
    ccs_fraction = _extract_ccs_capture_fraction(detail)
    if ccs_fraction is None:
        row_el.style.display = "none"
        return
    row_el.style.display = "block"
    if cost_el:
        try:
            current = float(cost_el.value)
        except Exception:
            current = None
        if current is None:
            cost_el.value = str(state.ccs_disposal_cost)


def parse_int_list(text):
    """Parse comma/space-separated integers."""
    if text is None:
        return []
    raw = re.split(r"[\s,]+", str(text).strip())
    out = []
    for tok in raw:
        if not tok:
            continue
        out.append(int(tok))
    return out


def _get_model_years_from_dom():
    """Read model years from the DOM input field."""
    return parse_int_list(_get_select_value(document.getElementById("modelYears"), ""))


def populate_resource_year_selects():
    """Populate both planning-year dropdowns from Model Setup's model years.

    Called on model year changes and at initial load. Preserves current
    selection if still valid.
    """
    model_years = _get_model_years_from_dom()

    for sel_id in ("newResourceYearSelect", "modResourceYearSelect"):
        sel = document.getElementById(sel_id)
        if not sel:
            continue
        current = _get_select_value(sel, "all")
        options = ["All (default)"] + [str(y) for y in model_years]
        values = ["all"] + [str(y) for y in model_years]
        # Build option HTML manually to keep "all" as value for the display text
        parts = []
        for val, label in zip(values, options):
            selected = "selected" if val == current else ""
            parts.append(
                f"<option value='{html.escape(val)}' {selected}>{html.escape(label)}</option>"
            )
        sel.innerHTML = "".join(parts)


def _get_resource_planning_year(select_id):
    """Read the selected planning year from a resource year dropdown.

    Returns ``"all"`` or an ``int``.
    """
    val = _get_select_value(document.getElementById(select_id), "all")
    if val == "all":
        return "all"
    try:
        return int(val)
    except (ValueError, TypeError):
        return "all"


def _check_year_default_warning(tech, detail, case, planning_year, warning_el_id):
    """Show a soft warning if user adds a year-specific resource without an
    'All (default)' counterpart.  Returns the warning element (may be hidden).
    """
    warn_el = document.getElementById(warning_el_id)
    if not warn_el:
        return
    if planning_year == "all":
        warn_el.style.display = "none"
        return

    # Check state.new_resources for an "all" version of same tech+detail+case
    has_all = any(
        r["technology"] == tech
        and r["tech_detail"] == detail
        and r["cost_case"] == case
        and r.get("planning_year") == "all"
        for r in state.new_resources
    )
    # Also check modified_new_resources
    if not has_all:
        has_all = any(
            v.get("technology") == tech
            and v.get("tech_detail") == detail
            and v.get("cost_case") == case
            and v.get("planning_year") == "all"
            for v in state.modified_new_resources.values()
        )
    if not has_all:
        warn_el.innerHTML = (
            f"⚠ No <b>All (default)</b> entry for "
            f"<em>{html.escape(tech)} — {html.escape(detail)} — {html.escape(case)}</em>. "
            f"Other planning years will not include this resource unless you also add an "
            f"'All (default)' version."
        )
        warn_el.style.display = "block"
    else:
        warn_el.style.display = "none"


def _year_badge_html(planning_year):
    """Return an inline HTML badge for a planning year, or empty string for 'all'."""
    if planning_year == "all":
        return ""
    return (
        f" <span style='display: inline-block; background: #0d6efd; color: white; "
        f"font-size: 9px; padding: 1px 5px; border-radius: 8px; vertical-align: middle;'>"
        f"{planning_year}</span>"
    )


def _resource_row_keydown_handler(js_call: str) -> str:
    """Return an inline keydown handler for keyboard activation."""
    return (
        'if(event.key==="Enter"||event.key===" "||event.key==="Spacebar")'
        "{event.preventDefault();"
        f"{js_call};"
        "}"
    )


def render_new_resources_list():
    """Render both regular and modified (attribute-override) new resources together."""
    container = document.getElementById("newResourcesList")
    if not container:
        return

    # Use state.new_resources as the source of truth for regular resources
    regular_items = list(state.new_resources)

    # Get resources with attribute modifiers
    modified_items = []
    for key in sorted(state.modified_new_resources.keys()):
        item = state.modified_new_resources[key]
        # Only include items that are purely attribute modifiers (not custom fuels or identity changes)
        if (
            (item.get("fuel_type") == "standard" or item.get("fuel_type") == "none")
            and item.get("technology") == item.get("new_technology")
            and item.get("tech_detail") == item.get("new_tech_detail")
            and item.get("cost_case") == item.get("new_cost_case")
        ):
            attr_mods = item.get("attr_modifiers") or {}
            if attr_mods:  # Only show if it actually has attribute modifiers
                modified_items.append((key, item, attr_mods))

    if not regular_items and not modified_items:
        container.innerHTML = "<em>No new-build resources selected yet.</em>"
        return

    parts = []
    resource_attr_keys = [
        "capex_mw",
        "capex_mwh",
        "heat_rate",
        "fixed_o_m_mw",
        "variable_o_m_mwh",
        "variable_o_m_mwh_in",
        "wacc_real",
    ]

    # Render regular resources with delete buttons and year badges
    for idx, r in enumerate(regular_items):
        tech = r["technology"]
        detail = r["tech_detail"]
        case = r["cost_case"]
        size = r["size_mw"]
        planning_year = r.get("planning_year", "all")

        # Show inline attribute overrides for regular resources when present
        # (for example, default battery variable O&M values).
        inline_mods = []
        for attr in resource_attr_keys:
            if attr in r:
                inline_mods.append(f"{attr}={r[attr]}")
        inline_mod_text = ""
        row_style = (
            "display: flex; justify-content: space-between; align-items: center;"
        )
        if inline_mods:
            inline_mod_text = (
                " "
                f"<span style='color: #856404; font-size: 10px;'>"
                f"({html.escape('; '.join(inline_mods))})</span>"
            )
            row_style += " background-color: #fff3cd;"

        ccs_fraction = _extract_ccs_capture_fraction(detail)
        ccs_note = ""
        if ccs_fraction is not None:
            tech_name = f"{tech}_{detail}"
            ccs_cost = state.ccs_disposal_cost_map.get(
                tech_name, state.ccs_disposal_cost
            )
            ccs_note = (
                " "
                f"<span style='color: #3b4a3f; font-size: 10px;'>"
                f"(CCS disposal ${ccs_cost}/tCO2)</span>"
            )
        year_badge = _year_badge_html(planning_year)
        populate_call = f"window.populatePickerFromResource({idx})"
        keydown_handler = _resource_row_keydown_handler(populate_call)
        parts.append(
            f"<div class='candidate-item' style='{row_style} cursor: pointer;'"
            f" role='button' tabindex='0'"
            f" title='Click to load into ATB picker'"
            f" onclick='{populate_call}'"
            f" onkeydown='{keydown_handler}'>"
            f"<span><strong>{html.escape(str(tech))}</strong> — {html.escape(str(detail))} — {html.escape(str(case))} — {int(size)} MW{ccs_note}{inline_mod_text}{year_badge}</span>"
            f"<button onclick='event.stopPropagation(); window.deleteNewResource({idx})' style='padding: 2px 8px; font-size: 11px; background: #dc3545; color: white; border: none; border-radius: 3px; cursor: pointer;'>Delete</button>"
            f"</div>"
        )

    # Render modified resources (with attribute overrides) with delete buttons
    for key, item, attr_mods in modified_items:
        tech = item.get("technology")
        detail = item.get("tech_detail")
        case = item.get("cost_case")
        size = item.get("size_mw", 1)
        planning_year = item.get("planning_year", "all")

        # Build modifier summary
        mod_summary = []
        for attr, val in sorted(attr_mods.items()):
            if isinstance(val, list) and len(val) == 2:
                mod_summary.append(f"{attr}=[{val[0]}, {val[1]}]")
            else:
                mod_summary.append(f"{attr}={val}")
        mod_text = "; ".join(mod_summary[:3])  # Show first 3 modifiers
        if len(mod_summary) > 3:
            mod_text += f" (+{len(mod_summary)-3} more)"

        ccs_fraction = _extract_ccs_capture_fraction(detail)
        ccs_note = ""
        if ccs_fraction is not None:
            ccs_cost = item.get("ccs_disposal_cost", state.ccs_disposal_cost)
            ccs_note = (
                " "
                f"<span style='color: #3b4a3f; font-size: 10px;'>"
                f"(CCS disposal ${ccs_cost}/tCO2)</span>"
            )

        year_badge = _year_badge_html(planning_year)
        escaped_key = html.escape(key, quote=True)
        populate_call = f'window.populatePickerFromModifiedResource("{escaped_key}")'
        keydown_handler = _resource_row_keydown_handler(populate_call)
        parts.append(
            f"<div class='candidate-item' style='display: flex; justify-content: space-between; align-items: center; background-color: #fff3cd; cursor: pointer;'"
            f" role='button' tabindex='0'"
            f" title='Click to load into ATB picker'"
            f" onclick='{populate_call}'"
            f" onkeydown='{keydown_handler}'>"
            f"<span><strong>{html.escape(str(tech))}</strong> — {html.escape(str(detail))} — {html.escape(str(case))} — {int(size)} MW{ccs_note} "
            f"<span style='color: #856404; font-size: 10px;'>({html.escape(mod_text)})</span>{year_badge}</span>"
            f"<button onclick='event.stopPropagation(); window.deleteModifiedNewResource(\"{escaped_key}\")' style='padding: 2px 8px; font-size: 11px; background: #dc3545; color: white; border: none; border-radius: 3px; cursor: pointer;'>Delete</button>"
            f"</div>"
        )

    container.innerHTML = "".join(parts)


def _get_current_resources_atb_year():
    """Return the ATB data_year already in use across all selected resources, or None.

    Looks at both ``state.new_resources`` and ``state.modified_new_resources``.
    Returns the first ``data_year`` value found. As a best-effort fallback for
    legacy modified resources created before ``data_year`` was persisted, uses
    the current ATB picker year when modified resources exist but none record a
    year.
    """
    for r in state.new_resources:
        yr = r.get("data_year")
        if yr is not None:
            return yr
    has_modified_resources = False
    for r in state.modified_new_resources.values():
        has_modified_resources = True
        yr = r.get("data_year")
        if yr is not None:
            return yr
    if has_modified_resources:
        fallback_year = _get_selected_atb_data_year()
        if fallback_year:
            return fallback_year
    return None


def show_atb_year_conflict_overlay(existing_year, new_year):
    """Show the ATB data-year conflict overlay with context-specific message."""
    msg_el = document.getElementById("atbYearConflictMessage")
    if msg_el:
        msg_el.innerHTML = (
            f"All new resources must use the same ATB data year. "
            f"Your current resources use ATB <strong>{existing_year}</strong>, "
            f"but you are trying to add a resource from ATB <strong>{new_year}</strong>. "
            f"Please remove all current resources before adding resources from a different ATB data year."
        )
    overlay = document.getElementById("atbYearConflictOverlay")
    if overlay:
        overlay.classList.remove("hidden")
    ok_button = document.getElementById("atbYearConflictOkButton")
    if ok_button and hasattr(ok_button, "focus"):
        ok_button.focus()


def on_add_new_resource(event):
    tech_el = document.getElementById("atbTechSelect")
    detail_el = document.getElementById("atbTechDetailSelect")
    case_el = document.getElementById("atbCostCaseSelect")
    size_el = document.getElementById("atbSizeMw")

    tech = _get_select_value(tech_el, "").strip()
    detail = _get_select_value(detail_el, "").strip()
    case = _get_select_value(case_el, "").strip()
    try:
        size = int(float(_get_select_value(size_el, 1)))
    except Exception:
        size = 1

    if not tech or not detail or not case:
        set_status("ATB index not available; add manually below.", "error")
        return

    # Read ATB data year from the picker
    atb_data_year = _get_selected_atb_data_year()

    # Reject if the selected ATB data year differs from what is already in use.
    if atb_data_year:
        existing_year = _get_current_resources_atb_year()
        if existing_year is not None and existing_year != atb_data_year:
            show_atb_year_conflict_overlay(existing_year, atb_data_year)
            return

    # Read planning year from the dropdown
    planning_year = _get_resource_planning_year("newResourceYearSelect")

    # Reject duplicate (technology, tech_detail, planning_year) combinations.
    # A given tech+detail slot may only appear once per planning year (or once
    # for "all years") to avoid ambiguous cost-case assignments in the export.
    duplicate = any(
        r["technology"] == tech
        and r["tech_detail"] == detail
        and r.get("planning_year") == planning_year
        for r in state.new_resources
    ) or any(
        v.get("technology") == tech
        and v.get("tech_detail") == detail
        and v.get("planning_year") == planning_year
        for v in state.modified_new_resources.values()
    )
    if duplicate:
        year_label = planning_year if planning_year != "all" else "all years"
        set_status(
            f"{tech} — {detail} is already added for {year_label}. "
            f"Remove the existing entry before adding a different configuration.",
            "error",
        )
        return

    # Collect optional attribute overrides from the collapsible panel
    attr_overrides = {}
    override_fields = [
        ("capex_mw", "atbOverrideCapex"),
        ("capex_mwh", "atbOverrideCapexMwh"),
        ("heat_rate", "atbOverrideHeatRate"),
        ("fixed_o_m_mw", "atbOverrideFixedOM"),
        ("variable_o_m_mwh", "atbOverrideVarOM"),
        ("variable_o_m_mwh_in", "atbOverrideVarOMIn"),
        ("wacc_real", "atbOverrideWacc"),
    ]
    for attr, el_id in override_fields:
        el = document.getElementById(el_id)
        if el is None:
            continue

        # Get value safely and check if it's actually filled in
        try:
            raw_value = el.value
        except Exception:
            continue

        if raw_value is None or raw_value == "":
            continue

        value_str = str(raw_value).strip()
        if not value_str or value_str.lower() == "none":
            continue

        try:
            # Check if value starts with an operator (e.g., "add:100", "mul:1.1")
            if ":" in value_str:
                parts = value_str.split(":", 1)
                if len(parts) == 2:
                    op, val = parts[0].strip().lower(), parts[1].strip()
                    if op in ["add", "mul", "truediv", "sub"]:
                        attr_overrides[attr] = [op, float(val)]
                    else:
                        set_status(
                            f"Invalid operator for {attr}: '{op}'. Must be add, mul, truediv, or sub.",
                            "error",
                        )
                        return
                else:
                    raise ValueError("Invalid format")
            else:
                # Plain numeric value
                attr_overrides[attr] = float(value_str)
        except ValueError:
            set_status(f"Invalid value for {attr}: '{value_str}'", "error")
            return

    if attr_overrides:
        # Add as a modified resource so PowerGenome applies the attribute overrides
        key = _auto_modified_key(tech, detail)
        defaults = _ATB_TECH_DEFAULTS.get(
            tech, {"fuel": "naturalgas", "tag_class": "THERM", "is_commit": True}
        )
        fuel = defaults.get("fuel")
        tag_class = defaults.get("tag_class", "THERM")
        is_commit = defaults.get("is_commit", False)
        fuel_type = "standard" if fuel else "none"
        std_fuel = fuel  # None for non-thermal (VRE/STOR/HYDRO) resources
        fuel_desc = std_fuel if fuel_type == "standard" else "none"

        # Check if this is a CCS technology
        ccs_fraction = _extract_ccs_capture_fraction(detail)
        ccs_disposal_cost = None
        if ccs_fraction is not None:
            cost_el = document.getElementById("atbCcsDisposalCost")
            try:
                ccs_disposal_cost = float(_get_select_value(cost_el, ""))
            except Exception:
                ccs_disposal_cost = state.ccs_disposal_cost
            if ccs_disposal_cost < 0:
                set_status("CCS disposal cost must be >= 0", "error")
                return

        state.modified_new_resources[key] = {
            "technology": tech,
            "tech_detail": detail,
            "cost_case": case,
            "size_mw": size,
            "new_technology": tech,
            "new_tech_detail": detail,
            "new_cost_case": case,
            "attr_modifiers": attr_overrides,
            "fuel_type": fuel_type,
            "standard_fuel": std_fuel,
            "new_fuel_name": "",
            "new_fuel_price": 0.0,
            "new_fuel_emission_factor": 0.0,
            "tag_class": tag_class,
            "is_commit": is_commit,
            "fuel_desc": fuel_desc,
            "ccs_capture_fraction": ccs_fraction,
            "ccs_disposal_cost": ccs_disposal_cost,
            "planning_year": planning_year,
            "data_year": atb_data_year if atb_data_year else None,
        }
        render_modified_resources_list()
        render_new_resources_list()  # Also update the main list to show this resource

        # Show year-default warning if needed
        _check_year_default_warning(
            tech, detail, case, planning_year, "newResourceYearWarning"
        )

        # Clear override fields for next entry
        for attr, el_id in override_fields:
            el = document.getElementById(el_id)
            if el is not None:
                try:
                    el.value = ""
                except Exception:
                    pass

        n = len(attr_overrides)
        year_label = planning_year if planning_year != "all" else "all years"
        set_status(
            f"Added '{key}' as a modified resource with {n} attribute override(s) for {year_label}.",
            "info",
        )
    else:
        # Store in state.new_resources instead of directly in the textarea
        resource_entry = {
            "technology": tech,
            "tech_detail": detail,
            "cost_case": case,
            "size_mw": size,
            "planning_year": planning_year,
            "data_year": atb_data_year if atb_data_year else None,
        }
        ccs_fraction = _extract_ccs_capture_fraction(detail)
        if ccs_fraction is not None:
            cost_el = document.getElementById("atbCcsDisposalCost")
            try:
                ccs_disposal_cost = float(_get_select_value(cost_el, ""))
            except Exception:
                ccs_disposal_cost = state.ccs_disposal_cost
            if ccs_disposal_cost < 0:
                set_status("CCS disposal cost must be >= 0", "error")
                return
            tech_name = f"{tech}_{detail}"
            state.ccs_disposal_cost_map[tech_name] = ccs_disposal_cost

        state.new_resources.append(resource_entry)
        render_new_resources_list()

        # Show year-default warning if needed
        _check_year_default_warning(
            tech, detail, case, planning_year, "newResourceYearWarning"
        )

        year_label = planning_year if planning_year != "all" else "all years"
        set_status(
            f"Added {tech} — {detail} — {case} — {size} MW for {year_label}.",
            "info",
        )


def delete_new_resource(index):
    """Delete a regular new resource by index from ``state.new_resources``."""
    if 0 <= index < len(state.new_resources):
        state.new_resources.pop(index)
        render_new_resources_list()
        set_status("Resource deleted.", "success")
    else:
        set_status(f"Invalid resource index: {index}", "error")


def delete_modified_new_resource(key):
    """Delete a modified new resource with attribute overrides by key."""
    if key in state.modified_new_resources:
        del state.modified_new_resources[key]
        render_modified_resources_list()
        render_new_resources_list()  # Update main list too
        set_status(f"Deleted modified resource: {key}", "success")
    else:
        set_status(f"Resource not found: {key}", "error")


def delete_all_new_resources(event=None):
    """Remove all selected new-build resources (regular and modified)."""
    state.new_resources.clear()
    state.modified_new_resources.clear()
    # Also clear any CCS disposal cost overrides associated with new resources
    if (
        hasattr(state, "ccs_disposal_cost_map")
        and state.ccs_disposal_cost_map is not None
    ):
        state.ccs_disposal_cost_map.clear()
    render_modified_resources_list()
    render_new_resources_list()
    set_status("All new resources have been removed.", "success")


def reset_new_resources_for_tests():
    """Restore Step 4 to a deterministic baseline for shared Playwright pages."""
    state.new_resources = [dict(r) for r in _DEFAULT_NEW_RESOURCES]
    state.modified_new_resources.clear()
    if (
        hasattr(state, "ccs_disposal_cost_map")
        and state.ccs_disposal_cost_map is not None
    ):
        state.ccs_disposal_cost_map.clear()

    render_modified_resources_list()
    render_new_resources_list()

    for warning_id in ("newResourceYearWarning", "modResourceYearWarning"):
        warning_el = document.getElementById(warning_id)
        if warning_el:
            warning_el.innerHTML = ""
            warning_el.style.display = "none"

    overlay = document.getElementById("atbYearConflictOverlay")
    if overlay:
        overlay.classList.add("hidden")

    message_el = document.getElementById("atbYearConflictMessage")
    if message_el:
        message_el.innerHTML = ""

    populate_resource_year_selects()

    for select_id in (
        "atbYearSelect",
        "atbTechSelect",
        "atbTechDetailSelect",
        "atbCostCaseSelect",
        "modBaseTech",
        "modBaseTechDetail",
        "modBaseCostCase",
    ):
        select_el = document.getElementById(select_id)
        if select_el:
            try:
                select_el.value = ""
            except Exception:
                pass

    populate_atb_picker()
    populate_mod_resource_pickers()

    for field_id, value in (
        ("atbSizeMw", "100"),
        ("atbCcsDisposalCost", str(state.ccs_disposal_cost)),
        ("modSizeMw", "100"),
        ("modNewTech", ""),
        ("modNewTechDetail", ""),
        ("modNewFuelName", ""),
        ("modNewFuelPrice", "16"),
        ("modNewFuelEf", "0"),
        ("atbOverrideCapex", ""),
        ("atbOverrideCapexMwh", ""),
        ("atbOverrideHeatRate", ""),
        ("atbOverrideFixedOM", ""),
        ("atbOverrideVarOM", ""),
        ("atbOverrideVarOMIn", ""),
        ("atbOverrideWacc", ""),
        ("modOverrideCapexMw", ""),
        ("modOverrideCapexMwh", ""),
        ("modOverrideHeatRate", ""),
        ("modOverrideFixedOM", ""),
        ("modOverrideVarOM", ""),
        ("modOverrideVarOMIn", ""),
        ("modOverrideWacc", ""),
    ):
        field_el = document.getElementById(field_id)
        if field_el:
            try:
                field_el.value = value
            except Exception:
                pass

    details_el = document.getElementById("atbAttrsOverride")
    if details_el:
        details_el.open = False

    for select_id, value in (
        ("newResourceYearSelect", "all"),
        ("modResourceYearSelect", "all"),
        ("modFuelType", "standard"),
        ("modStandardFuel", "naturalgas"),
        ("modTagClass", "THERM"),
    ):
        select_el = document.getElementById(select_id)
        if select_el:
            try:
                select_el.value = value
            except Exception:
                pass

    commit_el = document.getElementById("modIsCommit")
    if commit_el:
        try:
            commit_el.checked = True
        except Exception:
            pass

    if hasattr(window, "toggleModFuelType"):
        window.toggleModFuelType()
    if hasattr(window, "toggleCommitRow"):
        window.toggleCommitRow()
    update_atb_ccs_cost_visibility()
    set_status("Step 4 test baseline restored.", "info")


def _populate_atb_picker_from_resource(
    tech, detail, case, planning_year, size, attr_overrides=None
):
    """Populate ATB picker dropdowns and override fields to match the given resource.

    Args:
        tech: Technology name (e.g., "NaturalGas").
        detail: Tech detail (e.g., "2-on-1 Combined Cycle (F-Frame)").
        case: Cost case (e.g., "Moderate").
        planning_year: Planning year value ("all" or an int/str year).
        size: Size in MW.
        attr_overrides: Optional dict of UI-key → value for attribute overrides.
            Values may be plain floats or [operator, float] lists.
    """
    if attr_overrides is None:
        attr_overrides = {}

    year_el = document.getElementById("atbYearSelect")
    tech_el = document.getElementById("atbTechSelect")
    detail_el = document.getElementById("atbTechDetailSelect")
    case_el = document.getElementById("atbCostCaseSelect")
    size_el = document.getElementById("atbSizeMw")
    year_select_el = document.getElementById("newResourceYearSelect")

    # Use the currently selected ATB data year (do not change it)
    try:
        selected_year = int(_get_select_value(year_el, None) or 0)
    except Exception:
        selected_year = 0
    if not selected_year and state.atb_years:
        selected_year = max(state.atb_years)

    year_data = state.atb_index.get(selected_year, {})

    # Rebuild and set the Technology dropdown with the target tech selected
    techs = sorted(year_data.keys())
    _set_select_options(tech_el, techs, selected_value=tech)

    # Rebuild and set the Tech Detail dropdown for the selected tech
    details = sorted(year_data.get(tech, {}).keys())
    _set_select_options(detail_el, details, selected_value=detail)

    # Rebuild and set the Cost Case dropdown for the selected tech+detail
    cases = year_data.get(tech, {}).get(detail, [])
    _set_select_options(case_el, cases, selected_value=case)

    # Set the size field
    if size_el:
        size_el.value = str(size)

    # Set the planning year dropdown to match the resource
    if year_select_el:
        try:
            year_select_el.value = str(planning_year)
        except Exception:
            pass

    # Attribute override field mapping (UI key → input element ID)
    attr_field_map = [
        ("capex_mw", "atbOverrideCapex"),
        ("capex_mwh", "atbOverrideCapexMwh"),
        ("heat_rate", "atbOverrideHeatRate"),
        ("fixed_o_m_mw", "atbOverrideFixedOM"),
        ("variable_o_m_mwh", "atbOverrideVarOM"),
        ("variable_o_m_mwh_in", "atbOverrideVarOMIn"),
        ("wacc_real", "atbOverrideWacc"),
    ]

    # Clear all override fields, then populate any that have values
    has_overrides = False
    for attr_key, elem_id in attr_field_map:
        elem = document.getElementById(elem_id)
        if not elem:
            continue
        val = attr_overrides.get(attr_key)
        if val is not None:
            has_overrides = True
            if isinstance(val, list) and len(val) == 2:
                elem.value = f"{val[0]}:{val[1]}"
            else:
                elem.value = str(val)
        else:
            elem.value = ""

    # Auto-expand the override panel when overrides are present
    if has_overrides:
        details_el = document.getElementById("atbAttrsOverride")
        if details_el:
            details_el.open = True

    update_atb_ccs_cost_visibility()
    set_status(
        f"Loaded \u2018{tech} \u2014 {detail} \u2014 {case}\u2019 into the ATB picker.",
        "info",
    )


def populate_picker_from_resource_index(idx):
    """Populate ATB picker from the resource at index *idx* in state.new_resources.

    Called from JavaScript via ``window.populatePickerFromResource(idx)`` when a
    user clicks a resource row in the new-resources list.
    """
    try:
        idx = int(idx)
    except Exception:
        return
    if idx < 0 or idx >= len(state.new_resources):
        return
    r = state.new_resources[idx]
    resource_attr_keys = [
        "capex_mw",
        "capex_mwh",
        "heat_rate",
        "fixed_o_m_mw",
        "variable_o_m_mwh",
        "variable_o_m_mwh_in",
        "wacc_real",
    ]
    attr_overrides = {k: r[k] for k in resource_attr_keys if k in r}
    _populate_atb_picker_from_resource(
        r["technology"],
        r["tech_detail"],
        r["cost_case"],
        r.get("planning_year", "all"),
        r["size_mw"],
        attr_overrides,
    )


def populate_picker_from_modified_resource_key(key):
    """Populate ATB picker from the modified resource with the given *key*.

    Called from JavaScript via ``window.populatePickerFromModifiedResource(key)``
    when a user clicks a modified resource row in the new-resources list.
    """
    item = state.modified_new_resources.get(str(key))
    if not item:
        return
    _populate_atb_picker_from_resource(
        item.get("technology", ""),
        item.get("tech_detail", ""),
        item.get("cost_case", ""),
        item.get("planning_year", "all"),
        item.get("size_mw", 1),
        item.get("attr_modifiers") or {},
    )


# Export delete functions to JavaScript (must be after function definitions)
window.deleteNewResource = create_proxy(delete_new_resource)
window.deleteModifiedNewResource = create_proxy(delete_modified_new_resource)
window.deleteAllNewResources = create_proxy(delete_all_new_resources)
window.resetNewResourcesForTests = create_proxy(reset_new_resources_for_tests)

# Export populate-picker functions to JavaScript
window.populatePickerFromResource = create_proxy(populate_picker_from_resource_index)
window.populatePickerFromModifiedResource = create_proxy(
    populate_picker_from_modified_resource_key
)


def render_modified_resources_list():
    """Render resources with custom fuels or technology identity changes.

    Resources with ONLY attribute modifiers (no fuel/identity changes) are shown
    in the main new_resources list instead.
    """
    container = document.getElementById("modifiedResourcesList")
    if not container:
        return

    # Filter to only show resources with custom fuels or identity changes
    to_display = []
    for key in sorted(state.modified_new_resources.keys()):
        item = state.modified_new_resources[key]
        # Show if: custom fuel, OR technology/detail/case changed
        if (
            item.get("fuel_type") == "new"
            or item.get("technology") != item.get("new_technology")
            or item.get("tech_detail") != item.get("new_tech_detail")
            or item.get("cost_case") != item.get("new_cost_case")
        ):
            to_display.append((key, item))

    if not to_display:
        container.innerHTML = (
            "<em>No modified resources with custom fuels or identity changes.</em>"
        )
        return

    parts = []
    for key, item in to_display:
        new_tech = item.get("new_technology")
        fuel_desc = item.get("fuel_desc", "")
        tag_class = item.get("tag_class", "")
        planning_year = item.get("planning_year", "all")

        # Build a summary of attribute modifiers
        attr_mods = item.get("attr_modifiers") or {}
        mod_summary = []
        for attr, val in sorted(attr_mods.items()):
            if isinstance(val, list) and len(val) == 2:
                # Operator-based modification
                mod_summary.append(f"{attr}=[{val[0]}, {val[1]}]")
            else:
                # Absolute value
                mod_summary.append(f"{attr}={val}")

        mod_text = "; ".join(mod_summary) if mod_summary else "no attr modifiers"
        year_badge = _year_badge_html(planning_year)
        parts.append(
            f"<div class='candidate-item' style='display: flex; justify-content: space-between; align-items: center;'>"
            f"<span><strong>{html.escape(key)}</strong> — {html.escape(str(new_tech))} — {html.escape(str(tag_class))} — {html.escape(str(fuel_desc))} — ({html.escape(mod_text)}){year_badge}</span>"
            f"<button onclick='window.deleteModifiedNewResource(\"{html.escape(key, quote=True)}\")' style='padding: 2px 8px; font-size: 11px; background: #dc3545; color: white; border: none; border-radius: 3px; cursor: pointer;'>Delete</button>"
            f"</div>"
        )
    container.innerHTML = "".join(parts)


def on_clear_modified_resources(event):
    state.modified_new_resources = {}
    render_modified_resources_list()


def _prefix_from_new_technology(new_technology):
    t = str(new_technology).strip()
    if not t:
        return ""
    if t.endswith("_"):
        return t
    return f"{t}_"


def _extract_ccs_capture_fraction(tech_detail):
    """Extract CCS capture fraction from technology detail string.

    Examples:
        "1-on-1 Combined Cycle (H-Frame) 95% CCS" -> 0.95
        "F-Frame CC 97% CCS" -> 0.97
        "Fuel Cell - 98% CCS" -> 0.98
        "99%-CCS" -> 0.99
        "Regular Technology" -> None
    """
    import re

    if not tech_detail:
        return None
    # Match patterns like "95% CCS", "95%-CCS", "95%CCS"
    match = re.search(r"(\d+)%[-\s]*CCS", str(tech_detail), re.IGNORECASE)
    if match:
        percentage = int(match.group(1))
        return percentage / 100.0
    return None


# Defaults for auto-detecting fuel type and resource class when adding inline
# attribute overrides to standard ATB new-build resources.
_ATB_TECH_DEFAULTS = {
    "NaturalGas": {"fuel": "naturalgas", "tag_class": "THERM", "is_commit": True},
    "Coal": {"fuel": "coal", "tag_class": "THERM", "is_commit": True},
    "Conventional Steam Coal": {
        "fuel": "coal",
        "tag_class": "THERM",
        "is_commit": True,
    },
    "Nuclear": {"fuel": "uranium", "tag_class": "THERM", "is_commit": True},
    "Petroleum Liquids": {
        "fuel": "distillate",
        "tag_class": "THERM",
        "is_commit": True,
    },
    "LandbasedWind": {"fuel": None, "tag_class": "VRE", "is_commit": False},
    "OffshoreWind": {"fuel": None, "tag_class": "VRE", "is_commit": False},
    "UtilityPV": {"fuel": None, "tag_class": "VRE", "is_commit": False},
    "Utility-Scale Battery Storage": {
        "fuel": None,
        "tag_class": "STOR",
        "is_commit": False,
    },
    "Hydroelectric Pumped Storage": {
        "fuel": None,
        "tag_class": "HYDRO",
        "is_commit": False,
    },
}

# Mapping from internal UI snake_case attribute keys to ATB column-style keys used
# in the resource_modifiers section of resources.yml.  Keys absent from this mapping
# (capex_mw, capex_mwh, wacc_real) are already valid PowerGenome column names.
_UI_TO_ATB_KEY = {
    "heat_rate": "Heat_Rate_MMBTU_per_MWh",
    "fixed_o_m_mw": "Fixed_OM_Cost_per_MWyr",
    "variable_o_m_mwh": "Var_OM_Cost_per_MWh",
    "variable_o_m_mwh_in": "Var_OM_Cost_per_MWh_In",
}


def _get_default_resource_modifiers(technology, tech_detail):
    """Get default modifier values for a resource.

    Returns a dict with default attribute modifiers. Currently, utility-scale
    battery storage gets variable O&M and WACC defaults.

    Args:
        technology: The technology name (e.g., "Utility-Scale Battery Storage")
        tech_detail: The tech detail (e.g., "Lithium Ion")

    Returns:
        dict: Default modifiers to add to resource_modifiers entry
    """
    defaults = {}

    # Default attributes for battery storage (not pumped hydro or other storage types)
    tech_lower = technology.lower()
    is_battery_storage = "battery" in tech_lower or "energy storage" in tech_lower
    if is_battery_storage:
        # ATB does not provide a WACC for batteries; use the default value.
        defaults["wacc_real"] = _BATTERY_DEFAULT_WACC
        if "lithium" in tech_detail.lower():
            defaults["Var_OM_Cost_per_MWh"] = _BATTERY_DEFAULT_VAR_OM
            defaults["Var_OM_Cost_per_MWh_In"] = _BATTERY_DEFAULT_VAR_OM_IN

    return defaults


def _auto_modified_key(tech, detail):
    """Generate a sanitized, unique key for an inline-override modified resource.

    Consecutive non-alphanumeric characters are collapsed into a single underscore.
    If the generated key already exists in state.modified_new_resources, a numeric
    suffix (_1, _2, …) is appended to ensure uniqueness.
    """
    raw = f"{tech}_{detail}"
    base = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    key = base
    counter = 1
    while key in state.modified_new_resources:
        key = f"{base}_{counter}"
        counter += 1
    return key


def on_add_modified_resource(event):
    base_tech_el = document.getElementById("modBaseTech")
    base_detail_el = document.getElementById("modBaseTechDetail")
    base_case_el = document.getElementById("modBaseCostCase")
    base_size_el = document.getElementById("modSizeMw")
    new_tech_el = document.getElementById("modNewTech")
    new_detail_el = document.getElementById("modNewTechDetail")

    fuel_type_el = document.getElementById("modFuelType")
    std_fuel_el = document.getElementById("modStandardFuel")
    new_fuel_name_el = document.getElementById("modNewFuelName")
    new_fuel_price_el = document.getElementById("modNewFuelPrice")
    new_fuel_ef_el = document.getElementById("modNewFuelEf")

    tag_class_el = document.getElementById("modTagClass")
    is_commit_el = document.getElementById("modIsCommit")

    base_tech = str(_get_select_value(base_tech_el, "")).strip()
    base_detail = str(_get_select_value(base_detail_el, "")).strip()
    base_case = str(_get_select_value(base_case_el, "")).strip()
    try:
        base_size = int(float(_get_select_value(base_size_el, 100)))
    except Exception:
        base_size = 100

    new_tech = str(_get_select_value(new_tech_el, "")).strip()
    new_detail = str(_get_select_value(new_detail_el, "")).strip()
    new_case = base_case  # automatically use the same cost case as the base resource
    atb_data_year = _get_selected_atb_data_year()

    # Auto-generate the key from the new technology and tech detail
    key = _auto_modified_key(new_tech, new_detail)

    # Collect optional attribute overrides from the collapsible panel
    attr_modifiers = {}
    override_fields = [
        ("capex_mw", "modOverrideCapexMw"),
        ("capex_mwh", "modOverrideCapexMwh"),
        ("heat_rate", "modOverrideHeatRate"),
        ("fixed_o_m_mw", "modOverrideFixedOM"),
        ("variable_o_m_mwh", "modOverrideVarOM"),
        ("variable_o_m_mwh_in", "modOverrideVarOMIn"),
        ("wacc_real", "modOverrideWacc"),
    ]
    for attr, el_id in override_fields:
        el = document.getElementById(el_id)
        if el is None:
            continue

        # Get value safely and check if it's actually filled in
        try:
            raw_value = el.value
        except Exception:
            continue

        if raw_value is None or raw_value == "":
            continue

        value_str = str(raw_value).strip()
        if not value_str or value_str.lower() == "none":
            continue

        try:
            # Check if value starts with an operator (e.g., "add:100", "mul:1.1")
            if ":" in value_str:
                parts = value_str.split(":", 1)
                if len(parts) == 2:
                    op, val = parts[0].strip().lower(), parts[1].strip()
                    if op in ["add", "mul", "truediv", "sub"]:
                        attr_modifiers[attr] = [op, float(val)]
                    else:
                        set_status(
                            f"Invalid operator for {attr}: '{op}'. Must be add, mul, truediv, or sub.",
                            "error",
                        )
                        return
                else:
                    raise ValueError("Invalid format")
            else:
                # Plain numeric value
                attr_modifiers[attr] = float(value_str)
        except ValueError:
            set_status(f"Invalid value for {attr}: '{value_str}'", "error")
            return

    if not (base_tech and base_detail and base_case and new_tech and new_detail):
        set_status(
            "Fill out the base ATB resource and the new technology name and tech detail.",
            "error",
        )
        return

    if atb_data_year:
        existing_year = _get_current_resources_atb_year()
        if existing_year is not None and existing_year != atb_data_year:
            show_atb_year_conflict_overlay(existing_year, atb_data_year)
            return

    fuel_type = str(_get_select_value(fuel_type_el, "standard"))
    std_fuel = str(_get_select_value(std_fuel_el, "naturalgas"))

    new_fuel_name = str(_get_select_value(new_fuel_name_el, "")).strip()
    try:
        new_fuel_price = float(_get_select_value(new_fuel_price_el, 0))
    except Exception:
        new_fuel_price = 0.0
    try:
        new_fuel_ef = float(_get_select_value(new_fuel_ef_el, 0))
    except Exception:
        new_fuel_ef = 0.0

    tag_class = str(_get_select_value(tag_class_el, "THERM"))
    is_commit = (
        bool(is_commit_el.checked) if (is_commit_el and tag_class == "THERM") else False
    )

    if fuel_type == "new":
        if not new_fuel_name:
            set_status("New fuel requires a fuel name.", "error")
            return
        if new_fuel_price < 0:
            set_status("Fuel price must be >= 0.", "error")
            return
        if new_fuel_ef < 0:
            set_status("Emission factor must be >= 0.", "error")
            return

    fuel_desc = (
        std_fuel
        if fuel_type == "standard"
        else f"{new_fuel_name} @ ${new_fuel_price}/MMBtu"
    )

    # Check if this is a CCS technology (check both base and new tech_detail)
    ccs_fraction = _extract_ccs_capture_fraction(
        new_detail
    ) or _extract_ccs_capture_fraction(base_detail)

    # Read planning year from the dropdown
    planning_year = _get_resource_planning_year("modResourceYearSelect")

    state.modified_new_resources[key] = {
        # resources.yml schema
        "technology": base_tech,
        "tech_detail": base_detail,
        "cost_case": base_case,
        "size_mw": int(base_size),
        "new_technology": new_tech,
        "new_tech_detail": new_detail,
        "new_cost_case": new_case,
        "attr_modifiers": attr_modifiers,
        # metadata for fuels.yml and resource_tags.yml
        "fuel_type": fuel_type,
        "standard_fuel": std_fuel,
        "new_fuel_name": new_fuel_name,
        "new_fuel_price": float(new_fuel_price),
        "new_fuel_emission_factor": float(new_fuel_ef),
        "tag_class": tag_class,
        "is_commit": bool(is_commit),
        "fuel_desc": fuel_desc,
        "ccs_capture_fraction": ccs_fraction,
        "planning_year": planning_year,
        "data_year": atb_data_year if atb_data_year else None,
    }

    # Clear attribute override fields for next entry
    for attr, el_id in override_fields:
        el = document.getElementById(el_id)
        if el is not None:
            try:
                el.value = ""
            except Exception:
                pass

    render_modified_resources_list()

    # Show year-default warning if needed
    _check_year_default_warning(
        base_tech, base_detail, base_case, planning_year, "modResourceYearWarning"
    )

    year_label = planning_year if planning_year != "all" else "all years"
    set_status(f"Added modified resource: {key} for {year_label}", "success")


def _get_region_aggregations_or_raise():
    if state.region_aggregations:
        return state.region_aggregations
    raise Exception("Run region clustering first to generate model regions.")


def compute_regional_hydro_factor(region_aggregations):
    """Default hydro_factor=2 globally; set regional_hydro_factor=4 for any model region that contains BA p1-p7."""
    target_bas = {f"p{i}" for i in range(1, 8)}
    out = {}
    for region_name, bas in region_aggregations.items():
        bas_set = {str(b).strip().lower() for b in (bas or [])}
        if bas_set & target_bas:
            out[region_name] = 4
    return out


def _get_float_input(element_id, default=None):
    el = document.getElementById(element_id)
    if not el:
        return default
    try:
        return float(el.value)
    except (TypeError, ValueError):
        return default


def _get_int_input(element_id, default=None):
    el = document.getElementById(element_id)
    if not el:
        return default
    try:
        return int(float(el.value))
    except (TypeError, ValueError):
        return default


def _build_region_demand_map(region_aggs):
    if not state.reeds_annual_demand_avg:
        raise Exception("Annual demand data not loaded.")
    region_demand = {}
    missing_bas = []
    for region_name, bas in region_aggs.items():
        total = 0.0
        for ba in bas or []:
            key = str(ba).strip().lower()
            demand = state.reeds_annual_demand_avg.get(key)
            if demand is None:
                missing_bas.append(key)
                continue
            total += float(demand)
        region_demand[region_name] = total
    return region_demand, sorted(set(missing_bas))


def _load_resource_group_lcoe_df(resource_key):
    """Load LCOE data for a resource from cached assignments or uploaded files.

    Prefers in-session assignments cached by resource-group generation.
    Falls back to user-uploaded LCOE DataFrames when assignments are absent.
    """
    # --- primary path: in-session assignments ---
    if state.resource_group_assignments is not None:
        assignments = state.resource_group_assignments
        required = {"tech", "model_region", "cpa_mw", "cf", "lcoe"}
        if not required <= set(assignments.columns):
            window.console.log(
                f"Renewables: assignments missing columns "
                f"{sorted(required - set(assignments.columns))}"
            )
            return None

        df = assignments[assignments["tech"] == resource_key][
            ["model_region", "cpa_mw", "cf", "lcoe"]
        ].copy()
        if df.empty:
            window.console.log(
                f"Renewables: no rows for tech={resource_key} in assignments"
            )
            return None

        df = df.rename(columns={"model_region": "region", "cpa_mw": "capacity_mw"})
        window.console.log(
            f"Renewables: loaded {resource_key} from assignments, rows={len(df):,}"
        )
        return df

    # --- fallback path: user-uploaded LCOE file ---
    uploaded = None
    if resource_key == "onshorewind":
        uploaded = state.uploaded_lcoe_onshorewind
    elif resource_key == "solar":
        uploaded = state.uploaded_lcoe_solar

    if uploaded is None:
        window.console.log(
            f"Renewables: no cached assignments and no uploaded file for {resource_key}"
        )
        return None

    df = uploaded[["region", "cpa_mw", "cf", "lcoe"]].copy()
    if df.empty:
        window.console.log(f"Renewables: uploaded file for {resource_key} is empty")
        return None

    df = df.rename(columns={"cpa_mw": "capacity_mw"})
    window.console.log(
        f"Renewables: loaded {resource_key} from uploaded file, rows={len(df):,}"
    )
    return df


def _prepare_lcoe_region_data(lcoe_df):
    if lcoe_df is None or lcoe_df.empty:
        return {}
    out = {}
    for region_name, df in lcoe_df.groupby("region"):
        df_sorted = df.sort_values("lcoe").reset_index(drop=True)
        lcoe_vals = df_sorted["lcoe"].to_numpy(dtype=float)
        # cpa_mw = df_sorted["cpa_mw"].to_numpy(dtype=float)
        cf = df_sorted["cf"].to_numpy(dtype=float)
        cap_mw = df_sorted["capacity_mw"].to_numpy(dtype=float)
        # annual_mwh = cpa_mw * cf * 8760
        annual_mwh = cap_mw * cf * 8760
        cum_mwh = np.cumsum(annual_mwh)
        out[region_name] = {
            "lcoe": lcoe_vals,
            "capacity_mw": cap_mw,
            "cum_mwh": cum_mwh,
        }
    return out


def _compute_region_bin_cluster_config(
    region_caps,
    region_lcoe_data,
    region_ranges,
    target_total_resources,
    default_mw_per_bin,
    default_n_clusters,
):
    regions = [r for r, cap in region_caps.items() if cap > 0]
    if not regions:
        return {}, 0, 0, 0

    bins = {
        r: max(1, int(math.ceil(region_caps[r] / default_mw_per_bin))) for r in regions
    }
    minimum_total_resources = int(sum(bins.values()))

    if not target_total_resources or target_total_resources <= 0:
        n_clusters = {r: default_n_clusters for r in regions}
        total = sum(bins[r] * n_clusters[r] for r in regions)
        return (
            {
                r: {
                    "bins": bins[r],
                    "q": bins[r],
                    "mw_per_bin": max(1, int(round(region_caps[r] / bins[r]))),
                    "n_clusters": n_clusters[r],
                }
                for r in regions
            },
            total,
            total,
            minimum_total_resources,
        )

    effective_target = max(int(target_total_resources), minimum_total_resources)
    n_clusters = optimize_cluster_allocation(region_lcoe_data, bins, effective_target)

    # Fallback/Safety
    for r in regions:
        if r not in n_clusters:
            n_clusters[r] = 1

    total = int(sum(bins[r] * n_clusters[r] for r in regions))

    window.console.log(
        f"Renewables: optimized clusters total={total}, target={effective_target}, bins={sum(bins.values())}"
    )

    return (
        {
            r: {
                "bins": bins[r],
                "q": bins[r],
                "mw_per_bin": max(1, int(round(region_caps[r] / bins[r]))),
                "n_clusters": n_clusters[r],
            }
            for r in regions
        },
        total,
        effective_target,
        minimum_total_resources,
    )


def set_renewables_status(message, status_type="info"):
    el = document.getElementById("renewablesStatus")
    if el:
        el.textContent = message
        el.className = f"status {status_type}"
        el.style.display = "block"


def _safe_float(value, default=0.0):
    try:
        parsed = float(value)
        if math.isfinite(parsed):
            return parsed
    except Exception:
        pass
    return default


def _extract_cluster_lcoe_max(cluster_item):
    if not isinstance(cluster_item, dict):
        return None
    filters = cluster_item.get("filter")
    if not isinstance(filters, list):
        return None
    for filt in filters:
        if isinstance(filt, dict) and filt.get("feature") == "lcoe":
            return _safe_float(filt.get("max"), None)
    return None


def _extract_cluster_q(cluster_item):
    if not isinstance(cluster_item, dict):
        return 1
    bins = cluster_item.get("bin")
    if isinstance(bins, list) and bins:
        q = _safe_float(bins[0].get("q"), 1)
        return max(1, int(round(q)))
    return 1


def _extract_cluster_feature(cluster_item):
    if not isinstance(cluster_item, dict):
        return "lcoe"
    cluster_cfg = cluster_item.get("cluster")
    if isinstance(cluster_cfg, list) and cluster_cfg:
        feature = str(cluster_cfg[0].get("feature", "lcoe") or "lcoe")
        return feature
    return "lcoe"


def _extract_cluster_n_clusters(cluster_item):
    if not isinstance(cluster_item, dict):
        return 1
    cluster_cfg = cluster_item.get("cluster")
    if isinstance(cluster_cfg, list) and cluster_cfg:
        n_clusters = _safe_float(cluster_cfg[0].get("n_clusters"), 1)
        return max(1, int(round(n_clusters)))
    return 1


def _agglomerative_1d_labels(values, weights, k):
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    valid = np.isfinite(values) & np.isfinite(weights)
    values = values[valid]
    weights = weights[valid]

    if values.size == 0:
        return np.array([], dtype=int)

    weights = np.maximum(weights, 1e-9)
    k = max(1, min(int(k), values.size))
    if k == 1:
        return np.zeros(values.size, dtype=int)

    order = np.argsort(values, kind="mergesort")
    sorted_vals = values[order]
    sorted_weights = weights[order]

    clusters = [
        {
            "positions": [idx],
            "weight": float(sorted_weights[idx]),
            "mean": float(sorted_vals[idx]),
        }
        for idx in range(sorted_vals.size)
    ]

    def merge_cost(left_cluster, right_cluster):
        left_w = left_cluster["weight"]
        right_w = right_cluster["weight"]
        denom = left_w + right_w
        if denom <= 0:
            return 0.0
        mean_diff = left_cluster["mean"] - right_cluster["mean"]
        return (left_w * right_w / denom) * (mean_diff**2)

    while len(clusters) > k:
        best_idx = 0
        best_cost = None
        for idx in range(len(clusters) - 1):
            cost = merge_cost(clusters[idx], clusters[idx + 1])
            if best_cost is None or cost < best_cost:
                best_cost = cost
                best_idx = idx

        left_cluster = clusters[best_idx]
        right_cluster = clusters[best_idx + 1]
        merged_weight = left_cluster["weight"] + right_cluster["weight"]
        if merged_weight <= 0:
            merged_mean = 0.5 * (left_cluster["mean"] + right_cluster["mean"])
        else:
            merged_mean = (
                left_cluster["mean"] * left_cluster["weight"]
                + right_cluster["mean"] * right_cluster["weight"]
            ) / merged_weight

        merged_cluster = {
            "positions": left_cluster["positions"] + right_cluster["positions"],
            "weight": merged_weight,
            "mean": float(merged_mean),
        }
        clusters[best_idx] = merged_cluster
        del clusters[best_idx + 1]

    labels_sorted = np.zeros(sorted_vals.size, dtype=int)
    for label, cluster_data in enumerate(clusters):
        for pos in cluster_data["positions"]:
            labels_sorted[pos] = label

    labels = np.zeros(sorted_vals.size, dtype=int)
    labels[order] = labels_sorted
    return labels


def _assign_weighted_bins(df, bin_feature, q):
    if df is None or df.empty:
        return np.array([], dtype=int)

    q = max(1, int(q))
    if q == 1:
        return np.zeros(len(df), dtype=int)

    temp = df.copy().reset_index(drop=True)
    if bin_feature not in temp.columns:
        bin_feature = "lcoe"

    temp["_bin_feature"] = pd.to_numeric(temp[bin_feature], errors="coerce")
    temp["_weights"] = pd.to_numeric(temp["capacity_mw"], errors="coerce").fillna(0.0)
    temp["_weights"] = temp["_weights"].clip(lower=0.0)
    temp["_weights"] = temp["_weights"].where(temp["_weights"] > 0.0, 1e-9)

    temp_sorted = temp.sort_values("_bin_feature", kind="mergesort").reset_index()
    cumulative = temp_sorted["_weights"].cumsum().to_numpy()
    total = float(cumulative[-1]) if cumulative.size else 0.0
    if total <= 0:
        return np.zeros(len(df), dtype=int)

    bin_edges = np.linspace(0.0, total, q + 1)[1:-1]
    bin_ids_sorted = np.searchsorted(bin_edges, cumulative, side="left")

    bin_ids = np.zeros(len(df), dtype=int)
    for pos, row in enumerate(temp_sorted.itertuples(index=False)):
        original_idx = int(getattr(row, "index"))
        bin_ids[original_idx] = int(bin_ids_sorted[pos])

    return bin_ids


def _build_individual_supply_curve_bars(region_df):
    bars = []
    if region_df is None or region_df.empty:
        return bars
    for idx, row in enumerate(region_df.itertuples(index=False), start=1):
        cap = _safe_float(getattr(row, "capacity_mw", 0.0), 0.0)
        lcoe = _safe_float(getattr(row, "lcoe", 0.0), 0.0)
        if cap <= 0:
            continue
        bars.append({"label": f"CPA {idx}", "capacity_mw": cap, "lcoe": lcoe})
    return bars


def _build_aggregated_supply_curve_bars(region_df, cluster_item):
    if region_df is None or region_df.empty:
        return []

    work_df = region_df.copy().reset_index(drop=True)
    if "capacity_mw" not in work_df.columns or "lcoe" not in work_df.columns:
        return []

    bin_cfg = cluster_item.get("bin") if isinstance(cluster_item, dict) else None
    bin_feature = "lcoe"
    if isinstance(bin_cfg, list) and bin_cfg:
        bin_feature = str(bin_cfg[0].get("feature", "lcoe") or "lcoe")
    q = _extract_cluster_q(cluster_item)
    cluster_feature = _extract_cluster_feature(cluster_item)
    n_clusters = _extract_cluster_n_clusters(cluster_item)

    if cluster_feature not in work_df.columns:
        cluster_feature = "lcoe"

    work_df["capacity_mw"] = pd.to_numeric(
        work_df["capacity_mw"], errors="coerce"
    ).fillna(0.0)
    work_df["lcoe"] = pd.to_numeric(work_df["lcoe"], errors="coerce").fillna(0.0)
    work_df[cluster_feature] = pd.to_numeric(
        work_df[cluster_feature], errors="coerce"
    ).fillna(0.0)
    work_df = work_df[work_df["capacity_mw"] > 0.0].copy()
    if work_df.empty:
        return []

    work_df["_bin_id"] = _assign_weighted_bins(work_df, bin_feature, q)

    bars = []
    for bin_id, bin_df in work_df.groupby("_bin_id", sort=True):
        bin_df = bin_df.reset_index(drop=True)
        effective_k = max(1, min(int(n_clusters), len(bin_df)))

        values = bin_df[cluster_feature].to_numpy(dtype=float)
        weights = bin_df["capacity_mw"].to_numpy(dtype=float)
        labels = _agglomerative_1d_labels(values, weights, effective_k)
        if labels.size == 0:
            labels = np.zeros(len(bin_df), dtype=int)

        for cluster_idx in sorted(set(labels.tolist())):
            cluster_rows = bin_df[labels == cluster_idx]
            capacity = float(cluster_rows["capacity_mw"].sum())
            if capacity <= 0:
                continue
            lcoe = float(
                (cluster_rows["lcoe"] * cluster_rows["capacity_mw"]).sum() / capacity
            )
            bars.append(
                {
                    "label": f"Bin {int(bin_id) + 1} • Cluster {int(cluster_idx) + 1}",
                    "capacity_mw": capacity,
                    "lcoe": lcoe,
                    "count": int(len(cluster_rows)),
                    "bin": int(bin_id) + 1,
                }
            )

    bars.sort(
        key=lambda item: (
            _safe_float(item.get("lcoe"), 0.0),
            _safe_float(item.get("capacity_mw"), 0.0),
        )
    )
    return bars


def _format_number_short(value):
    val = _safe_float(value, 0.0)
    if abs(val) >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if abs(val) >= 1_000:
        return f"{val / 1_000:.1f}k"
    return f"{val:.0f}"


def _render_supply_curve_svg(
    bars,
    x_max,
    y_max,
    bar_fill,
    included_capacity_mw=None,
    excluded_fill=None,
):
    width = 360
    height = 200
    margin_left = 44
    margin_right = 10
    margin_top = 8
    margin_bottom = 28
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    x_max = max(1.0, _safe_float(x_max, 1.0))
    y_max = max(1.0, _safe_float(y_max, 1.0))

    svg_parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="180" role="img" aria-label="Supply curve">',
        f'<line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{margin_left + plot_w}" y2="{margin_top + plot_h}" stroke="#999" stroke-width="1" />',
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_h}" stroke="#999" stroke-width="1" />',
    ]

    cumulative = 0.0
    for bar in bars:
        cap = _safe_float(bar.get("capacity_mw", 0.0), 0.0)
        lcoe = _safe_float(bar.get("lcoe", 0.0), 0.0)
        if cap <= 0:
            continue
        x0 = margin_left + (cumulative / x_max) * plot_w
        w = max(1.0, (cap / x_max) * plot_w)
        h = max(0.0, min(plot_h, (lcoe / y_max) * plot_h))
        y = margin_top + (plot_h - h)
        fill = bar_fill
        if (
            included_capacity_mw is not None
            and excluded_fill
            and cumulative >= _safe_float(included_capacity_mw, 0.0)
        ):
            fill = excluded_fill
        title = html.escape(
            f"{bar.get('label', 'Bar')}: {cap:,.0f} MW, LCOE {lcoe:.2f}"
        )
        svg_parts.append(
            f'<rect x="{x0:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" fill="{fill}"><title>{title}</title></rect>'
        )
        cumulative += cap

    if included_capacity_mw is not None:
        marker = max(0.0, min(x_max, _safe_float(included_capacity_mw, 0.0)))
        marker_x = margin_left + (marker / x_max) * plot_w
        svg_parts.append(
            f'<line x1="{marker_x:.2f}" y1="{margin_top}" x2="{marker_x:.2f}" y2="{margin_top + plot_h}" stroke="#d32f2f" stroke-width="1.25" />'
        )

    svg_parts.extend(
        [
            f'<text x="{margin_left}" y="{margin_top + plot_h + 16}" font-size="10" fill="#666">0</text>',
            f'<text x="{margin_left + plot_w}" y="{margin_top + plot_h + 16}" text-anchor="end" font-size="10" fill="#666">{_format_number_short(x_max)} MW</text>',
            f'<text x="{margin_left - 6}" y="{margin_top + plot_h}" text-anchor="end" font-size="10" fill="#666">0</text>',
            f'<text x="{margin_left - 6}" y="{margin_top + 8}" text-anchor="end" font-size="10" fill="#666">{_safe_float(y_max, 0.0):.1f}</text>',
            f'<text x="{margin_left + (plot_w / 2)}" y="{height - 4}" text-anchor="middle" font-size="10" fill="#666">Cumulative capacity (MW)</text>',
            f'<text x="12" y="{margin_top + (plot_h / 2)}" text-anchor="middle" font-size="10" fill="#666" transform="rotate(-90 12 {margin_top + (plot_h / 2)})">LCOE</text>',
            "</svg>",
        ]
    )

    return "".join(svg_parts)


def _build_renewables_supply_curve_payload():
    if not isinstance(state.renewables_clusters, list) or not state.renewables_clusters:
        return {}

    payload = {}
    cluster_map = {}
    for cluster_item in state.renewables_clusters:
        if not isinstance(cluster_item, dict):
            continue
        tech = str(cluster_item.get("technology", ""))
        region = str(cluster_item.get("region", ""))
        if not tech or not region:
            continue
        cluster_map[(region, tech)] = cluster_item

    for tech in ["landbasedwind", "utilitypv"]:
        config = RENEWABLES_TECH_CONFIG.get(tech)
        if not config:
            continue
        lcoe_df = _load_resource_group_lcoe_df(config["resource_key"])
        if lcoe_df is None:
            continue

        lcoe_df = lcoe_df[["region", "lcoe", "capacity_mw", "cf"]].copy()
        lcoe_df["region"] = lcoe_df["region"].astype(str)

        for (region_name, item_tech), cluster_item in cluster_map.items():
            if item_tech != tech:
                continue
            lcoe_max = _extract_cluster_lcoe_max(cluster_item)
            if lcoe_max is None:
                continue
            region_mask = lcoe_df["region"] == region_name
            filtered = lcoe_df[region_mask & (lcoe_df["lcoe"] <= (lcoe_max + 0.011))]
            if filtered.empty:
                continue

            filtered = filtered.sort_values("lcoe").reset_index(drop=True)
            individual = _build_individual_supply_curve_bars(filtered)
            aggregated = _build_aggregated_supply_curve_bars(filtered, cluster_item)

            payload.setdefault(region_name, {})[tech] = {
                "q": _extract_cluster_q(cluster_item),
                "individual": individual,
                "aggregated": aggregated,
            }

    return payload


def _render_renewables_supply_curves():
    container = document.getElementById("renewablesSupplyCurves")
    if not container:
        return

    payload = _build_renewables_supply_curve_payload()
    if not payload:
        container.innerHTML = (
            "<em>Compute renewables clusters to generate supply-curve plots.</em>"
        )
        return

    tech_specs = [
        ("landbasedwind", "Wind", "#4f81bd", "#1f4e79"),
        ("utilitypv", "Solar", "#f2b134", "#b87f00"),
    ]

    if state.region_aggregations:
        region_names = sorted(state.region_aggregations.keys())
    else:
        region_names = sorted(payload.keys())

    parts = []
    for region_name in region_names:
        region_curves = payload.get(region_name, {})
        parts.append("<div class='renewables-plot-region'>")
        parts.append(f"<h4>{html.escape(region_name)}</h4>")
        parts.append("<div class='renewables-plot-grid'>")

        for tech_key, tech_label, agg_color, ind_color in tech_specs:
            curves = region_curves.get(tech_key)
            if not curves:
                parts.append(
                    f"<div class='renewables-plot-card'><h5>{tech_label} — Aggregated CPAs</h5><div class='renewables-plot-empty'>No selected {tech_label.lower()} CPAs for this region.</div></div>"
                )
                parts.append(
                    f"<div class='renewables-plot-card'><h5>{tech_label} — Individual CPAs</h5><div class='renewables-plot-empty'>No selected {tech_label.lower()} CPAs for this region.</div></div>"
                )
                continue

            aggregated = curves.get("aggregated", [])
            individual = curves.get("individual", [])
            agg_capacity = sum(
                _safe_float(b.get("capacity_mw", 0.0), 0.0) for b in aggregated
            )
            ind_capacity = sum(
                _safe_float(b.get("capacity_mw", 0.0), 0.0) for b in individual
            )
            x_max = max(agg_capacity, ind_capacity, 1.0)

            agg_y_max = max(
                (_safe_float(b.get("lcoe", 0.0), 0.0) for b in aggregated), default=0.0
            )
            ind_y_max = max(
                (_safe_float(b.get("lcoe", 0.0), 0.0) for b in individual), default=0.0
            )
            y_max = max(agg_y_max, ind_y_max, 1.0)

            agg_svg = _render_supply_curve_svg(aggregated, x_max, y_max, agg_color)
            ind_svg = _render_supply_curve_svg(individual, x_max, y_max, ind_color)

            parts.append(
                "".join(
                    [
                        "<div class='renewables-plot-card'>",
                        f"<h5>{tech_label} — Aggregated CPAs</h5>",
                        agg_svg,
                        (
                            f"<div class='renewables-plot-meta'>{len(aggregated)} groups, {int(round(agg_capacity)):,} MW total</div>"
                        ),
                        "</div>",
                    ]
                )
            )
            parts.append(
                "".join(
                    [
                        "<div class='renewables-plot-card'>",
                        f"<h5>{tech_label} — Individual CPAs</h5>",
                        ind_svg,
                        (
                            f"<div class='renewables-plot-meta'>{len(individual)} CPAs, {int(round(ind_capacity)):,} MW total</div>"
                        ),
                        "</div>",
                    ]
                )
            )

        parts.append("</div>")
        parts.append("</div>")

    container.innerHTML = "".join(parts)


def _render_renewables_preview():
    preview_el = document.getElementById("renewablesClustersPreview")
    if not preview_el:
        return
    if not state.renewables_clusters:
        preview_el.value = ""
        _render_renewables_supply_curves()
        _render_renewables_advanced_panel()
        return
    renewables_yaml = yaml.dump(
        {"renewables_clusters": state.renewables_clusters},
        default_flow_style=False,
        sort_keys=False,
    )
    comment_block = _format_renewables_capacity_comments()
    preview_el.value = (
        f"{comment_block}\n{renewables_yaml}" if comment_block else renewables_yaml
    )
    _render_renewables_supply_curves()
    _render_renewables_advanced_panel()


def _format_renewables_capacity_comments():
    cap_map = (
        state.renewables_region_capacity_mw
        if isinstance(state.renewables_region_capacity_mw, dict)
        else {}
    )
    if not cap_map:
        return ""

    lines = ["# Selected renewables capacity by region (MW)"]
    for tech in ["landbasedwind", "utilitypv"]:
        tech_caps = cap_map.get(tech)
        if not isinstance(tech_caps, dict) or not tech_caps:
            continue
        lines.append(f"# {tech}:")
        for region_name in sorted(tech_caps.keys()):
            cap_val = tech_caps[region_name]
            try:
                cap_display = int(round(float(cap_val)))
            except Exception:
                cap_display = cap_val
            lines.append(f"#   {region_name}: {cap_display}")

    return "\n".join(lines) if len(lines) > 1 else ""


def _update_renewables_suggested_counts(wind_count, solar_count):
    el = document.getElementById("renewablesSuggestedCounts")
    if el:
        el.textContent = f"Suggested budgets: wind {wind_count}, solar {solar_count}"


def _get_renewables_tech_input_ids(tech):
    if tech == "landbasedwind":
        return (
            "renewablesWindShare",
            "renewablesWindAvgResourceMw",
            "renewablesWindBudgetCount",
        )
    if tech == "utilitypv":
        return (
            "renewablesSolarShare",
            "renewablesSolarAvgResourceMw",
            "renewablesSolarBudgetCount",
        )
    raise ValueError(f"Unknown renewables tech: {tech}")


def _get_renewables_share_for_tech(tech):
    share_id, _, _ = _get_renewables_tech_input_ids(tech)
    return max(0.0, min(1.0, (_get_float_input(share_id, 0.0) or 0.0) / 100.0))


def _get_avg_resource_size_for_tech(tech):
    _, avg_id, _ = _get_renewables_tech_input_ids(tech)
    default = RENEWABLES_TECH_CONFIG[tech]["avg_resource_mw"]
    avg_resource_mw = _get_float_input(avg_id, default)
    return max(1.0, float(avg_resource_mw or default))


def _compute_suggested_budget(region_data, region_targets, avg_resource_mw):
    suggested = 0
    for region_name, target_mwh in region_targets.items():
        data = region_data.get(region_name)
        if not data or target_mwh <= 0:
            continue
        cum_mwh = data["cum_mwh"]
        if cum_mwh.size == 0:
            continue
        cutoff_idx = int(np.searchsorted(cum_mwh, target_mwh, side="left"))
        if cutoff_idx >= cum_mwh.size:
            cutoff_idx = cum_mwh.size - 1
        cap_vals = data["capacity_mw"]
        selected_capacity = float(cap_vals[: cutoff_idx + 1].sum())
        if selected_capacity > 0:
            suggested += int(math.ceil(selected_capacity / avg_resource_mw))
    return suggested


def _get_renewables_override_map(tech):
    if not isinstance(state.renewables_capacity_overrides_mw, dict):
        state.renewables_capacity_overrides_mw = {"landbasedwind": {}, "utilitypv": {}}
    return state.renewables_capacity_overrides_mw.setdefault(tech, {})


def _get_curve_region_data(tech, region_name):
    data = (
        state.renewables_curve_data
        if isinstance(state.renewables_curve_data, dict)
        else {}
    )
    tech_data = data.get(tech)
    if not isinstance(tech_data, dict):
        return None
    region_data = tech_data.get(region_name)
    if not isinstance(region_data, dict):
        return None
    return region_data


def _compute_cutoff_idx_from_capacity(cum_capacity_mw, target_capacity_mw):
    cum = np.asarray(cum_capacity_mw, dtype=float)
    if cum.size == 0:
        return None
    target = max(0.0, _safe_float(target_capacity_mw, 0.0))
    idx = int(np.searchsorted(cum, target, side="left"))
    if idx >= cum.size:
        idx = cum.size - 1
    if idx < 0:
        idx = 0
    return idx


def _apply_capacity_override_to_curve_data(tech, region_name, requested_capacity_mw):
    region_data = _get_curve_region_data(tech, region_name)
    if not region_data:
        return

    cum_capacity = np.asarray(region_data.get("cum_capacity_mw", []), dtype=float)
    lcoe_vals = np.asarray(region_data.get("lcoe", []), dtype=float)
    if cum_capacity.size == 0 or lcoe_vals.size == 0:
        return

    baseline_capacity = _safe_float(region_data.get("baseline_capacity_mw", 0.0), 0.0)
    available_capacity = _safe_float(region_data.get("available_capacity_mw", 0.0), 0.0)

    requested = _safe_float(requested_capacity_mw, baseline_capacity)
    target_capacity = max(0.0, min(requested, available_capacity))
    cutoff_idx = _compute_cutoff_idx_from_capacity(cum_capacity, target_capacity)
    if cutoff_idx is None:
        return

    included_capacity = float(cum_capacity[cutoff_idx])
    lcoe_max = float(lcoe_vals[min(cutoff_idx, lcoe_vals.size - 1)])

    region_data["cutoff_idx"] = int(cutoff_idx)
    region_data["included_capacity_mw"] = included_capacity
    region_data["lcoe_max"] = lcoe_max

    pending = (
        state.renewables_pending_region_capacity_mw
        if isinstance(state.renewables_pending_region_capacity_mw, dict)
        else {}
    )
    tech_pending = pending.setdefault(tech, {})
    tech_pending[region_name] = included_capacity
    state.renewables_pending_region_capacity_mw = pending


def _fraction_to_sequential_color(base_color, fraction):
    frac = max(0.0, min(1.0, _safe_float(fraction, 0.0)))
    # low fraction: very light, high fraction: base color
    lighten_factor = 0.82 - 0.67 * frac
    return lighten_color(base_color, max(0.08, min(0.92, lighten_factor)))


def _build_supply_curve_bars_from_curve_data(curve_data, max_points=None):
    if not isinstance(curve_data, dict):
        return []

    caps = np.asarray(curve_data.get("capacity_mw", []), dtype=float)
    lcoe = np.asarray(curve_data.get("lcoe", []), dtype=float)
    if caps.size == 0 or lcoe.size == 0:
        return []

    n = min(caps.size, lcoe.size)
    caps = caps[:n]
    lcoe = lcoe[:n]

    if max_points is None or max_points <= 0 or n <= max_points:
        bars = []
        for idx in range(n):
            cap = float(caps[idx])
            if cap <= 0:
                continue
            bars.append(
                {
                    "label": f"CPA {idx + 1}",
                    "capacity_mw": cap,
                    "lcoe": float(lcoe[idx]),
                }
            )
        return bars

    chunk = int(math.ceil(n / max_points))
    bars = []
    for start in range(0, n, chunk):
        end = min(start + chunk, n)
        block_caps = caps[start:end]
        block_lcoe = lcoe[start:end]
        total_cap = float(block_caps.sum())
        if total_cap <= 0:
            continue
        weighted_lcoe = float((block_lcoe * block_caps).sum() / total_cap)
        bars.append(
            {
                "label": f"CPA {start + 1}-{end}",
                "capacity_mw": total_cap,
                "lcoe": weighted_lcoe,
            }
        )

    return bars


def _render_region_supply_curve_svg(region_name, tech, compact=False):
    curve_data = _get_curve_region_data(tech, region_name)
    if not curve_data:
        return "<em>No curve data available for this region.</em>"

    max_points = 36 if compact else None
    bars = _build_supply_curve_bars_from_curve_data(curve_data, max_points=max_points)
    if not bars:
        return "<em>No curve data available for this region.</em>"

    x_max = sum(_safe_float(b.get("capacity_mw"), 0.0) for b in bars)
    y_max = max((_safe_float(b.get("lcoe"), 0.0) for b in bars), default=1.0)
    included_capacity = _safe_float(curve_data.get("included_capacity_mw", 0.0), 0.0)

    tech_style = RENEWABLES_TECH_STYLES.get(tech, {})
    bar_color = tech_style.get("bar_color", "#1f4e79")
    excluded_color = lighten_color(bar_color, 0.55)

    svg = _render_supply_curve_svg(
        bars,
        max(1.0, x_max),
        max(1.0, y_max),
        bar_color,
        included_capacity_mw=included_capacity,
        excluded_fill=excluded_color,
    )
    return svg


def _render_selected_renewables_region_panel():
    slider = document.getElementById("renewablesRegionCapacitySlider")
    title_el = document.getElementById("renewablesRegionEditorTitle")
    value_el = document.getElementById("renewablesRegionCapacityValue")
    meta_el = document.getElementById("renewablesRegionMeta")
    plot_el = document.getElementById("renewablesRegionPlot")
    label_el = document.getElementById("renewablesSliderLabel")

    if (
        not slider
        or not title_el
        or not value_el
        or not meta_el
        or not plot_el
        or not label_el
    ):
        return

    region_name = state.renewables_selected_region
    tech = state.renewables_selected_tech
    if not region_name or not tech:
        slider.disabled = True
        title_el.textContent = "Select a region from either map"
        label_el.textContent = "Capacity included (MW)"
        value_el.textContent = "—"
        meta_el.textContent = "Click a model region to inspect its supply curve and set additional included capacity."
        plot_el.innerHTML = "<em>Select a region to display the full supply curve.</em>"
        return

    curve_data = _get_curve_region_data(tech, region_name)
    if not curve_data:
        slider.disabled = True
        title_el.textContent = f"{region_name}"
        plot_el.innerHTML = "<em>No curve data available for this region.</em>"
        return

    tech_label = RENEWABLES_TECH_STYLES.get(tech, {}).get("label", tech)
    baseline_capacity = _safe_float(curve_data.get("baseline_capacity_mw", 0.0), 0.0)
    included_capacity = _safe_float(curve_data.get("included_capacity_mw", 0.0), 0.0)
    available_capacity = _safe_float(curve_data.get("available_capacity_mw", 0.0), 0.0)
    lcoe_max = _safe_float(curve_data.get("lcoe_max", 0.0), 0.0)
    extra_available = max(0.0, available_capacity - baseline_capacity)

    slider.disabled = False
    slider.min = "0"
    slider.max = str(max(int(round(available_capacity)), int(round(baseline_capacity))))
    slider.step = "1"
    slider.value = str(int(round(included_capacity)))

    title_el.textContent = f"{region_name} ({tech_label})"
    label_el.textContent = f"{tech_label} included capacity (MW)"
    value_el.textContent = f"{int(round(included_capacity)):,} MW"
    meta_el.innerHTML = (
        f"Baseline {int(round(baseline_capacity)):,} MW; "
        f"available total {int(round(available_capacity)):,} MW "
        f"({int(round(extra_available)):,} MW not included); "
        f"<strong>current max LCOE is ${lcoe_max:.2f}</strong>."
    )

    full_svg = _render_region_supply_curve_svg(region_name, tech, compact=False)
    plot_el.innerHTML = full_svg


def _render_renewables_advanced_hint(message):
    hint_el = document.getElementById("renewablesAdvancedHint")
    if hint_el:
        hint_el.textContent = message


def _renewables_feature_style(feature, tech):
    props = feature.properties
    region_name = str(
        getattr(props, "model_region", "")
        if not isinstance(props, dict)
        else props.get("model_region", "")
    )
    if not region_name:
        ba_id = str(
            getattr(props, "rb", "")
            if not isinstance(props, dict)
            else props.get("rb", "")
        )
        region_name = state.ba_to_region.get(ba_id)
    if not region_name:
        return to_js(
            {
                "fillColor": "#f2f2f2",
                "fillOpacity": 0.15,
                "color": "#d0d0d0",
                "weight": 1,
            }
        )

    curve_data = _get_curve_region_data(tech, region_name)
    if not curve_data:
        return to_js(
            {
                "fillColor": "#f2f2f2",
                "fillOpacity": 0.2,
                "color": "#c0c0c0",
                "weight": 1,
            }
        )

    included = _safe_float(curve_data.get("included_capacity_mw", 0.0), 0.0)
    available = _safe_float(curve_data.get("available_capacity_mw", 0.0), 0.0)
    fraction = included / available if available > 0 else 0.0

    base_color = RENEWABLES_TECH_STYLES.get(tech, {}).get("base_color", "#1f4e79")
    fill_color = _fraction_to_sequential_color(base_color, fraction)

    selected = (
        region_name == state.renewables_selected_region
        and tech == state.renewables_selected_tech
    )
    return to_js(
        {
            "fillColor": fill_color,
            "fillOpacity": 0.72,
            "color": "#d32f2f" if selected else "#666",
            "weight": 2.8 if selected else 1.0,
        }
    )


def _on_renewables_map_click(ba_id, tech):
    region_name = state.ba_to_region.get(ba_id)
    if not region_name:
        return
    state.renewables_selected_region = region_name
    state.renewables_selected_tech = tech
    _render_selected_renewables_region_panel()
    _render_renewables_maps()


def _on_each_renewables_map_feature(feature, layer, tech):
    props = feature.properties
    region_name = str(
        getattr(props, "model_region", "")
        if not isinstance(props, dict)
        else props.get("model_region", "")
    )
    ba_id = str(
        getattr(props, "rb", "") if not isinstance(props, dict) else props.get("rb", "")
    )

    def handle_click(event, region=region_name, ba=ba_id, t=tech):
        if region:
            state.renewables_selected_region = region
            state.renewables_selected_tech = t
            _render_selected_renewables_region_panel()
            _render_renewables_maps()
            return
        _on_renewables_map_click(ba, t)

    layer.on("click", create_proxy(handle_click))


def _build_renewables_geojson_for_selected_bas():
    if not isinstance(state.geojson_data, dict):
        return None
    features = state.geojson_data.get("features")
    if not isinstance(features, list):
        return None

    selected_bas = set(state.ba_to_region.keys())
    if not selected_bas:
        return None

    cache_key = tuple(sorted(state.ba_to_region.items()))
    if (
        state.renewables_regions_geojson_cache is not None
        and state.renewables_regions_geojson_key == cache_key
    ):
        return state.renewables_regions_geojson_cache

    region_geometries = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties", {})
        ba_id = str(props.get("rb", ""))
        region_name = state.ba_to_region.get(ba_id)
        if not region_name:
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        region_geometries.setdefault(region_name, []).append(geometry)

    if not region_geometries:
        return None

    dissolved_features = []
    try:
        from shapely.geometry import mapping, shape
        from shapely.ops import unary_union

        for region_name in sorted(region_geometries.keys()):
            geometries = region_geometries.get(region_name, [])
            if not geometries:
                continue
            merged = unary_union([shape(g) for g in geometries])
            dissolved_features.append(
                {
                    "type": "Feature",
                    "properties": {"model_region": region_name},
                    "geometry": mapping(merged),
                }
            )
    except Exception:
        for region_name in sorted(region_geometries.keys()):
            geometries = region_geometries.get(region_name, [])
            polygons = []
            for geom in geometries:
                gtype = geom.get("type")
                coords = geom.get("coordinates")
                if gtype == "Polygon" and isinstance(coords, list):
                    polygons.append(coords)
                elif gtype == "MultiPolygon" and isinstance(coords, list):
                    polygons.extend(coords)
            if not polygons:
                continue
            dissolved_features.append(
                {
                    "type": "Feature",
                    "properties": {"model_region": region_name},
                    "geometry": {"type": "MultiPolygon", "coordinates": polygons},
                }
            )

    if not dissolved_features:
        return None

    dissolved_geojson = {
        "type": "FeatureCollection",
        "features": dissolved_features,
    }
    state.renewables_regions_geojson_cache = dissolved_geojson
    state.renewables_regions_geojson_key = cache_key
    return dissolved_geojson


def _ensure_renewables_maps():
    if not state.geojson_data:
        return

    map_specs = {
        "landbasedwind": "renewablesWindMap",
        "utilitypv": "renewablesSolarMap",
    }

    for tech, map_id in map_specs.items():
        if state.renewables_maps.get(tech) is not None:
            continue
        map_obj = L.map(
            map_id, to_js({"zoomControl": True, "attributionControl": False})
        )
        map_obj.setView(to_js([39.8, -98.5]), 4)
        L.tileLayer(
            "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            to_js({"maxZoom": 12}),
        ).addTo(map_obj)
        state.renewables_maps[tech] = map_obj

    state.renewables_map_initialized = True


def _render_renewables_maps():
    if not state.region_aggregations:
        return
    _ensure_renewables_maps()

    if not state.renewables_map_initialized:
        return

    filtered_geojson = _build_renewables_geojson_for_selected_bas()
    if not filtered_geojson:
        return

    for tech, map_obj in state.renewables_maps.items():
        existing_layer = state.renewables_map_layers.get(tech)
        if existing_layer is not None:
            map_obj.removeLayer(existing_layer)

        style_proxy = create_proxy(
            lambda feature, t=tech: _renewables_feature_style(feature, t)
        )
        on_each_proxy = create_proxy(
            lambda feature, layer, t=tech: _on_each_renewables_map_feature(
                feature, layer, t
            )
        )

        layer = L.geoJSON(
            to_js(filtered_geojson),
            to_js({"style": style_proxy, "onEachFeature": on_each_proxy}),
        ).addTo(map_obj)
        state.renewables_map_layers[tech] = layer

        try:
            map_obj.fitBounds(layer.getBounds())
        except Exception:
            pass


def _render_renewables_advanced_panel():
    if (
        not isinstance(state.renewables_curve_data, dict)
        or not state.renewables_curve_data
    ):
        _render_renewables_advanced_hint(
            "Compute renewables clusters to populate advanced maps and regional supply curves."
        )
        _render_selected_renewables_region_panel()
        return

    _render_renewables_advanced_hint(
        "Click a region in the wind or solar map to inspect supply curves and set additional included capacity."
    )
    _render_renewables_maps()
    _render_selected_renewables_region_panel()


def invalidate_renewables_maps():
    if not isinstance(state.renewables_maps, dict):
        return
    for map_obj in state.renewables_maps.values():
        try:
            map_obj.invalidateSize(False)
        except Exception:
            pass
    _render_renewables_maps()


def on_renewables_capacity_slider_input(event):
    region_name = state.renewables_selected_region
    tech = state.renewables_selected_tech
    if not region_name or not tech:
        return

    slider = document.getElementById("renewablesRegionCapacitySlider")
    if not slider:
        return

    requested = _safe_float(slider.value, 0.0)
    curve_data = _get_curve_region_data(tech, region_name)
    if not curve_data:
        return

    baseline_capacity = _safe_float(curve_data.get("baseline_capacity_mw", 0.0), 0.0)
    available_capacity = _safe_float(curve_data.get("available_capacity_mw", 0.0), 0.0)
    target = max(0.0, min(requested, available_capacity))

    override_map = _get_renewables_override_map(tech)
    if target <= baseline_capacity + 1e-6:
        override_map.pop(region_name, None)
    else:
        override_map[region_name] = float(target)

    _apply_capacity_override_to_curve_data(tech, region_name, target)
    _render_selected_renewables_region_panel()
    _render_renewables_maps()


async def _refresh_renewables_budget_defaults(event=None):
    wind_budget_el = document.getElementById("renewablesWindBudgetCount")
    solar_budget_el = document.getElementById("renewablesSolarBudgetCount")

    if not wind_budget_el or not solar_budget_el:
        return

    if not state.region_aggregations:
        _update_renewables_suggested_counts(0, 0)
        return
    if state.resource_group_assignments is None:
        _update_renewables_suggested_counts(0, 0)
        return
    if not state.reeds_annual_demand_avg:
        _update_renewables_suggested_counts(0, 0)
        return

    region_demand, _ = _build_region_demand_map(state.region_aggregations)
    suggested = {}

    for tech in ["landbasedwind", "utilitypv"]:
        config = RENEWABLES_TECH_CONFIG[tech]
        share = _get_renewables_share_for_tech(tech)
        avg_resource_mw = _get_avg_resource_size_for_tech(tech)
        lcoe_df = _load_resource_group_lcoe_df(config["resource_key"])
        if lcoe_df is None:
            suggested[tech] = 0
            continue

        lcoe_df = lcoe_df[["region", "cf", "lcoe", "capacity_mw"]].copy()
        lcoe_df["region"] = lcoe_df["region"].astype(str)
        region_data = _prepare_lcoe_region_data(lcoe_df)
        region_targets = {
            region: region_demand.get(region, 0.0) * share
            for region in region_data.keys()
            if region_demand.get(region, 0.0) * share > 0
        }
        suggested[tech] = _compute_suggested_budget(
            region_data, region_targets, avg_resource_mw
        )

    wind_suggest = int(suggested.get("landbasedwind", 0))
    solar_suggest = int(suggested.get("utilitypv", 0))
    _update_renewables_suggested_counts(wind_suggest, solar_suggest)

    wind_budget_el.value = str(max(1, wind_suggest)) if wind_suggest > 0 else ""
    solar_budget_el.value = str(max(1, solar_suggest)) if solar_suggest > 0 else ""


def on_renewables_budget_inputs_change(event):
    asyncio.create_task(_refresh_renewables_budget_defaults())


async def _compute_renewables_clusters():
    try:
        t_start = time.perf_counter()
        window.console.log("Renewables: _compute_renewables_clusters() started")
        try:
            region_aggs = _get_region_aggregations_or_raise()
        except Exception:
            # Provide a better error message if LCOE files are uploaded
            has_lcoe_wind = state.uploaded_lcoe_onshorewind is not None
            has_lcoe_solar = state.uploaded_lcoe_solar is not None
            if has_lcoe_wind or has_lcoe_solar:
                lcoe_status = []
                if has_lcoe_wind:
                    lcoe_status.append("Wind LCOE uploaded")
                if has_lcoe_solar:
                    lcoe_status.append("Solar LCOE uploaded")
                raise Exception(
                    f"Model regions required: Complete Step 1 (Regions) to define model regions. "
                    f"You have {', '.join(lcoe_status)}, and they will be used once regions are defined. "
                    f"You can either run automatic clustering or use Manual Definition mode to quickly create regions."
                )
            else:
                raise Exception(
                    "Run Step 1 (Regions) first to define model regions, then return to Step 8 (Renewables Clustering)."
                )

        # Check if annual demand data is loaded
        if not state.reeds_annual_demand_avg:
            set_renewables_status("Annual demand data not loaded.", "error")
            return

        wind_share = _get_renewables_share_for_tech("landbasedwind")
        solar_share = _get_renewables_share_for_tech("utilitypv")
        wind_avg_resource_mw = _get_avg_resource_size_for_tech("landbasedwind")
        solar_avg_resource_mw = _get_avg_resource_size_for_tech("utilitypv")

        region_demand, missing_bas = _build_region_demand_map(region_aggs)
        window.console.log(
            f"Renewables: demand regions={len(region_demand):,}, missing_bas={len(missing_bas):,}"
        )

        clusters = []
        summary = {}
        capacity_summary = {}
        baseline_capacity_summary = {}
        available_capacity_summary = {}
        pending_capacity_summary = {}
        curve_data_summary = {}
        floor_notes = []

        # Track whether we have any LCOE data at all
        has_any_lcoe_data = False

        for tech, share in [("landbasedwind", wind_share), ("utilitypv", solar_share)]:
            config = RENEWABLES_TECH_CONFIG[tech]
            avg_resource_mw = (
                wind_avg_resource_mw
                if tech == "landbasedwind"
                else solar_avg_resource_mw
            )
            t_tech_start = time.perf_counter()
            lcoe_df = _load_resource_group_lcoe_df(config["resource_key"])
            if lcoe_df is None:
                window.console.log(
                    f"Renewables: No LCOE data available for {tech}. "
                    f"Skipping this technology."
                )
                continue

            has_any_lcoe_data = True

            lcoe_df = lcoe_df[["region", "cf", "lcoe", "capacity_mw"]].copy()
            lcoe_df["region"] = lcoe_df["region"].astype(str)
            t_prep_start = time.perf_counter()
            region_data = _prepare_lcoe_region_data(lcoe_df)
            t_prep_end = time.perf_counter()
            window.console.log(
                f"Renewables: {tech} region_data={len(region_data):,} prep_s={(t_prep_end - t_prep_start):.2f}"
            )

            region_targets = {
                region: region_demand.get(region, 0.0) * share
                for region in region_data.keys()
                if region_demand.get(region, 0.0) * share > 0
            }
            region_list = sorted(region_targets.keys())
            window.console.log(
                f"Renewables: {tech} regions_with_demand={len(region_list):,}"
            )

            region_caps = {}
            baseline_region_caps = {}
            available_region_caps = {}
            region_ranges = {}
            region_lcoe_max = {}
            region_lcoe_map = {}
            curve_data_by_region = {}
            suggested_total = 0
            override_map = _get_renewables_override_map(tech)

            last_status = 0.0
            for idx, region_name in enumerate(region_list, start=1):
                if idx % 5 == 0:
                    await asyncio.sleep(0)

                target_mwh = region_targets.get(region_name, 0.0)
                if target_mwh <= 0:
                    continue

                data = region_data.get(region_name)
                if not data:
                    continue

                now = time.perf_counter()
                if now - last_status > 0.5:
                    set_renewables_status(
                        f"Processing {tech} {idx}/{len(region_list)}...", "info"
                    )
                    last_status = now

                cum_mwh = data["cum_mwh"]
                if cum_mwh.size == 0:
                    continue

                lcoe_vals = data["lcoe"]
                cap_vals = data["capacity_mw"]
                if lcoe_vals.size == 0 or cap_vals.size == 0:
                    continue

                baseline_cutoff_idx = int(
                    np.searchsorted(cum_mwh, target_mwh, side="left")
                )
                if baseline_cutoff_idx >= cum_mwh.size:
                    baseline_cutoff_idx = cum_mwh.size - 1

                cum_capacity = np.cumsum(cap_vals)
                available_capacity = (
                    float(cum_capacity[-1]) if cum_capacity.size else 0.0
                )
                baseline_capacity = float(cum_capacity[baseline_cutoff_idx])

                requested_capacity = override_map.get(region_name)
                target_capacity = baseline_capacity
                if requested_capacity is not None:
                    target_capacity = max(
                        0.0,
                        min(
                            _safe_float(requested_capacity, baseline_capacity),
                            available_capacity,
                        ),
                    )

                cutoff_idx = _compute_cutoff_idx_from_capacity(
                    cum_capacity, target_capacity
                )
                if cutoff_idx is None:
                    continue

                lcoe_max = float(lcoe_vals[cutoff_idx])
                capacity = float(cum_capacity[cutoff_idx])
                region_caps[region_name] = capacity
                baseline_region_caps[region_name] = baseline_capacity
                available_region_caps[region_name] = available_capacity
                region_ranges[region_name] = float(lcoe_max - lcoe_vals[0])
                region_lcoe_max[region_name] = lcoe_max

                # Capture arrays for intelligent optimization
                if capacity > 0:
                    region_lcoe_map[region_name] = {
                        "lcoe": lcoe_vals[: cutoff_idx + 1],
                        "capacity": cap_vals[: cutoff_idx + 1],
                    }
                    suggested_total += int(math.ceil(capacity / avg_resource_mw))

                curve_data_by_region[region_name] = {
                    "lcoe": lcoe_vals,
                    "capacity_mw": cap_vals,
                    "cum_capacity_mw": cum_capacity,
                    "baseline_cutoff_idx": int(baseline_cutoff_idx),
                    "cutoff_idx": int(cutoff_idx),
                    "baseline_capacity_mw": float(baseline_capacity),
                    "included_capacity_mw": float(capacity),
                    "available_capacity_mw": float(available_capacity),
                    "lcoe_max": float(lcoe_max),
                }

            target_input = _get_int_input(
                (
                    "renewablesWindBudgetCount"
                    if tech == "landbasedwind"
                    else "renewablesSolarBudgetCount"
                ),
                None,
            )
            target_total = target_input if target_input and target_input > 0 else None
            if target_total is None:
                target_total = suggested_total

            region_cfg, total_resources, effective_target, minimum_budget = (
                _compute_region_bin_cluster_config(
                    region_caps,
                    region_lcoe_map,  # Pass the map here
                    region_ranges,
                    target_total,
                    config["mw_per_bin"],
                    config["n_clusters"],
                )
            )

            if target_total < minimum_budget:
                label = "wind" if tech == "landbasedwind" else "solar"
                floor_notes.append(f"{label} budget raised to minimum {minimum_budget}")
                budget_input = document.getElementById(
                    "renewablesWindBudgetCount"
                    if tech == "landbasedwind"
                    else "renewablesSolarBudgetCount"
                )
                if budget_input:
                    budget_input.value = str(minimum_budget)

            for region_name, lcoe_max in sorted(region_lcoe_max.items()):
                cfg = region_cfg.get(region_name)
                if not cfg:
                    continue
                clusters.append(
                    {
                        "region": region_name,
                        "technology": tech,
                        "filter": [{"feature": "lcoe", "max": round(lcoe_max, 2)}],
                        "bin": [
                            {
                                "feature": "lcoe",
                                "weights": "capacity_mw",
                                "q": cfg["q"],
                                "mw_per_bin": cfg["mw_per_bin"],
                            }
                        ],
                        "cluster": [
                            {
                                "feature": config["cluster_feature"],
                                "n_clusters": cfg["n_clusters"],
                                "method": "agg",
                            }
                        ],
                    }
                )

            capacity_summary[tech] = {
                region_name: float(capacity)
                for region_name, capacity in sorted(region_caps.items())
                if float(capacity) > 0
            }
            baseline_capacity_summary[tech] = {
                region_name: float(capacity)
                for region_name, capacity in sorted(baseline_region_caps.items())
                if float(capacity) > 0
            }
            available_capacity_summary[tech] = {
                region_name: float(capacity)
                for region_name, capacity in sorted(available_region_caps.items())
                if float(capacity) > 0
            }
            pending_capacity_summary[tech] = {
                region_name: float(capacity)
                for region_name, capacity in sorted(region_caps.items())
                if float(capacity) > 0
            }
            curve_data_summary[tech] = curve_data_by_region

            summary[tech] = {
                "suggested": suggested_total,
                "target": target_total,
                "effective_target": effective_target,
                "minimum_budget": minimum_budget,
                "total": total_resources,
            }
            t_tech_end = time.perf_counter()
            window.console.log(
                f"Renewables: {tech} done in {(t_tech_end - t_tech_start):.2f}s"
            )

        # Check if we have any LCOE data at all
        if not has_any_lcoe_data:
            set_renewables_status(
                "No LCOE data available. Either generate resource groups in Step 7 "
                "or upload LCOE parquet/CSV files for wind and/or solar.",
                "error",
            )
            return

        state.renewables_clusters = clusters
        state.renewables_clusters_info = summary
        state.renewables_region_capacity_mw = capacity_summary
        state.renewables_region_base_capacity_mw = baseline_capacity_summary
        state.renewables_region_available_mw = available_capacity_summary
        state.renewables_pending_region_capacity_mw = pending_capacity_summary
        state.renewables_curve_data = curve_data_summary

        if state.renewables_selected_region:
            selected_data = _get_curve_region_data(
                state.renewables_selected_tech, state.renewables_selected_region
            )
            if not selected_data:
                state.renewables_selected_region = None

        if not state.renewables_selected_region:
            for tech in ["landbasedwind", "utilitypv"]:
                tech_regions = curve_data_summary.get(tech, {})
                if tech_regions:
                    state.renewables_selected_tech = tech
                    state.renewables_selected_region = sorted(tech_regions.keys())[0]
                    break

        wind_suggest = summary.get("landbasedwind", {}).get("suggested", 0)
        solar_suggest = summary.get("utilitypv", {}).get("suggested", 0)
        _update_renewables_suggested_counts(wind_suggest, solar_suggest)
        _render_renewables_preview()

        missing_msg = (
            f" Missing demand data for {len(missing_bas)} BAs." if missing_bas else ""
        )
        floor_msg = f" {'; '.join(floor_notes)}." if floor_notes else ""
        set_renewables_status(
            "Renewables clusters computed. "
            "Extra budget is allocated to reduce weighted LCOE standard deviation."
            f"{floor_msg}{missing_msg}",
            "success",
        )
        t_end = time.perf_counter()
        window.console.log(f"Renewables: total_time_s={(t_end - t_start):.2f}")
    except Exception as exc:
        set_renewables_status(f"Renewables clustering error: {exc}", "error")


def on_compute_renewables_clusters(event):
    asyncio.create_task(_compute_renewables_clusters())


def generate_resources_settings():
    region_aggs = _get_region_aggregations_or_raise()

    # Existing generator clustering: prefer plant clustering output if available
    cluster_settings = state.plant_cluster_settings or {}
    existing_num_clusters = cluster_settings.get("num_clusters")
    if not isinstance(existing_num_clusters, dict) or not existing_num_clusters:
        # Minimal fallback (users should run plant clustering)
        existing_num_clusters = {
            "Conventional Steam Coal": 1,
            "Natural Gas Fired Combined Cycle": 1,
            "Natural Gas Fired Combustion Turbine": 1,
            "Nuclear": 1,
            "Conventional Hydroelectric": 1,
            "Solar Photovoltaic": 1,
            "Onshore Wind Turbine": 1,
            "Batteries": 1,
        }

    group_tech = bool(cluster_settings.get("group_technologies", True))
    tech_groups = cluster_settings.get("tech_groups")
    if not isinstance(tech_groups, dict):
        tech_groups = {
            "Biomass": [
                "Wood/Wood Waste Biomass",
                "Landfill Gas",
                "Municipal Solid Waste",
                "Other Waste Biomass",
            ],
            "Other_peaker": [
                "Natural Gas Internal Combustion Engine",
                "Other Natural Gas",
                "Other Gases",
                "Petroleum Liquids",
            ],
        }

    alt_num_clusters = cluster_settings.get("alt_num_clusters")
    if not isinstance(alt_num_clusters, dict) or not alt_num_clusters:
        alt_num_clusters = None

    # Check if any resources are tagged to a specific planning year
    has_year_specific = _has_year_specific_resources()
    # Compute once here; reused for new_resources, resource_modifiers, and
    # modified_new_resources below to avoid three separate iterations.
    specific_years = _get_year_specific_years() if has_year_specific else []

    # Base new-build resources — only "all" year entries
    base_new_resources = [
        [r["technology"], r["tech_detail"], r["cost_case"], r["size_mw"]]
        for r in state.new_resources
        if r.get("planning_year") == "all"
    ]
    # Also include "all" year modified resources that are attribute-only (no identity
    # or fuel changes). These go into resource_modifiers for their overrides but must
    # also appear in new_resources so PowerGenome loads them as new build options.
    _seen = {tuple(e) for e in base_new_resources}
    _append_attribute_only_modified_resources(base_new_resources, _seen, year=None)

    if has_year_specific:
        # Build year-specific values and only emit a keyed structure when values
        # actually differ by year.
        year_new_resources = {
            year: _build_new_resources_for_year(year) for year in specific_years
        }
        differs_by_year = any(
            year_list != base_new_resources for year_list in year_new_resources.values()
        )

        if differs_by_year:
            new_resources = {"default": base_new_resources}
            for year, year_list in year_new_resources.items():
                if year_list != base_new_resources:
                    new_resources[year] = year_list
        else:
            new_resources = base_new_resources
    else:
        new_resources = base_new_resources

    # Hydro defaults
    hydro_factor = 2
    regional_hydro = compute_regional_hydro_factor(region_aggs)

    # Resources inputs
    resource_data_year = int(
        _get_select_value(document.getElementById("targetUsdYear"), 2024)
    )
    # Keep resource_data_year separate from target_usd_year for future; default to targetUsdYear for MVP
    resource_financial_case = "Market"
    resource_cap_recovery_years = 20

    # Read interconnection cost from input field, defaulting to 100000 if not set
    interconnect_el = document.getElementById("interconnectCapexMw")
    try:
        interconnect_capex_mw = (
            int(interconnect_el.value)
            if interconnect_el and interconnect_el.value
            else 100000
        )
    except (ValueError, AttributeError):
        interconnect_capex_mw = 100000

    out = {
        "cluster_with_retired_gens": True,
        "num_clusters": existing_num_clusters,
        "group_technologies": bool(group_tech),
        "tech_groups": tech_groups,
        "regional_no_grouping": None,
        "alt_num_clusters": alt_num_clusters if alt_num_clusters is not None else None,
        "hydro_factor": hydro_factor,
        "regional_hydro_factor": regional_hydro if regional_hydro else None,
        "energy_storage_duration": {
            "Hydroelectric Pumped Storage": 15.5,
            "Batteries": 2,
        },
        "resource_data_year": resource_data_year,
        "resource_financial_case": resource_financial_case,
        "resource_cap_recovery_years": resource_cap_recovery_years,
        "alt_resource_cap_recovery_years": {
            "Battery": 15,
            "Nuclear": 40,
        },
        "new_resources": new_resources,
        "interconnect_capex_mw": interconnect_capex_mw,
        "cache_resource_clusters": True,
        "use_resource_clusters_cache": True,
        "renewables_clusters": (
            state.renewables_clusters
            if isinstance(state.renewables_clusters, list) and state.renewables_clusters
            else DEFAULT_RENEWABLES_CLUSTERS
        ),
    }

    # Build base resource_modifiers (for "all" year resources only).
    # Every new resource gets an entry with at least "technology" and "tech_detail".
    # Additionally, apply default modifiers (e.g., battery storage O&M defaults).
    # For modified_new_resources, only include attribute-only overrides (no identity
    # or fuel changes) with a non-empty attr_modifiers dict. Keys are translated from
    # internal UI names to ATB column-style names (e.g. variable_o_m_mwh → Var_OM_Cost_per_MWh).
    base_resource_modifiers = {}

    # First, add all "all" year new_resources with default modifiers
    for r in state.new_resources:
        if r.get("planning_year") == "all":
            tech = r["technology"]
            detail = r["tech_detail"]
            key = _auto_modified_key(tech, detail)
            modifier_dict = {
                "technology": tech,
                "tech_detail": detail,
            }
            defaults = _get_default_resource_modifiers(tech, detail)
            modifier_dict.update(defaults)
            base_resource_modifiers[key] = modifier_dict

    # Then, add entries from modified_new_resources that have attribute modifiers
    if state.modified_new_resources:
        for k, v in sorted(state.modified_new_resources.items()):
            # Only include "all" year (base) entries
            if v.get("planning_year", "all") != "all":
                continue
            attr_mods = v.get("attr_modifiers")
            if not isinstance(attr_mods, dict) or not attr_mods:
                continue
            # Skip entries that change resource identity – those go in modified_new_resources
            identity_changes = (
                v.get("new_technology") != v.get("technology")
                or v.get("new_tech_detail") != v.get("tech_detail")
                or v.get("new_cost_case") != v.get("cost_case")
            )
            if identity_changes:
                continue
            # Skip entries that introduce a new fuel type
            if v.get("fuel_type") == "new":
                continue
            modifier_dict = {
                "technology": v["technology"],
                "tech_detail": v["tech_detail"],
            }
            for ui_key, val in attr_mods.items():
                atb_key = _UI_TO_ATB_KEY.get(ui_key, ui_key)
                modifier_dict[atb_key] = val
            base_resource_modifiers[k] = modifier_dict

    if has_year_specific:
        # Build year-specific effective values, then nest year keys per-field
        # to avoid repeating every resource entry under top-level year keys.
        year_resource_modifiers = {
            year: (_build_resource_modifiers_for_year(year) or {})
            for year in specific_years
        }
        resource_modifiers = _build_nested_year_keyed_resource_modifiers(
            base_resource_modifiers,
            year_resource_modifiers,
        )
    else:
        resource_modifiers = base_resource_modifiers

    if resource_modifiers:
        out["resource_modifiers"] = resource_modifiers

    # Build base modified_new_resources (custom fuels / identity changes, "all" year only)
    base_modified_with_fuel = {}
    if state.modified_new_resources:
        for k, v in sorted(state.modified_new_resources.items()):
            # Only include "all" year (base) entries
            if v.get("planning_year", "all") != "all":
                continue
            if v.get("fuel_type") == "new" or (
                v.get("new_technology") != v.get("technology")
                or v.get("new_tech_detail") != v.get("tech_detail")
                or v.get("new_cost_case") != v.get("cost_case")
            ):
                entry = {
                    "technology": v["technology"],
                    "tech_detail": v["tech_detail"],
                    "cost_case": v["cost_case"],
                    "size_mw": v["size_mw"],
                    "new_technology": v["new_technology"],
                    "new_tech_detail": v["new_tech_detail"],
                    "new_cost_case": v["new_cost_case"],
                }
                attr_mods = v.get("attr_modifiers")
                if isinstance(attr_mods, dict) and attr_mods:
                    for ui_key, val in attr_mods.items():
                        atb_key = _UI_TO_ATB_KEY.get(ui_key, ui_key)
                        entry[atb_key] = val
                base_modified_with_fuel[k] = entry

    if has_year_specific:
        # Build year-specific values and only emit `default` when values differ by
        # year relative to base entries.
        year_modified_new_resources = {
            year: year_modified
            for year in specific_years
            for year_modified in [_build_modified_new_resources_for_year(year)]
            if year_modified is not None
        }
        differs_by_year = any(
            year_modified != base_modified_with_fuel
            for year_modified in year_modified_new_resources.values()
        )

        if differs_by_year:
            modified_new_resources_keyed = {}
            if base_modified_with_fuel:
                modified_new_resources_keyed["default"] = base_modified_with_fuel
            for year, year_modified in year_modified_new_resources.items():
                if year_modified != base_modified_with_fuel:
                    modified_new_resources_keyed[year] = year_modified
            if modified_new_resources_keyed:
                out["modified_new_resources"] = modified_new_resources_keyed
        elif base_modified_with_fuel:
            out["modified_new_resources"] = base_modified_with_fuel
    else:
        if base_modified_with_fuel:
            out["modified_new_resources"] = base_modified_with_fuel

    # Remove nulls to keep YAML clean
    out = {k: v for k, v in out.items() if v is not None}
    resources_yaml = yaml.dump(out, default_flow_style=False, sort_keys=False)
    comment_block = _format_renewables_capacity_comments()
    return f"{comment_block}\n{resources_yaml}" if comment_block else resources_yaml


# ---------------------------------------------------------------------------
# Scenario management helpers (year-specific resource queries)
# ---------------------------------------------------------------------------


def _has_year_specific_resources():
    """Return True if any new or modified resources target a specific planning year."""
    for r in state.new_resources:
        if r.get("planning_year") != "all":
            return True
    for v in state.modified_new_resources.values():
        if v.get("planning_year", "all") != "all":
            return True
    return False


def _get_year_specific_years():
    """Return the sorted set of specific planning years referenced by resources."""
    years = set()
    for r in state.new_resources:
        py = r.get("planning_year")
        if py != "all":
            years.add(int(py))
    for v in state.modified_new_resources.values():
        py = v.get("planning_year", "all")
        if py != "all":
            years.add(int(py))
    return sorted(years)


def _append_attribute_only_modified_resources(entries, existing_set, year=None):
    """Append attribute-only modified resources to ``entries`` in-place.

    An "attribute-only" entry is one where the resource identity is unchanged
    (``new_technology == technology``, ``new_tech_detail == tech_detail``,
    ``new_cost_case == cost_case``) and the fuel type is not ``"new"``.
    These entries must appear in ``new_resources`` so PowerGenome loads them
    as buildable options, even though their modifiers live in
    ``resource_modifiers``.

    Args:
        entries: The list to extend (modified in-place).
        existing_set: A set of already-seen ``(technology, tech_detail,
            cost_case, size_mw)`` tuples used for deduplication (mutated).
        year: If given, only include entries with ``planning_year == "all"``
            or ``planning_year == year``.  If ``None``, only "all" entries
            are included (the base / no-year-specific path).
    """
    for _, mod_res in sorted(state.modified_new_resources.items()):
        py = mod_res.get("planning_year", "all")
        if year is None:
            if py != "all":
                continue
        else:
            if py != "all" and py != year:
                continue
        if mod_res.get("fuel_type") == "new":
            continue
        if (
            mod_res.get("new_technology") != mod_res.get("technology")
            or mod_res.get("new_tech_detail") != mod_res.get("tech_detail")
            or mod_res.get("new_cost_case") != mod_res.get("cost_case")
        ):
            continue
        entry_key = (
            mod_res["technology"],
            mod_res["tech_detail"],
            mod_res["cost_case"],
            mod_res["size_mw"],
        )
        if entry_key not in existing_set:
            existing_set.add(entry_key)
            entries.append(list(entry_key))


def _build_new_resources_for_year(year):
    """Build the ``new_resources`` list for a specific planning year.

    Combines: all "all" resources + resources tagged to this year, including
    attribute-only modified resources (no identity/fuel changes) for "all" or
    the given year.
    """
    base = [
        [r["technology"], r["tech_detail"], r["cost_case"], r["size_mw"]]
        for r in state.new_resources
        if r.get("planning_year") == "all"
    ]
    year_specific = [
        [r["technology"], r["tech_detail"], r["cost_case"], r["size_mw"]]
        for r in state.new_resources
        if r.get("planning_year") == year
    ]
    result = base + year_specific
    # Also include attribute-only modified resources applicable to this year
    _seen = {tuple(e) for e in result}
    _append_attribute_only_modified_resources(result, _seen, year=year)
    return result


def _build_resource_modifiers_for_year(year):
    """Build the ``resource_modifiers`` dict for a specific planning year.

    Merges: all "all" entries + year-specific entries (which override by key).
    Includes all new_resources with default modifiers, plus only attribute-only
    overrides from modified_new_resources (no identity/fuel changes).
    """
    modifiers = {}

    # First, add all "all" year new_resources with default modifiers
    for r in state.new_resources:
        if r.get("planning_year") == "all":
            tech = r["technology"]
            detail = r["tech_detail"]
            # Generate a default key from tech and detail
            key = _auto_modified_key(tech, detail)
            modifier_dict = {
                "technology": tech,
                "tech_detail": detail,
            }
            # Add any default modifiers for this resource type
            defaults = _get_default_resource_modifiers(tech, detail)
            modifier_dict.update(defaults)
            modifiers[key] = modifier_dict

    # Add year-specific new_resources
    for r in state.new_resources:
        if r.get("planning_year") == year:
            tech = r["technology"]
            detail = r["tech_detail"]
            # Generate a default key from tech and detail
            key = _auto_modified_key(tech, detail)
            modifier_dict = {
                "technology": tech,
                "tech_detail": detail,
            }
            # Add any default modifiers for this resource type
            defaults = _get_default_resource_modifiers(tech, detail)
            modifier_dict.update(defaults)
            modifiers[key] = modifier_dict

    # Then, add entries from modified_new_resources that have attribute modifiers
    for k, v in sorted(state.modified_new_resources.items()):
        py = v.get("planning_year", "all")
        if py != "all" and py != year:
            continue

        attr_mods = v.get("attr_modifiers")
        if not isinstance(attr_mods, dict) or not attr_mods:
            continue
        identity_changes = (
            v.get("new_technology") != v.get("technology")
            or v.get("new_tech_detail") != v.get("tech_detail")
            or v.get("new_cost_case") != v.get("cost_case")
        )
        if identity_changes:
            continue
        if v.get("fuel_type") == "new":
            continue

        modifier_dict = {
            "technology": v["technology"],
            "tech_detail": v["tech_detail"],
        }
        for ui_key, val in attr_mods.items():
            atb_key = _UI_TO_ATB_KEY.get(ui_key, ui_key)
            modifier_dict[atb_key] = val
        # Override the entry if it already exists from new_resources
        modifiers[k] = modifier_dict

    return modifiers if modifiers else None


def _build_modified_new_resources_for_year(year):
    """Build the ``modified_new_resources`` dict for a specific planning year.

    Merges: all "all" entries + year-specific entries (which override by key).
    Only includes entries with custom fuel or identity changes.
    """
    result = {}

    for k, v in sorted(state.modified_new_resources.items()):
        py = v.get("planning_year", "all")
        if py != "all" and py != year:
            continue

        if not (
            v.get("fuel_type") == "new"
            or v.get("new_technology") != v.get("technology")
            or v.get("new_tech_detail") != v.get("tech_detail")
            or v.get("new_cost_case") != v.get("cost_case")
        ):
            continue

        entry = {
            "technology": v["technology"],
            "tech_detail": v["tech_detail"],
            "cost_case": v["cost_case"],
            "size_mw": v["size_mw"],
            "new_technology": v["new_technology"],
            "new_tech_detail": v["new_tech_detail"],
            "new_cost_case": v["new_cost_case"],
        }
        attr_mods = v.get("attr_modifiers")
        if isinstance(attr_mods, dict) and attr_mods:
            for ui_key, val in attr_mods.items():
                atb_key = _UI_TO_ATB_KEY.get(ui_key, ui_key)
                entry[atb_key] = val
        result[k] = entry

    return result if result else None


def _neutral_year_keyed_modifier_value(value):
    """Return a neutral fallback for year-keyed modifier fields."""
    if isinstance(value, list) and len(value) == 2:
        op = str(value[0]).lower()
        if op in {"mul", "truediv"}:
            return [op, 1]
        if op in {"add", "sub"}:
            return [op, 0]
    if isinstance(value, (int, float)):
        return ["mul", 1]
    return value


def _build_nested_year_keyed_resource_modifiers(
    base_resource_modifiers, year_resource_modifiers
):
    """Nest year keys at the field level for ``resource_modifiers`` output."""
    base = base_resource_modifiers if isinstance(base_resource_modifiers, dict) else {}
    year_map = (
        year_resource_modifiers if isinstance(year_resource_modifiers, dict) else {}
    )

    # Preserve existing flat structure as the baseline.
    out = {k: dict(v) for k, v in base.items()}

    all_resource_keys = set(base.keys())
    for year_mods in year_map.values():
        if isinstance(year_mods, dict):
            all_resource_keys.update(year_mods.keys())

    for resource_key in sorted(all_resource_keys):
        base_entry = base.get(resource_key, {})

        if resource_key not in out:
            identity_source = None
            for _, year_mods in sorted(year_map.items()):
                if isinstance(year_mods, dict) and resource_key in year_mods:
                    identity_source = year_mods[resource_key]
                    break
            if isinstance(identity_source, dict):
                out[resource_key] = {
                    "technology": identity_source.get("technology"),
                    "tech_detail": identity_source.get("tech_detail"),
                }
            else:
                continue

        all_fields = set(base_entry.keys())
        for year_mods in year_map.values():
            if (
                isinstance(year_mods, dict)
                and resource_key in year_mods
                and isinstance(year_mods[resource_key], dict)
            ):
                all_fields.update(year_mods[resource_key].keys())

        all_fields.discard("technology")
        all_fields.discard("tech_detail")

        for field in sorted(all_fields):
            base_has_field = field in base_entry
            base_val = base_entry.get(field)

            values_by_year = {}
            for year, year_mods in sorted(year_map.items()):
                entry = (
                    year_mods.get(resource_key, {})
                    if isinstance(year_mods, dict)
                    else {}
                )
                if isinstance(entry, dict) and field in entry:
                    values_by_year[year] = entry[field]
                else:
                    values_by_year[year] = base_val if base_has_field else None

            if base_has_field:
                differs = any(v != base_val for v in values_by_year.values())
            else:
                differs = any(v is not None for v in values_by_year.values())

            if not differs:
                if base_has_field:
                    out[resource_key][field] = base_val
                continue

            nested = {}
            if base_has_field:
                nested["default"] = base_val
            else:
                sample = next(
                    (v for v in values_by_year.values() if v is not None), None
                )
                if sample is not None:
                    nested["default"] = _neutral_year_keyed_modifier_value(sample)

            for year, year_val in values_by_year.items():
                if year_val is None:
                    continue
                if (not base_has_field) or (year_val != base_val):
                    nested[year] = year_val

            if nested:
                out[resource_key][field] = nested

    return out


def generate_fuels_settings():
    fuel_year = int(_get_select_value(document.getElementById("fuelDataYear"), 2026))

    # Collect fuel scenarios from the dynamically rendered container.
    container = document.getElementById("fuelScenariosContainer")
    fuel_scenarios: dict[str, str] = {}
    if container:
        for sel_el in container.querySelectorAll("select"):
            sel_id = sel_el.id
            if not sel_id.startswith("fuelScenario_"):
                continue
            fuel_key = sel_id[len("fuelScenario_") :]
            fuel_scenarios[fuel_key] = str(
                _get_select_value(sel_el, "reference") or "reference"
            )

    # If the container was empty (e.g. user jumped straight to export), populate
    # first then re-read.
    if not fuel_scenarios:
        populate_fuel_scenario_selects()
        if container:
            for sel_el in container.querySelectorAll("select"):
                sel_id = sel_el.id
                if not sel_id.startswith("fuelScenario_"):
                    continue
                fuel_key = sel_id[len("fuelScenario_") :]
                fuel_scenarios[fuel_key] = str(
                    _get_select_value(sel_el, "reference") or "reference"
                )

    # Guarantee standard fuels are always present.
    for std_fuel in ("coal", "naturalgas", "distillate", "uranium"):
        fuel_scenarios.setdefault(std_fuel, "reference")

    tech_fuel_map = {
        "Conventional Steam Coal": "coal",
        "Natural Gas Fired Combined Cycle": "naturalgas",
        "Natural Gas Fired Combustion Turbine": "naturalgas",
        "Natural Gas Steam Turbine": "naturalgas",
        "Natural Gas Internal Combustion Engine": "naturalgas",
        "Other_peaker": "naturalgas",
        "NaturalGas": "naturalgas",
        "Petroleum Liquids": "distillate",
        "Nuclear": "uranium",
    }

    fuel_emission_factors = {
        "naturalgas": 0.05306,
        "coal": 0.09552,
        "distillate": 0.07315,
    }

    user_fuel_price = {}

    # Modified resources can introduce new fuels and/or new mappings
    for _, item in state.modified_new_resources.items():
        prefix = _prefix_from_new_technology(item.get("new_technology"))
        if not prefix:
            continue
        fuel_type = item.get("fuel_type", "standard")
        if fuel_type == "none":
            # Non-thermal resource (e.g. wind, solar, storage) – no fuel mapping needed
            continue
        if fuel_type == "new":
            fuel_name = str(item.get("new_fuel_name") or "").strip()
            if not fuel_name:
                continue
            fuel_scenarios.setdefault(fuel_name, "reference")
            tech_fuel_map[prefix] = fuel_name
            user_fuel_price[fuel_name] = float(item.get("new_fuel_price", 0.0))
            fuel_emission_factors[fuel_name] = float(
                item.get("new_fuel_emission_factor", 0.0)
            )
        else:
            std_fuel = str(item.get("standard_fuel") or "naturalgas")
            tech_fuel_map[prefix] = std_fuel

    out = {
        "fuel_data_year": fuel_year,
        "fuel_scenarios": fuel_scenarios,
        "tech_fuel_map": tech_fuel_map,
        "fuel_emission_factors": fuel_emission_factors,
    }
    if user_fuel_price:
        out["user_fuel_price"] = user_fuel_price

    return yaml.dump(out, default_flow_style=False, sort_keys=False)


def generate_transmission_settings():
    out = {
        "tx_expansion_per_period": 1.0,
        "tx_expansion_mw_per_period": 400,
    }
    return yaml.dump(out, default_flow_style=False, sort_keys=False)


def generate_distributed_gen_settings():
    out = {
        "dg_as_resource": True,
        "avg_distribution_loss": 0.0453,
    }
    return yaml.dump(out, default_flow_style=False, sort_keys=False)


def generate_startup_costs_settings():
    out = {
        "startup_fuel_use": {
            "Conventional Steam Coal": 16.5,
            "Natural Gas Fired Combined Cycle": 2.0,
            "Natural Gas Fired Combustion Turbine": 3.5,
            "Natural Gas Steam Turbine": 13.7,
            "NaturalGas_1-on-1": 2.0,
            "NaturalGas_Combustion": 3.5,
        },
        "startup_vom_costs_mw": {
            "coal_small_sub": 2.81,
            "coal_large_sub": 2.69,
            "coal_supercritical": 2.98,
            "gas_cc": 1.03,
            "gas_large_ct": 0.77,
            "gas_aero_ct": 0.70,
            "gas_steam": 1.03,
            "nuclear": 5.4,
        },
        "startup_vom_costs_usd_year": 2011,
        "startup_costs_type": "startup_costs_per_cold_start_mw",
        "startup_costs_per_cold_start_mw": {
            "coal_small_sub": 147,
            "coal_large_sub": 105,
            "coal_supercritical": 104,
            "gas_cc": 79,
            "gas_large_ct": 103,
            "gas_aero_ct": 32,
            "gas_steam": 75,
            "nuclear": 210,
        },
        "startup_costs_per_cold_start_usd_year": 2011,
        "existing_startup_costs_tech_map": {
            "Conventional Steam Coal": "coal_large_sub",
            "Natural Gas Fired Combined Cycle": "gas_cc",
            "Natural Gas Fired Combustion Turbine": "gas_large_ct",
            "Natural Gas Steam Turbine": "gas_steam",
            "Nuclear": "nuclear",
            "Other_peaker": "gas_steam",
        },
        "new_build_startup_costs": {
            "Coal_CCS30": "coal_supercritical",
            "Coal_CCS90": "coal_supercritical",
            "Coal_IGCC": "coal_supercritical",
            "Coal_new": "coal_supercritical",
            "NaturalGas_CT": "gas_large_ct",
            "NaturalGas_CC": "gas_cc",
            "NaturalGas_CCS100": "gas_cc",
            "Nuclear_Nuclear": "nuclear",
            "NaturalGas_1-on-1": "gas_cc",
            "NaturalGas_Combustion": "gas_large_ct",
        },
    }
    return yaml.dump(out, default_flow_style=False, sort_keys=False)


def generate_resource_tags_settings():
    # Base tag names (ESR tags will be added dynamically)
    base_tag_names = [
        "THERM",
        "VRE",
        "Num_VRE_Bins",
        "MUST_RUN",
        "STOR",
        "FLEX",
        "HYDRO",
        "LDS",
        "Commit",
    ]

    # Collect ESR tag names from state (if ESR analysis has been run)
    esr_tag_names = []
    if state.esr_map:
        esr_tag_names = sorted(state.esr_map.keys(), key=lambda x: int(x.split("_")[1]))

    # Remaining tag names
    suffix_tag_names = [
        "New_Build",
        "CapRes_1",
        "CapRes_2",
        "MinCapTag_1",
        "MinCapTag_2",
        "MinCapTag_3",
        "Reg_Max",
        "Rsv_Max",
    ]

    tag_names = base_tag_names + esr_tag_names + suffix_tag_names

    values = {
        "THERM": {
            "Conventional Steam Coal": 1,
            "Natural Gas Fired Combined Cycle": 1,
            "Natural Gas Fired Combustion Turbine": 1,
            "Natural Gas Internal Combustion Engine": 1,
            "Natural Gas Steam Turbine": 1,
            "Other_peaker": 1,
            "Petroleum Liquids": 1,
            "Nuclear": 1,
            "NaturalGas_": 1,
        },
        "VRE": {
            "LandbasedWind": 1,
            "Onshore Wind": 1,
            "OffshoreWind": 1,
            "Offshore Wind Turbine": 1,
            "Solar Photovoltaic": 1,
            "UtilityPV": 1,
        },
        "Num_VRE_Bins": {
            "LandbasedWind": 1,
            "Onshore Wind": 1,
            "OffshoreWind": 1,
            "Offshore Wind Turbine": 1,
            "Solar Photovoltaic": 1,
            "UtilityPV": 1,
        },
        "STOR": {
            "Batteries": 1,
            "Battery": 1,
            "Hydroelectric Pumped Storage": 1,
        },
        "HYDRO": {
            "Conventional Hydroelectric": 1,
            "Hydropower": 1,
        },
        "MUST_RUN": {
            "Small Hydroelectric": 1,
            "Geothermal": 1,
            "Wood/Wood Waste Biomass": 1,
            "Biomass": 1,
            "distributed_gen": 1,
            "Run of River Hydroelectric": 1,
        },
        "Commit": {
            "Conventional Steam Coal": 1,
            "Natural Gas Fired Combined Cycle": 1,
            "Natural Gas Fired Combustion Turbine": 1,
            "Natural Gas Internal Combustion Engine": 1,
            "Natural Gas Steam Turbine": 1,
            "Other_peaker": 1,
            "Petroleum Liquids": 1,
            "Nuclear": 1,
        },
        "New_Build": {
            "NaturalGas": 1,
            "LandbasedWind": 1,
            "UtilityPV": 1,
            "Battery": 1,
            "Nuclear_Nuclear": 1,
        },
        "CapRes_1": {
            "Conventional Steam Coal": 0.9,
            "Natural Gas Fired Combined Cycle": 0.9,
            "Natural Gas Fired Combustion Turbine": 0.9,
            "Natural Gas Internal Combustion Engine": 0.9,
            "Natural Gas Steam Turbine": 0.9,
            "Other_peaker": 0.9,
            "Petroleum Liquids": 0.9,
            "Nuclear": 0.9,
            "LandbasedWind": 0.8,
            "UtilityPV": 0.8,
            "Battery": 0.95,
            "Hydroelectric Pumped Storage": 0.95,
        },
        "CapRes_2": {
            "Conventional Steam Coal": 0.9,
            "Natural Gas Fired Combined Cycle": 0.9,
            "Natural Gas Fired Combustion Turbine": 0.9,
            "Natural Gas Internal Combustion Engine": 0.9,
            "Natural Gas Steam Turbine": 0.9,
            "Other_peaker": 0.9,
            "Petroleum Liquids": 0.9,
            "Nuclear": 0.9,
            "LandbasedWind": 0.8,
            "UtilityPV": 0.8,
            "Battery": 0.95,
            "Hydroelectric Pumped Storage": 0.95,
        },
        "MinCapTag_1": {},
        "MinCapTag_2": {},
        "MinCapTag_3": {},
        "FLEX": {},
        "LDS": {},
        "Reg_Max": {},
        "Rsv_Max": {},
    }

    # Add empty ESR entries to model_tag_values (actual values are in regional_tag_values)
    for esr_name in esr_tag_names:
        values[esr_name] = {}

    # Collect CCS technologies from modified resources and regular new resources
    ccs_technologies = {}  # tech_name -> capture_fraction
    ccs_costs = {}  # tech_name -> disposal cost

    # Check modified resources for CCS
    for _, item in state.modified_new_resources.items():
        ccs_fraction = item.get("ccs_capture_fraction")
        if ccs_fraction is not None:
            # Use the new technology info to build the name
            new_tech = item.get("new_technology", "")
            new_detail = item.get("new_tech_detail", "")
            if new_tech and new_detail:
                tech_name = f"{new_tech}_{new_detail}"
                ccs_technologies[tech_name] = ccs_fraction
                ccs_costs[tech_name] = item.get(
                    "ccs_disposal_cost", state.ccs_disposal_cost
                )

    # Check regular new resources from state for CCS
    for resource in state.new_resources:
        tech = resource["technology"]
        detail = resource["tech_detail"]
        ccs_fraction = _extract_ccs_capture_fraction(detail)
        if ccs_fraction is not None:
            # Build the full tech name as it appears in PowerGenome: Technology_TechDetail
            tech_name = f"{tech}_{detail}"
            ccs_technologies[tech_name] = ccs_fraction
            ccs_costs[tech_name] = state.ccs_disposal_cost_map.get(
                tech_name, state.ccs_disposal_cost
            )

    # Apply modified resource tag choices
    for _, item in state.modified_new_resources.items():
        prefix = _prefix_from_new_technology(item.get("new_technology"))
        if not prefix:
            continue
        tag_class = str(item.get("tag_class") or "").strip()
        if tag_class:
            values.setdefault(tag_class, {})[prefix] = 1
        if tag_class == "THERM" and bool(item.get("is_commit")):
            values.setdefault("Commit", {})[prefix] = 1
        values.setdefault("New_Build", {})[prefix] = 1

    # Add CCS-related tags if any CCS technologies are present
    if ccs_technologies:
        # Add CCS tag names to the tag list
        tag_names.extend(
            [
                "CO2_Capture_Fraction",
                "CO2_Capture_Fraction_Startup",
                "CCS_Disposal_Cost_per_Metric_Ton",
            ]
        )

        values["CO2_Capture_Fraction"] = {}
        values["CO2_Capture_Fraction_Startup"] = {}
        values["CCS_Disposal_Cost_per_Metric_Ton"] = {}

        for tech_name, capture_fraction in ccs_technologies.items():
            values["CO2_Capture_Fraction"][tech_name] = capture_fraction
            values["CO2_Capture_Fraction_Startup"][tech_name] = capture_fraction
            values["CCS_Disposal_Cost_per_Metric_Ton"][tech_name] = ccs_costs.get(
                tech_name, state.ccs_disposal_cost
            )

    out = {
        "model_tag_names": tag_names,
        "default_model_tag": 0,
        "model_tag_values": {k: v for k, v in values.items() if k in tag_names},
    }

    # Generate regional_tag_values for ESR constraints
    # A generator in region R can satisfy ESR constraint E if:
    #   - Any state in R can export to the policy states of E
    #   - This uses asymmetric rectable: rectable.loc[policy_state, generator_state] > 0
    #   - Generator state and policy state must be in the same interconnect
    if (
        state.esr_map
        and state.esr_type_map
        and state.esr_policy_states
        and state.region_aggregations
    ):
        regional_tag_values = {}

        # Build BA-level interconnect maps once for accurate cross-interconnect guard.
        # Using BA-level (not state-level majority) correctly handles split states
        # (e.g. SD and MT have BAs in both Western and Eastern interconnects).
        ba_to_interconnect: dict = {}
        state_to_interconnects_global: dict = {}
        if (
            state.hierarchy_df is not None
            and "interconnect" in state.hierarchy_df.columns
        ):
            for _, row in state.hierarchy_df.iterrows():
                ba = row.get("ba")
                st_val = str(row.get("st", "")).lower()
                ic = str(row.get("interconnect", "")).strip()
                if ba and ic:
                    ba_to_interconnect[ba] = ic
                if st_val and ic:
                    state_to_interconnects_global.setdefault(st_val, set()).add(ic)

        for esr_name in state.esr_map.keys():
            esr_type = state.esr_type_map.get(esr_name)
            if not esr_type:
                continue

            # Get qualified technologies based on ESR type
            if esr_type == "RPS":
                qualified_techs = getattr(state, "esr_rps_techs", set()) or set()
            else:  # CES
                qualified_techs = getattr(state, "esr_ces_techs", set()) or set()

            # Get the policy states for this ESR constraint
            policy_states = state.esr_policy_states.get(esr_name, set())
            if not policy_states:
                continue

            # Build tech map for this ESR
            tech_map = {tech: 1 for tech in sorted(qualified_techs)}

            # Check each region to see if its generators can satisfy this ESR's policies
            for region_name, region_bas in state.region_aggregations.items():
                # Get BA→state mapping for this region, then derive region_states from it.
                # Keeping the mapping lets us look up each BA's state for the IC guard below.
                ba_to_state_region = extract_state_for_region(
                    region_bas, state.hierarchy_df
                )
                region_states = set(ba_to_state_region.values())

                # Check if any state in this region can export to any policy state
                # Uses asymmetric check: rectable.loc[policy_state, generator_state]
                # Also requires generator and policy states share the same interconnect.
                can_satisfy = False
                if state.rectable_df is not None:
                    for gen_state in region_states:
                        # Get interconnects of the region's BAs that belong to gen_state.
                        # BA-level comparison correctly handles split states where a state
                        # spans multiple interconnects (minority-interconnect BAs are not
                        # misclassified by a majority-based state→interconnect mapping).
                        gen_ics = {
                            ba_to_interconnect[ba]
                            for ba in region_bas
                            if ba_to_state_region.get(ba) == gen_state
                            and ba in ba_to_interconnect
                        }
                        for policy_state in policy_states:
                            # Reject cross-interconnect trading: skip if the gen BA
                            # interconnects and policy state interconnects don't overlap.
                            pol_ics = state_to_interconnects_global.get(
                                policy_state, set()
                            )
                            if (
                                gen_ics
                                and pol_ics
                                and not gen_ics.intersection(pol_ics)
                            ):
                                continue
                            if can_generator_satisfy_policy(
                                gen_state, policy_state, state.rectable_df
                            ):
                                can_satisfy = True
                                break
                        if can_satisfy:
                            break
                else:
                    # If no rectable, assume same-zone generators can satisfy (fallback)
                    can_satisfy = bool(region_states & policy_states)

                if can_satisfy:
                    if region_name not in regional_tag_values:
                        regional_tag_values[region_name] = {}
                    regional_tag_values[region_name][esr_name] = tech_map

        if regional_tag_values:
            # Sort regions for consistent output
            out["regional_tag_values"] = {
                region: regional_tag_values[region]
                for region in sorted(regional_tag_values.keys())
            }

    return yaml.dump(out, default_flow_style=False, sort_keys=False)


def generate_emission_policies_settings():
    """Generate emission_policies.csv as a string."""
    if state.emission_policies_df is None:
        return None
    return state.emission_policies_df.to_csv(index=False)


DEMAND_SEGMENTS_FILENAME = "demand_segments_voll.csv"


def generate_demand_segments_csv():
    """Generate the demand segments / VOLL CSV as a string.

    Columns match the PowerGenome Demand_data.csv structure: ``Voll`` (first
    row only), ``Demand_Segment``, ``Cost_of_Demand_Curtailment_per_MW``
    (fraction of VOLL), ``Max_Demand_Curtailment`` (fraction of demand), and
    ``$/MWh`` (VOLL * cost fraction).
    """
    voll_el = document.getElementById("vollValue")
    try:
        voll = float(str(getattr(voll_el, "value", "") or "").strip())
    except ValueError:
        voll = 5000.0

    container = document.getElementById("demandSegmentRows")
    segments = []
    if container:
        for row in container.querySelectorAll(".demand-segment-row"):
            cost_el = row.querySelector(".demand-segment-cost")
            max_el = row.querySelector(".demand-segment-max-curtailment")
            cost_text = str(getattr(cost_el, "value", "") or "").strip()
            max_text = str(getattr(max_el, "value", "") or "").strip()
            if not cost_text and not max_text:
                continue
            try:
                cost_fraction = float(cost_text)
                max_curtailment = float(max_text)
            except ValueError:
                raise Exception(
                    "Demand segment rows must have numeric cost and max curtailment values."
                )
            segments.append((cost_fraction, max_curtailment))

    if not segments:
        return None

    def _fmt(value):
        return f"{value:g}"

    lines = [
        "Voll,Demand_Segment,Cost_of_Demand_Curtailment_per_MW,Max_Demand_Curtailment,$/MWh"
    ]
    for index, (cost_fraction, max_curtailment) in enumerate(segments, start=1):
        voll_value = _fmt(voll) if index == 1 else ""
        lines.append(
            ",".join(
                [
                    voll_value,
                    str(index),
                    _fmt(cost_fraction),
                    _fmt(max_curtailment),
                    _fmt(voll * cost_fraction),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def generate_model_definition_settings():
    region_aggs = _get_region_aggregations_or_raise()
    model_regions = sorted(region_aggs.keys())

    target_usd_year = int(
        _get_select_value(document.getElementById("targetUsdYear"), 2024)
    )
    utc_offset = int(_get_select_value(document.getElementById("utcOffset"), -5))
    model_period_end_years = parse_int_list(
        _get_select_value(document.getElementById("modelYears"), "")
    )
    model_period_start_years = parse_int_list(
        _get_select_value(document.getElementById("planningYears"), "")
    )

    if (
        not model_period_end_years
        or not model_period_start_years
        or len(model_period_end_years) != len(model_period_start_years)
    ):
        raise Exception("Model years and first planning years must be the same length.")

    model_periods = [
        [period_start_year, period_end_year]
        for period_start_year, period_end_year in zip(
            model_period_start_years, model_period_end_years
        )
    ]

    out = {
        "model_regions": model_regions,
        "region_aggregations": region_aggs,
        "target_usd_year": target_usd_year,
        "model_periods": model_periods,
        "utc_offset": utc_offset,
        "generator_columns": DEFAULT_GENERATOR_COLUMNS,
    }
    return yaml.dump(out, default_flow_style=False, sort_keys=False)


def generate_data_settings():
    """Generate the data file path and table-name template for PowerGenome."""
    out = {
        "input_folder": "extra_inputs",
        "demand_segments_fn": "demand_segments_voll.csv",
        "emission_policies_fn": "emission_policies.csv",
        "RESOURCE_GROUPS": [
            "path/to/resource/groups/folder",  # existing resource groups (Zenodo)
            # new-build wind/solar resource groups from this web app (Step 7),
            # stored relative to data.yml in the project folder
            "resource_groups",
        ],
        "RESOURCE_GROUP_PROFILES": "path/to/resource/profiles/folder",
        "data_location": ["path/to/your/primary/data/folder"],
        "generation_table": "reeds_generators_transformed.csv",
        "plant_region_table": "plant_region_map.csv",
        "resource_heat_rate_table": "technology_heat_rates_nrelatb.csv",
        "resource_cost_table": "technology_costs_atb.parquet",
        "operational_constraints_table": "operational_constraints_reeds.csv",
        "transmission_constraints_table": "transmission_capacity_reeds.csv",
        "fuel_price_table": "fuel_prices.parquet",
        "dollar_year_table": "dollar_year_adjustment.csv",
        "transmission_cost_table": (
            _build_network_costs_filename()
            if state.network_costs_df is not None
            else "network_costs.csv"
        ),
        "demand_table": "reeds_load_transformed.parquet",
        "regional_cost_factor_table": "regional_cost_multipliers.csv",
        "distributed_capacity_table": "distributed_capacity.parquet",
        "distributed_profile_table": "distributed_profiles.parquet",
    }
    return (
        yaml.dump(out, default_flow_style=False, sort_keys=False)
        + "\n# weather_year: 2012\n"
    )


def build_settings_yamls():
    result = {
        "data.yml": generate_data_settings(),
        "model_definition.yml": generate_model_definition_settings(),
        "resources.yml": generate_resources_settings(),
        "fuels.yml": generate_fuels_settings(),
        "transmission.yml": generate_transmission_settings(),
        "distributed_gen.yml": generate_distributed_gen_settings(),
        "resource_tags.yml": generate_resource_tags_settings(),
        "startup_costs.yml": generate_startup_costs_settings(),
    }

    return result


def populate_settings_file_select():
    sel = document.getElementById("settingsFileSelect")
    if not sel:
        return
    files = (
        sorted(state.settings_yamls.keys())
        if state.settings_yamls
        else SETTINGS_FILENAMES
    )
    _set_select_options(sel, files, selected_value=files[0] if files else None)


def update_settings_preview():
    sel = document.getElementById("settingsFileSelect")
    out_el = document.getElementById("settingsYamlOut")
    if not out_el:
        return
    filename = _get_select_value(sel, None)
    if filename and filename in state.settings_yamls:
        out_el.value = state.settings_yamls[filename]
    else:
        out_el.value = ""


def on_generate_settings(event):
    try:
        state.settings_yamls = build_settings_yamls()
        populate_settings_file_select()
        update_settings_preview()
        set_status(
            "Settings YAMLs generated. Ready to download individually or as ZIP.",
            "success",
        )
    except Exception as exc:
        state.settings_yamls = {}
        set_status(f"Settings generation error: {exc}", "error")


def _download_text_file(filename, content):
    mime = "text/csv" if filename.endswith(".csv") else "text/yaml"
    blob = window.Blob.new([content], to_js({"type": mime}))
    url = window.URL.createObjectURL(blob)
    a = document.createElement("a")
    a.href = url
    a.download = filename
    a.click()
    window.URL.revokeObjectURL(url)


def on_download_settings_file(event):
    sel = document.getElementById("settingsFileSelect")
    filename = _get_select_value(sel, None)
    if not filename or filename not in state.settings_yamls:
        set_status("Generate settings first.", "error")
        return
    _download_text_file(filename, state.settings_yamls[filename])
    set_status(f"Downloaded {filename}", "success")


def on_settings_file_change(event):
    update_settings_preview()


def set_resource_group_status(message, status_type="info"):
    el = document.getElementById("resourceGroupStatus")
    if el:
        el.textContent = message
        el.className = f"status {status_type}"
        el.style.display = "block"


async def _fetch_parquet_df(url):
    response = await fetch(url)
    if not response.ok:
        raise Exception(f"Failed to load parquet: {url} ({response.status})")
    buffer = await response.arrayBuffer()
    data = bytes(Uint8Array.new(buffer).to_py())

    # First, try with any available parquet engine (pyarrow, fastparquet, duckdb)
    try:
        return pd.read_parquet(BytesIO(data))
    except Exception as e:
        initial_error = str(e)

    # If that fails, try loading parquet engines from Pyodide
    parquet_ready = False
    for pkg in ["pyarrow", "fastparquet"]:
        try:
            __import__(pkg)
            parquet_ready = True
            break
        except Exception:
            pass

    if not parquet_ready:
        # Try loading from Pyodide
        for pkg in ["pyarrow", "fastparquet"]:
            try:
                loaded = await _load_pyodide_package(pkg)
                if loaded:
                    __import__(pkg)
                    parquet_ready = True
                    break
            except Exception:
                pass

    if parquet_ready:
        try:
            return pd.read_parquet(BytesIO(data))
        except Exception:
            pass

    raise ImportError(
        f"pyarrow or fastparquet is required to read .parquet files. "
        f"Initial error: {initial_error}"
    )


async def _fetch_csv_df(url, **kwargs):
    response = await fetch(url)
    if not response.ok:
        raise Exception(f"Failed to load CSV: {url} ({response.status})")
    text = await response.text()
    if text.startswith("<!"):
        raise Exception(f"Got HTML instead of CSV from {url}")
    return pd.read_csv(StringIO(text), **kwargs)


async def _ensure_network_data_cache():
    """Fetch and cache network data files (nodes, edges, topology) on first call.

    Subsequent calls return immediately without re-fetching.  The cache lives in
    ``state.network_data_cache`` as a ``(nodes_df, edges_df, topo_df)`` tuple.
    """
    if state.network_data_cache is not None:
        return
    nodes_df = await _fetch_csv_df(
        "./data/network_data/nodes.csv", dtype={"msa_id": str}
    )
    edges_df = await _fetch_parquet_df("./data/network_data/edges.parquet")
    topo_df = await _fetch_csv_df("./data/network_data/topology_base.csv")
    state.network_data_cache = (nodes_df, edges_df, topo_df)


async def _run_network_cost_calculation():
    """Compute inter-regional network upgrade costs using the current region aggregation.

    Stores the result in ``state.network_costs_df``.  On failure logs a warning
    via ``set_status`` but does not override a previous clustering success message.
    """
    if not state.region_aggregations:
        return
    try:
        await _ensure_network_data_cache()
        nodes_df, edges_df, topo_df = state.network_data_cache
        settings = {
            "model_regions": list(state.region_aggregations.keys()),
            "region_aggregations": state.region_aggregations,
        }
        state.network_costs_df = calculate_network_from_frames(
            nodes_df, edges_df, topo_df, settings=settings
        )
    except Exception as exc:
        state.network_costs_df = None
        set_status(f"Warning: network cost calculation failed: {exc}", "warning")


def _build_name_stem() -> str:
    """Return the descriptive stem shared by derived file/region names.

    The stem encodes:
    - the number of model regions (e.g. ``_10r``)
    - the interconnects represented by the selected BAs, sorted and joined with
      hyphens (e.g. ``_eastern-western``); each name is sanitized to
      alphanumeric, underscore, and hyphen characters so the result is always a
      safe filesystem name
    - the grouping column used for clustering (e.g. ``_nercr``)

    Falls back to descriptive placeholder values when the relevant state
    attributes are unavailable.  Example: ``_10r_eastern-western_nercr``.
    """

    def _safe(s: str) -> str:
        """Sanitize a string segment for use in a filename."""
        return re.sub(r"[^A-Za-z0-9_-]", "_", s)

    # --- number of regions ---
    regions_part = ""
    if state.region_aggregations is not None:
        n_regions = len(state.region_aggregations)
        regions_part = f"_{n_regions}r"

    # --- interconnections present in the selected BAs ---
    interconnects_part = "unspecified"
    if state.hierarchy_df is not None and state.selected_bas:
        mask = state.hierarchy_df["ba"].isin(state.selected_bas)
        unique_ix = sorted(
            state.hierarchy_df.loc[mask, "interconnect"].dropna().unique()
        )
        if unique_ix:
            interconnects_part = "-".join(_safe(ix) for ix in unique_ix)

    # --- grouping column ---
    grouping_part = (
        _safe(state.current_grouping) if state.current_grouping else "default"
    )

    return f"{regions_part}_{interconnects_part}_{grouping_part}"


def _build_network_costs_filename() -> str:
    """Return a descriptive filename for the network costs CSV export.

    The name encodes the number of model regions, the interconnects
    represented by the selected BAs, and the grouping column used for
    clustering (see ``_build_name_stem()``).
    Example: ``network_costs_10r_eastern-western_nercr.csv``.
    """
    return f"network_costs{_build_name_stem()}.csv"


def build_resource_group_name_default() -> str:
    """Return the default Step 7 region name for resource-group file names.

    Uses the same derived stem as the network costs filename, but with
    ``resource_groups`` instead of ``network_costs``.
    Example: ``resource_groups_10r_eastern-western_nercr``.
    """
    return f"resource_groups{_build_name_stem()}"


def update_resource_group_name_default():
    """Refresh the Step 7 "Region Name" input to the derived default.

    Only overwrites the field when it is empty or still holds the previous
    auto-generated default, so custom names typed by the user are preserved.
    """
    el = document.getElementById("resourceGroupName")
    if el is None:
        return
    new_default = build_resource_group_name_default()
    current = el.value if el.value else ""
    if current in ("", state.resource_group_name_default, "resource_groups"):
        el.value = new_default
    state.resource_group_name_default = new_default


async def _load_pyodide_package(name: str) -> bool:
    try:
        import pyodide_js

        await pyodide_js.loadPackage(name)
        return True
    except ImportError:
        pass

    try:
        pyodide_js = getattr(globalThis, "pyodide", None)
        if pyodide_js and hasattr(pyodide_js, "loadPackage"):
            await pyodide_js.loadPackage(name)
            return True
    except Exception:
        pass

    try:
        pyscript_rt = getattr(globalThis, "pyscript", None)
        if pyscript_rt is not None:
            runtime = getattr(pyscript_rt, "runtime", None)
            if runtime is not None:
                pyodide_rt = getattr(runtime, "pyodide", None)
                if pyodide_rt and hasattr(pyodide_rt, "loadPackage"):
                    await pyodide_rt.loadPackage(name)
                    return True
    except Exception:
        pass

    return False


async def load_fast_interconnection_data():
    if state.fast_interconnection_data is not None:
        return

    set_resource_group_status("Loading resource group data...", "info")
    parquet_ready = False
    load_errors = []
    for pkg in ["pyarrow", "fastparquet", "duckdb"]:
        try:
            __import__(pkg)
            parquet_ready = True
            break
        except Exception as exc:
            load_errors.append(f"{pkg} import: {exc}")

    if not parquet_ready:
        for pkg in ["pyarrow", "fastparquet", "duckdb"]:
            try:
                loaded = await _load_pyodide_package(pkg)
                if loaded:
                    __import__(pkg)
                    parquet_ready = True
                    break
                load_errors.append(f"{pkg} load: pyodide loader unavailable")
            except Exception as exc:
                load_errors.append(f"{pkg} load: {exc}")

    if not parquet_ready:
        details = "; ".join(load_errors) if load_errors else "unknown error"
        set_resource_group_status(
            f"Resource group error: parquet engine not available ({details}).",
            "error",
        )
        raise Exception(f"Parquet engine not available ({details})")
    base = "./fast_interconnection/data"
    data = {}

    # Define all files to load with their keys
    files_to_load = [
        ("candidates", f"{base}/cpa_metro_candidates.parquet", _fetch_parquet_df),
        ("saturation", f"{base}/metro_saturation.parquet", _fetch_parquet_df),
        ("metro_region_map", f"{base}/metro_region_map.parquet", _fetch_parquet_df),
        (
            "substation_metro_region",
            f"{base}/substation_metro_region.parquet",
            _fetch_parquet_df,
        ),
        ("cpa_solar_attrs", f"{base}/CPA_Solar_OctUpdate.parquet", _fetch_parquet_df),
        (
            "cpa_onshorewind_attrs",
            f"{base}/CPA_OnshoreWind_OctUpdate.parquet",
            _fetch_parquet_df,
        ),
        ("msa_name_map", f"{base}/msa_id_name_map.csv", _fetch_csv_df),
        ("cross_region", f"{base}/cross_region_connections.parquet", _fetch_parquet_df),
    ]

    total_files = len(files_to_load)

    # Load each file with progress indicator
    for idx, (key, filepath, fetch_func) in enumerate(files_to_load, 1):
        set_resource_group_status(
            f"Loading resource group data... {idx}/{total_files} files", "info"
        )
        try:
            data[key] = await fetch_func(filepath)
        except Exception:
            if key == "cross_region":
                # cross_region file is optional
                data[key] = None
            else:
                raise

    state.fast_interconnection_data = data


def _get_resource_group_name():
    el = document.getElementById("resourceGroupName")
    name = el.value.strip() if el and el.value else ""
    return name or build_resource_group_name_default()


def _update_resource_group_list():
    list_el = document.getElementById("resourceGroupFiles")
    if not list_el:
        return
    if not state.resource_group_files:
        list_el.innerHTML = "<em>No resource group files generated yet.</em>"
        return

    items = []
    for filename, payload in sorted(state.resource_group_files.items()):
        size = (
            len(payload)
            if isinstance(payload, (bytes, bytearray))
            else len(str(payload))
        )
        items.append(
            f"<div class='candidate-item'><strong>{html.escape(filename)}</strong> ({size:,} bytes)</div>"
        )
    list_el.innerHTML = "".join(items)


async def _generate_resource_groups():
    try:
        if not state.region_aggregations:
            set_resource_group_status("Run region clustering first (Step 1).", "error")
            return

        await load_fast_interconnection_data()

        penalty_el = document.getElementById("resourceGroupPenalty")
        try:
            penalty = float(penalty_el.value) if penalty_el else 10.0
        except ValueError:
            penalty = 10.0

        set_resource_group_status("Running fast interconnection assignment...", "info")

        def progress_callback(current, total, tech=None):
            if total > 0:
                percent = int(current / total * 100)
                tech_label = f" ({tech})" if tech else ""
                set_resource_group_status(
                    f"Assigning resources{tech_label}... {percent}%", "info"
                )

        settings = {
            "region_aggregations": state.region_aggregations,
            "model_regions": sorted(state.region_aggregations.keys()),
            "region_name": _get_resource_group_name(),
            "resource_group_profile_paths": DEFAULT_PROFILE_PATHS,
        }

        data = state.fast_interconnection_data
        assignments = await fast_assign_cpas(
            candidates=data["candidates"],
            saturation=data["saturation"],
            settings=settings,
            metro_region_map=data["metro_region_map"],
            substation_metro_region=data["substation_metro_region"],
            allowed_cross_region=data.get("cross_region"),
            strategy="dynamic_lcoe",
            lcoe_penalty_factor=penalty,
            show_progress=True,
            progress_callback=progress_callback,
        )

        if "metro_model_region" in assignments.columns:
            assignments = assignments.rename(
                columns={"metro_model_region": "model_region"}
            )

        # Filter assignments to only include valid model regions
        if "model_region" in assignments.columns:
            assignments = assignments[
                assignments["model_region"].isin(settings["model_regions"])
            ]

        state.resource_group_assignments = assignments.copy()

        msa_name_map = None
        if data.get("msa_name_map") is not None:
            msa_name_map = data["msa_name_map"].set_index("CBSAFP")["NAME"]

        remove_columns = {
            "cpa_model_region",
            "is_in_region",
            "is_allowed_cross",
            "base_lcoe",
            "effective_lcoe",
            "hub_base_region",
            "hub_substation",
            "offshore_interconnect_km",
            "CPA_ID",
        }

        output_files = {}
        for resource, cpa_attrs in [
            ("solar", data["cpa_solar_attrs"]),
            ("onshorewind", data["cpa_onshorewind_attrs"]),
        ]:
            assigned_df = build_assigned_df(
                assignments, resource, data["metro_region_map"], msa_name_map
            )
            if assigned_df.empty:
                continue

            cpa_results = assigned_df.merge(
                cpa_attrs, left_on="cpa_id", right_on="CPA_ID", how="left"
            )

            for col in ["anyQual", "m_popden", "exFacil", "plFacil"]:
                if col not in cpa_results.columns:
                    cpa_results[col] = np.nan

            drop_cols = [c for c in remove_columns if c in cpa_results.columns]
            if drop_cols:
                cpa_results = cpa_results.drop(columns=drop_cols)

            if (
                "capacity_mw" not in cpa_results.columns
                and "cpa_mw" in cpa_results.columns
            ):
                cpa_results["capacity_mw"] = cpa_results["cpa_mw"]

            lcoe_filename = f"{resource}_lcoe_{settings['region_name']}.parquet"
            buffer = BytesIO()
            cpa_results.to_parquet(buffer, index=False)
            output_files[lcoe_filename] = buffer.getvalue()

            rg_dict = build_resource_group_json(
                resource, lcoe_filename, settings.get("resource_group_profile_paths")
            )
            if rg_dict is not None:
                output_files[f"{resource}_group.json"] = json.dumps(rg_dict, indent=4)

        state.resource_group_files = output_files
        _update_resource_group_list()
        asyncio.create_task(_refresh_renewables_budget_defaults())

        if output_files:
            set_resource_group_status(
                f"Generated {len(output_files)} resource group files.", "success"
            )
        else:
            set_resource_group_status("No resource group files generated.", "error")
    except Exception as exc:
        state.resource_group_files = {}
        state.resource_group_assignments = None
        _update_resource_group_list()
        set_resource_group_status(f"Resource group error: {exc}", "error")


def on_generate_resource_groups(event):
    asyncio.create_task(_generate_resource_groups())


def _download_binary_file(filename, payload_bytes, mime_type):
    blob = window.Blob.new([Uint8Array.new(payload_bytes)], to_js({"type": mime_type}))
    url = window.URL.createObjectURL(blob)
    a = document.createElement("a")
    a.href = url
    a.download = filename
    a.click()
    window.URL.revokeObjectURL(url)


def on_download_resource_groups(event):
    if not state.resource_group_files:
        set_resource_group_status("Generate resource groups first.", "error")
        return

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for filename, payload in state.resource_group_files.items():
            if isinstance(payload, (bytes, bytearray)):
                zipf.writestr(filename, payload)
            else:
                zipf.writestr(filename, str(payload))

    zip_name = f"resource_groups_{_get_resource_group_name()}.zip"
    _download_binary_file(zip_name, buffer.getvalue(), "application/zip")
    set_resource_group_status(f"Downloaded {zip_name}", "success")


def _workflow_yaml_safe(value):
    """Convert state values to deterministic YAML-safe Python values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (np.integer, np.floating)):
        return _workflow_yaml_safe(value.item())
    if isinstance(value, np.ndarray):
        return [_workflow_yaml_safe(item) for item in value.tolist()]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _workflow_yaml_safe(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (set, tuple, list)):
        values = [_workflow_yaml_safe(item) for item in value]
        return sorted(values, key=str) if isinstance(value, set) else values
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Unsupported workflow state value: {type(value).__name__}")


def _workflow_dataframe_payload(df):
    if df is None:
        return None
    return {
        "columns": [str(column) for column in df.columns],
        "records": _workflow_yaml_safe(df.to_dict(orient="records")),
    }


def _workflow_dataframe_from_payload(payload, name):
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError(f"Workflow state table '{name}' must be a mapping.")
    columns = payload.get("columns")
    records = payload.get("records")
    if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
        raise ValueError(f"Workflow state table '{name}' has invalid columns.")
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise ValueError(f"Workflow state table '{name}' has invalid records.")
    return pd.DataFrame.from_records(records, columns=columns)


def _workflow_form_value(element):
    if element is None:
        return None
    element_type = str(getattr(element, "type", "")).lower()
    if element_type == "checkbox":
        return bool(element.checked)
    return str(getattr(element, "value", ""))


def _workflow_planning_periods():
    container = document.getElementById("planningPeriodRows")
    if not container:
        return []
    periods = []
    for row in container.querySelectorAll(".planning-period-row"):
        start = row.querySelector(".planning-period-start")
        planning_year = row.querySelector(".planning-period-model-year")
        periods.append(
            {
                "period_start": str(getattr(start, "value", "")),
                "planning_year": str(getattr(planning_year, "value", "")),
                "start_mode": str(getattr(row.dataset, "startMode", "manual")),
                "autofill_value": str(getattr(row.dataset, "autofillValue", "")),
            }
        )
    return periods


def _workflow_demand_segments():
    container = document.getElementById("demandSegmentRows")
    if not container:
        return []
    segments = []
    for row in container.querySelectorAll(".demand-segment-row"):
        cost = row.querySelector(".demand-segment-cost")
        max_curtailment = row.querySelector(".demand-segment-max-curtailment")
        segments.append(
            {
                "cost": str(getattr(cost, "value", "")),
                "max_curtailment": str(getattr(max_curtailment, "value", "")),
            }
        )
    return segments


def _workflow_checked_no_cluster_values():
    container = document.getElementById("noClusterContainer")
    if not container:
        return []
    values = []
    for checkbox in container.querySelectorAll('input[name="noCluster"]'):
        if checkbox.checked:
            values.append(str(checkbox.value))
    return sorted(values)


def _workflow_fuel_scenarios():
    container = document.getElementById("fuelScenariosContainer")
    if not container:
        return {}
    selected = {}
    for element in container.querySelectorAll("select"):
        element_id = str(getattr(element, "id", ""))
        if element_id.startswith("fuelScenario_"):
            selected[element_id[len("fuelScenario_") :]] = str(element.value)
    return selected


def build_workflow_state_manifest():
    """Build the required, versioned manifest used by workflow imports."""
    forms = {
        element_id: _workflow_form_value(document.getElementById(element_id))
        for element_id in _WORKFLOW_FORM_IDS
        if document.getElementById(element_id) is not None
    }
    forms["planning_periods"] = _workflow_planning_periods()
    forms["demand_segments"] = _workflow_demand_segments()
    forms["no_cluster_selected"] = _workflow_checked_no_cluster_values()
    forms["fuel_scenarios"] = _workflow_fuel_scenarios()

    plant_overrides = [
        {
            "model_region": key[0],
            "tech_group": key[1],
            "num_clusters": value,
        }
        for key, value in sorted(state.plant_candidate_overrides.items(), key=str)
        if isinstance(key, tuple) and len(key) == 2
    ]

    state_payload = {
        "selected_bas": sorted(state.selected_bas),
        "current_grouping": state.current_grouping,
        "is_clustered": state.is_clustered,
        "is_manual_mode": state.is_manual_mode,
        "ba_to_region": state.ba_to_region,
        "region_aggregations": state.region_aggregations,
        "manual_regions": state.manual_regions,
        "selected_manual_region": state.selected_manual_region,
        "cluster_colors": state.cluster_colors,
        "custom_tech_groups": state.custom_tech_groups,
        "available_techs": state.available_techs,
        "current_group": state.current_group,
        "omit_selected": state.omit_selected,
        "omit_available": state.omit_available,
        "new_resources": state.new_resources,
        "modified_new_resources": state.modified_new_resources,
        "plant_cluster_settings": state.plant_cluster_settings,
        "plant_candidate_overrides": plant_overrides,
        "ccs_disposal_cost": state.ccs_disposal_cost,
        "ccs_disposal_cost_map": state.ccs_disposal_cost_map,
        "esr_zones": state.esr_zones,
        "esr_map": state.esr_map,
        "esr_type_map": state.esr_type_map,
        "esr_policy_states": state.esr_policy_states,
        "esr_rps_techs": state.esr_rps_techs,
        "esr_ces_techs": state.esr_ces_techs,
        # Curves and their summaries are derived from the restored LCOE data,
        # demand data, and the user-controlled renewable inputs below.
        "renewables_capacity_overrides_mw": state.renewables_capacity_overrides_mw,
        "renewables_selected_region": state.renewables_selected_region,
        "renewables_selected_tech": state.renewables_selected_tech,
        "show_transmission_lines": state.show_transmission_lines,
        "box_select_mode": state.box_select_mode,
        "settings_yamls": state.settings_yamls,
    }

    has_resource_group_lcoe = {
        tech: any(
            str(filename).lower().startswith(prefix)
            and str(filename).lower().endswith(".parquet")
            for filename in state.resource_group_files
        )
        for tech, prefix in [
            ("onshorewind", "onshorewind_lcoe_"),
            ("solar", "solar_lcoe_"),
        ]
    }
    tables = {
        "emission_policies": _workflow_dataframe_payload(state.emission_policies_df),
        "network_costs": _workflow_dataframe_payload(state.network_costs_df),
    }
    if not any(has_resource_group_lcoe.values()):
        tables["resource_group_assignments"] = _workflow_dataframe_payload(
            state.resource_group_assignments
        )
    for tech, key in [
        ("onshorewind", "uploaded_lcoe_onshorewind"),
        ("solar", "uploaded_lcoe_solar"),
    ]:
        if not has_resource_group_lcoe[tech]:
            tables[key] = _workflow_dataframe_payload(getattr(state, key))
    supplemental_files = []
    for filename in sorted(state.resource_group_files):
        if "/" not in filename and "\\" not in filename and ".." not in filename:
            supplemental_files.append(f"resource_groups/{filename}")

    return {
        "schema": WORKFLOW_STATE_SCHEMA,
        "version": WORKFLOW_STATE_VERSION,
        "required_supplemental_files": supplemental_files,
        "forms": _workflow_yaml_safe(forms),
        "state": _workflow_yaml_safe(state_payload),
        "tables": _workflow_yaml_safe(tables),
    }


def _validate_workflow_manifest(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("workflow_state.yml must contain a YAML mapping.")
    if manifest.get("schema") != WORKFLOW_STATE_SCHEMA:
        raise ValueError(
            f"Unsupported workflow state schema: {manifest.get('schema')!r}."
        )
    if manifest.get("version") != WORKFLOW_STATE_VERSION:
        raise ValueError(
            f"Unsupported workflow state version: {manifest.get('version')!r}."
        )
    required = manifest.get("required_supplemental_files", [])
    if not isinstance(required, list) or not all(
        isinstance(path, str) and path and _is_safe_workflow_path(path)
        for path in required
    ):
        raise ValueError("workflow_state.yml has invalid supplemental file paths.")
    for section in ("forms", "state", "tables"):
        if not isinstance(manifest.get(section), dict):
            raise ValueError(f"workflow_state.yml is missing the '{section}' mapping.")
    return manifest


def _is_safe_workflow_path(path):
    return (
        path
        and not path.startswith("/")
        and not path.startswith("\\")
        and "\\" not in path
        and ".." not in path.split("/")
    )


def _parse_workflow_manifest(data):
    try:
        manifest = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not parse workflow_state.yml: {exc}") from exc
    return _validate_workflow_manifest(manifest)


def _read_workflow_zip(data):
    archive = BytesIO(data)
    if not zipfile.is_zipfile(archive):
        raise ValueError("The uploaded file is not a valid ZIP archive.")
    archive.seek(0)
    with zipfile.ZipFile(archive, "r") as zipf:
        names = zipf.namelist()
        for name in names:
            if not _is_safe_workflow_path(name):
                raise ValueError(f"ZIP contains an unsafe path: {name}")
        if WORKFLOW_STATE_FILENAME not in names:
            raise ValueError(
                f"The ZIP must contain {WORKFLOW_STATE_FILENAME} at its root."
            )
        manifest = _parse_workflow_manifest(zipf.read(WORKFLOW_STATE_FILENAME))
        required = set(manifest["required_supplemental_files"])
        missing = sorted(required - set(names))
        if missing:
            raise ValueError(
                "The ZIP is missing required workflow files: " + ", ".join(missing)
            )
        settings_yamls = {}
        for name in names:
            if name.startswith("settings/") and name.lower().endswith(
                (".yml", ".yaml")
            ):
                settings_yamls[name[len("settings/") :]] = zipf.read(name).decode(
                    "utf-8"
                )
        resource_group_files = {}
        for name in required:
            if name.startswith("resource_groups/"):
                resource_group_files[name[len("resource_groups/") :]] = zipf.read(name)
        return manifest, settings_yamls, resource_group_files


def _load_resource_group_lcoe_tables(resource_group_files):
    """Rebuild renewable source tables from ZIP-contained LCOE Parquet files."""
    tables = {}
    for filename, payload in (resource_group_files or {}).items():
        lower = str(filename).lower()
        if not lower.endswith(".parquet"):
            continue
        tech = next(
            (
                value
                for value, prefix in [
                    ("onshorewind", "onshorewind_lcoe_"),
                    ("solar", "solar_lcoe_"),
                ]
                if lower.startswith(prefix)
            ),
            None,
        )
        if tech is None:
            continue
        try:
            df = pd.read_parquet(BytesIO(payload))
        except Exception as exc:
            window.console.log(
                f"Renewables: could not read {filename} from workflow ZIP: {exc}"
            )
            continue
        region_column = "model_region" if "model_region" in df.columns else "region"
        capacity_column = "capacity_mw" if "capacity_mw" in df.columns else "cpa_mw"
        required = {region_column, capacity_column, "cf", "lcoe"}
        if not required <= set(df.columns):
            continue
        table = df[[region_column, capacity_column, "cf", "lcoe"]].copy()
        # _load_resource_group_lcoe_df expects model_region/cpa_mw column names.
        table.columns = ["model_region", "cpa_mw", "cf", "lcoe"]
        table.insert(0, "tech", tech)
        tables.setdefault(tech, []).append(table)
    return {
        tech: pd.concat(parts, ignore_index=True)
        for tech, parts in tables.items()
        if parts
    }


async def _load_resource_group_lcoe_tables_async(resource_group_files):
    """Read ZIP Parquet sources after loading a Pyodide parquet engine."""
    parquet_files = [
        (filename, payload)
        for filename, payload in (resource_group_files or {}).items()
        if str(filename).lower().endswith(".parquet")
    ]
    if not parquet_files:
        return {}

    parquet_ready = False
    for package in ["pyarrow", "fastparquet"]:
        try:
            __import__(package)
            parquet_ready = True
            break
        except ImportError:
            try:
                if await _load_pyodide_package(package):
                    __import__(package)
                    parquet_ready = True
                    break
            except Exception:
                continue

    if not parquet_ready:
        set_renewables_status(
            "Parquet LCOE files were found, but no parquet reader is available.",
            "error",
        )
        return {}

    parsed = {}
    for filename, payload in parquet_files:
        try:
            parsed[filename] = pd.read_parquet(BytesIO(payload))
        except Exception as exc:
            set_renewables_status(
                f"Could not read resource-group file {filename}: {exc}", "error"
            )
    normalized = {}
    for filename, df in parsed.items():
        lower = str(filename).lower()
        tech = next(
            (
                value
                for value, prefix in [
                    ("onshorewind", "onshorewind_lcoe_"),
                    ("solar", "solar_lcoe_"),
                ]
                if lower.startswith(prefix)
            ),
            None,
        )
        if tech is None:
            continue
        region_column = "model_region" if "model_region" in df.columns else "region"
        capacity_column = "capacity_mw" if "capacity_mw" in df.columns else "cpa_mw"
        required = {region_column, capacity_column, "cf", "lcoe"}
        if not required <= set(df.columns):
            continue
        table = df[[region_column, capacity_column, "cf", "lcoe"]].copy()
        # _load_resource_group_lcoe_df expects model_region/cpa_mw column names.
        table.columns = ["model_region", "cpa_mw", "cf", "lcoe"]
        table.insert(0, "tech", tech)
        normalized.setdefault(tech, []).append(table)
    return {
        tech: pd.concat(parts, ignore_index=True)
        for tech, parts in normalized.items()
        if parts
    }


async def _rebuild_imported_renewables(resource_group_files):
    """Load imported renewable sources, then rebuild derived curves."""
    if resource_group_files:
        restored_lcoe = await _load_resource_group_lcoe_tables_async(
            resource_group_files
        )
        if restored_lcoe:
            tables = [table for table in restored_lcoe.values() if not table.empty]
            state.resource_group_assignments = pd.concat(tables, ignore_index=True)
            state.uploaded_lcoe_onshorewind = None
            state.uploaded_lcoe_solar = None

    if state.resource_group_assignments is not None:
        await _compute_renewables_clusters()


def _set_workflow_form_value(element_id, value):
    element = document.getElementById(element_id)
    if element is None or value is None:
        return
    if str(getattr(element, "type", "")).lower() == "checkbox":
        element.checked = bool(value)
    else:
        element.value = str(value)


def _restore_plant_clustering_outputs():
    """Rebuild Step 3 (Existing Plants) clustering outputs after a workflow import.

    The manifest stores the resulting ``plant_cluster_settings`` and
    ``plant_candidate_overrides`` but not the intermediate plant groups or
    split candidates. Re-run the clustering with the restored form inputs to
    rebuild them, then re-apply the imported overrides.
    """
    if not state.plant_cluster_settings:
        return
    if state.plants_df is None or not state.ba_to_region:
        return

    saved_settings = state.plant_cluster_settings
    saved_overrides = dict(state.plant_candidate_overrides)
    try:
        on_run_plant_clustering(None)
    except Exception:
        state.plant_groups = []
        state.plant_candidates = []
    if not state.plant_groups:
        # Re-run failed; keep the imported settings so export still uses them.
        state.plant_cluster_settings = saved_settings
        state.plant_candidate_overrides = saved_overrides
        return

    if saved_overrides:
        # Only keep overrides that still match a rebuilt plant group.
        state.plant_candidate_overrides = {
            key: value
            for key, value in saved_overrides.items()
            if any(
                g["model_region"] == key[0] and g["tech_group"] == key[1]
                for g in state.plant_groups
            )
        }
        regenerate_plant_yaml_with_overrides()
        render_plant_candidates()


def _restore_workflow_state(manifest, settings_yamls=None, resource_group_files=None):
    """Apply a validated manifest after static app data and controls are ready."""
    forms = manifest["forms"]
    state_payload = manifest["state"]
    tables = manifest["tables"]

    for element_id, value in forms.items():
        if element_id in _WORKFLOW_FORM_IDS:
            _set_workflow_form_value(element_id, value)

    periods = forms.get("planning_periods") or []
    restore_periods = getattr(window, "restorePlanningPeriods", None)
    if callable(restore_periods) and periods:
        restore_periods(to_js(periods))
    else:
        _set_workflow_form_value("modelYears", forms.get("modelYears", ""))
        _set_workflow_form_value("planningYears", forms.get("planningYears", ""))

    restore_segments = getattr(window, "restoreDemandSegments", None)
    if callable(restore_segments) and forms.get("demand_segments"):
        restore_segments(to_js(forms["demand_segments"]), forms.get("vollValue"))

    # Let the existing UI switch visibility, then replace the reset state with
    # the imported values below.
    toggle_region_mode = getattr(window, "toggleRegionMode", None)
    if callable(toggle_region_mode):
        toggle_region_mode(bool(state_payload.get("is_manual_mode", False)))

    state.selected_bas = set(state_payload.get("selected_bas") or [])
    state.current_grouping = state_payload.get("current_grouping")
    state.is_manual_mode = bool(state_payload.get("is_manual_mode", False))
    state.is_clustered = bool(state_payload.get("is_clustered", False))
    state.ba_to_region = dict(state_payload.get("ba_to_region") or {})
    state.region_aggregations = state_payload.get("region_aggregations")
    state.manual_regions = {
        str(name): list(values or [])
        for name, values in (state_payload.get("manual_regions") or {}).items()
    }
    state.selected_manual_region = state_payload.get("selected_manual_region")
    state.cluster_colors = dict(state_payload.get("cluster_colors") or {})
    state.custom_tech_groups = {
        str(name): set(values or [])
        for name, values in (state_payload.get("custom_tech_groups") or {}).items()
    }
    state.available_techs = set(state_payload.get("available_techs") or [])
    state.current_group = state_payload.get("current_group")
    state.omit_selected = set(state_payload.get("omit_selected") or [])
    state.omit_available = set(state_payload.get("omit_available") or [])
    state.new_resources = list(state_payload.get("new_resources") or [])
    state.modified_new_resources = dict(
        state_payload.get("modified_new_resources") or {}
    )
    state.plant_cluster_settings = state_payload.get("plant_cluster_settings")
    state.plant_candidate_overrides = {
        (str(item["model_region"]), str(item["tech_group"])): int(item["num_clusters"])
        for item in state_payload.get("plant_candidate_overrides") or []
    }
    state.ccs_disposal_cost = state_payload.get(
        "ccs_disposal_cost", state.ccs_disposal_cost
    )
    state.ccs_disposal_cost_map = dict(state_payload.get("ccs_disposal_cost_map") or {})
    state.esr_zones = state_payload.get("esr_zones")
    state.esr_map = state_payload.get("esr_map")
    state.esr_type_map = state_payload.get("esr_type_map")
    policy_states = state_payload.get("esr_policy_states")
    state.esr_policy_states = (
        {str(name): set(values or []) for name, values in policy_states.items()}
        if isinstance(policy_states, dict)
        else policy_states
    )
    state.esr_rps_techs = set(state_payload.get("esr_rps_techs") or [])
    state.esr_ces_techs = set(state_payload.get("esr_ces_techs") or [])
    state.renewables_clusters = None
    state.renewables_clusters_info = None
    state.renewables_region_capacity_mw = {}
    state.renewables_region_base_capacity_mw = {}
    state.renewables_pending_region_capacity_mw = {}
    state.renewables_region_available_mw = {}
    state.renewables_curve_data = {}
    state.renewables_capacity_overrides_mw = dict(
        state_payload.get("renewables_capacity_overrides_mw") or {}
    )
    state.renewables_selected_region = state_payload.get("renewables_selected_region")
    state.renewables_selected_tech = state_payload.get(
        "renewables_selected_tech", "landbasedwind"
    )
    state.show_transmission_lines = bool(
        state_payload.get("show_transmission_lines", False)
    )
    state.box_select_mode = bool(state_payload.get("box_select_mode", True))
    state.settings_yamls = dict(state_payload.get("settings_yamls") or {})
    if settings_yamls:
        state.settings_yamls = settings_yamls
    state.resource_group_files = dict(resource_group_files or {})

    state.emission_policies_df = _workflow_dataframe_from_payload(
        tables.get("emission_policies"), "emission_policies"
    )
    state.network_costs_df = _workflow_dataframe_from_payload(
        tables.get("network_costs"), "network_costs"
    )
    state.resource_group_assignments = _workflow_dataframe_from_payload(
        tables.get("resource_group_assignments"), "resource_group_assignments"
    )
    state.uploaded_lcoe_onshorewind = _workflow_dataframe_from_payload(
        tables.get("uploaded_lcoe_onshorewind"), "uploaded_lcoe_onshorewind"
    )
    state.uploaded_lcoe_solar = _workflow_dataframe_from_payload(
        tables.get("uploaded_lcoe_solar"), "uploaded_lcoe_solar"
    )
    # Rebuild dynamic controls and previews without invoking reset handlers.
    # Force grouping colors to be rebuilt for the imported grouping column.
    state.current_grouping = None
    update_no_cluster_options()
    selected_no_cluster = set(forms.get("no_cluster_selected") or [])
    container = document.getElementById("noClusterContainer")
    if container:
        for checkbox in container.querySelectorAll('input[name="noCluster"]'):
            checkbox.checked = str(checkbox.value) in selected_no_cluster
    render_omit_editor()
    render_group_editor()
    populate_resource_year_selects()
    populate_fuel_scenario_selects()
    for fuel, scenario in (forms.get("fuel_scenarios") or {}).items():
        element = document.getElementById(f"fuelScenario_{fuel}")
        if element:
            element.value = str(scenario)
    render_fuel_price_charts()
    render_new_resources_list()
    render_modified_resources_list()
    populate_settings_file_select()
    update_settings_preview()
    _update_resource_group_list()

    if state.is_manual_mode:
        update_manual_regions_display()
        update_unassigned_display()
        update_manual_region_colors()
    elif state.region_aggregations:
        update_map_cluster_colors(state.region_aggregations)
    update_selected_display()
    update_tooltips()
    update_transmission_lines()
    if state.map is not None:
        if state.box_select_mode:
            on_box_mode(None)
        else:
            on_click_mode(None)
    _restore_plant_clustering_outputs()
    render_esr_results()
    _render_renewables_preview()
    _render_renewables_advanced_panel()
    set_status("Workflow defaults imported successfully.", "success")

    # Renewable curves are derived from the restored source tables. Recompute
    # them asynchronously so importing a ZIP does not serialize large arrays
    # into the manifest or block the rest of the wizard from being restored.
    if state.region_aggregations and (
        state.resource_group_assignments is not None or resource_group_files
    ):
        asyncio.create_task(_rebuild_imported_renewables(resource_group_files))


def _import_workflow_bytes(data, filename):
    lower = filename.lower()
    if lower.endswith(".zip"):
        manifest, settings_yamls, resource_group_files = _read_workflow_zip(data)
        _restore_workflow_state(manifest, settings_yamls, resource_group_files)
        return
    if lower not in {"workflow_state.yml", "workflow_state.yaml"}:
        raise ValueError("Choose a workflow_state.yml file or a complete settings ZIP.")
    manifest = _parse_workflow_manifest(data)
    required = manifest["required_supplemental_files"]
    if required:
        raise ValueError(
            "This workflow state requires supplemental files. Upload the complete settings ZIP."
        )
    _restore_workflow_state(manifest)


async def _read_uploaded_workflow_file(event):
    files = event.target.files
    if not files or files.length == 0:
        return
    file_obj = files.item(0)
    filename = file_obj.name
    progress = document.getElementById("workflowImportProgress")
    progress_message = document.getElementById("workflowImportProgressMessage")
    if progress:
        progress.classList.add("visible")
        progress.setAttribute("aria-hidden", "false")
    if progress_message:
        progress_message.textContent = f"Loading workflow state from {filename}..."
    set_status(f"Importing workflow defaults from {filename}...", "info")
    try:
        array_buffer = await file_obj.arrayBuffer()
        data = bytes(Uint8Array.new(array_buffer).to_py())
        _import_workflow_bytes(data, filename)
    except (
        ValueError,
        UnicodeDecodeError,
        zipfile.BadZipFile,
        pd.errors.ParserError,
    ) as exc:
        set_status(f"Workflow import error: {exc}", "error")
    except Exception as exc:
        set_status(f"Workflow import error: {exc}", "error")
    finally:
        if progress:
            progress.classList.remove("visible")
            progress.setAttribute("aria-hidden", "true")
        try:
            event.target.value = ""
        except Exception:
            pass


def on_upload_workflow_file(event):
    asyncio.create_task(_read_uploaded_workflow_file(event))


def on_download_all_settings(event):
    """
    Download all generated settings files and the required workflow state manifest
    as a single ZIP.
    """
    if not state.settings_yamls:
        set_status(
            "Generate settings YAMLs first (click 'Generate Settings') before downloading the ZIP.",
            "error",
        )
        return

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        manifest = build_workflow_state_manifest()
        zipf.writestr(
            WORKFLOW_STATE_FILENAME, yaml.safe_dump(manifest, sort_keys=False)
        )

        # Bundle the input-data download instructions so they travel with settings.
        zipf.writestr(DATA_SOURCES_FILENAME, render_data_sources_md())

        # Write settings files in deterministic order, rejecting unsafe names.
        # All YAML files go to settings/.
        for filename in sorted(state.settings_yamls):
            if "/" in filename or "\\" in filename or ".." in filename:
                continue  # skip path-traversal / absolute-path entries
            zip_path = f"settings/{filename}"
            zipf.writestr(zip_path, state.settings_yamls[filename])

        # Write emission_policies.csv under extra_inputs/ if it exists.
        if state.emission_policies_df is not None:
            csv_content = state.emission_policies_df.to_csv(index=False)
            zipf.writestr("extra_inputs/emission_policies.csv", csv_content)

        # Write the demand segments / VOLL CSV under extra_inputs/.
        demand_segments_csv = generate_demand_segments_csv()
        if demand_segments_csv is not None:
            zipf.writestr(
                f"extra_inputs/{DEMAND_SEGMENTS_FILENAME}", demand_segments_csv
            )

        # Write network_costs.csv under data/ if it has been computed.
        if state.network_costs_df is not None:
            network_costs_filename = _build_network_costs_filename()
            zipf.writestr(
                f"extra_inputs/{network_costs_filename}",
                state.network_costs_df.to_csv(index=False),
            )

        for filename, payload in sorted(state.resource_group_files.items()):
            if "/" in filename or "\\" in filename or ".." in filename:
                continue
            if isinstance(payload, (bytes, bytearray)):
                zipf.writestr(f"resource_groups/{filename}", payload)
            else:
                zipf.writestr(f"resource_groups/{filename}", str(payload))

    zip_name = "powergenome_settings.zip"
    _download_binary_file(zip_name, buffer.getvalue(), "application/zip")
    set_status(f"Downloaded {zip_name}", "success")

    # Inform the user if network costs were omitted (after confirming download success).
    if state.network_costs_df is None:
        set_status(
            "Network cost calculation has not completed or was not run; "
            "the generated network cost CSV is not included in the downloaded ZIP.",
            "warning",
        )


_LCOE_REQUIRED_COLUMNS = {"region", "cpa_mw", "cf", "lcoe"}
_LCOE_TECH_LABELS = {"onshorewind": "Wind", "solar": "Solar"}


async def _read_uploaded_lcoe_file(event, tech):
    """Read a user-uploaded parquet or CSV LCOE file and store it in state.

    Validates that the file contains the required columns (region, cpa_mw, cf, lcoe)
    before storing.  Supports both .parquet and .csv file formats.
    """
    files = event.target.files
    if not files or files.length == 0:
        return

    label = _LCOE_TECH_LABELS.get(tech, tech)
    file_obj = files.item(0)
    filename = file_obj.name

    set_resource_group_status(f"Reading {label} LCOE file: {filename}…", "info")

    try:
        array_buffer = await file_obj.arrayBuffer()
        data = bytes(Uint8Array.new(array_buffer).to_py())

        lower = filename.lower()
        if lower.endswith(".parquet"):
            # Try reading with any available engine first
            try:
                df = pd.read_parquet(BytesIO(data))
            except Exception as initial_error:
                # If that fails, try to load parquet engines from Pyodide
                parquet_ready = False
                for pkg in ["pyarrow", "fastparquet"]:
                    try:
                        __import__(pkg)
                        parquet_ready = True
                        break
                    except Exception:
                        pass

                if not parquet_ready:
                    # Try loading from Pyodide
                    for pkg in ["pyarrow", "fastparquet"]:
                        try:
                            loaded = await _load_pyodide_package(pkg)
                            if loaded:
                                __import__(pkg)
                                parquet_ready = True
                                break
                        except Exception:
                            pass

                if parquet_ready:
                    try:
                        df = pd.read_parquet(BytesIO(data))
                    except Exception:
                        raise ImportError(
                            f"Failed to read .parquet file after loading parquet engines. "
                            f"Initial error: {initial_error}"
                        )
                else:
                    raise ImportError(
                        f"pyarrow or fastparquet is required to read .parquet files, "
                        f"but neither could be loaded. Please convert your file to CSV format. "
                        f"Initial error: {initial_error}"
                    )
        elif lower.endswith(".csv"):
            df = pd.read_csv(BytesIO(data))
        else:
            set_resource_group_status(
                f"{label} LCOE file must be .parquet or .csv (got {filename}).",
                "error",
            )
            return

        missing = _LCOE_REQUIRED_COLUMNS - set(df.columns)
        if missing:
            set_resource_group_status(
                f"{label} LCOE file is missing required columns: "
                f"{sorted(missing)}.  Required: {sorted(_LCOE_REQUIRED_COLUMNS)}.",
                "error",
            )
            return

        if tech == "onshorewind":
            state.uploaded_lcoe_onshorewind = df
        else:
            state.uploaded_lcoe_solar = df

        set_resource_group_status(
            f"{label} LCOE file loaded: {filename} ({len(df):,} rows).", "success"
        )
        window.console.log(
            f"Uploaded LCOE [{tech}]: {filename}, rows={len(df):,}, "
            f"columns={list(df.columns)}"
        )
    except Exception as exc:
        set_resource_group_status(f"Error reading {label} LCOE file: {exc}", "error")


def on_upload_lcoe_wind(event):
    asyncio.create_task(_read_uploaded_lcoe_file(event, "onshorewind"))


def on_upload_lcoe_solar(event):
    asyncio.create_task(_read_uploaded_lcoe_file(event, "solar"))


def on_copy_plant_yaml(event):
    """Copy plant YAML to clipboard."""
    yaml_el = document.getElementById("plantYamlOut")
    if yaml_el and yaml_el.value:
        window.navigator.clipboard.writeText(yaml_el.value)
        set_status("Plant YAML copied to clipboard!", "success")


def on_download_plant_yaml(event):
    """Download plant YAML file."""
    yaml_el = document.getElementById("plantYamlOut")
    if not yaml_el or not yaml_el.value:
        set_status("No plant YAML to download. Run plant clustering first.", "error")
        return

    blob = window.Blob.new([yaml_el.value], to_js({"type": "text/yaml"}))
    url = window.URL.createObjectURL(blob)

    a = document.createElement("a")
    a.href = url
    a.download = "plant_clusters.yml"
    a.click()

    window.URL.revokeObjectURL(url)
    set_status("Plant YAML downloaded!", "success")


def on_download_emission_policies(event):
    """Download emission_policies.csv file."""
    if state.emission_policies_df is None:
        set_status("Run ESR analysis first to generate emission_policies.csv.", "error")
        return

    csv_content = state.emission_policies_df.to_csv(index=False)
    blob = window.Blob.new([csv_content], to_js({"type": "text/csv"}))
    url = window.URL.createObjectURL(blob)

    a = document.createElement("a")
    a.href = url
    a.download = "emission_policies.csv"
    a.click()

    window.URL.revokeObjectURL(url)
    set_status("emission_policies.csv downloaded!", "success")


def on_grouping_change(event):
    """Handle grouping column change."""
    update_no_cluster_options()


# ============================================================================
# ESR Generation Functions
# ============================================================================


def set_esr_status(message, status_type="info"):
    """Update the ESR result text box."""
    el = document.getElementById("esrResultText")
    if el:
        el.textContent = message
        el.className = f"status {status_type}"
        el.style.display = "block"


def render_esr_results():
    """Render the ESR analysis results."""
    rps_list = document.getElementById("esrRPSTechList")
    ces_list = document.getElementById("esrCESTechList")
    zones_list = document.getElementById("esrZonesList")
    csv_preview = document.getElementById("esrCsvPreview")

    # Render RPS techs
    if rps_list and hasattr(state, "esr_rps_techs"):
        if state.esr_rps_techs:
            rps_html = "".join(
                f"<span class='ba-tag'>{html.escape(tech)}</span>"
                for tech in sorted(state.esr_rps_techs)
            )
            rps_list.innerHTML = rps_html
        else:
            rps_list.innerHTML = "<em>No RPS-qualified technologies found.</em>"

    # Render CES techs
    if ces_list and hasattr(state, "esr_ces_techs"):
        if state.esr_ces_techs:
            ces_html = "".join(
                f"<span class='ba-tag'>{html.escape(tech)}</span>"
                for tech in sorted(state.esr_ces_techs)
            )
            ces_list.innerHTML = ces_html
        else:
            ces_list.innerHTML = "<em>No CES-qualified technologies found.</em>"

    # Render ESR constraints (showing which regions participate in each ESR zone)
    if zones_list and state.esr_map and state.esr_type_map:
        zones_html_parts = []
        for esr_name in sorted(
            state.esr_map.keys(), key=lambda x: int(x.split("_")[1])
        ):
            regions = state.esr_map.get(esr_name, [])
            esr_type = state.esr_type_map.get(esr_name, "Unknown")
            # Find which state zone this ESR corresponds to
            zone_idx = (
                int(esr_name.split("_")[1]) - 1
            ) // 2  # ESR_1,2 -> zone 0, ESR_3,4 -> zone 1, etc.
            zone_states = (
                sorted(state.esr_zones[zone_idx])
                if zone_idx < len(state.esr_zones)
                else []
            )
            zones_html_parts.append(
                f"<div class='candidate-item'>"
                f"<strong>{esr_name} ({esr_type}):</strong> "
                f"States: {', '.join(zone_states)}<br>"
                f"Regions: {', '.join(sorted(regions)) if regions else '<em>none</em>'}"
                f"</div>"
            )
        zones_list.innerHTML = "".join(zones_html_parts)
    elif zones_list and state.esr_zones:
        # Fallback: show state zones if esr_map not available
        zones_html = "".join(
            f"<div class='candidate-item'><strong>Trading Zone {i+1}:</strong> States: {', '.join(sorted(zone))}</div>"
            for i, zone in enumerate(state.esr_zones)
        )
        zones_list.innerHTML = zones_html
    elif zones_list:
        # Clear zones list when no ESR data is available to avoid stale UI
        zones_list.innerHTML = ""

    # Render CSV preview
    if csv_preview:
        if state.emission_policies_df is not None:
            csv_str = state.emission_policies_df.to_csv(index=False)
            csv_preview.value = csv_str
        else:
            # Clear CSV preview when data is None to avoid stale UI
            csv_preview.value = ""


def on_run_esr_analysis(event):
    """Handle Run ESR Analysis button click."""
    try:
        # Check that region clustering has been run
        if not state.region_aggregations:
            set_esr_status("Run region clustering first (Step 1).", "error")
            return

        # Check that all ESR data is loaded
        missing_data = []
        if state.rps_df is None:
            missing_data.append("RPS policies")
        if state.ces_df is None:
            missing_data.append("CES policies")
        if state.rectable_df is None:
            missing_data.append("trading rules")
        if state.pop_fraction_df is None:
            missing_data.append("population fractions")
        if state.allowed_techs_df is None:
            missing_data.append("allowed techs")

        if missing_data:
            set_esr_status(
                f"ESR data still loading ({', '.join(missing_data)}). Please wait a moment and try again.",
                "error",
            )
            return

        set_esr_status("Analyzing ESR zones...", "info")

        # Get model years
        model_years_input = document.getElementById("modelYears").value
        model_years = parse_int_list(model_years_input)
        if not model_years:
            set_esr_status("Set model years in Model Setup step (Step 2).", "error")
            return

        # Get toggle states
        include_rps = document.getElementById("esrIncludeRPS").checked
        include_ces = document.getElementById("esrIncludeCES").checked

        if not include_rps and not include_ces:
            set_esr_status("Enable at least one of RPS or CES constraints.", "error")
            return

        # Build ESR zones (state-based trading zones)
        state.esr_zones, state_to_zone = build_esr_zones(
            state.region_aggregations, state.hierarchy_df, state.rectable_df
        )

        # Get qualified technologies
        # Convert state.new_resources to the format expected by get_qualified_technologies
        new_resources = [
            [r["technology"], r["tech_detail"], r["cost_case"], r["size_mw"]]
            for r in state.new_resources
        ]

        state.esr_rps_techs, state.esr_ces_techs = get_qualified_technologies(
            state.plants_df, new_resources, state.allowed_techs_df
        )

        # Generate emission policies CSV using original region aggregations
        # (no region name splitting - regions can span multiple ESR zones)
        (
            state.emission_policies_df,
            state.esr_map,
            state.esr_type_map,
            state.esr_policy_states,
        ) = generate_emission_policies_csv(
            state.region_aggregations,
            model_years,
            state.esr_zones,
            state_to_zone,
            state.hierarchy_df,
            state.pop_fraction_df,
            state.rps_df,
            state.ces_df,
            include_rps=include_rps,
            include_ces=include_ces,
            case_id="all",
        )

        # Render results
        render_esr_results()

        set_esr_status(
            f"ESR analysis complete: {len(state.esr_zones)} zones, "
            f"{len(state.esr_rps_techs)} RPS techs, {len(state.esr_ces_techs)} CES techs",
            "success",
        )

    except ESRGenerationError as e:
        set_esr_status(f"ESR Error: {e}", "error")
    except Exception as e:
        set_esr_status(f"ESR Analysis Error: {e}", "error")


# ============================================================================
# Initialization
# ============================================================================


async def main():
    """Main initialization function."""
    try:
        # Load data
        await load_data()

        # Load ATB index for Settings tab (optional)
        await load_atb_options()

        # Load ATB sizes for Settings tab (optional)
        await load_atb_size()

        # Load fuel scenarios for Settings tab (optional)
        await load_fuel_prices()

        # Initialize map
        await init_map()

        # Set up UI - this also sets up group colors after map is ready
        update_no_cluster_options()

        # Force initial group colors (in case update_no_cluster_options skipped it)
        state.current_grouping = None  # Force re-calculation
        update_group_colors()

        # Attach event handlers
        document.getElementById("runBtn").addEventListener(
            "click", create_proxy(on_run_clustering)
        )
        document.getElementById("selectAllBtn").addEventListener(
            "click", create_proxy(on_select_all)
        )
        document.getElementById("clearSelectionBtn").addEventListener(
            "click", create_proxy(on_clear_selection)
        )

        # Manual region mode buttons
        document.getElementById("addManualRegionBtn").addEventListener(
            "click", create_proxy(on_add_manual_region)
        )
        document.getElementById("parseYamlBtn").addEventListener(
            "click", create_proxy(on_parse_yaml_regions)
        )
        document.getElementById("assignBAsBtn").addEventListener(
            "click", create_proxy(on_assign_bas)
        )
        document.getElementById("finalizeManualBtn").addEventListener(
            "click", create_proxy(on_finalize_manual)
        )
        document.getElementById("clearManualRegionsBtn").addEventListener(
            "click", create_proxy(on_clear_manual_regions)
        )
        document.getElementById("selectAllBtn2").addEventListener(
            "click", create_proxy(on_select_all)
        )
        document.getElementById("clearSelectionBtn2").addEventListener(
            "click", create_proxy(on_clear_selection)
        )

        document.getElementById("copyYamlBtn").addEventListener(
            "click", create_proxy(on_copy_yaml)
        )
        document.getElementById("runPlantBtn").addEventListener(
            "click", create_proxy(on_run_plant_clustering)
        )
        document.getElementById("copyPlantYamlBtn").addEventListener(
            "click", create_proxy(on_copy_plant_yaml)
        )
        document.getElementById("downloadPlantYamlBtn").addEventListener(
            "click", create_proxy(on_download_plant_yaml)
        )
        document.getElementById("groupTechDefault").addEventListener(
            "change", create_proxy(update_default_cluster_budget)
        )
        document.getElementById("groupingColumn").addEventListener(
            "change", create_proxy(on_grouping_change)
        )
        document.getElementById("addGroupBtn").addEventListener(
            "click", create_proxy(on_add_group)
        )
        document.getElementById("moveToGroupBtn").addEventListener(
            "click", create_proxy(on_add_tech_to_group)
        )
        document.getElementById("moveToAvailableBtn").addEventListener(
            "click", create_proxy(on_remove_tech_from_group)
        )
        document.getElementById("resetGroupsBtn").addEventListener(
            "click", create_proxy(on_reset_groups)
        )
        document.getElementById("clearGroupsBtn").addEventListener(
            "click", create_proxy(on_clear_groups)
        )
        document.getElementById("groupSelectDual").addEventListener(
            "change", create_proxy(on_group_change)
        )
        document.getElementById("omitMoveToSelectedBtn").addEventListener(
            "click", create_proxy(on_omit_move_to_selected)
        )
        document.getElementById("omitMoveToAvailableBtn").addEventListener(
            "click", create_proxy(on_omit_move_to_available)
        )
        document.getElementById("omitResetBtn").addEventListener(
            "click", create_proxy(on_reset_omit_defaults)
        )

        # Model Setup - planning years change
        document.getElementById("modelYears").addEventListener(
            "input", create_proxy(on_model_years_change)
        )

        # Settings tab
        document.getElementById("addNewResourceBtn").addEventListener(
            "click", create_proxy(on_add_new_resource)
        )
        document.getElementById("addModifiedResourceBtn").addEventListener(
            "click", create_proxy(on_add_modified_resource)
        )
        document.getElementById("clearModifiedResourcesBtn").addEventListener(
            "click", create_proxy(on_clear_modified_resources)
        )
        document.getElementById("modBaseTech").addEventListener(
            "change", create_proxy(on_mod_base_picker_change)
        )
        document.getElementById("modBaseTechDetail").addEventListener(
            "change", create_proxy(on_mod_base_picker_change)
        )
        document.getElementById("generateSettingsBtn").addEventListener(
            "click", create_proxy(on_generate_settings)
        )
        document.getElementById("settingsFileSelect").addEventListener(
            "change", create_proxy(on_settings_file_change)
        )
        document.getElementById("downloadSettingsFileBtn").addEventListener(
            "click", create_proxy(on_download_settings_file)
        )
        document.getElementById("downloadAllSettingsZipBtn").addEventListener(
            "click", create_proxy(on_download_all_settings)
        )
        document.getElementById("uploadWorkflowInput").addEventListener(
            "change", create_proxy(on_upload_workflow_file)
        )

        # Resource groups
        document.getElementById("generateResourceGroupsBtn").addEventListener(
            "click", create_proxy(on_generate_resource_groups)
        )
        document.getElementById("downloadResourceGroupsBtn").addEventListener(
            "click", create_proxy(on_download_resource_groups)
        )
        document.getElementById("uploadLcoeWindInput").addEventListener(
            "change", create_proxy(on_upload_lcoe_wind)
        )
        document.getElementById("uploadLcoeSolarInput").addEventListener(
            "change", create_proxy(on_upload_lcoe_solar)
        )

        # Renewables clustering
        document.getElementById("computeRenewablesClustersBtn").addEventListener(
            "click", create_proxy(on_compute_renewables_clusters)
        )
        document.getElementById("recalcRenewablesClustersBtn").addEventListener(
            "click", create_proxy(on_compute_renewables_clusters)
        )
        document.getElementById("renewablesRegionCapacitySlider").addEventListener(
            "input", create_proxy(on_renewables_capacity_slider_input)
        )
        document.getElementById("renewablesWindShare").addEventListener(
            "input", create_proxy(on_renewables_budget_inputs_change)
        )
        document.getElementById("renewablesSolarShare").addEventListener(
            "input", create_proxy(on_renewables_budget_inputs_change)
        )
        document.getElementById("renewablesWindAvgResourceMw").addEventListener(
            "input", create_proxy(on_renewables_budget_inputs_change)
        )
        document.getElementById("renewablesSolarAvgResourceMw").addEventListener(
            "input", create_proxy(on_renewables_budget_inputs_change)
        )

        # ESR step
        document.getElementById("runESRBtn").addEventListener(
            "click", create_proxy(on_run_esr_analysis)
        )

        # Fuel scenario options
        document.getElementById("fuelDataYear").addEventListener(
            "change", create_proxy(populate_fuel_scenario_selects)
        )
        # Fuel scenario select change listeners are attached dynamically by
        # populate_fuel_scenario_selects when it builds the rows.

        # ATB picker change events
        document.getElementById("atbYearSelect").addEventListener(
            "change", create_proxy(on_atb_picker_change)
        )
        document.getElementById("atbTechSelect").addEventListener(
            "change", create_proxy(on_atb_picker_change)
        )
        document.getElementById("atbTechDetailSelect").addEventListener(
            "change", create_proxy(on_atb_picker_change)
        )
        document.getElementById("atbCostCaseSelect").addEventListener(
            "change", create_proxy(on_atb_picker_change)
        )

        # Box selection mode buttons
        document.getElementById("clickModeBtn").addEventListener(
            "click", create_proxy(on_click_mode)
        )
        document.getElementById("boxModeBtn").addEventListener(
            "click", create_proxy(on_box_mode)
        )
        document.getElementById("showTransmissionLines").addEventListener(
            "change", create_proxy(on_toggle_transmission_lines)
        )

        # Map events for box selection
        state.map.on("mousedown", create_proxy(on_map_mousedown))
        state.map.on("mousemove", create_proxy(on_map_mousemove))
        state.map.on("mouseup", create_proxy(on_map_mouseup))

        # Start in box select mode (disable map dragging)
        state.map.dragging.disable()
        document.getElementById("map").classList.add("box-select-mode")

        # Initialize omit list and grouping editor with defaults
        render_omit_editor()
        reset_custom_groups(omit_tokens=get_selected_omit_tokens())

        # Seed the plant budget with a 15% buffer above the minimum clusters
        update_default_cluster_budget()
        asyncio.create_task(_refresh_renewables_budget_defaults())

        # Initialize Settings tab widgets
        populate_atb_picker()
        populate_mod_resource_pickers()
        populate_resource_year_selects()
        populate_fuel_data_year_select()
        populate_fuel_scenario_selects()
        render_new_resources_list()
        render_modified_resources_list()
        populate_settings_file_select()
        update_settings_preview()
        populate_data_sources_section()
        update_resource_group_name_default()
        _update_resource_group_list()
        _render_renewables_advanced_panel()

        # Set up deferred ESR data loading
        async def load_esr_data_deferred():
            try:
                await load_esr_data()
            except Exception as e:
                set_status(f"Failed to load ESR data: {e}", "error")

        window.loadESRDataOnDemand = create_proxy(
            lambda: asyncio.ensure_future(load_esr_data_deferred())
        )
        window.invalidateRenewablesMaps = create_proxy(invalidate_renewables_maps)

        # Done loading
        hide_loading()
        set_status(
            "Ready! Drag on the map to select BAs, or switch to Click mode for individual selection.",
            "info",
        )

    except Exception as e:
        set_status(f"Initialization error: {e}", "error")
        hide_loading()


# Run main
asyncio.ensure_future(main())
