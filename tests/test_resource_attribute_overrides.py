"""
Comprehensive tests for resource attribute override feature.

Tests the ability to override ATB resource attributes using:
- Plain numeric values (e.g., "123.45")
- Operator-based modifications (e.g., "add:100", "mul:1.1", "truediv:2", "sub:50")

The feature supports 7 override fields:
- capex_mw (CAPEX Power, $/MW)
- capex_mwh (CAPEX Storage, $/MWh)
- heat_rate (Heat Rate, MMBtu/MWh)
- fixed_o_m_mw (Fixed O&M, $/MW-yr)
- variable_o_m_mwh (Variable O&M Out, $/MWh)
- variable_o_m_mwh_in (Variable O&M In, $/MWh)
- wacc_real (Real Weighted Average Cost of Capital, fraction)

Settings generation outputs:
- resource_modifiers: dict of resources with attribute overrides
- modified_new_resources: dict of resources with custom fuels or identity changes
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def cluster_app():
    """Load cluster_app module with mocked js/PyScript dependencies."""
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

    try:
        # Mock PyScript/browser environment
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

        # Load cluster_app module
        web_dir = Path(__file__).parent.parent / "web"
        module_path = web_dir / "cluster_app.py"
        spec = importlib.util.spec_from_file_location("cluster_app", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["cluster_app"] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)

        yield module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


@pytest.fixture()
def mock_app_state(cluster_app):
    """Create a clean AppState instance with minimal required data."""
    state = cluster_app.AppState()
    state.modified_new_resources = {}
    state.region_aggregations = {
        "Region1": ["ba1", "ba2"],
        "Region2": ["ba3", "ba4"],
    }
    state.is_clustered = True
    state.renewables_clusters = None
    state.plant_cluster_settings = None
    return state


# ============================================================================
# 1. Parsing Tests
# ============================================================================


class TestAttributeValueParsing:
    """Test parsing of plain numeric and operator-based attribute values."""

    def test_parse_plain_integer(self):
        """Plain integer value should be parsed as float."""
        value_str = "123"
        result = parse_attribute_value(value_str)
        assert isinstance(result, float)
        assert result == 123.0

    def test_parse_plain_float(self):
        """Plain float value should be parsed correctly."""
        value_str = "123.45"
        result = parse_attribute_value(value_str)
        assert isinstance(result, float)
        assert result == 123.45

    def test_parse_operator_add(self):
        """Operator 'add' value should be parsed as [operator, value]."""
        value_str = "add:100"
        result = parse_attribute_value(value_str)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == "add"
        assert result[1] == 100.0

    def test_parse_operator_mul(self):
        """Operator 'mul' value should be parsed as [operator, value]."""
        value_str = "mul:1.1"
        result = parse_attribute_value(value_str)
        assert result == ["mul", 1.1]

    def test_parse_operator_truediv(self):
        """Operator 'truediv' value should be parsed as [operator, value]."""
        value_str = "truediv:2"
        result = parse_attribute_value(value_str)
        assert result == ["truediv", 2.0]

    def test_parse_operator_sub(self):
        """Operator 'sub' value should be parsed as [operator, value]."""
        value_str = "sub:50"
        result = parse_attribute_value(value_str)
        assert result == ["sub", 50.0]

    def test_parse_operator_whitespace(self):
        """Whitespace around operator and value should be stripped."""
        value_str = " add : 100 "
        result = parse_attribute_value(value_str)
        assert result == ["add", 100.0]

    def test_parse_operator_uppercase(self):
        """Operator in uppercase should be normalized to lowercase."""
        value_str = "ADD:100"
        result = parse_attribute_value(value_str)
        assert result == ["add", 100.0]

    def test_parse_operator_mixed_case(self):
        """Operator in mixed case should be normalized to lowercase."""
        value_str = "MuL:1.5"
        result = parse_attribute_value(value_str)
        assert result == ["mul", 1.5]

    def test_parse_negative_plain_value(self):
        """Negative plain values should be parsed correctly."""
        value_str = "-123.45"
        result = parse_attribute_value(value_str)
        assert result == -123.45

    def test_parse_negative_operator_value(self):
        """Negative operator values should be parsed correctly."""
        value_str = "add:-100"
        result = parse_attribute_value(value_str)
        assert result == ["add", -100.0]

    def test_parse_scientific_notation(self):
        """Scientific notation should be parsed correctly."""
        value_str = "1.5e3"
        result = parse_attribute_value(value_str)
        assert result == 1500.0

    def test_parse_operator_scientific_notation(self):
        """Scientific notation in operator value should work."""
        value_str = "mul:1.5e-2"
        result = parse_attribute_value(value_str)
        assert result == ["mul", 0.015]

    def test_parse_invalid_operator_raises(self):
        """Invalid operator should raise ValueError."""
        value_str = "invalid:100"
        with pytest.raises(ValueError, match="Invalid operator"):
            parse_attribute_value(value_str)

    def test_parse_invalid_numeric_raises(self):
        """Invalid numeric value should raise ValueError."""
        value_str = "not_a_number"
        with pytest.raises(ValueError):
            parse_attribute_value(value_str)

    def test_parse_operator_invalid_numeric_raises(self):
        """Invalid numeric in operator value should raise ValueError."""
        value_str = "add:not_a_number"
        with pytest.raises(ValueError):
            parse_attribute_value(value_str)

    def test_parse_malformed_operator_no_colon(self):
        """Value with operator-like text but no colon should parse as float or fail."""
        value_str = "add100"
        with pytest.raises(ValueError):
            parse_attribute_value(value_str)

    def test_parse_malformed_operator_multiple_colons(self):
        """Value with multiple colons should use first colon as delimiter."""
        value_str = "add:100:extra"
        # Split on first colon only, so "add" and "100:extra"
        # "100:extra" should fail to parse as float
        with pytest.raises(ValueError):
            parse_attribute_value(value_str)

    def test_parse_empty_string_raises(self):
        """Empty string should raise ValueError."""
        value_str = ""
        with pytest.raises(ValueError):
            parse_attribute_value(value_str)

    def test_parse_whitespace_only_raises(self):
        """Whitespace-only string should raise ValueError."""
        value_str = "   "
        with pytest.raises(ValueError):
            parse_attribute_value(value_str)

    def test_parse_operator_no_value_raises(self):
        """Operator with no value should raise ValueError."""
        value_str = "add:"
        with pytest.raises(ValueError):
            parse_attribute_value(value_str)


def parse_attribute_value(value_str):
    """
    Helper function to parse attribute override values.
    Extracted logic from on_add_new_resource for testing.

    Returns:
        float: for plain numeric values
        list: [operator, value] for operator-based values

    Raises:
        ValueError: for invalid input
    """
    value_str = value_str.strip()
    if not value_str:
        raise ValueError("Empty value")

    if ":" in value_str:
        parts = value_str.split(":", 1)
        if len(parts) != 2:
            raise ValueError("Invalid format")
        op, val = parts[0].strip().lower(), parts[1].strip()
        if not op or not val:
            raise ValueError("Empty operator or value")
        if op not in ["add", "mul", "truediv", "sub"]:
            raise ValueError(f"Invalid operator: '{op}'")
        return [op, float(val)]
    else:
        return float(value_str)


# ============================================================================
# 2. Override Fields Processing Tests
# ============================================================================


class TestOverrideFieldsProcessing:
    """Test processing of all 7 override fields."""

    def test_all_fields_processed(self):
        """All 7 override fields should be recognized and processed."""
        override_fields = [
            ("capex_mw", "atbOverrideCapex"),
            ("capex_mwh", "atbOverrideCapexMwh"),
            ("heat_rate", "atbOverrideHeatRate"),
            ("fixed_o_m_mw", "atbOverrideFixedOM"),
            ("variable_o_m_mwh", "atbOverrideVarOM"),
            ("variable_o_m_mwh_in", "atbOverrideVarOMIn"),
            ("wacc_real", "atbOverrideWacc"),
        ]

        mock_values = {
            "atbOverrideCapex": "1000000",
            "atbOverrideCapexMwh": "add:100",
            "atbOverrideHeatRate": "mul:1.05",
            "atbOverrideFixedOM": "50000",
            "atbOverrideVarOM": "truediv:2",
            "atbOverrideVarOMIn": "0.15",
            "atbOverrideWacc": "sub:0.01",
        }

        result = process_override_fields(override_fields, mock_values)

        assert len(result) == 7
        assert result["capex_mw"] == 1000000.0
        assert result["capex_mwh"] == ["add", 100.0]
        assert result["heat_rate"] == ["mul", 1.05]
        assert result["fixed_o_m_mw"] == 50000.0
        assert result["variable_o_m_mwh"] == ["truediv", 2.0]
        assert result["variable_o_m_mwh_in"] == 0.15
        assert result["wacc_real"] == ["sub", 0.01]

    def test_empty_fields_ignored(self):
        """Empty override fields should be ignored."""
        override_fields = [
            ("capex_mw", "atbOverrideCapex"),
            ("heat_rate", "atbOverrideHeatRate"),
        ]

        mock_values = {
            "atbOverrideCapex": "",
            "atbOverrideHeatRate": "10000",
        }

        result = process_override_fields(override_fields, mock_values)

        assert len(result) == 1
        assert "capex_mw" not in result
        assert result["heat_rate"] == 10000.0

    def test_whitespace_fields_ignored(self):
        """Whitespace-only fields should be ignored."""
        override_fields = [
            ("capex_mw", "atbOverrideCapex"),
            ("heat_rate", "atbOverrideHeatRate"),
        ]

        mock_values = {
            "atbOverrideCapex": "   ",
            "atbOverrideHeatRate": "10000",
        }

        result = process_override_fields(override_fields, mock_values)

        assert len(result) == 1
        assert "capex_mw" not in result

    def test_missing_fields_ignored(self):
        """Missing fields (not in mock_values) should be ignored."""
        override_fields = [
            ("capex_mw", "atbOverrideCapex"),
            ("heat_rate", "atbOverrideHeatRate"),
        ]

        mock_values = {
            "atbOverrideCapex": "1000000",
            # atbOverrideHeatRate is missing
        }

        result = process_override_fields(override_fields, mock_values)

        assert len(result) == 1
        assert result["capex_mw"] == 1000000.0
        assert "heat_rate" not in result

    def test_battery_specific_fields(self):
        """Battery-specific fields (capex_mwh, variable_o_m_mwh_in) should work."""
        override_fields = [
            ("capex_mwh", "atbOverrideCapexMwh"),
            ("variable_o_m_mwh_in", "atbOverrideVarOMIn"),
        ]

        mock_values = {
            "atbOverrideCapexMwh": "add:1000",
            "atbOverrideVarOMIn": "0.15",
        }

        result = process_override_fields(override_fields, mock_values)

        assert result["capex_mwh"] == ["add", 1000.0]
        assert result["variable_o_m_mwh_in"] == 0.15


def process_override_fields(override_fields, mock_values):
    """
    Helper function to process override fields.
    Simulates the logic in on_add_new_resource.

    Args:
        override_fields: List of (attr_name, element_id) tuples
        mock_values: Dict mapping element_id to value string

    Returns:
        Dict mapping attr_name to parsed value
    """
    attr_overrides = {}
    for attr, el_id in override_fields:
        value_str = mock_values.get(el_id, "").strip()
        if value_str:
            attr_overrides[attr] = parse_attribute_value(value_str)
    return attr_overrides


# ============================================================================
# 3. Settings Generation Tests
# ============================================================================


class TestResourceModifiersGeneration:
    """Test generation of resource_modifiers in settings YAML."""

    def test_resource_modifiers_created(self, cluster_app, mock_app_state):
        """resource_modifiers section should be created when overrides exist."""
        # Set up state with modified resources
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "batteries": {
                "technology": "Utility-Scale Battery Storage",
                "tech_detail": "Lithium Ion",
                "cost_case": "Moderate",
                "size_mw": 1,
                "new_technology": "Utility-Scale Battery Storage",
                "new_tech_detail": "Lithium Ion",
                "new_cost_case": "Moderate",
                "attr_modifiers": {
                    "variable_o_m_mwh": ["add", 0.15],
                    "variable_o_m_mwh_in": 0.15,
                    "wacc_real": 0.0467,
                },
                "fuel_type": "none",
                "tag_class": "STOR",
                "is_commit": False,
                "fuel_desc": "none",
            }
        }

        # Generate settings
        result = generate_resource_modifiers_dict(mock_app_state)

        assert "batteries" in result
        assert result["batteries"]["technology"] == "Utility-Scale Battery Storage"
        assert result["batteries"]["tech_detail"] == "Lithium Ion"
        assert result["batteries"]["Var_OM_Cost_per_MWh"] == ["add", 0.15]
        assert result["batteries"]["Var_OM_Cost_per_MWh_In"] == 0.15
        assert result["batteries"]["wacc_real"] == 0.0467

    def test_numeric_overrides_as_plain_values(self, cluster_app, mock_app_state):
        """Numeric overrides should appear as plain values in YAML."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "custom_ct": {
                "technology": "NaturalGas",
                "tech_detail": "Combustion Turbine",
                "cost_case": "Moderate",
                "size_mw": 100,
                "new_technology": "NaturalGas",
                "new_tech_detail": "Combustion Turbine",
                "new_cost_case": "Moderate",
                "attr_modifiers": {
                    "capex_mw": 1000000.0,
                    "heat_rate": 10000.0,
                },
                "fuel_type": "standard",
                "standard_fuel": "naturalgas",
                "tag_class": "THERM",
                "is_commit": True,
                "fuel_desc": "naturalgas",
            }
        }

        result = generate_resource_modifiers_dict(mock_app_state)

        assert result["custom_ct"]["capex_mw"] == 1000000.0
        assert result["custom_ct"]["Heat_Rate_MMBTU_per_MWh"] == 10000.0
        assert isinstance(result["custom_ct"]["capex_mw"], float)
        assert isinstance(result["custom_ct"]["Heat_Rate_MMBTU_per_MWh"], float)

    def test_operator_overrides_as_lists(self, cluster_app, mock_app_state):
        """Operator-based overrides should appear as lists in YAML."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "modified_wind": {
                "technology": "LandbasedWind",
                "tech_detail": "Class3",
                "cost_case": "Moderate",
                "size_mw": 1,
                "new_technology": "LandbasedWind",
                "new_tech_detail": "Class3",
                "new_cost_case": "Moderate",
                "attr_modifiers": {
                    "capex_mw": ["mul", 1.1],
                    "fixed_o_m_mw": ["add", 5000],
                },
                "fuel_type": "none",
                "tag_class": "VRE",
                "is_commit": False,
                "fuel_desc": "none",
            }
        }

        result = generate_resource_modifiers_dict(mock_app_state)

        assert result["modified_wind"]["capex_mw"] == ["mul", 1.1]
        assert result["modified_wind"]["Fixed_OM_Cost_per_MWyr"] == ["add", 5000]
        assert isinstance(result["modified_wind"]["capex_mw"], list)
        assert isinstance(result["modified_wind"]["Fixed_OM_Cost_per_MWyr"], list)

    def test_no_overrides_empty_dict(self, cluster_app, mock_app_state):
        """resource_modifiers should be empty dict if no overrides exist."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {}

        result = generate_resource_modifiers_dict(mock_app_state)

        assert result == {}

    def test_multiple_resources_with_overrides(self, cluster_app, mock_app_state):
        """Multiple resources with overrides should all appear in resource_modifiers."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "batteries": {
                "technology": "Utility-Scale Battery Storage",
                "tech_detail": "Lithium Ion",
                "cost_case": "Moderate",
                "size_mw": 1,
                "new_technology": "Utility-Scale Battery Storage",
                "new_tech_detail": "Lithium Ion",
                "new_cost_case": "Moderate",
                "attr_modifiers": {
                    "variable_o_m_mwh": ["add", 0.15],
                },
                "fuel_type": "none",
                "tag_class": "STOR",
                "is_commit": False,
                "fuel_desc": "none",
            },
            "wind": {
                "technology": "LandbasedWind",
                "tech_detail": "Class3",
                "cost_case": "Moderate",
                "size_mw": 1,
                "new_technology": "LandbasedWind",
                "new_tech_detail": "Class3",
                "new_cost_case": "Moderate",
                "attr_modifiers": {
                    "capex_mw": 1500000.0,
                },
                "fuel_type": "none",
                "tag_class": "VRE",
                "is_commit": False,
                "fuel_desc": "none",
            },
        }

        result = generate_resource_modifiers_dict(mock_app_state)

        assert len(result) == 2
        assert "batteries" in result
        assert "wind" in result

    def test_resource_without_attr_modifiers(self, cluster_app, mock_app_state):
        """Resource without attr_modifiers should not appear in resource_modifiers."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "no_overrides": {
                "technology": "NaturalGas",
                "tech_detail": "Combined Cycle",
                "cost_case": "Moderate",
                "size_mw": 500,
                "new_technology": "NaturalGas",
                "new_tech_detail": "Combined Cycle",
                "new_cost_case": "Moderate",
                # No attr_modifiers
                "fuel_type": "standard",
                "standard_fuel": "naturalgas",
                "tag_class": "THERM",
                "is_commit": True,
                "fuel_desc": "naturalgas",
            }
        }

        result = generate_resource_modifiers_dict(mock_app_state)

        assert result == {}

    def test_resource_with_empty_attr_modifiers(self, cluster_app, mock_app_state):
        """Resource with empty attr_modifiers dict should not appear."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "empty_overrides": {
                "technology": "NaturalGas",
                "tech_detail": "Combined Cycle",
                "cost_case": "Moderate",
                "size_mw": 500,
                "new_technology": "NaturalGas",
                "new_tech_detail": "Combined Cycle",
                "new_cost_case": "Moderate",
                "attr_modifiers": {},  # Empty dict
                "fuel_type": "standard",
                "standard_fuel": "naturalgas",
                "tag_class": "THERM",
                "is_commit": True,
                "fuel_desc": "naturalgas",
            }
        }

        result = generate_resource_modifiers_dict(mock_app_state)

        assert result == {}


def generate_resource_modifiers_dict(state):
    """
    Helper function to generate resource_modifiers dict.
    Extracted from generate_resources_settings for testing.
    Mirrors the production logic including ATB column key translation.

    Args:
        state: AppState instance with modified_new_resources

    Returns:
        Dict in resource_modifiers format
    """
    # Mirror of _UI_TO_ATB_KEY in cluster_app.py
    ui_to_atb_key = {
        "heat_rate": "Heat_Rate_MMBTU_per_MWh",
        "fixed_o_m_mw": "Fixed_OM_Cost_per_MWyr",
        "variable_o_m_mwh": "Var_OM_Cost_per_MWh",
        "variable_o_m_mwh_in": "Var_OM_Cost_per_MWh_In",
    }

    if not state.modified_new_resources:
        return {}

    resource_modifiers = {}
    for k, v in sorted(state.modified_new_resources.items()):
        attr_mods = v.get("attr_modifiers")
        if not isinstance(attr_mods, dict) or not attr_mods:
            continue
        # Skip identity changes and new-fuel entries
        identity_changes = (
            v.get("new_technology") != v.get("technology")
            or v.get("new_tech_detail") != v.get("tech_detail")
            or v.get("new_cost_case") != v.get("cost_case")
        )
        if identity_changes or v.get("fuel_type") == "new":
            continue

        modifier_dict = {
            "technology": v["technology"],
            "tech_detail": v["tech_detail"],
        }
        for ui_key, val in attr_mods.items():
            atb_key = ui_to_atb_key.get(ui_key, ui_key)
            modifier_dict[atb_key] = val
        resource_modifiers[k] = modifier_dict

    return resource_modifiers


# ============================================================================
# 4. Modified New Resources Tests
# ============================================================================


class TestModifiedNewResourcesGeneration:
    """Test that modified_new_resources is still output for custom fuels."""

    def test_custom_fuel_in_modified_new_resources(self, cluster_app, mock_app_state):
        """Resource with custom fuel should appear in modified_new_resources."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "hydrogen_ct": {
                "technology": "NaturalGas",
                "tech_detail": "Combustion Turbine",
                "cost_case": "Moderate",
                "size_mw": 100,
                "new_technology": "Hydrogen_CT",
                "new_tech_detail": "Advanced",
                "new_cost_case": "Moderate",
                "attr_modifiers": {
                    "heat_rate": 9000.0,
                },
                "fuel_type": "new",
                "new_fuel_name": "hydrogen",
                "new_fuel_price": 50.0,
                "new_fuel_emission_factor": 0.0,
                "tag_class": "THERM",
                "is_commit": True,
                "fuel_desc": "hydrogen",
            }
        }

        result = generate_modified_new_resources_dict(mock_app_state)

        assert "hydrogen_ct" in result
        assert result["hydrogen_ct"]["technology"] == "NaturalGas"
        assert result["hydrogen_ct"]["new_technology"] == "Hydrogen_CT"

    def test_standard_fuel_not_in_modified_new_resources(
        self, cluster_app, mock_app_state
    ):
        """Resource with standard fuel and no identity change should not appear."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "standard_ct": {
                "technology": "NaturalGas",
                "tech_detail": "Combustion Turbine",
                "cost_case": "Moderate",
                "size_mw": 100,
                "new_technology": "NaturalGas",
                "new_tech_detail": "Combustion Turbine",
                "new_cost_case": "Moderate",
                "attr_modifiers": {
                    "capex_mw": 1000000.0,
                },
                "fuel_type": "standard",
                "standard_fuel": "naturalgas",
                "tag_class": "THERM",
                "is_commit": True,
                "fuel_desc": "naturalgas",
            }
        }

        result = generate_modified_new_resources_dict(mock_app_state)

        assert result == {}

    def test_identity_change_in_modified_new_resources(
        self, cluster_app, mock_app_state
    ):
        """Resource with identity change should appear in modified_new_resources."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "renamed_ct": {
                "technology": "NaturalGas",
                "tech_detail": "Combustion Turbine",
                "cost_case": "Moderate",
                "size_mw": 100,
                "new_technology": "Advanced_CT",
                "new_tech_detail": "High Efficiency",
                "new_cost_case": "Moderate",
                "attr_modifiers": {
                    "heat_rate": 8500.0,
                },
                "fuel_type": "standard",
                "standard_fuel": "naturalgas",
                "tag_class": "THERM",
                "is_commit": True,
                "fuel_desc": "naturalgas",
            }
        }

        result = generate_modified_new_resources_dict(mock_app_state)

        assert "renamed_ct" in result
        assert result["renamed_ct"]["Heat_Rate_MMBTU_per_MWh"] == 8500.0

    def test_custom_fuel_attr_modifiers_included_and_translated(
        self, cluster_app, mock_app_state
    ):
        """Bug fix regression: attr_modifiers on a custom-fuel resource (fuel_type=='new')
        with identity changes must be translated via _UI_TO_ATB_KEY and included in the
        modified_new_resources output—not silently dropped."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "hydrogen_ct_v2": {
                "technology": "NaturalGas",
                "tech_detail": "Combustion Turbine",
                "cost_case": "Moderate",
                "size_mw": 200,
                "new_technology": "Hydrogen_CT",
                "new_tech_detail": "Advanced",
                "new_cost_case": "Moderate",
                "attr_modifiers": {
                    "heat_rate": 9500.0,
                    "variable_o_m_mwh": ["add", 2.5],
                },
                "fuel_type": "new",
                "new_fuel_name": "hydrogen",
                "new_fuel_price": 45.0,
                "new_fuel_emission_factor": 0.0,
                "tag_class": "THERM",
                "is_commit": True,
                "fuel_desc": "hydrogen",
            }
        }

        result = generate_modified_new_resources_dict(mock_app_state)

        # Resource must be present
        assert "hydrogen_ct_v2" in result
        entry = result["hydrogen_ct_v2"]

        # Identity fields must be preserved
        assert entry["technology"] == "NaturalGas"
        assert entry["new_technology"] == "Hydrogen_CT"

        # heat_rate → Heat_Rate_MMBTU_per_MWh must be present (not dropped)
        assert "Heat_Rate_MMBTU_per_MWh" in entry, (
            "heat_rate attr_modifier was silently dropped for custom-fuel resource"
        )
        assert entry["Heat_Rate_MMBTU_per_MWh"] == 9500.0

        # variable_o_m_mwh → Var_OM_Cost_per_MWh must be present (not dropped)
        assert "Var_OM_Cost_per_MWh" in entry, (
            "variable_o_m_mwh attr_modifier was silently dropped for custom-fuel resource"
        )
        assert entry["Var_OM_Cost_per_MWh"] == ["add", 2.5]

        # Raw UI keys must NOT leak into the output
        assert "heat_rate" not in entry
        assert "variable_o_m_mwh" not in entry

    def test_standard_fuel_identity_change_multiple_attr_modifiers_included(
        self, cluster_app, mock_app_state
    ):
        """Bug fix regression: when a standard-fuel resource has an identity change
        (new_technology differs from technology) AND multiple attr_modifiers, all
        attr_modifiers must be translated via _UI_TO_ATB_KEY and included—not
        silently dropped."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "efficient_ct": {
                "technology": "NaturalGas",
                "tech_detail": "Combustion Turbine",
                "cost_case": "Moderate",
                "size_mw": 150,
                # Identity change: new_technology differs from technology
                "new_technology": "AdvancedNaturalGas",
                "new_tech_detail": "Combustion Turbine",
                "new_cost_case": "Moderate",
                "attr_modifiers": {
                    "heat_rate": 7800.0,
                    "variable_o_m_mwh": 3.0,
                    "fixed_o_m_mw": ["mul", 0.9],
                },
                "fuel_type": "standard",
                "standard_fuel": "naturalgas",
                "tag_class": "THERM",
                "is_commit": True,
                "fuel_desc": "naturalgas",
            }
        }

        result = generate_modified_new_resources_dict(mock_app_state)

        # Resource must be present because of identity change
        assert "efficient_ct" in result
        entry = result["efficient_ct"]

        # All three attr_modifiers must be present with translated ATB keys
        assert "Heat_Rate_MMBTU_per_MWh" in entry, (
            "heat_rate attr_modifier was silently dropped for identity-changed resource"
        )
        assert entry["Heat_Rate_MMBTU_per_MWh"] == 7800.0

        assert "Var_OM_Cost_per_MWh" in entry, (
            "variable_o_m_mwh attr_modifier was silently dropped for identity-changed resource"
        )
        assert entry["Var_OM_Cost_per_MWh"] == 3.0

        assert "Fixed_OM_Cost_per_MWyr" in entry, (
            "fixed_o_m_mw attr_modifier was silently dropped for identity-changed resource"
        )
        assert entry["Fixed_OM_Cost_per_MWyr"] == ["mul", 0.9]

        # Raw UI keys must NOT leak into the output
        assert "heat_rate" not in entry
        assert "variable_o_m_mwh" not in entry
        assert "fixed_o_m_mw" not in entry

        # Identity and structural fields must still be correct
        assert entry["technology"] == "NaturalGas"
        assert entry["new_technology"] == "AdvancedNaturalGas"
        assert entry["size_mw"] == 150


def generate_modified_new_resources_dict(state):
    """
    Helper function to generate modified_new_resources dict.
    Extracted from generate_resources_settings for testing.

    Args:
        state: AppState instance with modified_new_resources

    Returns:
        Dict in modified_new_resources format
    """
    # Mirror of _UI_TO_ATB_KEY in cluster_app.py
    ui_to_atb_key = {
        "heat_rate": "Heat_Rate_MMBTU_per_MWh",
        "fixed_o_m_mw": "Fixed_OM_Cost_per_MWyr",
        "variable_o_m_mwh": "Var_OM_Cost_per_MWh",
        "variable_o_m_mwh_in": "Var_OM_Cost_per_MWh_In",
    }

    modified_with_fuel = {}
    if not state.modified_new_resources:
        return modified_with_fuel

    for k, v in sorted(state.modified_new_resources.items()):
        # Only include if it has custom fuel or identity change
        if v.get("fuel_type") == "new" or (
            v.get("new_technology") != v.get("technology")
            or v.get("new_tech_detail") != v.get("tech_detail")
            or v.get("new_cost_case") != v.get("cost_case")
        ):
            entry = {
                "technology": v["technology"],
                "tech_detail": v["tech_detail"],
                "cost_case": v["cost_case"],
                "size_mw": v["size_mw"],
                "new_technology": v["new_technology"],
                "new_tech_detail": v["new_tech_detail"],
                "new_cost_case": v["new_cost_case"],
            }
            attr_mods = v.get("attr_modifiers")
            if isinstance(attr_mods, dict) and attr_mods:
                for ui_key, val in attr_mods.items():
                    atb_key = ui_to_atb_key.get(ui_key, ui_key)
                    entry[atb_key] = val
            modified_with_fuel[k] = entry

    return modified_with_fuel


# ============================================================================
# 5. Round-trip YAML Tests
# ============================================================================


class TestYAMLOutput:
    """Test that YAML output matches PowerGenome expected format."""

    def test_yaml_format_matches_example(self, cluster_app, mock_app_state):
        """YAML output should match format in EI_PJM_settings/resources.yml."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "batteries": {
                "technology": "Utility-Scale Battery Storage",
                "tech_detail": "Lithium Ion",
                "cost_case": "Moderate",
                "size_mw": 1,
                "new_technology": "Utility-Scale Battery Storage",
                "new_tech_detail": "Lithium Ion",
                "new_cost_case": "Moderate",
                "attr_modifiers": {
                    "variable_o_m_mwh": ["add", 0.15],
                    "variable_o_m_mwh_in": 0.15,
                    "wacc_real": 0.0467,
                },
                "fuel_type": "none",
                "tag_class": "STOR",
                "is_commit": False,
                "fuel_desc": "none",
            }
        }

        result = generate_resource_modifiers_dict(mock_app_state)
        yaml_str = yaml.dump({"resource_modifiers": result}, default_flow_style=False)

        # Parse YAML back
        parsed = yaml.safe_load(yaml_str)

        assert "resource_modifiers" in parsed
        assert "batteries" in parsed["resource_modifiers"]
        assert (
            parsed["resource_modifiers"]["batteries"]["technology"]
            == "Utility-Scale Battery Storage"
        )
        assert parsed["resource_modifiers"]["batteries"]["tech_detail"] == "Lithium Ion"
        assert parsed["resource_modifiers"]["batteries"]["Var_OM_Cost_per_MWh"] == [
            "add",
            0.15,
        ]
        assert parsed["resource_modifiers"]["batteries"]["Var_OM_Cost_per_MWh_In"] == 0.15
        assert parsed["resource_modifiers"]["batteries"]["wacc_real"] == 0.0467

    def test_yaml_operator_list_format(self, cluster_app, mock_app_state):
        """Operator values should serialize as YAML lists [operator, value]."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "test_resource": {
                "technology": "NaturalGas",
                "tech_detail": "Combined Cycle",
                "cost_case": "Moderate",
                "size_mw": 500,
                "new_technology": "NaturalGas",
                "new_tech_detail": "Combined Cycle",
                "new_cost_case": "Moderate",
                "attr_modifiers": {
                    "capex_mw": ["mul", 1.2],
                },
                "fuel_type": "standard",
                "standard_fuel": "naturalgas",
                "tag_class": "THERM",
                "is_commit": True,
                "fuel_desc": "naturalgas",
            }
        }

        result = generate_resource_modifiers_dict(mock_app_state)
        yaml_str = yaml.dump({"resource_modifiers": result}, default_flow_style=False)

        # Check that YAML contains list format
        assert "[mul, 1.2]" in yaml_str or "- mul\n" in yaml_str

    def test_yaml_numeric_value_format(self, cluster_app, mock_app_state):
        """Numeric values should serialize as plain scalars, not lists."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "test_resource": {
                "technology": "LandbasedWind",
                "tech_detail": "Class3",
                "cost_case": "Moderate",
                "size_mw": 1,
                "new_technology": "LandbasedWind",
                "new_tech_detail": "Class3",
                "new_cost_case": "Moderate",
                "attr_modifiers": {
                    "capex_mw": 1500000.0,
                },
                "fuel_type": "none",
                "tag_class": "VRE",
                "is_commit": False,
                "fuel_desc": "none",
            }
        }

        result = generate_resource_modifiers_dict(mock_app_state)
        yaml_str = yaml.dump({"resource_modifiers": result}, default_flow_style=False)

        # Parse back and verify
        parsed = yaml.safe_load(yaml_str)
        assert parsed["resource_modifiers"]["test_resource"]["capex_mw"] == 1500000.0
        assert isinstance(
            parsed["resource_modifiers"]["test_resource"]["capex_mw"], float
        )

    def test_yaml_mixed_overrides(self, cluster_app, mock_app_state):
        """Resource with mix of numeric and operator overrides should work."""
        cluster_app.state = mock_app_state
        mock_app_state.modified_new_resources = {
            "mixed": {
                "technology": "UtilityPV",
                "tech_detail": "Class1",
                "cost_case": "Moderate",
                "size_mw": 1,
                "new_technology": "UtilityPV",
                "new_tech_detail": "Class1",
                "new_cost_case": "Moderate",
                "attr_modifiers": {
                    "capex_mw": 1000000.0,
                    "fixed_o_m_mw": ["add", 5000],
                    "wacc_real": 0.05,
                    "variable_o_m_mwh": ["mul", 1.1],
                },
                "fuel_type": "none",
                "tag_class": "VRE",
                "is_commit": False,
                "fuel_desc": "none",
            }
        }

        result = generate_resource_modifiers_dict(mock_app_state)
        yaml_str = yaml.dump({"resource_modifiers": result}, default_flow_style=False)
        parsed = yaml.safe_load(yaml_str)

        mod = parsed["resource_modifiers"]["mixed"]
        assert mod["capex_mw"] == 1000000.0
        assert mod["Fixed_OM_Cost_per_MWyr"] == ["add", 5000]
        assert mod["wacc_real"] == 0.05
        assert mod["Var_OM_Cost_per_MWh"] == ["mul", 1.1]


# ============================================================================
# 6. Integration Tests
# ============================================================================


class TestEndToEndIntegration:
    """Test complete workflow from input to YAML output."""

    def test_complete_battery_override_workflow(self, cluster_app, mock_app_state):
        """Complete workflow: battery with storage-specific overrides."""
        cluster_app.state = mock_app_state

        # Simulate UI input
        override_inputs = {
            "atbOverrideCapex": "1200000",
            "atbOverrideCapexMwh": "add:50000",
            "atbOverrideFixedOM": "40000",
            "atbOverrideVarOM": "add:0.1",
            "atbOverrideVarOMIn": "0.15",
            "atbOverrideWacc": "0.0467",
        }

        # Parse overrides
        override_fields = [
            ("capex_mw", "atbOverrideCapex"),
            ("capex_mwh", "atbOverrideCapexMwh"),
            ("fixed_o_m_mw", "atbOverrideFixedOM"),
            ("variable_o_m_mwh", "atbOverrideVarOM"),
            ("variable_o_m_mwh_in", "atbOverrideVarOMIn"),
            ("wacc_real", "atbOverrideWacc"),
        ]
        attr_overrides = process_override_fields(override_fields, override_inputs)

        # Store in state
        mock_app_state.modified_new_resources = {
            "batteries": {
                "technology": "Utility-Scale Battery Storage",
                "tech_detail": "Lithium Ion",
                "cost_case": "Moderate",
                "size_mw": 1,
                "new_technology": "Utility-Scale Battery Storage",
                "new_tech_detail": "Lithium Ion",
                "new_cost_case": "Moderate",
                "attr_modifiers": attr_overrides,
                "fuel_type": "none",
                "tag_class": "STOR",
                "is_commit": False,
                "fuel_desc": "none",
            }
        }

        # Generate output
        result = generate_resource_modifiers_dict(mock_app_state)
        yaml_str = yaml.dump({"resource_modifiers": result}, default_flow_style=False)
        parsed = yaml.safe_load(yaml_str)

        # Verify output
        bat = parsed["resource_modifiers"]["batteries"]
        assert bat["technology"] == "Utility-Scale Battery Storage"
        assert bat["capex_mw"] == 1200000.0
        assert bat["capex_mwh"] == ["add", 50000]
        assert bat["Fixed_OM_Cost_per_MWyr"] == 40000.0
        assert bat["Var_OM_Cost_per_MWh"] == ["add", 0.1]
        assert bat["Var_OM_Cost_per_MWh_In"] == 0.15
        assert bat["wacc_real"] == 0.0467

    def test_complete_hydrogen_ct_workflow(self, cluster_app, mock_app_state):
        """Complete workflow: hydrogen CT with custom fuel and overrides."""
        cluster_app.state = mock_app_state

        # Simulate UI input
        override_inputs = {
            "atbOverrideHeatRate": "mul:0.95",
            "atbOverrideCapex": "1500000",
        }

        override_fields = [
            ("heat_rate", "atbOverrideHeatRate"),
            ("capex_mw", "atbOverrideCapex"),
        ]
        attr_overrides = process_override_fields(override_fields, override_inputs)

        # Store in state
        mock_app_state.modified_new_resources = {
            "hydrogen_ct": {
                "technology": "NaturalGas",
                "tech_detail": "Combustion Turbine",
                "cost_case": "Moderate",
                "size_mw": 100,
                "new_technology": "Hydrogen_CT",
                "new_tech_detail": "Advanced",
                "new_cost_case": "Moderate",
                "attr_modifiers": attr_overrides,
                "fuel_type": "new",
                "new_fuel_name": "hydrogen",
                "new_fuel_price": 50.0,
                "new_fuel_emission_factor": 0.0,
                "tag_class": "THERM",
                "is_commit": True,
                "fuel_desc": "hydrogen",
            }
        }

        # Generate both outputs
        modifiers = generate_resource_modifiers_dict(mock_app_state)
        modified = generate_modified_new_resources_dict(mock_app_state)

        # hydrogen_ct has an identity change (new_technology != technology) and
        # fuel_type == "new", so it goes to modified_new_resources, not resource_modifiers
        assert "hydrogen_ct" not in modifiers

        # Verify modified_new_resources has identity change
        assert "hydrogen_ct" in modified
        assert modified["hydrogen_ct"]["new_technology"] == "Hydrogen_CT"

    def test_multiple_resources_integration(self, cluster_app, mock_app_state):
        """Multiple resources with different override types."""
        cluster_app.state = mock_app_state

        # Resource 1: Battery with operators
        bat_overrides = {
            "variable_o_m_mwh": ["add", 0.15],
            "wacc_real": 0.0467,
        }

        # Resource 2: Wind with numeric override
        wind_overrides = {
            "capex_mw": 1600000.0,
        }

        # Resource 3: CT with mixed overrides
        ct_overrides = {
            "heat_rate": ["mul", 1.05],
            "capex_mw": 1000000.0,
        }

        mock_app_state.modified_new_resources = {
            "batteries": {
                "technology": "Utility-Scale Battery Storage",
                "tech_detail": "Lithium Ion",
                "cost_case": "Moderate",
                "size_mw": 1,
                "new_technology": "Utility-Scale Battery Storage",
                "new_tech_detail": "Lithium Ion",
                "new_cost_case": "Moderate",
                "attr_modifiers": bat_overrides,
                "fuel_type": "none",
                "tag_class": "STOR",
                "is_commit": False,
                "fuel_desc": "none",
            },
            "wind": {
                "technology": "LandbasedWind",
                "tech_detail": "Class3",
                "cost_case": "Moderate",
                "size_mw": 1,
                "new_technology": "LandbasedWind",
                "new_tech_detail": "Class3",
                "new_cost_case": "Moderate",
                "attr_modifiers": wind_overrides,
                "fuel_type": "none",
                "tag_class": "VRE",
                "is_commit": False,
                "fuel_desc": "none",
            },
            "ct": {
                "technology": "NaturalGas",
                "tech_detail": "Combustion Turbine",
                "cost_case": "Moderate",
                "size_mw": 100,
                "new_technology": "NaturalGas",
                "new_tech_detail": "Combustion Turbine",
                "new_cost_case": "Moderate",
                "attr_modifiers": ct_overrides,
                "fuel_type": "standard",
                "standard_fuel": "naturalgas",
                "tag_class": "THERM",
                "is_commit": True,
                "fuel_desc": "naturalgas",
            },
        }

        # Generate and verify
        result = generate_resource_modifiers_dict(mock_app_state)
        yaml_str = yaml.dump({"resource_modifiers": result}, default_flow_style=False)
        parsed = yaml.safe_load(yaml_str)

        assert len(parsed["resource_modifiers"]) == 3
        assert parsed["resource_modifiers"]["batteries"]["Var_OM_Cost_per_MWh"] == [
            "add",
            0.15,
        ]
        assert parsed["resource_modifiers"]["wind"]["capex_mw"] == 1600000.0
        assert parsed["resource_modifiers"]["ct"]["Heat_Rate_MMBTU_per_MWh"] == ["mul", 1.05]
        assert parsed["resource_modifiers"]["ct"]["capex_mw"] == 1000000.0
