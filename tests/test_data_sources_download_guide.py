"""Tests for the Step 9 data-download guidance (DATA_SOURCES config).

Covers:
- The DATA_SOURCES deposit metadata (ids/fields/placeholder markers).
- build_data_yml_snippet(): example data.yml paths derived from DATA_SOURCES.
- render_data_sources_md(): Markdown guide bundled into the export ZIP.
- render_data_sources_html(): HTML fragment injected into the Step 9 pane.
- populate_data_sources_section(): wires the HTML fragment into #dataSourcesContent.
- on_download_all_settings: DATA_SOURCES.md is included at the ZIP root.
- web/index.html: Step 9 contains the "Download input data" section.
- Step 7 new-build resource groups guidance: the new-build note appears in both
  render_data_sources_md()/render_data_sources_html() and in the Step 7 pane.
"""

import contextlib
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

    def test_core_deposit_files_match_manifest(self, cluster_app):
        """The core deposit lists exactly the 15 files in the current manifest.

        nerc_reserve_margins_vintage.json was removed on PowerGenome-data main
        (commit 258e681); the assertion is intentional and must be updated if the
        manifest's file set changes.
        """
        by_id = {d["id"]: d for d in cluster_app.DATA_SOURCES}
        core_files = set(by_id["core"]["files"])
        assert core_files == {
            "cpi_data.csv",
            "distributed_capacity.parquet",
            "distributed_profiles.parquet",
            "dollar_year_adjustment.csv",
            "fuel_prices.parquet",
            "nerc_reserve_margins.csv",
            "operational_constraints_reeds.csv",
            "plant_region_map.csv",
            "reeds_generators_transformed.csv",
            "reeds_load_transformed.parquet",
            "regional_cost_multipliers.csv",
            "reserve_margins.csv",
            "technology_costs_atb.parquet",
            "technology_heat_rates_nrelatb.csv",
            "transmission_capacity_reeds.csv",
        }

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
# generate_data_settings
# ---------------------------------------------------------------------------


class TestGenerateDataSettings:
    def test_resource_groups_is_a_list_of_two_folders(self, cluster_app):
        """generate_data_settings() emits RESOURCE_GROUPS as a list of exactly the
        two folders: the derived region-specific new-build folder FIRST, the
        sample existing-groups Zenodo path SECOND."""
        data = yaml.safe_load(cluster_app.generate_data_settings())
        derived = cluster_app._get_resource_group_name()
        assert data["RESOURCE_GROUPS"] == [
            derived,
            cluster_app.EXISTING_RESOURCE_GROUPS_SAMPLE_PATH,
        ]
        # The first entry is the derived value (built from the app helper), not a
        # hardcoded constant.
        assert derived == cluster_app.build_resource_group_name_default()

    def test_resource_groups_first_entry_tracks_live_input(self, cluster_app):
        """The first RESOURCE_GROUPS entry follows #resourceGroupName when it
        holds a real string."""
        name_el = MagicMock()
        name_el.value = "my_rg"
        cluster_app.document.getElementById = MagicMock(return_value=name_el)

        data = yaml.safe_load(cluster_app.generate_data_settings())

        assert data["RESOURCE_GROUPS"][0] == "my_rg"
        assert data["RESOURCE_GROUPS"][1] == (
            cluster_app.EXISTING_RESOURCE_GROUPS_SAMPLE_PATH
        )

    def test_resource_groups_deposit_description_mentions_list(self, cluster_app):
        """The resource_groups deposit description explains the list form."""
        by_id = {d["id"]: d for d in cluster_app.DATA_SOURCES}
        assert "list" in by_id["resource_groups"]["description"].lower()

    def test_resource_groups_deposit_description_mentions_step7(self, cluster_app):
        """The resource_groups deposit description points at the web app's Step 7."""
        by_id = {d["id"]: d for d in cluster_app.DATA_SOURCES}
        description = by_id["resource_groups"]["description"]
        assert "Step 7" in description, (
            "resource_groups deposit description should mention the web app's "
            "Step 7 (Interconnection) as the source of the new-build groups"
        )


# ---------------------------------------------------------------------------
# build_data_yml_snippet
# ---------------------------------------------------------------------------


class TestBuildDataYmlSnippet:
    def test_snippet_contains_expected_paths(self, cluster_app):
        snippet = cluster_app.build_data_yml_snippet()

        assert 'data_location: ["~/PowerGenome-data/data"]' in snippet
        # First RESOURCE_GROUPS entry is the derived region-specific folder.
        derived = cluster_app.build_resource_group_name_default()
        assert f'  - "{derived}"' in snippet
        assert "# new-build groups, in the export ZIP" in snippet
        # Second RESOURCE_GROUPS entry is the existing-groups (Zenodo) path.
        assert '  - "~/PowerGenome-data/existing_resource_groups"' in snippet
        assert "# existing groups (Zenodo)" in snippet
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

    def test_snippet_new_build_folder_is_region_specific_derived(self, cluster_app):
        """The new-build RESOURCE_GROUPS entry is the region-specific derived
        folder (build_resource_group_name_default()), not a static constant, and
        does NOT carry the DATA_ROOT_EXAMPLE (~/PowerGenome-data) prefix.
        """
        snippet = cluster_app.build_data_yml_snippet()
        derived = cluster_app.build_resource_group_name_default()
        assert f'"{derived}"' in snippet
        assert f"{cluster_app.DATA_ROOT_EXAMPLE}/{derived}" not in snippet, (
            "the new-build resource groups folder must be the region-specific "
            "derived folder, not under DATA_ROOT_EXAMPLE (~/PowerGenome-data)"
        )

    def test_snippet_parses_to_resource_groups_list_of_two(self, cluster_app):
        """The snippet's RESOURCE_GROUPS is a yaml list of exactly two folder paths."""
        snippet = cluster_app.build_data_yml_snippet()
        parsed = yaml.safe_load(snippet)
        assert parsed["RESOURCE_GROUPS"] == [
            cluster_app._get_resource_group_name(),
            "~/PowerGenome-data/existing_resource_groups",
        ]

    def test_snippet_accepts_custom_resource_group_folder(self, cluster_app):
        """A caller-supplied folder becomes the FIRST RESOURCE_GROUPS entry.

        The snippet stays yaml-parseable and the generated comment records that
        this is the new-build groups folder, in the export ZIP.
        """
        snippet = cluster_app.build_data_yml_snippet("custom_folder")
        parsed = yaml.safe_load(snippet)
        assert parsed["RESOURCE_GROUPS"][0] == "custom_folder"
        assert "# new-build groups, in the export ZIP" in snippet

    def test_snippet_no_argument_uses_derived_resource_group_folder(self, cluster_app):
        """No/None argument → the derived region-specific folder in the first entry."""
        for snippet in (
            cluster_app.build_data_yml_snippet(),
            cluster_app.build_data_yml_snippet(None),
        ):
            parsed = yaml.safe_load(snippet)
            assert parsed["RESOURCE_GROUPS"][0] == (
                cluster_app.build_resource_group_name_default()
            )
            assert "# new-build groups, in the export ZIP" in snippet


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
        derived = cluster_app.build_resource_group_name_default()
        assert f'  - "{derived}"' in block
        assert "# new-build groups, in the export ZIP" in block
        assert '  - "~/PowerGenome-data/existing_resource_groups"' in block
        assert "# existing groups (Zenodo)" in block

    def test_contains_data_versioning_section(self, cluster_app):
        md = cluster_app.render_data_sources_md()
        versioning_idx = md.index("## Data versioning")
        versioning = md[versioning_idx:]

        assert "data_version" in versioning
        assert "release notes" in versioning

    def test_contains_new_build_resource_groups_section_before_example_paths(
        self, cluster_app
    ):
        """The Step 7 new-build note appears before the example data.yml paths."""
        md = cluster_app.render_data_sources_md()

        assert "## New-build resource groups (Step 7)" in md
        # Stable, human-meaningful substring of NEW_BUILD_RESOURCE_GROUPS_NOTE
        assert "included automatically in the final Export (Step 9) ZIP" in md

        new_build_idx = md.index("## New-build resource groups (Step 7)")
        example_idx = md.index("## Example data.yml paths")
        assert new_build_idx < example_idx, (
            "the new-build resource groups section must precede the example "
            "data.yml paths section"
        )

    def test_new_build_note_mentions_settings_folder_placement(self, cluster_app):
        """The Step 7 note places the new-build folder inside the export ZIP."""
        note = cluster_app.NEW_BUILD_RESOURCE_GROUPS_NOTE
        assert "included automatically in the final Export (Step 9) ZIP" in note
        assert "region-specific folder" in note
        assert "`settings`" in note
        assert "no manual download" in note
        md = cluster_app.render_data_sources_md()
        assert "included automatically in the final Export (Step 9) ZIP" in md
        # The md bundles the snippet; its new-build comment records the placement.
        assert "# new-build groups, in the export ZIP" in md

    def test_render_data_sources_md_accepts_custom_folder(self, cluster_app):
        """A supplied folder flows into the bundled example data.yml snippet."""
        md = cluster_app.render_data_sources_md("custom_folder")
        assert '  - "custom_folder"' in md
        assert "# new-build groups, in the export ZIP" in md

    def test_core_files_render_as_nested_bullet_list(self, cluster_app):
        """The core deposit's file manifest renders as one bullet per file.

        Previously all filenames were joined onto a single comma-separated
        line, which was hard to read for the 15-file core manifest. Each
        file should now get its own indented bullet, matching the nested
        <ul> used by render_data_sources_html().
        """
        md = cluster_app.render_data_sources_md()
        by_id = {d["id"]: d for d in cluster_app.DATA_SOURCES}
        core_files = by_id["core"]["files"]
        assert core_files, "expected the core deposit to list files"

        assert "- Contains files:" in md
        # No single comma-joined line listing every core file remains.
        assert "- Contains files: `" not in md
        for f in core_files:
            assert f"  - `{f}`" in md

    def test_deposit_with_no_files_omits_contains_files_line(self, cluster_app):
        """Deposits with an empty ``files`` list get no 'Contains files' bullet."""
        by_id = {d["id"]: d for d in cluster_app.DATA_SOURCES}
        empty_file_deposits = [d for d in by_id.values() if not d["files"]]
        assert empty_file_deposits, "expected at least one deposit with no files"

        md = cluster_app.render_data_sources_md()
        for deposit in empty_file_deposits:
            section_idx = md.index(f"## {deposit['title']}")
            next_heading_idx = md.find("\n## ", section_idx + 1)
            section = md[
                section_idx : next_heading_idx if next_heading_idx != -1 else None
            ]
            assert "Contains files" not in section


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

        # populate now also probes #resourceGroupName for the live folder name,
        # so only assert the container lookup happened (not call counts/order).
        get_by_id_calls = [
            call.args for call in cluster_app.document.getElementById.call_args_list
        ]
        assert ("dataSourcesContent",) in get_by_id_calls
        assert ("resourceGroupName",) in get_by_id_calls
        # The mocked input value is a non-str MagicMock, so the default constant
        # folder is used and the rendered HTML matches render_data_sources_html().
        assert fake_el.innerHTML == cluster_app.render_data_sources_html()

    def test_populate_uses_live_resource_group_folder(self, cluster_app):
        """When #resourceGroupName holds a real str, the snippet uses that folder."""
        name_el = MagicMock()
        name_el.value = "my_region_abc"
        content_el = MagicMock()
        elements = {"resourceGroupName": name_el, "dataSourcesContent": content_el}
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda el_id: elements.get(el_id)
        )

        cluster_app.populate_data_sources_section()

        assert 'id="dataYmlSnippet"' in content_el.innerHTML
        # The escaped snippet's FIRST RESOURCE_GROUPS entry is the live folder,
        # NOT the derived-default fallback.
        assert "  - &quot;my_region_abc&quot;" in content_el.innerHTML
        assert (
            "&quot;resource_groups_unspecified_default&quot;"
            not in content_el.innerHTML
        )
        assert "# new-build groups, in the export ZIP" in content_el.innerHTML

    def test_render_data_sources_html_accepts_custom_folder(self, cluster_app):
        """A supplied folder flows into the escaped snippet inside the HTML."""
        html = cluster_app.render_data_sources_html("custom_folder")
        assert "custom_folder" in html
        # html.escape leaves '#' and plain alnum text untouched.
        assert "# new-build groups, in the export ZIP" in html

    def test_html_new_build_note_mentions_settings_folder_placement(self, cluster_app):
        """The HTML Step 7 note also places the new-build folder in the export ZIP.

        The note is html.escaped, but backticks, `settings`, and the em dash
        survive unchanged.
        """
        html = cluster_app.render_data_sources_html()
        assert "included automatically in the final Export (Step 9) ZIP" in html
        assert "`settings` folder" in html
        assert "no manual download" in html

    def test_html_contains_new_build_resource_groups_section(self, cluster_app):
        """The Step 7 new-build note appears before the example paths heading."""
        html = cluster_app.render_data_sources_html()

        assert "New-build resource groups (Step 7)" in html
        # html.escape leaves RESOURCE_GROUPS and the em dash unchanged, so the
        # same stable substring used for the Markdown note works here.
        assert "included automatically in the final Export (Step 9) ZIP" in html

        new_build_idx = html.index("New-build resource groups (Step 7)")
        example_idx = html.index("<h4>Example data.yml paths</h4>")
        textarea_idx = html.index('id="dataYmlSnippet"')
        assert new_build_idx < example_idx, (
            "the new-build resource groups section must precede the example "
            "data.yml paths heading"
        )
        assert (
            new_build_idx < textarea_idx
        ), "the new-build resource groups section must precede the snippet textarea"


# ---------------------------------------------------------------------------
# _live_resource_group_folder()
# ---------------------------------------------------------------------------


class TestLiveResourceGroupFolder:
    """_live_resource_group_folder() resolves the live Step 7 region name."""

    def test_returns_input_value_when_real_string(self, cluster_app):
        name_el = MagicMock()
        name_el.value = "my_region_abc"
        cluster_app.document.getElementById = MagicMock(return_value=name_el)

        assert cluster_app._live_resource_group_folder() == "my_region_abc"

    def test_strips_whitespace_around_input_value(self, cluster_app):
        name_el = MagicMock()
        name_el.value = "  my_region_abc  "
        cluster_app.document.getElementById = MagicMock(return_value=name_el)

        assert cluster_app._live_resource_group_folder() == "my_region_abc"

    def test_falls_back_when_element_missing(self, cluster_app):
        cluster_app.document.getElementById = MagicMock(return_value=None)

        assert cluster_app._live_resource_group_folder() == (
            cluster_app.build_resource_group_name_default()
        )

    def test_falls_back_when_value_empty(self, cluster_app):
        name_el = MagicMock()
        name_el.value = ""
        cluster_app.document.getElementById = MagicMock(return_value=name_el)

        assert cluster_app._live_resource_group_folder() == (
            cluster_app.build_resource_group_name_default()
        )

    def test_falls_back_when_value_whitespace_only(self, cluster_app):
        name_el = MagicMock()
        name_el.value = "   "
        cluster_app.document.getElementById = MagicMock(return_value=name_el)

        assert cluster_app._live_resource_group_folder() == (
            cluster_app.build_resource_group_name_default()
        )

    def test_falls_back_when_value_is_not_a_string(self, cluster_app):
        """A non-str (e.g. MagicMock) input value falls back to the derived default."""
        name_el = MagicMock()
        name_el.value = MagicMock()
        cluster_app.document.getElementById = MagicMock(return_value=name_el)

        assert cluster_app._live_resource_group_folder() == (
            cluster_app.build_resource_group_name_default()
        )


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


class TestIndexHtmlStep7:
    def test_step7_has_output_files_hint_after_download_zip_button(self):
        html = _read_index_html()
        step7_start = html.find('<div id="step-7"')
        assert step7_start != -1, "Could not find Step 7 pane"
        step7 = html[step7_start:]

        # The hint wraps across several source lines, but the leading phrase
        # "Downloading the ZIP here is optional" appears contiguously on a single
        # line, so a plain substring search on the raw pane is safe. Multi-line
        # phrases below are matched against a whitespace-normalized copy.
        hint_text = "Downloading the ZIP here is optional"
        zip_btn_idx = step7.find('id="downloadResourceGroupsBtn"')
        hint_idx = step7.find(hint_text)
        rg_mentions = step7.count("RESOURCE_GROUPS")

        assert zip_btn_idx != -1, "Could not find the Download ZIP button"
        # The button is now explicitly optional: the files ship in the export.
        assert (
            "Download ZIP (optional)" in step7
        ), "the Step 7 button should be labeled 'Download ZIP (optional)'"
        assert hint_idx != -1, "Could not find the output-files save hint"
        assert (
            hint_idx > zip_btn_idx
        ), "the save hint must come after the Download ZIP button"

        # The hint should explain that the folder maps to RESOURCE_GROUPS in the
        # generated data.yml.
        assert rg_mentions >= 1, (
            "the Step 7 pane should mention RESOURCE_GROUPS (the setting used in "
            "the generated data.yml)"
        )

        # The hint explains the files are bundled into the final export ZIP under
        # a region-specific folder, listed as the FIRST RESOURCE_GROUPS entry
        # (wrapped across source lines and styled with <code>, so normalize
        # whitespace and match including the inline tag).
        step7_norm = " ".join(step7.split())
        assert (
            "automatically included in the final export" in step7_norm
        ), "the Step 7 hint should say the files are included in the final export"
        assert (
            "lists that folder as its first entry" in step7_norm
        ), "the Step 7 hint should say RESOURCE_GROUPS lists the folder as its first entry"
