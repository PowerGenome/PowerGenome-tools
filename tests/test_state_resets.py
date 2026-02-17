"""
Test suite for state reset functionality in cluster_app.py.

Tests the reset_region_dependent_state() and reset_planning_year_dependent_state()
functions to ensure proper cascading resets when upstream selections change.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Load cluster_app.py with mocked PyScript dependencies
@pytest.fixture()
def cluster_app():
    """Load cluster_app module with mocked js and DOM dependencies."""
    # Save any existing sys.modules entries we are about to mock
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
        # Mock the js and pyodide.ffi modules
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

        # Add mock L (Leaflet) object
        mock_js.L = MagicMock()
        mock_js.document = MagicMock()
        mock_js.window = MagicMock()
        mock_js.fetch = MagicMock()
        mock_js.Uint8Array = MagicMock()
        mock_js.globalThis = MagicMock()

        # Load the module
        web_dir = Path(__file__).parent.parent / "web"
        module_path = web_dir / "cluster_app.py"

        spec = importlib.util.spec_from_file_location("cluster_app", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["cluster_app"] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)

        yield module
    finally:
        # Restore original sys.modules entries
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


class TestResetRegionDependentState:
    """Test reset_region_dependent_state() function."""

    def test_resets_plant_cluster_settings(self, cluster_app):
        """Test that plant clustering settings are reset."""
        cluster_app.state.plant_cluster_settings = {"num_clusters": 100}
        cluster_app.reset_region_dependent_state()
        assert cluster_app.state.plant_cluster_settings is None

    def test_resets_resource_group_files(self, cluster_app):
        """Test that resource group files are reset."""
        cluster_app.state.resource_group_files = {"file1.parquet": b"data"}
        cluster_app.reset_region_dependent_state()
        assert cluster_app.state.resource_group_files == {}

    def test_resets_resource_group_assignments(self, cluster_app):
        """Test that resource group assignments are reset."""
        cluster_app.state.resource_group_assignments = MagicMock()
        cluster_app.reset_region_dependent_state()
        assert cluster_app.state.resource_group_assignments is None

    def test_resets_renewables_clusters(self, cluster_app):
        """Test that renewables cluster settings are reset."""
        cluster_app.state.renewables_clusters = [{"tech": "wind"}]
        cluster_app.state.renewables_clusters_info = {"summary": "test"}
        cluster_app.reset_region_dependent_state()
        assert cluster_app.state.renewables_clusters is None
        assert cluster_app.state.renewables_clusters_info is None

    def test_resets_renewables_capacity_attributes(self, cluster_app):
        """Test that all renewables capacity tracking attributes are reset."""
        cluster_app.state.renewables_region_capacity_mw = {"wind": {"region1": 1000}}
        cluster_app.state.renewables_region_base_capacity_mw = {"wind": {"region1": 800}}
        cluster_app.state.renewables_pending_region_capacity_mw = {"wind": {"region1": 1200}}
        cluster_app.state.renewables_region_available_mw = {"wind": {"region1": 5000}}

        cluster_app.reset_region_dependent_state()

        assert cluster_app.state.renewables_region_capacity_mw == {}
        assert cluster_app.state.renewables_region_base_capacity_mw == {}
        assert cluster_app.state.renewables_pending_region_capacity_mw == {}
        assert cluster_app.state.renewables_region_available_mw == {}

    def test_resets_renewables_capacity_overrides(self, cluster_app):
        """Test that renewables capacity overrides are reset to default structure."""
        cluster_app.state.renewables_capacity_overrides_mw = {
            "landbasedwind": {"region1": 1000},
            "utilitypv": {"region2": 500},
        }

        cluster_app.reset_region_dependent_state()

        # Should be reset to empty dicts for each tech
        assert cluster_app.state.renewables_capacity_overrides_mw == {
            "landbasedwind": {},
            "utilitypv": {},
        }

    def test_resets_renewables_curve_data_and_ui_state(self, cluster_app):
        """Test that renewables curve data and UI state are reset."""
        cluster_app.state.renewables_curve_data = {"wind": {"region1": {}}}
        cluster_app.state.renewables_selected_region = "region1"
        cluster_app.state.renewables_regions_geojson_cache = {"type": "FeatureCollection"}
        cluster_app.state.renewables_regions_geojson_key = ("r1", "r2")

        cluster_app.reset_region_dependent_state()

        assert cluster_app.state.renewables_curve_data == {}
        assert cluster_app.state.renewables_selected_region is None
        assert cluster_app.state.renewables_regions_geojson_cache is None
        assert cluster_app.state.renewables_regions_geojson_key is None

    def test_resets_esr_zones_and_policies(self, cluster_app):
        """Test that ESR zones and policies are reset."""
        cluster_app.state.esr_zones = [{"zone1"}, {"zone2"}]
        cluster_app.state.esr_map = {"esr_1": ["region1"]}
        cluster_app.state.esr_type_map = {"esr_1": "RPS"}
        cluster_app.state.esr_policy_states = {"esr_1": {"CA"}}
        cluster_app.state.emission_policies_df = MagicMock()

        cluster_app.reset_region_dependent_state()

        assert cluster_app.state.esr_zones is None
        assert cluster_app.state.esr_map is None
        assert cluster_app.state.esr_type_map is None
        assert cluster_app.state.esr_policy_states is None
        assert cluster_app.state.emission_policies_df is None

    def test_complete_reset_all_attributes(self, cluster_app):
        """Test that all region-dependent attributes are reset together."""
        # Set all attributes to non-default values
        cluster_app.state.plant_cluster_settings = {"data": "test"}
        cluster_app.state.resource_group_files = {"file": "data"}
        cluster_app.state.resource_group_assignments = MagicMock()
        cluster_app.state.renewables_clusters = ["data"]
        cluster_app.state.renewables_clusters_info = {"data": "test"}
        cluster_app.state.renewables_region_capacity_mw = {"data": "test"}
        cluster_app.state.renewables_region_base_capacity_mw = {"data": "test"}
        cluster_app.state.renewables_pending_region_capacity_mw = {"data": "test"}
        cluster_app.state.renewables_region_available_mw = {"data": "test"}
        cluster_app.state.renewables_capacity_overrides_mw = {"data": "test"}
        cluster_app.state.renewables_curve_data = {"data": "test"}
        cluster_app.state.renewables_selected_region = "region1"
        cluster_app.state.renewables_regions_geojson_cache = {"data": "test"}
        cluster_app.state.renewables_regions_geojson_key = "key"
        cluster_app.state.esr_zones = ["zone"]
        cluster_app.state.esr_map = {"data": "test"}
        cluster_app.state.esr_type_map = {"data": "test"}
        cluster_app.state.esr_policy_states = {"data": "test"}
        cluster_app.state.emission_policies_df = MagicMock()

        # Reset all
        cluster_app.reset_region_dependent_state()

        # Verify all are reset
        assert cluster_app.state.plant_cluster_settings is None
        assert cluster_app.state.resource_group_files == {}
        assert cluster_app.state.resource_group_assignments is None
        assert cluster_app.state.renewables_clusters is None
        assert cluster_app.state.renewables_clusters_info is None
        assert cluster_app.state.renewables_region_capacity_mw == {}
        assert cluster_app.state.renewables_region_base_capacity_mw == {}
        assert cluster_app.state.renewables_pending_region_capacity_mw == {}
        assert cluster_app.state.renewables_region_available_mw == {}
        assert cluster_app.state.renewables_capacity_overrides_mw == {
            "landbasedwind": {},
            "utilitypv": {},
        }
        assert cluster_app.state.renewables_curve_data == {}
        assert cluster_app.state.renewables_selected_region is None
        assert cluster_app.state.renewables_regions_geojson_cache is None
        assert cluster_app.state.renewables_regions_geojson_key is None
        assert cluster_app.state.esr_zones is None
        assert cluster_app.state.esr_map is None
        assert cluster_app.state.esr_type_map is None
        assert cluster_app.state.esr_policy_states is None
        assert cluster_app.state.emission_policies_df is None


class TestResetPlanningYearDependentState:
    """Test reset_planning_year_dependent_state() function."""

    def test_resets_esr_policies(self, cluster_app):
        """Test that ESR policies are reset."""
        cluster_app.state.esr_map = {"esr_1": ["region1"]}
        cluster_app.state.esr_type_map = {"esr_1": "RPS"}
        cluster_app.state.esr_policy_states = {"esr_1": {"CA"}}
        cluster_app.state.emission_policies_df = MagicMock()

        cluster_app.reset_planning_year_dependent_state()

        assert cluster_app.state.esr_map is None
        assert cluster_app.state.esr_type_map is None
        assert cluster_app.state.esr_policy_states is None
        assert cluster_app.state.emission_policies_df is None

    def test_does_not_reset_esr_zones(self, cluster_app):
        """Test that ESR zones are NOT reset (they depend on regions, not years)."""
        cluster_app.state.esr_zones = [{"zone1"}, {"zone2"}]

        cluster_app.reset_planning_year_dependent_state()

        # esr_zones should remain unchanged
        assert cluster_app.state.esr_zones == [{"zone1"}, {"zone2"}]

    def test_does_not_reset_region_dependent_state(self, cluster_app):
        """Test that region-dependent state is NOT reset."""
        cluster_app.state.plant_cluster_settings = {"data": "test"}
        cluster_app.state.renewables_clusters = ["data"]

        cluster_app.reset_planning_year_dependent_state()

        # These should remain unchanged
        assert cluster_app.state.plant_cluster_settings == {"data": "test"}
        assert cluster_app.state.renewables_clusters == ["data"]


class TestResetFunctionIntegration:
    """Test integration of reset functions with event handlers."""

    def test_on_clear_selection_calls_reset(self, cluster_app):
        """Test that on_clear_selection calls reset_region_dependent_state."""
        # Set up state
        cluster_app.state.selected_bas = set()
        cluster_app.state.ba_layers = {}
        cluster_app.state.plant_cluster_settings = {"data": "test"}

        # Mock DOM elements and functions
        with patch.object(cluster_app, "update_selected_display"):
            with patch.object(cluster_app, "update_tooltips"):
                with patch.object(cluster_app, "update_transmission_lines"):
                    with patch.object(cluster_app, "update_default_cluster_budget"):
                        event = MagicMock()
                        cluster_app.on_clear_selection(event)

        # Verify reset was called
        assert cluster_app.state.plant_cluster_settings is None

    def test_on_region_mode_change_to_manual_calls_reset(self, cluster_app):
        """Test that switching to manual mode calls reset_region_dependent_state."""
        cluster_app.state.plant_cluster_settings = {"data": "test"}

        with patch.object(cluster_app, "update_manual_regions_display"):
            with patch.object(cluster_app, "update_unassigned_display"):
                cluster_app.on_region_mode_change(True)

        # Verify reset was called
        assert cluster_app.state.plant_cluster_settings is None

    def test_on_clear_manual_regions_calls_reset(self, cluster_app):
        """Test that on_clear_manual_regions calls reset_region_dependent_state."""
        cluster_app.state.ba_layers = {}
        cluster_app.state.plant_cluster_settings = {"data": "test"}

        with patch.object(cluster_app, "update_manual_regions_display"):
            with patch.object(cluster_app, "update_unassigned_display"):
                with patch.object(cluster_app, "update_tooltips"):
                    with patch.object(cluster_app, "set_status"):
                        mock_doc = MagicMock()
                        mock_doc.getElementById.return_value = None
                        with patch.object(cluster_app, "document", mock_doc):
                            event = MagicMock()
                            cluster_app.on_clear_manual_regions(event)

        # Verify reset was called
        assert cluster_app.state.plant_cluster_settings is None

    def test_on_model_years_change_calls_reset(self, cluster_app):
        """Test that on_model_years_change calls reset_planning_year_dependent_state."""
        cluster_app.state.esr_map = {"esr_1": ["region1"]}

        event = MagicMock()
        cluster_app.on_model_years_change(event)

        # Verify reset was called
        assert cluster_app.state.esr_map is None


class TestStateDependencyIsolation:
    """Test that different reset functions maintain proper isolation."""

    def test_region_reset_does_not_affect_base_data(self, cluster_app):
        """Test that region reset doesn't clear base data like hierarchy_df."""
        cluster_app.state.hierarchy_df = MagicMock()
        cluster_app.state.transmission_df = MagicMock()
        cluster_app.state.plants_df = MagicMock()

        cluster_app.reset_region_dependent_state()

        # Base data should remain
        assert cluster_app.state.hierarchy_df is not None
        assert cluster_app.state.transmission_df is not None
        assert cluster_app.state.plants_df is not None

    def test_planning_year_reset_does_not_affect_regions(self, cluster_app):
        """Test that planning year reset doesn't clear region state."""
        cluster_app.state.region_aggregations = {"region1": ["ba1"]}
        cluster_app.state.is_clustered = True
        cluster_app.state.ba_to_region = {"ba1": "region1"}

        cluster_app.reset_planning_year_dependent_state()

        # Region state should remain
        assert cluster_app.state.region_aggregations == {"region1": ["ba1"]}
        assert cluster_app.state.is_clustered is True
        assert cluster_app.state.ba_to_region == {"ba1": "region1"}

    def test_reset_functions_are_idempotent(self, cluster_app):
        """Test that calling reset functions multiple times is safe."""
        # First reset
        cluster_app.reset_region_dependent_state()
        cluster_app.reset_planning_year_dependent_state()

        # Second reset should not raise errors
        cluster_app.reset_region_dependent_state()
        cluster_app.reset_planning_year_dependent_state()

        # Verify state is still properly reset
        assert cluster_app.state.plant_cluster_settings is None
        assert cluster_app.state.esr_map is None


class TestRenderESRResultsUIClearing:
    """Test render_esr_results() UI clearing behavior."""

    def test_clears_csv_preview_when_emission_policies_df_is_none(self, cluster_app):
        """Test that CSV preview is cleared when emission_policies_df is None."""
        # Setup: Create mock csv_preview element
        mock_csv_preview = MagicMock()
        mock_csv_preview.value = "old CSV data"

        # Setup state with no emission policies
        cluster_app.state.emission_policies_df = None
        cluster_app.state.esr_map = None
        cluster_app.state.esr_zones = []

        # Mock document.getElementById to return our mock elements
        mock_js = sys.modules["js"]
        original_get_elem = mock_js.document.getElementById
        mock_js.document.getElementById = MagicMock(side_effect=lambda id: {
            "esrCsvPreview": mock_csv_preview,
            "esrZonesList": MagicMock(),
            "esrRPSTechList": None,
            "esrCESTechList": None
        }.get(id))

        try:
            cluster_app.render_esr_results()
            # Verify CSV preview was cleared
            assert mock_csv_preview.value == ""
        finally:
            mock_js.document.getElementById = original_get_elem

    def test_renders_csv_preview_when_emission_policies_df_exists(self, cluster_app):
        """Test that CSV preview is rendered when emission_policies_df exists."""
        import pandas as pd

        # Setup: Create mock csv_preview element
        mock_csv_preview = MagicMock()
        mock_csv_preview.value = ""

        # Setup state with emission policies
        test_df = pd.DataFrame({
            "region": ["region1"],
            "year": [2030],
            "policy": ["RPS"]
        })
        cluster_app.state.emission_policies_df = test_df
        cluster_app.state.esr_map = {"ESR_1": ["region1"]}
        cluster_app.state.esr_type_map = {"ESR_1": "RPS"}
        cluster_app.state.esr_zones = [["TX"]]
        cluster_app.state.esr_rps_techs = ["wind", "solar"]
        cluster_app.state.esr_ces_techs = ["nuclear"]

        # Mock document.getElementById to return our mock elements
        mock_js = sys.modules["js"]
        original_get_elem = mock_js.document.getElementById
        mock_zones_list = MagicMock()
        mock_rps_list = MagicMock()
        mock_ces_list = MagicMock()
        mock_js.document.getElementById = MagicMock(side_effect=lambda id: {
            "esrCsvPreview": mock_csv_preview,
            "esrZonesList": mock_zones_list,
            "esrRPSTechList": mock_rps_list,
            "esrCESTechList": mock_ces_list
        }.get(id))

        try:
            cluster_app.render_esr_results()
            # Verify CSV preview was populated (not empty)
            assert mock_csv_preview.value != ""
            assert "region" in mock_csv_preview.value
        finally:
            mock_js.document.getElementById = original_get_elem

    def test_clears_zones_list_when_no_esr_data(self, cluster_app):
        """Test that zones list is cleared when no ESR data is available."""
        # Setup: Create mock zones_list element
        mock_zones_list = MagicMock()
        mock_zones_list.innerHTML = "old zones data"

        # Setup state with no ESR data
        cluster_app.state.esr_map = None
        cluster_app.state.esr_zones = []
        cluster_app.state.emission_policies_df = None

        # Mock document.getElementById to return our mock elements
        mock_js = sys.modules["js"]
        original_get_elem = mock_js.document.getElementById
        mock_js.document.getElementById = MagicMock(side_effect=lambda id: {
            "esrCsvPreview": MagicMock(),
            "esrZonesList": mock_zones_list,
            "esrRPSTechList": None,
            "esrCESTechList": None
        }.get(id))

        try:
            cluster_app.render_esr_results()
            # Verify zones list was cleared
            assert mock_zones_list.innerHTML == ""
        finally:
            mock_js.document.getElementById = original_get_elem
