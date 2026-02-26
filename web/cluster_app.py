"""
PowerGenome Region Clustering - PyScript Web App

This module runs in the browser via PyScript and handles:
1. Loading and displaying the BA map
2. Managing BA selection state
3. Running clustering algorithms (agglomerative and Louvain)
4. Generating YAML output
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
from fast_interconnection.fast_assign import fast_assign_cpas
from fast_interconnection.resource_groups import (
    DEFAULT_PROFILE_PATHS,
    build_assigned_df,
    build_resource_group_json,
)

# ============================================================================
# Global State
# ============================================================================


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
        self.modified_new_resources = {}  # key -> metadata + schema for resources.yml
        self.atb_options = []  # list[dict] loaded from web/data/atb_options.json
        self.atb_index = {}  # year -> tech -> detail -> sorted(list(cost_case))
        self.atb_years = []  # sorted list of years
        self.plant_cluster_settings = (
            None  # parsed YAML dict from plant clustering output
        )
        self.ccs_disposal_cost = 20  # Default CCS disposal cost in $/metric ton
        self.ccs_disposal_cost_map = {}  # tech_name -> disposal cost override

        # Resource groups (Fast interconnection)
        self.fast_interconnection_data = None  # cached parquet/csv dataframes
        self.resource_group_files = {}  # filename -> bytes or str
        self.resource_group_assignments = None  # cached assignments dataframe

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
        self.emission_policies_df = None  # Generated emission_policies.csv


state = AppState()


# ============================================================================
# ESR Generator Functions (Energy Share Requirements)
# ============================================================================


class ESRGenerationError(Exception):
    """Raised when ESR generation is not possible with the given region configuration."""

    pass


def extract_state_for_region(region_bas, hierarchy_df):
    """Extract states for each BA in a model region."""
    ba_to_state = {}
    for ba in region_bas:
        row = hierarchy_df[hierarchy_df["ba"] == ba]
        if row.empty:
            raise ESRGenerationError(f"BA '{ba}' not found in hierarchy data")
        state_val = str(row.iloc[0]["st"]).lower()
        ba_to_state[ba] = state_val
    return ba_to_state


def get_states_in_region(region_bas, hierarchy_df):
    """Get unique states in a model region."""
    ba_to_state = extract_state_for_region(region_bas, hierarchy_df)
    return set(ba_to_state.values())


def split_bas_by_trading_zones(bas, hierarchy_df, rectable_df):
    """Split a set of BAs into groups where all states in each group can trade transitively.

    Returns a list of sets, where each set contains BAs whose states form a connected
    trading component. BAs in different sets cannot be clustered together.
    """
    if rectable_df is None or len(bas) <= 1:
        return [set(bas)]

    # Build BA to state mapping
    ba_to_state = {}
    for ba in bas:
        row = hierarchy_df[hierarchy_df["ba"] == ba]
        if not row.empty:
            ba_to_state[ba] = str(row.iloc[0]["st"]).lower()

    # Get unique states
    states = set(ba_to_state.values())
    if len(states) <= 1:
        return [set(bas)]

    states_list = list(states)

    # Build a graph of direct trading relationships between states
    trading_graph = {s: set() for s in states_list}
    for i, s1 in enumerate(states_list):
        for s2 in states_list[i + 1 :]:
            if can_states_trade(s1, s2, rectable_df):
                trading_graph[s1].add(s2)
                trading_graph[s2].add(s1)

    # Find connected components (trading zones)
    visited = set()
    trading_zones = []

    def dfs(state, zone):
        visited.add(state)
        zone.add(state)
        for neighbor in trading_graph[state]:
            if neighbor not in visited:
                dfs(neighbor, zone)

    for state in states_list:
        if state not in visited:
            zone = set()
            dfs(state, zone)
            trading_zones.append(zone)

    # If all states are in one trading zone, no split needed
    if len(trading_zones) == 1:
        return [set(bas)]

    # Group BAs by their trading zone
    ba_groups = []
    for zone in trading_zones:
        group = {ba for ba, st in ba_to_state.items() if st in zone}
        if group:
            ba_groups.append(group)

    return ba_groups


def can_states_trade(state1, state2, rectable_df, transitive_only=False):
    """Check if two states can trade REC/ESR credits based on rectable.csv.

    Args:
        state1: First state code (case-insensitive)
        state2: Second state code (case-insensitive)
        rectable_df: DataFrame with trading rules (states as index and columns)
        transitive_only: If True, only return True for value=1 (transitive trading allowed).
                        If False, return True for any value > 0 (including value=2 direct-only).

    Trading values in rectable.csv:
        - 1: States can trade directly AND transitively (through intermediate states)
        - 2: States can trade directly ONLY (no transitive chains)
        - 0 or missing: States cannot trade
    """
    state1_upper = state1.upper()
    state2_upper = state2.upper()
    if state1_upper not in rectable_df.index or state2_upper not in rectable_df.columns:
        return False
    value = rectable_df.loc[state1_upper, state2_upper]
    if pd.isna(value):
        return False
    val = float(value)
    if transitive_only:
        # Only value=1 allows transitive trading
        return val == 1
    else:
        # Any positive value allows direct trading
        return val > 0


def can_generator_satisfy_policy(generator_state, policy_state, rectable_df):
    """Check if generators in generator_state can satisfy the ESR policy in policy_state.

    This checks the ASYMMETRIC trading relationship:
        rectable.loc[policy_state, generator_state] > 0

    The row represents the policy holder (whose constraint needs to be met).
    The column represents the generator location (whose generation can satisfy the policy).

    Args:
        generator_state: State where the generator is located (case-insensitive)
        policy_state: State whose ESR policy needs to be satisfied (case-insensitive)
        rectable_df: DataFrame with trading rules (policy states as index, generator states as columns)

    Returns:
        True if generators in generator_state can satisfy policy_state's ESR constraint
    """
    policy_upper = policy_state.upper()
    generator_upper = generator_state.upper()

    if (
        policy_upper not in rectable_df.index
        or generator_upper not in rectable_df.columns
    ):
        return False

    value = rectable_df.loc[policy_upper, generator_upper]
    if pd.isna(value):
        return False

    return float(value) > 0


def can_states_trade_transitively(states_set, rectable_df):
    """Check if all states in a set can trade with each other transitively.

    States can be in the same zone if they're all connected through trading partners.
    For example, if A trades with C and B trades with C, then A and B can be in the same zone.
    """
    if len(states_set) <= 1:
        return True

    states_list = list(states_set)

    # Build a graph of direct trading relationships
    trading_graph = {s: set() for s in states_list}
    for i, s1 in enumerate(states_list):
        for s2 in states_list[i + 1 :]:
            if can_states_trade(s1, s2, rectable_df):
                trading_graph[s1].add(s2)
                trading_graph[s2].add(s1)

    # Check if all states are in the same connected component
    visited = set()

    def dfs(state):
        visited.add(state)
        for neighbor in trading_graph[state]:
            if neighbor not in visited:
                dfs(neighbor)

    dfs(states_list[0])
    return len(visited) == len(states_list)


def build_state_trading_zones(all_states, rectable_df, state_to_interconnect=None):
    """Build trading zones based on state-level trading rules.

    Groups states into zones where all states in a zone can trade with each other
    (directly or transitively through other states in the zone).

    If state_to_interconnect is provided, trading is limited to within the same
    interconnect (Western, Eastern, ERCOT).

    Returns:
        list of sets, where each set contains states that form a trading zone
    """
    if rectable_df is None or len(all_states) <= 1:
        return [set(all_states)]

    states_list = list(all_states)

    # Build a graph of trading relationships between states
    # For states to be in the same zone, they must have BIDIRECTIONAL trading
    # (both can satisfy each other's policies). Only value=1 allows transitive chains.
    trading_graph = {s: set() for s in states_list}
    for i, s1 in enumerate(states_list):
        for s2 in states_list[i + 1 :]:
            # Check interconnect constraint first (if provided)
            if state_to_interconnect is not None:
                ic1 = state_to_interconnect.get(s1)
                ic2 = state_to_interconnect.get(s2)
                # If both states have known interconnects and they differ, skip
                if ic1 and ic2 and ic1 != ic2:
                    continue

            # Check BOTH directions for bidirectional trading (zone membership)
            # rectable is asymmetric: row=policy holder, column=generator location
            # For zone membership, require both states can satisfy each other's policies
            s1_can_satisfy_s2 = can_states_trade(
                s2, s1, rectable_df, transitive_only=True
            )  # s1 generators -> s2 policy
            s2_can_satisfy_s1 = can_states_trade(
                s1, s2, rectable_df, transitive_only=True
            )  # s2 generators -> s1 policy

            # Only add edge if trading is bidirectional with value=1
            if s1_can_satisfy_s2 and s2_can_satisfy_s1:
                trading_graph[s1].add(s2)
                trading_graph[s2].add(s1)

    # Find connected components (trading zones) using DFS
    visited = set()
    zones = []

    def dfs(state, zone):
        visited.add(state)
        zone.add(state)
        for neighbor in trading_graph[state]:
            if neighbor not in visited:
                dfs(neighbor, zone)

    for state in states_list:
        if state not in visited:
            zone = set()
            dfs(state, zone)
            zones.append(zone)

    return zones


def build_state_to_interconnect_map(hierarchy_df):
    """Build a mapping from state to interconnect.

    If a state spans multiple interconnects, uses the interconnect with the most BAs
    for that state. Returns lowercase state codes mapped to interconnect names.
    """
    if hierarchy_df is None or "interconnect" not in hierarchy_df.columns:
        return {}

    # Count BAs per (state, interconnect) pair
    state_ic_counts = {}
    for _, row in hierarchy_df.iterrows():
        st = str(row.get("st", "")).lower()
        ic = str(row.get("interconnect", "")).strip()
        if not st or not ic:
            continue
        key = (st, ic)
        state_ic_counts[key] = state_ic_counts.get(key, 0) + 1

    # For each state, pick the interconnect with most BAs
    state_to_ic = {}
    state_best_count = {}
    for (st, ic), count in state_ic_counts.items():
        if st not in state_to_ic or count > state_best_count.get(st, 0):
            state_to_ic[st] = ic
            state_best_count[st] = count

    return state_to_ic


def build_esr_zones(region_aggregations, hierarchy_df, rectable_df):
    """Build ESR trading zones based on state-level trading rules.

    Unlike ESR-compatible clustering which ensures regions only contain states
    that can trade, this function handles regions that may span multiple
    non-trading states by assigning each state to its appropriate zone.

    A single region can participate in multiple ESR zones if it contains
    states from different trading groups. The ESR constraint values are
    weighted by the demand fraction from each state.

    Trading is limited to within the same interconnect (Western, Eastern, ERCOT).

    Returns:
        (state_zones, state_to_zone):
            - state_zones: list of sets, each set contains states in a trading zone
            - state_to_zone: dict mapping state -> zone_index
    """
    # Collect all states present in any region
    all_states = set()
    for region_bas in region_aggregations.values():
        states = get_states_in_region(region_bas, hierarchy_df)
        all_states.update(states)

    # Build state to interconnect mapping
    state_to_interconnect = build_state_to_interconnect_map(hierarchy_df)

    # Build trading zones at the state level (limited by interconnect)
    state_zones = build_state_trading_zones(
        all_states, rectable_df, state_to_interconnect
    )

    # Create state -> zone index mapping
    state_to_zone = {}
    for zone_idx, zone_states in enumerate(state_zones):
        for state in zone_states:
            state_to_zone[state] = zone_idx

    return state_zones, state_to_zone


def get_qualified_technologies(plants_df, new_resources, allowed_techs_df):
    """Determine which technologies qualify for RPS and CES policies."""
    rps_keywords = allowed_techs_df["RPS"].dropna().str.lower().tolist()
    ces_keywords = allowed_techs_df["CES"].dropna().str.lower().tolist()

    all_techs = set()
    if plants_df is not None and not plants_df.empty:
        all_techs.update(plants_df["technology"].dropna().astype(str).tolist())
    if new_resources:
        for res in new_resources:
            if isinstance(res, (list, tuple)) and len(res) > 0:
                # Combine technology and tech_detail for matching (e.g., "NaturalGas | CCS")
                tech_name = str(res[0])
                tech_detail = str(res[1]) if len(res) > 1 else ""
                combined = f"{tech_name} {tech_detail}".strip()
                all_techs.add(combined)

    rps_qualified = set()
    ces_qualified = set()
    for tech in all_techs:
        tech_lower = str(tech).lower()
        for keyword in rps_keywords:
            if keyword in tech_lower:
                rps_qualified.add(tech)
                break
        for keyword in ces_keywords:
            if keyword in tech_lower:
                ces_qualified.add(tech)
                break
    return rps_qualified, ces_qualified


def compute_state_demand_fractions(region_bas, hierarchy_df, pop_fraction_df):
    """Compute the demand fraction for each state within a region.

    Uses population as a proxy for demand. Returns a dict mapping
    state -> demand_fraction where fractions sum to 1.0.
    """
    ba_to_state_val = extract_state_for_region(region_bas, hierarchy_df)

    # Compute total population in this region and population per state
    region_total_pop = 0.0
    state_pop_in_region = {}  # state -> total population from that state in this region

    for ba in region_bas:
        state_val = ba_to_state_val[ba]
        ba_pop_row = pop_fraction_df[
            (pop_fraction_df["region"] == ba) & (pop_fraction_df["st"] == state_val)
        ]
        if ba_pop_row.empty:
            # Fallback: assume equal population across BAs
            ba_pop = 1.0
        else:
            ba_pop = float(ba_pop_row.iloc[0]["total_population"])

        region_total_pop += ba_pop
        state_pop_in_region[state_val] = (
            state_pop_in_region.get(state_val, 0.0) + ba_pop
        )

    if region_total_pop == 0:
        return {}

    # Convert to fractions
    return {state: pop / region_total_pop for state, pop in state_pop_in_region.items()}


def get_state_policy_value(state, year, policy_type, policy_df):
    """Get the policy value for a specific state and year.

    Returns 0.0 if no policy is found.
    """
    policy_row = policy_df[(policy_df["year"] == year) & (policy_df["st"] == state)]
    if policy_row.empty:
        return 0.0

    col_name = "rps_all" if policy_type == "RPS" else "Value"
    if col_name not in policy_row.columns:
        return 0.0

    return float(policy_row.iloc[0][col_name])


def aggregate_policy_for_region(
    region_bas, year, policy_type, hierarchy_df, pop_fraction_df, policy_df
):
    """Compute population-weighted average policy requirement for a model region in a given year.

    The result is a weighted average of each state's policy requirement, where the weights
    are the fraction of the region's total population that resides in each state.
    """
    state_fractions = compute_state_demand_fractions(
        region_bas, hierarchy_df, pop_fraction_df
    )

    if not state_fractions:
        return 0.0

    # Compute weighted average of policy requirements by state
    total_requirement = 0.0
    for state_val, weight in state_fractions.items():
        policy_value = get_state_policy_value(state_val, year, policy_type, policy_df)
        total_requirement += weight * policy_value

    return total_requirement


def generate_emission_policies_csv(
    region_aggregations,
    model_years,
    state_zones,
    state_to_zone,
    hierarchy_df,
    pop_fraction_df,
    rps_df,
    ces_df,
    include_rps=True,
    include_ces=True,
    case_id="all",
):
    """Generate emission_policies.csv data.

    This function handles regions that may span multiple trading zones. For each
    region, the ESR constraint value for each zone is computed as:
        sum(state_demand_fraction * state_policy) for states in that zone

    A single region can have non-zero values in multiple ESR columns if it contains
    states from different trading zones.

    Args:
        region_aggregations: dict mapping region_name -> list of BAs
        model_years: list of years to generate policies for
        state_zones: list of sets, each set contains states in a trading zone
        state_to_zone: dict mapping state -> zone_index
        hierarchy_df: DataFrame with BA hierarchy info
        pop_fraction_df: DataFrame with population fractions per BA/state
        rps_df: DataFrame with RPS policy values
        ces_df: DataFrame with CES policy values
        include_rps: whether to include RPS constraints
        include_ces: whether to include CES constraints
        case_id: case identifier string

    Returns:
        (df, esr_map, esr_type_map, esr_policy_states):
            - df: DataFrame with emission policies
            - esr_map: {ESR_1: [regions], ESR_2: [regions], ...}
            - esr_type_map: {ESR_1: "RPS", ESR_2: "CES", ...}
            - esr_policy_states: {ESR_1: set(states), ...} - states whose policies are in each ESR
    """
    max_year_in_data_rps = rps_df["year"].max()
    max_year_in_data_ces = ces_df["year"].max()

    # Build ESR column names for each zone
    esr_constraint_num = 1
    esr_map = {}
    esr_type_map = {}
    esr_policy_states = {}  # ESR_name -> set of states whose policies are in this ESR
    zone_esr_map = {}  # zone_idx -> (rps_col, ces_col)

    for zone_idx in range(len(state_zones)):
        zone_rps = None
        zone_ces = None
        zone_states = state_zones[zone_idx]
        if include_rps:
            zone_rps = f"ESR_{esr_constraint_num}"
            esr_map[zone_rps] = (
                []
            )  # Will be filled with regions that have non-zero values
            esr_type_map[zone_rps] = "RPS"
            esr_policy_states[zone_rps] = set(zone_states)  # States in this zone
            esr_constraint_num += 1
        if include_ces:
            zone_ces = f"ESR_{esr_constraint_num}"
            esr_map[zone_ces] = []
            esr_type_map[zone_ces] = "CES"
            esr_policy_states[zone_ces] = set(zone_states)  # States in this zone
            esr_constraint_num += 1
        zone_esr_map[zone_idx] = (zone_rps, zone_ces)

    rows = []

    for region_name, region_bas in region_aggregations.items():
        # Get state demand fractions for this region
        state_fractions = compute_state_demand_fractions(
            region_bas, hierarchy_df, pop_fraction_df
        )

        if not state_fractions:
            continue

        for year in model_years:
            row = {"case_id": case_id, "year": int(year), "region": region_name}
            use_year_rps = min(year, max_year_in_data_rps)
            use_year_ces = min(year, max_year_in_data_ces)

            # For each zone, compute the weighted policy value from states in that zone
            for zone_idx, zone_states in enumerate(state_zones):
                zone_rps_col, zone_ces_col = zone_esr_map[zone_idx]

                # Sum demand_fraction * policy for states in this zone that are in this region
                rps_val = 0.0
                ces_val = 0.0

                for state_val, demand_frac in state_fractions.items():
                    if state_val not in zone_states:
                        continue

                    if include_rps:
                        state_rps = get_state_policy_value(
                            state_val, use_year_rps, "RPS", rps_df
                        )
                        rps_val += demand_frac * state_rps

                    if include_ces:
                        state_ces = get_state_policy_value(
                            state_val, use_year_ces, "CES", ces_df
                        )
                        ces_val += demand_frac * state_ces

                # Only add to row if there's a non-zero contribution
                if zone_rps_col and rps_val > 0:
                    row[zone_rps_col] = round(float(rps_val), 3)
                    if region_name not in esr_map[zone_rps_col]:
                        esr_map[zone_rps_col].append(region_name)

                if zone_ces_col and ces_val > 0:
                    row[zone_ces_col] = round(float(ces_val), 3)
                    if region_name not in esr_map[zone_ces_col]:
                        esr_map[zone_ces_col].append(region_name)

            rows.append(row)

    df = pd.DataFrame(rows)

    # Ensure CES >= RPS for each zone (RPS resources also qualify for CES)
    for zone_idx in range(len(state_zones)):
        zone_rps_col, zone_ces_col = zone_esr_map[zone_idx]
        if zone_rps_col and zone_ces_col:
            if zone_rps_col in df.columns and zone_ces_col in df.columns:
                for idx in df.index:
                    rps_val = (
                        df.at[idx, zone_rps_col]
                        if pd.notna(df.at[idx, zone_rps_col])
                        else 0.0
                    )
                    ces_val = (
                        df.at[idx, zone_ces_col]
                        if pd.notna(df.at[idx, zone_ces_col])
                        else 0.0
                    )
                    if ces_val < rps_val:
                        df.at[idx, zone_ces_col] = rps_val

    # Fill NaN with 0 for ESR columns
    esr_cols = [c for c in df.columns if c.startswith("ESR_")]
    for col in esr_cols:
        df[col] = df[col].fillna(0.0)

    # Sort ESR columns by numeric ID
    esr_cols_sorted = sorted(esr_cols, key=lambda x: int(x.split("_")[1]))
    columns = ["case_id", "year", "region"] + esr_cols_sorted
    df = df[columns]

    return df, esr_map, esr_type_map, esr_policy_states


SETTINGS_FILENAMES = [
    "model_definition.yml",
    "resources.yml",
    "fuels.yml",
    "transmission.yml",
    "distributed_gen.yml",
    "resource_tags.yml",
    "startup_costs.yml",
]


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
GROUP_OUTLINE_COLORS = [
    "#1b9e77",
    "#d95f02",
    "#7570b3",
    "#e7298a",
    "#66a61e",
    "#e6ab02",
    "#a6761d",
    "#666666",
    "#8dd3c7",
    "#fb8072",
    "#80b1d3",
    "#fdb462",
    "#b3de69",
    "#fccde5",
    "#bc80bd",
    "#ccebc5",
    "#ffed6f",
    "#1f78b4",
    "#33a02c",
    "#fb9a99",
]

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

# Cluster colors for visualization (fill colors after clustering)
CLUSTER_COLORS = [
    "#e41a1c",
    "#377eb8",
    "#4daf4a",
    "#984ea3",
    "#ff7f00",
    "#ffff33",
    "#a65628",
    "#f781bf",
    "#999999",
    "#66c2a5",
    "#fc8d62",
    "#8da0cb",
    "#e78ac3",
    "#a6d854",
    "#ffd92f",
    "#e5c494",
    "#b3b3b3",
    "#8dd3c7",
    "#ffffb3",
    "#bebada",
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
    """Hide the loading overlay."""
    el = document.getElementById("loading")
    if el:
        el.classList.add("hidden")


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


def hex_to_rgb(hex_color):
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    """Convert RGB tuple to hex color."""
    return "#{:02x}{:02x}{:02x}".format(int(rgb[0]), int(rgb[1]), int(rgb[2]))


def lighten_color(hex_color, factor=0.7):
    """Create a lighter version of a color by mixing with white."""
    r, g, b = hex_to_rgb(hex_color)
    # Mix with white (255, 255, 255)
    r = r + (255 - r) * factor
    g = g + (255 - g) * factor
    b = b + (255 - b) * factor
    return rgb_to_hex((r, g, b))


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


# ============================================================================
# Clustering Logic (adapted from cluster_regions.py)
# ============================================================================


def standardize_features(matrix):
    """Standardize columns to zero mean, unit variance."""
    means = np.nanmean(matrix, axis=0)
    stds = np.nanstd(matrix, axis=0)
    stds = np.where(stds == 0, 1.0, stds)
    return (matrix - means) / stds


def run_kmeans_simple(features, k, weights=None, max_iter=40, seed=42):
    """Simple k-means implementation returning (inertia, centers, labels)."""
    rng = np.random.default_rng(seed)
    n_samples = features.shape[0]
    if k <= 0 or n_samples == 0:
        return 0.0, None, None

    # Initialize centers from samples
    init_idx = rng.choice(n_samples, size=min(k, n_samples), replace=False)
    centers = features[init_idx]

    labels = np.zeros(n_samples, dtype=int)

    for _ in range(max_iter):
        # Assign
        dists = np.linalg.norm(features[:, None, :] - centers[None, :, :], axis=2) ** 2
        labels = np.argmin(dists, axis=1)

        # Update
        new_centers = []
        for i in range(k):
            mask = labels == i
            if not np.any(mask):
                # Keep old center if empty
                new_centers.append(centers[i])
                continue

            cluster_points = features[mask]
            if weights is not None:
                cluster_weights = weights[mask][:, None]
                new_center = (cluster_points * cluster_weights).sum(
                    axis=0
                ) / cluster_weights.sum()
            else:
                new_center = cluster_points.mean(axis=0)
            new_centers.append(new_center)

        new_centers = np.vstack(new_centers)
        if np.allclose(new_centers, centers):
            centers = new_centers
            break
        centers = new_centers

    # Compute inertia
    inertia = 0.0
    for i in range(k):
        mask = labels == i
        if not np.any(mask):
            continue
        cluster_points = features[mask]
        cluster_center = centers[i]
        sq_dists = np.sum((cluster_points - cluster_center) ** 2, axis=1)
        if weights is not None:
            inertia += float((sq_dists * weights[mask]).sum())
        else:
            inertia += float(sq_dists.sum())

    return inertia, centers, labels


def build_transmission_graph(transmission_df, valid_bas):
    """Build undirected weighted graph from transmission data."""
    G = nx.Graph()

    for _, row in transmission_df.iterrows():
        region_from = row["region_from"]
        region_to = row["region_to"]
        capacity = row["firm_ttc_mw"]

        # Only include edges between valid BAs
        if region_from not in valid_bas or region_to not in valid_bas:
            continue

        if G.has_edge(region_from, region_to):
            G[region_from][region_to]["weight"] += capacity
        else:
            G.add_edge(region_from, region_to, weight=capacity)

    # Add isolated nodes for BAs with no connections
    for ba in valid_bas:
        if ba not in G:
            G.add_node(ba)

    return G


def get_regional_groups(hierarchy_df, grouping_column, valid_bas):
    """Map regional groups to their BAs."""
    groups = {}

    for _, row in hierarchy_df.iterrows():
        ba = row["ba"]
        if ba not in valid_bas:
            continue

        group = row[grouping_column]
        if group not in groups:
            groups[group] = set()
        groups[group].add(ba)

    return groups


def agglomerative_cluster(graph, n_clusters, linkage="sum"):
    """
    Perform agglomerative clustering on a graph.

    Linkage methods:
    - 'sum': Merge based on sum of edge weights (standard).
    - 'average': Merge based on average edge weight (sum / (size_a * size_b)).
    - 'max': Merge based on maximum single edge weight (single linkage).
    """
    nodes = list(graph.nodes())
    n = len(nodes)

    if n <= n_clusters:
        # Each node is its own cluster
        return {i: {node} for i, node in enumerate(nodes)}

    # Initialize: each node is its own cluster
    clusters = {i: {node} for i, node in enumerate(nodes)}
    node_to_cluster = {node: i for i, node in enumerate(nodes)}
    cluster_sizes = {i: 1 for i in range(n)}

    # Build initial inter-cluster weights
    cluster_weights = {}
    for u, v, data in graph.edges(data=True):
        c1, c2 = node_to_cluster[u], node_to_cluster[v]
        if c1 != c2:
            key = (min(c1, c2), max(c1, c2))
            weight = data.get("weight", 1.0)

            if linkage == "max":
                current = cluster_weights.get(key, -float("inf"))
                cluster_weights[key] = max(current, weight)
            else:
                cluster_weights[key] = cluster_weights.get(key, 0) + weight

    # Merge until we reach target number of clusters
    while len(clusters) > n_clusters:
        if not cluster_weights:
            break

        # Find the pair with maximum score
        if linkage == "average":

            def get_score(k):
                c1, c2 = k
                w = cluster_weights[k]
                return w / (cluster_sizes[c1] * cluster_sizes[c2])

            best_pair = max(cluster_weights.keys(), key=get_score)
        else:
            best_pair = max(cluster_weights.keys(), key=lambda k: cluster_weights[k])

        c1, c2 = best_pair

        # Merge c2 into c1
        clusters[c1].update(clusters[c2])
        cluster_sizes[c1] += cluster_sizes[c2]
        del cluster_sizes[c2]

        for node in clusters[c2]:
            node_to_cluster[node] = c1
        del clusters[c2]

        # Update cluster weights
        new_weights = {}
        keys_to_remove = []

        for (ca, cb), weight in cluster_weights.items():
            if ca == c2 or cb == c2:
                keys_to_remove.append((ca, cb))
                # Redirect to c1
                other = cb if ca == c2 else ca
                if other != c1:
                    new_key = (min(c1, other), max(c1, other))

                    # Get existing weight between c1 and other
                    w1 = cluster_weights.get(new_key)

                    if linkage == "max":
                        val1 = w1 if w1 is not None else -float("inf")
                        new_val = max(val1, weight)
                    else:
                        val1 = w1 if w1 is not None else 0
                        new_val = val1 + weight

                    new_weights[new_key] = new_val

        for key in keys_to_remove:
            del cluster_weights[key]

        for key, weight in new_weights.items():
            cluster_weights[key] = weight

    # Renumber clusters to be sequential
    result = {}
    for i, (cluster_id, nodes_set) in enumerate(clusters.items()):
        result[i] = nodes_set

    return result


def spectral_cluster(graph, n_clusters):
    """
    Perform spectral clustering on the graph using Normalized Laplacian.
    """
    nodes = list(graph.nodes())
    n = len(nodes)
    if n <= n_clusters:
        return {i: {node} for i, node in enumerate(nodes)}

    node_to_idx = {node: i for i, node in enumerate(nodes)}

    # Adjacency matrix
    A = np.zeros((n, n))
    for u, v, data in graph.edges(data=True):
        i, j = node_to_idx[u], node_to_idx[v]
        w = data.get("weight", 1.0)
        A[i, j] = w
        A[j, i] = w

    # Degree matrix
    d = np.sum(A, axis=1)

    # Normalized Laplacian: L_sym = I - D^-1/2 * A * D^-1/2
    d_inv_sqrt = np.power(d, -0.5, where=d > 0)
    d_inv_sqrt[d == 0] = 0
    D_inv_sqrt = np.diag(d_inv_sqrt)

    L = np.eye(n) - D_inv_sqrt @ A @ D_inv_sqrt

    # Eigen decomposition
    vals, vecs = np.linalg.eigh(L)

    # First k eigenvectors
    k = n_clusters
    X = vecs[:, :k]

    # Normalize rows
    rows_norm = np.linalg.norm(X, axis=1, keepdims=True)
    rows_norm[rows_norm == 0] = 1
    X_normalized = X / rows_norm

    # Run K-Means
    _, _, labels = run_kmeans_simple(X_normalized, k)

    # Convert labels back to clusters
    clusters = {}
    for idx, label in enumerate(labels):
        if label not in clusters:
            clusters[label] = set()
        clusters[label].add(nodes[idx])

    return clusters


def louvain_cluster(graph):
    """
    Perform Louvain community detection on a graph.

    Returns communities that maximize modularity.
    The number of clusters is determined automatically.

    This is used for auto-optimize mode.
    """
    if graph.number_of_nodes() == 0:
        return {}

    if graph.number_of_edges() == 0:
        # No edges - each node is its own community
        return {i: {node} for i, node in enumerate(graph.nodes())}

    try:
        # Use NetworkX's Louvain implementation
        communities = nx.community.louvain_communities(
            graph, weight="weight", resolution=1.0, seed=42
        )

        # Convert to our format: dict of label -> set of nodes
        return {i: set(community) for i, community in enumerate(communities)}
    except Exception:
        # Fallback: each node is its own cluster
        return {i: {node} for i, node in enumerate(graph.nodes())}


def hierarchical_cluster(
    hierarchy_df,
    transmission_df,
    cluster_bas,
    grouping_column,
    target_regions,
    method="hierarchical-sum",
    esr_rectable_df=None,
):
    """
    Hierarchical clustering that respects grouping column boundaries.

    Phase 1: Cluster BAs within each grouping column region
    Phase 2: Merge entire grouping column regions together if needed

    Grouping column regions are never split across model regions.

    If esr_rectable_df is provided, also removes edges between BAs in states
    that cannot trade (even transitively), ensuring ESR-compatible clustering.
    """
    # Parse method
    if method == "spectral":
        algo = "spectral"
        linkage = None
    elif method.startswith("hierarchical-"):
        algo = "hierarchical"
        linkage = method.split("-")[1]
    else:
        # Default
        algo = "hierarchical"
        linkage = "sum"

    # Group BAs by their grouping column value
    groups = {}
    for _, row in hierarchy_df[hierarchy_df["ba"].isin(cluster_bas)].iterrows():
        ba = row["ba"]
        group = row[grouping_column]
        if group not in groups:
            groups[group] = set()
        groups[group].add(ba)

    # Build BA to state mapping for ESR-compatible clustering
    ba_to_state = {}
    if esr_rectable_df is not None:
        for ba in cluster_bas:
            row = hierarchy_df[hierarchy_df["ba"] == ba]
            if not row.empty:
                ba_to_state[ba] = str(row.iloc[0]["st"]).lower()

        # Pre-split grouping column groups by trading zones
        # This ensures BAs in non-trading states are never in the same group
        new_groups = {}
        for group_name, group_bas in groups.items():
            trading_subgroups = split_bas_by_trading_zones(
                group_bas, hierarchy_df, esr_rectable_df
            )
            if len(trading_subgroups) == 1:
                # No split needed
                new_groups[group_name] = group_bas
            else:
                # Split into multiple subgroups with suffixed names
                for i, subgroup in enumerate(trading_subgroups):
                    new_groups[f"{group_name}_tz{i+1}"] = subgroup
        groups = new_groups

    num_groups = len(groups)

    # If target is >= number of groups, cluster within each group
    if target_regions >= num_groups:
        # Build map of BA to group
        ba_to_group = {}
        for group, bas in groups.items():
            for ba in bas:
                ba_to_group[ba] = group

        # Build full graph
        graph = build_transmission_graph(transmission_df, cluster_bas)

        # Remove edges between different groups to enforce group boundaries
        edges_to_remove = []
        for u, v in graph.edges():
            if ba_to_group.get(u) != ba_to_group.get(v):
                edges_to_remove.append((u, v))

        # Also remove edges between BAs in non-trading states if ESR-compatible
        if esr_rectable_df is not None:
            for u, v in graph.edges():
                if (u, v) in edges_to_remove:
                    continue  # Already marked for removal
                state_u = ba_to_state.get(u)
                state_v = ba_to_state.get(v)
                if state_u and state_v and state_u != state_v:
                    if not can_states_trade(state_u, state_v, esr_rectable_df):
                        edges_to_remove.append((u, v))

        graph.remove_edges_from(edges_to_remove)

        if algo == "spectral":
            # For spectral clustering, we must run it independently on each group
            # to avoid mixing eigenvectors of disconnected components.
            # We use agglomerative clustering to decide how many clusters each group gets.

            # 1. Get reference allocation using agglomerative clustering
            ref_clusters = agglomerative_cluster(
                graph, target_regions, linkage="average"
            )

            # 2. Count clusters per group
            group_allocations = {g: 0 for g in groups}
            for _, nodes in ref_clusters.items():
                # Pick a representative node to find the group
                # (All nodes in a cluster are in the same group because edges were removed)
                if not nodes:
                    continue
                rep_node = next(iter(nodes))
                grp = ba_to_group[rep_node]
                group_allocations[grp] += 1

            # 3. Run spectral clustering on each group independently
            final_clusters = {}
            cluster_id_counter = 0

            for group, count in group_allocations.items():
                if count == 0:
                    continue

                group_bas = groups[group]
                # Build subgraph for this group
                subgraph = build_transmission_graph(transmission_df, group_bas)

                # Run spectral on subgraph
                sub_clusters = spectral_cluster(subgraph, count)

                # Add to final result
                for _, nodes in sub_clusters.items():
                    final_clusters[cluster_id_counter] = nodes
                    cluster_id_counter += 1

            return final_clusters
        else:
            # Run agglomerative clustering on the whole graph
            # This prioritizes the strongest connections across all groups
            # instead of pre-allocating a fixed number of clusters per group
            return agglomerative_cluster(graph, target_regions, linkage=linkage)

    else:
        # target_regions < num_groups: need to merge entire groups
        # Phase 1: Each group becomes a single unit
        # Phase 2: Merge groups based on inter-group transmission capacity

        # Build a graph where nodes are groups and edges are total transmission between groups
        group_graph = nx.Graph()
        for group in groups:
            group_graph.add_node(group)

        # Calculate inter-group transmission capacity
        for _, row in transmission_df.iterrows():
            ba_from = row["region_from"]
            ba_to = row["region_to"]
            capacity = row["firm_ttc_mw"]

            # Find which groups these BAs belong to
            group_from = None
            group_to = None
            for group, bas in groups.items():
                if ba_from in bas:
                    group_from = group
                if ba_to in bas:
                    group_to = group

            if group_from and group_to and group_from != group_to:
                # If ESR-compatible, skip edges between BAs in non-trading states
                if esr_rectable_df is not None:
                    state_from = ba_to_state.get(ba_from)
                    state_to = ba_to_state.get(ba_to)
                    if state_from and state_to and state_from != state_to:
                        if not can_states_trade(state_from, state_to, esr_rectable_df):
                            continue  # Skip this edge

                if group_graph.has_edge(group_from, group_to):
                    group_graph[group_from][group_to]["weight"] += capacity
                else:
                    group_graph.add_edge(group_from, group_to, weight=capacity)

        # Cluster groups
        if algo == "spectral":
            group_clusters = spectral_cluster(group_graph, target_regions)
        else:
            group_clusters = agglomerative_cluster(
                group_graph, target_regions, linkage=linkage
            )

        # Convert group clusters to BA clusters
        all_clusters = {}
        cluster_id = 0

        for label, group_set in group_clusters.items():
            # Combine all BAs from all groups in this cluster
            combined_bas = set()
            for group in group_set:
                combined_bas.update(groups[group])
            all_clusters[cluster_id] = combined_bas
            cluster_id += 1

        return all_clusters


def calculate_modularity(graph, clusters):
    """
    Calculate the modularity score for a clustering result.

    Modularity measures how well a network is divided into communities.
    Higher values (closer to 1) indicate better clustering.
    Values typically range from -0.5 to 1.
    """
    if not clusters or graph.number_of_edges() == 0:
        return 0.0

    # Convert clusters dict to list of sets for networkx
    communities = [clusters[label] for label in sorted(clusters.keys())]

    # Filter to only include nodes that are in the graph
    graph_nodes = set(graph.nodes())
    communities = [c & graph_nodes for c in communities]
    communities = [c for c in communities if len(c) > 0]

    if not communities:
        return 0.0

    try:
        # Use networkx modularity function
        modularity = nx.community.modularity(graph, communities, weight="weight")
        return modularity
    except Exception:
        return 0.0


def find_optimal_clusters(
    hierarchy_df,
    transmission_df,
    cluster_bas,
    grouping_column,
    min_regions,
    max_regions,
):
    """
    Find the optimal clustering using Louvain community detection.

    Uses Louvain algorithm which directly maximizes modularity.
    The min_regions and max_regions are used to constrain the result:
    - If Louvain finds fewer clusters than min_regions, we don't split further
    - If Louvain finds more clusters than max_regions, we merge using agglomerative

    Returns (best_clusters, best_n, best_modularity, all_scores)
    """
    # Group BAs by their grouping column value
    groups = {}
    for _, row in hierarchy_df[hierarchy_df["ba"].isin(cluster_bas)].iterrows():
        ba = row["ba"]
        group = row[grouping_column]
        if group not in groups:
            groups[group] = set()
        groups[group].add(ba)

    # Build graph for the cluster BAs
    graph = build_transmission_graph(transmission_df, cluster_bas)

    # Use Louvain on within-group subgraphs, respecting grouping boundaries
    all_clusters = {}
    cluster_id = 0

    for group, group_bas in groups.items():
        if len(group_bas) == 1:
            all_clusters[cluster_id] = group_bas
            cluster_id += 1
        else:
            # Build subgraph for this group
            subgraph = build_transmission_graph(transmission_df, group_bas)
            # Use Louvain to find natural communities within this group
            sub_clusters = louvain_cluster(subgraph)

            for label, nodes in sub_clusters.items():
                all_clusters[cluster_id] = nodes
                cluster_id += 1

    num_clusters = len(all_clusters)

    # Capture optimal here
    optimal_clusters = dict(all_clusters)

    # If we have fewer clusters than min_regions, split using agglomerative clustering
    if num_clusters < min_regions:
        # We need to split clusters until we reach min_regions
        # We will greedily split the largest cluster (by number of BAs)

        # Convert to mutable dict
        current_clusters = dict(all_clusters)
        next_id = max(current_clusters.keys()) + 1 if current_clusters else 0

        while len(current_clusters) < min_regions:
            # Find cluster with most BAs that has at least 2 BAs
            candidates = [
                (cid, len(nodes))
                for cid, nodes in current_clusters.items()
                if len(nodes) > 1
            ]

            if not candidates:
                break  # Cannot split further

            # Pick largest
            cid_to_split, _ = max(candidates, key=lambda x: x[1])
            nodes_to_split = current_clusters[cid_to_split]

            # Build subgraph
            subgraph = build_transmission_graph(transmission_df, nodes_to_split)

            # Split into 2 using agglomerative clustering (average linkage for balanced splits)
            split_res = agglomerative_cluster(subgraph, 2, linkage="average")

            # Update clusters
            del current_clusters[cid_to_split]
            current_clusters[cid_to_split] = split_res[0]  # Reuse ID for one
            current_clusters[next_id] = split_res[1]  # New ID for other
            next_id += 1

        all_clusters = current_clusters
        num_clusters = len(all_clusters)

    # If we have more clusters than max_regions, merge using agglomerative
    if num_clusters > max_regions:
        # Build a graph of the current clusters
        cluster_graph = nx.Graph()
        for cid in all_clusters:
            cluster_graph.add_node(cid)

        # Add edges based on transmission between clusters
        for _, row in transmission_df.iterrows():
            ba_from = row["region_from"]
            ba_to = row["region_to"]
            capacity = row["firm_ttc_mw"]

            # Find which clusters these BAs belong to
            cluster_from = None
            cluster_to = None
            for cid, bas in all_clusters.items():
                if ba_from in bas:
                    cluster_from = cid
                if ba_to in bas:
                    cluster_to = cid

            if (
                cluster_from is not None
                and cluster_to is not None
                and cluster_from != cluster_to
            ):
                if cluster_graph.has_edge(cluster_from, cluster_to):
                    cluster_graph[cluster_from][cluster_to]["weight"] += capacity
                else:
                    cluster_graph.add_edge(cluster_from, cluster_to, weight=capacity)

        # Merge clusters down to max_regions
        merged = agglomerative_cluster(cluster_graph, max_regions)

        # Convert back to BA clusters
        new_clusters = {}
        for new_id, old_cluster_ids in merged.items():
            combined_bas = set()
            for old_id in old_cluster_ids:
                combined_bas.update(all_clusters[old_id])
            new_clusters[new_id] = combined_bas

        all_clusters = new_clusters

    # Calculate final modularity
    modularity = calculate_modularity(graph, all_clusters)
    num_clusters = len(all_clusters)

    return (
        all_clusters,
        num_clusters,
        modularity,
        {num_clusters: modularity},
        optimal_clusters,
    )


def generate_cluster_names(clusters, groups):
    """Generate meaningful cluster names based on smallest containing grouping column.

    Naming rules allow only state plus one other grouping column:
    1) Use the state code when all BAs in the cluster share a state.
    2) Pick a single grouping column (other than state) that can name every
       remaining cluster; all non-state names must come from that one column.
    3) If no column satisfies (2), still pick one column (broadest available)
       and name every non-state cluster from that same column.
    """
    # Grouping columns ordered from smallest to largest geographic scope
    GROUPING_HIERARCHY = [
        "st",
        "cendiv",
        "transgrp",
        "nercr",
        "transreg",
        "interconnect",
    ]

    cluster_names = {}
    name_counts = {}  # Track counts for each base name

    def common_value(nodes_set, column):
        vals = (
            state.hierarchy_df[state.hierarchy_df["ba"].isin(nodes_set)][column]
            .dropna()
            .unique()
        )
        return vals[0] if len(vals) == 1 else None

    # First pass: identify clusters that can use state names
    single_state_labels = set()
    for label, nodes in clusters.items():
        st_val = common_value(nodes, "st")
        if st_val:
            single_state_labels.add(label)

    # Choose one naming column for the remaining clusters
    candidate_columns = [col for col in GROUPING_HIERARCHY if col != "st"]
    naming_column = None

    for col in candidate_columns:
        if col not in state.hierarchy_df.columns:
            continue

        all_cover = True
        for label, nodes in clusters.items():
            if label in single_state_labels:
                continue
            if common_value(nodes, col) is None:
                all_cover = False
                break

        if all_cover:
            naming_column = col
            break

    # Fallback: pick the broadest available column
    if naming_column is None:
        for col in reversed(candidate_columns):
            if col in state.hierarchy_df.columns:
                naming_column = col
                break

    # Assign names
    for label, nodes in clusters.items():
        nodes_list = list(nodes)

        # Single BA or single-state cluster
        st_val = common_value(nodes, "st")
        if st_val:
            base_name = st_val if len(nodes_list) > 1 else st_val
        else:
            if naming_column and naming_column in state.hierarchy_df.columns:
                vals = (
                    state.hierarchy_df[state.hierarchy_df["ba"].isin(nodes)][
                        naming_column
                    ]
                    .dropna()
                    .unique()
                )
                if len(vals) == 1:
                    base_name = vals[0]
                else:
                    # Still stick to one column; join values if mixed
                    base_name = "-".join(sorted([str(v) for v in vals])) or "Region"
            else:
                base_name = "Region"

        if base_name in name_counts:
            name_counts[base_name] += 1
            cluster_names[label] = f"{base_name}{name_counts[base_name]}"
        else:
            name_counts[base_name] = 1
            cluster_names[label] = f"{base_name}1"

    return cluster_names


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

            clusters, chosen_n, modularity, all_scores, optimal_clusters = (
                find_optimal_clusters(
                    hierarchy,
                    state.transmission_df,
                    cluster_bas,
                    grouping_column,
                    actual_min,
                    actual_max,
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

            if method == "louvain":
                clusters, _, modularity_val, _, _ = find_optimal_clusters(
                    hierarchy,
                    state.transmission_df,
                    cluster_bas,
                    grouping_column,
                    actual_target,  # min
                    actual_target,  # max
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
                )

            # Calculate modularity for info
            graph = build_transmission_graph(state.transmission_df, cluster_bas)
            modularity = calculate_modularity(graph, clusters)
            info["modularity"] = modularity

        # Generate names
        cluster_names = generate_cluster_names(clusters, groups)

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

    # Resource groups depend on region aggregations
    state.resource_group_files = {}
    state.resource_group_assignments = None

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
    state.emission_policies_df = None
    # Note: esr_rps_techs and esr_ces_techs are set during ESR generation
    # and don't persist in AppState, so no reset needed

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
    """
    # Clear ESR-related state that depends on planning years
    reset_planning_year_dependent_state()

    # Refresh ESR results UI so that any previously displayed constraints/zones/CSV
    # are cleared or updated to reflect the reset state.
    try:
        render_esr_results()
    except NameError:
        # If ESR UI rendering is not available in this context, skip UI refresh.
        pass

    # Inform the user that ESR policies were reset due to the change in model years.
    try:
        set_status(
            "ESR policy state has been reset because model years changed. Please rerun ESR analysis.",
            status_type="info",
        )
    except NameError:
        # If status messaging is not available, fail silently.
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
    for g in state.plant_candidates:
        parts.append(
            f"<div class='candidate-item'><strong>{g['model_region']}</strong> — {g['tech_group']} (desired {g['desired']}, assigned {g['num_clusters']}; {g['total_capacity']:.0f} MW, HR IQR {g['hr_iqr']:.2f})</div>"
        )
    container.innerHTML = "".join(parts)


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

    Expected to live at web/data/atb_options.json. This is designed to be regenerated
    offline from technology_costs_atb.parquet; the web app only consumes the index.
    """
    try:
        response = await fetch("./data/atb_options.json")
        if not response.ok:
            state.atb_options = []
            state.atb_index = {}
            state.atb_years = []
            return

        txt = await response.text()
        payload = json.loads(txt)
        options = payload.get("options", []) if isinstance(payload, dict) else []

        # Normalize to list of dicts containing at least data_year/technology/tech_detail/cost_case
        normalized = []
        for row in options:
            if not isinstance(row, dict):
                continue
            if not all(
                k in row
                for k in ("data_year", "technology", "tech_detail", "cost_case")
            ):
                continue
            normalized.append(row)

        state.atb_options = normalized
        years = sorted(
            {
                int(r["data_year"])
                for r in normalized
                if str(r.get("data_year", "")).isdigit()
            }
        )
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
    return scenarios[0] if scenarios else None


def populate_fuel_scenario_selects(event=None):
    """Populate the Fuel Scenarios selects based on the selected fuel data year."""
    year_el = document.getElementById("fuelDataYear")
    help_el = document.getElementById("fuelScenarioHelp")

    coal_el = document.getElementById("fuelScenarioCoal")
    gas_el = document.getElementById("fuelScenarioNaturalGas")
    dist_el = document.getElementById("fuelScenarioDistillate")
    ura_el = document.getElementById("fuelScenarioUranium")

    try:
        selected_year = int(_get_select_value(year_el, 0) or 0)
    except Exception:
        selected_year = 0

    if not state.fuel_scenario_index or selected_year not in state.fuel_scenario_index:
        # Fallback: just offer 'reference'
        _set_select_options_simple(coal_el, ["reference"], selected_value="reference")
        _set_select_options_simple(gas_el, ["reference"], selected_value="reference")
        _set_select_options_simple(dist_el, ["reference"], selected_value="reference")
        _set_select_options_simple(ura_el, ["reference"], selected_value="reference")
        if help_el:
            help_el.textContent = (
                "Fuel scenario options not available for this year; using 'reference'."
            )
        return

    year_map = state.fuel_scenario_index.get(selected_year, {})

    def set_for(fuel_key: str, select_el):
        scenarios = year_map.get(fuel_key, ["reference"])
        current = _get_select_value(select_el, None)
        default_val = _default_scenario_for_fuel(fuel_key, scenarios)
        chosen = current if current in scenarios else default_val
        _set_select_options_simple(select_el, scenarios, selected_value=chosen)

    set_for("coal", coal_el)
    set_for("naturalgas", gas_el)
    set_for("distillate", dist_el)
    set_for("uranium", ura_el)

    if help_el:
        # Inform about coal default if relevant
        coal_scenarios = year_map.get("coal", [])
        if "no_111d" in set(coal_scenarios):
            help_el.textContent = (
                "Coal defaults to 'no_111d' for this year (available)."
            )
        else:
            help_el.textContent = (
                "Coal 'no_111d' not available for this year; defaulting to 'reference'."
            )


def populate_fuel_data_year_select(event=None):
    """Populate the Fuel Data Year dropdown from loaded fuel_prices.csv."""
    year_el = document.getElementById("fuelDataYear")
    if not year_el:
        return

    # Gather available years from loaded index
    if not state.fuel_scenario_index:
        # Fallback to a reasonable default when fuel_prices.csv can't be loaded
        _set_select_options_simple(year_el, [2025], selected_value="2025")
        return

    years = sorted(state.fuel_scenario_index.keys())
    current = _get_select_value(year_el, None)

    # Choose a default: keep current if valid; else prefer 2025 if present; else latest.
    selected = None
    try:
        current_int = int(current) if current is not None else None
    except Exception:
        current_int = None

    if current_int in years:
        selected = current_int
    elif 2025 in years:
        selected = 2025
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
    selected_case = _get_select_value(case_el, cases[0] if cases else None)
    if selected_case not in cases and cases:
        selected_case = cases[0]
    _set_select_options(case_el, cases, selected_value=selected_case)
    update_atb_ccs_cost_visibility()


def on_atb_picker_change(event=None):
    populate_atb_picker()
    update_atb_ccs_cost_visibility()


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


def parse_new_resources_text(text):
    """Parse manual new_resources lines.

    Each non-empty line should be: Technology | Tech Detail | Cost Case | Size
    """
    if not text:
        return []
    items = []
    for line in str(text).splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 4:
            continue
        tech, detail, case, size = parts
        if not tech or not detail or not case or not size:
            continue
        try:
            size_val = int(float(size))
        except Exception:
            continue
        items.append([tech, detail, case, size_val])
    return items


def render_new_resources_list():
    """Render both regular and modified (attribute-override) new resources together."""
    container = document.getElementById("newResourcesList")
    raw_el = document.getElementById("newResourcesRaw")
    if not container:
        return

    # Parse regular resources from textarea
    regular_items = []
    if raw_el:
        regular_items = parse_new_resources_text(raw_el.value)

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

    # Render regular resources with delete buttons
    for idx, (tech, detail, case, size) in enumerate(regular_items):
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
        parts.append(
            f"<div class='candidate-item' style='display: flex; justify-content: space-between; align-items: center;'>"
            f"<span><strong>{html.escape(str(tech))}</strong> — {html.escape(str(detail))} — {html.escape(str(case))} — {int(size)} MW{ccs_note}</span>"
            f"<button onclick='window.deleteNewResource({idx})' style='padding: 2px 8px; font-size: 11px; background: #dc3545; color: white; border: none; border-radius: 3px; cursor: pointer;'>Delete</button>"
            f"</div>"
        )

    # Render modified resources (with attribute overrides) with delete buttons
    for key, item, attr_mods in modified_items:
        tech = item.get("technology")
        detail = item.get("tech_detail")
        case = item.get("cost_case")
        size = item.get("size_mw", 1)

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

        parts.append(
            f"<div class='candidate-item' style='display: flex; justify-content: space-between; align-items: center; background-color: #fff3cd;'>"
            f"<span><strong>{html.escape(str(tech))}</strong> — {html.escape(str(detail))} — {html.escape(str(case))} — {int(size)} MW{ccs_note} "
            f"<span style='color: #856404; font-size: 10px;'>({html.escape(mod_text)})</span></span>"
            f"<button onclick='window.deleteModifiedNewResource(\"{html.escape(key, quote=True)}\")' style='padding: 2px 8px; font-size: 11px; background: #dc3545; color: white; border: none; border-radius: 3px; cursor: pointer;'>Delete</button>"
            f"</div>"
        )

    container.innerHTML = "".join(parts)


def on_add_new_resource(event):
    raw_el = document.getElementById("newResourcesRaw")
    if not raw_el:
        return

    year_el = document.getElementById("atbYearSelect")
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
        }
        render_modified_resources_list()
        render_new_resources_list()  # Also update the main list to show this resource

        # Clear override fields for next entry
        for attr, el_id in override_fields:
            el = document.getElementById(el_id)
            if el is not None:
                try:
                    el.value = ""
                except Exception:
                    pass

        n = len(attr_overrides)
        set_status(
            f"Added '{key}' as a modified resource with {n} attribute override(s).",
            "info",
        )
    else:
        line = f"{tech} | {detail} | {case} | {size}"
        existing = raw_el.value.strip()
        raw_el.value = (existing + "\n" + line).strip() if existing else line
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
        render_new_resources_list()


def delete_new_resource(index):
    """Delete a regular new resource by index.

    The index refers to the N-th parsed resource line (as shown in the UI).
    Operates on original textarea contents to preserve comments and any
    non-conforming lines.
    """
    raw_el = document.getElementById("newResourcesRaw")
    if not raw_el:
        return

    original_text = raw_el.value or ""
    lines = original_text.splitlines()

    new_lines = []
    parsed_idx = 0
    deleted = False

    for line in lines:
        parts = [p.strip() for p in line.split("|")]
        is_resource_line = (
            len(parts) == 4 and all(parts) and not line.strip().startswith("#")
        )

        if is_resource_line:
            if parsed_idx == index and not deleted:
                deleted = True
                parsed_idx += 1
                continue
            parsed_idx += 1

        new_lines.append(line)

    if deleted:
        raw_el.value = "\n".join(new_lines)
        render_new_resources_list()
        set_status("Resource deleted.", "success")


def delete_modified_new_resource(key):
    """Delete a modified new resource with attribute overrides by key."""
    if key in state.modified_new_resources:
        del state.modified_new_resources[key]
        render_modified_resources_list()
        render_new_resources_list()  # Update main list too
        set_status(f"Deleted modified resource: {key}", "success")
    else:
        set_status(f"Resource not found: {key}", "error")


# Export delete functions to JavaScript (must be after function definitions)
window.deleteNewResource = create_proxy(delete_new_resource)
window.deleteModifiedNewResource = create_proxy(delete_modified_new_resource)


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
        parts.append(
            f"<div class='candidate-item' style='display: flex; justify-content: space-between; align-items: center;'>"
            f"<span><strong>{html.escape(key)}</strong> — {html.escape(str(new_tech))} — {html.escape(str(tag_class))} — {html.escape(str(fuel_desc))} — ({html.escape(mod_text)})</span>"
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
    name_el = document.getElementById("modResName")
    base_tech_el = document.getElementById("modBaseTech")
    base_detail_el = document.getElementById("modBaseTechDetail")
    base_case_el = document.getElementById("modBaseCostCase")
    base_size_el = document.getElementById("modBaseSizeMw")
    new_tech_el = document.getElementById("modNewTech")
    new_detail_el = document.getElementById("modNewTechDetail")
    new_case_el = document.getElementById("modNewCostCase")

    fuel_type_el = document.getElementById("modFuelType")
    std_fuel_el = document.getElementById("modStandardFuel")
    new_fuel_name_el = document.getElementById("modNewFuelName")
    new_fuel_price_el = document.getElementById("modNewFuelPrice")
    new_fuel_ef_el = document.getElementById("modNewFuelEf")

    tag_class_el = document.getElementById("modTagClass")
    is_commit_el = document.getElementById("modIsCommit")

    key = str(_get_select_value(name_el, "")).strip()
    if not key:
        set_status("Modified resource needs a name/key.", "error")
        return

    base_tech = str(_get_select_value(base_tech_el, "")).strip()
    base_detail = str(_get_select_value(base_detail_el, "")).strip()
    base_case = str(_get_select_value(base_case_el, "")).strip()
    try:
        base_size = int(float(_get_select_value(base_size_el, 100)))
    except Exception:
        base_size = 100

    new_tech = str(_get_select_value(new_tech_el, "")).strip()
    new_detail = str(_get_select_value(new_detail_el, "")).strip()
    new_case = str(_get_select_value(new_case_el, "")).strip()

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

    if not (
        base_tech and base_detail and base_case and new_tech and new_detail and new_case
    ):
        set_status(
            "Fill out both the base ATB resource and the new resource identity.",
            "error",
        )
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
    set_status(f"Added modified resource: {key}", "success")


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
    """Load LCOE data for a resource from cached assignments.

    NOTE: Parquet fallback is intentionally removed.  Decoding large parquet
    files in Pyodide/WASM can hang the browser for hours.  Users must
    (re-)run resource-group generation so that assignments are cached in
    ``state.resource_group_assignments``.
    """
    if state.resource_group_assignments is None:
        window.console.log(
            f"Renewables: no cached assignments – cannot load {resource_key}"
        )
        return None

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
    # df["capacity_mw"] = df["cpa_mw"]
    window.console.log(
        f"Renewables: loaded {resource_key} from assignments, rows={len(df):,}"
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
    meta_el.textContent = (
        f"Baseline {int(round(baseline_capacity)):,} MW; "
        f"available total {int(round(available_capacity)):,} MW "
        f"(additional above baseline: {int(round(extra_available)):,} MW); "
        f"current max included LCOE {lcoe_max:.2f}."
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
        region_aggs = _get_region_aggregations_or_raise()
        if state.resource_group_assignments is None:
            set_renewables_status(
                "Resource group assignments not cached. "
                "Please re-run 'Generate Resource Groups' first.",
                "error",
            )
            return
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
                continue

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
                "Petroleum Liquids",
            ],
        }

    alt_num_clusters = cluster_settings.get("alt_num_clusters")
    if not isinstance(alt_num_clusters, dict) or not alt_num_clusters:
        alt_num_clusters = None

    # New-build resources come from textarea
    raw_el = document.getElementById("newResourcesRaw")
    new_resources = parse_new_resources_text(raw_el.value if raw_el else "")
    if not new_resources:
        # Seed a minimal starter set
        new_resources = [
            ["NaturalGas", "1-on-1 Combined Cycle (H-Frame)", "Moderate", 500],
            ["LandbasedWind", "Class3", "Moderate", 1],
            ["UtilityPV", "Class1", "Moderate", 1],
            ["Utility-Scale Battery Storage", "Lithium Ion", "Moderate", 1],
            ["Nuclear", "Nuclear - Large", "Moderate", 1000],
        ]

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

    # Build resource_modifiers from modified_new_resources.
    # Only include attribute-only overrides (no identity or fuel changes) with
    # a non-empty attr_modifiers dict. Keys are translated from internal UI names
    # to ATB column-style names (e.g. variable_o_m_mwh → Var_OM_Cost_per_MWh).
    if state.modified_new_resources:
        resource_modifiers = {}
        for k, v in sorted(state.modified_new_resources.items()):
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
            resource_modifiers[k] = modifier_dict
        if resource_modifiers:
            out["resource_modifiers"] = resource_modifiers

    # Also output modified_new_resources for custom fuels (if any have new fuel types)
    modified_with_fuel = {}
    if state.modified_new_resources:
        for k, v in sorted(state.modified_new_resources.items()):
            # Only include in modified_new_resources if it has custom fuel or needs the full structure
            if v.get("fuel_type") == "new" or (
                v.get("new_technology") != v.get("technology")
                or v.get("new_tech_detail") != v.get("tech_detail")
                or v.get("new_cost_case") != v.get("cost_case")
            ):
                modified_with_fuel[k] = {
                    "technology": v["technology"],
                    "tech_detail": v["tech_detail"],
                    "cost_case": v["cost_case"],
                    "size_mw": v["size_mw"],
                    "new_technology": v["new_technology"],
                    "new_tech_detail": v["new_tech_detail"],
                    "new_cost_case": v["new_cost_case"],
                }
    if modified_with_fuel:
        out["modified_new_resources"] = modified_with_fuel

    # Remove nulls to keep YAML clean
    out = {k: v for k, v in out.items() if v is not None}
    resources_yaml = yaml.dump(out, default_flow_style=False, sort_keys=False)
    comment_block = _format_renewables_capacity_comments()
    return f"{comment_block}\n{resources_yaml}" if comment_block else resources_yaml


def generate_fuels_settings():
    fuel_year = int(_get_select_value(document.getElementById("fuelDataYear"), 2025))

    # Fuel scenarios: default coal to no_111d if present for selected year; otherwise reference.
    coal_sel = _get_select_value(document.getElementById("fuelScenarioCoal"), None)
    gas_sel = _get_select_value(document.getElementById("fuelScenarioNaturalGas"), None)
    dist_sel = _get_select_value(
        document.getElementById("fuelScenarioDistillate"), None
    )
    ura_sel = _get_select_value(document.getElementById("fuelScenarioUranium"), None)

    # Ensure selects are populated (e.g., if user generates settings before load finishes)
    if not coal_sel or not gas_sel or not dist_sel or not ura_sel:
        populate_fuel_scenario_selects()
        coal_sel = _get_select_value(
            document.getElementById("fuelScenarioCoal"), "reference"
        )
        gas_sel = _get_select_value(
            document.getElementById("fuelScenarioNaturalGas"), "reference"
        )
        dist_sel = _get_select_value(
            document.getElementById("fuelScenarioDistillate"), "reference"
        )
        ura_sel = _get_select_value(
            document.getElementById("fuelScenarioUranium"), "reference"
        )

    fuel_scenarios = {
        "coal": str(coal_sel or "reference"),
        "naturalgas": str(gas_sel or "reference"),
        "distillate": str(dist_sel or "reference"),
        "uranium": str(ura_sel or "reference"),
    }

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

    # Check regular new resources from textarea for CCS
    raw_el = document.getElementById("newResourcesRaw")
    if raw_el:
        new_resources = parse_new_resources_text(raw_el.value if raw_el else "")
        for tech, detail, case, size in new_resources:
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
    if (
        state.esr_map
        and state.esr_type_map
        and state.esr_policy_states
        and state.region_aggregations
    ):
        regional_tag_values = {}

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
                # Get states in this region
                region_states = get_states_in_region(region_bas, state.hierarchy_df)

                # Check if any state in this region can export to any policy state
                # Uses asymmetric check: rectable.loc[policy_state, generator_state]
                can_satisfy = False
                if state.rectable_df is not None:
                    for gen_state in region_states:
                        for policy_state in policy_states:
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


def generate_model_definition_settings():
    region_aggs = _get_region_aggregations_or_raise()
    model_regions = sorted(region_aggs.keys())

    target_usd_year = int(
        _get_select_value(document.getElementById("targetUsdYear"), 2024)
    )
    utc_offset = int(_get_select_value(document.getElementById("utcOffset"), -5))
    model_years = parse_int_list(
        _get_select_value(document.getElementById("modelYears"), "")
    )
    planning_years = parse_int_list(
        _get_select_value(document.getElementById("planningYears"), "")
    )

    if not model_years or not planning_years or len(model_years) != len(planning_years):
        raise Exception("Model years and first planning years must be the same length.")

    out = {
        "model_regions": model_regions,
        "region_aggregations": region_aggs,
        "target_usd_year": target_usd_year,
        "model_year": model_years,
        "model_first_planning_year": planning_years,
        "utc_offset": utc_offset,
        "generator_columns": DEFAULT_GENERATOR_COLUMNS,
    }
    return yaml.dump(out, default_flow_style=False, sort_keys=False)


def build_settings_yamls():
    return {
        "model_definition.yml": generate_model_definition_settings(),
        "resources.yml": generate_resources_settings(),
        "fuels.yml": generate_fuels_settings(),
        "transmission.yml": generate_transmission_settings(),
        "distributed_gen.yml": generate_distributed_gen_settings(),
        "resource_tags.yml": generate_resource_tags_settings(),
        "startup_costs.yml": generate_startup_costs_settings(),
    }


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
        set_status("Settings YAMLs generated.", "success")
    except Exception as exc:
        state.settings_yamls = {}
        set_status(f"Settings generation error: {exc}", "error")


def _download_text_file(filename, content):
    blob = window.Blob.new([content], to_js({"type": "text/yaml"}))
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
    try:
        return pd.read_parquet(BytesIO(data))
    except Exception:
        import os
        import uuid

        import duckdb

        tmp_dir = "/tmp"
        try:
            os.makedirs(tmp_dir, exist_ok=True)
        except Exception:
            pass
        tmp_path = f"{tmp_dir}/pg_parquet_{uuid.uuid4().hex}.parquet"
        with open(tmp_path, "wb") as f:
            f.write(data)
        try:
            return duckdb.read_parquet(tmp_path).df()
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass


async def _fetch_csv_df(url, **kwargs):
    response = await fetch(url)
    if not response.ok:
        raise Exception(f"Failed to load CSV: {url} ({response.status})")
    text = await response.text()
    if text.startswith("<!"):
        raise Exception(f"Got HTML instead of CSV from {url}")
    return pd.read_csv(StringIO(text), **kwargs)


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
    return name or "resource_groups"


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
        # Collect new resources from textarea
        raw_el = document.getElementById("newResourcesRaw")
        new_resources = parse_new_resources_text(raw_el.value if raw_el else "")

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
        document.getElementById("downloadYamlBtn").addEventListener(
            "click", create_proxy(on_download_yaml)
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
        document.getElementById("newResourcesRaw").addEventListener(
            "input", create_proxy(lambda e: render_new_resources_list())
        )
        document.getElementById("addModifiedResourceBtn").addEventListener(
            "click", create_proxy(on_add_modified_resource)
        )
        document.getElementById("clearModifiedResourcesBtn").addEventListener(
            "click", create_proxy(on_clear_modified_resources)
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

        # Resource groups
        document.getElementById("generateResourceGroupsBtn").addEventListener(
            "click", create_proxy(on_generate_resource_groups)
        )
        document.getElementById("downloadResourceGroupsBtn").addEventListener(
            "click", create_proxy(on_download_resource_groups)
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
        document.getElementById("downloadEmissionPoliciesBtn").addEventListener(
            "click", create_proxy(on_download_emission_policies)
        )

        # Fuel scenario options
        document.getElementById("fuelDataYear").addEventListener(
            "change", create_proxy(populate_fuel_scenario_selects)
        )

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
        populate_fuel_data_year_select()
        populate_fuel_scenario_selects()
        render_new_resources_list()
        render_modified_resources_list()
        populate_settings_file_select()
        update_settings_preview()
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
