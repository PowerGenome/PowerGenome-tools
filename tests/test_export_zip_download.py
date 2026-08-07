"""Tests for the on_download_all_settings ZIP export functionality.

Covers:
- on_download_all_settings: all YAML files under settings/ and
    scenario_inputs.csv + emission_policies.csv under extra_inputs/
    bundled into powergenome_settings.zip.
"""

import contextlib
import importlib.util
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
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


def _capture_zip_download(cluster_app):
    """Patch _download_binary_file and return a list that collects calls.

    Each call appends a dict with keys: filename, payload_bytes, mime_type.
    """
    calls = []

    def _fake_download(filename, payload_bytes, mime_type):
        calls.append(
            {
                "filename": filename,
                "payload_bytes": payload_bytes,
                "mime_type": mime_type,
            }
        )

    cluster_app._download_binary_file = _fake_download
    return calls


@contextlib.contextmanager
def _open_zip_from_calls(calls, index=0):
    """Context manager that opens the ZIP bytes from a captured _download_binary_file call."""
    buf = BytesIO(calls[index]["payload_bytes"])
    with zipfile.ZipFile(buf, "r") as zf:
        yield zf


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOnDownloadAllSettings:

    def test_happy_path_yaml_and_emissions_both_included(self, cluster_app):
        """ZIP contains YAML files and emission_policies.csv when both are present."""
        cluster_app.state.settings_yamls = {
            "model_definition.yml": "foo: bar\n",
            "resources.yml": "resources: []\n",
        }
        cluster_app.state.emission_policies_df = pd.DataFrame(
            {"zone": ["zone1"], "rps_fraction": [0.5]}
        )
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        assert len(calls) == 1
        with _open_zip_from_calls(calls) as zf:
            names = zf.namelist()
        assert "settings/model_definition.yml" in names
        assert "settings/resources.yml" in names
        assert "extra_inputs/emission_policies.csv" in names

    def test_yaml_only_no_emissions_csv(self, cluster_app):
        """ZIP contains YAML files and workflow_state.yml but no emission_policies.csv when emission_policies_df is None."""
        cluster_app.state.settings_yamls = {
            "model_definition.yml": "regions: []\n",
            "fuels.yml": "fuels: {}\n",
        }
        cluster_app.state.emission_policies_df = None
        cluster_app.state.resource_group_files = {}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        assert len(calls) == 1
        with _open_zip_from_calls(calls) as zf:
            names = set(zf.namelist())
        assert "extra_inputs/emission_policies.csv" not in names
        assert "settings/model_definition.yml" in names
        assert "settings/fuels.yml" in names
        # workflow_state.yml is always included in the ZIP
        assert "workflow_state.yml" in names

    def test_error_when_settings_yamls_empty(self, cluster_app):
        """No download is triggered when settings_yamls is empty; error status shown."""
        cluster_app.state.settings_yamls = {}
        cluster_app.state.emission_policies_df = None
        calls = _capture_zip_download(cluster_app)
        status_calls = []
        cluster_app.set_status = lambda msg, kind: status_calls.append((msg, kind))

        cluster_app.on_download_all_settings(None)

        assert calls == [], "No download should occur when settings_yamls is empty"
        assert status_calls, "A status message should have been set"
        assert status_calls[0][1] == "error"

    def test_error_when_settings_yamls_none(self, cluster_app):
        """No download is triggered when settings_yamls is None; error status shown."""
        cluster_app.state.settings_yamls = None
        cluster_app.state.emission_policies_df = None
        calls = _capture_zip_download(cluster_app)
        status_calls = []
        cluster_app.set_status = lambda msg, kind: status_calls.append((msg, kind))

        cluster_app.on_download_all_settings(None)

        assert calls == [], "No download should occur when settings_yamls is None"
        assert status_calls[0][1] == "error"

    def test_all_yaml_files_present_in_zip(self, cluster_app):
        """All seven standard YAML filenames appear in the ZIP under settings/."""
        filenames = [
            "model_definition.yml",
            "resources.yml",
            "fuels.yml",
            "transmission.yml",
            "distributed_gen.yml",
            "resource_tags.yml",
            "startup_costs.yml",
        ]
        cluster_app.state.settings_yamls = {f: f"# {f}\n" for f in filenames}
        cluster_app.state.emission_policies_df = None
        cluster_app.state.resource_group_files = {}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        expected_settings = {f"settings/{name}" for name in filenames}
        with _open_zip_from_calls(calls) as zf:
            names = set(zf.namelist())
        assert expected_settings <= names
        assert "workflow_state.yml" in names

    def test_yaml_file_contents_preserved(self, cluster_app):
        """The content of each YAML file is exactly preserved in the ZIP."""
        cluster_app.state.settings_yamls = {
            "model_definition.yml": "regions:\n  - RegionA\n  - RegionB\n",
            "fuels.yml": "natural_gas:\n  price: 4.5\n",
        }
        cluster_app.state.emission_policies_df = None
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        with _open_zip_from_calls(calls) as zf:
            assert zf.read("settings/model_definition.yml").decode() == (
                "regions:\n  - RegionA\n  - RegionB\n"
            )
            assert (
                zf.read("settings/fuels.yml").decode() == "natural_gas:\n  price: 4.5\n"
            )

    def test_emissions_csv_content_matches_dataframe(self, cluster_app):
        """The emission_policies.csv content in the ZIP matches df.to_csv(index=False)."""
        df = pd.DataFrame(
            {
                "zone": ["zone1", "zone2"],
                "rps_fraction": [0.5, 0.3],
                "ces_fraction": [0.8, 0.6],
            }
        )
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = df
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        with _open_zip_from_calls(calls) as zf:
            csv_bytes = zf.read("extra_inputs/emission_policies.csv").decode()
        assert csv_bytes == df.to_csv(index=False)

    def test_zip_filename_is_powergenome_settings(self, cluster_app):
        """The downloaded file is named exactly 'powergenome_settings.zip'."""
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        assert calls[0]["filename"] == "powergenome_settings.zip"

    def test_zip_mime_type_is_application_zip(self, cluster_app):
        """The MIME type passed to _download_binary_file is 'application/zip'."""
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        assert calls[0]["mime_type"] == "application/zip"

    def test_zip_bytes_are_valid_zip(self, cluster_app):
        """The bytes returned are a valid ZIP archive (no integrity errors)."""
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        buf = BytesIO(calls[0]["payload_bytes"])
        assert zipfile.is_zipfile(buf), "Payload should be a valid ZIP file"
        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            assert zf.testzip() is None, "ZIP should have no corrupt entries"

    def test_success_status_message_set(self, cluster_app):
        """A success status message is set after a successful download."""
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        _capture_zip_download(cluster_app)
        status_calls = []
        cluster_app.set_status = lambda msg, kind: status_calls.append((msg, kind))

        cluster_app.on_download_all_settings(None)

        assert status_calls, "set_status should have been called"
        assert status_calls[0][1] == "success"
        assert "powergenome_settings.zip" in status_calls[0][0]

    def test_scenario_files_included_when_present(self, cluster_app):
        """YAML files like scenario_management.yml and extra_inputs.yml go under settings/."""
        cluster_app.state.settings_yamls = {
            "model_definition.yml": "x: 1\n",
            "scenario_management.yml": "scenarios: []\n",
            "extra_inputs.yml": "extra: {}\n",
        }
        cluster_app.state.emission_policies_df = None
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        with _open_zip_from_calls(calls) as zf:
            names = set(zf.namelist())
        assert "settings/scenario_management.yml" in names
        assert "settings/extra_inputs.yml" in names

    def test_emissions_csv_is_in_extra_inputs_folder(self, cluster_app):
        """Emission policies CSV is written under extra_inputs/."""
        cluster_app.state.settings_yamls = {
            "resources.yml": "resources: []\n",
        }
        cluster_app.state.emission_policies_df = pd.DataFrame(
            {"zone": ["z1"], "rps_fraction": [0.5]}
        )
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        with _open_zip_from_calls(calls) as zf:
            names = set(zf.namelist())
        assert "settings/resources.yml" in names
        assert "extra_inputs/emission_policies.csv" in names

    def test_exact_zip_layout_for_yaml_files(self, cluster_app):
        """All YAML files go under settings/; emission_policies.csv goes under extra_inputs/;
        workflow_state.yml is always present at the root."""
        cluster_app.state.settings_yamls = {
            "model_definition.yml": "x: 1\n",
            "extra_inputs.yaml": "note: still_a_yaml\n",
        }
        cluster_app.state.emission_policies_df = pd.DataFrame(
            {"zone": ["z1"], "rps_fraction": [0.5]}
        )
        cluster_app.state.resource_group_files = {}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        with _open_zip_from_calls(calls) as zf:
            names = set(zf.namelist())

        assert names == {
            "settings/model_definition.yml",
            "settings/extra_inputs.yaml",
            "extra_inputs/emission_policies.csv",
            "workflow_state.yml",
        }

    def test_data_yaml_is_included_with_required_placeholders(self, cluster_app):
        """data.yml and the network-cost CSV use their expected ZIP locations."""
        cluster_app.state.region_aggregations = {"Region1": ["BA1"]}
        cluster_app.state.current_grouping = "interconnect"
        cluster_app.state.network_costs_df = pd.DataFrame(
            {"region_from": ["Region1"], "region_to": ["Region1"], "cost": [0.0]}
        )
        network_costs_filename = cluster_app._build_network_costs_filename()
        cluster_app.state.settings_yamls = {
            "data.yml": cluster_app.generate_data_settings()
        }
        cluster_app.state.emission_policies_df = None
        cluster_app.state.resource_group_files = {}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        with _open_zip_from_calls(calls) as zf:
            assert set(zf.namelist()) == {
                "settings/data.yml",
                f"extra_inputs/{network_costs_filename}",
                "workflow_state.yml",
            }
            exported_data = yaml.safe_load(zf.read("settings/data.yml"))

        assert exported_data["input_folder"] == "extra_inputs"
        assert exported_data["demand_segments_fn"] == "demand_segments_voll.csv"
        assert exported_data["emission_policies_fn"] == "emission_policies.csv"
        assert exported_data["RESOURCE_GROUPS"] == "path/to/resource/groups/folder"
        assert (
            exported_data["RESOURCE_GROUP_PROFILES"]
            == "path/to/resource/profiles/folder"
        )
        assert exported_data["data_location"] == ["path/to/your/primary/data/folder"]
        assert exported_data["transmission_cost_table"] == network_costs_filename

    @pytest.mark.parametrize(
        "n_yamls,has_emissions",
        [(1, False), (1, True), (3, False), (3, True), (7, True)],
    )
    def test_file_count_in_zip(self, cluster_app, n_yamls, has_emissions):
        """ZIP contains exactly n_yamls + (1 if has_emissions) + 1 (workflow_state.yml) files."""
        yamls = {f"file_{i}.yml": f"val: {i}\n" for i in range(n_yamls)}
        cluster_app.state.settings_yamls = yamls
        cluster_app.state.emission_policies_df = (
            pd.DataFrame({"zone": ["z1"], "val": [1.0]}) if has_emissions else None
        )
        cluster_app.state.resource_group_files = {}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        with _open_zip_from_calls(calls) as zf:
            actual_count = len(zf.namelist())
        expected = (
            n_yamls + (1 if has_emissions else 0) + 1
        )  # +1 for workflow_state.yml always present
        assert actual_count == expected
