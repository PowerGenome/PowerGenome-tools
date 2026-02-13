"""
Fast CPA Assignment using pre-computed candidate tables.

This script:
1. Loads CPA→Metro candidate connections
2. Filters by regional aggregation (from settings YAML)
3. Assigns CPAs to metros using greedy LCOE-based allocation
4. Respects metro saturation limits (by resource type)
5. Outputs results in the same format as the original algorithm
"""

import asyncio
import re
from pathlib import Path
from typing import Dict, Optional, Set

import numpy as np
import pandas as pd
import yaml

try:
    from tqdm import tqdm
except Exception:

    class tqdm:
        def __init__(self, iterable=None, update_callback=None, total=None, **kwargs):
            self.iterable = iterable if iterable is not None else []
            self.update_callback = update_callback
            self.total = total or (
                len(self.iterable) if hasattr(self.iterable, "__len__") else 0
            )
            self.n = 0

        def __iter__(self):
            for item in self.iterable:
                yield item
                self.update(1)

        def update(self, n=1):
            self.n += n
            if self.update_callback:
                self.update_callback(self.n, self.total)

        def close(self):
            pass


def load_settings(path: Path) -> dict:
    """Load YAML settings file."""
    try:
        from ruamel.yaml import YAML

        yaml_loader = YAML(typ="safe")
        with open(path, "r") as f:
            return yaml_loader.load(f)
    except Exception:
        with open(path, "r") as f:
            return yaml.safe_load(f)


def build_region_to_metros(
    settings: dict,
    metro_region_map: pd.DataFrame,
    substation_metro_region: pd.DataFrame,
) -> Dict[str, Set[str]]:
    """
    Build mapping from model regions to metro IDs.

    Uses the region_aggregations from settings to group base regions,
    then maps to metros via the substation data.
    """
    region_aggregations = settings.get("region_aggregations", {})
    model_regions = settings.get("model_regions", [])

    # Build base_region → model_region mapping
    base_to_model = {}
    for model_region, base_regions in region_aggregations.items():
        if base_regions:
            for base_region in base_regions:
                base_to_model[base_region] = model_region

    # Map metros to model regions via their base region
    metro_region_map = metro_region_map.copy()
    metro_region_map["model_region"] = metro_region_map["base_region"].map(
        base_to_model
    )

    # Build model_region → set of metro_ids
    region_to_metros = {}
    for model_region in model_regions:
        metros_in_region = (
            metro_region_map[metro_region_map["model_region"] == model_region][
                "metro_id"
            ]
            .astype(str)
            .tolist()
        )
        region_to_metros[model_region] = set(metros_in_region)

    return region_to_metros


def get_cpa_home_region(
    cpa_id: int,
    hub_substation: int,
    substation_metro_region: pd.DataFrame,
    base_to_model: Dict[str, str],
) -> Optional[str]:
    """Get the model region a CPA belongs to based on its hub substation."""
    sub_data = substation_metro_region[
        substation_metro_region["substation_id"] == hub_substation
    ]
    if sub_data.empty:
        return None
    base_region = sub_data.iloc[0]["base_region"]
    return base_to_model.get(base_region)


def identify_largest_metro_per_region(
    region_to_metros: Dict[str, Set[str]],
    saturation: pd.DataFrame,
) -> Set[str]:
    """
    Identify the largest metro in each region (by population).

    These become infinite sinks if no metro in the region exceeds
    the population threshold.
    """
    largest_metros = set()
    saturation = saturation.copy()
    saturation["metro_id"] = saturation["metro_id"].astype(str)

    for region, metros in region_to_metros.items():
        region_metros = saturation[saturation["metro_id"].isin(metros)]
        if region_metros.empty:
            continue

        # Check if any metro exceeds population threshold
        has_large_metro = region_metros["is_infinite_sink"].any()

        if not has_large_metro:
            # Make largest metro in region an infinite sink
            largest_idx = region_metros["population"].idxmax()
            largest_metro = region_metros.loc[largest_idx, "metro_id"]
            largest_metros.add(largest_metro)

    return largest_metros


async def fast_assign_cpas(
    candidates: pd.DataFrame,
    saturation: pd.DataFrame,
    settings: dict,
    metro_region_map: pd.DataFrame,
    substation_metro_region: pd.DataFrame,
    allowed_cross_region: Optional[pd.DataFrame] = None,
    strategy: str = "greedy",
    lcoe_penalty_factor: float = 10.0,
    metro_observed_stats: Optional[pd.DataFrame] = None,
    metro_lcoe_cutoff: Optional[pd.DataFrame] = None,
    show_progress: bool = True,
    progress_callback=None,
) -> pd.DataFrame:
    """
    Assign CPAs to metros using various allocation strategies.


    Strategies:
    - "greedy": Simple greedy by LCOE (baseline)
    - "dynamic_lcoe": Apply LCOE penalty as metros fill up
    - "lcoe_cutoff": Filter candidates by observed max LCOE per metro
    - "observed_saturation": Use observed saturation from training data

    Parameters
    ----------
    candidates : pd.DataFrame
        Pre-computed CPA→Metro candidate connections
    saturation : pd.DataFrame
        Metro saturation thresholds by resource type
    settings : dict
        Settings YAML with region_aggregations and model_regions
    metro_region_map : pd.DataFrame
        Mapping from metro_id to base_region
    substation_metro_region : pd.DataFrame
        Mapping from substation_id to metro_id and region
    allowed_cross_region : pd.DataFrame, optional
        Allowed cross-region connections (observed in training cases)
    strategy : str
        Assignment strategy: "greedy", "dynamic_lcoe", "lcoe_cutoff", "observed_saturation"
    lcoe_penalty_factor : float
        For dynamic_lcoe: penalty multiplier as fill_ratio increases
    metro_observed_stats : pd.DataFrame, optional
        Observed metro stats for saturation-based strategies
    metro_lcoe_cutoff : pd.DataFrame, optional
        Max observed LCOE per metro per tech
    show_progress : bool
        Whether to show progress bars (default True)

    Returns
    -------
    pd.DataFrame
        Assigned CPAs with metro_id, path, cost, etc.
    """
    if show_progress:
        print(f"Starting fast CPA assignment (strategy={strategy})...")

    # Build region mappings
    region_to_metros = build_region_to_metros(
        settings, metro_region_map, substation_metro_region
    )

    # Build base_region → model_region mapping
    region_aggregations = settings.get("region_aggregations", {})
    base_to_model = {}
    for model_region, base_regions in region_aggregations.items():
        if base_regions:
            for base_region in base_regions:
                base_to_model[base_region] = model_region

    # Identify infinite sink metros
    saturation = saturation.copy()
    saturation["metro_id"] = saturation["metro_id"].astype(str)

    largest_per_region = identify_largest_metro_per_region(region_to_metros, saturation)
    saturation["is_infinite_sink"] = saturation["is_infinite_sink"] | saturation[
        "metro_id"
    ].isin(largest_per_region)

    infinite_sinks = set(saturation[saturation["is_infinite_sink"]]["metro_id"])
    if show_progress:
        print(f"Infinite sink metros: {len(infinite_sinks)}")

    # Identify pseudo-metros (pattern: p followed by digits, e.g., p29)
    pseudo_metro_pattern = re.compile(r"^p\d+$")
    pseudo_metros = set(
        m for m in saturation["metro_id"] if pseudo_metro_pattern.match(str(m))
    )
    if show_progress:
        print(f"Pseudo-metros identified: {len(pseudo_metros)}")

    # Identify regions that have at least one real metro (non-pseudo)
    regions_with_real_metros = set()
    for region, metros in region_to_metros.items():
        real_metros_in_region = [m for m in metros if m not in pseudo_metros]
        if real_metros_in_region:
            regions_with_real_metros.add(region)

    # Build pseudo-metro → region mapping
    metro_to_region = {}
    for region, metros in region_to_metros.items():
        for m in metros:
            metro_to_region[m] = region

    # Pseudo-metros in regions with real metros get capped at 100 MW
    PSEUDO_METRO_CAP_MW = 100.0
    pseudo_metros_capped = set()
    for pm in pseudo_metros:
        pm_region = metro_to_region.get(pm)
        if pm_region and pm_region in regions_with_real_metros:
            pseudo_metros_capped.add(pm)

    if show_progress:
        print(
            f"Pseudo-metros capped to {PSEUDO_METRO_CAP_MW} MW: {len(pseudo_metros_capped)}"
        )

    # Initialize remaining capacity per metro per tech
    remaining_capacity = {}
    initial_capacity = {}  # Track initial for fill ratio calculation

    for _, row in saturation.iterrows():
        metro = row["metro_id"]
        remaining_capacity[metro] = {
            "solar": row.get("solar_saturation_mw", float("inf")),
            "onshorewind": row.get("onshorewind_saturation_mw", float("inf")),
        }

        # Apply observed saturation if using that strategy
        if strategy == "observed_saturation" and metro_observed_stats is not None:
            obs = metro_observed_stats[metro_observed_stats["metro_id"] == metro]
            for tech in ["solar", "onshorewind"]:
                tech_obs = obs[obs["tech"] == tech]
                if not tech_obs.empty:
                    # Use max observed + 20% buffer as capacity limit
                    max_obs = tech_obs.iloc[0]["max_assigned_mw"]
                    remaining_capacity[metro][tech] = min(
                        remaining_capacity[metro][tech], max_obs * 1.2
                    )

        if metro in infinite_sinks:
            for tech in remaining_capacity[metro]:
                remaining_capacity[metro][tech] = float("inf")
        # Cap pseudo-metros in regions with real metros
        if metro in pseudo_metros_capped:
            for tech in remaining_capacity[metro]:
                remaining_capacity[metro][tech] = min(
                    remaining_capacity[metro][tech], PSEUDO_METRO_CAP_MW
                )

        # Store initial capacity
        initial_capacity[metro] = remaining_capacity[metro].copy()

    # Add CPA's hub region to candidates
    candidates = candidates.copy()
    candidates["metro_id"] = candidates["metro_id"].astype(str)
    # Drop zero-MW CPAs (noise)
    candidates = candidates[candidates["cpa_mw"] > 0].copy()

    # Map hub substation to base region
    sub_to_base_region = substation_metro_region.set_index("substation_id")[
        "base_region"
    ].to_dict()
    candidates["hub_base_region"] = candidates["hub_substation"].map(sub_to_base_region)
    candidates["cpa_model_region"] = candidates["hub_base_region"].map(base_to_model)

    # Map metro to model region
    metro_to_model = (
        metro_region_map.set_index("metro_id")["base_region"]
        .map(base_to_model)
        .to_dict()
    )
    candidates["metro_model_region"] = candidates["metro_id"].map(metro_to_model)

    # Filter to valid connections:
    # 1. Metro is in same model region as CPA, OR
    # 2. Connection was observed in allowed_cross_region

    # First, mark in-region connections
    candidates["is_in_region"] = (
        candidates["cpa_model_region"] == candidates["metro_model_region"]
    )

    # Mark allowed cross-region connections
    if allowed_cross_region is not None and not allowed_cross_region.empty:
        allowed_cross_region = allowed_cross_region.copy()
        allowed_cross_region["metro_id"] = allowed_cross_region["metro_id"].astype(str)
        allowed_keys = set(
            zip(
                allowed_cross_region["CPA_ID"],
                allowed_cross_region["tech"],
                allowed_cross_region["metro_id"],
            )
        )
        candidates["is_allowed_cross"] = candidates.apply(
            lambda r: (r["CPA_ID"], r["tech"], r["metro_id"]) in allowed_keys, axis=1
        )
    else:
        candidates["is_allowed_cross"] = False

    # Keep valid connections
    valid_mask = candidates["is_in_region"] | candidates["is_allowed_cross"]
    valid_candidates = candidates[valid_mask].copy()

    if show_progress:
        print(f"Total candidates: {len(candidates):,}")
        print(f"Valid candidates after regional filter: {len(valid_candidates):,}")

    # Apply LCOE cutoff filter if using that strategy
    if strategy == "lcoe_cutoff" and metro_lcoe_cutoff is not None:
        metro_lcoe_cutoff = metro_lcoe_cutoff.copy()
        metro_lcoe_cutoff["metro_id"] = metro_lcoe_cutoff["metro_id"].astype(str)
        # Merge cutoff with candidates
        valid_candidates = valid_candidates.merge(
            metro_lcoe_cutoff[["metro_id", "tech", "max_observed_lcoe"]],
            on=["metro_id", "tech"],
            how="left",
        )
        # Filter: keep only candidates with LCOE <= max_observed + 10% buffer
        valid_candidates["max_observed_lcoe"] = valid_candidates[
            "max_observed_lcoe"
        ].fillna(float("inf"))
        pre_filter = len(valid_candidates)
        valid_candidates = valid_candidates[
            valid_candidates["lcoe"] <= valid_candidates["max_observed_lcoe"] * 1.1
        ].copy()
        if show_progress:
            print(
                f"Valid candidates after LCOE cutoff filter: {len(valid_candidates):,} (removed {pre_filter - len(valid_candidates):,})"
            )

    # For dynamic_lcoe strategy, compute effective LCOE with fill penalty
    if strategy == "dynamic_lcoe":
        # We'll re-sort after each assignment, so just prepare the base LCOE
        valid_candidates["base_lcoe"] = valid_candidates["lcoe"].copy()
        valid_candidates["effective_lcoe"] = valid_candidates["lcoe"].copy()

    # Sort by LCOE (or effective_lcoe for dynamic strategy)
    sort_col = "effective_lcoe" if strategy == "dynamic_lcoe" else "lcoe"
    valid_candidates = valid_candidates.sort_values(sort_col).reset_index(drop=True)

    # Track assigned CPAs
    assigned_cpas = set()
    assignments = []

    # Support callback in dummy tqdm
    tqdm_kwargs = {}
    if "update_callback" in tqdm.__init__.__code__.co_varnames:
        tqdm_kwargs["update_callback"] = progress_callback

    if strategy == "dynamic_lcoe":

        # OPTIMIZED DYNAMIC ASSIGNMENT
        # Process each tech separately to allow vectorized operations

        # Pre-calculate initial capacities as series for fast lookup
        init_caps = []
        for m, caps in initial_capacity.items():
            for t, cap in caps.items():
                init_caps.append({"metro_id": m, "tech": t, "cap": cap})
        init_cap_df = pd.DataFrame(init_caps)

        # Track assigned MW (mutable)
        metro_assigned_mw = {
            m: {t: 0.0 for t in ["solar", "onshorewind"]} for m in remaining_capacity
        }

        # Process each tech independently
        for tech in ["solar", "onshorewind"]:
            # Filter candidates for this tech
            tech_cands = valid_candidates[valid_candidates["tech"] == tech].copy()
            if tech_cands.empty:
                continue

            # Get capacities for this tech
            # Safe handling if tech not in init_cap_df
            if not init_cap_df.empty and tech in init_cap_df["tech"].values:
                tech_init_caps = init_cap_df[init_cap_df["tech"] == tech].set_index(
                    "metro_id"
                )["cap"]
            else:
                tech_init_caps = pd.Series(dtype=float)

            remaining = tech_cands.copy()
            # Ensure base_lcoe exists (should be set above)
            if "base_lcoe" not in remaining.columns:
                remaining["base_lcoe"] = remaining["lcoe"]

            batch_size = 5000  # Smaller batch = more accurate penalty

            pbar = tqdm(
                total=len(remaining),
                desc=f"Assigning {tech} (dynamic)",
                disable=not show_progress,
                **tqdm_kwargs,
            )

            try:
                while len(remaining) > 0:
                    # 1. VECTORIZED PENALTY CALCULATION
                    # Get current assigned MW for this tech
                    current_assigned = pd.Series(
                        {
                            m: metro_assigned_mw.get(m, {}).get(tech, 0.0)
                            for m in metro_assigned_mw
                        }
                    )

                    # Align with capacities
                    # Use a dataframe align to handle indexes safely
                    status = pd.DataFrame(
                        {"assigned": current_assigned, "initial": tech_init_caps}
                    )
                    status["initial"] = (
                        status["initial"].replace(0, float("inf")).fillna(float("inf"))
                    )
                    status["fill_ratio"] = (
                        (status["assigned"] / status["initial"])
                        .clip(upper=1.0)
                        .fillna(0.0)
                    )

                    # Calculate penalty: 1 + factor * ratio
                    status["penalty"] = 1.0 + lcoe_penalty_factor * status["fill_ratio"]

                    # 2. UPDATE CANDIDATES
                    # Map penalties to candidates
                    # Metros not in status (no capacity limit) get penalty 1.0
                    penalties = status["penalty"]
                    remaining["penalty"] = (
                        remaining["metro_id"].map(penalties).fillna(1.0)
                    )
                    remaining["effective_lcoe"] = (
                        remaining["base_lcoe"] * remaining["penalty"]
                    )

                    # 3. SORT
                    remaining = remaining.sort_values("effective_lcoe")

                    # 4. ASSIGN BATCH
                    assigned_this_batch = 0
                    rows_to_drop = []

                    # Use iterrows on a slice to speed up loop
                    # Take 3x batch size to be safe
                    candidate_slice = remaining.iloc[: batch_size * 3]

                    for idx, row in candidate_slice.iterrows():
                        if assigned_this_batch >= batch_size:
                            break

                        cpa_id = row["CPA_ID"]
                        metro_id = row["metro_id"]
                        cpa_mw = row["cpa_mw"]

                        cpa_tech_key = (cpa_id, tech)
                        if cpa_tech_key in assigned_cpas:
                            rows_to_drop.append(idx)
                            continue

                        metro_cap = remaining_capacity.get(metro_id, {}).get(tech, 0)
                        if metro_cap <= 0 and metro_id not in infinite_sinks:
                            rows_to_drop.append(idx)
                            continue

                        # Assign
                        assigned_cpas.add(cpa_tech_key)
                        assignments.append(row.to_dict())
                        rows_to_drop.append(idx)
                        assigned_this_batch += 1

                        # Update capacity tracking
                        if (
                            metro_id in remaining_capacity
                            and metro_id not in infinite_sinks
                        ):
                            remaining_capacity[metro_id][tech] = max(
                                0, metro_cap - cpa_mw
                            )
                            metro_assigned_mw[metro_id][tech] += cpa_mw

                    # Remove processed rows from remaining
                    remaining = remaining.drop(rows_to_drop)
                    pbar.update(len(rows_to_drop))
                    await asyncio.sleep(0)  # Yield to event loop

                    if assigned_this_batch == 0:
                        # No assignments were made in this batch. This typically means
                        # all metros are saturated for this tech or no valid candidates
                        # remain under the current constraints. Log this so users can
                        # distinguish normal completion from a misconfiguration.
                        try:
                            pbar.write(
                                f"Stopping dynamic_lcoe assignment for tech '{tech}': "
                                "no additional CPAs could be assigned in this batch."
                            )
                        except Exception:
                            # Fallback if tqdm progress bar does not support write()
                            print(
                                f"[fast_assign] Stopping dynamic_lcoe assignment for tech '{tech}' "
                                "because no additional CPAs could be assigned in this batch."
                            )
                        break
            finally:
                pbar.close()
    else:
        # Standard greedy assignment
        row_counter = 0
        for _, row in tqdm(
            valid_candidates.iterrows(),
            total=len(valid_candidates),
            desc="Assigning CPAs",
            disable=not show_progress,
            **tqdm_kwargs,
        ):
            row_counter += 1
            if row_counter % 100 == 0:
                await asyncio.sleep(0)

            cpa_id = row["CPA_ID"]
            tech = row["tech"]
            metro_id = row["metro_id"]
            cpa_mw = row["cpa_mw"]

            # Skip if CPA already assigned (for this tech)
            cpa_tech_key = (cpa_id, tech)
            if cpa_tech_key in assigned_cpas:
                continue

            # Check capacity
            metro_cap = remaining_capacity.get(metro_id, {}).get(tech, 0)
            if metro_cap <= 0 and metro_id not in infinite_sinks:
                continue

            # Assign
            assigned_cpas.add(cpa_tech_key)
            assignments.append(row.to_dict())

            # Decrement capacity
            if metro_id in remaining_capacity and metro_id not in infinite_sinks:
                remaining_capacity[metro_id][tech] = max(0, metro_cap - cpa_mw)

    result = pd.DataFrame(assignments)
    if show_progress:
        print(f"Assigned CPAs: {len(result):,}")

    return result


def validate_against_case(
    assignments: pd.DataFrame,
    case_path: Path,
    tech: str,
) -> Dict[str, float]:
    """
    Validate assignments against a known case.

    Returns metrics:
    - destination_accuracy: % of CPAs assigned to same metro
    - cost_mape: Mean Absolute Percentage Error on interconnect cost
    """
    region_name = case_path.name
    truth_file = case_path / "output" / "cpas" / f"{tech}_lcoe_{region_name}.parquet"

    if not truth_file.exists():
        return {"error": "Truth file not found"}

    truth = pd.read_parquet(truth_file)
    truth["metro_id"] = truth["metro_id"].astype(str)

    # Filter assignments to this tech
    pred = assignments[assignments["tech"] == tech].copy()

    # Merge on CPA_ID
    merged = pred.merge(
        truth[["CPA_ID", "metro_id", "interconnect_capex_mw"]],
        on="CPA_ID",
        suffixes=("_pred", "_truth"),
    )

    if merged.empty:
        return {"error": "No matching CPAs"}

    # Destination accuracy
    dest_match = (merged["metro_id_pred"] == merged["metro_id_truth"]).mean()

    # Cost MAPE
    cost_error = np.abs(
        merged["interconnect_capex_mw_pred"] - merged["interconnect_capex_mw_truth"]
    ) / merged["interconnect_capex_mw_truth"].clip(lower=1)
    cost_mape = cost_error.mean()

    return {
        "destination_accuracy": dest_match,
        "cost_mape": cost_mape,
        "n_cpas": len(merged),
    }


def main():
    """Run fast assignment on a test case."""
    base_path = Path(__file__).parent.parent
    data_path = base_path / "fast_interconnection" / "data"

    # Load pre-computed tables
    print("Loading pre-computed tables...")
    candidates = pd.read_parquet(data_path / "cpa_metro_candidates.parquet")
    saturation = pd.read_parquet(data_path / "metro_saturation.parquet")
    metro_region_map = pd.read_parquet(data_path / "metro_region_map.parquet")
    substation_metro_region = pd.read_parquet(
        data_path / "substation_metro_region.parquet"
    )

    cross_region_file = data_path / "cross_region_connections.parquet"
    if cross_region_file.exists():
        cross_region = pd.read_parquet(cross_region_file)
    else:
        cross_region = None

    # Test on a specific case
    import sys

    test_case = sys.argv[1] if len(sys.argv) > 1 else "transgrp_50_spectral"
    settings = load_settings(base_path / test_case / "settings.yml")

    print(f"\nTesting on: {test_case}")
    print(f"Model regions: {len(settings.get('model_regions', []))}")

    # Load observed stats for advanced strategies
    metro_observed_stats = None
    metro_lcoe_cutoff = None
    obs_stats_file = data_path / "metro_observed_stats.parquet"
    lcoe_cutoff_file = data_path / "metro_lcoe_cutoff.parquet"

    if obs_stats_file.exists():
        metro_observed_stats = pd.read_parquet(obs_stats_file)
    if lcoe_cutoff_file.exists():
        metro_lcoe_cutoff = pd.read_parquet(lcoe_cutoff_file)

    # Test all strategies
    strategies = ["greedy", "dynamic_lcoe", "lcoe_cutoff", "observed_saturation"]

    all_results = {}

    for strategy in strategies:
        print(f"\n{'='*60}")
        print(f"Strategy: {strategy}")
        print("=" * 60)

        # Run assignment
        assignments = fast_assign_cpas(
            candidates=candidates,
            saturation=saturation,
            settings=settings,
            metro_region_map=metro_region_map,
            substation_metro_region=substation_metro_region,
            allowed_cross_region=cross_region,
            strategy=strategy,
            lcoe_penalty_factor=10.0,
            metro_observed_stats=metro_observed_stats,
            metro_lcoe_cutoff=metro_lcoe_cutoff,
        )

        # Validate
        print("\nValidation results:")
        strategy_results = {}
        for tech in ["solar", "onshorewind"]:
            metrics = validate_against_case(
                assignments,
                base_path / test_case,
                tech,
            )
            print(f"  {tech}: {metrics}")
            strategy_results[tech] = metrics

        all_results[strategy] = strategy_results

        # Save results
        output_path = data_path / "test_results"
        output_path.mkdir(exist_ok=True)
        assignments.to_parquet(
            output_path / f"assignments_{test_case}_{strategy}.parquet", index=False
        )

    # Print summary comparison
    print("\n" + "=" * 80)
    print("SUMMARY COMPARISON")
    print("=" * 80)
    print(
        f"{'Strategy':<25} {'Solar Acc':<12} {'Solar MAPE':<12} {'Wind Acc':<12} {'Wind MAPE':<12}"
    )
    print("-" * 80)
    for strategy in strategies:
        solar = all_results[strategy].get("solar", {})
        wind = all_results[strategy].get("onshorewind", {})
        solar_acc = solar.get("destination_accuracy", 0) * 100
        solar_mape = solar.get("cost_mape", 0) * 100
        wind_acc = wind.get("destination_accuracy", 0) * 100
        wind_mape = wind.get("cost_mape", 0) * 100
        print(
            f"{strategy:<25} {solar_acc:>10.1f}% {solar_mape:>10.1f}% {wind_acc:>10.1f}% {wind_mape:>10.1f}%"
        )


if __name__ == "__main__":
    main()
