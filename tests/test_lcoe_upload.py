"""
Tests for LCOE file upload functionality in cluster_app.py.

Covers:
- _load_resource_group_lcoe_df() — primary (assignments) and fallback (uploaded) paths
- _LCOE_REQUIRED_COLUMNS constant
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Fixture — mirrors the one in test_state_resets.py exactly
# ---------------------------------------------------------------------------


@pytest.fixture()
def cluster_app():
    """Load cluster_app module with mocked js and DOM dependencies."""
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
        mock_js = MagicMock()
        mock_ffi = MagicMock()
        mock_ffi.create_proxy = lambda x: x
        mock_ffi.to_js = lambda x: x

        sys.modules["js"] = mock_js
        sys.modules["pyodide"] = MagicMock()
        sys.modules["pyodide.ffi"] = mock_ffi
        sys.modules["renewables_utils"] = MagicMock()
        sys.modules["fast_interconnection"] = MagicMock()
        sys.modules["fast_interconnection.fast_assign"] = MagicMock()
        sys.modules["fast_interconnection.resource_groups"] = MagicMock()

        mock_js.L = MagicMock()
        mock_js.document = MagicMock()
        mock_js.window = MagicMock()
        mock_js.fetch = MagicMock()
        mock_js.Uint8Array = MagicMock()
        mock_js.globalThis = MagicMock()

        web_dir = Path(__file__).parent.parent / "web"
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


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_assignments_df(techs=("onshorewind", "solar")):
    """Return a minimal resource_group_assignments DataFrame."""
    rows = []
    for i, tech in enumerate(techs):
        rows.append(
            {
                "tech": tech,
                "model_region": f"region_{i + 1}",
                "cpa_mw": float((i + 1) * 100),
                "cf": 0.30 + i * 0.05,
                "lcoe": 40.0 + i * 5,
            }
        )
    return pd.DataFrame(rows)


def _make_uploaded_df():
    """Return a minimal uploaded LCOE DataFrame (region/cpa_mw/cf/lcoe)."""
    return pd.DataFrame(
        {
            "region": ["region_A", "region_B"],
            "cpa_mw": [200.0, 350.0],
            "cf": [0.28, 0.32],
            "lcoe": [38.0, 42.5],
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLoadResourceGroupLcoeDF:

    def test_load_from_assignments_primary_path(self, cluster_app):
        """Assignments take priority; result has renamed columns; only matching tech rows."""
        assignments = _make_assignments_df(techs=["onshorewind", "solar"])
        cluster_app.state.resource_group_assignments = assignments

        # Also set the uploaded attr — it must NOT be used
        cluster_app.state.uploaded_lcoe_onshorewind = _make_uploaded_df()

        result = cluster_app._load_resource_group_lcoe_df("onshorewind")

        assert result is not None
        # Columns must be renamed correctly
        assert set(result.columns) == {"region", "capacity_mw", "cf", "lcoe"}
        assert "model_region" not in result.columns
        assert "cpa_mw" not in result.columns
        # Only onshorewind rows
        assert len(result) == 1
        assert result["region"].iloc[0] == "region_1"
        assert result["capacity_mw"].iloc[0] == 100.0

    def test_load_from_uploaded_wind_when_no_assignments(self, cluster_app):
        """Fallback to uploaded_lcoe_onshorewind when no assignments present."""
        cluster_app.state.resource_group_assignments = None
        cluster_app.state.uploaded_lcoe_onshorewind = _make_uploaded_df()
        cluster_app.state.uploaded_lcoe_solar = None

        result = cluster_app._load_resource_group_lcoe_df("onshorewind")

        assert result is not None
        assert set(result.columns) == {"region", "capacity_mw", "cf", "lcoe"}
        assert "cpa_mw" not in result.columns
        assert len(result) == 2
        assert list(result["region"]) == ["region_A", "region_B"]
        assert list(result["capacity_mw"]) == [200.0, 350.0]

    def test_load_from_uploaded_solar_when_no_assignments(self, cluster_app):
        """Fallback to uploaded_lcoe_solar when resource_key is 'solar'."""
        cluster_app.state.resource_group_assignments = None
        cluster_app.state.uploaded_lcoe_onshorewind = None

        solar_df = pd.DataFrame(
            {
                "region": ["solar_region_1", "solar_region_2", "solar_region_3"],
                "cpa_mw": [500.0, 600.0, 700.0],
                "cf": [0.22, 0.24, 0.26],
                "lcoe": [35.0, 33.5, 31.0],
            }
        )
        cluster_app.state.uploaded_lcoe_solar = solar_df

        result = cluster_app._load_resource_group_lcoe_df("solar")

        assert result is not None
        assert set(result.columns) == {"region", "capacity_mw", "cf", "lcoe"}
        assert "cpa_mw" not in result.columns
        assert len(result) == 3

    def test_returns_none_when_both_missing(self, cluster_app):
        """Returns None for both techs when assignments and uploads are absent."""
        cluster_app.state.resource_group_assignments = None
        cluster_app.state.uploaded_lcoe_onshorewind = None
        cluster_app.state.uploaded_lcoe_solar = None

        assert cluster_app._load_resource_group_lcoe_df("onshorewind") is None
        assert cluster_app._load_resource_group_lcoe_df("solar") is None

    def test_uploaded_wind_ignored_for_solar(self, cluster_app):
        """uploaded_lcoe_onshorewind does not satisfy a 'solar' request."""
        cluster_app.state.resource_group_assignments = None
        cluster_app.state.uploaded_lcoe_onshorewind = _make_uploaded_df()
        cluster_app.state.uploaded_lcoe_solar = None

        result = cluster_app._load_resource_group_lcoe_df("solar")

        assert result is None

    def test_required_columns_constant(self, cluster_app):
        """_LCOE_REQUIRED_COLUMNS contains the expected set of column names."""
        assert cluster_app._LCOE_REQUIRED_COLUMNS == {"region", "cpa_mw", "cf", "lcoe"}

    def test_load_returns_none_for_empty_uploaded_df(self, cluster_app):
        """An uploaded DataFrame with correct columns but no rows returns None."""
        cluster_app.state.resource_group_assignments = None
        empty_df = pd.DataFrame(columns=["region", "cpa_mw", "cf", "lcoe"])
        cluster_app.state.uploaded_lcoe_onshorewind = empty_df
        cluster_app.state.uploaded_lcoe_solar = None

        result = cluster_app._load_resource_group_lcoe_df("onshorewind")

        assert result is None

    def test_load_returns_none_for_empty_assignments(self, cluster_app):
        """Assignments present but no rows matching the requested tech returns None."""
        # Only solar rows — no onshorewind rows
        assignments = _make_assignments_df(techs=["solar"])
        cluster_app.state.resource_group_assignments = assignments

        result = cluster_app._load_resource_group_lcoe_df("onshorewind")

        assert result is None
