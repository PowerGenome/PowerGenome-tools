"""Helper utilities for building resource group outputs in the web app."""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

ATB_TECH_MAP = {"onshorewind": "landbasedwind", "solar": "utilitypv"}
DEFAULT_PROFILE_PATHS = {
    "onshorewind": {
        "profiles": "onshorewind_rev_profiles_20240801_tidy.parquet",
        "site_map": "onshorewind_site_mapping_20240801.parquet",
    },
    "solar": {
        "profiles": "solar_rev_profiles_20240801_tidy.parquet",
        "site_map": "solar_site_mapping_20240801.parquet",
    },
}


def build_assigned_df(
    assignments: pd.DataFrame,
    tech: str,
    metro_region_map: pd.DataFrame,
    msa_name_map: Optional[pd.Series] = None,
) -> pd.DataFrame:
    """Build the output dataframe for a single technology."""
    tech_assigns = assignments[assignments["tech"] == tech].copy()
    if tech_assigns.empty:
        return pd.DataFrame()

    tech_name = "photovoltaic" if tech == "solar" else tech

    assigned_df = pd.DataFrame(
        {
            "cpa_id": tech_assigns["CPA_ID"].astype("int32"),
            "msa_id": tech_assigns["metro_id"].astype(str),
            "region": tech_assigns.get("model_region", ""),
            "cpa_mw": tech_assigns["cpa_mw"].astype("float32"),
            "cf": tech_assigns["cf"].astype("float32"),
            "path": tech_assigns["path"].apply(
                lambda x: (
                    [int(i) for i in x] if isinstance(x, (list, np.ndarray)) else x
                )
            ),
            "path_len": tech_assigns["path"]
            .apply(lambda p: len(p) if hasattr(p, "__len__") else np.nan)
            .astype("float32"),
            "lcoe": tech_assigns["lcoe"].astype("float32"),
            "interconnect_capex_mw": tech_assigns["interconnect_capex_mw"].astype(
                "float32"
            ),
            "total_interconnect_km": tech_assigns["total_interconnect_km"].astype(
                "float32"
            ),
        }
    )

    # Use model_region from input if available, otherwise fallback (or leave empty)
    if "model_region" in tech_assigns.columns:
        assigned_df["region"] = tech_assigns["model_region"]
    elif "region" not in assigned_df.columns:  # Fail-safe
        assigned_df["region"] = ""

    if msa_name_map is not None:
        assigned_df["msa_name"] = assigned_df["msa_id"].map(msa_name_map)

    return assigned_df


def build_resource_group_json(
    resource: str,
    lcoe_filename: str,
    profile_paths: Optional[Dict[str, Dict[str, str]]] = None,
) -> Optional[Dict[str, str | list]]:
    """Build resource group JSON metadata for a resource."""
    if resource not in ATB_TECH_MAP:
        return None

    paths = (profile_paths or {}).get(resource)
    if not paths:
        paths = DEFAULT_PROFILE_PATHS.get(resource)
    if not paths:
        return None

    rg_dict: Dict[str, str | list] = {
        "technology": ATB_TECH_MAP[resource],
        "metadata": lcoe_filename,
        "profiles": paths["profiles"],
    }
    site_map = paths.get("site_map")
    if site_map:
        rg_dict["site_map"] = site_map

    return rg_dict
