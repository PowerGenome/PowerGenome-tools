"""Tests for the Step 9 data-download guidance (DATA_SOURCES config).

Covers:
- The DATA_SOURCES deposit metadata (ids/fields/placeholder markers).
- build_data_yml_snippet(): example data.yml paths derived from DATA_SOURCES.
- render_data_sources_md(): Markdown guide bundled into the export ZIP.
- render_data_sources_html(): HTML fragment injected into the Step 9 pane.
- populate_data_sources_section(): wires the HTML fragment into #dataSourcesContent.
- on_download_all_settings: DATA_SOURCES.md is included at the ZIP root.
- web/index.html: Step 9 contains the "Download input data" section.
"""

import contextlib
import importlib.util
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixture: load cluster_app with mocked browser globals
# (copied verbatim from tests/test_export_zip_download.py — proven to work.)
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
# Helpers (mirror tests/test_export_zip_download.py)
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


def _read_index_html() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / "web" / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# DATA_SOURCES deposit metadata
# ---------------------------------------------------------------------------


class TestDataSourcesConfig:
    def test_data_sources_has_three_deposits_with_required_fields(self, cluster_app):
        sources = cluster_app.DATA_SOURCES
        assert len(sources) == 3
        assert {d["id"] for d in sources} == {"core", "profiles", "resource_groups"}

        required_fields = (
            "id",
            "title",
            "description",
            "url",
            "doi",
            "target_folder",
            "settings_keys",
            "files",
        )
        for deposit in sources:
            missing = [f for f in required_fields if f not in deposit]
            assert not missing, f"deposit {deposit['id']!r} missing: {missing}"
            assert deposit["title"], f"deposit {deposit['id']!r} has empty title"
            assert deposit[
                "description"
            ], f"deposit {deposit['id']!r} has empty description"
            assert deposit["url"], f"deposit {deposit['id']!r} has empty url"
            assert deposit["doi"], f"deposit {deposit['id']!r} has empty doi"
            assert deposit[
                "target_folder"
            ], f"deposit {deposit['id']!r} has empty target_folder"
            assert deposit[
                "settings_keys"
            ], f"deposit {deposit['id']!r} must have non-empty settings_keys"
            assert isinstance(
                deposit["files"], list
            ), f"deposit {deposit['id']!r} files must be a list"

    def test_settings_keys_map_to_the_data_yml_keys(self, cluster_app):
        by_id = {d["id"]: d for d in cluster_app.DATA_SOURCES}
        assert "data_location" in by_id["core"]["settings_keys"]
        assert "RESOURCE_GROUP_PROFILES" in by_id["profiles"]["settings_keys"]
        assert "RESOURCE_GROUPS" in by_id["resource_groups"]["settings_keys"]

    def test_placeholder_deposits_are_clearly_marked(self, cluster_app):
        """profiles/resource_groups are placeholders; core points at the sandbox record.

        Asserting the exact placeholder markers is intentional: a future
        production update to the real Zenodo records must update this test too.
        """
        by_id = {d["id"]: d for d in cluster_app.DATA_SOURCES}

        for deposit_id in ("profiles", "resource_groups"):
            deposit = by_id[deposit_id]
            assert deposit["url"].startswith("https://zenodo.org/"), (
                f"{deposit_id} url should be a https://zenodo.org/ placeholder, "
                f"got {deposit['url']!r}"
            )
            assert (
                "pending" in deposit["doi"].lower()
            ), f"{deposit_id} doi should mark a pending DOI, got {deposit['doi']!r}"

        assert by_id["core"]["url"] == "https://sandbox.zenodo.org/records/590994", (
            "core deposit url should point at the sandbox record until the "
            "production Zenodo deposit exists"
        )


# ---------------------------------------------------------------------------
# build_data_yml_snippet
# ---------------------------------------------------------------------------


class TestBuildDataYmlSnippet:
    def test_snippet_contains_expected_paths(self, cluster_app):
        snippet = cluster_app.build_data_yml_snippet()

        assert 'data_location: ["~/PowerGenome-data/data"]' in snippet
        assert (
            'RESOURCE_GROUPS: "~/PowerGenome-data/existing_resource_groups"' in snippet
        )
        assert (
            'RESOURCE_GROUP_PROFILES: "~/PowerGenome-data/resource_profiles"' in snippet
        )

    def test_snippet_paths_derived_from_data_sources_target_folders(self, cluster_app):
        """The snippet is derived from DATA_SOURCES target folders, not hardcoded."""
        snippet = cluster_app.build_data_yml_snippet()
        paths = {d["id"]: d["target_folder"] for d in cluster_app.DATA_SOURCES}

        for deposit_id, python_key in (
            ("core", "data_location"),
            ("resource_groups", "RESOURCE_GROUPS"),
            ("profiles", "RESOURCE_GROUP_PROFILES"),
        ):
            expected = f"{cluster_app.DATA_ROOT_EXAMPLE}/{paths[deposit_id]}"
            assert expected in snippet, (
                f"snippet must contain a path for {python_key} derived from the "
                f"{deposit_id!r} deposit target_folder"
            )


# ---------------------------------------------------------------------------
# render_data_sources_md
# ---------------------------------------------------------------------------


class TestRenderDataSourcesMd:
    def test_starts_with_heading_and_mentions_zenodo(self, cluster_app):
        md = cluster_app.render_data_sources_md()
        assert md.startswith("# ")
        assert "Zenodo" in md

    def test_includes_data_yml_snippet_in_fenced_yaml_block(self, cluster_app):
        md = cluster_app.render_data_sources_md()
        fenced_start = md.index("```yaml")
        fenced_end = md.index("```", fenced_start + len("```yaml"))
        block = md[fenced_start:fenced_end]

        assert 'data_location: ["~/PowerGenome-data/data"]' in block
        assert (
            'RESOURCE_GROUP_PROFILES: "~/PowerGenome-data/resource_profiles"' in block
        )
        assert 'RESOURCE_GROUPS: "~/PowerGenome-data/existing_resource_groups"' in block

    def test_contains_data_versioning_section(self, cluster_app):
        md = cluster_app.render_data_sources_md()
        versioning_idx = md.index("## Data versioning")
        versioning = md[versioning_idx:]

        assert "data_version" in versioning
        assert "release notes" in versioning


# ---------------------------------------------------------------------------
# render_data_sources_html + populate_data_sources_section
# ---------------------------------------------------------------------------


class TestRenderDataSourcesHtml:
    def test_html_contains_data_yml_snippet_textarea(self, cluster_app):
        html = cluster_app.render_data_sources_html()
        assert 'id="dataYmlSnippet"' in html
        assert "readonly" in html

    def test_html_contains_settings_keys_and_sandbox_link(self, cluster_app):
        html = cluster_app.render_data_sources_html()
        assert "<code>data_location</code>" in html
        assert "<code>RESOURCE_GROUP_PROFILES</code>" in html
        assert "<code>RESOURCE_GROUPS</code>" in html
        assert "https://sandbox.zenodo.org/records/590994" in html

    def test_html_contains_escaped_snippet_with_data_root(self, cluster_app):
        html = cluster_app.render_data_sources_html()
        # html.escape turns the snippet's double quotes into &quot;, but ~ and /
        # pass through unchanged.
        assert "~/PowerGenome-data" in html
        assert "&quot;~/PowerGenome-data/data&quot;" in html

    def test_populate_data_sources_section_sets_inner_html(self, cluster_app):
        fake_el = MagicMock()
        cluster_app.document.getElementById = MagicMock(return_value=fake_el)

        cluster_app.populate_data_sources_section()

        cluster_app.document.getElementById.assert_called_once_with(
            "dataSourcesContent"
        )
        assert fake_el.innerHTML == cluster_app.render_data_sources_html()


# ---------------------------------------------------------------------------
# DATA_SOURCES.md bundled into the export ZIP
# ---------------------------------------------------------------------------


class TestDataSourcesMdInZip:
    def test_data_sources_md_included_at_zip_root(self, cluster_app):
        cluster_app.state.settings_yamls = {"model_definition.yml": "x: 1\n"}
        cluster_app.state.emission_policies_df = None
        cluster_app.state.resource_group_files = {}
        calls = _capture_zip_download(cluster_app)

        cluster_app.on_download_all_settings(None)

        assert len(calls) == 1
        with _open_zip_from_calls(calls) as zf:
            names = zf.namelist()
            assert cluster_app.DATA_SOURCES_FILENAME in names
            content = zf.read(cluster_app.DATA_SOURCES_FILENAME).decode()

        assert content.startswith("# ")
        assert "Zenodo" in content
        assert "data_version" in content


# ---------------------------------------------------------------------------
# index.html Step 9 presence
# ---------------------------------------------------------------------------


class TestIndexHtmlStep9:
    def test_step9_has_download_input_data_section_after_zip_button(self):
        html = _read_index_html()
        step9_start = html.find('<div id="step-9"')
        assert step9_start != -1, "Could not find Step 9 pane"
        step9 = html[step9_start:]

        zip_button_idx = step9.find("Download All as ZIP")
        label_idx = step9.find("<label>Download input data</label>")
        div_idx = step9.find('<div id="dataSourcesContent"')

        assert zip_button_idx != -1, "Could not find the Download All as ZIP button"
        assert label_idx != -1, "Could not find the Download input data label"
        assert div_idx != -1, "Could not find the dataSourcesContent div"

        assert (
            label_idx > zip_button_idx
        ), "Download input data section must come after the Download All as ZIP button"
        assert (
            div_idx > label_idx
        ), "dataSourcesContent div must come after the Download input data label"
