"""
Comprehensive tests for the per-year scenario management feature.

Covers:
- Pure logic: _has_year_specific_resources, _get_year_specific_years,
  _build_new_resources_for_year, _build_resource_modifiers_for_year,
  _build_modified_new_resources_for_year, _year_badge_html,
  generate_resources_settings (year-keyed format for PowerGenome v0.8.0-beta)
- DOM-interacting: populate_resource_year_selects, _get_resource_planning_year,
  _check_year_default_warning
- Integration: full flows, resources.yml year-keyed output, build_settings_yamls
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixture: load cluster_app with mocked browser globals
# ---------------------------------------------------------------------------


@pytest.fixture()
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
    web_dir = None

    try:
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
# Helpers
# ---------------------------------------------------------------------------


def _make_resource(
    tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
):
    return {
        "technology": tech,
        "tech_detail": detail,
        "cost_case": case,
        "size_mw": size,
        "planning_year": year,
    }


def _make_modified(
    key,
    tech="NaturalGas",
    detail="CC",
    case="Moderate",
    size=500,
    year="all",
    new_tech=None,
    new_detail=None,
    new_case=None,
    fuel_type=None,
    attr_modifiers=None,
):
    """Build a modified_new_resources entry dict (value, not including key)."""
    return {
        "technology": tech,
        "tech_detail": detail,
        "cost_case": case,
        "size_mw": size,
        "planning_year": year,
        "new_technology": new_tech or tech,
        "new_tech_detail": new_detail or detail,
        "new_cost_case": new_case or case,
        "fuel_type": fuel_type,
        "attr_modifiers": attr_modifiers or {},
    }


def _mock_dom_element(value=""):
    """Return a minimal mock DOM element with a .value attribute and style."""
    el = MagicMock()
    el.value = value
    el.style = MagicMock()
    el.innerHTML = ""
    return el


def _setup_model_years_dom(cluster_app, years_str="2030, 2040"):
    """Wire up document.getElementById so modelYears returns the given string."""
    el_map = {"modelYears": _mock_dom_element(years_str)}

    def get_el(id_):
        return el_map.get(id_, MagicMock())

    cluster_app.document.getElementById = MagicMock(side_effect=get_el)
    return el_map


# ============================================================================
# A. Pure logic tests (no DOM needed)
# ============================================================================


class TestHasYearSpecificResources:
    """Tests for _has_year_specific_resources()."""

    def test_empty_state(self, cluster_app):
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {}
        assert cluster_app._has_year_specific_resources() is False

    def test_all_resources_are_all(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(year="all"),
            _make_resource(tech="UtilityPV", detail="Class1", year="all"),
        ]
        cluster_app.state.modified_new_resources = {}
        assert cluster_app._has_year_specific_resources() is False

    def test_one_year_specific_new_resource(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(year="all"),
            _make_resource(tech="UtilityPV", detail="Class1", year=2030),
        ]
        cluster_app.state.modified_new_resources = {}
        assert cluster_app._has_year_specific_resources() is True

    def test_one_year_specific_modified_resource(self, cluster_app):
        cluster_app.state.new_resources = [_make_resource(year="all")]
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified("k1", year=2040),
        }
        assert cluster_app._has_year_specific_resources() is True

    def test_modified_resource_missing_planning_year_defaults_all(self, cluster_app):
        """Modified resources without planning_year key should default to 'all'."""
        cluster_app.state.new_resources = []
        v = _make_modified("k1")
        del v["planning_year"]
        cluster_app.state.modified_new_resources = {"k1": v}
        assert cluster_app._has_year_specific_resources() is False


class TestGetYearSpecificYears:
    """Tests for _get_year_specific_years()."""

    def test_empty_state(self, cluster_app):
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {}
        assert cluster_app._get_year_specific_years() == []

    def test_single_year_from_new_resource(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(year="all"),
            _make_resource(tech="UtilityPV", year=2035),
        ]
        cluster_app.state.modified_new_resources = {}
        assert cluster_app._get_year_specific_years() == [2035]

    def test_multiple_years_from_both_sources(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(year=2040),
            _make_resource(year=2030),
        ]
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified("k1", year=2050),
        }
        result = cluster_app._get_year_specific_years()
        assert result == [2030, 2040, 2050]

    def test_deduplication(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(year=2030),
            _make_resource(tech="UtilityPV", year=2030),
        ]
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified("k1", year=2030),
        }
        assert cluster_app._get_year_specific_years() == [2030]

    def test_modified_missing_key_ignored(self, cluster_app):
        cluster_app.state.new_resources = []
        v = _make_modified("k1")
        del v["planning_year"]
        cluster_app.state.modified_new_resources = {"k1": v}
        assert cluster_app._get_year_specific_years() == []


class TestBuildNewResourcesForYear:
    """Tests for _build_new_resources_for_year(year)."""

    def test_base_only(self, cluster_app):
        """When no year-specific resources exist, only base resources appear."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
        ]
        result = cluster_app._build_new_resources_for_year(2030)
        assert result == [["NaturalGas", "CC", "Moderate", 500]]

    def test_year_specific_additions(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
        ]
        result = cluster_app._build_new_resources_for_year(2030)
        assert result == [
            ["NaturalGas", "CC", "Moderate", 500],
            ["UtilityPV", "Class1", "Moderate", 100],
        ]

    def test_mixed_years_filters_correctly(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
            _make_resource(
                tech="Nuclear", detail="Large", case="Moderate", size=1000, year=2040
            ),
        ]
        result_2030 = cluster_app._build_new_resources_for_year(2030)
        assert len(result_2030) == 2
        assert ["Nuclear", "Large", "Moderate", 1000] not in result_2030
        assert ["UtilityPV", "Class1", "Moderate", 100] in result_2030

        result_2040 = cluster_app._build_new_resources_for_year(2040)
        assert len(result_2040) == 2
        assert ["Nuclear", "Large", "Moderate", 1000] in result_2040
        assert ["UtilityPV", "Class1", "Moderate", 100] not in result_2040

    def test_empty_state(self, cluster_app):
        cluster_app.state.new_resources = []
        result = cluster_app._build_new_resources_for_year(2030)
        assert result == []

    def test_all_year_specific_no_base(self, cluster_app):
        """When resources have no 'all' base, the base portion is empty."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
        ]
        result = cluster_app._build_new_resources_for_year(2030)
        assert result == [["UtilityPV", "Class1", "Moderate", 100]]

    def test_unmatched_year_returns_base_only(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
        ]
        result = cluster_app._build_new_resources_for_year(2040)
        assert result == [["NaturalGas", "CC", "Moderate", 500]]


class TestBuildResourceModifiersForYear:
    """Tests for _build_resource_modifiers_for_year(year)."""

    def test_no_modifiers_returns_none(self, cluster_app):
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {}
        assert cluster_app._build_resource_modifiers_for_year(2030) is None

    def test_all_only_modifiers(self, cluster_app):
        """'All' modifiers appear for every year."""
        cluster_app.state.modified_new_resources = {
            "ng_cc": _make_modified("ng_cc", attr_modifiers={"heat_rate": 6.5}),
        }
        result = cluster_app._build_resource_modifiers_for_year(2030)
        assert result is not None
        assert "ng_cc" in result
        assert result["ng_cc"]["Heat_Rate_MMBTU_per_MWh"] == 6.5

    def test_year_specific_overrides(self, cluster_app):
        cluster_app.state.modified_new_resources = {
            "ng_cc_2030": _make_modified(
                "ng_cc_2030",
                year=2030,
                attr_modifiers={"fixed_o_m_mw": 42.0},
            ),
        }
        result = cluster_app._build_resource_modifiers_for_year(2030)
        assert result is not None
        assert result["ng_cc_2030"]["Fixed_OM_Cost_per_MWyr"] == 42.0

    def test_different_year_excluded(self, cluster_app):
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "ng_cc_2040": _make_modified(
                "ng_cc_2040",
                year=2040,
                attr_modifiers={"heat_rate": 7.0},
            ),
        }
        assert cluster_app._build_resource_modifiers_for_year(2030) is None

    def test_skips_identity_changes(self, cluster_app):
        """Entries that change technology/detail/case are not resource_modifiers."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified(
                "k1",
                tech="NaturalGas",
                new_tech="NewTech",
                attr_modifiers={"heat_rate": 6.0},
            ),
        }
        assert cluster_app._build_resource_modifiers_for_year(2030) is None

    def test_skips_fuel_type_new(self, cluster_app):
        """Entries with fuel_type='new' are not resource_modifiers."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified(
                "k1",
                fuel_type="new",
                attr_modifiers={"heat_rate": 6.0},
            ),
        }
        assert cluster_app._build_resource_modifiers_for_year(2030) is None

    def test_empty_attr_modifiers_skipped(self, cluster_app):
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified("k1", attr_modifiers={}),
        }
        assert cluster_app._build_resource_modifiers_for_year(2030) is None

    def test_no_attr_modifiers_key_skipped(self, cluster_app):
        cluster_app.state.new_resources = []
        v = _make_modified("k1")
        v["attr_modifiers"] = None
        cluster_app.state.modified_new_resources = {"k1": v}
        assert cluster_app._build_resource_modifiers_for_year(2030) is None

    def test_passthrough_key_not_in_mapping(self, cluster_app):
        """Keys not in _UI_TO_ATB_KEY pass through as-is (e.g. capex_mw)."""
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified(
                "k1",
                attr_modifiers={"capex_mw": 999.0},
            ),
        }
        result = cluster_app._build_resource_modifiers_for_year(2030)
        assert result["k1"]["capex_mw"] == 999.0

    def test_merge_all_and_year_specific(self, cluster_app):
        """Both 'all' and year-specific modifiers appear together."""
        cluster_app.state.modified_new_resources = {
            "base": _make_modified(
                "base", year="all", attr_modifiers={"heat_rate": 6.5}
            ),
            "yr2030": _make_modified(
                "yr2030", year=2030, attr_modifiers={"fixed_o_m_mw": 42.0}
            ),
        }
        result = cluster_app._build_resource_modifiers_for_year(2030)
        assert "base" in result
        assert "yr2030" in result


class TestBuildModifiedNewResourcesForYear:
    """Tests for _build_modified_new_resources_for_year(year)."""

    def test_no_modified_returns_none(self, cluster_app):
        cluster_app.state.modified_new_resources = {}
        assert cluster_app._build_modified_new_resources_for_year(2030) is None

    def test_attribute_only_excluded(self, cluster_app):
        """Entries with no identity change and no fuel change are excluded."""
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified("k1", attr_modifiers={"heat_rate": 6.0}),
        }
        assert cluster_app._build_modified_new_resources_for_year(2030) is None

    def test_identity_change_included(self, cluster_app):
        """Entries changing technology identity are included."""
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified(
                "k1",
                tech="NaturalGas",
                detail="CC",
                case="Moderate",
                size=500,
                new_tech="CustomGas",
                attr_modifiers={"heat_rate": 6.0},
            ),
        }
        result = cluster_app._build_modified_new_resources_for_year(2030)
        assert result is not None
        assert result["k1"]["new_technology"] == "CustomGas"
        assert result["k1"]["Heat_Rate_MMBTU_per_MWh"] == 6.0

    def test_fuel_type_new_included(self, cluster_app):
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified("k1", fuel_type="new"),
        }
        result = cluster_app._build_modified_new_resources_for_year(2030)
        assert result is not None
        assert "k1" in result

    def test_year_filtering(self, cluster_app):
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified("k1", year=2040, new_tech="CustomGas"),
        }
        assert cluster_app._build_modified_new_resources_for_year(2030) is None

    def test_all_entries_included_for_every_year(self, cluster_app):
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified("k1", year="all", new_tech="CustomGas"),
        }
        result = cluster_app._build_modified_new_resources_for_year(2030)
        assert result is not None
        result = cluster_app._build_modified_new_resources_for_year(2040)
        assert result is not None

    def test_merge_all_and_year_specific(self, cluster_app):
        cluster_app.state.modified_new_resources = {
            "base": _make_modified("base", year="all", fuel_type="new"),
            "yr2030": _make_modified("yr2030", year=2030, new_tech="Custom2030"),
        }
        result = cluster_app._build_modified_new_resources_for_year(2030)
        assert "base" in result
        assert "yr2030" in result

    def test_detail_change_included(self, cluster_app):
        """Changing just tech_detail counts as identity change."""
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified("k1", detail="CC", new_detail="CT"),
        }
        result = cluster_app._build_modified_new_resources_for_year(2030)
        assert result is not None
        assert result["k1"]["new_tech_detail"] == "CT"

    def test_case_change_included(self, cluster_app):
        """Changing just cost_case counts as identity change."""
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified("k1", case="Moderate", new_case="Advanced"),
        }
        result = cluster_app._build_modified_new_resources_for_year(2030)
        assert result is not None
        assert result["k1"]["new_cost_case"] == "Advanced"


class TestYearBadgeHtml:
    """Tests for _year_badge_html()."""

    def test_all_returns_empty(self, cluster_app):
        assert cluster_app._year_badge_html("all") == ""

    def test_year_returns_span(self, cluster_app):
        html = cluster_app._year_badge_html(2030)
        assert "<span" in html
        assert "2030" in html

    def test_badge_contains_styling(self, cluster_app):
        html = cluster_app._year_badge_html(2040)
        assert "background" in html
        assert "border-radius" in html


# ============================================================================
# A (continued). Generation functions (may need minimal DOM mocks)
# ============================================================================


def _setup_dom_for_resources_settings(cluster_app, model_years="2030, 2040"):
    """Wire up document.getElementById with all DOM elements required by
    generate_resources_settings()."""
    el_map = {
        "modelYears": _mock_dom_element(model_years),
        "targetUsdYear": _mock_dom_element("2024"),
        "atbYearSelect": _mock_dom_element("2024"),
        "utcOffset": _mock_dom_element("-5"),
        "planningYears": _mock_dom_element("2028, 2038"),
    }
    cluster_app.document.getElementById = MagicMock(
        side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
    )

    cluster_app.state.region_aggregations = {"R1": ["ba1"]}
    cluster_app.state.is_clustered = True
    cluster_app.state.ba_to_region = {"ba1": "R1"}
    cluster_app.state.plant_cluster_settings = {}
    cluster_app.state.renewables_clusters = None

    return el_map


class TestGenerateResourcesSettingsYearKeyed:
    """Tests for generate_resources_settings() year-keyed output (PowerGenome v0.8.0-beta)."""

    def test_no_year_specific_produces_flat_new_resources(self, cluster_app):
        """When all resources are 'all', new_resources is a plain list, not a dict."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
        ]
        cluster_app.state.modified_new_resources = {}
        _setup_dom_for_resources_settings(cluster_app)

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        new_res = parsed["new_resources"]
        assert isinstance(new_res, list), "new_resources should be a flat list when no year-specific resources"

    def test_year_specific_produces_keyed_new_resources(self, cluster_app):
        """When a resource is tagged 2030, new_resources is a dict with default and 2030 keys."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
        ]
        cluster_app.state.modified_new_resources = {}
        _setup_dom_for_resources_settings(cluster_app)

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        new_res = parsed["new_resources"]
        assert isinstance(new_res, dict), "new_resources should be a dict when year-specific resources exist"
        assert "default" in new_res
        assert 2030 in new_res

    def test_default_key_contains_base_resources(self, cluster_app):
        """The 'default' key of new_resources contains only 'all'-year resources."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
        ]
        cluster_app.state.modified_new_resources = {}
        _setup_dom_for_resources_settings(cluster_app)

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        default_list = parsed["new_resources"]["default"]
        assert ["NaturalGas", "CC", "Moderate", 500] in default_list
        assert ["UtilityPV", "Class1", "Moderate", 100] not in default_list

    def test_year_key_contains_base_plus_year_specific(self, cluster_app):
        """The year key of new_resources includes both 'all' and year-specific resources."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
        ]
        cluster_app.state.modified_new_resources = {}
        _setup_dom_for_resources_settings(cluster_app)

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        year_list = parsed["new_resources"][2030]
        assert ["NaturalGas", "CC", "Moderate", 500] in year_list
        assert ["UtilityPV", "Class1", "Moderate", 100] in year_list

    def test_multiple_years_produce_multiple_keys(self, cluster_app):
        """Two year-specific resources (2030, 2040) produce default, 2030, and 2040 keys."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
            _make_resource(
                tech="Wind", detail="Class3", case="Moderate", size=200, year=2040
            ),
        ]
        cluster_app.state.modified_new_resources = {}
        _setup_dom_for_resources_settings(cluster_app)

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        new_res = parsed["new_resources"]
        assert isinstance(new_res, dict)
        assert "default" in new_res
        assert 2030 in new_res
        assert 2040 in new_res

    def test_year_specific_resource_modifiers_are_keyed(self, cluster_app):
        """When year-specific resources exist, resource_modifiers is a dict with 'default' key."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
        ]
        cluster_app.state.modified_new_resources = {}
        _setup_dom_for_resources_settings(cluster_app)

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        resource_mods = parsed.get("resource_modifiers")
        assert resource_mods is not None
        assert isinstance(resource_mods, dict)
        assert "default" in resource_mods

    def test_year_specific_in_resource_modifiers(self, cluster_app):
        """Year-specific resources appear under their year key in resource_modifiers."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
        ]
        cluster_app.state.modified_new_resources = {}
        _setup_dom_for_resources_settings(cluster_app)

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        resource_mods = parsed["resource_modifiers"]
        # The 2030 year key should have an entry for UtilityPV (in addition to base)
        assert 2030 in resource_mods
        year_mods = resource_mods[2030]
        techs_in_year = [v.get("technology") for v in year_mods.values()]
        assert "UtilityPV" in techs_in_year


# ============================================================================
# B. DOM-interacting tests
# ============================================================================


class TestPopulateResourceYearSelects:
    """Tests for populate_resource_year_selects()."""

    def test_populates_both_selects(self, cluster_app):
        el_map = {}
        for sel_id in ("newResourceYearSelect", "modResourceYearSelect"):
            el_map[sel_id] = _mock_dom_element("all")
        el_map["modelYears"] = _mock_dom_element("2030, 2040")

        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, MagicMock())
        )
        cluster_app.populate_resource_year_selects()

        for sel_id in ("newResourceYearSelect", "modResourceYearSelect"):
            html = el_map[sel_id].innerHTML
            assert "all" in html or "All" in html
            assert "2030" in html
            assert "2040" in html

    def test_empty_model_years(self, cluster_app):
        el_map = {
            "newResourceYearSelect": _mock_dom_element("all"),
            "modResourceYearSelect": _mock_dom_element("all"),
            "modelYears": _mock_dom_element(""),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, MagicMock())
        )
        cluster_app.populate_resource_year_selects()

        # Should still have the "All" option
        for sel_id in ("newResourceYearSelect", "modResourceYearSelect"):
            html = el_map[sel_id].innerHTML
            assert "all" in html.lower()

    def test_missing_select_element_no_error(self, cluster_app):
        """If a select element doesn't exist, no error is raised."""
        el_map = {
            "modelYears": _mock_dom_element("2030"),
            "newResourceYearSelect": None,
            "modResourceYearSelect": None,
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_)
        )
        # Should not raise
        cluster_app.populate_resource_year_selects()

    def test_preserves_current_selection(self, cluster_app):
        el_map = {
            "newResourceYearSelect": _mock_dom_element("2030"),
            "modResourceYearSelect": _mock_dom_element("all"),
            "modelYears": _mock_dom_element("2030, 2040"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, MagicMock())
        )
        cluster_app.populate_resource_year_selects()

        html_new = el_map["newResourceYearSelect"].innerHTML
        assert "selected" in html_new
        # 2030 option should be selected
        assert (
            "value='2030' selected" in html_new
            or "value='2030'  selected" in html_new
            or ("2030" in html_new and "selected" in html_new)
        )


class TestGetResourcePlanningYear:
    """Tests for _get_resource_planning_year()."""

    def test_returns_all_for_default(self, cluster_app):
        el = _mock_dom_element("all")
        cluster_app.document.getElementById = MagicMock(return_value=el)
        assert cluster_app._get_resource_planning_year("newResourceYearSelect") == "all"

    def test_returns_int_for_year(self, cluster_app):
        el = _mock_dom_element("2030")
        cluster_app.document.getElementById = MagicMock(return_value=el)
        result = cluster_app._get_resource_planning_year("newResourceYearSelect")
        assert result == 2030
        assert isinstance(result, int)

    def test_returns_all_for_invalid(self, cluster_app):
        el = _mock_dom_element("invalid")
        cluster_app.document.getElementById = MagicMock(return_value=el)
        assert cluster_app._get_resource_planning_year("newResourceYearSelect") == "all"

    def test_returns_all_for_missing_element(self, cluster_app):
        cluster_app.document.getElementById = MagicMock(return_value=None)
        assert cluster_app._get_resource_planning_year("missing") == "all"


class TestCheckYearDefaultWarning:
    """Tests for _check_year_default_warning()."""

    def test_all_year_hides_warning(self, cluster_app):
        warn_el = _mock_dom_element()
        cluster_app.document.getElementById = MagicMock(return_value=warn_el)

        cluster_app._check_year_default_warning(
            "NaturalGas", "CC", "Moderate", "all", "warnEl"
        )
        assert warn_el.style.display == "none"

    def test_year_specific_with_all_counterpart_hides_warning(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(tech="NaturalGas", detail="CC", case="Moderate", year="all"),
        ]
        cluster_app.state.modified_new_resources = {}
        warn_el = _mock_dom_element()
        cluster_app.document.getElementById = MagicMock(return_value=warn_el)

        cluster_app._check_year_default_warning(
            "NaturalGas", "CC", "Moderate", 2030, "warnEl"
        )
        assert warn_el.style.display == "none"

    def test_year_specific_without_counterpart_shows_warning(self, cluster_app):
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {}
        warn_el = _mock_dom_element()
        cluster_app.document.getElementById = MagicMock(return_value=warn_el)

        cluster_app._check_year_default_warning(
            "NaturalGas", "CC", "Moderate", 2030, "warnEl"
        )
        assert warn_el.style.display == "block"
        assert "No" in warn_el.innerHTML
        assert "All (default)" in warn_el.innerHTML

    def test_counterpart_in_modified_resources(self, cluster_app):
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified(
                "k1",
                tech="NaturalGas",
                detail="CC",
                case="Moderate",
                year="all",
            ),
        }
        warn_el = _mock_dom_element()
        cluster_app.document.getElementById = MagicMock(return_value=warn_el)

        cluster_app._check_year_default_warning(
            "NaturalGas", "CC", "Moderate", 2030, "warnEl"
        )
        assert warn_el.style.display == "none"

    def test_missing_warning_element_no_error(self, cluster_app):
        cluster_app.document.getElementById = MagicMock(return_value=None)
        # Should not raise
        cluster_app._check_year_default_warning(
            "NaturalGas", "CC", "Moderate", 2030, "warnEl"
        )

    def test_different_tech_not_counted(self, cluster_app):
        """An 'all' counterpart must match tech, detail, and case."""
        cluster_app.state.new_resources = [
            _make_resource(tech="UtilityPV", detail="CC", case="Moderate", year="all"),
        ]
        cluster_app.state.modified_new_resources = {}
        warn_el = _mock_dom_element()
        cluster_app.document.getElementById = MagicMock(return_value=warn_el)

        cluster_app._check_year_default_warning(
            "NaturalGas", "CC", "Moderate", 2030, "warnEl"
        )
        assert warn_el.style.display == "block"


class TestGetModelYearsFromDom:
    """Tests for _get_model_years_from_dom()."""

    def test_parses_comma_separated(self, cluster_app):
        el = _mock_dom_element("2030, 2040, 2050")
        cluster_app.document.getElementById = MagicMock(return_value=el)
        result = cluster_app._get_model_years_from_dom()
        assert result == [2030, 2040, 2050]

    def test_empty_string(self, cluster_app):
        el = _mock_dom_element("")
        cluster_app.document.getElementById = MagicMock(return_value=el)
        assert cluster_app._get_model_years_from_dom() == []

    def test_missing_element(self, cluster_app):
        cluster_app.document.getElementById = MagicMock(return_value=None)
        assert cluster_app._get_model_years_from_dom() == []


# ============================================================================
# C. Integration tests
# ============================================================================


class TestIntegrationFullFlow:
    """End-to-end tests combining resource setup and year-keyed resources.yml generation."""

    def test_full_flow_with_year_specific_resources(self, cluster_app):
        """Add resources with different years and verify year-keyed output in resources.yml."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
            _make_resource(
                tech="Nuclear", detail="Large", case="Moderate", size=1000, year=2040
            ),
        ]
        cluster_app.state.modified_new_resources = {}

        # Verify helper functions still work correctly
        assert cluster_app._has_year_specific_resources() is True
        assert cluster_app._get_year_specific_years() == [2030, 2040]

        # Verify year-keyed structure in generate_resources_settings output
        _setup_dom_for_resources_settings(cluster_app, model_years="2030, 2040")
        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)
        new_res = parsed["new_resources"]

        # 2030 should contain base + UtilityPV
        assert ["UtilityPV", "Class1", "Moderate", 100] in new_res[2030]
        assert ["NaturalGas", "CC", "Moderate", 500] in new_res[2030]

        # 2040 should contain base + Nuclear
        assert ["Nuclear", "Large", "Moderate", 1000] in new_res[2040]
        assert ["NaturalGas", "CC", "Moderate", 500] in new_res[2040]

    def test_full_flow_with_modified_resources(self, cluster_app):
        """Modified resources with identity changes and attribute-only changes produce keyed output."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
        ]
        cluster_app.state.modified_new_resources = {
            # Attribute-only modifier for 2030
            "attr_mod": _make_modified(
                "attr_mod", year=2030, attr_modifiers={"heat_rate": 6.5}
            ),
            # Identity change for 2040
            "id_change": _make_modified(
                "id_change",
                year=2040,
                new_tech="CustomGas",
                attr_modifiers={"heat_rate": 7.0},
            ),
        }

        assert cluster_app._has_year_specific_resources() is True
        assert sorted(cluster_app._get_year_specific_years()) == [2030, 2040]

        _setup_dom_for_resources_settings(cluster_app, model_years="2030, 2040")
        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        # resource_modifiers should be year-keyed (2030 has attr_mod)
        resource_mods = parsed.get("resource_modifiers", {})
        assert isinstance(resource_mods, dict)
        assert "default" in resource_mods
        assert 2030 in resource_mods

        # modified_new_resources should be year-keyed (2040 has id_change)
        mod_new_res = parsed.get("modified_new_resources", {})
        assert isinstance(mod_new_res, dict)
        assert 2040 in mod_new_res

    def test_year_keyed_values_appear_in_resources_yml(self, cluster_app):
        """When year-specific resources exist, resources.yml contains year-keyed new_resources."""
        cluster_app.state.new_resources = [
            _make_resource(year="all"),
            _make_resource(tech="UtilityPV", year=2030),
        ]
        cluster_app.state.modified_new_resources = {}
        _setup_dom_for_resources_settings(cluster_app, model_years="2030, 2040")

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        new_res = parsed["new_resources"]
        assert isinstance(new_res, dict)
        assert "default" in new_res
        assert 2030 in new_res

    def test_no_year_specific_produces_flat_resources_yml(self, cluster_app):
        """When all resources are 'all', new_resources in resources.yml is a flat list."""
        cluster_app.state.new_resources = [_make_resource(year="all")]
        cluster_app.state.modified_new_resources = {}
        _setup_dom_for_resources_settings(cluster_app)

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        new_res = parsed["new_resources"]
        assert isinstance(new_res, list), "Expected flat list when no year-specific resources"


class TestResourcesYmlFiltering:
    """Verify generate_resources_settings() produces correct year-keyed structure."""

    def test_year_specific_excluded_from_resources_yml(self, cluster_app):
        """Under 'default' key, only 'all'-year resources appear; year-specific are in their own key."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
        ]
        cluster_app.state.modified_new_resources = {}

        # Mock all required DOM elements for generate_resources_settings
        el_map = {
            "modelYears": _mock_dom_element("2030, 2040"),
            "targetUsdYear": _mock_dom_element("2024"),
            "atbYearSelect": _mock_dom_element("2024"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
        )

        # Set up minimal required state for generation
        cluster_app.state.region_aggregations = {"R1": ["ba1", "ba2"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1", "ba2": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        new_res = parsed["new_resources"]
        # new_resources is a year-keyed dict when year-specific resources exist
        assert isinstance(new_res, dict)

        # Under 'default', only the "all" resource appears
        default_list = new_res["default"]
        assert ["NaturalGas", "CC", "Moderate", 500] in default_list
        assert ["UtilityPV", "Class1", "Moderate", 100] not in default_list

        # Under 2030, both the "all" resource and the year-specific one appear
        year_list = new_res[2030]
        assert ["NaturalGas", "CC", "Moderate", 500] in year_list
        assert ["UtilityPV", "Class1", "Moderate", 100] in year_list

    def test_year_specific_modified_excluded_from_resources_yml(self, cluster_app):
        """In resource_modifiers, 'default' has only 'all'-year mods; year keys have year-specific mods."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
        ]
        cluster_app.state.modified_new_resources = {
            "base_mod": _make_modified(
                "base_mod", year="all", attr_modifiers={"heat_rate": 6.5}
            ),
            "yr_mod": _make_modified(
                "yr_mod", year=2030, attr_modifiers={"heat_rate": 7.0}
            ),
        }

        el_map = {
            "modelYears": _mock_dom_element("2030, 2040"),
            "targetUsdYear": _mock_dom_element("2024"),
            "atbYearSelect": _mock_dom_element("2024"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
        )

        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        resource_mods = parsed.get("resource_modifiers", {})
        # resource_modifiers is year-keyed
        assert isinstance(resource_mods, dict)
        assert "default" in resource_mods

        # base_mod (year="all") should be in the 'default' key
        assert "base_mod" in resource_mods["default"]

        # yr_mod (year=2030) should be in the 2030 key, not in 'default'
        assert "yr_mod" not in resource_mods["default"]
        assert 2030 in resource_mods
        assert "yr_mod" in resource_mods[2030]


class TestBuildSettingsYamlsIntegration:
    """Verify build_settings_yamls() generates the 7 core YAML files correctly."""

    def test_no_scenario_files_when_all_years(self, cluster_app):
        """scenario_management.yml, extra_inputs.yml, and scenario_inputs.csv are never generated.
        When all resources are 'all', new_resources in resources.yml is a flat list."""
        cluster_app.state.new_resources = [_make_resource(year="all")]
        cluster_app.state.modified_new_resources = {}
        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        defaults = {
            "modelYears": "2030, 2040",
            "targetUsdYear": "2024",
            "atbYearSelect": "2024",
            "utcOffset": "-5",
            "planningYears": "2028, 2038",
        }
        el_map = {k: _mock_dom_element(v) for k, v in defaults.items()}
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_)
        )

        result = cluster_app.build_settings_yamls()
        assert "scenario_management.yml" not in result
        assert "extra_inputs.yml" not in result
        assert "scenario_inputs.csv" not in result

        # new_resources should be a flat list (no year-keying)
        resources_parsed = yaml.safe_load(result["resources.yml"])
        assert isinstance(resources_parsed["new_resources"], list)

    def test_year_keyed_in_resources_yml_when_year_specific(self, cluster_app):
        """When year-specific resources exist, resources.yml has year-keyed new_resources;
        scenario_management.yml is NOT in the result."""
        cluster_app.state.new_resources = [
            _make_resource(year="all"),
            _make_resource(tech="UtilityPV", detail="Class1", year=2030),
        ]
        cluster_app.state.modified_new_resources = {}
        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        defaults = {
            "modelYears": "2030, 2040",
            "targetUsdYear": "2024",
            "atbYearSelect": "2024",
            "utcOffset": "-5",
            "planningYears": "2028, 2038",
        }
        el_map = {k: _mock_dom_element(v) for k, v in defaults.items()}
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_)
        )

        result = cluster_app.build_settings_yamls()

        # Scenario-management files are never produced
        assert "scenario_management.yml" not in result
        assert "extra_inputs.yml" not in result
        assert "scenario_inputs.csv" not in result

        # resources.yml is always present
        assert "resources.yml" in result

        # new_resources is year-keyed
        resources_parsed = yaml.safe_load(result["resources.yml"])
        new_res = resources_parsed["new_resources"]
        assert isinstance(new_res, dict)
        assert "default" in new_res
        assert 2030 in new_res


# ============================================================================
# F. Duplicate-resource prevention in on_add_new_resource
# ============================================================================


class TestDuplicateResourcePrevention:
    """Tests for the duplicate (technology, tech_detail, planning_year) guard
    that was added to on_add_new_resource.

    The guard runs after reading tech/detail/case/size/planning_year but before
    the attr_overrides block.  When a duplicate is detected it calls
    set_status(..., "error") and returns without modifying state.
    """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_dom_map(self, tech, detail, case="Moderate", size="500", year_str="all"):
        """Return a dict mapping element IDs to mocks for the ATB picker and
        the resource year select, which are the only elements on_add_new_resource
        reads before (and including) the duplicate check."""
        return {
            "atbYearSelect": _mock_dom_element("2024"),
            "atbTechSelect": _mock_dom_element(tech),
            "atbTechDetailSelect": _mock_dom_element(detail),
            "atbCostCaseSelect": _mock_dom_element(case),
            "atbSizeMw": _mock_dom_element(size),
            "newResourceYearSelect": _mock_dom_element(year_str),
            # set_status target
            "statusBox": _mock_dom_element(""),
            # Various override fields (empty → no attr overrides)
            "atbOverrideCapex": _mock_dom_element(""),
            "atbOverrideCapexMwh": _mock_dom_element(""),
            "atbOverrideHeatRate": _mock_dom_element(""),
            "atbOverrideFixedOM": _mock_dom_element(""),
            "atbOverrideVarOM": _mock_dom_element(""),
            "atbOverrideVarOMIn": _mock_dom_element(""),
            "atbOverrideWacc": _mock_dom_element(""),
        }

    def _wire_dom(self, cluster_app, dom_map):
        """Install dom_map into cluster_app.document.getElementById."""
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: dom_map.get(id_, _mock_dom_element(""))
        )

    def _call_add(self, cluster_app, dom_map):
        """Wire DOM, reset relevant render helpers so they don't fail, then
        invoke on_add_new_resource with a dummy event."""
        self._wire_dom(cluster_app, dom_map)
        # Stub out render helpers that are called on the success path so the
        # test doesn't depend on unrelated DOM state.
        cluster_app.render_new_resources_list = MagicMock()
        cluster_app.render_modified_resources_list = MagicMock()
        cluster_app._check_year_default_warning = MagicMock()
        cluster_app.on_add_new_resource(None)

    # ------------------------------------------------------------------
    # Test 1 – duplicate in state.new_resources with planning_year="all"
    # ------------------------------------------------------------------

    def test_duplicate_in_new_resources_all_year_is_blocked(self, cluster_app):
        """Adding (UtilityPV, Class1, all) when it already exists in
        state.new_resources must trigger an error status and not append."""
        cluster_app.state.new_resources = [
            _make_resource(tech="UtilityPV", detail="Class1", year="all"),
        ]
        cluster_app.state.modified_new_resources = {}

        dom = self._make_dom_map("UtilityPV", "Class1", year_str="all")
        status_el = dom["statusBox"]
        self._call_add(cluster_app, dom)

        # Status box must show an error class
        assert "error" in status_el.className
        # The resource list must be unchanged (no new entry appended)
        assert len(cluster_app.state.new_resources) == 1

    # ------------------------------------------------------------------
    # Test 2 – duplicate with a specific integer planning year
    # ------------------------------------------------------------------

    def test_duplicate_in_new_resources_specific_year_is_blocked(self, cluster_app):
        """Adding (NaturalGas, CC, 2030) when that exact triple already exists
        in state.new_resources must be blocked."""
        cluster_app.state.new_resources = [
            _make_resource(tech="NaturalGas", detail="CC", year=2030),
        ]
        cluster_app.state.modified_new_resources = {}

        dom = self._make_dom_map("NaturalGas", "CC", year_str="2030")
        status_el = dom["statusBox"]
        self._call_add(cluster_app, dom)

        assert "error" in status_el.className
        assert len(cluster_app.state.new_resources) == 1

    # ------------------------------------------------------------------
    # Test 3 – same tech+detail but DIFFERENT year → should be ALLOWED
    # ------------------------------------------------------------------

    def test_same_tech_detail_different_year_is_allowed(self, cluster_app):
        """Adding (NaturalGas, CC, 2030) when only (NaturalGas, CC, all)
        already exists must succeed — the planning years differ."""
        cluster_app.state.new_resources = [
            _make_resource(tech="NaturalGas", detail="CC", year="all"),
        ]
        cluster_app.state.modified_new_resources = {}

        dom = self._make_dom_map("NaturalGas", "CC", year_str="2030")
        status_el = dom["statusBox"]
        self._call_add(cluster_app, dom)

        # Should NOT be an error
        assert "error" not in status_el.className
        # A new entry must have been appended
        assert len(cluster_app.state.new_resources) == 2

    # ------------------------------------------------------------------
    # Test 4 – duplicate found in state.modified_new_resources
    # ------------------------------------------------------------------

    def test_duplicate_in_modified_new_resources_is_blocked(self, cluster_app):
        """Adding (UtilityPV, Class1, 2040) when modified_new_resources already
        contains an entry with the same triple must be blocked."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "upv_class1": _make_modified(
                "upv_class1", tech="UtilityPV", detail="Class1", year=2040
            ),
        }

        dom = self._make_dom_map("UtilityPV", "Class1", year_str="2040")
        status_el = dom["statusBox"]
        self._call_add(cluster_app, dom)

        assert "error" in status_el.className
        # No new resources should have been appended
        assert len(cluster_app.state.new_resources) == 0
        # Existing modified entry must be untouched
        assert "upv_class1" in cluster_app.state.modified_new_resources

    # ------------------------------------------------------------------
    # Test 5 – same technology+year but DIFFERENT tech_detail → ALLOWED
    # ------------------------------------------------------------------

    def test_same_tech_different_detail_is_allowed(self, cluster_app):
        """Adding (NaturalGas, CT, all) when only (NaturalGas, CC, all)
        already exists must succeed — the tech_detail differs."""
        cluster_app.state.new_resources = [
            _make_resource(tech="NaturalGas", detail="CC", year="all"),
        ]
        cluster_app.state.modified_new_resources = {}

        dom = self._make_dom_map("NaturalGas", "CT", year_str="all")
        status_el = dom["statusBox"]
        self._call_add(cluster_app, dom)

        assert "error" not in status_el.className
        assert len(cluster_app.state.new_resources) == 2

    # ------------------------------------------------------------------
    # Bonus: error message content for the "all years" label
    # ------------------------------------------------------------------

    def test_error_message_uses_all_years_label(self, cluster_app):
        """When a duplicate is detected for planning_year='all', the error
        message should read '…for all years.'."""
        cluster_app.state.new_resources = [
            _make_resource(tech="Nuclear", detail="Large", year="all"),
        ]
        cluster_app.state.modified_new_resources = {}

        dom = self._make_dom_map("Nuclear", "Large", year_str="all")
        status_el = dom["statusBox"]
        self._call_add(cluster_app, dom)

        assert "error" in status_el.className
        assert "all years" in status_el.textContent

    # ------------------------------------------------------------------
    # Bonus: error message content for a specific planning year
    # ------------------------------------------------------------------

    def test_error_message_uses_numeric_year_label(self, cluster_app):
        """When a duplicate is detected for planning_year=2035, the error
        message should mention '2035'."""
        cluster_app.state.new_resources = [
            _make_resource(tech="OffShoreWind", detail="Class3", year=2035),
        ]
        cluster_app.state.modified_new_resources = {}

        dom = self._make_dom_map("OffShoreWind", "Class3", year_str="2035")
        status_el = dom["statusBox"]
        self._call_add(cluster_app, dom)

        assert "error" in status_el.className
        assert "2035" in status_el.textContent


# ============================================================================
# G. ATB data-year constraint tests
# ============================================================================


class _ClassListSpy:
    """Minimal classList stand-in that records calls to remove()."""

    def __init__(self, *initial_classes):
        self.classes = set(initial_classes)
        self.removed = []

    def remove(self, cls):
        self.removed.append(cls)
        self.classes.discard(cls)

    def add(self, cls):
        self.classes.add(cls)

    def contains(self, cls):
        return cls in self.classes

    def __contains__(self, cls):
        return cls in self.classes


class TestAtbYearConstraint:
    """Tests for ATB data_year enforcement across new-build resources.

    Covers:
    - _get_current_resources_atb_year() pure logic
    - show_atb_year_conflict_overlay() DOM manipulation
    - on_add_new_resource() year-conflict guard
    - delete_all_new_resources() clears both resource stores
    - _DEFAULT_NEW_RESOURCES entries each carry data_year == 2024
    """

    # ------------------------------------------------------------------
    # Internal helpers (mirrors TestDuplicateResourcePrevention style)
    # ------------------------------------------------------------------

    def _make_dom_map(
        self,
        tech="UtilityPV",
        detail="Class1",
        case="Moderate",
        size="100",
        year_str="all",
        atb_year_str="2024",
    ):
        """Return a dict mapping element IDs to mocks.

        Extends the base ATB-picker map with the ATB-year-conflict overlay
        elements so that show_atb_year_conflict_overlay() can manipulate them.
        """
        overlay_el = _mock_dom_element("")
        overlay_el.classList = _ClassListSpy("hidden")
        ok_button_el = _mock_dom_element("")

        return {
            "atbYearSelect": _mock_dom_element(atb_year_str),
            "atbTechSelect": _mock_dom_element(tech),
            "atbTechDetailSelect": _mock_dom_element(detail),
            "atbCostCaseSelect": _mock_dom_element(case),
            "atbSizeMw": _mock_dom_element(size),
            "newResourceYearSelect": _mock_dom_element(year_str),
            # set_status target
            "statusBox": _mock_dom_element(""),
            # ATB year-conflict overlay elements
            "atbYearConflictMessage": _mock_dom_element(""),
            "atbYearConflictOverlay": overlay_el,
            "atbYearConflictOkButton": ok_button_el,
            # Override fields (empty → no attr overrides)
            "atbOverrideCapex": _mock_dom_element(""),
            "atbOverrideCapexMwh": _mock_dom_element(""),
            "atbOverrideHeatRate": _mock_dom_element(""),
            "atbOverrideFixedOM": _mock_dom_element(""),
            "atbOverrideVarOM": _mock_dom_element(""),
            "atbOverrideVarOMIn": _mock_dom_element(""),
            "atbOverrideWacc": _mock_dom_element(""),
        }

    def _wire_dom(self, cluster_app, dom_map):
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: dom_map.get(id_, _mock_dom_element(""))
        )

    def _call_add(self, cluster_app, dom_map):
        """Wire DOM, stub render helpers, then invoke on_add_new_resource."""
        self._wire_dom(cluster_app, dom_map)
        cluster_app.render_new_resources_list = MagicMock()
        cluster_app.render_modified_resources_list = MagicMock()
        cluster_app._check_year_default_warning = MagicMock()
        cluster_app.on_add_new_resource(None)

    def _make_modified_dom_map(
        self,
        base_tech="NaturalGas",
        base_detail="CC",
        base_case="Moderate",
        base_size="500",
        new_tech="CustomGas",
        new_detail="Custom Detail",
        fuel_type="standard",
        std_fuel="naturalgas",
        new_fuel_name="",
        new_fuel_price="0",
        new_fuel_ef="0",
        tag_class="THERM",
        is_commit=True,
        year_str="all",
        atb_year_str="2024",
    ):
        """Return a dict mapping modified-resource form element IDs to mocks."""
        overlay_el = _mock_dom_element("")
        overlay_el.classList = _ClassListSpy("hidden")
        is_commit_el = _mock_dom_element("")
        is_commit_el.checked = is_commit

        return {
            "atbYearSelect": _mock_dom_element(atb_year_str),
            "modBaseTech": _mock_dom_element(base_tech),
            "modBaseTechDetail": _mock_dom_element(base_detail),
            "modBaseCostCase": _mock_dom_element(base_case),
            "modSizeMw": _mock_dom_element(base_size),
            "modNewTech": _mock_dom_element(new_tech),
            "modNewTechDetail": _mock_dom_element(new_detail),
            "modFuelType": _mock_dom_element(fuel_type),
            "modStandardFuel": _mock_dom_element(std_fuel),
            "modNewFuelName": _mock_dom_element(new_fuel_name),
            "modNewFuelPrice": _mock_dom_element(new_fuel_price),
            "modNewFuelEf": _mock_dom_element(new_fuel_ef),
            "modTagClass": _mock_dom_element(tag_class),
            "modIsCommit": is_commit_el,
            "modResourceYearSelect": _mock_dom_element(year_str),
            "statusBox": _mock_dom_element(""),
            "atbYearConflictMessage": _mock_dom_element(""),
            "atbYearConflictOverlay": overlay_el,
            "modOverrideCapexMw": _mock_dom_element(""),
            "modOverrideCapexMwh": _mock_dom_element(""),
            "modOverrideHeatRate": _mock_dom_element(""),
            "modOverrideFixedOM": _mock_dom_element(""),
            "modOverrideVarOM": _mock_dom_element(""),
            "modOverrideVarOMIn": _mock_dom_element(""),
            "modOverrideWacc": _mock_dom_element(""),
        }

    def _call_add_modified(self, cluster_app, dom_map):
        """Wire DOM, stub render helpers, then invoke on_add_modified_resource."""
        self._wire_dom(cluster_app, dom_map)
        cluster_app.render_new_resources_list = MagicMock()
        cluster_app.render_modified_resources_list = MagicMock()
        cluster_app._check_year_default_warning = MagicMock()
        cluster_app.on_add_modified_resource(None)

    # ------------------------------------------------------------------
    # 1. _get_current_resources_atb_year – empty state
    # ------------------------------------------------------------------

    def test_get_current_resources_atb_year_empty(self, cluster_app):
        """Returns None when both resource stores are empty."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {}

        result = cluster_app._get_current_resources_atb_year()

        assert result is None

    # ------------------------------------------------------------------
    # 2. _get_current_resources_atb_year – from new_resources
    # ------------------------------------------------------------------

    def test_get_current_resources_atb_year_from_new_resources(self, cluster_app):
        """Returns the data_year present in state.new_resources."""
        cluster_app.state.new_resources = [
            {**_make_resource(tech="UtilityPV", detail="Class1"), "data_year": 2024},
        ]
        cluster_app.state.modified_new_resources = {}

        result = cluster_app._get_current_resources_atb_year()

        assert result == 2024

    # ------------------------------------------------------------------
    # 3. _get_current_resources_atb_year – from modified_new_resources
    # ------------------------------------------------------------------

    def test_get_current_resources_atb_year_from_modified_resources(self, cluster_app):
        """Returns the data_year found in state.modified_new_resources when
        state.new_resources is empty."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "ng_cc": {**_make_modified("ng_cc"), "data_year": 2023},
        }

        result = cluster_app._get_current_resources_atb_year()

        assert result == 2023

    def test_get_current_resources_atb_year_falls_back_for_legacy_modified_entries(
        self, cluster_app
    ):
        """Legacy modified resources without stored data_year fall back to the
        currently selected ATB year."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "legacy": _make_modified("legacy"),
        }
        self._wire_dom(
            cluster_app,
            {"atbYearSelect": _mock_dom_element("2024")},
        )

        result = cluster_app._get_current_resources_atb_year()

        assert result == 2024

    # ------------------------------------------------------------------
    # 4. _get_current_resources_atb_year – new_resources takes priority
    # ------------------------------------------------------------------

    def test_get_current_resources_atb_year_prefers_new_resources(self, cluster_app):
        """When both stores have entries, new_resources is checked first."""
        cluster_app.state.new_resources = [
            {**_make_resource(tech="UtilityPV", detail="Class1"), "data_year": 2024},
        ]
        cluster_app.state.modified_new_resources = {
            "ng_cc": {**_make_modified("ng_cc"), "data_year": 2023},
        }

        result = cluster_app._get_current_resources_atb_year()

        assert result == 2024

    def test_get_current_resources_atb_year_prefers_explicit_modified_year(
        self, cluster_app
    ):
        """Stored modified-resource years take precedence over the DOM fallback."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "ng_cc": {**_make_modified("ng_cc"), "data_year": 2023},
            "legacy": _make_modified("legacy"),
        }
        self._wire_dom(
            cluster_app,
            {"atbYearSelect": _mock_dom_element("2024")},
        )

        result = cluster_app._get_current_resources_atb_year()

        assert result == 2023

    # ------------------------------------------------------------------
    # 5. show_atb_year_conflict_overlay reveals dialog and focuses OK
    # ------------------------------------------------------------------

    def test_show_atb_year_conflict_overlay_reveals_overlay_and_focuses_ok_button(
        self, cluster_app
    ):
        """Showing the conflict overlay should populate its message, reveal it,
        and move focus to the OK button."""
        dom = self._make_dom_map()
        message_el = dom["atbYearConflictMessage"]
        overlay_el = dom["atbYearConflictOverlay"]
        ok_button_el = dom["atbYearConflictOkButton"]

        self._wire_dom(cluster_app, dom)
        cluster_app.show_atb_year_conflict_overlay(2024, 2023)

        assert "ATB <strong>2024</strong>" in message_el.innerHTML
        assert "ATB <strong>2023</strong>" in message_el.innerHTML
        assert "hidden" in overlay_el.classList.removed
        ok_button_el.focus.assert_called_once()

    # ------------------------------------------------------------------
    # 6. on_add_new_resource blocks mismatched ATB year
    # ------------------------------------------------------------------

    def test_add_resource_from_different_atb_year_is_blocked(self, cluster_app):
        """Adding a resource whose ATB year differs from the existing year must
        NOT append the resource and must reveal the conflict overlay."""
        cluster_app.state.new_resources = [
            {**_make_resource(tech="UtilityPV", detail="Class1"), "data_year": 2024},
        ]
        cluster_app.state.modified_new_resources = {}

        dom = self._make_dom_map(
            tech="LandbasedWind",
            detail="Class3",
            atb_year_str="2023",  # ← different from existing 2024
        )
        overlay_el = dom["atbYearConflictOverlay"]

        self._call_add(cluster_app, dom)

        # Resource list must be unchanged – conflict blocked the addition
        assert len(cluster_app.state.new_resources) == 1
        # The overlay's "hidden" class must have been removed
        assert "hidden" in overlay_el.classList.removed

    def test_add_resource_from_different_atb_year_is_blocked_by_modified_resource(
        self, cluster_app
    ):
        """Modified resources with stored data_year enforce the same conflict guard."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "custom_gas": {**_make_modified("custom_gas"), "data_year": 2024},
        }

        dom = self._make_dom_map(
            tech="LandbasedWind",
            detail="Class3",
            atb_year_str="2023",
        )
        overlay_el = dom["atbYearConflictOverlay"]

        self._call_add(cluster_app, dom)

        assert cluster_app.state.new_resources == []
        assert "hidden" in overlay_el.classList.removed

    # ------------------------------------------------------------------
    # 7. on_add_new_resource allows same ATB year
    # ------------------------------------------------------------------

    def test_add_resource_from_same_atb_year_is_allowed(self, cluster_app):
        """Adding a resource with the same ATB year as existing resources
        must succeed normally."""
        cluster_app.state.new_resources = [
            {**_make_resource(tech="UtilityPV", detail="Class1"), "data_year": 2024},
        ]
        cluster_app.state.modified_new_resources = {}

        dom = self._make_dom_map(
            tech="LandbasedWind",
            detail="Class3",
            atb_year_str="2024",  # ← same as existing
        )
        overlay_el = dom["atbYearConflictOverlay"]

        self._call_add(cluster_app, dom)

        # A new resource must have been appended
        assert len(cluster_app.state.new_resources) == 2
        # Conflict overlay must NOT have been triggered
        assert "hidden" not in overlay_el.classList.removed

    # ------------------------------------------------------------------
    # 8. First resource stores data_year in its entry
    # ------------------------------------------------------------------

    def test_add_resource_to_empty_list_stores_data_year(self, cluster_app):
        """The first resource added to an empty list must persist its
        ATB data_year in the stored entry."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {}

        dom = self._make_dom_map(
            tech="UtilityPV",
            detail="Class1",
            atb_year_str="2024",
        )
        self._call_add(cluster_app, dom)

        assert len(cluster_app.state.new_resources) == 1
        assert cluster_app.state.new_resources[0]["data_year"] == 2024

    def test_add_modified_resource_to_empty_list_stores_data_year(self, cluster_app):
        """Modified resources created from the custom-resource flow persist
        the selected ATB data year."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {}

        dom = self._make_modified_dom_map(atb_year_str="2024")

        self._call_add_modified(cluster_app, dom)

        assert len(cluster_app.state.modified_new_resources) == 1
        stored = next(iter(cluster_app.state.modified_new_resources.values()))
        assert stored["data_year"] == 2024

    def test_add_modified_resource_from_different_atb_year_is_blocked(
        self, cluster_app
    ):
        """Modified-resource additions must also honor the single-year ATB rule."""
        cluster_app.state.new_resources = [
            {**_make_resource(tech="UtilityPV", detail="Class1"), "data_year": 2024},
        ]
        cluster_app.state.modified_new_resources = {}

        dom = self._make_modified_dom_map(atb_year_str="2023")
        overlay_el = dom["atbYearConflictOverlay"]

        self._call_add_modified(cluster_app, dom)

        assert cluster_app.state.modified_new_resources == {}
        assert "hidden" in overlay_el.classList.removed

    # ------------------------------------------------------------------
    # 9. delete_all_new_resources clears both stores
    # ------------------------------------------------------------------

    def test_delete_all_new_resources(self, cluster_app):
        """delete_all_new_resources() must empty both resource stores and
        call set_status with a success-level message."""
        cluster_app.state.new_resources = [
            {**_make_resource(tech="UtilityPV", detail="Class1"), "data_year": 2024},
            {
                **_make_resource(tech="LandbasedWind", detail="Class3"),
                "data_year": 2024,
            },
        ]
        cluster_app.state.modified_new_resources = {
            "ng_cc": {**_make_modified("ng_cc"), "data_year": 2024},
        }

        # Stub render helpers so the test doesn't need full DOM state
        cluster_app.render_new_resources_list = MagicMock()
        cluster_app.render_modified_resources_list = MagicMock()

        # Wire a real statusBox so we can assert on the message
        status_el = _mock_dom_element("")
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: (
                status_el if id_ == "statusBox" else _mock_dom_element("")
            )
        )

        cluster_app.delete_all_new_resources()

        assert cluster_app.state.new_resources == []
        assert cluster_app.state.modified_new_resources == {}
        # set_status must have been called with a success class
        assert "success" in status_el.className

    # ------------------------------------------------------------------
    # 9. _DEFAULT_NEW_RESOURCES all carry data_year == 2024
    # ------------------------------------------------------------------

    def test_default_resources_have_data_year(self, cluster_app):
        """Every entry in _DEFAULT_NEW_RESOURCES must specify data_year == 2024."""
        defaults = cluster_app._DEFAULT_NEW_RESOURCES

        assert len(defaults) > 0, "_DEFAULT_NEW_RESOURCES must not be empty"
        for entry in defaults:
            assert "data_year" in entry, f"Entry {entry!r} is missing 'data_year'"
            assert (
                entry["data_year"] == 2024
            ), f"Expected data_year=2024, got {entry['data_year']!r} in {entry!r}"
