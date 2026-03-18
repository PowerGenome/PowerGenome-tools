"""
Tests for web/calc_network.py — unit and integration coverage for:
  - build_base_to_model_map
  - apply_region_mapping
  - calculate_network_from_frames
  - calculate_network (file-loading wrapper)
  - cluster_app integration: reset_region_dependent_state + _run_network_cost_calculation
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Path setup — make calc_network importable directly
# ---------------------------------------------------------------------------

_WEB_DIR = Path(__file__).parent.parent / "web"
_NETWORK_DATA_DIR = _WEB_DIR / "data" / "network_data"

if str(_WEB_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_DIR))

from calc_network import (  # noqa: E402
    apply_region_mapping,
    build_base_to_model_map,
    calculate_network,
    calculate_network_from_frames,
)

# ---------------------------------------------------------------------------
# Shared minimal synthetic DataFrames
# ---------------------------------------------------------------------------


def _make_nodes():
    """Three-node synthetic nodes DataFrame: p1, p2, p3."""
    return pd.DataFrame(
        {
            "msa_id": ["A", "B", "C"],
            "pop": [2_000_000.0, 300_000.0, 400_000.0],
            "base_region": ["p1", "p2", "p3"],
        }
    )


def _make_edges():
    """Two synthetic cross-region edges: p1→p2 and p2→p3."""
    return pd.DataFrame(
        {
            "start_id": [1, 2],
            "dest_id": [2, 3],
            "u": ["A", "B"],
            "v": ["B", "C"],
            "u_base_region": ["p1", "p2"],
            "v_base_region": ["p2", "p3"],
            "cost": [100_000.0, 200_000.0],
            "dist": [50.0, 80.0],
            "line_loss_frac": [0.02, 0.03],
        }
    )


def _make_topology():
    """Bidirectional topology covering p1↔p2 and p2↔p3."""
    return pd.DataFrame(
        {
            "region_from_base": ["p1", "p2", "p2", "p3"],
            "region_to_base": ["p2", "p1", "p3", "p2"],
        }
    )


# ===========================================================================
# 1. build_base_to_model_map
# ===========================================================================


def test_build_base_to_model_map_with_aggregations():
    settings = {
        "model_regions": ["RegionA", "RegionB"],
        "region_aggregations": {
            "RegionA": ["p1", "p2"],
            "RegionB": ["p3"],
        },
    }
    result = build_base_to_model_map(settings)
    assert result == {"p1": "RegionA", "p2": "RegionA", "p3": "RegionB"}


def test_build_base_to_model_map_empty_aggregations_identity():
    """Empty aggregations dict → each model region maps to itself."""
    settings = {
        "model_regions": ["ISONE", "NYISO"],
        "region_aggregations": {},
    }
    result = build_base_to_model_map(settings)
    assert result == {"ISONE": "ISONE", "NYISO": "NYISO"}


def test_build_base_to_model_map_none_aggregations_identity():
    """Missing/None region_aggregations → identity mapping on model_regions."""
    settings = {"model_regions": ["East", "West"]}
    result = build_base_to_model_map(settings)
    assert result == {"East": "East", "West": "West"}


def test_build_base_to_model_map_empty_settings():
    """Completely empty settings dict → empty map."""
    result = build_base_to_model_map({})
    assert result == {}


def test_build_base_to_model_map_model_region_without_agg_list():
    """If a model_region has no list in aggregations, it maps to itself."""
    settings = {
        "model_regions": ["R1", "R2"],
        "region_aggregations": {
            "R1": ["p1", "p2"],
            # R2 not present in aggregations
        },
    }
    result = build_base_to_model_map(settings)
    assert result["p1"] == "R1"
    assert result["p2"] == "R1"
    # R2 has no list entry ⟹ falls through to identity
    assert result["R2"] == "R2"


# ===========================================================================
# 2. apply_region_mapping
# ===========================================================================


def test_apply_region_mapping_no_settings():
    """With settings=None, region columns are copies of base_region columns."""
    nodes = _make_nodes()
    edges = _make_edges()
    topo = _make_topology()

    out_nodes, out_edges, out_topo = apply_region_mapping(
        nodes, edges, topo, settings=None
    )

    assert list(out_nodes["region"]) == list(nodes["base_region"])
    assert list(out_edges["region_u"]) == list(edges["u_base_region"])
    assert list(out_edges["region_v"]) == list(edges["v_base_region"])
    assert "start_region" in out_topo.columns
    assert "dest_region" in out_topo.columns
    # Original topology length preserved (no filtering when settings=None)
    assert len(out_topo) == len(topo)


def test_apply_region_mapping_with_settings_maps_correctly():
    """With settings, base regions are mapped to model regions."""
    nodes = _make_nodes()
    edges = _make_edges()
    topo = _make_topology()

    settings = {
        "model_regions": ["ModelA", "ModelB"],
        "region_aggregations": {
            "ModelA": ["p1"],
            "ModelB": ["p2"],
            # p3 is NOT included → its node will be dropped
        },
    }

    out_nodes, out_edges, out_topo = apply_region_mapping(nodes, edges, topo, settings)

    # Only p1 and p2 nodes survive (p3 dropped since it has no mapping)
    assert set(out_nodes["region"]) == {"ModelA", "ModelB"}
    assert "C" not in out_nodes["msa_id"].values

    # Edge from p2→p3 (B→C) must be dropped because C is unmapped
    remaining_u = set(out_edges["u"].values)
    assert "C" not in remaining_u


def test_apply_region_mapping_unmapped_bas_dropped_from_edges():
    """BAs not covered by any model region are removed from both nodes and edges."""
    nodes = _make_nodes()
    edges = _make_edges()
    topo = _make_topology()

    settings = {
        "model_regions": ["OnlyA"],
        "region_aggregations": {"OnlyA": ["p1"]},
    }

    out_nodes, out_edges, out_topo = apply_region_mapping(nodes, edges, topo, settings)

    # Only the p1 node is kept
    assert len(out_nodes) == 1
    assert out_nodes.iloc[0]["region"] == "OnlyA"

    # No edges remain because both p2→ and →p3 endpoints are unmapped
    assert out_edges.empty


def test_apply_region_mapping_deduplicates_topology():
    """When two base topology rows map to the same model-region pair they are deduplicated."""
    nodes = pd.DataFrame(
        {
            "msa_id": ["A", "B"],
            "pop": [500_000.0, 600_000.0],
            "base_region": ["p1", "p2"],
        }
    )
    edges = pd.DataFrame(
        {
            "start_id": [1],
            "dest_id": [2],
            "u": ["A"],
            "v": ["B"],
            "u_base_region": ["p1"],
            "v_base_region": ["p2"],
            "cost": [50_000.0],
            "dist": [30.0],
            "line_loss_frac": [0.01],
        }
    )
    # Both p1→p2 rows map to ModelA→ModelB; should be deduplicated to one row
    topo = pd.DataFrame(
        {
            "region_from_base": ["p1", "p1"],  # duplicate source pair
            "region_to_base": ["p2", "p2"],
        }
    )
    settings = {
        "model_regions": ["ModelA", "ModelB"],
        "region_aggregations": {"ModelA": ["p1"], "ModelB": ["p2"]},
    }
    _, _, out_topo = apply_region_mapping(nodes, edges, topo, settings)

    # Two duplicate input rows collapse to one output row
    assert len(out_topo) == 1
    assert out_topo.iloc[0]["start_region"] == "ModelA"
    assert out_topo.iloc[0]["dest_region"] == "ModelB"


# ===========================================================================
# 3. calculate_network with real data files (settings=None)
# ===========================================================================


def test_calculate_network_no_settings_returns_dataframe():
    """File-loading wrapper returns a non-empty DataFrame with expected columns."""
    result = calculate_network(data_dir=_NETWORK_DATA_DIR, settings=None)

    assert isinstance(result, pd.DataFrame)
    assert len(result) > 0


def test_calculate_network_no_settings_expected_columns():
    """Output has all required cost/loss/distance columns."""
    result = calculate_network(data_dir=_NETWORK_DATA_DIR, settings=None)

    expected_cols = {
        "start_region",
        "dest_region",
        "start_id",
        "dest_id",
        "interconnect_cost_mw",
        "line_loss_frac",
        "mw-km_per_mw",
        "start_intraregion_cost_mw",
        "dest_intraregion_cost_mw",
        "start_intraregion_loss_frac",
        "dest_intraregion_loss_frac",
        "start_mw-km_per_mw",
        "dest_mw-km_per_mw",
        "total_interconnect_cost_mw",
        "total_line_loss_frac",
        "total_mw-km_per_mw",
    }
    assert expected_cols.issubset(set(result.columns))


def test_calculate_network_no_self_loops():
    """No row should have start_region == dest_region."""
    result = calculate_network(data_dir=_NETWORK_DATA_DIR, settings=None)
    assert (result["start_region"] != result["dest_region"]).all()


def test_calculate_network_cost_non_negative():
    """total_interconnect_cost_mw must be non-negative for all rows."""
    result = calculate_network(data_dir=_NETWORK_DATA_DIR, settings=None)
    assert (result["total_interconnect_cost_mw"] >= 0).all()


def test_calculate_network_loss_in_unit_interval():
    """total_line_loss_frac must lie in [0, 1]."""
    result = calculate_network(data_dir=_NETWORK_DATA_DIR, settings=None)
    assert (result["total_line_loss_frac"] >= 0).all()
    assert (result["total_line_loss_frac"] <= 1).all()


# ===========================================================================
# 4. calculate_network_from_frames with a 2-region aggregation over real data
#
# Real data facts:
#   p1 → msa_id='42660', pop=3_979_845  (≥1M ⟹ major MSA in p1)
#   p2 → msa_id='49420', pop=250_873   (<1M ⟹ fallback single MSA in p2)
#   p1↔p2 appears in topology_base.csv; 5 cross-region edges exist
# ===========================================================================


@pytest.fixture(scope="module")
def real_network_frames():
    """Load actual network data files once for the whole module."""
    nodes = pd.read_csv(_NETWORK_DATA_DIR / "nodes.csv", dtype={"msa_id": str})
    edges = pd.read_parquet(_NETWORK_DATA_DIR / "edges.parquet")
    topo = pd.read_csv(_NETWORK_DATA_DIR / "topology_base.csv")
    return nodes, edges, topo


def test_calculate_network_from_frames_two_region_aggregation(real_network_frames):
    """p1→WestCoast, p2→Northwest aggregation produces 2 directed result rows."""
    nodes, edges, topo = real_network_frames
    settings = {
        "model_regions": ["WestCoast", "Northwest"],
        "region_aggregations": {
            "WestCoast": ["p1"],
            "Northwest": ["p2"],
        },
    }

    result = calculate_network_from_frames(nodes, edges, topo, settings=settings)

    assert isinstance(result, pd.DataFrame)
    assert len(result) == 2  # one row per directed pair
    regions_set = set(zip(result["start_region"], result["dest_region"]))
    assert ("WestCoast", "Northwest") in regions_set
    assert ("Northwest", "WestCoast") in regions_set


def test_calculate_network_from_frames_two_region_values_valid(real_network_frames):
    """Numeric output columns are physically sensible for the 2-region case."""
    nodes, edges, topo = real_network_frames
    settings = {
        "model_regions": ["WestCoast", "Northwest"],
        "region_aggregations": {
            "WestCoast": ["p1"],
            "Northwest": ["p2"],
        },
    }

    result = calculate_network_from_frames(nodes, edges, topo, settings=settings)

    assert (result["total_interconnect_cost_mw"] >= 0).all()
    assert (result["total_line_loss_frac"] >= 0).all()
    assert (result["total_line_loss_frac"] <= 1).all()
    assert (result["total_mw-km_per_mw"] >= 0).all()


# ===========================================================================
# 5. Empty topology — no cross-region edges → empty result
# ===========================================================================


def test_calculate_network_from_frames_empty_result_when_no_cross_region_edges():
    """When all edges are within the same region and topology is empty, return empty DF."""
    nodes = pd.DataFrame(
        {
            "msa_id": ["A", "B"],
            "pop": [500_000.0, 600_000.0],
            "base_region": ["R1", "R2"],
        }
    )
    edges = pd.DataFrame(
        {
            "start_id": [1, 2],
            "dest_id": [2, 3],
            "u": ["A", "A"],
            "v": ["A", "A"],
            "u_base_region": ["R1", "R1"],
            "v_base_region": ["R1", "R1"],  # all intraregional
            "cost": [50_000.0, 60_000.0],
            "dist": [30.0, 40.0],
            "line_loss_frac": [0.01, 0.02],
        }
    )
    # Empty topology; and since all edges are within R1, fallback also finds nothing
    topology_base = pd.DataFrame(
        {
            "region_from_base": pd.Series([], dtype=str),
            "region_to_base": pd.Series([], dtype=str),
        }
    )

    result = calculate_network_from_frames(nodes, edges, topology_base, settings=None)

    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0


# ===========================================================================
# 6. output_path — CSV file is created with expected columns
# ===========================================================================


def test_calculate_network_from_frames_output_path(tmp_path, real_network_frames):
    """Passing output_path creates a CSV file with the expected columns."""
    nodes, edges, topo = real_network_frames
    settings = {
        "model_regions": ["WestCoast", "Northwest"],
        "region_aggregations": {
            "WestCoast": ["p1"],
            "Northwest": ["p2"],
        },
    }
    out_file = tmp_path / "subdir" / "network_costs.csv"

    calculate_network_from_frames(
        nodes, edges, topo, settings=settings, output_path=out_file
    )

    assert out_file.exists()
    saved = pd.read_csv(out_file)
    assert "start_region" in saved.columns
    assert "dest_region" in saved.columns
    assert "total_interconnect_cost_mw" in saved.columns
    assert "total_line_loss_frac" in saved.columns
    assert "total_mw-km_per_mw" in saved.columns


# ===========================================================================
# 7. cluster_app integration tests
# ===========================================================================

_CLUSTER_APP_MODULE_NAMES = [
    "js",
    "pyodide",
    "pyodide.ffi",
    "renewables_utils",
    "fast_interconnection",
    "fast_interconnection.fast_assign",
    "fast_interconnection.resource_groups",
    "cluster_app",
]


@pytest.fixture(scope="module")
def cluster_app_module():
    """Load cluster_app with mocked PyScript/browser dependencies.

    Follows the same pattern as test_cluster_app_integration.py.
    """
    original_modules = {
        name: sys.modules.get(name) for name in _CLUSTER_APP_MODULE_NAMES
    }
    web_dir = None

    try:
        mock_js = MagicMock()
        mock_js.L = MagicMock()
        mock_js.document = MagicMock()
        mock_js.window = MagicMock()
        mock_js.fetch = AsyncMock()
        mock_js.Uint8Array = MagicMock()
        mock_js.globalThis = MagicMock()

        mock_ffi = MagicMock()
        mock_ffi.create_proxy = lambda x: x
        mock_ffi.to_js = lambda x: x
        mock_ffi.JsProxy = object

        sys.modules["js"] = mock_js
        sys.modules["pyodide"] = MagicMock()
        sys.modules["pyodide.ffi"] = mock_ffi

        mock_ru = MagicMock()
        sys.modules["renewables_utils"] = mock_ru

        sys.modules["fast_interconnection"] = MagicMock()
        sys.modules["fast_interconnection.fast_assign"] = MagicMock()
        mock_rg = MagicMock()
        mock_rg.DEFAULT_PROFILE_PATHS = {}
        mock_rg.build_assigned_df = MagicMock(return_value=None)
        mock_rg.build_resource_group_json = MagicMock(return_value={})
        sys.modules["fast_interconnection.resource_groups"] = mock_rg

        web_dir = Path(__file__).parent.parent / "web"
        if str(web_dir) not in sys.path:
            sys.path.insert(0, str(web_dir))

        module_path = web_dir / "cluster_app.py"
        spec = importlib.util.spec_from_file_location("cluster_app", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["cluster_app"] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)

        yield module
    finally:
        if web_dir is not None and str(web_dir) in sys.path:
            sys.path.remove(str(web_dir))
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


@pytest.fixture()
def app(cluster_app_module):
    """Return cluster_app module with a fresh AppState for each test."""
    cluster_app_module.state = cluster_app_module.AppState()
    return cluster_app_module


# ---------------------------------------------------------------------------
# 7a. reset_region_dependent_state clears network_costs_df but not cache
# ---------------------------------------------------------------------------


def test_reset_clears_network_costs_df_keeps_cache(app):
    """reset_region_dependent_state nulls network_costs_df without touching the cache."""
    app.state.network_costs_df = "some_dataframe"
    app.state.network_data_cache = "cached_data"

    app.reset_region_dependent_state()

    assert app.state.network_costs_df is None
    assert app.state.network_data_cache == "cached_data"


# ---------------------------------------------------------------------------
# 7b. _run_network_cost_calculation is a no-op when region_aggregations is None
# ---------------------------------------------------------------------------


async def test_run_network_cost_noop_when_no_aggregations(app):
    """_run_network_cost_calculation returns early when region_aggregations is falsy."""
    sentinel = object()
    app.state.region_aggregations = None
    app.state.network_costs_df = sentinel

    await app._run_network_cost_calculation()

    assert app.state.network_costs_df is sentinel


# ---------------------------------------------------------------------------
# 7c. _run_network_cost_calculation populates network_costs_df with real data
# ---------------------------------------------------------------------------


async def test_run_network_cost_sets_costs_df(app):
    """_run_network_cost_calculation computes and stores network_costs_df."""
    # Pre-populate context: use p1 (WestCoast) and p2 (Northwest) real regions
    nodes_df = pd.read_csv(_NETWORK_DATA_DIR / "nodes.csv", dtype={"msa_id": str})
    edges_df = pd.read_parquet(_NETWORK_DATA_DIR / "edges.parquet")
    topo_df = pd.read_csv(_NETWORK_DATA_DIR / "topology_base.csv")

    # Pre-seed the cache so _ensure_network_data_cache becomes a no-op
    app.state.network_data_cache = (nodes_df, edges_df, topo_df)
    app.state.region_aggregations = {"WestCoast": ["p1"], "Northwest": ["p2"]}
    app.state.network_costs_df = None

    await app._run_network_cost_calculation()

    # Should produce a DataFrame (may be empty if p1/p2 have no topology match,
    # but should not remain None after a successful run)
    assert app.state.network_costs_df is not None
    assert isinstance(app.state.network_costs_df, pd.DataFrame)


async def test_run_network_cost_result_has_expected_columns(app):
    """network_costs_df produced by the coroutine has the standard output columns."""
    nodes_df = pd.read_csv(_NETWORK_DATA_DIR / "nodes.csv", dtype={"msa_id": str})
    edges_df = pd.read_parquet(_NETWORK_DATA_DIR / "edges.parquet")
    topo_df = pd.read_csv(_NETWORK_DATA_DIR / "topology_base.csv")

    app.state.network_data_cache = (nodes_df, edges_df, topo_df)
    app.state.region_aggregations = {"WestCoast": ["p1"], "Northwest": ["p2"]}

    await app._run_network_cost_calculation()

    df = app.state.network_costs_df
    assert df is not None
    if not df.empty:
        for col in (
            "start_region",
            "dest_region",
            "total_interconnect_cost_mw",
            "total_line_loss_frac",
            "total_mw-km_per_mw",
        ):
            assert col in df.columns


async def test_run_network_cost_handles_exception_gracefully(app):
    """If the calculation raises, network_costs_df is set to None and no exception propagates."""
    # Pre-seed cache with a broken DataFrame to force a downstream error
    app.state.network_data_cache = ("not_a_df", "not_a_df", "not_a_df")
    app.state.region_aggregations = {"R1": ["p1"]}
    app.state.network_costs_df = "previous_value"

    # Should not raise; internally catches and calls set_status
    await app._run_network_cost_calculation()

    assert app.state.network_costs_df is None
