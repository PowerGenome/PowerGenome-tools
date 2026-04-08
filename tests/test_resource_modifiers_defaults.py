"""
Comprehensive tests for the resource_modifiers default values feature.

Tests the automatic generation of resource_modifiers entries with default
values for all new resources, particularly battery storage technologies.

Covers:
- _get_default_resource_modifiers(): Default modifier generation logic
- build_settings_yaml(): Integration of defaults into resources.yml
- _build_resource_modifiers_for_year(): Year-specific default application
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
    """Create a new_resource dict."""
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
    """Build a modified_new_resources entry dict."""
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
    """Return a minimal mock DOM element."""
    el = MagicMock()
    el.value = value
    el.style = MagicMock()
    el.innerHTML = ""
    return el


# ============================================================================
# 1. Tests for _get_default_resource_modifiers()
# ============================================================================


class TestGetDefaultResourceModifiers:
    """Test the _get_default_resource_modifiers function."""

    def test_battery_storage_lithium_gets_defaults(self, cluster_app):
        """Utility-Scale Battery Storage with Lithium Ion gets default O&M values."""
        defaults = cluster_app._get_default_resource_modifiers(
            "Utility-Scale Battery Storage", "Lithium Ion"
        )

        assert isinstance(defaults, dict)
        assert "Var_OM_Cost_per_MWh" in defaults
        assert "Var_OM_Cost_per_MWh_In" in defaults
        assert defaults["Var_OM_Cost_per_MWh"] == 0.15
        assert defaults["Var_OM_Cost_per_MWh_In"] == 0.15

    def test_battery_storage_lithium_lowercase_tech(self, cluster_app):
        """Battery matching is case-insensitive for technology."""
        defaults = cluster_app._get_default_resource_modifiers(
            "utility-scale battery storage", "Lithium Ion"
        )

        assert defaults["Var_OM_Cost_per_MWh"] == 0.15
        assert defaults["Var_OM_Cost_per_MWh_In"] == 0.15

    def test_battery_storage_lithium_uppercase_tech(self, cluster_app):
        """Battery matching works with uppercase technology name."""
        defaults = cluster_app._get_default_resource_modifiers(
            "UTILITY-SCALE BATTERY STORAGE", "Lithium Ion"
        )

        assert defaults["Var_OM_Cost_per_MWh"] == 0.15
        assert defaults["Var_OM_Cost_per_MWh_In"] == 0.15

    def test_battery_storage_lithium_lowercase_detail(self, cluster_app):
        """Lithium matching is case-insensitive for tech_detail."""
        defaults = cluster_app._get_default_resource_modifiers(
            "Utility-Scale Battery Storage", "lithium ion"
        )

        assert defaults["Var_OM_Cost_per_MWh"] == 0.15
        assert defaults["Var_OM_Cost_per_MWh_In"] == 0.15

    def test_battery_storage_lithium_uppercase_detail(self, cluster_app):
        """Works with uppercase tech_detail."""
        defaults = cluster_app._get_default_resource_modifiers(
            "Utility-Scale Battery Storage", "LITHIUM ION"
        )

        assert defaults["Var_OM_Cost_per_MWh"] == 0.15
        assert defaults["Var_OM_Cost_per_MWh_In"] == 0.15

    def test_battery_without_lithium_no_defaults(self, cluster_app):
        """Battery storage with non-lithium tech_detail gets no defaults."""
        defaults = cluster_app._get_default_resource_modifiers(
            "Utility-Scale Battery Storage", "Lead Acid"
        )

        assert isinstance(defaults, dict)
        assert len(defaults) == 0

    def test_battery_with_mixed_case_lithium(self, cluster_app):
        """Works with mixed case lithium identifier."""
        defaults = cluster_app._get_default_resource_modifiers(
            "Utility-Scale Battery Storage", "LiThIuM IoN"
        )

        assert defaults["Var_OM_Cost_per_MWh"] == 0.15
        assert defaults["Var_OM_Cost_per_MWh_In"] == 0.15

    def test_generic_battery_with_lithium(self, cluster_app):
        """Generic 'Battery' technology with lithium gets defaults."""
        defaults = cluster_app._get_default_resource_modifiers("Battery", "Lithium Ion")

        assert defaults["Var_OM_Cost_per_MWh"] == 0.15
        assert defaults["Var_OM_Cost_per_MWh_In"] == 0.15

    def test_batteries_plural_with_lithium(self, cluster_app):
        """'Batteries' (plural) technology without 'battery' substring doesn't get defaults.

        The function checks for 'battery' as a substring, which is not present in 'batteries'.
        So this tests that the logic is correctly checking for the substring, not loose matching.
        """
        defaults = cluster_app._get_default_resource_modifiers(
            "Batteries", "Lithium Ion"
        )

        # "battery" is not in "batteries", so no defaults
        assert isinstance(defaults, dict)
        assert len(defaults) == 0

    def test_storage_technology_with_lithium(self, cluster_app):
        """Generic 'Storage' technology with lithium gets defaults."""
        defaults = cluster_app._get_default_resource_modifiers(
            "Energy Storage", "Lithium Ion"
        )

        assert defaults["Var_OM_Cost_per_MWh"] == 0.15
        assert defaults["Var_OM_Cost_per_MWh_In"] == 0.15

    def test_natural_gas_gets_no_defaults(self, cluster_app):
        """Natural gas resources get no defaults."""
        defaults = cluster_app._get_default_resource_modifiers(
            "NaturalGas", "Combined Cycle"
        )

        assert isinstance(defaults, dict)
        assert len(defaults) == 0

    def test_wind_gets_no_defaults(self, cluster_app):
        """Wind resources get no defaults."""
        defaults = cluster_app._get_default_resource_modifiers(
            "LandbasedWind", "Class3"
        )

        assert isinstance(defaults, dict)
        assert len(defaults) == 0

    def test_solar_gets_no_defaults(self, cluster_app):
        """Solar resources get no defaults."""
        defaults = cluster_app._get_default_resource_modifiers("UtilitySolar", "Class1")

        assert isinstance(defaults, dict)
        assert len(defaults) == 0

    def test_nuclear_gets_no_defaults(self, cluster_app):
        """Nuclear resources get no defaults."""
        defaults = cluster_app._get_default_resource_modifiers("Nuclear", "Large")

        assert isinstance(defaults, dict)
        assert len(defaults) == 0

    def test_hydro_gets_no_defaults(self, cluster_app):
        """Hydroelectric resources get no defaults."""
        defaults = cluster_app._get_default_resource_modifiers(
            "Hydroelectric", "Conventional"
        )

        assert isinstance(defaults, dict)
        assert len(defaults) == 0

    def test_hydrogen_gets_no_defaults(self, cluster_app):
        """Hydrogen resources get no defaults."""
        defaults = cluster_app._get_default_resource_modifiers(
            "Hydrogen", "Electrolyzer"
        )

        assert isinstance(defaults, dict)
        assert len(defaults) == 0

    def test_emptytech_empty_detail(self, cluster_app):
        """Empty tech and detail return empty dict."""
        defaults = cluster_app._get_default_resource_modifiers("", "")

        assert isinstance(defaults, dict)
        assert len(defaults) == 0

    def test_returns_new_dict_not_reference(self, cluster_app):
        """Returned dict is a new instance, not a shared reference."""
        defaults1 = cluster_app._get_default_resource_modifiers(
            "Utility-Scale Battery Storage", "Lithium Ion"
        )
        defaults2 = cluster_app._get_default_resource_modifiers(
            "Utility-Scale Battery Storage", "Lithium Ion"
        )

        assert defaults1 is not defaults2
        assert defaults1 == defaults2


# ============================================================================
# 2. Tests for build_settings_yaml() with resource_modifiers
# ============================================================================


class TestBuildSettingsYamlResourceModifiers:
    """Test resource_modifiers generation in build_settings_yaml()."""

    def test_new_resources_create_resource_modifiers_entries(self, cluster_app):
        """Every new_resource gets a resource_modifiers entry with tech/detail."""
        cluster_app.state.new_resources = [
            _make_resource(tech="NaturalGas", detail="CC", year="all"),
            _make_resource(tech="LandbasedWind", detail="Class3", year="all"),
        ]
        cluster_app.state.modified_new_resources = {}
        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        el_map = {
            "modelYears": _mock_dom_element("2030"),
            "targetUsdYear": _mock_dom_element("2024"),
            "atbYearSelect": _mock_dom_element("2024"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
        )

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        assert "resource_modifiers" in parsed
        mods = parsed["resource_modifiers"]

        # Both resources should have modifiers entries
        assert any(mod.get("technology") == "NaturalGas" for mod in mods.values())
        assert any(mod.get("technology") == "LandbasedWind" for mod in mods.values())

    def test_battery_storage_gets_default_modifiers(self, cluster_app):
        """Battery storage with lithium gets default O&M modifiers."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="Utility-Scale Battery Storage",
                detail="Lithium Ion",
                year="all",
            ),
        ]
        cluster_app.state.modified_new_resources = {}
        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        el_map = {
            "modelYears": _mock_dom_element("2030"),
            "targetUsdYear": _mock_dom_element("2024"),
            "atbYearSelect": _mock_dom_element("2024"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
        )

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        assert "resource_modifiers" in parsed
        mods = parsed["resource_modifiers"]

        # Find the battery storage entry
        battery_mod = None
        for mod in mods.values():
            if mod.get("technology") == "Utility-Scale Battery Storage":
                battery_mod = mod
                break

        assert battery_mod is not None
        assert battery_mod.get("Var_OM_Cost_per_MWh") == 0.15
        assert battery_mod.get("Var_OM_Cost_per_MWh_In") == 0.15

    def test_battery_without_lithium_no_defaults_in_modifiers(self, cluster_app):
        """Battery storage without lithium doesn't get default O&M values."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="Utility-Scale Battery Storage",
                detail="Lead Acid",
                year="all",
            ),
        ]
        cluster_app.state.modified_new_resources = {}
        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        el_map = {
            "modelYears": _mock_dom_element("2030"),
            "targetUsdYear": _mock_dom_element("2024"),
            "atbYearSelect": _mock_dom_element("2024"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
        )

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        assert "resource_modifiers" in parsed
        mods = parsed["resource_modifiers"]

        # Find the battery storage entry
        battery_mod = None
        for mod in mods.values():
            if mod.get("technology") == "Utility-Scale Battery Storage":
                battery_mod = mod
                break

        assert battery_mod is not None
        # Should have tech/detail but not O&M defaults
        assert "Var_OM_Cost_per_MWh" not in battery_mod
        assert "Var_OM_Cost_per_MWh_In" not in battery_mod

    def test_modified_resources_with_attr_modifiers_included(self, cluster_app):
        """Modified resources with attr_modifiers are included in resource_modifiers."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "gas_cc": _make_modified(
                "gas_cc",
                tech="NaturalGas",
                detail="CC",
                year="all",
                attr_modifiers={"heat_rate": 6.5},
            ),
        }
        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        el_map = {
            "modelYears": _mock_dom_element("2030"),
            "targetUsdYear": _mock_dom_element("2024"),
            "atbYearSelect": _mock_dom_element("2024"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
        )

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        assert "resource_modifiers" in parsed
        assert "gas_cc" in parsed["resource_modifiers"]
        assert parsed["resource_modifiers"]["gas_cc"]["Heat_Rate_MMBTU_per_MWh"] == 6.5

    def test_identity_changes_excluded_from_resource_modifiers(self, cluster_app):
        """Modified resources with identity changes are not in resource_modifiers."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "identity_change": _make_modified(
                "identity_change",
                tech="NaturalGas",
                detail="CC",
                new_tech="CustomGas",
                year="all",
                attr_modifiers={"heat_rate": 6.5},
            ),
        }
        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        el_map = {
            "modelYears": _mock_dom_element("2030"),
            "targetUsdYear": _mock_dom_element("2024"),
            "atbYearSelect": _mock_dom_element("2024"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
        )

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        # resource_modifiers should not include identity changes
        resource_modifiers = parsed.get("resource_modifiers", {})
        assert "identity_change" not in resource_modifiers

    def test_fuel_type_new_excluded_from_resource_modifiers(self, cluster_app):
        """Modified resources with fuel_type='new' are not in resource_modifiers."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "custom_fuel": _make_modified(
                "custom_fuel",
                tech="Hydrogen",
                detail="Electrolyzer",
                year="all",
                fuel_type="new",
                attr_modifiers={"capex_mw": 1000000},
            ),
        }
        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        el_map = {
            "modelYears": _mock_dom_element("2030"),
            "targetUsdYear": _mock_dom_element("2024"),
            "atbYearSelect": _mock_dom_element("2024"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
        )

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        resource_modifiers = parsed.get("resource_modifiers", {})
        assert "custom_fuel" not in resource_modifiers

    def test_empty_selection_exports_no_base_new_resources(self, cluster_app):
        """An intentionally empty selection should stay empty in resources.yml."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {}
        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        el_map = {
            "modelYears": _mock_dom_element("2030"),
            "targetUsdYear": _mock_dom_element("2024"),
            "atbYearSelect": _mock_dom_element("2024"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
        )

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        assert parsed["new_resources"] == []
        assert parsed.get("resource_modifiers") is None

    def test_year_specific_only_resources_do_not_fallback_to_defaults(
        self, cluster_app
    ):
        """Year-specific resources alone should produce an empty 'default' base list."""
        cluster_app.state.new_resources = [
            _make_resource(tech="UtilityPV", detail="Class1", year=2030),
            _make_resource(tech="LandbasedWind", detail="Class3", year=2040),
        ]
        cluster_app.state.modified_new_resources = {}
        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        el_map = {
            "modelYears": _mock_dom_element("2030, 2040"),
            "targetUsdYear": _mock_dom_element("2024"),
            "atbYearSelect": _mock_dom_element("2024"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
        )

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        # With year-specific resources, new_resources is a year-keyed dict.
        # The default (base) list is empty since there are no "all"-year resources.
        new_res = parsed["new_resources"]
        assert isinstance(new_res, dict)
        assert new_res.get("default") == []
        assert [["UtilityPV", "Class1", "Moderate", 500]] == new_res.get(2030)
        assert [["LandbasedWind", "Class3", "Moderate", 500]] == new_res.get(2040)

    def test_all_year_modified_resources_appear_in_new_resources(
        self, cluster_app
    ):
        """Attribute-only modified resources must appear in new_resources.

        This is a regression test for the bug where attribute-only modified
        resources (no identity changes) were appearing in resource_modifiers
        but were missing from the new_resources list, causing PowerGenome to
        receive an empty new_resources list even though resources were added.
        """
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "gas_cc": _make_modified(
                "gas_cc",
                tech="NaturalGas",
                detail="CC",
                year="all",
                attr_modifiers={"heat_rate": 6.5},
            ),
        }
        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        el_map = {
            "modelYears": _mock_dom_element("2030"),
            "targetUsdYear": _mock_dom_element("2024"),
            "atbYearSelect": _mock_dom_element("2024"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
        )

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        # The attribute-only modified resource must appear in new_resources
        assert parsed["new_resources"] == [["NaturalGas", "CC", "Moderate", 500]]
        assert parsed["resource_modifiers"]["gas_cc"]["Heat_Rate_MMBTU_per_MWh"] == 6.5

    def test_year_specific_resources_excluded_from_resources_yml(self, cluster_app):
        """Year-specific resources do not force top-level year keys in resource_modifiers.

        When effective modifier values are identical across years, output stays flat.
        """
        cluster_app.state.new_resources = [
            _make_resource(tech="NaturalGas", detail="CC", year="all"),
            _make_resource(tech="UtilityPV", detail="Class1", year=2030),
        ]
        cluster_app.state.modified_new_resources = {}
        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        el_map = {
            "modelYears": _mock_dom_element("2030"),
            "targetUsdYear": _mock_dom_element("2024"),
            "atbYearSelect": _mock_dom_element("2024"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
        )

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        assert "resource_modifiers" in parsed
        mods = parsed["resource_modifiers"]

        # resource_modifiers remains flat at the top level.
        assert isinstance(mods, dict)
        assert "default" not in mods
        assert 2030 not in mods
        assert any(m.get("technology") == "NaturalGas" for m in mods.values())
        assert any(m.get("technology") == "UtilityPV" for m in mods.values())

    def test_multiple_resources_all_get_modifiers(self, cluster_app):
        """Multiple resources all get resource_modifiers entries."""
        cluster_app.state.new_resources = [
            _make_resource(tech="NaturalGas", detail="CC", year="all"),
            _make_resource(tech="UtilitySolar", detail="Class1", year="all"),
            _make_resource(
                tech="Utility-Scale Battery Storage", detail="Lithium Ion", year="all"
            ),
        ]
        cluster_app.state.modified_new_resources = {}
        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

        el_map = {
            "modelYears": _mock_dom_element("2030"),
            "targetUsdYear": _mock_dom_element("2024"),
            "atbYearSelect": _mock_dom_element("2024"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
        )

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        assert "resource_modifiers" in parsed
        mods = parsed["resource_modifiers"]

        # All three should have entries
        techs = {mod.get("technology") for mod in mods.values()}
        assert "NaturalGas" in techs
        assert "UtilitySolar" in techs
        assert "Utility-Scale Battery Storage" in techs

    # -------------------------------------------------------------------------
    # Regression tests for the attribute-only modified resource bug
    # -------------------------------------------------------------------------

    def _setup_generate_state(self, cluster_app):
        """Apply common state and DOM setup for generate_resources_settings tests."""
        cluster_app.state.region_aggregations = {"R1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "R1"}
        cluster_app.state.plant_cluster_settings = {}
        cluster_app.state.renewables_clusters = None

    def _setup_el_map(self, cluster_app, model_years="2030"):
        """Wire up a minimal getElementById mock."""
        el_map = {
            "modelYears": _mock_dom_element(model_years),
            "targetUsdYear": _mock_dom_element("2024"),
            "atbYearSelect": _mock_dom_element("2024"),
        }
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda id_: el_map.get(id_, _mock_dom_element(""))
        )

    def test_coal_igcc_attribute_only_appears_in_new_resources_and_modifiers(
        self, cluster_app
    ):
        """Regression: Coal IGCC with capex modifier appears in both new_resources
        and resource_modifiers (exact scenario from the bug report).

        Before the fix, attribute-only modified resources were written to
        resource_modifiers but were silently omitted from new_resources, causing
        PowerGenome to see new_resources: [].
        """
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "coal_igcc": _make_modified(
                "coal_igcc",
                tech="Coal",
                detail="IGCC",
                case="Moderate",
                size=641,
                year="all",
                attr_modifiers={"capex_mw": ["mul", 1.25]},
            ),
        }
        self._setup_generate_state(cluster_app)
        self._setup_el_map(cluster_app, "2030")

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        # Must appear in new_resources
        assert parsed["new_resources"] == [["Coal", "IGCC", "Moderate", 641]]

        # Must appear in resource_modifiers with the capex override
        assert "coal_igcc" in parsed["resource_modifiers"]
        assert parsed["resource_modifiers"]["coal_igcc"]["capex_mw"] == ["mul", 1.25]

    def test_identity_changed_resource_not_in_new_resources(self, cluster_app):
        """Resources with new_technology != technology must NOT appear in new_resources.

        Identity-changed resources (where new_technology differs from technology)
        are handled via a separate mechanism and should never be injected into the
        new_resources list.
        """
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "custom_gas": _make_modified(
                "custom_gas",
                tech="NaturalGas",
                detail="CC",
                year="all",
                new_tech="CustomGasTech",  # identity change
                attr_modifiers={"heat_rate": 6.0},
            ),
        }
        self._setup_generate_state(cluster_app)
        self._setup_el_map(cluster_app, "2030")

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        # The identity-changed resource must NOT appear in new_resources
        new_res = parsed["new_resources"]
        if isinstance(new_res, list):
            assert new_res == []
        else:
            # dict form: default key must be empty
            assert new_res.get("default", []) == []
            for yr_list in new_res.values():
                assert ["NaturalGas", "CC", "Moderate", 500] not in yr_list

    def test_year_specific_attribute_only_modified_resource_in_new_resources(
        self, cluster_app
    ):
        """Year-specific attribute-only modified resource appears under its year key.

        When a resource has planning_year=2030 and is attribute-only (no identity
        or fuel changes) it should appear in new_resources[2030] but not in
        new_resources[default].
        """
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "wind_class3": _make_modified(
                "wind_class3",
                tech="LandbasedWind",
                detail="Class3",
                case="Moderate",
                size=200,
                year=2030,
                attr_modifiers={"capex_mw": ["mul", 1.1]},
            ),
        }
        self._setup_generate_state(cluster_app)
        self._setup_el_map(cluster_app, "2030")

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        new_res = parsed["new_resources"]
        # With year-specific resources, output must be a dict
        assert isinstance(new_res, dict), (
            f"Expected dict new_resources, got {type(new_res)}: {new_res}"
        )
        # Default (base) list has no "all"-year entries
        assert new_res.get("default") == []
        # The 2030 list includes the attribute-only modified resource
        assert [
            "LandbasedWind",
            "Class3",
            "Moderate",
            200,
        ] in new_res.get(2030, [])

        # The modifier entry must also be present.
        # When year-specific resources exist, field values may themselves be
        # year-keyed dicts (e.g. {"default": [...], 2030: [...]}).
        assert "wind_class3" in parsed["resource_modifiers"]
        capex_val = parsed["resource_modifiers"]["wind_class3"]["capex_mw"]
        if isinstance(capex_val, dict):
            # Year-keyed form: the 2030-specific value must be ["mul", 1.1]
            assert capex_val.get(2030) == ["mul", 1.1]
        else:
            assert capex_val == ["mul", 1.1]

    def test_mix_regular_and_attribute_only_modified_resources_no_duplicates(
        self, cluster_app
    ):
        """Regular new_resources and attribute-only modified resources both appear
        in new_resources, with no duplicate entries.

        Verifies that combining state.new_resources with attribute-only
        state.modified_new_resources does not produce duplicates even when both
        refer to the same technology tuple.
        """
        cluster_app.state.new_resources = [
            _make_resource(tech="NaturalGas", detail="CC", case="Moderate", size=500),
        ]
        cluster_app.state.modified_new_resources = {
            "solar_class1": _make_modified(
                "solar_class1",
                tech="UtilitySolar",
                detail="Class1",
                case="Moderate",
                size=150,
                year="all",
                attr_modifiers={"capex_mw": ["mul", 0.9]},
            ),
        }
        self._setup_generate_state(cluster_app)
        self._setup_el_map(cluster_app, "2030")

        result = cluster_app.generate_resources_settings()
        parsed = yaml.safe_load(result)

        new_res = parsed["new_resources"]
        # Normalise: may be a plain list or dict depending on year-specificity
        if isinstance(new_res, dict):
            entries = new_res.get("default", [])
        else:
            entries = new_res

        # Both resources must be present
        assert ["NaturalGas", "CC", "Moderate", 500] in entries
        assert ["UtilitySolar", "Class1", "Moderate", 150] in entries

        # No duplicates
        assert len(entries) == len(
            [list(e) for e in {tuple(e) for e in entries}]
        ), f"Duplicate entries found in new_resources: {entries}"

        # Modifier entry for the attribute-only resource must exist
        assert "solar_class1" in parsed["resource_modifiers"]
        assert parsed["resource_modifiers"]["solar_class1"]["capex_mw"] == ["mul", 0.9]


# ============================================================================
# 3. Tests for _build_resource_modifiers_for_year() with defaults
# ============================================================================


class TestBuildResourceModifiersForYearWithDefaults:
    """Test _build_resource_modifiers_for_year() with default modifiers."""

    def test_new_resources_all_year_get_modifiers(self, cluster_app):
        """All 'all' year new_resources appear in modifiers with defaults."""
        cluster_app.state.new_resources = [
            _make_resource(tech="NaturalGas", detail="CC", year="all"),
            _make_resource(
                tech="Utility-Scale Battery Storage", detail="Lithium Ion", year="all"
            ),
        ]
        cluster_app.state.modified_new_resources = {}

        result = cluster_app._build_resource_modifiers_for_year(2030)

        assert result is not None
        # Should have entries for both resources
        assert any(mod.get("technology") == "NaturalGas" for mod in result.values())
        assert any(
            mod.get("technology") == "Utility-Scale Battery Storage"
            for mod in result.values()
        )

    def test_new_resources_year_specific_included(self, cluster_app):
        """Year-specific new_resources appear in modifiers for their year."""
        cluster_app.state.new_resources = [
            _make_resource(tech="NaturalGas", detail="CC", year="all"),
            _make_resource(tech="UtilityPV", detail="Class1", year=2030),
        ]
        cluster_app.state.modified_new_resources = {}

        result_2030 = cluster_app._build_resource_modifiers_for_year(2030)
        result_2040 = cluster_app._build_resource_modifiers_for_year(2040)

        # 2030 should have both
        assert len(result_2030) == 2
        assert any(
            mod.get("technology") == "NaturalGas" for mod in result_2030.values()
        )
        assert any(mod.get("technology") == "UtilityPV" for mod in result_2030.values())

        # 2040 should only have the base
        assert len(result_2040) == 1
        assert any(
            mod.get("technology") == "NaturalGas" for mod in result_2040.values()
        )
        assert not any(
            mod.get("technology") == "UtilityPV" for mod in result_2040.values()
        )

    def test_battery_defaults_applied_in_year_modifiers(self, cluster_app):
        """Battery storage defaults are applied in year-specific modifiers."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="Utility-Scale Battery Storage",
                detail="Lithium Ion",
                year="all",
            ),
        ]
        cluster_app.state.modified_new_resources = {}

        result = cluster_app._build_resource_modifiers_for_year(2030)

        assert result is not None
        battery_mod = None
        for mod in result.values():
            if mod.get("technology") == "Utility-Scale Battery Storage":
                battery_mod = mod
                break

        assert battery_mod is not None
        assert battery_mod.get("Var_OM_Cost_per_MWh") == 0.15
        assert battery_mod.get("Var_OM_Cost_per_MWh_In") == 0.15

    def test_modified_resources_all_year_included(self, cluster_app):
        """Modified resources with 'all' year and attr_modifiers are included."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "gas_mod": _make_modified(
                "gas_mod",
                tech="NaturalGas",
                detail="CT",
                year="all",
                attr_modifiers={"heat_rate": 11.0},
            ),
        }

        result = cluster_app._build_resource_modifiers_for_year(2030)

        assert result is not None
        assert "gas_mod" in result
        assert result["gas_mod"]["Heat_Rate_MMBTU_per_MWh"] == 11.0

    def test_modified_resources_year_specific_included(self, cluster_app):
        """Modified resources with matching year and attr_modifiers are included."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "gas_2030": _make_modified(
                "gas_2030",
                tech="NaturalGas",
                detail="CC",
                year=2030,
                attr_modifiers={"fixed_o_m_mw": 45.0},
            ),
        }

        result_2030 = cluster_app._build_resource_modifiers_for_year(2030)
        result_2040 = cluster_app._build_resource_modifiers_for_year(2040)

        assert result_2030 is not None
        assert "gas_2030" in result_2030
        assert result_2030["gas_2030"]["Fixed_OM_Cost_per_MWyr"] == 45.0

        assert result_2040 is None

    def test_modified_identity_changes_excluded(self, cluster_app):
        """Modified resources with identity changes are excluded."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "identity_change": _make_modified(
                "identity_change",
                tech="NaturalGas",
                new_tech="CustomGas",
                year="all",
                attr_modifiers={"heat_rate": 6.5},
            ),
        }

        result = cluster_app._build_resource_modifiers_for_year(2030)

        assert result is None

    def test_modified_fuel_type_new_excluded(self, cluster_app):
        """Modified resources with fuel_type='new' are excluded."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "new_fuel": _make_modified(
                "new_fuel",
                tech="Hydrogen",
                detail="Electrolyzer",
                year="all",
                fuel_type="new",
                attr_modifiers={"capex_mw": 1000000},
            ),
        }

        result = cluster_app._build_resource_modifiers_for_year(2030)

        assert result is None

    def test_mix_new_and_modified_resources(self, cluster_app):
        """Mix of new resources and modified resources appear together."""
        cluster_app.state.new_resources = [
            _make_resource(tech="NaturalGas", detail="CC", year="all"),
        ]
        cluster_app.state.modified_new_resources = {
            "wind_mod": _make_modified(
                "wind_mod",
                tech="LandbasedWind",
                detail="Class3",
                year="all",
                attr_modifiers={"capex_mw": 1200000},
            ),
        }

        result = cluster_app._build_resource_modifiers_for_year(2030)

        assert result is not None
        # Should have entries for both
        assert any(mod.get("technology") == "NaturalGas" for mod in result.values())
        assert "wind_mod" in result
        assert result["wind_mod"]["capex_mw"] == 1200000

    def test_returns_none_when_no_modifiers(self, cluster_app):
        """Returns None when no modifiers exist for the year."""
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {}

        result = cluster_app._build_resource_modifiers_for_year(2030)

        assert result is None

    def test_multiple_years_same_base_resources(self, cluster_app):
        """Same base resources appear in all years."""
        cluster_app.state.new_resources = [
            _make_resource(tech="NaturalGas", detail="CC", year="all"),
            _make_resource(
                tech="Utility-Scale Battery Storage", detail="Lithium Ion", year="all"
            ),
        ]
        cluster_app.state.modified_new_resources = {}

        result_2030 = cluster_app._build_resource_modifiers_for_year(2030)
        result_2040 = cluster_app._build_resource_modifiers_for_year(2040)
        result_2050 = cluster_app._build_resource_modifiers_for_year(2050)

        # All years should have the same base resources
        for result in [result_2030, result_2040, result_2050]:
            assert len(result) == 2
            techs = {mod.get("technology") for mod in result.values()}
            assert "NaturalGas" in techs
            assert "Utility-Scale Battery Storage" in techs

    def test_battery_defaults_consistent_across_years(self, cluster_app):
        """Battery defaults remain consistent across different years."""
        cluster_app.state.new_resources = [
            _make_resource(
                tech="Utility-Scale Battery Storage",
                detail="Lithium Ion",
                year="all",
            ),
        ]
        cluster_app.state.modified_new_resources = {}

        result_2030 = cluster_app._build_resource_modifiers_for_year(2030)
        result_2040 = cluster_app._build_resource_modifiers_for_year(2040)

        for result in [result_2030, result_2040]:
            battery_mod = [
                mod
                for mod in result.values()
                if mod.get("technology") == "Utility-Scale Battery Storage"
            ][0]
            assert battery_mod.get("Var_OM_Cost_per_MWh") == 0.15
            assert battery_mod.get("Var_OM_Cost_per_MWh_In") == 0.15

    def test_modified_overrides_new_resource_entry(self, cluster_app):
        """Modified resource with same tech/detail overrides new resource entry."""
        cluster_app.state.new_resources = [
            _make_resource(tech="NaturalGas", detail="CC", year="all"),
        ]
        cluster_app.state.modified_new_resources = {
            "gas_override": _make_modified(
                "gas_override",
                tech="NaturalGas",
                detail="CC",
                year="all",
                attr_modifiers={"heat_rate": 7.5},
            ),
        }

        result = cluster_app._build_resource_modifiers_for_year(2030)

        assert result is not None
        # Should have override entry
        assert "gas_override" in result
        assert result["gas_override"]["Heat_Rate_MMBTU_per_MWh"] == 7.5


# ---------------------------------------------------------------------------
# Tests for populate_default_battery_attributes() - UI population
# ---------------------------------------------------------------------------


class TestPopulateDefaultBatteryAttributes:
    """Tests for the populate_default_battery_attributes() UI function."""

    def test_battery_defaults_populated_in_ui(self, cluster_app):
        """Default values are populated for battery storage UI fields."""
        # Create mock DOM elements
        mock_tech_el = MagicMock()
        mock_tech_el.value = "Utility-Scale Battery Storage"
        mock_detail_el = MagicMock()
        mock_detail_el.value = "Lithium Ion"
        mock_var_om_el = MagicMock()
        mock_var_om_el.value = ""
        mock_var_om_in_el = MagicMock()
        mock_var_om_in_el.value = ""

        # Mock getElementById
        original_get_elem = cluster_app.document.getElementById

        def mock_get_elem(elem_id):
            if elem_id == "atbTechSelect":
                return mock_tech_el
            elif elem_id == "atbTechDetailSelect":
                return mock_detail_el
            elif elem_id == "atbOverrideVarOM":
                return mock_var_om_el
            elif elem_id == "atbOverrideVarOMIn":
                return mock_var_om_in_el
            return original_get_elem(elem_id)

        cluster_app.document.getElementById = mock_get_elem

        # Mock _get_select_value
        def mock_get_select_value(elem, default):
            if hasattr(elem, "value"):
                return elem.value if elem.value else default
            return default

        cluster_app._get_select_value = mock_get_select_value

        # Call function
        cluster_app.populate_default_battery_attributes()

        # Verify defaults were populated
        assert mock_var_om_el.value == "0.15"
        assert mock_var_om_in_el.value == "0.15"

    def test_non_battery_fields_cleared(self, cluster_app):
        """Non-battery technologies have O&M fields cleared."""
        # Create mock DOM elements
        mock_tech_el = MagicMock()
        mock_tech_el.value = "NaturalGas"
        mock_detail_el = MagicMock()
        mock_detail_el.value = "Combined Cycle"
        mock_var_om_el = MagicMock()
        mock_var_om_el.value = "0.15"  # Pre-filled from previous selection
        mock_var_om_in_el = MagicMock()
        mock_var_om_in_el.value = "0.15"

        # Mock getElementById
        original_get_elem = cluster_app.document.getElementById

        def mock_get_elem(elem_id):
            if elem_id == "atbTechSelect":
                return mock_tech_el
            elif elem_id == "atbTechDetailSelect":
                return mock_detail_el
            elif elem_id == "atbOverrideVarOM":
                return mock_var_om_el
            elif elem_id == "atbOverrideVarOMIn":
                return mock_var_om_in_el
            return original_get_elem(elem_id)

        cluster_app.document.getElementById = mock_get_elem

        # Mock _get_select_value
        def mock_get_select_value(elem, default):
            if hasattr(elem, "value"):
                return elem.value if elem.value else default
            return default

        cluster_app._get_select_value = mock_get_select_value

        # Call function
        cluster_app.populate_default_battery_attributes()

        # Verify fields were cleared (since non-battery tech has no defaults)
        assert mock_var_om_el.value == ""
        assert mock_var_om_in_el.value == ""

    def test_preserves_user_values(self, cluster_app):
        """User-entered values are not overwritten by defaults."""
        # Create mock DOM elements
        mock_tech_el = MagicMock()
        mock_tech_el.value = "Utility-Scale Battery Storage"
        mock_detail_el = MagicMock()
        mock_detail_el.value = "Lithium Ion"
        mock_var_om_el = MagicMock()
        mock_var_om_el.value = "0.25"  # User-specified value
        mock_var_om_in_el = MagicMock()
        mock_var_om_in_el.value = ""

        # Mock getElementById
        original_get_elem = cluster_app.document.getElementById

        def mock_get_elem(elem_id):
            if elem_id == "atbTechSelect":
                return mock_tech_el
            elif elem_id == "atbTechDetailSelect":
                return mock_detail_el
            elif elem_id == "atbOverrideVarOM":
                return mock_var_om_el
            elif elem_id == "atbOverrideVarOMIn":
                return mock_var_om_in_el
            return original_get_elem(elem_id)

        cluster_app.document.getElementById = mock_get_elem

        # Mock _get_select_value
        def mock_get_select_value(elem, default):
            if hasattr(elem, "value"):
                return elem.value if elem.value else default
            return default

        cluster_app._get_select_value = mock_get_select_value

        # Call function
        cluster_app.populate_default_battery_attributes()

        # User value should be preserved, other field should get default
        assert mock_var_om_el.value == "0.25"
        assert mock_var_om_in_el.value == "0.15"

    def test_case_insensitive_battery_detection(self, cluster_app):
        """Battery detection works with different capitalizations."""
        # Create mock DOM elements
        mock_tech_el = MagicMock()
        mock_tech_el.value = "UTILITY-SCALE BATTERY STORAGE"  # All caps
        mock_detail_el = MagicMock()
        mock_detail_el.value = "lithium ion"  # Lowercase
        mock_var_om_el = MagicMock()
        mock_var_om_el.value = ""
        mock_var_om_in_el = MagicMock()
        mock_var_om_in_el.value = ""

        # Mock getElementById
        original_get_elem = cluster_app.document.getElementById

        def mock_get_elem(elem_id):
            if elem_id == "atbTechSelect":
                return mock_tech_el
            elif elem_id == "atbTechDetailSelect":
                return mock_detail_el
            elif elem_id == "atbOverrideVarOM":
                return mock_var_om_el
            elif elem_id == "atbOverrideVarOMIn":
                return mock_var_om_in_el
            return original_get_elem(elem_id)

        cluster_app.document.getElementById = mock_get_elem

        # Mock _get_select_value
        def mock_get_select_value(elem, default):
            if hasattr(elem, "value"):
                return elem.value if elem.value else default
            return default

        cluster_app._get_select_value = mock_get_select_value

        # Call function
        cluster_app.populate_default_battery_attributes()

        # Defaults should be populated despite case differences
        assert mock_var_om_el.value == "0.15"
        assert mock_var_om_in_el.value == "0.15"
