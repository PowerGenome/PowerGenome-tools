"""
Tests for _DEFAULT_NEW_RESOURCES constant and AppState.new_resources initialization
in web/cluster_app.py.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixture
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
# Expected data
# ---------------------------------------------------------------------------

EXPECTED_ENTRIES = [
    {
        "technology": "NaturalGas",
        "tech_detail": "2-on-1 Combined Cycle (H-Frame)",
        "cost_case": "Moderate",
        "size_mw": 992,
        "planning_year": "all",
    },
    {
        "technology": "NaturalGas",
        "tech_detail": "Combustion Turbine (F-Frame)",
        "cost_case": "Moderate",
        "size_mw": 233,
        "planning_year": "all",
    },
    {
        "technology": "LandbasedWind",
        "tech_detail": "Class3",
        "cost_case": "Moderate",
        "size_mw": 200,
        "planning_year": "all",
    },
    {
        "technology": "UtilityPV",
        "tech_detail": "Class1",
        "cost_case": "Moderate",
        "size_mw": 100,
        "planning_year": "all",
    },
    {
        "technology": "Utility-Scale Battery Storage",
        "tech_detail": "Lithium Ion",
        "cost_case": "Moderate",
        "size_mw": 60,
        "variable_o_m_mwh": 0.15,
        "variable_o_m_mwh_in": 0.15,
        "wacc_real": 0.05,
        "planning_year": "all",
    },
    {
        "technology": "Nuclear",
        "tech_detail": "Nuclear - Large",
        "cost_case": "Moderate",
        "size_mw": 1000,
        "planning_year": "all",
    },
]

REQUIRED_KEYS = {
    "technology",
    "tech_detail",
    "cost_case",
    "size_mw",
    "planning_year",
    "data_year",
}
BATTERY_OPTIONAL_KEYS = {"variable_o_m_mwh", "variable_o_m_mwh_in", "wacc_real"}


# ---------------------------------------------------------------------------
# Tests for _DEFAULT_NEW_RESOURCES constant
# ---------------------------------------------------------------------------


class TestDefaultNewResourcesConstant:
    def test_is_a_list(self, cluster_app):
        """_DEFAULT_NEW_RESOURCES must be a plain list, not a tuple or generator."""
        assert isinstance(cluster_app._DEFAULT_NEW_RESOURCES, list)

    def test_has_exactly_six_entries(self, cluster_app):
        assert len(cluster_app._DEFAULT_NEW_RESOURCES) == 6

    def test_all_entries_are_dicts(self, cluster_app):
        for entry in cluster_app._DEFAULT_NEW_RESOURCES:
            assert isinstance(entry, dict), f"Expected dict, got {type(entry)}"

    def test_each_entry_has_required_keys(self, cluster_app):
        for i, entry in enumerate(cluster_app._DEFAULT_NEW_RESOURCES):
            assert REQUIRED_KEYS.issubset(
                entry.keys()
            ), f"Entry {i} is missing required keys: {REQUIRED_KEYS - set(entry.keys())}"

    def test_only_battery_entry_has_variable_om_defaults(self, cluster_app):
        for i, entry in enumerate(cluster_app._DEFAULT_NEW_RESOURCES):
            extra_battery_keys = BATTERY_OPTIONAL_KEYS.intersection(entry.keys())
            if (
                entry["technology"] == "Utility-Scale Battery Storage"
                and entry["tech_detail"] == "Lithium Ion"
            ):
                assert extra_battery_keys == BATTERY_OPTIONAL_KEYS
                assert entry["variable_o_m_mwh"] == 0.15
                assert entry["variable_o_m_mwh_in"] == 0.15
                assert entry["wacc_real"] == 0.05
            else:
                assert (
                    not extra_battery_keys
                ), f"Entry {i} unexpectedly has battery-only keys: {extra_battery_keys}"

    def test_no_unexpected_keys(self, cluster_app):
        """Each entry must have only the expected keys (no extras beyond required + optional battery keys)."""
        for i, entry in enumerate(cluster_app._DEFAULT_NEW_RESOURCES):
            entry_keys = set(entry.keys())
            if (
                entry["technology"] == "Utility-Scale Battery Storage"
                and entry["tech_detail"] == "Lithium Ion"
            ):
                # Battery entry can have required keys + battery optional keys
                allowed_keys = REQUIRED_KEYS | BATTERY_OPTIONAL_KEYS
                unexpected_keys = entry_keys - allowed_keys
                assert not unexpected_keys, (
                    f"Battery entry {i} has unexpected keys: {unexpected_keys}. "
                    f"Expected keys: {allowed_keys}"
                )
            else:
                # Non-battery entries should only have required keys
                unexpected_keys = entry_keys - REQUIRED_KEYS
                assert not unexpected_keys, (
                    f"Entry {i} has unexpected keys: {unexpected_keys}. "
                    f"Expected keys: {REQUIRED_KEYS}"
                )

    def test_all_entries_have_planning_year_all(self, cluster_app):
        for i, entry in enumerate(cluster_app._DEFAULT_NEW_RESOURCES):
            assert (
                entry["planning_year"] == "all"
            ), f"Entry {i} has planning_year={entry['planning_year']!r}, expected 'all'"

    @pytest.mark.parametrize("index,expected", list(enumerate(EXPECTED_ENTRIES)))
    def test_entry_content(self, cluster_app, index, expected):
        """Each entry must match the expected values exactly."""
        actual = cluster_app._DEFAULT_NEW_RESOURCES[index]
        assert actual["technology"] == expected["technology"]
        assert actual["tech_detail"] == expected["tech_detail"]
        assert actual["cost_case"] == expected["cost_case"]
        assert actual["size_mw"] == expected["size_mw"]
        assert actual["planning_year"] == expected["planning_year"]

    def test_entries_are_mutable_dicts(self, cluster_app):
        """Entries should be ordinary dicts (mutable), not frozensets or NamedTuples."""
        entry = cluster_app._DEFAULT_NEW_RESOURCES[0]
        original_size = entry["size_mw"]
        entry["size_mw"] = 9999
        assert cluster_app._DEFAULT_NEW_RESOURCES[0]["size_mw"] == 9999
        # restore
        entry["size_mw"] = original_size


# ---------------------------------------------------------------------------
# Tests for AppState.new_resources initialization
# ---------------------------------------------------------------------------


class TestAppStateNewResourcesInit:
    def test_new_resources_is_populated_on_init(self, cluster_app):
        """A fresh AppState must have new_resources pre-populated."""
        state = cluster_app.AppState()
        assert len(state.new_resources) == 6

    def test_new_resources_content_matches_defaults(self, cluster_app):
        """AppState.new_resources must contain the same data as _DEFAULT_NEW_RESOURCES."""
        state = cluster_app.AppState()
        for i, (actual, expected) in enumerate(
            zip(state.new_resources, cluster_app._DEFAULT_NEW_RESOURCES)
        ):
            assert actual == expected, f"Mismatch at index {i}: {actual} != {expected}"

    def test_new_resources_items_are_copies_not_same_objects(self, cluster_app):
        """Items in AppState.new_resources must be independent copies of the originals."""
        state = cluster_app.AppState()
        for i, (state_item, default_item) in enumerate(
            zip(state.new_resources, cluster_app._DEFAULT_NEW_RESOURCES)
        ):
            assert (
                state_item is not default_item
            ), f"Entry {i} is the same object as _DEFAULT_NEW_RESOURCES[{i}]"

    def test_mutating_instance_does_not_affect_defaults(self, cluster_app):
        """Mutating AppState.new_resources must not change _DEFAULT_NEW_RESOURCES."""
        state = cluster_app.AppState()
        original_tech = cluster_app._DEFAULT_NEW_RESOURCES[0]["technology"]

        state.new_resources[0]["technology"] = "MUTATED"

        assert cluster_app._DEFAULT_NEW_RESOURCES[0]["technology"] == original_tech

    def test_mutating_instance_does_not_affect_other_instance(self, cluster_app):
        """Two AppState instances must have fully independent new_resources lists."""
        state_a = cluster_app.AppState()
        state_b = cluster_app.AppState()

        state_a.new_resources[0]["technology"] = "CHANGED"

        assert state_b.new_resources[0]["technology"] != "CHANGED"

    def test_two_instances_have_independent_lists(self, cluster_app):
        """Appending to one instance's list must not affect another's."""
        state_a = cluster_app.AppState()
        state_b = cluster_app.AppState()

        state_a.new_resources.append(
            {
                "technology": "Extra",
                "tech_detail": "X",
                "cost_case": "Low",
                "size_mw": 1,
                "planning_year": "all",
            }
        )

        assert len(state_a.new_resources) == 7
        assert len(state_b.new_resources) == 6

    def test_new_resources_all_have_planning_year_all(self, cluster_app):
        """Every default resource in a fresh AppState must have planning_year == 'all'."""
        state = cluster_app.AppState()
        for i, resource in enumerate(state.new_resources):
            assert (
                resource["planning_year"] == "all"
            ), f"new_resources[{i}] has planning_year={resource['planning_year']!r}"

    def test_new_resources_list_is_not_same_object_as_defaults(self, cluster_app):
        """The new_resources list itself must be a new list, not _DEFAULT_NEW_RESOURCES."""
        state = cluster_app.AppState()
        assert state.new_resources is not cluster_app._DEFAULT_NEW_RESOURCES

    @pytest.mark.parametrize("index,expected", list(enumerate(EXPECTED_ENTRIES)))
    def test_each_new_resource_entry(self, cluster_app, index, expected):
        """Parametrised check: each entry in AppState.new_resources matches expected."""
        state = cluster_app.AppState()
        actual = state.new_resources[index]
        assert actual["technology"] == expected["technology"]
        assert actual["tech_detail"] == expected["tech_detail"]
        assert actual["cost_case"] == expected["cost_case"]
        assert actual["size_mw"] == expected["size_mw"]
        assert actual["planning_year"] == expected["planning_year"]


class TestNewResourcesListRendering:
    def test_regular_battery_defaults_render_in_resource_list(self, cluster_app):
        """Default battery modifier fields should be visible in the regular resource list."""
        container = MagicMock()
        container.innerHTML = ""

        def _get_element_by_id(element_id):
            if element_id == "newResourcesList":
                return container
            return None

        cluster_app.document.getElementById.side_effect = _get_element_by_id

        cluster_app.state.new_resources = [
            {
                "technology": "Utility-Scale Battery Storage",
                "tech_detail": "Lithium Ion",
                "cost_case": "Moderate",
                "size_mw": 60,
                "variable_o_m_mwh": 0.15,
                "variable_o_m_mwh_in": 0.15,
                "wacc_real": 0.05,
                "planning_year": "all",
            }
        ]
        cluster_app.state.modified_new_resources = {}

        cluster_app.render_new_resources_list()

        assert "variable_o_m_mwh=0.15" in container.innerHTML
        assert "variable_o_m_mwh_in=0.15" in container.innerHTML
        assert "wacc_real=0.05" in container.innerHTML
        assert "background-color: #fff3cd;" in container.innerHTML

    def test_regular_resource_capex_override_renders_in_resource_list(
        self, cluster_app
    ):
        """Regular resources should render supported inline overrides beyond battery defaults."""
        container = MagicMock()
        container.innerHTML = ""

        def _get_element_by_id(element_id):
            if element_id == "newResourcesList":
                return container
            return None

        cluster_app.document.getElementById.side_effect = _get_element_by_id

        cluster_app.state.new_resources = [
            {
                "technology": "NaturalGas",
                "tech_detail": "Combustion Turbine (F-Frame)",
                "cost_case": "Moderate",
                "size_mw": 233,
                "capex_mw": 1234.5,
                "planning_year": "all",
            }
        ]
        cluster_app.state.modified_new_resources = {}

        cluster_app.render_new_resources_list()

        assert "capex_mw=1234.5" in container.innerHTML
        assert "background-color: #fff3cd;" in container.innerHTML
