"""Tests for _load_settings_zip and on_upload_settings_zip in cluster_app.py.

Covers:
- Happy-path ZIP loading: state.settings_yamls and state.emission_policies_df populated
- model_definition.yml fields: target_usd_year, utc_offset, planning periods, region_aggregations
- Edge cases: empty ZIP, malformed YAML, non-list model_year, string region_agg values
- Directory-prefixed entries are stored by basename
- emission_policies.csv is not placed into settings_yamls
"""

import asyncio
import importlib.util
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Fixture: load cluster_app with mocked js/PyScript dependencies
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
        sys.modules["visualization_utils"] = MagicMock()
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

        # cluster_app executes `asyncio.ensure_future(main())` at module level,
        # which requires a current event loop.  Create one and keep it alive for
        # the lifetime of the fixture so that each fixture instance works even
        # after a previous test used asyncio.run() (which closes the loop).
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

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
        # Tear down event loop and restore original modules.
        try:
            loop = asyncio.get_event_loop()
            if not loop.is_closed():
                loop.close()
        except RuntimeError:
            pass
        # Install a fresh loop so subsequent fixtures that call asyncio.ensure_future
        # (e.g. when loading cluster_app at module level) find a working loop.
        asyncio.set_event_loop(asyncio.new_event_loop())

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


def _make_zip_event(cluster_app, files_dict, zip_filename="settings.zip"):
    """Build a mock upload event wrapping an in-memory ZIP.

    Parameters
    ----------
    cluster_app:
        The loaded cluster_app module (used to wire up Uint8Array mock).
    files_dict:
        Mapping of archive-entry-name -> text content.
    zip_filename:
        The ``file_obj.name`` to report.
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files_dict.items():
            zf.writestr(name, content)
    zip_bytes = buf.getvalue()

    # Wire Uint8Array so bytes(Uint8Array.new(ab).to_py()) returns zip_bytes
    cluster_app.Uint8Array.new.return_value.to_py.return_value = zip_bytes

    file_obj = MagicMock()
    file_obj.name = zip_filename
    file_obj.arrayBuffer = AsyncMock(return_value=zip_bytes)

    event = MagicMock()
    event.target.files.length = 1
    event.target.files.item.return_value = file_obj
    return event


def _run_load(cluster_app, event):
    """Run _load_settings_zip synchronously using the current event loop.

    We deliberately avoid asyncio.run() because it closes the event loop after
    completion, which breaks subsequent fixture setups that need a live loop
    (cluster_app calls asyncio.ensure_future at module level).
    """
    loop = asyncio.get_event_loop()
    loop.run_until_complete(cluster_app._load_settings_zip(event))


def _mock_document_elements(cluster_app):
    """Make document.getElementById return a fresh MagicMock for any id."""
    cluster_app.document.getElementById.side_effect = lambda el_id: MagicMock()


def _setup_state(cluster_app):
    """Reset relevant state and replace side-effectful module-level functions."""
    cluster_app.state.all_bas = set()
    cluster_app.state.settings_yamls = {}
    cluster_app.state.emission_policies_df = None
    cluster_app.state.new_resources = []
    cluster_app.state.plant_cluster_settings = None
    cluster_app.state.renewables_clusters = None
    cluster_app.update_map_cluster_colors = MagicMock()
    cluster_app.update_selected_display = MagicMock()
    cluster_app.update_transmission_lines = MagicMock()
    cluster_app.update_tooltips = MagicMock()
    cluster_app.reset_region_dependent_state = MagicMock()
    cluster_app.render_new_resources_list = MagicMock()
    cluster_app.render_esr_results = MagicMock()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLoadSettingsZip:

    def test_happy_path_populates_state(self, cluster_app):
        """ZIP with model_definition.yml, fuels.yml, emission_policies.csv
        populates settings_yamls with YAML files and emission_policies_df."""
        _setup_state(cluster_app)
        _mock_document_elements(cluster_app)

        csv_content = "zone,rps_fraction\nzone1,0.5\nzone2,0.3\n"
        event = _make_zip_event(
            cluster_app,
            {
                "model_definition.yml": "target_usd_year: 2022\n",
                "fuels.yml": "natural_gas:\n  price: 4.5\n",
                "emission_policies.csv": csv_content,
            },
        )

        _run_load(cluster_app, event)

        # YAML files are stored
        assert "model_definition.yml" in cluster_app.state.settings_yamls
        assert "fuels.yml" in cluster_app.state.settings_yamls
        # CSV is NOT in settings_yamls
        assert "emission_policies.csv" not in cluster_app.state.settings_yamls
        # emission_policies_df is populated
        assert cluster_app.state.emission_policies_df is not None
        df = cluster_app.state.emission_policies_df
        assert list(df.columns) == ["zone", "rps_fraction"]
        assert len(df) == 2

    def test_model_definition_populates_target_usd_year_and_utc_offset(
        self, cluster_app
    ):
        """target_usd_year and utc_offset from model_definition.yml are written
        to the corresponding DOM elements."""
        _setup_state(cluster_app)

        # Use per-id element mocks so we can inspect their .value
        elements = {}

        def _get_el(el_id):
            if el_id not in elements:
                elements[el_id] = MagicMock()
            return elements[el_id]

        cluster_app.document.getElementById.side_effect = _get_el

        event = _make_zip_event(
            cluster_app,
            {
                "model_definition.yml": (
                    "target_usd_year: 2022\nutc_offset: -5\n"
                ),
            },
        )

        _run_load(cluster_app, event)

        assert elements["targetUsdYear"].value == "2022"
        assert elements["utcOffset"].value == "-5"

    def test_planning_periods_calls_load_planning_periods_from_data(
        self, cluster_app
    ):
        """Valid model_year / model_first_planning_year lists cause
        window.loadPlanningPeriodsFromData to be called once."""
        _setup_state(cluster_app)
        _mock_document_elements(cluster_app)

        event = _make_zip_event(
            cluster_app,
            {
                "model_definition.yml": (
                    "model_year: [2030, 2035]\n"
                    "model_first_planning_year: [2026, 2031]\n"
                ),
            },
        )

        _run_load(cluster_app, event)

        cluster_app.window.loadPlanningPeriodsFromData.assert_called_once()

    def test_region_aggregations_restores_state(self, cluster_app):
        """region_aggregations in model_definition.yml is stored in state."""
        _setup_state(cluster_app)
        cluster_app.state.all_bas = {"BA1", "BA2", "BA3"}
        _mock_document_elements(cluster_app)

        event = _make_zip_event(
            cluster_app,
            {
                "model_definition.yml": (
                    "region_aggregations:\n"
                    "  RegionA: [BA1, BA2]\n"
                    "  RegionB: [BA3]\n"
                ),
            },
        )

        _run_load(cluster_app, event)

        assert cluster_app.state.region_aggregations == {
            "RegionA": ["BA1", "BA2"],
            "RegionB": ["BA3"],
        }

    def test_emission_policies_csv_not_in_settings_yamls(self, cluster_app):
        """emission_policies.csv must not appear in settings_yamls but must
        populate emission_policies_df."""
        _setup_state(cluster_app)
        _mock_document_elements(cluster_app)

        event = _make_zip_event(
            cluster_app,
            {"emission_policies.csv": "zone,rps_fraction\nz1,0.4\n"},
        )

        _run_load(cluster_app, event)

        assert "emission_policies.csv" not in cluster_app.state.settings_yamls
        assert cluster_app.state.emission_policies_df is not None

    def test_directory_prefixed_entries_are_stored_by_basename(self, cluster_app):
        """Entries like subdir/fuels.yml are stored under their basename key."""
        _setup_state(cluster_app)
        _mock_document_elements(cluster_app)

        event = _make_zip_event(
            cluster_app,
            {
                "subdir/fuels.yml": "natural_gas:\n  price: 3.0\n",
                "subdir/model_definition.yml": "target_usd_year: 2030\n",
            },
        )

        _run_load(cluster_app, event)

        # Full paths must NOT appear as keys
        assert "subdir/fuels.yml" not in cluster_app.state.settings_yamls
        assert "subdir/model_definition.yml" not in cluster_app.state.settings_yamls
        # Basenames MUST appear
        assert "fuels.yml" in cluster_app.state.settings_yamls
        assert "model_definition.yml" in cluster_app.state.settings_yamls

    def test_malformed_model_definition_yaml_calls_error_status(self, cluster_app):
        """A model_definition.yml with invalid YAML triggers an error status."""
        _setup_state(cluster_app)
        _mock_document_elements(cluster_app)

        # Replace _set_model_setup_status to capture calls
        status_calls = []
        cluster_app._set_model_setup_status = MagicMock(
            side_effect=lambda msg, status_type="info": status_calls.append(
                (msg, status_type)
            )
        )

        event = _make_zip_event(
            cluster_app,
            {"model_definition.yml": "key: :\n  bad: indent\n  :\n"},
        )

        _run_load(cluster_app, event)

        error_calls = [c for c in status_calls if c[1] == "error"]
        assert error_calls, (
            f"Expected at least one error status call; got: {status_calls}"
        )

    def test_empty_zip_handled_gracefully(self, cluster_app):
        """A ZIP with no files results in empty settings_yamls and no exception."""
        _setup_state(cluster_app)
        _mock_document_elements(cluster_app)

        event = _make_zip_event(cluster_app, {})

        _run_load(cluster_app, event)  # must not raise

        assert cluster_app.state.settings_yamls == {}

    def test_non_list_model_year_skips_planning_periods(self, cluster_app):
        """Scalar model_year prevents loadPlanningPeriodsFromData from being called."""
        _setup_state(cluster_app)
        _mock_document_elements(cluster_app)

        event = _make_zip_event(
            cluster_app,
            {
                "model_definition.yml": (
                    "model_year: 2030\n"
                    "model_first_planning_year: [2026]\n"
                ),
            },
        )

        _run_load(cluster_app, event)

        cluster_app.window.loadPlanningPeriodsFromData.assert_not_called()

    def test_non_list_region_aggregations_values_skipped(self, cluster_app):
        """region_aggregations with a non-list value must not update map colors."""
        _setup_state(cluster_app)
        cluster_app.state.all_bas = {"BA1"}
        _mock_document_elements(cluster_app)

        # String value instead of list
        event = _make_zip_event(
            cluster_app,
            {
                "model_definition.yml": (
                    "region_aggregations:\n  RegionA: BA1\n"
                ),
            },
        )

        _run_load(cluster_app, event)

        cluster_app.update_map_cluster_colors.assert_not_called()
        # state.region_aggregations must not have been set to the bad value
        bad_value = {"RegionA": "BA1"}
        assert getattr(cluster_app.state, "region_aggregations", None) != bad_value

    def test_resources_yml_populates_new_resources(self, cluster_app):
        """new_resources list from resources.yml is restored into state.new_resources."""
        _setup_state(cluster_app)
        _mock_document_elements(cluster_app)

        resources_yaml = (
            "new_resources:\n"
            "- - UtilityPV\n"
            "  - LowCost\n"
            "  - Market\n"
            "  - 200\n"
            "- - LandbasedWind\n"
            "  - Class1\n"
            "  - Market\n"
            "  - 100\n"
        )
        event = _make_zip_event(cluster_app, {"resources.yml": resources_yaml})

        _run_load(cluster_app, event)

        assert len(cluster_app.state.new_resources) == 2
        assert cluster_app.state.new_resources[0]["technology"] == "UtilityPV"
        assert cluster_app.state.new_resources[0]["tech_detail"] == "LowCost"
        assert cluster_app.state.new_resources[0]["planning_year"] == "all"
        assert cluster_app.state.new_resources[1]["technology"] == "LandbasedWind"
        cluster_app.render_new_resources_list.assert_called()

    def test_resources_yml_populates_renewables_clusters(self, cluster_app):
        """renewables_clusters list from resources.yml is restored into state."""
        _setup_state(cluster_app)
        _mock_document_elements(cluster_app)

        resources_yaml = (
            "renewables_clusters:\n"
            "- region: all\n"
            "  technology: landbasedwind\n"
        )
        event = _make_zip_event(cluster_app, {"resources.yml": resources_yaml})

        _run_load(cluster_app, event)

        assert isinstance(cluster_app.state.renewables_clusters, list)
        assert len(cluster_app.state.renewables_clusters) == 1
        assert cluster_app.state.renewables_clusters[0]["technology"] == "landbasedwind"

    def test_resources_yml_populates_plant_cluster_settings(self, cluster_app):
        """num_clusters from resources.yml creates state.plant_cluster_settings."""
        _setup_state(cluster_app)
        _mock_document_elements(cluster_app)

        resources_yaml = (
            "num_clusters:\n"
            "  Nuclear: 1\n"
            "  Natural Gas Fired Combined Cycle: 2\n"
            "group_technologies: true\n"
        )
        event = _make_zip_event(cluster_app, {"resources.yml": resources_yaml})

        _run_load(cluster_app, event)

        assert cluster_app.state.plant_cluster_settings is not None
        assert cluster_app.state.plant_cluster_settings["num_clusters"]["Nuclear"] == 1
        assert cluster_app.state.plant_cluster_settings["group_technologies"] is True

    def test_fuels_yml_populates_fuel_data_year(self, cluster_app):
        """fuel_data_year from fuels.yml is written to the fuelDataYear DOM element."""
        _setup_state(cluster_app)

        elements = {}

        def _get_el(el_id):
            if el_id not in elements:
                elements[el_id] = MagicMock()
            return elements[el_id]

        cluster_app.document.getElementById.side_effect = _get_el

        event = _make_zip_event(
            cluster_app, {"fuels.yml": "fuel_data_year: 2025\n"}
        )

        _run_load(cluster_app, event)

        assert elements["fuelDataYear"].value == "2025"

    def test_fuels_yml_populates_fuel_scenario_selects(self, cluster_app):
        """fuel_scenarios from fuels.yml selects the right option for each fuel."""
        _setup_state(cluster_app)

        elements = {}

        # "reference" is the default scenario value for fuel selects; we pre-populate
        # each mock element with it to simulate a fully-populated dropdown so the
        # handler takes the existing-value branch (el.value = ...) rather than the
        # fallback _set_select_options_simple() branch.
        _DEFAULT_SCENARIO = "reference"

        def _get_el(el_id):
            if el_id not in elements:
                m = MagicMock()
                m.value = _DEFAULT_SCENARIO
                elements[el_id] = m
            return elements[el_id]

        cluster_app.document.getElementById.side_effect = _get_el

        fuels_yaml = (
            "fuel_data_year: 2025\n"
            "fuel_scenarios:\n"
            "  coal: no_111d\n"
            "  naturalgas: reference\n"
            "  distillate: reference\n"
            "  uranium: reference\n"
        )
        event = _make_zip_event(cluster_app, {"fuels.yml": fuels_yaml})

        _run_load(cluster_app, event)

        assert elements["fuelScenarioCoal"].value == "no_111d"

    def test_emission_policies_csv_calls_render_esr_results(self, cluster_app):
        """When emission_policies.csv is present, render_esr_results() is called."""
        _setup_state(cluster_app)
        _mock_document_elements(cluster_app)

        event = _make_zip_event(
            cluster_app,
            {"emission_policies.csv": "zone,rps_fraction\nz1,0.4\n"},
        )

        _run_load(cluster_app, event)

        cluster_app.render_esr_results.assert_called()
