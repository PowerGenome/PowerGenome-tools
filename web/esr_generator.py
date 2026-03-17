"""
Energy Share Requirements (ESR) Generator

Generates RPS/CES constraints, zone assignments, and technology qualifications
for PowerGenome settings based on region aggregations and state trading rules.
"""

from io import StringIO

import pandas as pd


class ESRGenerationError(Exception):
    """Raised when ESR generation is not possible with the given region configuration."""

    pass


def extract_state_for_region(region_bas, hierarchy_df):
    """
    Extract states for each BA in a model region.

    Args:
        region_bas: List of BA IDs in the region
        hierarchy_df: DataFrame with columns ['ba', 'st']

    Returns:
        Dict mapping BA -> state abbreviation (lowercase), or raises if BA not found
    """
    ba_to_state = {}

    for ba in region_bas:
        row = hierarchy_df[hierarchy_df["ba"] == ba]
        if row.empty:
            raise ESRGenerationError(f"BA '{ba}' not found in hierarchy data")
        state = str(row.iloc[0]["st"]).lower()
        ba_to_state[ba] = state

    return ba_to_state


def get_states_in_region(region_bas, hierarchy_df):
    """
    Get unique states in a model region.

    Args:
        region_bas: List of BA IDs in the region
        hierarchy_df: DataFrame with columns ['ba', 'st']

    Returns:
        Set of state abbreviations (lowercase)
    """
    ba_to_state = extract_state_for_region(region_bas, hierarchy_df)
    return set(ba_to_state.values())


def can_states_trade(state1, state2, rectable_df):
    """
    Check if two states can trade REC/ESR credits based on rectable.csv.

    Args:
        state1, state2: State abbreviations (lowercase)
        rectable_df: DataFrame with states as both row and column indices

    Returns:
        Boolean: True if trading is allowed (value > 0), False otherwise
    """
    state1_upper = state1.upper()
    state2_upper = state2.upper()

    # Check if both states exist in rectable
    if state1_upper not in rectable_df.index or state2_upper not in rectable_df.columns:
        # If either state is not in rectable, assume no trading
        return False

    # Check trading value
    value = rectable_df.loc[state1_upper, state2_upper]
    return pd.notna(value) and float(value) > 0


def build_esr_zones(region_aggregations, hierarchy_df, rectable_df):
    """
    Infer ESR zones (groups of regions that can trade with each other).

    Two regions are in the same zone if all states in region A can trade with
    all states in region B (directly or transitively).

    Args:
        region_aggregations: Dict {region_name: [bas]}
        hierarchy_df: DataFrame with ['ba', 'st']
        rectable_df: Trading matrix DataFrame

    Returns:
        List of zones, where each zone is a list of region names

    Raises:
        ESRGenerationError if non-trading states are in the same region
    """
    # First, check for non-trading states within the same region
    for region_name, region_bas in region_aggregations.items():
        states = get_states_in_region(region_bas, hierarchy_df)
        states_list = sorted(list(states))

        # Check all pairs within this region
        for i, state1 in enumerate(states_list):
            for state2 in states_list[i + 1 :]:
                if not can_states_trade(state1, state2, rectable_df):
                    raise ESRGenerationError(
                        f"Cannot create ESR zones: BAs in states {state1.upper()} and "
                        f"{state2.upper()} (which do not allow trading) are grouped in region "
                        f"'{region_name}'. Separate them or modify grouping."
                    )

    # Build a graph where nodes are regions and edges indicate tradeable connections
    regions = list(region_aggregations.keys())
    trading_graph = {r: set() for r in regions}

    for i, region1 in enumerate(regions):
        for region2 in regions[i + 1 :]:
            states1 = get_states_in_region(region_aggregations[region1], hierarchy_df)
            states2 = get_states_in_region(region_aggregations[region2], hierarchy_df)

            # Regions can trade if at least one state pair can trade
            # (Use representative state from each region)
            can_trade = False
            for s1 in states1:
                for s2 in states2:
                    if can_states_trade(s1, s2, rectable_df):
                        can_trade = True
                        break
                if can_trade:
                    break

            if can_trade:
                trading_graph[region1].add(region2)
                trading_graph[region2].add(region1)

    # Find connected components (zones)
    visited = set()
    zones = []

    def dfs(region, zone):
        visited.add(region)
        zone.add(region)
        for neighbor in trading_graph[region]:
            if neighbor not in visited:
                dfs(neighbor, zone)

    for region in regions:
        if region not in visited:
            zone = set()
            dfs(region, zone)
            zones.append(sorted(list(zone)))

    return zones


def get_qualified_technologies(plants_df, new_resources, allowed_techs_df):
    """
    Determine which technologies qualify for RPS and CES policies.

    Args:
        plants_df: DataFrame with 'technology' column (existing generators)
        new_resources: List of [tech, detail, case, size] lists (new resources)
        allowed_techs_df: DataFrame with 'RPS' and 'CES' columns of tech names

    Returns:
        Tuple of (rps_techs_set, ces_techs_set): Sets of qualified technology names
    """
    # Get list of all tech keywords from allowed_techs
    rps_keywords = allowed_techs_df["RPS"].dropna().str.lower().tolist()
    ces_keywords = allowed_techs_df["CES"].dropna().str.lower().tolist()

    # Collect all techs from plants and new resources
    all_techs = set()

    if plants_df is not None and not plants_df.empty:
        all_techs.update(plants_df["technology"].dropna().astype(str).tolist())

    if new_resources:
        for res in new_resources:
            if isinstance(res, (list, tuple)) and len(res) > 0:
                all_techs.add(str(res[0]))

    # Substring match each tech to RPS/CES keywords
    rps_qualified = set()
    ces_qualified = set()

    for tech in all_techs:
        tech_lower = str(tech).lower()

        # Check RPS keywords
        for keyword in rps_keywords:
            if keyword in tech_lower:
                rps_qualified.add(tech)
                break

        # Check CES keywords
        for keyword in ces_keywords:
            if keyword in tech_lower:
                ces_qualified.add(tech)
                break

    return rps_qualified, ces_qualified


def aggregate_policy_for_region(
    region_bas, year, policy_type, hierarchy_df, pop_fraction_df, policy_df
):
    """
    Compute population-weighted policy requirement for a model region in a given year.

    Args:
        region_bas: List of BA IDs in the region
        year: Model year (int)
        policy_type: 'RPS' or 'CES'
        hierarchy_df: DataFrame with ['ba', 'st']
        pop_fraction_df: DataFrame with ['region', 'st', 'frac_of_state_pop']
        policy_df: DataFrame with ['year', 'st'] and policy columns

    Returns:
        Float: Aggregated policy requirement (0.0-1.0), or 0.0 if no data
    """
    ba_to_state = extract_state_for_region(region_bas, hierarchy_df)

    total_requirement = 0.0

    for ba in region_bas:
        state = ba_to_state[ba]

        # Find population fraction for this BA
        ba_pop = pop_fraction_df[
            (pop_fraction_df["region"] == ba) & (pop_fraction_df["st"] == state)
        ]

        if ba_pop.empty:
            frac = 1.0 / len(region_bas)  # Equal weight if not found
        else:
            frac = float(ba_pop.iloc[0]["frac_of_state_pop"])

        # Find policy for this state and year
        policy_row = policy_df[(policy_df["year"] == year) & (policy_df["st"] == state)]

        if policy_row.empty:
            policy_value = 0.0
        else:
            # Determine column name based on policy type
            if policy_type == "RPS":
                col_name = "rps_all"
            else:  # CES
                col_name = "Value"

            if col_name in policy_row.columns:
                policy_value = float(policy_row.iloc[0][col_name])
            else:
                policy_value = 0.0

        total_requirement += frac * policy_value

    return total_requirement


def generate_emission_policies_csv(
    region_aggregations,
    model_years,
    zones,
    hierarchy_df,
    pop_fraction_df,
    rps_df,
    ces_df,
    include_rps=True,
    include_ces=True,
    case_id="all",
):
    """
    Generate emission_policies.csv data.

    Args:
        region_aggregations: Dict {region_name: [bas]}
        model_years: List of years (ints)
        zones: List of zones, each zone is list of region names
        hierarchy_df: DataFrame with ['ba', 'st']
        pop_fraction_df: DataFrame with population fractions
        rps_df: RPS policy DataFrame with ['year', 'st', 'rps_all']
        ces_df: CES policy DataFrame with ['year', 'st', 'Value']
        include_rps: Bool to include RPS constraints
        include_ces: Bool to include CES constraints
        case_id: Case identifier string

    Returns:
        Tuple of (DataFrame with columns [case_id, year, region, ESR_1, ESR_2, ...],
                  Dict mapping ESR constraint names to their zone region lists)
    """
    # Handle years > 2050: forward-fill with 2050 value
    max_year_in_data_rps = rps_df["year"].max()
    max_year_in_data_ces = ces_df["year"].max()

    rows = []
    esr_constraint_num = 1
    esr_map = {}  # constraint_name -> list of regions

    # Create zones with their constraints
    zone_esr_map = {}  # zone_idx -> (rps_constraint, ces_constraint)

    for zone_idx, zone_regions in enumerate(zones):
        zone_rps = None
        zone_ces = None

        if include_rps:
            zone_rps = f"ESR_{esr_constraint_num}"
            esr_map[zone_rps] = zone_regions
            esr_constraint_num += 1

        if include_ces:
            zone_ces = f"ESR_{esr_constraint_num}"
            esr_map[zone_ces] = zone_regions
            esr_constraint_num += 1

        zone_esr_map[zone_idx] = (zone_rps, zone_ces)

    # Generate rows for each region and year
    for region_name, region_bas in region_aggregations.items():
        # Find which zone this region belongs to
        region_zone = None
        for zone_idx, zone_regions in enumerate(zones):
            if region_name in zone_regions:
                region_zone = zone_idx
                break

        if region_zone is None:
            # Region not in any zone (shouldn't happen)
            continue

        zone_rps, zone_ces = zone_esr_map[region_zone]

        for year in model_years:
            row = {"case_id": case_id, "year": int(year), "region": region_name}

            # Use 2050 value for years > 2050
            use_year_rps = min(year, max_year_in_data_rps)
            use_year_ces = min(year, max_year_in_data_ces)

            if zone_rps:
                rps_val = aggregate_policy_for_region(
                    region_bas,
                    use_year_rps,
                    "RPS",
                    hierarchy_df,
                    pop_fraction_df,
                    rps_df,
                )
                row[zone_rps] = round(float(rps_val), 3)

            if zone_ces:
                ces_val = aggregate_policy_for_region(
                    region_bas,
                    use_year_ces,
                    "CES",
                    hierarchy_df,
                    pop_fraction_df,
                    ces_df,
                )
                row[zone_ces] = round(float(ces_val), 3)

            rows.append(row)

    # Post-process: enforce CES >= RPS for each region/year
    df = pd.DataFrame(rows)

    for zone_idx, zone_regions in enumerate(zones):
        zone_rps, zone_ces = zone_esr_map[zone_idx]

        if zone_rps and zone_ces:
            for idx, row in df.iterrows():
                if row["region"] in zone_regions:
                    rps_val = df.at[idx, zone_rps]
                    ces_val = df.at[idx, zone_ces]
                    if ces_val < rps_val:
                        df.at[idx, zone_ces] = rps_val

    if not df.empty:
        df = df.sort_values(by=["region"], kind="stable").reset_index(drop=True)

    # Order columns
    columns = ["case_id", "year", "region"] + sorted(
        [c for c in df.columns if c.startswith("ESR_")]
    )
    df = df[columns]

    return df, esr_map
