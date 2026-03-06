"""
Tests for click-to-populate feature in web/cluster_app.py.

Covers:
- populate_picker_from_resource_index
- populate_picker_from_modified_resource_key
- render_new_resources_list clickability attributes
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

ATB_INDEX = {
    2024: {
        "NaturalGas": {
            "2-on-1 Combined Cycle (F-Frame)": ["Advanced", "Conservative", "Moderate"],
            "Combustion Turbine (F-Frame)": ["Advanced", "Conservative", "Moderate"],
        },
        "LandbasedWind": {
            "Class3": ["Advanced", "Conservative", "Moderate"],
        },
        "Utility-Scale Battery Storage": {
            "Lithium Ion": ["Advanced", "Conservative", "Moderate"],
        },
    }
}


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
        sys.modules["renewables_utils"] = MagicMock()
        sys.modules["visualization_utils"] = MagicMock()
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
        if web_dir is not None:
            web_dir_str = str(web_dir)
            if web_dir_str in sys.path:
                sys.path.remove(web_dir_str)
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_element(value=""):
    """Return a fresh MagicMock that behaves like a DOM input/select element."""
    el = MagicMock()
    el.value = value
    el.innerHTML = ""
    el.open = False
    return el


def _setup_atb_state(cluster_app):
    """Configure state with a minimal ATB index for testing."""
    cluster_app.state.atb_index = ATB_INDEX
    cluster_app.state.atb_years = [2024]


def _make_dom_elements():
    """Create a dict of element-id → MagicMock for all ATB picker fields."""
    return {
        "atbYearSelect": _make_element("2024"),
        "atbTechSelect": _make_element(),
        "atbTechDetailSelect": _make_element(),
        "atbCostCaseSelect": _make_element(),
        "atbSizeMw": _make_element(),
        "newResourceYearSelect": _make_element(),
        "atbOverrideCapex": _make_element(),
        "atbOverrideCapexMwh": _make_element(),
        "atbOverrideHeatRate": _make_element(),
        "atbOverrideFixedOM": _make_element(),
        "atbOverrideVarOM": _make_element(),
        "atbOverrideVarOMIn": _make_element(),
        "atbOverrideWacc": _make_element(),
        "atbAttrsOverride": _make_element(),
    }


def _wire_document(cluster_app, elements):
    """Set document.getElementById side-effect using the given element map."""
    cluster_app.document.getElementById.side_effect = lambda eid: elements.get(eid)


# ---------------------------------------------------------------------------
# Tests: populate_picker_from_resource_index
# ---------------------------------------------------------------------------

class TestPopulatePickerFromResourceIndex:
    def _setup(self, cluster_app):
        _setup_atb_state(cluster_app)
        elements = _make_dom_elements()
        _wire_document(cluster_app, elements)
        return elements

    def test_valid_index_sets_size(self, cluster_app):
        """Valid index should write the resource size_mw to the size input."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        assert elements["atbSizeMw"].value == "727"

    def test_valid_index_sets_planning_year(self, cluster_app):
        """Valid index should set the planning year dropdown value."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "2030",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        assert elements["newResourceYearSelect"].value == "2030"

    def test_valid_index_populates_tech_dropdown_html(self, cluster_app):
        """_set_select_options should write innerHTML for the tech dropdown."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "Combustion Turbine (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 233,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        # innerHTML should have been set (non-empty) and contain the tech name
        assert "NaturalGas" in elements["atbTechSelect"].innerHTML

    def test_valid_index_populates_detail_dropdown_html(self, cluster_app):
        """_set_select_options should write innerHTML for the detail dropdown."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "Combustion Turbine (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 233,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        assert "Combustion Turbine (F-Frame)" in elements["atbTechDetailSelect"].innerHTML

    def test_valid_index_populates_case_dropdown_html(self, cluster_app):
        """_set_select_options should write innerHTML for the cost-case dropdown."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Advanced",
                "size_mw": 727,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        assert "Advanced" in elements["atbCostCaseSelect"].innerHTML

    def test_invalid_index_out_of_range_does_nothing(self, cluster_app):
        """Index beyond list length should not call getElementById at all."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
            }
        ]
        cluster_app.document.getElementById.reset_mock()
        cluster_app.populate_picker_from_resource_index(99)
        cluster_app.document.getElementById.assert_not_called()

    def test_negative_index_does_nothing(self, cluster_app):
        """Negative index should be rejected and not touch the DOM."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
            }
        ]
        cluster_app.document.getElementById.reset_mock()
        cluster_app.populate_picker_from_resource_index(-1)
        cluster_app.document.getElementById.assert_not_called()

    def test_non_integer_index_does_nothing(self, cluster_app):
        """Non-integer index that can't be cast to int should be silently ignored."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
            }
        ]
        cluster_app.document.getElementById.reset_mock()
        cluster_app.populate_picker_from_resource_index("not_a_number")
        cluster_app.document.getElementById.assert_not_called()

    def test_resource_with_capex_override_populates_capex_field(self, cluster_app):
        """capex_mw key in resource should be written to the atbOverrideCapex input."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "capex_mw": 1234.5,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        assert elements["atbOverrideCapex"].value == "1234.5"

    def test_resource_with_capex_override_expands_override_panel(self, cluster_app):
        """Having an attr override should set atbAttrsOverride.open = True."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "capex_mw": 1234.5,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        assert elements["atbAttrsOverride"].open is True

    def test_resource_without_overrides_clears_override_fields(self, cluster_app):
        """Resource with no attr keys should leave all override fields empty."""
        elements = self._setup(cluster_app)
        # pre-populate a field to confirm it gets cleared
        elements["atbOverrideCapex"].value = "999"
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        assert elements["atbOverrideCapex"].value == ""

    def test_resource_without_overrides_does_not_expand_panel(self, cluster_app):
        """When no attr overrides exist, the override panel should stay closed."""
        elements = self._setup(cluster_app)
        elements["atbAttrsOverride"].open = False
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        assert elements["atbAttrsOverride"].open is False

    def test_battery_variable_om_overrides_populate_correct_fields(self, cluster_app):
        """Battery resource with variable O&M fields should populate both VarOM inputs."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "Utility-Scale Battery Storage",
                "tech_detail": "Lithium Ion",
                "cost_case": "Moderate",
                "size_mw": 60,
                "variable_o_m_mwh": 0.15,
                "variable_o_m_mwh_in": 0.15,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        assert elements["atbOverrideVarOM"].value == "0.15"
        assert elements["atbOverrideVarOMIn"].value == "0.15"

    def test_battery_variable_om_overrides_expand_panel(self, cluster_app):
        """Battery resource with variable O&M fields should open the override panel."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "Utility-Scale Battery Storage",
                "tech_detail": "Lithium Ion",
                "cost_case": "Moderate",
                "size_mw": 60,
                "variable_o_m_mwh": 0.15,
                "variable_o_m_mwh_in": 0.15,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        assert elements["atbAttrsOverride"].open is True

    def test_multiple_resources_second_index(self, cluster_app):
        """Index 1 should load the second resource, not the first."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
            },
            {
                "technology": "LandbasedWind",
                "tech_detail": "Class3",
                "cost_case": "Advanced",
                "size_mw": 200,
                "planning_year": "2035",
            },
        ]
        cluster_app.populate_picker_from_resource_index(1)
        assert elements["atbSizeMw"].value == "200"
        assert elements["newResourceYearSelect"].value == "2035"
        assert "LandbasedWind" in elements["atbTechSelect"].innerHTML

    def test_string_index_is_coerced_to_int(self, cluster_app):
        """JavaScript may pass index as string; it should be cast to int."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 500,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index("0")
        assert elements["atbSizeMw"].value == "500"

    def test_heat_rate_override_populates_correct_field(self, cluster_app):
        """heat_rate key should be written to atbOverrideHeatRate."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "heat_rate": 6500.0,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        assert elements["atbOverrideHeatRate"].value == "6500.0"

    def test_fixed_om_override_populates_correct_field(self, cluster_app):
        """fixed_o_m_mw key should be written to atbOverrideFixedOM."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "fixed_o_m_mw": 12345.0,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        assert elements["atbOverrideFixedOM"].value == "12345.0"

    def test_wacc_override_populates_correct_field(self, cluster_app):
        """wacc_real key should be written to atbOverrideWacc."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "wacc_real": 0.05,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        assert elements["atbOverrideWacc"].value == "0.05"

    def test_capex_mwh_override_populates_correct_field(self, cluster_app):
        """capex_mwh key should be written to atbOverrideCapexMwh."""
        elements = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "Utility-Scale Battery Storage",
                "tech_detail": "Lithium Ion",
                "cost_case": "Moderate",
                "size_mw": 60,
                "capex_mwh": 250.0,
                "planning_year": "all",
            }
        ]
        cluster_app.populate_picker_from_resource_index(0)
        assert elements["atbOverrideCapexMwh"].value == "250.0"


# ---------------------------------------------------------------------------
# Tests: populate_picker_from_modified_resource_key
# ---------------------------------------------------------------------------

class TestPopulatePickerFromModifiedResourceKey:
    def _setup(self, cluster_app):
        _setup_atb_state(cluster_app)
        elements = _make_dom_elements()
        _wire_document(cluster_app, elements)
        return elements

    def _make_modified_resource(self, **kwargs):
        base = {
            "technology": "NaturalGas",
            "new_technology": "NaturalGas",
            "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
            "new_tech_detail": "2-on-1 Combined Cycle (F-Frame)",
            "cost_case": "Moderate",
            "new_cost_case": "Moderate",
            "size_mw": 727,
            "planning_year": "all",
            "fuel_type": "standard",
            "attr_modifiers": {},
        }
        base.update(kwargs)
        return base

    def test_valid_key_sets_size(self, cluster_app):
        """Valid key should write size_mw to the size input."""
        elements = self._setup(cluster_app)
        cluster_app.state.modified_new_resources = {
            "res1": self._make_modified_resource(size_mw=400),
        }
        cluster_app.populate_picker_from_modified_resource_key("res1")
        assert elements["atbSizeMw"].value == "400"

    def test_valid_key_sets_planning_year(self, cluster_app):
        """Valid key should set the planning year dropdown value."""
        elements = self._setup(cluster_app)
        cluster_app.state.modified_new_resources = {
            "res1": self._make_modified_resource(planning_year="2040"),
        }
        cluster_app.populate_picker_from_modified_resource_key("res1")
        assert elements["newResourceYearSelect"].value == "2040"

    def test_valid_key_populates_tech_dropdown(self, cluster_app):
        """Valid key should write innerHTML to the tech select element."""
        elements = self._setup(cluster_app)
        cluster_app.state.modified_new_resources = {
            "res1": self._make_modified_resource(technology="LandbasedWind", new_technology="LandbasedWind",
                                                  tech_detail="Class3", new_tech_detail="Class3",
                                                  cost_case="Moderate", new_cost_case="Moderate",
                                                  size_mw=200),
        }
        cluster_app.populate_picker_from_modified_resource_key("res1")
        assert "LandbasedWind" in elements["atbTechSelect"].innerHTML

    def test_missing_key_does_nothing(self, cluster_app):
        """Key not in modified_new_resources should not touch the DOM."""
        elements = self._setup(cluster_app)
        cluster_app.state.modified_new_resources = {}
        cluster_app.document.getElementById.reset_mock()
        cluster_app.populate_picker_from_modified_resource_key("nonexistent_key")
        cluster_app.document.getElementById.assert_not_called()

    def test_none_value_for_key_does_nothing(self, cluster_app):
        """If the stored item is falsy, function should return early."""
        elements = self._setup(cluster_app)
        # Store None explicitly — item check `if not item` guards against this
        cluster_app.state.modified_new_resources = {"bad_key": None}
        cluster_app.document.getElementById.reset_mock()
        cluster_app.populate_picker_from_modified_resource_key("bad_key")
        cluster_app.document.getElementById.assert_not_called()

    def test_attr_modifiers_variable_om_populates_field(self, cluster_app):
        """attr_modifiers variable_o_m_mwh should populate the VarOM field."""
        elements = self._setup(cluster_app)
        cluster_app.state.modified_new_resources = {
            "bat_res": self._make_modified_resource(
                technology="Utility-Scale Battery Storage",
                new_technology="Utility-Scale Battery Storage",
                tech_detail="Lithium Ion",
                new_tech_detail="Lithium Ion",
                cost_case="Moderate",
                new_cost_case="Moderate",
                size_mw=60,
                attr_modifiers={"variable_o_m_mwh": 0.15},
            ),
        }
        cluster_app.populate_picker_from_modified_resource_key("bat_res")
        assert elements["atbOverrideVarOM"].value == "0.15"

    def test_attr_modifiers_variable_om_expands_panel(self, cluster_app):
        """attr_modifiers with a value should open the override panel."""
        elements = self._setup(cluster_app)
        cluster_app.state.modified_new_resources = {
            "bat_res": self._make_modified_resource(
                attr_modifiers={"variable_o_m_mwh": 0.15},
            ),
        }
        cluster_app.populate_picker_from_modified_resource_key("bat_res")
        assert elements["atbAttrsOverride"].open is True

    def test_operator_based_modifier_formats_as_op_colon_value(self, cluster_app):
        """Operator-based modifier [op, value] should format as 'op:value' in the field."""
        elements = self._setup(cluster_app)
        cluster_app.state.modified_new_resources = {
            "op_res": self._make_modified_resource(
                attr_modifiers={"capex_mw": ["mul", 1.1]},
            ),
        }
        cluster_app.populate_picker_from_modified_resource_key("op_res")
        assert elements["atbOverrideCapex"].value == "mul:1.1"

    def test_operator_based_modifier_expands_panel(self, cluster_app):
        """Operator-based modifier should also trigger override panel expansion."""
        elements = self._setup(cluster_app)
        cluster_app.state.modified_new_resources = {
            "op_res": self._make_modified_resource(
                attr_modifiers={"capex_mw": ["mul", 1.1]},
            ),
        }
        cluster_app.populate_picker_from_modified_resource_key("op_res")
        assert elements["atbAttrsOverride"].open is True

    def test_empty_attr_modifiers_clears_fields(self, cluster_app):
        """Empty attr_modifiers dict should leave override fields empty."""
        elements = self._setup(cluster_app)
        elements["atbOverrideCapex"].value = "9999"  # pre-set
        cluster_app.state.modified_new_resources = {
            "plain_res": self._make_modified_resource(attr_modifiers={}),
        }
        cluster_app.populate_picker_from_modified_resource_key("plain_res")
        assert elements["atbOverrideCapex"].value == ""

    def test_none_attr_modifiers_treated_as_empty(self, cluster_app):
        """attr_modifiers=None should be treated as empty dict (no overrides)."""
        elements = self._setup(cluster_app)
        elements["atbOverrideCapex"].value = "9999"
        cluster_app.state.modified_new_resources = {
            "plain_res": self._make_modified_resource(attr_modifiers=None),
        }
        cluster_app.populate_picker_from_modified_resource_key("plain_res")
        assert elements["atbOverrideCapex"].value == ""

    def test_key_coerced_to_string(self, cluster_app):
        """Key is coerced to str before lookup, matching dict key storage."""
        elements = self._setup(cluster_app)
        cluster_app.state.modified_new_resources = {
            "42": self._make_modified_resource(size_mw=300),
        }
        # Pass key as string (JavaScript always sends strings)
        cluster_app.populate_picker_from_modified_resource_key("42")
        assert elements["atbSizeMw"].value == "300"

    def test_multiple_attr_modifiers_all_populated(self, cluster_app):
        """Multiple attr_modifiers should each populate their respective field."""
        elements = self._setup(cluster_app)
        cluster_app.state.modified_new_resources = {
            "multi_res": self._make_modified_resource(
                attr_modifiers={
                    "capex_mw": 800.0,
                    "fixed_o_m_mw": 20000.0,
                    "wacc_real": 0.04,
                },
            ),
        }
        cluster_app.populate_picker_from_modified_resource_key("multi_res")
        assert elements["atbOverrideCapex"].value == "800.0"
        assert elements["atbOverrideFixedOM"].value == "20000.0"
        assert elements["atbOverrideWacc"].value == "0.04"


# ---------------------------------------------------------------------------
# Tests: render_new_resources_list clickability
# ---------------------------------------------------------------------------

class TestRenderNewResourcesListClickability:
    def _setup(self, cluster_app):
        container = MagicMock()
        container.innerHTML = ""

        def _get_element_by_id(element_id):
            if element_id == "newResourcesList":
                return container
            return MagicMock()

        cluster_app.document.getElementById.side_effect = _get_element_by_id
        return container

    def test_regular_resource_onclick_attribute(self, cluster_app):
        """Regular resource row must have onclick='window.populatePickerFromResource(0)'."""
        container = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
            }
        ]
        cluster_app.state.modified_new_resources = {}
        cluster_app.render_new_resources_list()
        assert "onclick='window.populatePickerFromResource(0)'" in container.innerHTML

    def test_regular_resource_cursor_pointer_style(self, cluster_app):
        """Regular resource row must have cursor: pointer in its style."""
        container = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
            }
        ]
        cluster_app.state.modified_new_resources = {}
        cluster_app.render_new_resources_list()
        assert "cursor: pointer" in container.innerHTML

    def test_regular_resource_delete_button_stop_propagation(self, cluster_app):
        """Regular resource delete button must call event.stopPropagation()."""
        container = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
            }
        ]
        cluster_app.state.modified_new_resources = {}
        cluster_app.render_new_resources_list()
        assert "event.stopPropagation()" in container.innerHTML

    def test_regular_resource_delete_button_references_correct_index(self, cluster_app):
        """Delete button for regular resources should reference the same index."""
        container = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
            }
        ]
        cluster_app.state.modified_new_resources = {}
        cluster_app.render_new_resources_list()
        # Both populate and delete should reference index 0
        assert "window.deleteNewResource(0)" in container.innerHTML

    def test_second_regular_resource_onclick_index(self, cluster_app):
        """Second regular resource should have index 1 in its onclick handler."""
        container = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
            },
            {
                "technology": "LandbasedWind",
                "tech_detail": "Class3",
                "cost_case": "Moderate",
                "size_mw": 200,
                "planning_year": "all",
            },
        ]
        cluster_app.state.modified_new_resources = {}
        cluster_app.render_new_resources_list()
        assert "onclick='window.populatePickerFromResource(1)'" in container.innerHTML

    def test_modified_resource_onclick_attribute(self, cluster_app):
        """Modified resource row must have onclick='window.populatePickerFromModifiedResource(\"key\")'."""
        container = self._setup(cluster_app)
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "mykey": {
                "technology": "NaturalGas",
                "new_technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "new_tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "new_cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
                "fuel_type": "standard",
                "attr_modifiers": {"capex_mw": 1000.0},
            }
        }
        cluster_app.render_new_resources_list()
        assert 'onclick=\'window.populatePickerFromModifiedResource("mykey")\'' in container.innerHTML

    def test_modified_resource_cursor_pointer_style(self, cluster_app):
        """Modified resource row must have cursor: pointer in its style."""
        container = self._setup(cluster_app)
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "mykey": {
                "technology": "NaturalGas",
                "new_technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "new_tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "new_cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
                "fuel_type": "standard",
                "attr_modifiers": {"capex_mw": 1000.0},
            }
        }
        cluster_app.render_new_resources_list()
        assert "cursor: pointer" in container.innerHTML

    def test_modified_resource_delete_button_stop_propagation(self, cluster_app):
        """Modified resource delete button must call event.stopPropagation()."""
        container = self._setup(cluster_app)
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "mykey": {
                "technology": "NaturalGas",
                "new_technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "new_tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "new_cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
                "fuel_type": "standard",
                "attr_modifiers": {"capex_mw": 1000.0},
            }
        }
        cluster_app.render_new_resources_list()
        assert "event.stopPropagation()" in container.innerHTML

    def test_modified_resource_without_attr_modifiers_not_rendered(self, cluster_app):
        """Modified resources with empty attr_modifiers are NOT shown in this list."""
        container = self._setup(cluster_app)
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {
            "empty_key": {
                "technology": "NaturalGas",
                "new_technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "new_tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "new_cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
                "fuel_type": "standard",
                "attr_modifiers": {},  # empty — should not appear
            }
        }
        cluster_app.render_new_resources_list()
        # With no items at all, the list should show the empty message
        assert "No new-build resources" in container.innerHTML

    def test_empty_lists_show_placeholder_message(self, cluster_app):
        """Empty new_resources and empty modified_new_resources shows placeholder."""
        container = self._setup(cluster_app)
        cluster_app.state.new_resources = []
        cluster_app.state.modified_new_resources = {}
        cluster_app.render_new_resources_list()
        assert "No new-build resources" in container.innerHTML

    def test_both_regular_and_modified_resources_rendered(self, cluster_app):
        """When both kinds are present, both onclick types appear in the output."""
        container = self._setup(cluster_app)
        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "2-on-1 Combined Cycle (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 727,
                "planning_year": "all",
            }
        ]
        cluster_app.state.modified_new_resources = {
            "mod_res": {
                "technology": "LandbasedWind",
                "new_technology": "LandbasedWind",
                "tech_detail": "Class3",
                "new_tech_detail": "Class3",
                "cost_case": "Moderate",
                "new_cost_case": "Moderate",
                "size_mw": 200,
                "planning_year": "all",
                "fuel_type": "standard",
                "attr_modifiers": {"capex_mw": 900.0},
            }
        }
        cluster_app.render_new_resources_list()
        assert "onclick='window.populatePickerFromResource(0)'" in container.innerHTML
        assert 'onclick=\'window.populatePickerFromModifiedResource("mod_res")\'' in container.innerHTML
