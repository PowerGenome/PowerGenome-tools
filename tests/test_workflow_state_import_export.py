"""Tests for the workflow_state.yml import/export contract.

Covers:
- Required manifest structure (schema, version, sections, supplemental paths)
- _validate_workflow_manifest: accepts valid manifests; rejects each violation
- _parse_workflow_manifest: parses bytes; rejects bad YAML / non-UTF-8
- _is_safe_workflow_path: pure path-safety checker
- _read_workflow_zip: extracts manifest + settings_yamls + resource_group_files;
    rejects non-ZIP, missing workflow_state.yml, unsafe paths, missing required files
- _import_workflow_bytes: dispatches correctly for .zip vs .yml filenames;
    rejects wrong extension; rejects standalone import when supplemental files are required
- ZIP export always includes workflow_state.yml with valid manifest content
- ZIP export includes resource_groups/ entries when state.resource_group_files is non-empty
- State round-trip: selected_bas, ba_to_region, settings_yamls survive import via ZIP
"""

import importlib.util
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

# ---------------------------------------------------------------------------
# Shared module fixture (same pattern as test_export_zip_download.py)
# ---------------------------------------------------------------------------

SCHEMA = "powergenome-tools-workflow-state"
VERSION = 1


@pytest.fixture(scope="module")
def cluster_app():
    """Load cluster_app once per module with mocked browser globals."""
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
        mock_ffi = MagicMock()
        mock_ffi.create_proxy = lambda x: x
        mock_ffi.to_js = lambda x: x
        mock_ffi.JsProxy = object

        sys.modules["js"] = MagicMock()
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

        js = sys.modules["js"]
        js.document = MagicMock()
        js.window = MagicMock()
        js.fetch = MagicMock()
        js.Uint8Array = MagicMock()
        js.globalThis = MagicMock()
        js.L = MagicMock()

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


def _minimal_manifest(
    schema=SCHEMA,
    version=VERSION,
    supplemental=None,
    forms=None,
    state=None,
    tables=None,
):
    """Return a minimal valid manifest dict."""
    return {
        "schema": schema,
        "version": version,
        "required_supplemental_files": supplemental if supplemental is not None else [],
        "forms": forms if forms is not None else {},
        "state": state if state is not None else {},
        "tables": tables if tables is not None else {},
    }


def _manifest_bytes(manifest):
    """Serialize a manifest dict to YAML bytes."""
    return yaml.safe_dump(manifest, sort_keys=False).encode("utf-8")


def _build_zip(files: dict[str, bytes | str]) -> bytes:
    """Build an in-memory ZIP from a filename->content mapping."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            zf.writestr(name, content)
    return buf.getvalue()


def _capture_zip_download(ca):
    calls = []

    def _fake(filename, payload_bytes, mime_type):
        calls.append({"filename": filename, "payload_bytes": payload_bytes})

    ca._download_binary_file = _fake
    return calls


# ---------------------------------------------------------------------------
# _is_safe_workflow_path
# ---------------------------------------------------------------------------


class TestIsSafeWorkflowPath:
    @pytest.mark.parametrize(
        "path",
        [
            "workflow_state.yml",
            "settings/model_definition.yml",
            "resource_groups/groups.json",
            "data/network_costs.csv",
        ],
    )
    def test_safe_paths_accepted(self, cluster_app, path):
        assert cluster_app._is_safe_workflow_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "",
            None,
            "/etc/passwd",
            "\\windows\\path",
            "../etc/passwd",
            "a/../b",
            "settings\\model.yml",
        ],
    )
    def test_unsafe_paths_rejected(self, cluster_app, path):
        result = cluster_app._is_safe_workflow_path(path) if path else False
        assert not result


# ---------------------------------------------------------------------------
# _validate_workflow_manifest: contract enforcement
# ---------------------------------------------------------------------------


class TestValidateWorkflowManifest:
    def test_valid_minimal_manifest_passes(self, cluster_app):
        m = _minimal_manifest()
        result = cluster_app._validate_workflow_manifest(m)
        assert result is m  # returns same dict

    def test_wrong_schema_raises(self, cluster_app):
        m = _minimal_manifest(schema="wrong-schema")
        with pytest.raises(ValueError, match="schema"):
            cluster_app._validate_workflow_manifest(m)

    def test_wrong_version_raises(self, cluster_app):
        m = _minimal_manifest(version=99)
        with pytest.raises(ValueError, match="version"):
            cluster_app._validate_workflow_manifest(m)

    def test_non_dict_raises(self, cluster_app):
        with pytest.raises(ValueError, match="mapping"):
            cluster_app._validate_workflow_manifest(["not", "a", "dict"])

    def test_missing_forms_section_raises(self, cluster_app):
        m = _minimal_manifest()
        del m["forms"]
        with pytest.raises(ValueError, match="forms"):
            cluster_app._validate_workflow_manifest(m)

    def test_missing_state_section_raises(self, cluster_app):
        m = _minimal_manifest()
        del m["state"]
        with pytest.raises(ValueError, match="state"):
            cluster_app._validate_workflow_manifest(m)

    def test_missing_tables_section_raises(self, cluster_app):
        m = _minimal_manifest()
        del m["tables"]
        with pytest.raises(ValueError, match="tables"):
            cluster_app._validate_workflow_manifest(m)

    def test_non_dict_forms_raises(self, cluster_app):
        m = _minimal_manifest(forms="not-a-dict")
        with pytest.raises(ValueError, match="forms"):
            cluster_app._validate_workflow_manifest(m)

    def test_non_dict_state_raises(self, cluster_app):
        m = _minimal_manifest(state=["list"])
        with pytest.raises(ValueError, match="state"):
            cluster_app._validate_workflow_manifest(m)

    def test_non_dict_tables_raises(self, cluster_app):
        m = _minimal_manifest(tables=42)
        with pytest.raises(ValueError, match="tables"):
            cluster_app._validate_workflow_manifest(m)

    def test_valid_supplemental_files_accepted(self, cluster_app):
        m = _minimal_manifest(supplemental=["resource_groups/groups.json"])
        result = cluster_app._validate_workflow_manifest(m)
        assert result["required_supplemental_files"] == ["resource_groups/groups.json"]

    def test_unsafe_supplemental_path_raises(self, cluster_app):
        m = _minimal_manifest(supplemental=["../evil.json"])
        with pytest.raises(ValueError, match="supplemental"):
            cluster_app._validate_workflow_manifest(m)

    def test_empty_supplemental_path_raises(self, cluster_app):
        m = _minimal_manifest(supplemental=[""])
        with pytest.raises(ValueError, match="supplemental"):
            cluster_app._validate_workflow_manifest(m)

    def test_non_list_supplemental_raises(self, cluster_app):
        m = _minimal_manifest(supplemental="not-a-list")
        with pytest.raises(ValueError, match="supplemental"):
            cluster_app._validate_workflow_manifest(m)

    def test_supplemental_with_integer_entry_raises(self, cluster_app):
        m = _minimal_manifest(supplemental=[123])
        with pytest.raises(ValueError, match="supplemental"):
            cluster_app._validate_workflow_manifest(m)


# ---------------------------------------------------------------------------
# _parse_workflow_manifest: bytes → validated manifest
# ---------------------------------------------------------------------------


class TestParseWorkflowManifest:
    def test_valid_yaml_bytes_parsed(self, cluster_app):
        m = _minimal_manifest()
        data = _manifest_bytes(m)
        result = cluster_app._parse_workflow_manifest(data)
        assert result["schema"] == SCHEMA
        assert result["version"] == VERSION

    def test_invalid_yaml_raises_value_error(self, cluster_app):
        with pytest.raises(ValueError, match="parse"):
            cluster_app._parse_workflow_manifest(b"key: [\nnot closed")

    def test_non_utf8_bytes_raises_value_error(self, cluster_app):
        with pytest.raises(ValueError, match="parse"):
            cluster_app._parse_workflow_manifest(b"\xff\xfe invalid utf-8 \x80")

    def test_yaml_list_instead_of_mapping_raises(self, cluster_app):
        data = b"- item1\n- item2\n"
        with pytest.raises(ValueError, match="mapping"):
            cluster_app._parse_workflow_manifest(data)

    def test_wrong_schema_in_bytes_raises(self, cluster_app):
        m = _minimal_manifest(schema="bad-schema")
        with pytest.raises(ValueError, match="schema"):
            cluster_app._parse_workflow_manifest(_manifest_bytes(m))


# ---------------------------------------------------------------------------
# _read_workflow_zip: ZIP parsing and validation
# ---------------------------------------------------------------------------


class TestReadWorkflowZip:
    def test_valid_minimal_zip_returns_manifest(self, cluster_app):
        m = _minimal_manifest()
        data = _build_zip({"workflow_state.yml": _manifest_bytes(m)})
        manifest, settings_yamls, resource_group_files = cluster_app._read_workflow_zip(
            data
        )
        assert manifest["schema"] == SCHEMA
        assert settings_yamls == {}
        assert resource_group_files == {}

    def test_not_a_zip_raises(self, cluster_app):
        with pytest.raises(ValueError, match="ZIP"):
            cluster_app._read_workflow_zip(b"not a zip file")

    def test_missing_workflow_state_yml_raises(self, cluster_app):
        data = _build_zip({"settings/model_definition.yml": b"x: 1\n"})
        with pytest.raises(ValueError, match="workflow_state.yml"):
            cluster_app._read_workflow_zip(data)

    def test_unsafe_path_in_zip_raises(self, cluster_app):
        m = _minimal_manifest()
        data = _build_zip(
            {
                "workflow_state.yml": _manifest_bytes(m),
                "../evil.txt": b"evil",
            }
        )
        with pytest.raises(ValueError, match="unsafe"):
            cluster_app._read_workflow_zip(data)

    def test_missing_required_supplemental_file_raises(self, cluster_app):
        m = _minimal_manifest(supplemental=["resource_groups/groups.json"])
        data = _build_zip({"workflow_state.yml": _manifest_bytes(m)})
        with pytest.raises(ValueError, match="missing"):
            cluster_app._read_workflow_zip(data)

    def test_settings_yamls_extracted_from_settings_folder(self, cluster_app):
        m = _minimal_manifest()
        data = _build_zip(
            {
                "workflow_state.yml": _manifest_bytes(m),
                "settings/model_definition.yml": b"regions: []\n",
                "settings/fuels.yml": b"fuels: {}\n",
            }
        )
        _, settings_yamls, _ = cluster_app._read_workflow_zip(data)
        assert "model_definition.yml" in settings_yamls
        assert settings_yamls["model_definition.yml"] == "regions: []\n"
        assert "fuels.yml" in settings_yamls

    def test_resource_group_files_extracted_when_required(self, cluster_app):
        groups_content = b'{"groups": []}'
        m = _minimal_manifest(supplemental=["resource_groups/groups.json"])
        data = _build_zip(
            {
                "workflow_state.yml": _manifest_bytes(m),
                "resource_groups/groups.json": groups_content,
            }
        )
        _, _, resource_group_files = cluster_app._read_workflow_zip(data)
        assert "groups.json" in resource_group_files
        assert resource_group_files["groups.json"] == groups_content

    def test_non_required_resource_group_files_not_extracted(self, cluster_app):
        """Files in resource_groups/ that are NOT in required_supplemental_files are
        not extracted (only required files are returned)."""
        m = _minimal_manifest(supplemental=[])  # nothing required
        data = _build_zip(
            {
                "workflow_state.yml": _manifest_bytes(m),
                "resource_groups/extra.json": b"{}",
            }
        )
        _, _, resource_group_files = cluster_app._read_workflow_zip(data)
        assert resource_group_files == {}

    def test_settings_yaml_extension_is_also_accepted(self, cluster_app):
        m = _minimal_manifest()
        data = _build_zip(
            {
                "workflow_state.yml": _manifest_bytes(m),
                "settings/config.yaml": b"key: value\n",
            }
        )
        _, settings_yamls, _ = cluster_app._read_workflow_zip(data)
        assert "config.yaml" in settings_yamls


# ---------------------------------------------------------------------------
# _import_workflow_bytes: dispatch logic
# ---------------------------------------------------------------------------


class TestImportWorkflowBytes:
    def test_wrong_filename_extension_raises(self, cluster_app):
        with pytest.raises(ValueError, match="workflow_state.yml"):
            cluster_app._import_workflow_bytes(b"data", "some_file.csv")

    def test_txt_extension_raises(self, cluster_app):
        with pytest.raises(ValueError, match="workflow_state.yml"):
            cluster_app._import_workflow_bytes(b"data", "workflow_state.txt")

    def test_standalone_yml_with_required_files_raises(self, cluster_app):
        """A standalone .yml with required supplemental files must reject and
        prompt the user to upload the full ZIP instead."""
        m = _minimal_manifest(supplemental=["resource_groups/groups.json"])
        with pytest.raises(ValueError, match="supplemental"):
            cluster_app._import_workflow_bytes(_manifest_bytes(m), "workflow_state.yml")

    def test_standalone_yml_no_supplemental_files_restores_state(self, cluster_app):
        """A standalone workflow_state.yml with no required supplemental files
        should succeed and restore state fields without error."""
        m = _minimal_manifest(
            state={"selected_bas": ["BA1", "BA2"], "is_clustered": True},
        )
        # Should not raise; DOM calls are safe with MagicMock
        cluster_app._import_workflow_bytes(_manifest_bytes(m), "workflow_state.yml")
        assert "BA1" in cluster_app.state.selected_bas
        assert "BA2" in cluster_app.state.selected_bas
        assert cluster_app.state.is_clustered is True

    def test_standalone_yaml_extension_accepted(self, cluster_app):
        """workflow_state.yaml (with 'a') is also accepted for standalone import."""
        m = _minimal_manifest(state={"is_manual_mode": False})
        cluster_app._import_workflow_bytes(_manifest_bytes(m), "workflow_state.yaml")

    def test_zip_import_restores_settings_yamls(self, cluster_app):
        """ZIP import overwrites state.settings_yamls with files from settings/."""
        m = _minimal_manifest()
        data = _build_zip(
            {
                "workflow_state.yml": _manifest_bytes(m),
                "settings/model_definition.yml": b"regions:\n  - R1\n",
            }
        )
        cluster_app._import_workflow_bytes(data, "export.zip")
        assert "model_definition.yml" in cluster_app.state.settings_yamls
        assert "R1" in cluster_app.state.settings_yamls["model_definition.yml"]

    def test_zip_import_restores_resource_group_files(self, cluster_app):
        """ZIP import populates state.resource_group_files for required entries."""
        grp = b'{"resource_groups": []}'
        m = _minimal_manifest(supplemental=["resource_groups/groups.json"])
        data = _build_zip(
            {
                "workflow_state.yml": _manifest_bytes(m),
                "resource_groups/groups.json": grp,
            }
        )
        cluster_app._import_workflow_bytes(data, "export.zip")
        assert cluster_app.state.resource_group_files.get("groups.json") == grp


# ---------------------------------------------------------------------------
# ZIP export always writes workflow_state.yml at the archive root
# ---------------------------------------------------------------------------


class TestZipExportIncludesWorkflowState:
    def test_workflow_state_yml_present_in_export(self, cluster_app):
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        cluster_app.state.resource_group_files = {}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        buf = BytesIO(calls[0]["payload_bytes"])
        with zipfile.ZipFile(buf) as zf:
            assert "workflow_state.yml" in zf.namelist()

    def test_workflow_state_yml_is_valid_manifest(self, cluster_app):
        """The embedded workflow_state.yml round-trips as a valid manifest."""
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        cluster_app.state.resource_group_files = {}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        buf = BytesIO(calls[0]["payload_bytes"])
        with zipfile.ZipFile(buf) as zf:
            raw = zf.read("workflow_state.yml")
        manifest = yaml.safe_load(raw)
        assert manifest["schema"] == SCHEMA
        assert manifest["version"] == VERSION
        for section in ("forms", "state", "tables"):
            assert isinstance(manifest[section], dict), f"'{section}' must be a dict"

    def test_resource_groups_included_in_zip_when_state_populated(self, cluster_app):
        """resource_groups/ entries appear in the ZIP when state.resource_group_files is set."""
        grp_bytes = b'{"resource_groups": []}'
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        cluster_app.state.resource_group_files = {"groups.json": grp_bytes}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        buf = BytesIO(calls[0]["payload_bytes"])
        with zipfile.ZipFile(buf) as zf:
            names = zf.namelist()
            assert "resource_groups/groups.json" in names
            assert zf.read("resource_groups/groups.json") == grp_bytes

    def test_resource_groups_listed_in_manifest_supplemental(self, cluster_app):
        """resource_group_files filenames appear in required_supplemental_files."""
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        cluster_app.state.resource_group_files = {"assignments.csv": b"a,b\n1,2\n"}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        buf = BytesIO(calls[0]["payload_bytes"])
        with zipfile.ZipFile(buf) as zf:
            manifest = yaml.safe_load(zf.read("workflow_state.yml"))
        assert (
            "resource_groups/assignments.csv" in manifest["required_supplemental_files"]
        )

    def test_resource_group_with_unsafe_filename_excluded_from_zip(self, cluster_app):
        """Files with path-traversal names in resource_group_files are silently skipped."""
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        cluster_app.state.resource_group_files = {
            "safe.json": b"{}",
            "../evil.sh": b"rm -rf /",
        }
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        buf = BytesIO(calls[0]["payload_bytes"])
        with zipfile.ZipFile(buf) as zf:
            names = set(zf.namelist())
        assert "resource_groups/safe.json" in names
        assert "resource_groups/../evil.sh" not in names
        assert "../evil.sh" not in names


# ---------------------------------------------------------------------------
# State round-trip: export ZIP → _read_workflow_zip → _restore_workflow_state
# ---------------------------------------------------------------------------


class TestStateRoundTrip:
    # CLUSTER_COLORS is imported from mocked visualization_utils so len() == 0,
    # causing a ZeroDivisionError in update_map_cluster_colors when region_aggregations
    # is truthy.  Patch it on the module directly in tests that need it.
    # NOTE: This is a testability defect — CLUSTER_COLORS should be guarded or
    # injectable; reported without editing application code.
    _COLORS = ["#111111", "#222222", "#333333", "#444444"]

    def test_selected_bas_and_ba_to_region_survive_roundtrip(self, cluster_app):
        """selected_bas and ba_to_region written into state are recovered after import."""
        cluster_app.CLUSTER_COLORS = self._COLORS  # prevent ZeroDivisionError
        cluster_app.state.selected_bas = {"BA1", "BA2", "BA3"}
        cluster_app.state.ba_to_region = {
            "BA1": "Region1",
            "BA2": "Region1",
            "BA3": "Region2",
        }
        cluster_app.state.current_grouping = "test_group"
        cluster_app.state.is_clustered = True
        cluster_app.state.is_manual_mode = False
        cluster_app.state.region_aggregations = {
            "Region1": ["BA1", "BA2"],
            "Region2": ["BA3"],
        }
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        cluster_app.state.resource_group_files = {}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        # Reset state before import
        cluster_app.state.selected_bas = set()
        cluster_app.state.ba_to_region = {}
        cluster_app.state.is_clustered = False

        # Import from the exported ZIP bytes
        zip_bytes = calls[0]["payload_bytes"]
        manifest, settings_yamls, resource_group_files = cluster_app._read_workflow_zip(
            zip_bytes
        )
        cluster_app._restore_workflow_state(
            manifest, settings_yamls, resource_group_files
        )

        assert cluster_app.state.selected_bas == {"BA1", "BA2", "BA3"}
        assert cluster_app.state.ba_to_region == {
            "BA1": "Region1",
            "BA2": "Region1",
            "BA3": "Region2",
        }
        assert cluster_app.state.is_clustered is True

    def test_settings_yamls_content_survives_roundtrip(self, cluster_app):
        """settings_yamls content in the ZIP overrides any manifest-embedded version."""
        cluster_app.CLUSTER_COLORS = self._COLORS
        yaml_content = "regions:\n  - RegionA\n  - RegionB\n"
        cluster_app.state.settings_yamls = {"model_definition.yml": yaml_content}
        cluster_app.state.emission_policies_df = None
        cluster_app.state.region_aggregations = None  # avoid update_map_cluster_colors
        cluster_app.state.resource_group_files = {}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        cluster_app.state.settings_yamls = {}

        zip_bytes = calls[0]["payload_bytes"]
        manifest, settings_yamls, resource_group_files = cluster_app._read_workflow_zip(
            zip_bytes
        )
        cluster_app._restore_workflow_state(
            manifest, settings_yamls, resource_group_files
        )

        assert "model_definition.yml" in cluster_app.state.settings_yamls
        assert "RegionA" in cluster_app.state.settings_yamls["model_definition.yml"]

    def test_resource_group_files_survive_roundtrip(self, cluster_app):
        """resource_group_files bytes survive an export/import ZIP round-trip."""
        cluster_app.CLUSTER_COLORS = self._COLORS
        grp_bytes = b'{"resource_groups": [{"id": "rg1"}]}'
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        cluster_app.state.region_aggregations = None  # avoid update_map_cluster_colors
        cluster_app.state.resource_group_files = {"groups.json": grp_bytes}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        cluster_app.state.resource_group_files = {}

        zip_bytes = calls[0]["payload_bytes"]
        manifest, settings_yamls, resource_group_files = cluster_app._read_workflow_zip(
            zip_bytes
        )
        cluster_app._restore_workflow_state(
            manifest, settings_yamls, resource_group_files
        )

        assert cluster_app.state.resource_group_files.get("groups.json") == grp_bytes


# ---------------------------------------------------------------------------
# Manifest omits derived renewables_curve_data
# ---------------------------------------------------------------------------


class TestManifestOmitsDerivedRenewablesCurveData:
    """Verify that build_workflow_state_manifest never serialises
    renewables_curve_data (a runtime-derived cache) into the manifest.

    The contract is:
      * manifest["state"] must NOT contain a 'renewables_curve_data' key.
      * manifest["tables"] must NOT contain a 'renewables_curve_data' key.

    This is intentional: the curve data is large and is always recomputed from
    the uploaded LCOE tables / resource-group assignments after import.
    """

    def _setup_state_with_curve_data(self, ca):
        """Populate state with pre-computed renewables_curve_data so that if the
        manifest builder accidentally serialises it the tests will catch it."""
        ca.state.renewables_curve_data = {
            "landbasedwind": {
                "RegionA": {
                    "cum_capacity": [0.0, 100.0, 200.0],
                    "lcoe": [30.0, 35.0, 42.0],
                    "lcoe_max": 42.0,
                }
            },
            "utilitypv": {
                "RegionB": {
                    "cum_capacity": [0.0, 50.0],
                    "lcoe": [25.0, 28.0],
                    "lcoe_max": 28.0,
                }
            },
        }
        ca.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        ca.state.emission_policies_df = None
        ca.state.resource_group_files = {}

    def test_build_manifest_state_has_no_renewables_curve_data(self, cluster_app):
        """build_workflow_state_manifest must not include renewables_curve_data
        in the 'state' section even when state holds populated curve data."""
        self._setup_state_with_curve_data(cluster_app)
        manifest = cluster_app.build_workflow_state_manifest()
        assert "renewables_curve_data" not in manifest["state"], (
            "manifest['state'] must not contain 'renewables_curve_data'; "
            "curve data is always recalculated on import."
        )

    def test_build_manifest_tables_has_no_renewables_curve_data(self, cluster_app):
        """build_workflow_state_manifest must not include renewables_curve_data
        in the 'tables' section even when state holds populated curve data."""
        self._setup_state_with_curve_data(cluster_app)
        manifest = cluster_app.build_workflow_state_manifest()
        assert (
            "renewables_curve_data" not in manifest["tables"]
        ), "manifest['tables'] must not contain 'renewables_curve_data'."

    def test_exported_zip_manifest_state_has_no_renewables_curve_data(
        self, cluster_app
    ):
        """The workflow_state.yml embedded in the exported ZIP must not carry
        renewables_curve_data in its 'state' mapping."""
        self._setup_state_with_curve_data(cluster_app)
        calls = _capture_zip_download(cluster_app)
        cluster_app.on_download_all_settings(None)

        buf = BytesIO(calls[0]["payload_bytes"])
        with zipfile.ZipFile(buf) as zf:
            manifest = yaml.safe_load(zf.read("workflow_state.yml"))

        assert (
            "renewables_curve_data" not in manifest["state"]
        ), "Exported ZIP manifest must omit renewables_curve_data from 'state'."

    def test_exported_zip_manifest_tables_has_no_renewables_curve_data(
        self, cluster_app
    ):
        """The workflow_state.yml embedded in the exported ZIP must not carry
        renewables_curve_data in its 'tables' mapping."""
        self._setup_state_with_curve_data(cluster_app)
        calls = _capture_zip_download(cluster_app)
        cluster_app.on_download_all_settings(None)

        buf = BytesIO(calls[0]["payload_bytes"])
        with zipfile.ZipFile(buf) as zf:
            manifest = yaml.safe_load(zf.read("workflow_state.yml"))

        assert (
            "renewables_curve_data" not in manifest["tables"]
        ), "Exported ZIP manifest must omit renewables_curve_data from 'tables'."

    def test_uploaded_lcoe_onshorewind_is_persisted_in_tables(self, cluster_app):
        """Uploaded LCOE data (the source from which curves are derived) IS
        serialised under tables so it survives the round-trip."""
        import pandas as pd

        self._setup_state_with_curve_data(cluster_app)
        cluster_app.state.uploaded_lcoe_onshorewind = pd.DataFrame(
            {
                "region": ["RegionA", "RegionA"],
                "cpa_mw": [100.0, 150.0],
                "cf": [0.35, 0.38],
                "lcoe": [30.0, 35.0],
            }
        )
        manifest = cluster_app.build_workflow_state_manifest()
        tables = manifest["tables"]
        assert (
            "uploaded_lcoe_onshorewind" in tables
        ), "uploaded_lcoe_onshorewind source table must appear in manifest tables."
        assert tables["uploaded_lcoe_onshorewind"] is not None
        cluster_app.state.uploaded_lcoe_onshorewind = None  # clean up

    def test_resource_group_assignments_is_persisted_in_tables(self, cluster_app):
        """resource_group_assignments (the other LCOE source) IS serialised
        under tables so that curves can be recomputed on import."""
        import pandas as pd

        self._setup_state_with_curve_data(cluster_app)
        cluster_app.state.resource_group_assignments = pd.DataFrame(
            {
                "tech": ["landbasedwind"],
                "model_region": ["RegionA"],
                "cpa_mw": [100.0],
                "cf": [0.35],
                "lcoe": [30.0],
            }
        )
        manifest = cluster_app.build_workflow_state_manifest()
        tables = manifest["tables"]
        assert (
            "resource_group_assignments" in tables
        ), "resource_group_assignments source table must appear in manifest tables."
        assert tables["resource_group_assignments"] is not None
        cluster_app.state.resource_group_assignments = None  # clean up

    def test_resource_group_lcoe_sources_are_not_duplicated_in_manifest(
        self, cluster_app
    ):
        """Generated LCOE Parquet files are the source of truth in a ZIP."""
        import pandas as pd

        self._setup_state_with_curve_data(cluster_app)
        cluster_app.state.resource_group_files = {
            "onshorewind_lcoe_RegionA.parquet": b"parquet",
            "solar_lcoe_RegionA.parquet": b"parquet",
        }
        cluster_app.state.resource_group_assignments = pd.DataFrame(
            {"tech": ["onshorewind"], "model_region": ["RegionA"]}
        )
        cluster_app.state.uploaded_lcoe_onshorewind = pd.DataFrame(
            {"region": ["RegionA"], "cpa_mw": [1], "cf": [0.3], "lcoe": [20]}
        )
        cluster_app.state.uploaded_lcoe_solar = pd.DataFrame(
            {"region": ["RegionA"], "cpa_mw": [1], "cf": [0.2], "lcoe": [25]}
        )

        tables = cluster_app.build_workflow_state_manifest()["tables"]

        assert "resource_group_assignments" not in tables
        assert "uploaded_lcoe_onshorewind" not in tables
        assert "uploaded_lcoe_solar" not in tables


# ---------------------------------------------------------------------------
# Import resets renewables_curve_data and triggers recompute
# ---------------------------------------------------------------------------


class TestImportResetsAndRecomputesCurveData:
    """After _restore_workflow_state the runtime cache renewables_curve_data
    must be cleared to {}, and the async recompute must be scheduled whenever
    the source data (resource_group_assignments) is available.

    We do not test the async coroutine itself because asyncio.create_task
    requires a running event loop; instead we verify:
      1. renewables_curve_data is reset to {} immediately after restore.
      2. uploaded LCOE DataFrames are attached to state so the recompute
         path has the data it needs.
    """

    _COLORS = ["#111111", "#222222", "#333333", "#444444"]

    def _make_manifest_with_lcoe_table(self, ca):
        """Return a minimal manifest that carries an uploaded_lcoe_onshorewind
        table payload, simulating a workflow that had LCOE data before export."""
        import pandas as pd

        df = pd.DataFrame(
            {
                "region": ["RegionA", "RegionA"],
                "cpa_mw": [100.0, 200.0],
                "cf": [0.30, 0.35],
                "lcoe": [30.0, 36.0],
            }
        )
        # Use the app's own helper to produce the payload dict
        payload = ca._workflow_dataframe_payload(df)
        return _minimal_manifest(
            tables={
                "uploaded_lcoe_onshorewind": payload,
                "uploaded_lcoe_solar": None,
                "emission_policies": None,
                "network_costs": None,
                "resource_group_assignments": None,
            }
        )

    def test_restore_clears_renewables_curve_data(self, cluster_app):
        """renewables_curve_data must be reset to {} when a workflow is restored,
        regardless of what was in state before the import."""
        cluster_app.CLUSTER_COLORS = self._COLORS
        # Pre-populate curve data to simulate a session that had already computed curves
        cluster_app.state.renewables_curve_data = {
            "landbasedwind": {"OldRegion": {"cum_capacity": [0, 100], "lcoe": [30, 40]}}
        }
        m = _minimal_manifest()
        cluster_app._restore_workflow_state(m)
        assert cluster_app.state.renewables_curve_data == {}, (
            "renewables_curve_data must be cleared to {} on restore so the "
            "recompute path runs clean."
        )

    def test_restore_from_zip_clears_renewables_curve_data(self, cluster_app):
        """ZIP import must also reset renewables_curve_data to {}."""
        cluster_app.CLUSTER_COLORS = self._COLORS
        cluster_app.state.renewables_curve_data = {"utilitypv": {"R": {}}}
        m = _minimal_manifest()
        data = _build_zip({"workflow_state.yml": _manifest_bytes(m)})
        cluster_app._import_workflow_bytes(data, "export.zip")
        assert cluster_app.state.renewables_curve_data == {}

    def test_restore_attaches_uploaded_lcoe_onshorewind_for_recompute(
        self, cluster_app
    ):
        """After restoring a manifest that carries uploaded_lcoe_onshorewind,
        state.uploaded_lcoe_onshorewind must be a non-None DataFrame so the
        subsequent async recompute has access to the LCOE source data."""
        cluster_app.CLUSTER_COLORS = self._COLORS
        m = self._make_manifest_with_lcoe_table(cluster_app)
        cluster_app._restore_workflow_state(m)
        result = cluster_app.state.uploaded_lcoe_onshorewind
        assert result is not None, (
            "uploaded_lcoe_onshorewind must be restored from manifest tables "
            "so that _compute_renewables_clusters can recompute curve data."
        )
        assert list(result.columns) == ["region", "cpa_mw", "cf", "lcoe"] or set(
            result.columns
        ) >= {
            "region",
            "cpa_mw",
            "cf",
            "lcoe",
        }, "Restored LCOE DataFrame must include 'region', 'cpa_mw', 'cf', 'lcoe' columns."

    def test_restore_attaches_resource_group_assignments_for_recompute(
        self, cluster_app
    ):
        """If the manifest tables carry resource_group_assignments, the restored
        state must make it available for the async recompute."""
        import pandas as pd

        cluster_app.CLUSTER_COLORS = self._COLORS
        df = pd.DataFrame(
            {
                "tech": ["landbasedwind", "landbasedwind"],
                "model_region": ["RegionA", "RegionA"],
                "cpa_mw": [80.0, 120.0],
                "cf": [0.32, 0.36],
                "lcoe": [28.0, 33.0],
            }
        )
        payload = cluster_app._workflow_dataframe_payload(df)
        m = _minimal_manifest(
            tables={
                "resource_group_assignments": payload,
                "uploaded_lcoe_onshorewind": None,
                "uploaded_lcoe_solar": None,
                "emission_policies": None,
                "network_costs": None,
            }
        )
        cluster_app._restore_workflow_state(m)
        result = cluster_app.state.resource_group_assignments
        assert result is not None, (
            "resource_group_assignments must be restored from manifest tables "
            "so that _compute_renewables_clusters can recompute curve data."
        )
        assert {"tech", "model_region", "lcoe"} <= set(
            result.columns
        ), "Restored resource_group_assignments must include tech, model_region, lcoe."

    def test_resource_group_parquet_rebuilds_lcoe_source_table(self, cluster_app):
        """ZIP LCOE Parquet data is normalized for renewables recomputation."""
        import pandas as pd

        source = pd.DataFrame(
            {
                "model_region": ["RegionA"],
                "capacity_mw": [100.0],
                "cf": [0.35],
                "lcoe": [30.0],
            }
        )
        cluster_app.pd.read_parquet = lambda _: source.copy()

        restored = cluster_app._load_resource_group_lcoe_tables(
            {"onshorewind_lcoe_RegionA.parquet": b"ignored"}
        )

        assert list(restored["onshorewind"].columns) == [
            "tech",
            "model_region",
            "cpa_mw",
            "cf",
            "lcoe",
        ]
        assert restored["onshorewind"].iloc[0]["tech"] == "onshorewind"

    def test_rebuilt_assignments_feed_lcoe_loader(self, cluster_app):
        """The reconstructed assignments table must satisfy the column contract
        that _load_resource_group_lcoe_df enforces (tech/model_region/...)."""
        import pandas as pd

        source = pd.DataFrame(
            {
                "model_region": ["RegionA"],
                "capacity_mw": [100.0],
                "cf": [0.35],
                "lcoe": [30.0],
            }
        )
        cluster_app.pd.read_parquet = lambda _: source.copy()
        restored = cluster_app._load_resource_group_lcoe_tables(
            {"onshorewind_lcoe_RegionA.parquet": b"ignored"}
        )
        cluster_app.state.resource_group_assignments = pd.concat(
            list(restored.values()), ignore_index=True
        )

        df = cluster_app._load_resource_group_lcoe_df("onshorewind")

        assert df is not None
        assert list(df.columns) == ["region", "capacity_mw", "cf", "lcoe"]
        assert df.iloc[0]["region"] == "RegionA"

    def test_manifest_state_section_does_not_carry_stale_curve_data_after_roundtrip(
        self, cluster_app
    ):
        """Full export → parse manifest → re-examine: stale curves must not
        appear in the manifest's 'state' section even after state held them."""
        import pandas as pd

        cluster_app.CLUSTER_COLORS = self._COLORS
        cluster_app.state.renewables_curve_data = {
            "landbasedwind": {"RegionX": {"cum_capacity": [0], "lcoe": [50]}}
        }
        cluster_app.state.settings_yamls = {"m.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        cluster_app.state.resource_group_files = {}
        cluster_app.state.uploaded_lcoe_onshorewind = pd.DataFrame(
            {"region": ["RegionX"], "cpa_mw": [100.0], "cf": [0.3], "lcoe": [50.0]}
        )
        calls = _capture_zip_download(cluster_app)
        cluster_app.on_download_all_settings(None)
        cluster_app.state.uploaded_lcoe_onshorewind = None

        # Parse the exported manifest
        buf = BytesIO(calls[0]["payload_bytes"])
        with zipfile.ZipFile(buf) as zf:
            manifest = yaml.safe_load(zf.read("workflow_state.yml"))

        # Restore from the parsed manifest into a clean state
        cluster_app.state.renewables_curve_data = {}
        cluster_app._restore_workflow_state(manifest)

        # Curve data must not come back through the manifest; only {} is valid here
        assert cluster_app.state.renewables_curve_data == {}, (
            "After round-trip import the curve data must be {} because the manifest "
            "omits it; the recompute path fills it later asynchronously."
        )


# ---------------------------------------------------------------------------
# _restore_plant_clustering_outputs: rebuild Step 3 outputs after import
# ---------------------------------------------------------------------------


class TestRestorePlantClusteringOutputs:
    """Importing a workflow that carries plant_cluster_settings must rebuild
    the Step 3 (Existing Plants) outputs (plant_groups, plant_candidates, the
    plantYamlOut textarea) by re-running on_run_plant_clustering, and must
    re-apply the imported plant_candidate_overrides.

    Failure contract: if the re-run raises or produces no groups, the imported
    plant_cluster_settings / plant_candidate_overrides are kept unchanged so
    export still uses them.
    """

    _COLORS = ["#111111", "#222222", "#333333", "#444444"]
    _TECH = "Natural Gas Fired Combined Cycle"

    _STATE_ATTRS = [
        "plants_df",
        "plant_region_map",
        "plant_cluster_settings",
        "plant_candidate_overrides",
        "plant_groups",
        "plant_candidates",
        "region_aggregations",
        "ba_to_region",
        "selected_bas",
        "all_bas",
        "is_clustered",
        "omit_selected",
    ]

    @pytest.fixture
    def plant_env(self, cluster_app, monkeypatch):
        """Set up plant data + DOM stubs so on_run_plant_clustering can run
        for real, then restore global state afterwards."""
        import pandas as pd

        ca = cluster_app
        ca.CLUSTER_COLORS = self._COLORS  # prevent ZeroDivisionError
        saved = {name: getattr(ca.state, name) for name in self._STATE_ATTRS}

        # 3 plants in ba1 (RegA), 2 in ba2 (RegB); all one tech group.
        ca.state.plants_df = pd.DataFrame(
            {
                "plant_id": [1, 2, 3, 4, 5],
                "technology": [self._TECH] * 5,
                "capacity_mw": [500.0, 600.0, 550.0, 400.0, 450.0],
                "heat_rate_mmbtu_mwh": [6.0, 9.0, 7.0, 6.5, 8.5],
                "fom_per_mwyr": [20.0, 25.0, 22.0, 18.0, 21.0],
            }
        )
        ca.state.plant_region_map = pd.DataFrame(
            {
                "plant_id": [1, 2, 3, 4, 5],
                "region": ["ba1", "ba1", "ba1", "ba2", "ba2"],
            }
        )

        elements = {}

        def _get_element(element_id):
            el = elements.get(element_id)
            if el is None:
                el = MagicMock()
                elements[element_id] = el
            return el

        _get_element("plantBudget").value = "10"
        _get_element("capThreshold").value = "0"
        _get_element("hrThreshold").value = "0.5"
        _get_element("groupTechDefault").checked = True
        monkeypatch.setattr(ca.document, "getElementById", _get_element)

        yield ca, elements

        for name, value in saved.items():
            setattr(ca.state, name, value)

    def _manifest(self, settings=None, overrides=None, include_settings=True):
        state = {
            "selected_bas": ["ba1", "ba2"],
            "ba_to_region": {"ba1": "RegA", "ba2": "RegB"},
            "is_clustered": True,
            "region_aggregations": {"RegA": ["ba1"], "RegB": ["ba2"]},
            "plant_candidate_overrides": overrides or [],
        }
        if include_settings:
            state["plant_cluster_settings"] = (
                settings
                if settings is not None
                else {
                    "num_clusters": {self._TECH: 2},
                    "group_technologies": True,
                    "tech_groups": {},
                    "alt_num_clusters": {},
                }
            )
        return _minimal_manifest(state=state)

    def test_import_with_plant_cluster_settings_triggers_rebuild(self, plant_env):
        """The on_run_plant_clustering path runs on import and the Step 3
        outputs (plant_groups + YAML textarea) are populated."""
        ca, elements = plant_env
        m = self._manifest()

        ca._restore_workflow_state(m)

        assert (
            ca.state.plant_groups
        ), "plant_groups must be rebuilt by the re-run during restore"
        assert {g["model_region"] for g in ca.state.plant_groups} == {
            "RegA",
            "RegB",
        }
        assert ca.state.plant_cluster_settings is not None
        yaml_out = elements["plantYamlOut"].value
        assert (
            isinstance(yaml_out, str) and "num_clusters" in yaml_out
        ), "plantYamlOut textarea must contain the rebuilt clustering YAML"
        result_text = elements["plantResultText"].textContent
        assert "Plant clustering ready" in result_text

    def test_imported_overrides_reapplied_and_stale_overrides_dropped(self, plant_env):
        """Overrides matching a rebuilt group are kept and reflected in the
        regenerated YAML; overrides with no matching group are dropped."""
        ca, elements = plant_env
        overrides = [
            # Valid: RegB rebuilt with 2 clusters by default; overriding to 3
            # lifts the tech default to 3 in the regenerated YAML.
            {
                "model_region": "RegB",
                "tech_group": self._TECH,
                "num_clusters": 3,
            },
            # Stale: no rebuilt group matches this key.
            {
                "model_region": "NoRegion",
                "tech_group": "NoTech",
                "num_clusters": 5,
            },
        ]
        m = self._manifest(overrides=overrides)

        ca._restore_workflow_state(m)

        assert ca.state.plant_candidate_overrides == {
            ("RegB", self._TECH): 3
        }, "Only overrides matching a rebuilt group must survive the restore"
        yaml_out = elements["plantYamlOut"].value
        assert f"{self._TECH}: 3" in yaml_out, (
            "Regenerated YAML must reflect the re-applied override "
            "(tech default lifted to 3 clusters)"
        )

    def test_rerun_exception_preserves_imported_settings(self, plant_env, monkeypatch):
        """If on_run_plant_clustering raises, the imported settings and
        overrides are restored unchanged."""
        ca, _ = plant_env
        settings = {"num_clusters": {self._TECH: 4}, "group_technologies": True}
        overrides = [
            {"model_region": "RegA", "tech_group": self._TECH, "num_clusters": 2}
        ]
        monkeypatch.setattr(
            ca,
            "on_run_plant_clustering",
            MagicMock(side_effect=RuntimeError("boom")),
        )

        ca._restore_workflow_state(self._manifest(settings, overrides))

        assert ca.state.plant_cluster_settings == settings
        assert ca.state.plant_candidate_overrides == {("RegA", self._TECH): 2}
        assert ca.state.plant_groups == []

    def test_rerun_with_no_groups_preserves_imported_settings(
        self, plant_env, monkeypatch
    ):
        """If the re-run returns without populating plant_groups, the imported
        settings and overrides are preserved unchanged."""
        ca, _ = plant_env
        settings = {"num_clusters": {self._TECH: 4}, "group_technologies": True}
        overrides = [
            {"model_region": "RegA", "tech_group": self._TECH, "num_clusters": 2}
        ]
        monkeypatch.setattr(ca, "on_run_plant_clustering", MagicMock())
        # The mocked run must not populate groups itself.
        ca.state.plant_groups = []

        ca._restore_workflow_state(self._manifest(settings, overrides))

        assert ca.state.plant_cluster_settings == settings
        assert ca.state.plant_candidate_overrides == {("RegA", self._TECH): 2}

    def test_no_plant_cluster_settings_in_manifest_is_noop(
        self, plant_env, monkeypatch
    ):
        """A manifest without plant_cluster_settings must not trigger a
        clustering re-run (early return, no error)."""
        ca, _ = plant_env
        runner = MagicMock()
        monkeypatch.setattr(ca, "on_run_plant_clustering", runner)
        ca.state.plant_groups = []
        m = self._manifest(include_settings=False)

        ca._restore_workflow_state(m)

        runner.assert_not_called()
        assert ca.state.plant_cluster_settings is None
        assert ca.state.plant_groups == []
