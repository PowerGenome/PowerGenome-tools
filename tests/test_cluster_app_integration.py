"""
Integration tests for cluster_app.py covering core workflows:
1. Data loading (load_data)
2. Clustering (run_clustering)
3. YAML generation (generate_*)

This file uses the same mocking strategy as test_cluster_app_algorithms.py
to load the PyScript-dependent module in a standard pytest environment.
"""

import importlib.util
import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixture: load cluster_app with mocked dependencies
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cluster_app_module():
    """Load cluster_app module with mocked js/PyScript dependencies.
    Returns the loaded module and mock objects dictionary.
    """
    module_names = [
        "js",
        "pyodide",
        "pyodide.ffi",
        "renewables_utils",
        "fast_interconnection",
        "fast_interconnection.fast_assign",
        "fast_interconnection.resource_groups",
        "cluster_app",
    ]
    original_modules = {name: sys.modules.get(name) for name in module_names}
    web_dir = None

    try:
        # Create mock objects that will be imported by cluster_app
        mock_L = MagicMock()
        mock_document = MagicMock()
        mock_window = MagicMock()
        mock_fetch = AsyncMock()
        mock_Uint8Array = MagicMock()
        mock_globalThis = MagicMock()

        # Create js module mock with these objects
        mock_js = MagicMock()
        mock_js.L = mock_L
        mock_js.document = mock_document
        mock_js.window = mock_window
        mock_js.fetch = mock_fetch
        mock_js.Uint8Array = mock_Uint8Array
        mock_js.globalThis = mock_globalThis

        mock_ffi = MagicMock()
        mock_ffi.create_proxy = lambda x: x
        mock_ffi.to_js = lambda x: x
        mock_ffi.JsProxy = object

        sys.modules["js"] = mock_js
        sys.modules["pyodide"] = MagicMock()
        sys.modules["pyodide.ffi"] = mock_ffi

        # Mock renewables_utils
        mock_ru = MagicMock()
        mock_ru.optimize_cluster_allocation = lambda region_lcoe_data, bins, target: {
            r: 1 for r in bins
        }
        sys.modules["renewables_utils"] = mock_ru

        # Mock fast_interconnection submodules
        sys.modules["fast_interconnection"] = MagicMock()
        sys.modules["fast_interconnection.fast_assign"] = MagicMock()
        mock_rg = MagicMock()
        mock_rg.DEFAULT_PROFILE_PATHS = {}
        mock_rg.build_assigned_df = MagicMock(return_value=None)
        mock_rg.build_resource_group_json = MagicMock(return_value={})
        sys.modules["fast_interconnection.resource_groups"] = mock_rg

        # Load the module
        web_dir = Path(__file__).parent.parent / "web"
        sys.path.insert(0, str(web_dir))
        module_path = web_dir / "cluster_app.py"
        spec = importlib.util.spec_from_file_location("cluster_app", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["cluster_app"] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)

        # Store mock references for test access
        module._test_mocks = {
            "fetch": mock_fetch,
            "document": mock_document,
            "window": mock_window,
            "L": mock_L,
            "Uint8Array": mock_Uint8Array,
            "globalThis": mock_globalThis,
        }

        yield module
    finally:
        if web_dir is not None and str(web_dir) in sys.path:
            sys.path.remove(str(web_dir))
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


@pytest.fixture
def app(cluster_app_module):
    """Return the module but reset critical state between tests."""
    # Reset state
    cluster_app_module.state = cluster_app_module.AppState()
    # Reset mocks using the stored references
    cluster_app_module._test_mocks["fetch"].reset_mock()
    cluster_app_module._test_mocks["document"].reset_mock()
    return cluster_app_module


# ---------------------------------------------------------------------------
# 1. Data Loading Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_data_success(app):
    """Test successful data loading with mocked fetch."""
    # Define mock CSV content
    hierarchy_csv = "ba,st,nercr,transgrp,transreg,interconnect,country\nISONE,MA,NPCC,New England,Northeast,Eastern,USA"
    transmission_csv = "region_from,region_to,firm_ttc_mw\nISONE,NY,1000"
    plant_csv = "plant_id,capacity_mw,technology,region\n1,100,Nuclear,ISONE"
    plant_map_csv = "plant_id,region\n1,ISONE"
    demand_csv = "region,weather_year,annual_demand_mwh\nisone,2012,50000"

    # Setup fetch mock side effects
    async def mock_fetch_side_effect(url):
        mock_resp = MagicMock()
        mock_resp.ok = True
        if "hierarchy.csv" in url:
            mock_resp.text.return_value = hierarchy_csv
        elif "transmission_capacity_reeds.csv" in url:
            mock_resp.text.return_value = transmission_csv
        elif "reeds_generators_transformed.csv" in url:
            mock_resp.text.return_value = plant_csv
        elif "plant_region_map.csv" in url:
            mock_resp.text.return_value = plant_map_csv
        elif "reeds_annual_demand_2050.csv" in url:
            mock_resp.text.return_value = demand_csv
        else:
            mock_resp.text.return_value = ""
        mock_resp.text = AsyncMock(return_value=mock_resp.text.return_value)
        return mock_resp

    app._test_mocks["fetch"].side_effect = mock_fetch_side_effect

    # Run load_data
    await app.load_data()

    # Verify state populated
    assert app.state.hierarchy_df is not None
    assert len(app.state.hierarchy_df) == 1
    assert app.state.hierarchy_df.iloc[0]["ba"] == "ISONE"

    assert app.state.transmission_df is not None
    assert len(app.state.transmission_df) == 1

    assert app.state.plants_df is not None
    assert len(app.state.plants_df) == 1

    assert app.state.reeds_annual_demand_df is not None
    assert "isone" in app.state.reeds_annual_demand_avg
    assert app.state.reeds_annual_demand_avg["isone"] == 50000


@pytest.mark.asyncio
async def test_load_data_failure(app):
    """Test data loading failure handling."""
    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status = 404
    mock_resp.statusText = "Not Found"

    # Create an async function that returns the mock response
    async def mock_fetch_failure(*args, **kwargs):
        return mock_resp

    app._test_mocks["fetch"].side_effect = mock_fetch_failure

    # Should raise exception
    with pytest.raises(Exception, match="Failed to load hierarchy.csv"):
        await app.load_data()


# ---------------------------------------------------------------------------
# 2. Clustering Logic Tests
# ---------------------------------------------------------------------------


def test_run_clustering_workflow(app):
    """Test the run_clustering function orchestrating the workflow."""
    # Setup state
    app.state.hierarchy_df = pd.DataFrame(
        {
            "ba": ["A", "B", "C", "D"],
            "st": ["State1", "State1", "State2", "State2"],
            "nercr": ["Region1", "Region1", "Region2", "Region2"],
        }
    )
    app.state.transmission_df = pd.DataFrame(
        {
            "region_from": ["A", "B", "C"],
            "region_to": ["B", "C", "D"],
            "firm_ttc_mw": [100.0, 50.0, 100.0],
        }
    )

    selected_bas = {"A", "B", "C", "D"}
    # Cluster into 2 regions based on 'st' (State)
    # Expected: "State1" (A, B) and "State2" (C, D)
    # Note: Hierarchical clustering respecting "st" grouping should produce this

    model_regions, region_aggs, error, info = app.run_clustering(
        selected_bas=selected_bas,
        grouping_column="st",
        target_regions=2,
        no_cluster_groups=[],
        method="hierarchical-sum",
    )

    assert error is None
    assert len(model_regions) == 2
    # Check aggregation content
    # Values might be renamed (e.g. State1_1), but grouping should be preserved.
    # Logic: grouping by 'st', and target=2 = # of groups. So it should be exact groups.
    # Name generation adds numbers, so look for "State1..." and "State2..."

    # We verify that A and B are together, C and D are together
    a_region = [r for r, bas in region_aggs.items() if "A" in bas][0]
    b_region = [r for r, bas in region_aggs.items() if "B" in bas][0]
    c_region = [r for r, bas in region_aggs.items() if "C" in bas][0]
    d_region = [r for r, bas in region_aggs.items() if "D" in bas][0]

    assert a_region == b_region
    assert c_region == d_region
    assert a_region != c_region


def test_run_clustering_no_selection(app):
    """Test run_clustering with empty selection."""
    app.state.hierarchy_df = pd.DataFrame({"ba": ["A"]})
    model_regions, _, error, _ = app.run_clustering(
        selected_bas=set(),
        grouping_column="st",
        target_regions=1,
        no_cluster_groups=[],
    )
    assert error == "No valid BAs selected"
    assert model_regions is None


def test_run_clustering_no_cluster_groups(app):
    """Test 'no_cluster' logic excludes BAs from clustering algorithms."""
    # A, B in Group1; C in Group2
    app.state.hierarchy_df = pd.DataFrame(
        {
            "ba": ["A", "B", "C"],
            "grp": ["G1", "G1", "G2"],
            "st": ["S1", "S1", "S2"],
        }
    )
    # Transmission needed for graph building
    app.state.transmission_df = pd.DataFrame(
        {
            "region_from": ["A"],
            "region_to": ["B"],
            "firm_ttc_mw": [100.0],
        }
    )

    # Exclude G1 from clustering -> A and B stay as single BAs
    # C is clustered (but alone since it's the only one left)

    selected_bas = {"A", "B", "C"}
    model_regions, region_aggs, error, info = app.run_clustering(
        selected_bas=selected_bas,
        grouping_column="grp",
        target_regions=1,  # Only applies to clustered set (C)
        no_cluster_groups=["G1"],
        method="hierarchical-sum",
    )

    assert error is None
    # Expect 3 regions: A, B (unclustered) and C (clustered result)
    assert len(model_regions) == 3

    # Verify A and B are their own regions (named by state likely)
    for ba in ["A", "B"]:
        # Find which region contains this BA
        regions_with_ba = [r for r, bas in region_aggs.items() if ba in bas]
        assert len(regions_with_ba) == 1
        region_name = regions_with_ba[0]
        # Should contain ONLY this BA
        assert region_aggs[region_name] == [ba]


# ---------------------------------------------------------------------------
# 3. YAML Generation Tests
# ---------------------------------------------------------------------------


def test_generate_model_definition_settings(app):
    """Test generating model_definition.yml content."""
    # Setup necessary state
    app.state.region_aggregations = {"Region1": ["A", "B"], "Region2": ["C"]}

    # Mock document.getElementById for year inputs
    def mock_get_element_by_id(id_):
        mock_el = MagicMock()
        if id_ == "targetUsdYear":
            mock_el.value = "2020"
        elif id_ == "utcOffset":
            mock_el.value = "-5"
        elif id_ == "modelYears":
            mock_el.value = "2030, 2040"
        elif id_ == "planningYears":
            mock_el.value = "2025, 2035"
        return mock_el

    app._test_mocks["document"].getElementById.side_effect = mock_get_element_by_id

    # Call simple wrapper function from your app (if available) or the generate function
    # Note: _get_region_aggregations_or_raise calls state.region_aggregations

    yaml_str = app.generate_model_definition_settings()
    data = yaml.safe_load(yaml_str)

    assert data["target_usd_year"] == 2020
    assert data["utc_offset"] == -5
    assert data["model_periods"] == [[2025, 2030], [2035, 2040]]
    assert "model_year" not in data
    assert "model_first_planning_year" not in data
    assert "Region1" in data["model_regions"]
    assert "Region2" in data["model_regions"]
    assert data["region_aggregations"]["Region1"] == ["A", "B"]


def test_build_settings_yamls(app):
    """Test the full settings generation dictionary."""
    # Mock all generate_* functions to return dummy strings
    # This ensures we are testing the composition, not re-testing every detail

    # Setup state to avoid immediate errors
    app.state.region_aggregations = {"R1": ["A"]}
    app._test_mocks["document"].getElementById.return_value.value = (
        "2030"  # Default for years
    )

    with (
        patch.object(
            app, "generate_model_definition_settings", return_value="model_def: 1"
        ),
        patch.object(app, "generate_resources_settings", return_value="resources: 1"),
        patch.object(app, "generate_fuels_settings", return_value="fuels: 1"),
        patch.object(app, "generate_transmission_settings", return_value="trans: 1"),
        patch.object(app, "generate_distributed_gen_settings", return_value="dg: 1"),
        patch.object(app, "generate_resource_tags_settings", return_value="tags: 1"),
        patch.object(app, "generate_startup_costs_settings", return_value="startup: 1"),
    ):

        yamls = app.build_settings_yamls()

        assert "model_definition.yml" in yamls
        assert "resources.yml" in yamls
        assert yamls["model_definition.yml"] == "model_def: 1"
