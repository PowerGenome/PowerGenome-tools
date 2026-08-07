"""Tests for the VOLL / Demand Segments feature.

Covers:
- generate_demand_segments_csv: exact default CSV output, VOLL on first row
    only, $/MWh scaling with custom VOLL, empty-row skipping, None when all
    rows empty, and errors for non-numeric values.
- on_download_all_settings: demand_segments_voll.csv written under
    extra_inputs/ when segments exist; omitted when there are none.
- Workflow state manifest: forms include "demand_segments" rows and the
    "vollValue" form field; _restore_workflow_state dispatches to
    window.restoreDemandSegments for a round-trip.

The DOM is faked with small stand-in objects (same mocking philosophy as
tests/test_export_zip_download.py and tests/test_workflow_state_import_export.py).
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
# Fixture: load cluster_app with mocked browser globals
# (same pattern as test_export_zip_download.py)
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
# Fake DOM helpers
# ---------------------------------------------------------------------------


class _FakeInput:
    """Stand-in for an <input> element; only .value/.type are used."""

    def __init__(self, value="", input_type="number"):
        self.value = value
        self.type = input_type


class _FakeSegmentRow:
    """Stand-in for a .demand-segment-row element."""

    def __init__(self, cost, max_curtailment):
        self._inputs = {
            ".demand-segment-cost": _FakeInput(cost),
            ".demand-segment-max-curtailment": _FakeInput(max_curtailment),
        }

    def querySelector(self, selector):
        return self._inputs.get(selector)


class _FakeContainer:
    """Stand-in for the #demandSegmentRows container element."""

    def __init__(self, rows):
        self._rows = list(rows)

    def querySelectorAll(self, selector):
        if selector == ".demand-segment-row":
            return list(self._rows)
        return []


class _FakeDocument:
    """Minimal document: vollValue input + demand segment rows container."""

    def __init__(self, voll="5000", rows=()):
        self._elements = {
            "vollValue": _FakeInput(voll),
            "demandSegmentRows": _FakeContainer(rows),
        }

    def getElementById(self, element_id):
        return self._elements.get(element_id)


DEFAULT_ROWS = [
    ("1", "1"),
    ("0.9", "0.04"),
    ("0.55", "0.024"),
    ("0.2", "0.003"),
]

EXPECTED_DEFAULT_CSV = (
    "Voll,Demand_Segment,Cost_of_Demand_Curtailment_per_MW,Max_Demand_Curtailment,$/MWh\n"
    "5000,1,1,1,5000\n"
    ",2,0.9,0.04,4500\n"
    ",3,0.55,0.024,2750\n"
    ",4,0.2,0.003,1000\n"
)


def _install_fake_document(cluster_app, voll="5000", rows=DEFAULT_ROWS):
    """Swap cluster_app's document for a fake with the given VOLL and rows."""
    segment_rows = [_FakeSegmentRow(cost, max_c) for cost, max_c in rows]
    cluster_app.document = _FakeDocument(voll=voll, rows=segment_rows)


def _capture_zip_download(cluster_app):
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


# ---------------------------------------------------------------------------
# generate_demand_segments_csv
# ---------------------------------------------------------------------------


class TestGenerateDemandSegmentsCsv:
    def test_default_values_produce_exact_csv(self, cluster_app):
        _install_fake_document(cluster_app)

        csv_text = cluster_app.generate_demand_segments_csv()

        assert csv_text == EXPECTED_DEFAULT_CSV

    def test_voll_appears_only_on_first_row(self, cluster_app):
        _install_fake_document(cluster_app, voll="2000")

        csv_text = cluster_app.generate_demand_segments_csv()
        lines = csv_text.strip().split("\n")

        assert lines[1].startswith("2000,")
        for line in lines[2:]:
            assert line.startswith(","), "Voll column must be empty after row 1"

    def test_price_per_mwh_scales_with_custom_voll(self, cluster_app):
        _install_fake_document(cluster_app, voll="2000")

        csv_text = cluster_app.generate_demand_segments_csv()
        lines = csv_text.strip().split("\n")

        assert lines[1] == "2000,1,1,1,2000"
        assert lines[2] == ",2,0.9,0.04,1800"
        assert lines[3] == ",3,0.55,0.024,1100"
        assert lines[4] == ",4,0.2,0.003,400"

    def test_demand_segment_numbers_are_one_based_and_sequential(self, cluster_app):
        _install_fake_document(cluster_app, rows=[("0.5", "0.1"), ("0.25", "0.05")])

        csv_text = cluster_app.generate_demand_segments_csv()
        lines = csv_text.strip().split("\n")

        assert lines[1].split(",")[1] == "1"
        assert lines[2].split(",")[1] == "2"

    def test_rows_with_both_fields_empty_are_skipped(self, cluster_app):
        _install_fake_document(
            cluster_app,
            rows=[("1", "1"), ("", ""), ("0.5", "0.1")],
        )

        csv_text = cluster_app.generate_demand_segments_csv()
        lines = csv_text.strip().split("\n")

        # header + 2 data rows; skipped row does not consume a segment number
        assert len(lines) == 3
        assert lines[1] == "5000,1,1,1,5000"
        assert lines[2] == ",2,0.5,0.1,2500"

    def test_all_empty_rows_returns_none(self, cluster_app):
        _install_fake_document(cluster_app, rows=[("", ""), ("", "")])

        assert cluster_app.generate_demand_segments_csv() is None

    def test_no_rows_at_all_returns_none(self, cluster_app):
        _install_fake_document(cluster_app, rows=[])

        assert cluster_app.generate_demand_segments_csv() is None

    @pytest.mark.parametrize(
        "rows",
        [
            [("abc", "0.1")],  # non-numeric cost
            [("0.5", "xyz")],  # non-numeric max curtailment
            [("1", "1"), ("lots", "0.1")],  # bad value in a later row
        ],
    )
    def test_non_numeric_values_raise(self, cluster_app, rows):
        _install_fake_document(cluster_app, rows=rows)

        with pytest.raises(Exception, match="numeric"):
            cluster_app.generate_demand_segments_csv()

    def test_invalid_voll_falls_back_to_5000(self, cluster_app):
        _install_fake_document(cluster_app, voll="not-a-number", rows=[("1", "1")])

        csv_text = cluster_app.generate_demand_segments_csv()

        assert csv_text.strip().split("\n")[1] == "5000,1,1,1,5000"

    def test_missing_container_returns_none(self, cluster_app):
        # Document with only the VOLL input, no #demandSegmentRows container.
        fake_doc = _FakeDocument(voll="5000")
        fake_doc._elements.pop("demandSegmentRows")
        cluster_app.document = fake_doc

        assert cluster_app.generate_demand_segments_csv() is None

    def test_filename_constant(self, cluster_app):
        assert cluster_app.DEMAND_SEGMENTS_FILENAME == "demand_segments_voll.csv"


# ---------------------------------------------------------------------------
# ZIP export includes the demand segments CSV
# ---------------------------------------------------------------------------


class TestDownloadAllSettingsIncludesDemandSegmentsCsv:
    def test_csv_included_under_extra_inputs_when_segments_exist(self, cluster_app):
        _install_fake_document(cluster_app)
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        cluster_app.state.resource_group_files = {}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        assert len(calls) == 1
        with zipfile.ZipFile(BytesIO(calls[0]["payload_bytes"])) as zf:
            names = set(zf.namelist())
            assert "extra_inputs/demand_segments_voll.csv" in names
            csv_text = zf.read("extra_inputs/demand_segments_voll.csv").decode()
        assert csv_text == EXPECTED_DEFAULT_CSV

    def test_csv_omitted_when_no_segments(self, cluster_app):
        _install_fake_document(cluster_app, rows=[])
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        cluster_app.state.resource_group_files = {}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        with zipfile.ZipFile(BytesIO(calls[0]["payload_bytes"])) as zf:
            names = set(zf.namelist())
        assert "extra_inputs/demand_segments_voll.csv" not in names
        # YAML settings and the workflow manifest are still exported
        assert "settings/model_definition.yml" in names
        assert "workflow_state.yml" in names


# ---------------------------------------------------------------------------
# Workflow state manifest: demand_segments + vollValue
# ---------------------------------------------------------------------------


class TestWorkflowStateManifest:
    def test_workflow_form_ids_include_voll_value(self, cluster_app):
        assert "vollValue" in cluster_app._WORKFLOW_FORM_IDS

    def test_workflow_demand_segments_reads_dom_rows(self, cluster_app):
        _install_fake_document(cluster_app)

        segments = cluster_app._workflow_demand_segments()

        assert segments == [
            {"cost": cost, "max_curtailment": max_c} for cost, max_c in DEFAULT_ROWS
        ]

    def test_workflow_demand_segments_empty_without_container(self, cluster_app):
        fake_doc = _FakeDocument(voll="5000")
        fake_doc._elements.pop("demandSegmentRows")
        cluster_app.document = fake_doc

        assert cluster_app._workflow_demand_segments() == []

    def test_manifest_includes_demand_segments_and_voll_value(self, cluster_app):
        _install_fake_document(cluster_app, voll="7500")
        cluster_app.state.selected_bas = set()
        cluster_app.state.region_aggregations = None
        cluster_app.state.resource_group_files = {}

        manifest = cluster_app.build_workflow_state_manifest()

        assert manifest["forms"]["vollValue"] == "7500"
        assert manifest["forms"]["demand_segments"] == [
            {"cost": cost, "max_curtailment": max_c} for cost, max_c in DEFAULT_ROWS
        ]

    def test_exported_workflow_state_yml_contains_demand_segments(self, cluster_app):
        """The workflow_state.yml inside the export ZIP round-trips the form data."""
        _install_fake_document(cluster_app)
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        cluster_app.state.resource_group_files = {}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        with zipfile.ZipFile(BytesIO(calls[0]["payload_bytes"])) as zf:
            manifest = yaml.safe_load(zf.read("workflow_state.yml"))

        assert manifest["forms"]["vollValue"] == "5000"
        assert manifest["forms"]["demand_segments"] == [
            {"cost": cost, "max_curtailment": max_c} for cost, max_c in DEFAULT_ROWS
        ]


class TestRestoreWorkflowStateDemandSegments:
    def _minimal_manifest(self, forms):
        return {
            "schema": "powergenome-tools-workflow-state",
            "version": 1,
            "required_supplemental_files": [],
            "forms": forms,
            "state": {},
            "tables": {},
        }

    def test_restore_dispatches_to_restore_demand_segments(self, cluster_app):
        """_restore_workflow_state passes segments + vollValue to the JS hook."""
        cluster_app.state.region_aggregations = None  # avoid color rebuild
        restore_calls = []
        cluster_app.window.restoreDemandSegments = (
            lambda segments, voll: restore_calls.append((segments, voll))
        )
        segments = [
            {"cost": "1", "max_curtailment": "1"},
            {"cost": "0.7", "max_curtailment": "0.1"},
        ]
        manifest = self._minimal_manifest(
            {"demand_segments": segments, "vollValue": "8000"}
        )

        cluster_app._restore_workflow_state(manifest)

        assert restore_calls == [(segments, "8000")]

    def test_restore_skips_js_hook_when_no_segments(self, cluster_app):
        cluster_app.state.region_aggregations = None
        restore_calls = []
        cluster_app.window.restoreDemandSegments = (
            lambda segments, voll: restore_calls.append((segments, voll))
        )
        manifest = self._minimal_manifest({"demand_segments": []})

        cluster_app._restore_workflow_state(manifest)

        assert restore_calls == []
