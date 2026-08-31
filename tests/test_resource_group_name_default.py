"""Tests for the Step 7 "Region Name" default-name feature.

Covers:
- build_resource_group_name_default(): the derived default mirrors the network
  costs filename stem (region count, interconnects, grouping) with a
  ``resource_groups`` prefix.
- update_resource_group_name_default(): refreshes the #resourceGroupName input
  only when it holds the previous auto default / the static placeholder / is
  empty, preserving custom user names; no-ops when the element is missing; and
  re-populates the Step 9 data-sources section so its example snippet reflects
  the refreshed name.
- on_resource_group_name_change(): re-populates the Step 9 data-sources section
  when the Step 7 region name is edited.
- _get_resource_group_name(): falls back to the derived default when the input
  is empty or whitespace.
- reset_region_dependent_state(): wiring that refreshes the default whenever
  regions are reset.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
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
# Helpers
# ---------------------------------------------------------------------------

HIGH_LEVEL_DEFAULT = "resource_groups_2r_Eastern-Western_nercr"
HIGH_LEVEL_FILENAME = "network_costs_2r_Eastern-Western_nercr.csv"


def _setup_derived_state(cluster_app):
    """Populate state so the derived stem is _2r_Eastern-Western_nercr.

    Interconnect names (``Eastern``/``Western``) are deliberately capitalized:
    they pass through the filename sanitizer unchanged (no lowercasing).
    """
    cluster_app.state.region_aggregations = {"R1": ["a"], "R2": ["b"]}
    cluster_app.state.current_grouping = "nercr"
    cluster_app.state.selected_bas = {"a", "b"}
    cluster_app.state.hierarchy_df = pd.DataFrame(
        {"ba": ["a", "b"], "interconnect": ["Eastern", "Western"]}
    )


def _setup_empty_state(cluster_app):
    """Clear all state inputs so every stem part falls back to a placeholder."""
    cluster_app.state.region_aggregations = None
    cluster_app.state.selected_bas = set()
    cluster_app.state.hierarchy_df = None
    cluster_app.state.current_grouping = None


def _fake_name_element(value):
    """Return a MagicMock standing in for the #resourceGroupName input."""
    el = MagicMock()
    el.value = value
    return el


# ---------------------------------------------------------------------------
# build_resource_group_name_default()
# ---------------------------------------------------------------------------


class TestBuildResourceGroupNameDefault:
    """The derived default mirrors the network costs filename stem."""

    def test_mirrors_network_costs_filename_stem(self, cluster_app):
        """Same stem as network costs file, with resource_groups prefix."""
        _setup_derived_state(cluster_app)

        assert cluster_app._build_network_costs_filename() == HIGH_LEVEL_FILENAME
        assert cluster_app.build_resource_group_name_default() == HIGH_LEVEL_DEFAULT

    def test_full_fallback_when_no_region_state(self, cluster_app):
        """No region/grouping state → unspecified/default placeholders."""
        _setup_empty_state(cluster_app)

        assert cluster_app.build_resource_group_name_default() == (
            "resource_groups_unspecified_default"
        )
        assert cluster_app._build_network_costs_filename() == (
            "network_costs_unspecified_default.csv"
        )

    def test_region_count_part_matches_network_costs(self, cluster_app):
        """Region count segment is identical in both names."""
        cluster_app.state.region_aggregations = {
            f"Region{i}": [f"ba{i}"] for i in range(5)
        }
        cluster_app.state.selected_bas = set()
        cluster_app.state.hierarchy_df = None
        cluster_app.state.current_grouping = "nercr"

        assert cluster_app.build_resource_group_name_default() == (
            "resource_groups_5r_unspecified_nercr"
        )
        assert cluster_app._build_network_costs_filename() == (
            "network_costs_5r_unspecified_nercr.csv"
        )

    def test_grouping_fallback_and_sanitization(self, cluster_app):
        """Grouping 'default' fallback and special-char sanitization match."""
        _setup_empty_state(cluster_app)
        cluster_app.state.current_grouping = "my grouping!"
        cluster_app._build_network_costs_filename()

        default = cluster_app.build_resource_group_name_default()

        assert "my_grouping_" in default
        assert "!" not in default
        assert " " not in default

    def test_interconnect_sanitization_matches(self, cluster_app):
        """Special chars in interconnect names are underscored in both names."""
        cluster_app.state.region_aggregations = None
        cluster_app.state.selected_bas = {"ba1"}
        cluster_app.state.hierarchy_df = pd.DataFrame(
            {"ba": ["ba1"], "interconnect": ["East/North America"]}
        )
        cluster_app.state.current_grouping = None

        default = cluster_app.build_resource_group_name_default()

        assert "/" not in default
        assert " " not in default
        assert "East_North_America" in default

    def test_prefix_and_no_double_underscores(self, cluster_app):
        """Name starts with resource_groups and has no repeated separators."""
        _setup_empty_state(cluster_app)
        cluster_app.state.current_grouping = "nercr"

        default = cluster_app.build_resource_group_name_default()

        assert default.startswith("resource_groups_")
        assert "__" not in default


# ---------------------------------------------------------------------------
# update_resource_group_name_default()
# ---------------------------------------------------------------------------


class TestUpdateResourceGroupNameDefault:
    """Refreshes the #resourceGroupName input only when appropriate."""

    def test_sets_default_when_value_empty(self, cluster_app):
        """An empty field is overwritten with the derived default."""
        _setup_derived_state(cluster_app)
        el = _fake_name_element("")

        with patch.object(cluster_app.document, "getElementById", return_value=el):
            cluster_app.update_resource_group_name_default()

        assert el.value == HIGH_LEVEL_DEFAULT
        assert cluster_app.state.resource_group_name_default == HIGH_LEVEL_DEFAULT

    def test_sets_default_when_static_placeholder(self, cluster_app):
        """The static 'resource_groups' value is replaced by the derived default."""
        _setup_derived_state(cluster_app)
        el = _fake_name_element("resource_groups")

        with patch.object(cluster_app.document, "getElementById", return_value=el):
            cluster_app.update_resource_group_name_default()

        assert el.value == HIGH_LEVEL_DEFAULT
        assert cluster_app.state.resource_group_name_default == HIGH_LEVEL_DEFAULT

    def test_refreshes_when_previous_auto_default(self, cluster_app):
        """A stale auto default is updated after the region state changes."""
        _setup_derived_state(cluster_app)
        el = _fake_name_element(HIGH_LEVEL_DEFAULT)

        # Simulate a prior default from an older (smaller) region setup.
        cluster_app.state.resource_group_name_default = (
            "resource_groups_1r_Eastern_nercr"
        )

        with patch.object(cluster_app.document, "getElementById", return_value=el):
            cluster_app.update_resource_group_name_default()

        assert el.value == HIGH_LEVEL_DEFAULT
        assert cluster_app.state.resource_group_name_default == HIGH_LEVEL_DEFAULT

    def test_preserves_custom_name(self, cluster_app):
        """A user-typed name is left untouched while the tracked default updates."""
        _setup_derived_state(cluster_app)
        el = _fake_name_element("my_custom_name")

        with patch.object(cluster_app.document, "getElementById", return_value=el):
            cluster_app.update_resource_group_name_default()

        assert el.value == "my_custom_name"
        assert cluster_app.state.resource_group_name_default == HIGH_LEVEL_DEFAULT

    def test_custom_name_survives_region_changes(self, cluster_app):
        """Two consecutive calls keep a custom name across a state change."""
        el = _fake_name_element("my_custom_name")
        with patch.object(cluster_app.document, "getElementById", return_value=el):
            cluster_app.update_resource_group_name_default()
        # Region state changes between calls; the custom name must survive.
        _setup_derived_state(cluster_app)
        with patch.object(cluster_app.document, "getElementById", return_value=el):
            cluster_app.update_resource_group_name_default()

        assert el.value == "my_custom_name"
        assert cluster_app.state.resource_group_name_default == HIGH_LEVEL_DEFAULT

    def test_whitespace_only_value_is_preserved(self, cluster_app):
        """A whitespace-only value counts as a (custom) non-empty name.

        This documents current behavior: only exactly-empty, the previous auto
        default, or the static placeholder are overwritten; whitespace is not
        collapsed before the comparison.
        """
        _setup_derived_state(cluster_app)
        el = _fake_name_element("   ")

        with patch.object(cluster_app.document, "getElementById", return_value=el):
            cluster_app.update_resource_group_name_default()

        assert el.value == "   "
        assert cluster_app.state.resource_group_name_default == HIGH_LEVEL_DEFAULT

    def test_state_tracks_derived_default_each_call(self, cluster_app):
        """state.resource_group_name_default is always refreshed to the latest stem."""
        _setup_empty_state(cluster_app)
        el = _fake_name_element("resource_groups")
        with patch.object(cluster_app.document, "getElementById", return_value=el):
            cluster_app.update_resource_group_name_default()

        assert cluster_app.state.resource_group_name_default == (
            "resource_groups_unspecified_default"
        )

        _setup_derived_state(cluster_app)
        with patch.object(cluster_app.document, "getElementById", return_value=el):
            cluster_app.update_resource_group_name_default()

        assert cluster_app.state.resource_group_name_default == HIGH_LEVEL_DEFAULT

    def test_noop_when_element_missing(self, cluster_app):
        """Missing #resourceGroupName element → no exception, no state change."""
        _setup_derived_state(cluster_app)
        cluster_app.state.resource_group_name_default = "previous_default"

        with patch.object(cluster_app.document, "getElementById", return_value=None):
            cluster_app.update_resource_group_name_default()

        assert cluster_app.state.resource_group_name_default == "previous_default"

    def _fresh_name_and_content_elements(self):
        """Return (name_el, content_el) plus a getElementById that routes by id.

        The Step 9 data-sources section re-population reads both
        ``resourceGroupName`` (for the live folder name) and
        ``dataSourcesContent`` (the container to fill), so the mock must return
        a different fake per id.
        """
        name_el = _fake_name_element("resource_groups")
        content_el = MagicMock()
        elements = {"resourceGroupName": name_el, "dataSourcesContent": content_el}
        return (
            name_el,
            content_el,
            MagicMock(side_effect=lambda el_id: elements.get(el_id)),
        )

    def test_repopulates_data_sources_section_with_new_default(self, cluster_app):
        """After refreshing the default, the Step 9 snippet shows that folder."""
        _setup_derived_state(cluster_app)
        name_el, content_el, get_by_id = self._fresh_name_and_content_elements()
        cluster_app.document.getElementById = get_by_id

        cluster_app.update_resource_group_name_default()

        assert name_el.value == HIGH_LEVEL_DEFAULT
        assert HIGH_LEVEL_DEFAULT in content_el.innerHTML, (
            "the Step 9 data-sources section should re-render using the "
            "refreshed Step 7 region name"
        )

    def test_repopulates_data_sources_section_with_custom_name(self, cluster_app):
        """A preserved custom name is reflected in the re-rendered snippet."""
        _setup_derived_state(cluster_app)
        name_el = _fake_name_element("my_custom_name")
        content_el = MagicMock()
        elements = {"resourceGroupName": name_el, "dataSourcesContent": content_el}
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda el_id: elements.get(el_id)
        )

        cluster_app.update_resource_group_name_default()

        assert name_el.value == "my_custom_name"
        assert "my_custom_name" in content_el.innerHTML

    def test_repopulates_noop_when_data_sources_container_missing(self, cluster_app):
        """A missing #dataSourcesContent element doesn't break the refresh."""
        _setup_derived_state(cluster_app)
        name_el = _fake_name_element("resource_groups")
        elements = {"resourceGroupName": name_el}
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda el_id: elements.get(el_id)
        )

        cluster_app.update_resource_group_name_default()  # no exception

        assert name_el.value == HIGH_LEVEL_DEFAULT


# ---------------------------------------------------------------------------
# on_resource_group_name_change()
# ---------------------------------------------------------------------------


class TestOnResourceGroupNameChange:
    """Typing in the Step 7 region name refreshes the Step 9 method snippet."""

    def test_repopulates_data_sources_section_with_live_name(self, cluster_app):
        """The handler re-renders the snippet with the current input value."""
        name_el = _fake_name_element("my_region_abc")
        content_el = MagicMock()
        elements = {"resourceGroupName": name_el, "dataSourcesContent": content_el}
        cluster_app.document.getElementById = MagicMock(
            side_effect=lambda el_id: elements.get(el_id)
        )

        cluster_app.on_resource_group_name_change(None)

        assert "my_region_abc" in content_el.innerHTML

    def test_tolerates_missing_elements(self, cluster_app):
        """No elements wired up yet → handler is a silent no-op."""
        cluster_app.document.getElementById = MagicMock(return_value=None)

        cluster_app.on_resource_group_name_change(None)  # no exception


# ---------------------------------------------------------------------------
# _get_resource_group_name()
# ---------------------------------------------------------------------------


class TestGetResourceGroupName:
    """Falls back to the derived default when the input is empty/whitespace."""

    def test_falls_back_when_value_empty(self, cluster_app):
        """Empty input → derived default instead of the static 'resource_groups'."""
        _setup_derived_state(cluster_app)
        el = _fake_name_element("")

        with patch.object(cluster_app.document, "getElementById", return_value=el):
            result = cluster_app._get_resource_group_name()

        assert result == HIGH_LEVEL_DEFAULT

    def test_falls_back_when_value_whitespace(self, cluster_app):
        """Whitespace-only input is stripped to empty and falls back."""
        _setup_derived_state(cluster_app)
        el = _fake_name_element("   ")

        with patch.object(cluster_app.document, "getElementById", return_value=el):
            result = cluster_app._get_resource_group_name()

        assert result == HIGH_LEVEL_DEFAULT

    def test_falls_back_to_derived_default_not_static(self, cluster_app):
        """The fallback is the derived name, not the old literal 'resource_groups'."""
        _setup_derived_state(cluster_app)
        el = _fake_name_element("")

        with patch.object(cluster_app.document, "getElementById", return_value=el):
            result = cluster_app._get_resource_group_name()

        assert result == "resource_groups_2r_Eastern-Western_nercr"
        assert result != "resource_groups"

    def test_uses_custom_value(self, cluster_app):
        """A typed name is returned as-is (trimmed)."""
        _setup_derived_state(cluster_app)
        el = _fake_name_element("  My Regions  ")

        with patch.object(cluster_app.document, "getElementById", return_value=el):
            result = cluster_app._get_resource_group_name()

        assert result == "My Regions"

    def test_falls_back_when_element_missing(self, cluster_app):
        """No element → falls back to the derived default without raising."""
        _setup_derived_state(cluster_app)

        with patch.object(cluster_app.document, "getElementById", return_value=None):
            result = cluster_app._get_resource_group_name()

        assert result == HIGH_LEVEL_DEFAULT


# ---------------------------------------------------------------------------
# reset_region_dependent_state() wiring
# ---------------------------------------------------------------------------


class TestResetRegionDependentStateWiring:
    """reset_region_dependent_state() refreshes the Step 7 default name."""

    def test_reset_refreshes_resource_group_name_default(self, cluster_app):
        """Calling the reset with a placeholder value yields the derived default."""
        _setup_derived_state(cluster_app)
        el = _fake_name_element("resource_groups")

        # Suppress the unrelated render helpers that reset_region_dependent_state
        # touches; update_resource_group_name_default stays real so we can verify
        # the new wiring actually refreshes the field.
        with (
            patch.object(cluster_app, "_update_resource_group_list"),
            patch.object(cluster_app, "_render_renewables_preview"),
            patch.object(cluster_app, "render_esr_results"),
            patch.object(cluster_app, "render_plant_candidates"),
            patch.object(cluster_app.document, "getElementById", return_value=el),
        ):
            cluster_app.reset_region_dependent_state()

        assert el.value == HIGH_LEVEL_DEFAULT
        assert cluster_app.state.resource_group_name_default == HIGH_LEVEL_DEFAULT

    def test_reset_preserves_custom_name(self, cluster_app):
        """A custom name is not clobbered by the reset's default refresh."""
        _setup_derived_state(cluster_app)
        el = _fake_name_element("my_custom_name")

        with (
            patch.object(cluster_app, "_update_resource_group_list"),
            patch.object(cluster_app, "_render_renewables_preview"),
            patch.object(cluster_app, "render_esr_results"),
            patch.object(cluster_app, "render_plant_candidates"),
            patch.object(cluster_app.document, "getElementById", return_value=el),
        ):
            cluster_app.reset_region_dependent_state()

        assert el.value == "my_custom_name"
        assert cluster_app.state.resource_group_name_default == HIGH_LEVEL_DEFAULT

    def test_reset_noops_when_element_missing(self, cluster_app):
        """Reset tolerates a missing #resourceGroupName element.

        With no element, update_resource_group_name_default() returns before
        touching state, so the tracked default stays empty.
        """
        _setup_derived_state(cluster_app)

        with (
            patch.object(cluster_app, "_update_resource_group_list"),
            patch.object(cluster_app, "_render_renewables_preview"),
            patch.object(cluster_app, "render_esr_results"),
            patch.object(cluster_app, "render_plant_candidates"),
            patch.object(cluster_app.document, "getElementById", return_value=None),
        ):
            cluster_app.reset_region_dependent_state()

        assert cluster_app.state.resource_group_name_default == ""
