"""
ESR (Energy Share Requirement) utility functions for the PowerGenome web app.

These pure functions handle generation of emission policy constraints based on
state-level RPS/CES policies and BA-to-state mappings.
"""

import pandas as pd


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
