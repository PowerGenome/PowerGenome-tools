"""Tests for fast interconnection resource group helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd


def _load_resource_groups_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "web"
        / "fast_interconnection"
        / "resource_groups.py"
    )
    spec = importlib.util.spec_from_file_location("resource_groups", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_assigned_df_solar_tech_and_columns():
    resource_groups = _load_resource_groups_module()

    assignments = pd.DataFrame(
        {
            "tech": ["solar", "onshorewind"],
            "CPA_ID": [1, 2],
            "metro_id": [101, 202],
            "cpa_mw": [50.5, 75.0],
            "cf": [0.25, 0.4],
            "path": [[101, 102], [203]],
            "lcoe": [40.0, 55.0],
            "interconnect_capex_mw": [1000.0, 1500.0],
            "total_interconnect_km": [12.5, 30.0],
            "model_region": ["Region1", "Region2"],
        }
    )
    metro_region_map = pd.DataFrame(
        {"metro_id": [101, 202], "base_region": ["REG1", "REG2"]}
    )

    assigned_df = resource_groups.build_assigned_df(
        assignments=assignments,
        tech="solar",
        metro_region_map=metro_region_map,
        msa_name_map=None,
    )

    assert set(assigned_df.columns) == {
        "cpa_id",
        "msa_id",
        "region",
        "cpa_mw",
        "cf",
        "path",
        "path_len",
        "lcoe",
        "interconnect_capex_mw",
        "total_interconnect_km",
    }
    assert assigned_df.loc[0, "region"] == "Region1"
    assert assigned_df.loc[0, "path_len"] == 2


def test_build_assigned_df_msa_name_map_optional():
    resource_groups = _load_resource_groups_module()

    assignments = pd.DataFrame(
        {
            "tech": ["solar"],
            "CPA_ID": [10],
            "metro_id": [303],
            "cpa_mw": [22.0],
            "cf": [0.3],
            "path": [[304]],
            "lcoe": [60.0],
            "interconnect_capex_mw": [900.0],
            "total_interconnect_km": [5.0],
            "model_region": ["Region3"],
        }
    )
    metro_region_map = pd.DataFrame({"metro_id": [303], "base_region": ["REG3"]})
    msa_name_map = pd.Series({"303": "Metro Three"})

    assigned_df = resource_groups.build_assigned_df(
        assignments=assignments,
        tech="solar",
        metro_region_map=metro_region_map,
        msa_name_map=msa_name_map,
    )

    assert "msa_name" in assigned_df.columns
    assert assigned_df.loc[0, "msa_name"] == "Metro Three"
    assert assigned_df.loc[0, "region"] == "Region3"


def test_build_resource_group_json_defaults():
    resource_groups = _load_resource_groups_module()

    result = resource_groups.build_resource_group_json(
        resource="solar",
        lcoe_filename="lcoe_output.csv",
    )

    assert result == {
        "technology": "utilitypv",
        "metadata": "lcoe_output.csv",
        "profiles": "solar_rev_profiles_20240801_tidy.parquet",
        "site_map": "solar_site_mapping_20240801.parquet",
    }


def test_build_resource_group_json_profile_override_without_site_map():
    resource_groups = _load_resource_groups_module()

    result = resource_groups.build_resource_group_json(
        resource="solar",
        lcoe_filename="lcoe_output.csv",
        profile_paths={"solar": {"profiles": "custom_profiles.parquet"}},
    )

    assert result == {
        "technology": "utilitypv",
        "metadata": "lcoe_output.csv",
        "profiles": "custom_profiles.parquet",
    }


def test_build_assigned_df_without_model_region():
    """Test that region column is set to empty string when model_region is missing."""
    resource_groups = _load_resource_groups_module()

    assignments = pd.DataFrame(
        {
            "tech": ["solar"],
            "CPA_ID": [1],
            "metro_id": [101],
            "cpa_mw": [50.5],
            "cf": [0.25],
            "path": [[101, 102]],
            "lcoe": [40.0],
            "interconnect_capex_mw": [1000.0],
            "total_interconnect_km": [12.5],
            # Note: no model_region column
        }
    )
    metro_region_map = pd.DataFrame(
        {"metro_id": [101], "base_region": ["REG1"]}
    )

    assigned_df = resource_groups.build_assigned_df(
        assignments=assignments,
        tech="solar",
        metro_region_map=metro_region_map,
        msa_name_map=None,
    )

    assert "region" in assigned_df.columns
    assert assigned_df.loc[0, "region"] == ""
