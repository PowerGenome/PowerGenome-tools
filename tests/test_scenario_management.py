"""
Comprehensive tests for the per-year scenario management feature.

Covers:
- Pure logic: _has_year_specific_resources, _get_year_specific_years,
  _build_new_resources_for_year, _build_resource_modifiers_for_year,
  _build_modified_new_resources_for_year, _year_badge_html,
  generate_scenario_management_settings, generate_scenario_csv,
  generate_extra_inputs_settings
- DOM-interacting: populate_resource_year_selects, _get_resource_planning_year,
  sync_textarea_from_state, _on_textarea_input, _check_year_default_warning
- Integration: full flows, resources.yml filtering, build_settings_yamls
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


class TestGenerateScenarioManagementSettings:
    """Tests for generate_scenario_management_settings()."""

    def test_returns_none_when_no_overrides(self, cluster_app):
        cluster_app.state.new_resources = [_make_resource(year="all")]
        cluster_app.state.modified_new_resources = {}
        assert cluster_app.generate_scenario_management_settings() is None

    def test_returns_none_for_empty_state(self, cluster_app):
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {}
        assert cluster_app.generate_scenario_management_settings() is None

    def test_structure_with_year_specific_new_resource(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
        ]
        cluster_app.state.modified_new_resources = {}
        result = cluster_app.generate_scenario_management_settings()
        assert result is not None

        parsed = yaml.safe_load(result)
        assert "settings_management" in parsed
        assert 2030 in parsed["settings_management"]
        year_block = parsed["settings_management"][2030]
        assert "all_cases" in year_block
        all_cases = year_block["all_cases"]
        assert "new_resources" in all_cases
        # Should contain base + year-specific
        assert len(all_cases["new_resources"]) == 2

    def test_year_specific_modified_resource_generates_output(self, cluster_app):
        cluster_app.state.new_resources = [_make_resource(year="all")]
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified("k1", year=2040, attr_modifiers={"heat_rate": 6.5}),
        }
        result = cluster_app.generate_scenario_management_settings()
        assert result is not None
        parsed = yaml.safe_load(result)
        assert 2040 in parsed["settings_management"]

    def test_multiple_years(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(year="all"),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
            _make_resource(
                tech="Wind", detail="Class3", case="Moderate", size=200, year=2040
            ),
        ]
        cluster_app.state.modified_new_resources = {}
        result = cluster_app.generate_scenario_management_settings()
        parsed = yaml.safe_load(result)
        assert 2030 in parsed["settings_management"]
        assert 2040 in parsed["settings_management"]

    def test_resource_modifiers_in_output(self, cluster_app):
        cluster_app.state.new_resources = [_make_resource(year="all")]
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified("k1", year=2030, attr_modifiers={"heat_rate": 6.5}),
        }
        result = cluster_app.generate_scenario_management_settings()
        parsed = yaml.safe_load(result)
        all_cases = parsed["settings_management"][2030]["all_cases"]
        assert "resource_modifiers" in all_cases

    def test_modified_new_resources_in_output(self, cluster_app):
        cluster_app.state.new_resources = [_make_resource(year="all")]
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified("k1", year=2030, new_tech="CustomGas"),
        }
        result = cluster_app.generate_scenario_management_settings()
        parsed = yaml.safe_load(result)
        all_cases = parsed["settings_management"][2030]["all_cases"]
        assert "modified_new_resources" in all_cases

    def test_year_with_same_new_resources_as_base_omits_key(self, cluster_app):
        """If year_new_resources == base, 'new_resources' is not in the override."""
        cluster_app.state.new_resources = [_make_resource(year="all")]
        cluster_app.state.modified_new_resources = {
            "k1": _make_modified("k1", year=2030, attr_modifiers={"heat_rate": 6.5}),
        }
        result = cluster_app.generate_scenario_management_settings()
        parsed = yaml.safe_load(result)
        all_cases = parsed["settings_management"][2030]["all_cases"]
        assert "new_resources" not in all_cases

    def test_output_is_valid_yaml(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(year="all"),
            _make_resource(tech="UtilityPV", year=2030),
        ]
        cluster_app.state.modified_new_resources = {}
        result = cluster_app.generate_scenario_management_settings()
        parsed = yaml.safe_load(result)
        assert isinstance(parsed, dict)


class TestGenerateScenarioCsv:
    """Tests for generate_scenario_csv()."""

    def test_returns_none_when_no_overrides(self, cluster_app):
        cluster_app.state.new_resources = [_make_resource(year="all")]
        cluster_app.state.modified_new_resources = {}
        assert cluster_app.generate_scenario_csv() is None

    def test_returns_none_when_no_model_years(self, cluster_app):
        cluster_app.state.new_resources = [_make_resource(year=2030)]
        cluster_app.state.modified_new_resources = {}
        # Mock empty model years
        el = _mock_dom_element("")
        cluster_app.document.getElementById = MagicMock(return_value=el)
        assert cluster_app.generate_scenario_csv() is None

    def test_generates_proper_csv(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(year="all"),
            _make_resource(tech="UtilityPV", year=2030),
        ]
        cluster_app.state.modified_new_resources = {}
        el = _mock_dom_element("2030, 2040")
        cluster_app.document.getElementById = MagicMock(return_value=el)

        result = cluster_app.generate_scenario_csv()
        assert result is not None
        lines = result.strip().split("\n")
        assert lines[0] == "case_id,year"
        assert lines[1] == "baseline,2030"
        assert lines[2] == "baseline,2040"
        assert len(lines) == 3

    def test_csv_ends_with_newline(self, cluster_app):
        cluster_app.state.new_resources = [_make_resource(year=2030)]
        cluster_app.state.modified_new_resources = {}
        el = _mock_dom_element("2030")
        cluster_app.document.getElementById = MagicMock(return_value=el)

        result = cluster_app.generate_scenario_csv()
        assert result.endswith("\n")

    def test_three_model_years(self, cluster_app):
        cluster_app.state.new_resources = [_make_resource(year=2030)]
        cluster_app.state.modified_new_resources = {}
        el = _mock_dom_element("2030, 2040, 2050")
        cluster_app.document.getElementById = MagicMock(return_value=el)

        result = cluster_app.generate_scenario_csv()
        lines = result.strip().split("\n")
        assert len(lines) == 4  # header + 3 rows


class TestGenerateExtraInputsSettings:
    """Tests for generate_extra_inputs_settings()."""

    def test_returns_none_when_no_overrides(self, cluster_app):
        cluster_app.state.new_resources = [_make_resource(year="all")]
        cluster_app.state.modified_new_resources = {}
        assert cluster_app.generate_extra_inputs_settings() is None

    def test_returns_yaml_with_overrides(self, cluster_app):
        cluster_app.state.new_resources = [_make_resource(year=2030)]
        cluster_app.state.modified_new_resources = {}
        result = cluster_app.generate_extra_inputs_settings()
        assert result is not None

        parsed = yaml.safe_load(result)
        assert parsed["input_folder"] == "extra_inputs"
        assert parsed["scenario_definitions_fn"] == "scenario_inputs.csv"

    def test_output_is_valid_yaml(self, cluster_app):
        cluster_app.state.new_resources = [_make_resource(year=2030)]
        cluster_app.state.modified_new_resources = {}
        result = cluster_app.generate_extra_inputs_settings()
        parsed = yaml.safe_load(result)
        assert isinstance(parsed, dict)
        assert len(parsed) == 2


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


class TestSyncTextareaFromState:
    """Tests for sync_textarea_from_state()."""

    def test_builds_textarea_from_state(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year="all"
            ),
        ]
        raw_el = _mock_dom_element("")
        cluster_app.document.getElementById = MagicMock(return_value=raw_el)

        cluster_app.sync_textarea_from_state()

        lines = raw_el.value.split("\n")
        assert len(lines) == 2
        assert "NaturalGas | CC | Moderate | 500" in lines[0]
        assert "UtilityPV | Class1 | Moderate | 100" in lines[1]

    def test_appends_year_comment_for_non_all(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year=2030
            ),
        ]
        raw_el = _mock_dom_element("")
        cluster_app.document.getElementById = MagicMock(return_value=raw_el)

        cluster_app.sync_textarea_from_state()

        assert "# year:2030" in raw_el.value

    def test_no_year_comment_for_all(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(year="all"),
        ]
        raw_el = _mock_dom_element("")
        cluster_app.document.getElementById = MagicMock(return_value=raw_el)

        cluster_app.sync_textarea_from_state()

        assert "# year:" not in raw_el.value

    def test_empty_state(self, cluster_app):
        cluster_app.state.new_resources = []
        raw_el = _mock_dom_element("")
        cluster_app.document.getElementById = MagicMock(return_value=raw_el)

        cluster_app.sync_textarea_from_state()

        assert raw_el.value == ""

    def test_missing_element_no_error(self, cluster_app):
        cluster_app.state.new_resources = [_make_resource()]
        cluster_app.document.getElementById = MagicMock(return_value=None)
        # Should not raise
        cluster_app.sync_textarea_from_state()


class TestOnTextareaInput:
    """Tests for _on_textarea_input()."""

    def test_parses_basic_resources(self, cluster_app):
        raw_el = _mock_dom_element("NaturalGas | CC | Moderate | 500")
        # Need to also mock render_new_resources_list
        cluster_app.document.getElementById = MagicMock(return_value=raw_el)

        cluster_app._on_textarea_input()

        assert len(cluster_app.state.new_resources) == 1
        r = cluster_app.state.new_resources[0]
        assert r["technology"] == "NaturalGas"
        assert r["tech_detail"] == "CC"
        assert r["cost_case"] == "Moderate"
        assert r["size_mw"] == 500
        assert r["planning_year"] == "all"

    def test_extracts_year_comment(self, cluster_app):
        raw_el = _mock_dom_element("NaturalGas | CC | Moderate | 500  # year:2030")
        cluster_app.document.getElementById = MagicMock(return_value=raw_el)

        cluster_app._on_textarea_input()

        assert len(cluster_app.state.new_resources) == 1
        assert cluster_app.state.new_resources[0]["planning_year"] == 2030

    def test_multiple_lines(self, cluster_app):
        text = (
            "NaturalGas | CC | Moderate | 500\n"
            "UtilityPV | Class1 | Moderate | 100  # year:2030\n"
            "Nuclear | Large | Moderate | 1000  # year:2040"
        )
        raw_el = _mock_dom_element(text)
        cluster_app.document.getElementById = MagicMock(return_value=raw_el)

        cluster_app._on_textarea_input()

        assert len(cluster_app.state.new_resources) == 3
        assert cluster_app.state.new_resources[0]["planning_year"] == "all"
        assert cluster_app.state.new_resources[1]["planning_year"] == 2030
        assert cluster_app.state.new_resources[2]["planning_year"] == 2040

    def test_skips_empty_and_comment_lines(self, cluster_app):
        text = "# This is a comment\n" "\n" "NaturalGas | CC | Moderate | 500\n" "\n"
        raw_el = _mock_dom_element(text)
        cluster_app.document.getElementById = MagicMock(return_value=raw_el)

        cluster_app._on_textarea_input()

        assert len(cluster_app.state.new_resources) == 1

    def test_skips_malformed_lines(self, cluster_app):
        text = (
            "NaturalGas | CC | Moderate | 500\n"
            "BadLine\n"
            "Too | Few\n"
            "NaturalGas | CC | Moderate | notanumber\n"
        )
        raw_el = _mock_dom_element(text)
        cluster_app.document.getElementById = MagicMock(return_value=raw_el)

        cluster_app._on_textarea_input()

        assert len(cluster_app.state.new_resources) == 1

    def test_invalid_year_defaults_to_all(self, cluster_app):
        raw_el = _mock_dom_element("NaturalGas | CC | Moderate | 500  # year:abc")
        cluster_app.document.getElementById = MagicMock(return_value=raw_el)

        cluster_app._on_textarea_input()

        assert cluster_app.state.new_resources[0]["planning_year"] == "all"

    def test_roundtrip_sync_textarea(self, cluster_app):
        """sync_textarea_from_state → _on_textarea_input should be lossless."""
        original = [
            _make_resource(
                tech="NaturalGas", detail="CC", case="Moderate", size=500, year="all"
            ),
            _make_resource(
                tech="UtilityPV", detail="Class1", case="Moderate", size=100, year=2030
            ),
        ]
        cluster_app.state.new_resources = list(original)

        raw_el = _mock_dom_element("")
        cluster_app.document.getElementById = MagicMock(return_value=raw_el)

        # Sync to textarea
        cluster_app.sync_textarea_from_state()
        # Parse back
        cluster_app._on_textarea_input()

        assert len(cluster_app.state.new_resources) == 2
        assert cluster_app.state.new_resources[0]["planning_year"] == "all"
        assert cluster_app.state.new_resources[1]["planning_year"] == 2030
        for i, orig in enumerate(original):
            got = cluster_app.state.new_resources[i]
            assert got["technology"] == orig["technology"]
            assert got["tech_detail"] == orig["tech_detail"]
            assert got["cost_case"] == orig["cost_case"]
            assert got["size_mw"] == orig["size_mw"]

    def test_missing_element_no_error(self, cluster_app):
        cluster_app.document.getElementById = MagicMock(return_value=None)
        cluster_app._on_textarea_input()

    def test_float_size_truncated(self, cluster_app):
        raw_el = _mock_dom_element("NaturalGas | CC | Moderate | 123.7")
        cluster_app.document.getElementById = MagicMock(return_value=raw_el)

        cluster_app._on_textarea_input()

        assert cluster_app.state.new_resources[0]["size_mw"] == 123

    def test_empty_parts_skipped(self, cluster_app):
        """Lines with empty tech/detail/case/size fields are skipped."""
        raw_el = _mock_dom_element(" |  | Moderate | 500\nNaturalGas |  |  | 500")
        cluster_app.document.getElementById = MagicMock(return_value=raw_el)

        cluster_app._on_textarea_input()

        assert len(cluster_app.state.new_resources) == 0


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
    """End-to-end tests combining resource setup and scenario generation."""

    def test_full_flow_with_year_specific_resources(self, cluster_app):
        """Add resources with different years and verify scenario management output."""
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

        # Verify _has_year_specific_resources
        assert cluster_app._has_year_specific_resources() is True
        # Verify years
        assert cluster_app._get_year_specific_years() == [2030, 2040]

        # Verify scenario management
        result = cluster_app.generate_scenario_management_settings()
        parsed = yaml.safe_load(result)
        sm = parsed["settings_management"]

        # 2030 should have base + UtilityPV
        all_cases_2030 = sm[2030]["all_cases"]
        assert ["UtilityPV", "Class1", "Moderate", 100] in all_cases_2030[
            "new_resources"
        ]
        assert ["NaturalGas", "CC", "Moderate", 500] in all_cases_2030["new_resources"]

        # 2040 should have base + Nuclear
        all_cases_2040 = sm[2040]["all_cases"]
        assert ["Nuclear", "Large", "Moderate", 1000] in all_cases_2040["new_resources"]
        assert ["NaturalGas", "CC", "Moderate", 500] in all_cases_2040["new_resources"]

    def test_full_flow_with_modified_resources(self, cluster_app):
        """Modified resources with identity changes and attribute-only changes."""
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

        result = cluster_app.generate_scenario_management_settings()
        parsed = yaml.safe_load(result)

        # 2030 should have resource_modifiers
        all_cases_2030 = parsed["settings_management"][2030]["all_cases"]
        assert "resource_modifiers" in all_cases_2030

        # 2040 should have modified_new_resources
        all_cases_2040 = parsed["settings_management"][2040]["all_cases"]
        assert "modified_new_resources" in all_cases_2040

    def test_scenario_csv_and_extra_inputs_generated(self, cluster_app):
        cluster_app.state.new_resources = [
            _make_resource(year="all"),
            _make_resource(tech="UtilityPV", year=2030),
        ]
        cluster_app.state.modified_new_resources = {}

        el = _mock_dom_element("2030, 2040")
        cluster_app.document.getElementById = MagicMock(return_value=el)

        csv = cluster_app.generate_scenario_csv()
        extra = cluster_app.generate_extra_inputs_settings()

        assert csv is not None
        assert extra is not None
        assert "baseline,2030" in csv
        assert "baseline,2040" in csv
        assert "scenario_inputs.csv" in extra

    def test_no_year_specific_produces_no_scenario_files(self, cluster_app):
        cluster_app.state.new_resources = [_make_resource(year="all")]
        cluster_app.state.modified_new_resources = {}

        assert cluster_app.generate_scenario_management_settings() is None
        assert cluster_app.generate_scenario_csv() is None
        assert cluster_app.generate_extra_inputs_settings() is None


class TestResourcesYmlFiltering:
    """Verify generate_resources_settings() only includes 'all' year resources."""

    def test_year_specific_excluded_from_resources_yml(self, cluster_app):
        """Resources with year != 'all' should not appear in resources.yml."""
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

        # new_resources from resources.yml should only contain the "all" resource
        new_res = parsed.get("new_resources", [])
        assert ["NaturalGas", "CC", "Moderate", 500] in new_res
        assert ["UtilityPV", "Class1", "Moderate", 100] not in new_res

    def test_year_specific_modified_excluded_from_resources_yml(self, cluster_app):
        """Modified resources with year != 'all' should not appear in resources.yml."""
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
        # Only "all" modifier should be in resources.yml
        assert "base_mod" in resource_mods
        assert "yr_mod" not in resource_mods


class TestBuildSettingsYamlsIntegration:
    """Verify build_settings_yamls() conditionally includes scenario files."""

    def test_no_scenario_files_when_all_years(self, cluster_app):
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

    def test_scenario_files_included_when_year_specific(self, cluster_app):
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
        assert "scenario_management.yml" in result
        assert "extra_inputs.yml" in result
        assert "scenario_inputs.csv" in result

        # Validate contents
        sm = yaml.safe_load(result["scenario_management.yml"])
        assert "settings_management" in sm

        csv_lines = result["scenario_inputs.csv"].strip().split("\n")
        assert csv_lines[0] == "case_id,year"

        extra = yaml.safe_load(result["extra_inputs.yml"])
        assert extra["input_folder"] == "extra_inputs"


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
            "newResourcesRaw": _mock_dom_element(""),
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
        cluster_app.sync_textarea_from_state = MagicMock()
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
