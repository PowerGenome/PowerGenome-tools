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
