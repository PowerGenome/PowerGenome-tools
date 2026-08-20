"""
Tests for demand visualization functions added to cluster_app.py.

Covers:
- _get_model_years_for_demand()
- _get_demand_display_df(scenario)
- _render_demand_bar_chart(df)
- _render_demand_line_chart(df)
- _get_demand_region_avg(scenario)
- _interpolate_color(val, vmin, vmax)
- populate_demand_scenario_select(event)
- on_render_demand(event)

Uses the same mock-PyScript technique as test_cluster_app_algorithms.py.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Session-scoped fixture: load cluster_app with mocked dependencies
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cluster_app():
    """Load cluster_app module with mocked js/PyScript dependencies."""
    module_names = [
        "js",
        "pyodide",
        "pyodide.ffi",
        "renewables_utils",
        "visualization_utils",
        "fast_interconnection",
        "fast_interconnection.fast_assign",
        "fast_interconnection.resource_groups",
        "cluster_app",
    ]
    original_modules = {name: sys.modules.get(name) for name in module_names}
    web_dir = Path(__file__).parent.parent / "web"

    try:
        # Add web/ to sys.path so cluster_app.py can import its siblings
        if str(web_dir) not in sys.path:
            sys.path.insert(0, str(web_dir))

        mock_js = MagicMock()
        mock_ffi = MagicMock()
        mock_ffi.create_proxy = lambda x: x
        mock_ffi.to_js = lambda x: x
        mock_ffi.JsProxy = object

        sys.modules["js"] = mock_js
        sys.modules["pyodide"] = MagicMock()
        sys.modules["pyodide.ffi"] = mock_ffi

        mock_ru = MagicMock()
        mock_ru.optimize_cluster_allocation = lambda region_lcoe_data, bins, target: {
            r: 1 for r in bins
        }
        sys.modules["renewables_utils"] = mock_ru

        mock_vu = MagicMock()
        import importlib.util as _ilu
        _vu_spec = _ilu.spec_from_file_location("visualization_utils", web_dir / "visualization_utils.py")
        _vu_mod = _ilu.module_from_spec(_vu_spec)
        _vu_spec.loader.exec_module(_vu_mod)
        CLUSTER_COLORS = _vu_mod.CLUSTER_COLORS
        GROUP_OUTLINE_COLORS = _vu_mod.GROUP_OUTLINE_COLORS
        lighten_color = _vu_mod.lighten_color

        mock_vu.lighten_color = lighten_color
        mock_vu.CLUSTER_COLORS = CLUSTER_COLORS
        mock_vu.GROUP_OUTLINE_COLORS = GROUP_OUTLINE_COLORS
        sys.modules["visualization_utils"] = mock_vu

        sys.modules["fast_interconnection"] = MagicMock()
        sys.modules["fast_interconnection.fast_assign"] = MagicMock()
        mock_rg = MagicMock()
        mock_rg.DEFAULT_PROFILE_PATHS = {}
        mock_rg.build_assigned_df = MagicMock(return_value=None)
        mock_rg.build_resource_group_json = MagicMock(return_value={})
        sys.modules["fast_interconnection.resource_groups"] = mock_rg

        mock_js.L = MagicMock()
        mock_js.document = MagicMock()
        mock_js.window = MagicMock()
        mock_js.fetch = MagicMock()
        mock_js.Uint8Array = MagicMock()
        mock_js.globalThis = MagicMock()

        web_dir = Path(__file__).parent.parent / "web"
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
# Shared helpers / fixtures
# ---------------------------------------------------------------------------


def _make_demand_df(
    regions=("BA1", "BA2"),
    years=(2030, 2040),
    scenarios=("base",),
    weather_years=(2012, 2013),
    mwh_per_cell=1_000_000,
):
    """Build a minimal demand_summary_df for testing."""
    rows = []
    for scenario in scenarios:
        for region in regions:
            for year in years:
                for wy in weather_years:
                    rows.append(
                        {
                            "region": region,
                            "year": year,
                            "scenario": scenario,
                            "weather_year": wy,
                            "annual_demand_mwh": float(mwh_per_cell),
                        }
                    )
    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def reset_state(cluster_app):
    """Reset relevant AppState fields before each test."""
    cluster_app.state.demand_summary_df = None
    cluster_app.state.ba_to_region = {}
    yield
    cluster_app.state.demand_summary_df = None
    cluster_app.state.ba_to_region = {}


# ---------------------------------------------------------------------------
# Helpers to control _get_model_years_for_demand() output
# ---------------------------------------------------------------------------


def _mock_no_model_years(cluster_app):
    """Make _get_model_years_for_demand() return [] (no filtering)."""
    cluster_app.document.getElementById.return_value = None


def _mock_model_years(cluster_app, years):
    """Make _get_model_years_for_demand() return the given list of years."""
    el = MagicMock()
    el.value = ", ".join(str(y) for y in years)
    cluster_app.document.getElementById.return_value = el


# ---------------------------------------------------------------------------
# _get_model_years_for_demand
# ---------------------------------------------------------------------------


class TestGetModelYearsForDemand:
    """Tests for _get_model_years_for_demand."""

    def test_returns_sorted_list(self, cluster_app):
        _mock_model_years(cluster_app, [2040, 2030, 2035])
        result = cluster_app._get_model_years_for_demand()
        assert result == [2030, 2035, 2040]

    def test_returns_empty_when_dom_element_missing(self, cluster_app):
        _mock_no_model_years(cluster_app)
        result = cluster_app._get_model_years_for_demand()
        assert result == []

    def test_returns_empty_on_exception(self, cluster_app):
        # Simulate _get_model_years_from_dom raising an exception
        cluster_app.document.getElementById.side_effect = RuntimeError("DOM error")
        result = cluster_app._get_model_years_for_demand()
        assert result == []
        # Restore side_effect for subsequent tests
        cluster_app.document.getElementById.side_effect = None

    def test_single_year(self, cluster_app):
        _mock_model_years(cluster_app, [2030])
        result = cluster_app._get_model_years_for_demand()
        assert result == [2030]


# ---------------------------------------------------------------------------
# _get_demand_display_df
# ---------------------------------------------------------------------------


class TestGetDemandDisplayDf:
    """Tests for _get_demand_display_df."""

    def test_returns_none_when_no_demand_data(self, cluster_app):
        _mock_no_model_years(cluster_app)
        result = cluster_app._get_demand_display_df("base")
        assert result is None

    def test_returns_none_for_unknown_scenario(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(scenarios=("base",))
        _mock_no_model_years(cluster_app)
        result = cluster_app._get_demand_display_df("nonexistent")
        assert result is None

    def test_basic_filtering_and_aggregation(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(
            regions=("BA1", "BA2"),
            years=(2030, 2040),
            scenarios=("base",),
            weather_years=(2012, 2013),
            mwh_per_cell=2_000_000,
        )
        _mock_no_model_years(cluster_app)
        df = cluster_app._get_demand_display_df("base")

        assert df is not None
        # Should have 2 regions × 2 years = 4 rows
        assert len(df) == 4
        assert set(df["region"]) == {"BA1", "BA2"}
        assert set(df["year"]) == {2030, 2040}
        assert "avg_demand_twh" in df.columns
        # 2_000_000 MWh / 1e6 = 2.0 TWh
        assert pytest.approx(df["avg_demand_twh"].iloc[0], abs=1e-9) == 2.0

    def test_averages_over_weather_years(self, cluster_app):
        """Different weather years should be averaged, not summed."""
        rows = [
            {"region": "BA1", "year": 2030, "scenario": "base", "weather_year": 2012,
             "annual_demand_mwh": 1_000_000.0},
            {"region": "BA1", "year": 2030, "scenario": "base", "weather_year": 2013,
             "annual_demand_mwh": 3_000_000.0},
        ]
        cluster_app.state.demand_summary_df = pd.DataFrame(rows)
        _mock_no_model_years(cluster_app)
        df = cluster_app._get_demand_display_df("base")

        assert df is not None
        assert len(df) == 1
        # Average of 1 and 3 = 2.0 TWh
        assert pytest.approx(df["avg_demand_twh"].iloc[0], abs=1e-9) == 2.0

    def test_converts_mwh_to_twh(self, cluster_app):
        rows = [
            {"region": "BA1", "year": 2030, "scenario": "base", "weather_year": 2012,
             "annual_demand_mwh": 5_000_000.0},
        ]
        cluster_app.state.demand_summary_df = pd.DataFrame(rows)
        _mock_no_model_years(cluster_app)
        df = cluster_app._get_demand_display_df("base")

        assert df is not None
        assert pytest.approx(df["avg_demand_twh"].iloc[0], abs=1e-9) == 5.0

    def test_aggregates_bas_to_model_regions(self, cluster_app):
        """When ba_to_region is set, BAs should be summed into model regions."""
        # Use lowercase region names to match how ba_to_region keys are lowercased
        cluster_app.state.demand_summary_df = _make_demand_df(
            regions=("ba1", "ba2"),
            years=(2030,),
            scenarios=("base",),
            weather_years=(2012,),
            mwh_per_cell=1_000_000,
        )
        cluster_app.state.ba_to_region = {"BA1": "RegionA", "BA2": "RegionA"}
        _mock_no_model_years(cluster_app)
        df = cluster_app._get_demand_display_df("base")

        assert df is not None
        # Both BAs go to RegionA, so 1 row
        assert len(df) == 1
        assert df.iloc[0]["region"] == "RegionA"
        # 1.0 TWh + 1.0 TWh = 2.0 TWh
        assert pytest.approx(df.iloc[0]["avg_demand_twh"], abs=1e-9) == 2.0

    def test_ba_to_region_case_insensitive_keys(self, cluster_app):
        """ba_to_region lookup uses lowercase keys for matching."""
        rows = [
            {"region": "ba1", "year": 2030, "scenario": "base", "weather_year": 2012,
             "annual_demand_mwh": 1_000_000.0},
        ]
        cluster_app.state.demand_summary_df = pd.DataFrame(rows)
        # Key is uppercase but should match lowercase region
        cluster_app.state.ba_to_region = {"BA1": "RegionA"}
        _mock_no_model_years(cluster_app)
        df = cluster_app._get_demand_display_df("base")

        assert df is not None
        assert len(df) == 1
        assert df.iloc[0]["region"] == "RegionA"

    def test_ba_to_region_drops_unmapped_bas(self, cluster_app):
        """BAs not present in ba_to_region are dropped."""
        rows = [
            {"region": "ba1", "year": 2030, "scenario": "base", "weather_year": 2012,
             "annual_demand_mwh": 1_000_000.0},
            {"region": "ba2", "year": 2030, "scenario": "base", "weather_year": 2012,
             "annual_demand_mwh": 1_000_000.0},
        ]
        cluster_app.state.demand_summary_df = pd.DataFrame(rows)
        # Only BA1 is mapped; BA2 should be dropped
        cluster_app.state.ba_to_region = {"BA1": "RegionA"}
        _mock_no_model_years(cluster_app)
        df = cluster_app._get_demand_display_df("base")

        assert df is not None
        assert len(df) == 1
        assert df.iloc[0]["region"] == "RegionA"

    def test_returns_none_when_all_bas_unmapped(self, cluster_app):
        rows = [
            {"region": "BA1", "year": 2030, "scenario": "base", "weather_year": 2012,
             "annual_demand_mwh": 1_000_000.0},
        ]
        cluster_app.state.demand_summary_df = pd.DataFrame(rows)
        cluster_app.state.ba_to_region = {"BA_UNKNOWN": "RegionA"}
        _mock_no_model_years(cluster_app)
        result = cluster_app._get_demand_display_df("base")
        assert result is None

    def test_filters_to_model_years(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(
            regions=("BA1",),
            years=(2030, 2035, 2040),
            scenarios=("base",),
            weather_years=(2012,),
        )
        _mock_model_years(cluster_app, [2030, 2040])
        df = cluster_app._get_demand_display_df("base")

        assert df is not None
        assert set(df["year"]) == {2030, 2040}
        # 2035 must be excluded
        assert 2035 not in df["year"].values

    def test_returns_none_after_model_year_filter_empties_df(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(
            regions=("BA1",),
            years=(2030,),
            scenarios=("base",),
            weather_years=(2012,),
        )
        # Model year that doesn't exist in the data
        _mock_model_years(cluster_app, [2050])
        result = cluster_app._get_demand_display_df("base")
        assert result is None

    def test_isolates_scenario_correctly(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(
            regions=("BA1",),
            years=(2030,),
            scenarios=("base", "high"),
            weather_years=(2012,),
            mwh_per_cell=1_000_000,
        )
        _mock_no_model_years(cluster_app)
        df = cluster_app._get_demand_display_df("high")

        assert df is not None
        assert len(df) == 1
        assert df.iloc[0]["region"] == "BA1"

    def test_multiple_model_regions_remain_separate(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(
            regions=("ba1", "ba2", "ba3"),
            years=(2030,),
            scenarios=("base",),
            weather_years=(2012,),
        )
        cluster_app.state.ba_to_region = {"BA1": "R1", "BA2": "R2", "BA3": "R1"}
        _mock_no_model_years(cluster_app)
        df = cluster_app._get_demand_display_df("base")

        assert df is not None
        assert set(df["region"]) == {"R1", "R2"}
        r1_row = df[df["region"] == "R1"]
        # BA1 + BA3 = 2 TWh
        assert pytest.approx(r1_row["avg_demand_twh"].iloc[0], abs=1e-9) == 2.0


# ---------------------------------------------------------------------------
# _render_demand_bar_chart
# ---------------------------------------------------------------------------


class TestRenderDemandBarChart:
    """Tests for _render_demand_bar_chart."""

    def _simple_df(self):
        return pd.DataFrame(
            {
                "region": ["BA1", "BA1", "BA2", "BA2"],
                "year": [2030, 2040, 2030, 2040],
                "avg_demand_twh": [1.0, 1.5, 2.0, 2.5],
            }
        )

    def test_returns_svg_string(self, cluster_app):
        result = cluster_app._render_demand_bar_chart(self._simple_df())
        assert isinstance(result, str)
        assert result.startswith("<svg")
        assert "</svg>" in result

    def test_svg_contains_bars(self, cluster_app):
        result = cluster_app._render_demand_bar_chart(self._simple_df())
        # Should have <rect> elements for each bar
        assert "<rect" in result

    def test_svg_contains_region_labels(self, cluster_app):
        result = cluster_app._render_demand_bar_chart(self._simple_df())
        assert "BA1" in result
        assert "BA2" in result

    def test_svg_contains_year_labels_in_legend(self, cluster_app):
        result = cluster_app._render_demand_bar_chart(self._simple_df())
        assert "2030" in result
        assert "2040" in result

    def test_svg_contains_twh_axis_label(self, cluster_app):
        result = cluster_app._render_demand_bar_chart(self._simple_df())
        assert "TWh" in result

    def test_empty_regions_returns_no_data_message(self, cluster_app):
        df = pd.DataFrame({"region": [], "year": [], "avg_demand_twh": []})
        result = cluster_app._render_demand_bar_chart(df)
        assert "<em>" in result
        assert "No data" in result

    def test_limits_to_20_regions(self, cluster_app):
        regions = [f"BA{i:02d}" for i in range(25)]
        rows = []
        for r in regions:
            rows.append({"region": r, "year": 2030, "avg_demand_twh": 1.0})
        df = pd.DataFrame(rows)
        result = cluster_app._render_demand_bar_chart(df)
        # Regions beyond 20 should not appear
        assert "BA20" not in result
        assert "BA00" in result

    def test_single_region_single_year(self, cluster_app):
        df = pd.DataFrame({"region": ["BA1"], "year": [2030], "avg_demand_twh": [3.5]})
        result = cluster_app._render_demand_bar_chart(df)
        assert "<svg" in result
        assert "BA1" in result
        assert "3.50" in result

    def test_title_attribute_contains_value(self, cluster_app):
        df = pd.DataFrame({"region": ["MyBA"], "year": [2030], "avg_demand_twh": [7.25]})
        result = cluster_app._render_demand_bar_chart(df)
        assert "7.25 TWh" in result

    def test_zero_max_value_does_not_crash(self, cluster_app):
        df = pd.DataFrame({"region": ["BA1"], "year": [2030], "avg_demand_twh": [0.0]})
        result = cluster_app._render_demand_bar_chart(df)
        assert "<svg" in result


# ---------------------------------------------------------------------------
# _render_demand_line_chart
# ---------------------------------------------------------------------------


class TestRenderDemandLineChart:
    """Tests for _render_demand_line_chart."""

    def _simple_df(self, n_years=3):
        years = list(range(2030, 2030 + n_years))
        rows = []
        for r in ("BA1", "BA2"):
            for y in years:
                rows.append({"region": r, "year": y, "avg_demand_twh": float(y - 2025)})
        return pd.DataFrame(rows)

    def test_returns_svg_string(self, cluster_app):
        result = cluster_app._render_demand_line_chart(self._simple_df())
        assert isinstance(result, str)
        assert result.startswith("<svg")
        assert "</svg>" in result

    def test_svg_contains_polyline(self, cluster_app):
        result = cluster_app._render_demand_line_chart(self._simple_df())
        assert "<polyline" in result

    def test_svg_contains_region_labels_in_legend(self, cluster_app):
        result = cluster_app._render_demand_line_chart(self._simple_df())
        assert "BA1" in result
        assert "BA2" in result

    def test_svg_contains_year_axis_labels(self, cluster_app):
        result = cluster_app._render_demand_line_chart(self._simple_df())
        assert "2030" in result

    def test_svg_contains_twh_label(self, cluster_app):
        result = cluster_app._render_demand_line_chart(self._simple_df())
        assert "TWh" in result

    def test_empty_df_returns_no_data_message(self, cluster_app):
        df = pd.DataFrame({"region": [], "year": [], "avg_demand_twh": []})
        result = cluster_app._render_demand_line_chart(df)
        assert "<em>" in result
        assert "No data" in result

    def test_single_point_renders_circle(self, cluster_app):
        """A region with only one data point should render a circle, not a polyline."""
        df = pd.DataFrame({"region": ["BA1"], "year": [2030], "avg_demand_twh": [2.0]})
        result = cluster_app._render_demand_line_chart(df)
        assert "<circle" in result

    def test_limits_to_15_regions(self, cluster_app):
        rows = []
        for i in range(20):
            rows.append({"region": f"BA{i:02d}", "year": 2030, "avg_demand_twh": 1.0})
            rows.append({"region": f"BA{i:02d}", "year": 2035, "avg_demand_twh": 1.5})
        df = pd.DataFrame(rows)
        result = cluster_app._render_demand_line_chart(df)
        # 16th+ region sorted alphabetically should not appear
        assert "BA15" not in result
        assert "BA00" in result

    def test_svg_has_axes(self, cluster_app):
        result = cluster_app._render_demand_line_chart(self._simple_df())
        assert "<line" in result

    def test_zero_demand_does_not_crash(self, cluster_app):
        df = pd.DataFrame(
            {"region": ["BA1", "BA1"], "year": [2030, 2035], "avg_demand_twh": [0.0, 0.0]}
        )
        result = cluster_app._render_demand_line_chart(df)
        assert "<svg" in result


# ---------------------------------------------------------------------------
# _get_demand_region_avg
# ---------------------------------------------------------------------------


class TestGetDemandRegionAvg:
    """Tests for _get_demand_region_avg."""

    def test_returns_empty_dict_when_no_data(self, cluster_app):
        result = cluster_app._get_demand_region_avg("base")
        assert result == {}

    def test_returns_empty_dict_for_unknown_scenario(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(scenarios=("base",))
        _mock_no_model_years(cluster_app)
        result = cluster_app._get_demand_region_avg("nonexistent")
        assert result == {}

    def test_basic_avg_per_region(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(
            regions=("BA1", "BA2"),
            years=(2030,),
            scenarios=("base",),
            weather_years=(2012,),
            mwh_per_cell=2_000_000,
        )
        _mock_no_model_years(cluster_app)
        result = cluster_app._get_demand_region_avg("base")

        assert set(result.keys()) == {"BA1", "BA2"}
        assert pytest.approx(result["BA1"], abs=1e-9) == 2.0
        assert pytest.approx(result["BA2"], abs=1e-9) == 2.0

    def test_averages_over_years_and_weather_years(self, cluster_app):
        rows = [
            {"region": "BA1", "year": 2030, "scenario": "base", "weather_year": 2012,
             "annual_demand_mwh": 1_000_000.0},
            {"region": "BA1", "year": 2030, "scenario": "base", "weather_year": 2013,
             "annual_demand_mwh": 3_000_000.0},
            {"region": "BA1", "year": 2040, "scenario": "base", "weather_year": 2012,
             "annual_demand_mwh": 2_000_000.0},
            {"region": "BA1", "year": 2040, "scenario": "base", "weather_year": 2013,
             "annual_demand_mwh": 4_000_000.0},
        ]
        cluster_app.state.demand_summary_df = pd.DataFrame(rows)
        _mock_no_model_years(cluster_app)
        result = cluster_app._get_demand_region_avg("base")

        # Mean over all 4 rows: (1+3+2+4)/4 = 2.5 TWh
        assert pytest.approx(result["BA1"], abs=1e-9) == 2.5

    def test_aggregates_bas_to_model_regions(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(
            regions=("ba1", "ba2"),
            years=(2030,),
            scenarios=("base",),
            weather_years=(2012,),
            mwh_per_cell=1_000_000,
        )
        cluster_app.state.ba_to_region = {"BA1": "RegionA", "BA2": "RegionA"}
        _mock_no_model_years(cluster_app)
        result = cluster_app._get_demand_region_avg("base")

        assert "RegionA" in result
        assert "BA1" not in result
        assert "BA2" not in result
        # 1.0 + 1.0 = 2.0 TWh
        assert pytest.approx(result["RegionA"], abs=1e-9) == 2.0

    def test_returns_empty_when_all_bas_unmapped(self, cluster_app):
        rows = [
            {"region": "BA1", "year": 2030, "scenario": "base", "weather_year": 2012,
             "annual_demand_mwh": 1_000_000.0},
        ]
        cluster_app.state.demand_summary_df = pd.DataFrame(rows)
        cluster_app.state.ba_to_region = {"BA_UNKNOWN": "RegionA"}
        _mock_no_model_years(cluster_app)
        result = cluster_app._get_demand_region_avg("base")
        assert result == {}

    def test_filters_to_model_years(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(
            regions=("BA1",),
            years=(2030, 2040),
            scenarios=("base",),
            weather_years=(2012,),
            mwh_per_cell=1_000_000,
        )
        # Only year 2030 is a model year
        _mock_model_years(cluster_app, [2030])
        result = cluster_app._get_demand_region_avg("base")

        assert "BA1" in result
        # Only 2030 included → average is exactly 1.0 TWh
        assert pytest.approx(result["BA1"], abs=1e-9) == 1.0

    def test_returns_empty_after_model_year_filter(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(
            regions=("BA1",),
            years=(2030,),
            scenarios=("base",),
            weather_years=(2012,),
        )
        _mock_model_years(cluster_app, [2050])  # no overlap
        result = cluster_app._get_demand_region_avg("base")
        assert result == {}

    def test_multiple_model_regions(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(
            regions=("ba1", "ba2", "ba3"),
            years=(2030,),
            scenarios=("base",),
            weather_years=(2012,),
            mwh_per_cell=1_000_000,
        )
        cluster_app.state.ba_to_region = {"BA1": "R1", "BA2": "R2", "BA3": "R1"}
        _mock_no_model_years(cluster_app)
        result = cluster_app._get_demand_region_avg("base")

        assert set(result.keys()) == {"R1", "R2"}
        assert pytest.approx(result["R1"], abs=1e-9) == 2.0
        assert pytest.approx(result["R2"], abs=1e-9) == 1.0


# ---------------------------------------------------------------------------
# _interpolate_color
# ---------------------------------------------------------------------------


class TestInterpolateColor:
    """Tests for _interpolate_color."""

    def test_at_minimum_returns_light_blue(self, cluster_app):
        # Light blue: rgb(219, 233, 247)
        result = cluster_app._interpolate_color(0.0, 0.0, 1.0)
        assert result == "rgb(219,233,247)"

    def test_at_maximum_returns_dark_blue(self, cluster_app):
        # Dark blue: rgb(26, 86, 196)
        result = cluster_app._interpolate_color(1.0, 0.0, 1.0)
        assert result == "rgb(26,86,196)"

    def test_at_midpoint_interpolates(self, cluster_app):
        result = cluster_app._interpolate_color(0.5, 0.0, 1.0)
        # Midpoint: int(219 + 0.5*(26-219)) = int(219 - 96.5) = int(122.5) = 122
        r = int(219 + 0.5 * (26 - 219))
        g = int(233 + 0.5 * (86 - 233))
        b = int(247 + 0.5 * (196 - 247))
        assert result == f"rgb({r},{g},{b})"

    def test_vmax_equals_vmin_returns_midpoint(self, cluster_app):
        """When vmax == vmin, t=0.5 should be used."""
        result = cluster_app._interpolate_color(5.0, 5.0, 5.0)
        r = int(219 + 0.5 * (26 - 219))
        g = int(233 + 0.5 * (86 - 233))
        b = int(247 + 0.5 * (196 - 247))
        assert result == f"rgb({r},{g},{b})"

    def test_value_below_vmin_clamps_to_light_blue(self, cluster_app):
        result = cluster_app._interpolate_color(-10.0, 0.0, 1.0)
        assert result == "rgb(219,233,247)"

    def test_value_above_vmax_clamps_to_dark_blue(self, cluster_app):
        result = cluster_app._interpolate_color(100.0, 0.0, 1.0)
        assert result == "rgb(26,86,196)"

    def test_returns_rgb_string_format(self, cluster_app):
        result = cluster_app._interpolate_color(0.3, 0.0, 1.0)
        assert result.startswith("rgb(")
        assert result.endswith(")")
        # Should contain exactly 3 integer components
        inner = result[4:-1]
        parts = inner.split(",")
        assert len(parts) == 3
        for p in parts:
            assert p.strip().isdigit()

    @pytest.mark.parametrize("val,vmin,vmax", [
        (0.0, 0.0, 10.0),
        (10.0, 0.0, 10.0),
        (5.0, 0.0, 10.0),
        (2.5, 0.0, 10.0),
    ])
    def test_values_in_valid_rgb_range(self, cluster_app, val, vmin, vmax):
        result = cluster_app._interpolate_color(val, vmin, vmax)
        inner = result[4:-1]
        for component_str in inner.split(","):
            component = int(component_str)
            assert 0 <= component <= 255


# ---------------------------------------------------------------------------
# populate_demand_scenario_select
# ---------------------------------------------------------------------------


class TestPopulateDemandScenarioSelect:
    """Tests for populate_demand_scenario_select."""

    def _make_sel_el(self):
        el = MagicMock()
        el.innerHTML = ""
        return el

    def test_sets_no_data_when_demand_df_is_none(self, cluster_app):
        sel_el = self._make_sel_el()
        cluster_app.document.getElementById.return_value = sel_el
        cluster_app.state.demand_summary_df = None

        cluster_app.populate_demand_scenario_select()

        assert "No data loaded" in sel_el.innerHTML

    def test_populates_with_sorted_scenarios(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(
            scenarios=("high", "base", "low"),
        )
        sel_el = self._make_sel_el()
        cluster_app.document.getElementById.return_value = sel_el

        cluster_app.populate_demand_scenario_select()

        html_str = sel_el.innerHTML
        # All three scenarios should appear as <option> elements
        assert 'value="base"' in html_str
        assert 'value="high"' in html_str
        assert 'value="low"' in html_str
        # Sorted order: base < high < low
        assert html_str.index("base") < html_str.index("high")
        assert html_str.index("high") < html_str.index("low")

    def test_no_op_when_element_missing(self, cluster_app):
        """Should return gracefully when the DOM element is not found."""
        cluster_app.document.getElementById.return_value = None
        cluster_app.state.demand_summary_df = _make_demand_df()
        # Should not raise
        cluster_app.populate_demand_scenario_select()

    def test_html_escapes_scenario_names(self, cluster_app):
        """Scenario names with special characters should be HTML-escaped."""
        rows = [
            {"region": "BA1", "year": 2030, "scenario": "<high>&special",
             "weather_year": 2012, "annual_demand_mwh": 1e6},
        ]
        cluster_app.state.demand_summary_df = pd.DataFrame(rows)
        sel_el = self._make_sel_el()
        cluster_app.document.getElementById.return_value = sel_el

        cluster_app.populate_demand_scenario_select()

        # Raw < and > should not appear unescaped in innerHTML
        assert "<high>" not in sel_el.innerHTML
        assert "&lt;" in sel_el.innerHTML or "special" in sel_el.innerHTML

    def test_single_scenario(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(scenarios=("only_one",))
        sel_el = self._make_sel_el()
        cluster_app.document.getElementById.return_value = sel_el

        cluster_app.populate_demand_scenario_select()

        assert 'value="only_one"' in sel_el.innerHTML


# ---------------------------------------------------------------------------
# on_render_demand
# ---------------------------------------------------------------------------


class TestOnRenderDemand:
    """Tests for on_render_demand."""

    def _make_mock_el(self, value=None):
        el = MagicMock()
        el.value = value or ""
        el.innerHTML = ""
        el.style = MagicMock()
        el.style.display = ""
        el.textContent = ""
        el.className = ""
        return el

    def _setup_dom(self, cluster_app, scenario="base"):
        """Return a side_effect callable that yields named DOM elements."""
        sel_el = self._make_mock_el(value=scenario)
        status_el = self._make_mock_el()
        bar_section = self._make_mock_el()
        bar_container = self._make_mock_el()
        line_section = self._make_mock_el()
        line_container = self._make_mock_el()
        map_section = self._make_mock_el()

        element_map = {
            "demandScenarioSelect": sel_el,
            "demandStatus": status_el,
            "demandBarSection": bar_section,
            "demandBarChartContainer": bar_container,
            "demandLineSection": line_section,
            "demandLineChartContainer": line_container,
            "demandMapSection": map_section,
            "modelYears": None,  # no model year filtering
        }
        cluster_app.document.getElementById.side_effect = lambda id_: element_map.get(id_)
        return element_map

    def test_sets_error_status_when_no_demand_data(self, cluster_app):
        dom = self._setup_dom(cluster_app)
        cluster_app.state.demand_summary_df = None

        cluster_app.on_render_demand()

        status_el = dom["demandStatus"]
        assert "error" in status_el.className or "not loaded" in status_el.textContent.lower()

    def test_sets_error_when_no_scenario_selected(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df()
        dom = self._setup_dom(cluster_app, scenario="")  # no selection

        cluster_app.on_render_demand()

        status_el = dom["demandStatus"]
        assert "error" in status_el.className or "select" in status_el.textContent.lower()

    def test_renders_bar_chart_when_data_available(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(
            regions=("BA1", "BA2"),
            years=(2030, 2040),
            scenarios=("base",),
        )
        dom = self._setup_dom(cluster_app, scenario="base")

        cluster_app.on_render_demand()

        bar_container = dom["demandBarChartContainer"]
        assert "<svg" in bar_container.innerHTML

    def test_renders_line_chart_when_data_available(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(
            regions=("BA1", "BA2"),
            years=(2030, 2040),
            scenarios=("base",),
        )
        dom = self._setup_dom(cluster_app, scenario="base")

        cluster_app.on_render_demand()

        line_container = dom["demandLineChartContainer"]
        assert "<svg" in line_container.innerHTML

    def test_hides_bar_section_when_no_data_for_scenario(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(scenarios=("base",))
        dom = self._setup_dom(cluster_app, scenario="missing_scenario")

        cluster_app.on_render_demand()

        bar_section = dom["demandBarSection"]
        assert bar_section.style.display == "none"

    def test_hides_line_section_when_no_data_for_scenario(self, cluster_app):
        cluster_app.state.demand_summary_df = _make_demand_df(scenarios=("base",))
        dom = self._setup_dom(cluster_app, scenario="missing_scenario")

        cluster_app.on_render_demand()

        line_section = dom["demandLineSection"]
        assert line_section.style.display == "none"
