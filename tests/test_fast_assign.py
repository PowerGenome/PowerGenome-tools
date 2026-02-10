"""Unit tests for fast_assign.py module.

This module tests the core CPA assignment algorithm and helper functions
used to assign Candidate Power Areas (CPAs) to metro areas based on LCOE
and various assignment strategies.
"""

from __future__ import annotations

import asyncio
import importlib.util
import tempfile
from pathlib import Path
from typing import Dict, Set

import numpy as np
import pandas as pd
import pytest
import yaml


def _load_fast_assign_module():
    """Load the fast_assign module dynamically."""
    module_path = (
        Path(__file__).resolve().parents[1]
        / "web"
        / "fast_interconnection"
        / "fast_assign.py"
    )
    spec = importlib.util.spec_from_file_location("fast_assign", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# Fixtures for test data
@pytest.fixture
def sample_settings():
    """Sample settings YAML structure."""
    return {
        "model_regions": ["Region1", "Region2"],
        "region_aggregations": {
            "Region1": ["BA1", "BA2"],
            "Region2": ["BA3", "BA4"],
        },
    }


@pytest.fixture
def sample_metro_region_map():
    """Sample metro to base region mapping."""
    return pd.DataFrame(
        {
            "metro_id": ["101", "102", "201", "202"],
            "base_region": ["BA1", "BA2", "BA3", "BA4"],
        }
    )


@pytest.fixture
def sample_substation_metro_region():
    """Sample substation to metro/region mapping."""
    return pd.DataFrame(
        {
            "substation_id": [1001, 1002, 2001, 2002],
            "metro_id": ["101", "102", "201", "202"],
            "base_region": ["BA1", "BA2", "BA3", "BA4"],
        }
    )


@pytest.fixture
def sample_saturation():
    """Sample metro saturation data."""
    return pd.DataFrame(
        {
            "metro_id": ["101", "102", "201", "202"],
            "solar_saturation_mw": [1000.0, 500.0, 2000.0, 800.0],
            "onshorewind_saturation_mw": [1500.0, 600.0, 2500.0, 900.0],
            "population": [500000, 200000, 1000000, 300000],
            "is_infinite_sink": [True, False, False, False],
        }
    )


@pytest.fixture
def sample_candidates():
    """Sample CPA candidate connections."""
    return pd.DataFrame(
        {
            "CPA_ID": [1, 1, 2, 2, 3, 3],
            "tech": ["solar", "solar", "onshorewind", "onshorewind", "solar", "solar"],
            "metro_id": ["101", "102", "101", "102", "201", "202"],
            "cpa_mw": [100.0, 100.0, 150.0, 150.0, 80.0, 80.0],
            "cf": [0.25, 0.24, 0.4, 0.38, 0.26, 0.25],
            "lcoe": [40.0, 42.0, 50.0, 52.0, 38.0, 40.0],
            "interconnect_capex_mw": [1000.0, 1200.0, 1500.0, 1700.0, 900.0, 1100.0],
            "total_interconnect_km": [10.0, 12.0, 15.0, 18.0, 8.0, 10.0],
            "path": [
                [101],
                [101, 102],
                [201],
                [201, 202],
                [201],
                [201, 202],
            ],
            "hub_substation": [1001, 1001, 2001, 2001, 2001, 2001],
        }
    )


# Test load_settings
def test_load_settings():
    """Test loading YAML settings file."""
    fast_assign = _load_fast_assign_module()

    # Create a temporary YAML file
    settings_dict = {
        "model_regions": ["TestRegion1", "TestRegion2"],
        "region_aggregations": {
            "TestRegion1": ["BA1", "BA2"],
            "TestRegion2": ["BA3"],
        },
    }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yml", delete=False
    ) as temp_file:
        yaml.dump(settings_dict, temp_file)
        temp_path = Path(temp_file.name)

    try:
        loaded_settings = fast_assign.load_settings(temp_path)
        assert loaded_settings == settings_dict
        assert "model_regions" in loaded_settings
        assert len(loaded_settings["model_regions"]) == 2
    finally:
        temp_path.unlink()


# Test build_region_to_metros
def test_build_region_to_metros_basic(
    sample_settings, sample_metro_region_map, sample_substation_metro_region
):
    """Test building region to metros mapping."""
    fast_assign = _load_fast_assign_module()

    result = fast_assign.build_region_to_metros(
        sample_settings, sample_metro_region_map, sample_substation_metro_region
    )

    assert isinstance(result, dict)
    assert "Region1" in result
    assert "Region2" in result
    assert isinstance(result["Region1"], set)
    assert isinstance(result["Region2"], set)
    assert "101" in result["Region1"]
    assert "102" in result["Region1"]
    assert "201" in result["Region2"]
    assert "202" in result["Region2"]


def test_build_region_to_metros_empty_aggregations():
    """Test build_region_to_metros with empty region aggregations."""
    fast_assign = _load_fast_assign_module()

    settings = {
        "model_regions": ["Region1"],
        "region_aggregations": {},
    }
    metro_region_map = pd.DataFrame(
        {"metro_id": ["101"], "base_region": ["BA1"]}
    )
    substation_metro_region = pd.DataFrame(
        {"substation_id": [1001], "metro_id": ["101"], "base_region": ["BA1"]}
    )

    result = fast_assign.build_region_to_metros(
        settings, metro_region_map, substation_metro_region
    )

    assert isinstance(result, dict)
    assert "Region1" in result
    assert len(result["Region1"]) == 0  # No metros mapped without aggregations


# Test get_cpa_home_region
def test_get_cpa_home_region_valid(sample_substation_metro_region):
    """Test getting CPA home region with valid substation."""
    fast_assign = _load_fast_assign_module()

    base_to_model = {
        "BA1": "Region1",
        "BA2": "Region1",
        "BA3": "Region2",
        "BA4": "Region2",
    }

    result = fast_assign.get_cpa_home_region(
        cpa_id=1,
        hub_substation=1001,
        substation_metro_region=sample_substation_metro_region,
        base_to_model=base_to_model,
    )

    assert result == "Region1"


def test_get_cpa_home_region_invalid_substation(sample_substation_metro_region):
    """Test getting CPA home region with invalid substation."""
    fast_assign = _load_fast_assign_module()

    base_to_model = {"BA1": "Region1", "BA2": "Region1"}

    result = fast_assign.get_cpa_home_region(
        cpa_id=1,
        hub_substation=9999,  # Non-existent substation
        substation_metro_region=sample_substation_metro_region,
        base_to_model=base_to_model,
    )

    assert result is None


def test_get_cpa_home_region_unmapped_base_region(sample_substation_metro_region):
    """Test getting CPA home region with unmapped base region."""
    fast_assign = _load_fast_assign_module()

    base_to_model = {}  # Empty mapping

    result = fast_assign.get_cpa_home_region(
        cpa_id=1,
        hub_substation=1001,
        substation_metro_region=sample_substation_metro_region,
        base_to_model=base_to_model,
    )

    assert result is None


# Test identify_largest_metro_per_region
def test_identify_largest_metro_per_region_basic(sample_saturation):
    """Test identifying largest metro per region."""
    fast_assign = _load_fast_assign_module()

    region_to_metros: Dict[str, Set[str]] = {
        "Region1": {"101", "102"},
        "Region2": {"201", "202"},
    }

    result = fast_assign.identify_largest_metro_per_region(
        region_to_metros, sample_saturation
    )

    assert isinstance(result, set)
    # Region1: 101 is already infinite sink, so 102 should not be added
    # Region2: 201 has highest population (1M) and no infinite sink, so should be added
    assert "201" in result
    assert "102" not in result  # 101 is already infinite sink


def test_identify_largest_metro_per_region_all_have_infinite_sink():
    """Test when all regions already have an infinite sink metro."""
    fast_assign = _load_fast_assign_module()

    region_to_metros: Dict[str, Set[str]] = {
        "Region1": {"101", "102"},
        "Region2": {"201", "202"},
    }

    saturation = pd.DataFrame(
        {
            "metro_id": ["101", "102", "201", "202"],
            "population": [500000, 200000, 1000000, 300000],
            "is_infinite_sink": [True, False, True, False],
        }
    )

    result = fast_assign.identify_largest_metro_per_region(
        region_to_metros, saturation
    )

    assert isinstance(result, set)
    assert len(result) == 0  # No new infinite sinks needed


def test_identify_largest_metro_per_region_empty():
    """Test with empty region mapping."""
    fast_assign = _load_fast_assign_module()

    region_to_metros: Dict[str, Set[str]] = {}
    saturation = pd.DataFrame(
        {
            "metro_id": ["101"],
            "population": [500000],
            "is_infinite_sink": [False],
        }
    )

    result = fast_assign.identify_largest_metro_per_region(
        region_to_metros, saturation
    )

    assert isinstance(result, set)
    assert len(result) == 0


# Test fast_assign_cpas
@pytest.mark.asyncio
async def test_fast_assign_cpas_greedy_strategy(
    sample_candidates,
    sample_saturation,
    sample_settings,
    sample_metro_region_map,
    sample_substation_metro_region,
):
    """Test fast CPA assignment with greedy strategy."""
    fast_assign = _load_fast_assign_module()

    result = await fast_assign.fast_assign_cpas(
        candidates=sample_candidates,
        saturation=sample_saturation,
        settings=sample_settings,
        metro_region_map=sample_metro_region_map,
        substation_metro_region=sample_substation_metro_region,
        strategy="greedy",
        show_progress=False,
    )

    assert isinstance(result, pd.DataFrame)
    assert not result.empty
    assert "CPA_ID" in result.columns
    assert "metro_id" in result.columns
    assert "tech" in result.columns
    assert "lcoe" in result.columns

    # Check that each CPA is assigned only once per tech
    grouped = result.groupby(["CPA_ID", "tech"]).size()
    assert all(grouped == 1)


@pytest.mark.asyncio
async def test_fast_assign_cpas_respects_capacity_limits(
    sample_settings, sample_metro_region_map, sample_substation_metro_region
):
    """Test that assignment respects metro capacity limits.

    Note: The algorithm allows assigning a CPA as long as remaining capacity > 0,
    even if the CPA size exceeds remaining capacity. This is all-or-nothing per CPA.
    """
    fast_assign = _load_fast_assign_module()

    # Create candidates that exceed capacity
    # Use metro 102 which has neighbors and won't become infinite sink
    candidates = pd.DataFrame(
        {
            "CPA_ID": [1, 2, 3],
            "tech": ["solar", "solar", "solar"],
            "metro_id": ["102", "102", "102"],
            "cpa_mw": [300.0, 400.0, 500.0],
            "cf": [0.25, 0.25, 0.25],
            "lcoe": [40.0, 41.0, 42.0],
            "interconnect_capex_mw": [1000.0, 1000.0, 1000.0],
            "total_interconnect_km": [10.0, 10.0, 10.0],
            "path": [[102], [102], [102]],
            "hub_substation": [1002, 1002, 1002],
        }
    )

    # Set capacity limits for both metros in Region1, with 101 having larger pop
    # so it becomes the infinite sink instead of 102
    saturation = pd.DataFrame(
        {
            "metro_id": ["101", "102"],
            "solar_saturation_mw": [1000.0, 500.0],  # 102 has only 500 MW capacity
            "onshorewind_saturation_mw": [1000.0, 1000.0],
            "population": [700000, 500000],  # 101 has higher population
            "is_infinite_sink": [False, False],
        }
    )

    result = await fast_assign.fast_assign_cpas(
        candidates=candidates,
        saturation=saturation,
        settings=sample_settings,
        metro_region_map=sample_metro_region_map,
        substation_metro_region=sample_substation_metro_region,
        strategy="greedy",
        show_progress=False,
    )

    # The algorithm assigns CPAs as long as remaining capacity > 0.
    # Assignment is all-or-nothing per CPA, so a CPA may exceed remaining capacity.
    # CPA 1 (300 MW): 500 MW remaining > 0, assign, leaving 200 MW
    # CPA 2 (400 MW): 200 MW remaining > 0, assign, leaving -200 MW (clamped to 0)
    # CPA 3 (500 MW): 0 MW remaining, skip
    assert len(result) == 2
    total_assigned_mw = result["cpa_mw"].sum()
    assert total_assigned_mw == 700.0


@pytest.mark.asyncio
async def test_fast_assign_cpas_infinite_sink_ignores_capacity(
    sample_settings, sample_metro_region_map, sample_substation_metro_region
):
    """Test that infinite sink metros ignore capacity limits."""
    fast_assign = _load_fast_assign_module()

    candidates = pd.DataFrame(
        {
            "CPA_ID": [1, 2, 3],
            "tech": ["solar", "solar", "solar"],
            "metro_id": ["101", "101", "101"],
            "cpa_mw": [300.0, 400.0, 500.0],
            "cf": [0.25, 0.25, 0.25],
            "lcoe": [40.0, 41.0, 42.0],
            "interconnect_capex_mw": [1000.0, 1000.0, 1000.0],
            "total_interconnect_km": [10.0, 10.0, 10.0],
            "path": [[101], [101], [101]],
            "hub_substation": [1001, 1001, 1001],
        }
    )

    saturation = pd.DataFrame(
        {
            "metro_id": ["101"],
            "solar_saturation_mw": [100.0],  # Small capacity
            "onshorewind_saturation_mw": [100.0],
            "population": [500000],
            "is_infinite_sink": [True],  # But it's an infinite sink
        }
    )

    result = await fast_assign.fast_assign_cpas(
        candidates=candidates,
        saturation=saturation,
        settings=sample_settings,
        metro_region_map=sample_metro_region_map,
        substation_metro_region=sample_substation_metro_region,
        strategy="greedy",
        show_progress=False,
    )

    # Should assign all CPAs despite capacity limit
    assert len(result) == 3
    total_assigned_mw = result["cpa_mw"].sum()
    assert total_assigned_mw == 1200.0  # All three CPAs assigned


@pytest.mark.asyncio
async def test_fast_assign_cpas_filters_zero_mw_cpas(
    sample_settings, sample_metro_region_map, sample_substation_metro_region
):
    """Test that zero-MW CPAs are filtered out."""
    fast_assign = _load_fast_assign_module()

    candidates = pd.DataFrame(
        {
            "CPA_ID": [1, 2, 3],
            "tech": ["solar", "solar", "solar"],
            "metro_id": ["101", "101", "101"],
            "cpa_mw": [100.0, 0.0, 200.0],  # CPA 2 has zero MW
            "cf": [0.25, 0.25, 0.25],
            "lcoe": [40.0, 41.0, 42.0],
            "interconnect_capex_mw": [1000.0, 1000.0, 1000.0],
            "total_interconnect_km": [10.0, 10.0, 10.0],
            "path": [[101], [101], [101]],
            "hub_substation": [1001, 1001, 1001],
        }
    )

    saturation = pd.DataFrame(
        {
            "metro_id": ["101"],
            "solar_saturation_mw": [1000.0],
            "onshorewind_saturation_mw": [1000.0],
            "population": [500000],
            "is_infinite_sink": [False],
        }
    )

    result = await fast_assign.fast_assign_cpas(
        candidates=candidates,
        saturation=saturation,
        settings=sample_settings,
        metro_region_map=sample_metro_region_map,
        substation_metro_region=sample_substation_metro_region,
        strategy="greedy",
        show_progress=False,
    )

    # Should only assign CPAs 1 and 3, not CPA 2
    assert len(result) == 2
    assert 2 not in result["CPA_ID"].values


@pytest.mark.asyncio
async def test_fast_assign_cpas_dynamic_lcoe_strategy(
    sample_candidates,
    sample_saturation,
    sample_settings,
    sample_metro_region_map,
    sample_substation_metro_region,
):
    """Test fast CPA assignment with dynamic LCOE strategy."""
    fast_assign = _load_fast_assign_module()

    result = await fast_assign.fast_assign_cpas(
        candidates=sample_candidates,
        saturation=sample_saturation,
        settings=sample_settings,
        metro_region_map=sample_metro_region_map,
        substation_metro_region=sample_substation_metro_region,
        strategy="dynamic_lcoe",
        lcoe_penalty_factor=5.0,
        show_progress=False,
    )

    assert isinstance(result, pd.DataFrame)
    assert not result.empty
    # Dynamic LCOE should produce different assignments than greedy
    # due to penalty applied as metros fill up


@pytest.mark.asyncio
async def test_fast_assign_cpas_regional_filtering(
    sample_settings, sample_metro_region_map, sample_substation_metro_region
):
    """Test that CPAs are filtered to their home region."""
    fast_assign = _load_fast_assign_module()

    # Create candidates where CPA from Region1 tries to connect to Region2 metro
    candidates = pd.DataFrame(
        {
            "CPA_ID": [1, 1],
            "tech": ["solar", "solar"],
            "metro_id": ["101", "201"],  # 101 is in Region1, 201 is in Region2
            "cpa_mw": [100.0, 100.0],
            "cf": [0.25, 0.25],
            "lcoe": [40.0, 35.0],  # Cross-region has better LCOE
            "interconnect_capex_mw": [1000.0, 900.0],
            "total_interconnect_km": [10.0, 8.0],
            "path": [[101], [201]],
            "hub_substation": [1001, 1001],  # Hub is in BA1 (Region1)
        }
    )

    saturation = pd.DataFrame(
        {
            "metro_id": ["101", "201"],
            "solar_saturation_mw": [1000.0, 1000.0],
            "onshorewind_saturation_mw": [1000.0, 1000.0],
            "population": [500000, 1000000],
            "is_infinite_sink": [False, False],
        }
    )

    result = await fast_assign.fast_assign_cpas(
        candidates=candidates,
        saturation=saturation,
        settings=sample_settings,
        metro_region_map=sample_metro_region_map,
        substation_metro_region=sample_substation_metro_region,
        strategy="greedy",
        show_progress=False,
    )

    # Should only assign to metro 101 (in-region), not 201 (cross-region)
    # unless allowed_cross_region is provided
    assert len(result) == 1
    assert result.iloc[0]["metro_id"] == "101"


@pytest.mark.asyncio
async def test_fast_assign_cpas_allows_cross_region_connections(
    sample_settings, sample_metro_region_map, sample_substation_metro_region
):
    """Test that allowed cross-region connections are respected."""
    fast_assign = _load_fast_assign_module()

    candidates = pd.DataFrame(
        {
            "CPA_ID": [1, 1],
            "tech": ["solar", "solar"],
            "metro_id": ["101", "201"],
            "cpa_mw": [100.0, 100.0],
            "cf": [0.25, 0.25],
            "lcoe": [40.0, 35.0],  # Cross-region has better LCOE
            "interconnect_capex_mw": [1000.0, 900.0],
            "total_interconnect_km": [10.0, 8.0],
            "path": [[101], [201]],
            "hub_substation": [1001, 1001],
        }
    )

    saturation = pd.DataFrame(
        {
            "metro_id": ["101", "201"],
            "solar_saturation_mw": [1000.0, 1000.0],
            "onshorewind_saturation_mw": [1000.0, 1000.0],
            "population": [500000, 1000000],
            "is_infinite_sink": [False, False],
        }
    )

    # Allow the cross-region connection
    allowed_cross_region = pd.DataFrame(
        {
            "CPA_ID": [1],
            "tech": ["solar"],
            "metro_id": ["201"],
        }
    )

    result = await fast_assign.fast_assign_cpas(
        candidates=candidates,
        saturation=saturation,
        settings=sample_settings,
        metro_region_map=sample_metro_region_map,
        substation_metro_region=sample_substation_metro_region,
        allowed_cross_region=allowed_cross_region,
        strategy="greedy",
        show_progress=False,
    )

    # Should now assign to metro 201 (better LCOE and allowed cross-region)
    assert len(result) == 1
    assert result.iloc[0]["metro_id"] == "201"


# Test validate_against_case
def test_validate_against_case_basic():
    """Test validation against a known case."""
    fast_assign = _load_fast_assign_module()

    # Create sample assignments
    assignments = pd.DataFrame(
        {
            "CPA_ID": [1, 2, 3],
            "tech": ["solar", "solar", "onshorewind"],
            "metro_id": ["101", "102", "201"],
            "interconnect_capex_mw": [1000.0, 1200.0, 1500.0],
        }
    )

    # Create a temporary case directory with truth data
    with tempfile.TemporaryDirectory() as temp_dir:
        case_path = Path(temp_dir) / "test_case"
        case_path.mkdir()
        output_path = case_path / "output" / "cpas"
        output_path.mkdir(parents=True)

        # Create truth data
        truth = pd.DataFrame(
            {
                "CPA_ID": [1, 2],
                "metro_id": ["101", "102"],
                "interconnect_capex_mw": [1000.0, 1100.0],
            }
        )

        truth_file = output_path / "solar_lcoe_test_case.parquet"
        truth.to_parquet(truth_file, index=False)

        # Validate
        result = fast_assign.validate_against_case(
            assignments, case_path, "solar"
        )

        assert "destination_accuracy" in result
        assert "cost_mape" in result
        assert "n_cpas" in result
        assert result["destination_accuracy"] == 1.0  # Both matched
        assert result["n_cpas"] == 2


def test_validate_against_case_missing_file():
    """Test validation with missing truth file."""
    fast_assign = _load_fast_assign_module()

    assignments = pd.DataFrame(
        {
            "CPA_ID": [1],
            "tech": ["solar"],
            "metro_id": ["101"],
            "interconnect_capex_mw": [1000.0],
        }
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        case_path = Path(temp_dir) / "test_case"
        case_path.mkdir()

        result = fast_assign.validate_against_case(
            assignments, case_path, "solar"
        )

        assert "error" in result
        assert result["error"] == "Truth file not found"


def test_validate_against_case_no_matching_cpas():
    """Test validation with no matching CPAs."""
    fast_assign = _load_fast_assign_module()

    assignments = pd.DataFrame(
        {
            "CPA_ID": [1, 2],
            "tech": ["solar", "solar"],
            "metro_id": ["101", "102"],
            "interconnect_capex_mw": [1000.0, 1200.0],
        }
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        case_path = Path(temp_dir) / "test_case"
        case_path.mkdir()
        output_path = case_path / "output" / "cpas"
        output_path.mkdir(parents=True)

        # Create truth data with different CPA IDs
        truth = pd.DataFrame(
            {
                "CPA_ID": [3, 4],
                "metro_id": ["101", "102"],
                "interconnect_capex_mw": [1000.0, 1100.0],
            }
        )

        truth_file = output_path / "solar_lcoe_test_case.parquet"
        truth.to_parquet(truth_file, index=False)

        result = fast_assign.validate_against_case(
            assignments, case_path, "solar"
        )

        assert "error" in result
        assert result["error"] == "No matching CPAs"
