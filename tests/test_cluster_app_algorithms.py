"""
Real-coverage tests for cluster_app.py.

Imports the module directly using the same mock-PyScript technique as
test_state_resets.py so that coverage tools track the actual source file.

Session scope is used for the module fixture to amortize the slow Pyodide
module-load cost across all tests.
"""

import importlib.util
import math
import sys
from pathlib import Path
from unittest.mock import MagicMock

import networkx as nx
import numpy as np
import pandas as pd
import pytest
import yaml

# ---------------------------------------------------------------------------
# Session-scoped fixture: load cluster_app with mocked dependencies
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cluster_app():
    """Load cluster_app module with mocked js/PyScript dependencies."""
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
        if str(web_dir) in sys.path:
            sys.path.remove(str(web_dir))
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


# ---------------------------------------------------------------------------
# 1. Color utilities
# ---------------------------------------------------------------------------


class TestColorUtilities:
    """Tests for hex_to_rgb, rgb_to_hex, lighten_color."""

    def test_hex_to_rgb_red(self, cluster_app):
        assert cluster_app.hex_to_rgb("#FF0000") == (255, 0, 0)

    def test_hex_to_rgb_no_hash(self, cluster_app):
        # hex_to_rgb strips leading '#'
        assert cluster_app.hex_to_rgb("00FF00") == (0, 255, 0)

    def test_hex_to_rgb_blue(self, cluster_app):
        assert cluster_app.hex_to_rgb("#0000FF") == (0, 0, 255)

    def test_rgb_to_hex_roundtrip(self, cluster_app):
        original = "#1a2b3c"
        rgb = cluster_app.hex_to_rgb(original)
        assert cluster_app.rgb_to_hex(rgb) == original

    def test_rgb_to_hex_white(self, cluster_app):
        assert cluster_app.rgb_to_hex((255, 255, 255)) == "#ffffff"

    def test_lighten_black(self, cluster_app):
        result = cluster_app.lighten_color("#000000", factor=0.5)
        r, g, b = cluster_app.hex_to_rgb(result)
        # Each channel should increase from 0 toward 255
        assert r > 0
        assert g > 0
        assert b > 0

    def test_lighten_white_stays_white(self, cluster_app):
        result = cluster_app.lighten_color("#ffffff", factor=0.7)
        assert result == "#ffffff"

    def test_lighten_default_factor(self, cluster_app):
        # Default factor=0.7 should produce a lighter color than the original
        result = cluster_app.lighten_color("#ff0000")
        r, _, _ = cluster_app.hex_to_rgb(result)
        assert r == 255  # red channel already max
        _, g, _ = cluster_app.hex_to_rgb(result)
        assert g > 0  # green channel lifted


# ---------------------------------------------------------------------------
# 2. Graph utilities
# ---------------------------------------------------------------------------


class TestGraphUtilities:
    """Tests for build_transmission_graph and get_regional_groups."""

    @pytest.fixture()
    def tx_df(self):
        return pd.DataFrame(
            {
                "region_from": ["a", "a", "b"],
                "region_to": ["b", "c", "c"],
                "firm_ttc_mw": [100, 200, 300],
            }
        )

    @pytest.fixture()
    def hierarchy_df(self):
        return pd.DataFrame(
            {
                "ba": ["a", "b", "c", "d"],
                "st": ["CA", "CA", "TX", "TX"],
                "nercr": ["WECC", "WECC", "TRE", "TRE"],
            }
        )

    def test_all_nodes_present(self, cluster_app, tx_df):
        G = cluster_app.build_transmission_graph(tx_df, {"a", "b", "c"})
        assert set(G.nodes()) == {"a", "b", "c"}

    def test_parallel_edges_summed(self, cluster_app, tx_df):
        G = cluster_app.build_transmission_graph(tx_df, {"a", "b", "c"})
        assert G["a"]["b"]["weight"] == 100
        assert G["a"]["c"]["weight"] == 200
        assert G["b"]["c"]["weight"] == 300

    def test_invalid_ba_excluded(self, cluster_app, tx_df):
        # BA 'c' not in valid_bas → edges involving 'c' should be missing
        G = cluster_app.build_transmission_graph(tx_df, {"a", "b"})
        assert not G.has_edge("a", "c")
        assert not G.has_edge("b", "c")

    def test_isolated_node_added(self, cluster_app, tx_df):
        # 'z' is in valid_bas but never appears in any edge
        G = cluster_app.build_transmission_graph(tx_df, {"a", "b", "c", "z"})
        assert "z" in G.nodes()
        assert G.degree("z") == 0

    def test_get_regional_groups_basic(self, cluster_app, hierarchy_df):
        groups = cluster_app.get_regional_groups(
            hierarchy_df, "st", {"a", "b", "c", "d"}
        )
        assert groups["CA"] == {"a", "b"}
        assert groups["TX"] == {"c", "d"}

    def test_get_regional_groups_valid_bas_filter(self, cluster_app, hierarchy_df):
        groups = cluster_app.get_regional_groups(hierarchy_df, "st", {"a", "b"})
        assert "TX" not in groups
        assert groups["CA"] == {"a", "b"}

    def test_get_regional_groups_nercr(self, cluster_app, hierarchy_df):
        groups = cluster_app.get_regional_groups(
            hierarchy_df, "nercr", {"a", "b", "c", "d"}
        )
        assert groups["WECC"] == {"a", "b"}
        assert groups["TRE"] == {"c", "d"}


# ---------------------------------------------------------------------------
# 3. Agglomerative clustering
# ---------------------------------------------------------------------------


class TestAgglomerativeCluster:
    """Tests for agglomerative_cluster."""

    @pytest.fixture()
    def chain_graph(self):
        """4-node chain a-b-c-d with increasing weights."""
        G = nx.Graph()
        G.add_edge("a", "b", weight=100)
        G.add_edge("b", "c", weight=200)
        G.add_edge("c", "d", weight=300)
        return G

    def _all_nodes(self, clusters, expected):
        found = set()
        for nodes in clusters.values():
            found.update(nodes)
        assert found == expected

    def _partition(self, clusters):
        """Verify no node appears in more than one cluster."""
        seen = []
        for nodes in clusters.values():
            seen.extend(nodes)
        assert len(seen) == len(set(seen))

    def test_n_clusters_gte_n_nodes_identity(self, cluster_app, chain_graph):
        result = cluster_app.agglomerative_cluster(chain_graph, 10)
        self._all_nodes(result, {"a", "b", "c", "d"})
        assert len(result) == 4

    def test_k1_all_in_one_cluster(self, cluster_app, chain_graph):
        result = cluster_app.agglomerative_cluster(chain_graph, 1, linkage="sum")
        assert len(result) == 1
        self._all_nodes(result, {"a", "b", "c", "d"})

    def test_sum_linkage_k2(self, cluster_app, chain_graph):
        result = cluster_app.agglomerative_cluster(chain_graph, 2, linkage="sum")
        assert len(result) == 2
        self._partition(result)
        self._all_nodes(result, {"a", "b", "c", "d"})

    def test_average_linkage_k2(self, cluster_app, chain_graph):
        result = cluster_app.agglomerative_cluster(chain_graph, 2, linkage="average")
        assert len(result) == 2
        self._partition(result)
        self._all_nodes(result, {"a", "b", "c", "d"})

    def test_max_linkage_k2(self, cluster_app, chain_graph):
        result = cluster_app.agglomerative_cluster(chain_graph, 2, linkage="max")
        assert len(result) == 2
        self._partition(result)
        self._all_nodes(result, {"a", "b", "c", "d"})

    def test_isolated_node_gets_own_cluster(self, cluster_app):
        G = nx.Graph()
        G.add_edge("x", "y", weight=10)
        G.add_node("z")  # isolated
        result = cluster_app.agglomerative_cluster(G, 2)
        # 'z' must appear somewhere
        found = set()
        for nodes in result.values():
            found.update(nodes)
        assert "z" in found

    def test_partition_property(self, cluster_app, chain_graph):
        result = cluster_app.agglomerative_cluster(chain_graph, 3)
        self._partition(result)

    def test_correct_cluster_count(self, cluster_app, chain_graph):
        for k in (1, 2, 3, 4):
            result = cluster_app.agglomerative_cluster(chain_graph, k)
            assert len(result) == k


# ---------------------------------------------------------------------------
# 4. Spectral clustering
# ---------------------------------------------------------------------------


class TestSpectralCluster:
    """Tests for spectral_cluster."""

    def test_n_clusters_gte_n_nodes(self, cluster_app):
        G = nx.path_graph(3)
        result = cluster_app.spectral_cluster(G, 10)
        assert len(result) == 3
        found = set()
        for nodes in result.values():
            found.update(nodes)
        assert found == {0, 1, 2}

    def test_two_components_k2(self, cluster_app):
        """Two disconnected components → each forms its own cluster."""
        G = nx.Graph()
        G.add_edge("a", "b", weight=100)
        G.add_edge("c", "d", weight=100)
        result = cluster_app.spectral_cluster(G, 2)
        assert len(result) == 2
        found = set()
        for nodes in result.values():
            found.update(nodes)
        assert found == {"a", "b", "c", "d"}

    def test_all_nodes_covered(self, cluster_app):
        G = nx.complete_graph(5)
        result = cluster_app.spectral_cluster(G, 3)
        found = set()
        for nodes in result.values():
            found.update(nodes)
        assert found == set(range(5))

    def test_no_node_duplicated(self, cluster_app):
        G = nx.path_graph(6)
        result = cluster_app.spectral_cluster(G, 3)
        all_nodes = []
        for nodes in result.values():
            all_nodes.extend(nodes)
        assert len(all_nodes) == len(set(all_nodes))


# ---------------------------------------------------------------------------
# 5. Louvain clustering
# ---------------------------------------------------------------------------


class TestLouvainCluster:
    """Tests for louvain_cluster."""

    def test_empty_graph_returns_empty(self, cluster_app):
        G = nx.Graph()
        assert cluster_app.louvain_cluster(G) == {}

    def test_no_edges_each_node_own_cluster(self, cluster_app):
        G = nx.Graph()
        G.add_nodes_from(["a", "b", "c"])
        result = cluster_app.louvain_cluster(G)
        found = set()
        for nodes in result.values():
            found.update(nodes)
        assert found == {"a", "b", "c"}
        assert len(result) == 3

    def test_connected_graph_all_nodes_covered(self, cluster_app):
        G = nx.karate_club_graph()
        result = cluster_app.louvain_cluster(G)
        assert len(result) >= 1
        found = set()
        for nodes in result.values():
            found.update(nodes)
        assert found == set(G.nodes())

    def test_two_cliques_separate(self, cluster_app):
        """Two dense cliques connected by a weak bridge → likely two clusters."""
        G = nx.Graph()
        # Clique A
        for u in range(5):
            for v in range(u + 1, 5):
                G.add_edge(u, v, weight=1000)
        # Clique B
        for u in range(5, 10):
            for v in range(u + 1, 10):
                G.add_edge(u, v, weight=1000)
        # Weak bridge
        G.add_edge(4, 5, weight=1)
        result = cluster_app.louvain_cluster(G)
        # All nodes present
        found = set()
        for nodes in result.values():
            found.update(nodes)
        assert found == set(range(10))
        # Expected ≥ 2 clusters with strong intra-cluster edges
        assert len(result) >= 2


# ---------------------------------------------------------------------------
# 6. Hierarchical clustering
# ---------------------------------------------------------------------------


class TestHierarchicalCluster:
    """Tests for hierarchical_cluster."""

    @pytest.fixture()
    def small_data(self):
        hierarchy_df = pd.DataFrame(
            {
                "ba": ["a", "b", "c", "d"],
                "st": ["CA", "CA", "TX", "TX"],
                "nercr": ["WECC", "WECC", "TRE", "TRE"],
            }
        )
        tx_df = pd.DataFrame(
            {
                "region_from": ["a", "b", "c"],
                "region_to": ["b", "c", "d"],
                "firm_ttc_mw": [1000.0, 500.0, 800.0],
            }
        )
        return hierarchy_df, tx_df

    def _all_nodes(self, clusters, expected):
        found = set()
        for nodes in clusters.values():
            found.update(nodes)
        assert found == expected

    def _no_duplicates(self, clusters):
        all_nodes = []
        for nodes in clusters.values():
            all_nodes.extend(nodes)
        assert len(all_nodes) == len(set(all_nodes))

    def test_target_gte_groups_splits_within_groups(self, cluster_app, small_data):
        hierarchy_df, tx_df = small_data
        # 2 groups (WECC, TRE), target=4 → up to 4 clusters, all nodes covered
        result = cluster_app.hierarchical_cluster(
            hierarchy_df, tx_df, {"a", "b", "c", "d"}, "nercr", 4
        )
        self._all_nodes(result, {"a", "b", "c", "d"})
        self._no_duplicates(result)
        assert 1 <= len(result) <= 4

    def test_target_1_merges_all(self, cluster_app, small_data):
        hierarchy_df, tx_df = small_data
        result = cluster_app.hierarchical_cluster(
            hierarchy_df, tx_df, {"a", "b", "c", "d"}, "nercr", 1
        )
        self._all_nodes(result, {"a", "b", "c", "d"})
        assert len(result) == 1

    def test_average_method(self, cluster_app, small_data):
        hierarchy_df, tx_df = small_data
        result = cluster_app.hierarchical_cluster(
            hierarchy_df,
            tx_df,
            {"a", "b", "c", "d"},
            "nercr",
            2,
            method="hierarchical-average",
        )
        self._all_nodes(result, {"a", "b", "c", "d"})
        self._no_duplicates(result)

    def test_max_method(self, cluster_app, small_data):
        hierarchy_df, tx_df = small_data
        result = cluster_app.hierarchical_cluster(
            hierarchy_df,
            tx_df,
            {"a", "b", "c", "d"},
            "nercr",
            2,
            method="hierarchical-max",
        )
        self._all_nodes(result, {"a", "b", "c", "d"})
        self._no_duplicates(result)

    def test_esr_rectable_separates_zones(self, cluster_app, small_data):
        """CA and TX can't trade → clustering should not merge them."""
        hierarchy_df, tx_df = small_data
        states = ["CA", "TX"]
        # Zero trading between CA and TX
        rectable = pd.DataFrame([[1, 0], [0, 1]], index=states, columns=states)
        result = cluster_app.hierarchical_cluster(
            hierarchy_df,
            tx_df,
            {"a", "b", "c", "d"},
            "st",
            2,
            method="hierarchical-sum",
            esr_rectable_df=rectable,
        )
        self._all_nodes(result, {"a", "b", "c", "d"})
        self._no_duplicates(result)
        # CA BAs (a, b) and TX BAs (c, d) should be in separate clusters
        for nodes in result.values():
            ca_in = bool(nodes & {"a", "b"})
            tx_in = bool(nodes & {"c", "d"})
            assert not (
                ca_in and tx_in
            ), "CA and TX BAs should not be in the same cluster"


# ---------------------------------------------------------------------------
# 7. Calculate modularity
# ---------------------------------------------------------------------------


class TestCalculateModularity:
    """Tests for calculate_modularity."""

    def test_no_edges_returns_zero(self, cluster_app):
        G = nx.Graph()
        G.add_nodes_from(["a", "b"])
        clusters = {0: {"a"}, 1: {"b"}}
        assert cluster_app.calculate_modularity(G, clusters) == 0.0

    def test_perfect_partition_positive(self, cluster_app):
        """Two cliques; each is its own cluster → modularity > 0."""
        G = nx.Graph()
        G.add_edge("a", "b", weight=10)
        G.add_edge("c", "d", weight=10)
        clusters = {0: {"a", "b"}, 1: {"c", "d"}}
        mod = cluster_app.calculate_modularity(G, clusters)
        assert mod > 0

    def test_returns_finite_float(self, cluster_app):
        G = nx.karate_club_graph()
        clusters = cluster_app.louvain_cluster(G)
        mod = cluster_app.calculate_modularity(G, clusters)
        assert math.isfinite(mod)


# ---------------------------------------------------------------------------
# 8. Find optimal clusters
# ---------------------------------------------------------------------------


class TestFindOptimalClusters:
    """Tests for find_optimal_clusters."""

    @pytest.fixture()
    def small_data(self):
        hierarchy_df = pd.DataFrame(
            {
                "ba": ["a", "b", "c", "d", "e", "f"],
                "st": ["CA", "CA", "CA", "TX", "TX", "TX"],
                "nercr": ["WECC", "WECC", "WECC", "TRE", "TRE", "TRE"],
            }
        )
        tx_df = pd.DataFrame(
            {
                "region_from": ["a", "b", "c", "d", "e"],
                "region_to": ["b", "c", "d", "e", "f"],
                "firm_ttc_mw": [500.0, 500.0, 200.0, 500.0, 500.0],
            }
        )
        return hierarchy_df, tx_df

    def test_returns_five_tuple(self, cluster_app, small_data):
        hierarchy_df, tx_df = small_data
        result = cluster_app.find_optimal_clusters(
            hierarchy_df, tx_df, {"a", "b", "c", "d", "e", "f"}, "nercr", 1, 4
        )
        assert len(result) == 5

    def test_all_bas_covered(self, cluster_app, small_data):
        hierarchy_df, tx_df = small_data
        clusters, n, _, _, _ = cluster_app.find_optimal_clusters(
            hierarchy_df, tx_df, {"a", "b", "c", "d", "e", "f"}, "nercr", 1, 4
        )
        found = set()
        for nodes in clusters.values():
            found.update(nodes)
        assert found == {"a", "b", "c", "d", "e", "f"}

    def test_cluster_count_within_bounds(self, cluster_app, small_data):
        hierarchy_df, tx_df = small_data
        clusters, n, _, _, _ = cluster_app.find_optimal_clusters(
            hierarchy_df, tx_df, {"a", "b", "c", "d", "e", "f"}, "nercr", 1, 4
        )
        assert 1 <= len(clusters) <= 4
        assert 1 <= n <= 4

    def test_min_equals_max_1(self, cluster_app, small_data):
        hierarchy_df, tx_df = small_data
        clusters, n, _, _, _ = cluster_app.find_optimal_clusters(
            hierarchy_df, tx_df, {"a", "b", "c", "d", "e", "f"}, "nercr", 1, 1
        )
        assert len(clusters) == 1
        assert n == 1


# ---------------------------------------------------------------------------
# 9. Generate cluster names
# ---------------------------------------------------------------------------


class TestGenerateClusterNames:
    """Tests for generate_cluster_names."""

    @pytest.fixture()
    def hierarchy_df(self):
        return pd.DataFrame(
            {
                "ba": ["ca1", "ca2", "tx1"],
                "st": ["CA", "CA", "TX"],
                "cendiv": ["WSC", "WSC", "WSC"],
                "transgrp": ["WECC", "WECC", "SPP"],
                "nercr": ["WECC", "WECC", "TRE"],
                "transreg": ["West", "West", "Central"],
                "interconnect": ["Western", "Western", "Western"],
            }
        )

    def test_single_state_cluster_name_starts_with_state(
        self, cluster_app, hierarchy_df
    ):
        clusters = {0: {"ca1", "ca2"}}
        names = cluster_app.generate_cluster_names(clusters, hierarchy_df)
        assert names[0].startswith("CA")

    def test_all_labels_in_result(self, cluster_app, hierarchy_df):
        clusters = {0: {"ca1", "ca2"}, 1: {"tx1"}}
        names = cluster_app.generate_cluster_names(clusters, hierarchy_df)
        assert 0 in names
        assert 1 in names

    def test_no_duplicate_names(self, cluster_app, hierarchy_df):
        clusters = {0: {"ca1", "ca2"}, 1: {"tx1"}}
        names = cluster_app.generate_cluster_names(clusters, hierarchy_df)
        # Values should be unique
        assert len(set(names.values())) == len(names)

    def test_counter_suffix_on_first_occurrence(self, cluster_app, hierarchy_df):
        clusters = {0: {"ca1", "ca2"}}
        names = cluster_app.generate_cluster_names(clusters, hierarchy_df)
        # First occurrence gets suffix "1"
        assert names[0].endswith("1")


# ---------------------------------------------------------------------------
# 10. ESR policy functions
# ---------------------------------------------------------------------------


class TestESRPolicyFunctions:
    """Tests for ESR trading-zone and policy functions."""

    @pytest.fixture()
    def hierarchy_df(self):
        return pd.DataFrame(
            {
                "ba": ["ca", "nv", "tx", "ok", "co"],
                "st": ["CA", "NV", "TX", "OK", "CO"],
                "interconnect": [
                    "Western",
                    "Western",
                    "ERCOT",
                    "Eastern",
                    "Western",
                ],
            }
        )

    @pytest.fixture()
    def rectable_df(self):
        states = ["CA", "NV", "TX", "OK", "CO"]
        data = [
            [1, 1, 0, 0, 0],
            [1, 1, 0, 0, 0],
            [0, 0, 1, 1, 0],
            [0, 0, 1, 1, 0],
            [0, 0, 0, 0, 1],
        ]
        return pd.DataFrame(data, index=states, columns=states)

    # --- extract_state_for_region ---

    def test_extract_state_for_region_basic(self, cluster_app, hierarchy_df):
        result = cluster_app.extract_state_for_region(["ca", "nv"], hierarchy_df)
        assert result == {"ca": "ca", "nv": "nv"}

    def test_extract_state_for_region_missing_ba_raises(
        self, cluster_app, hierarchy_df
    ):
        with pytest.raises(cluster_app.ESRGenerationError):
            cluster_app.extract_state_for_region(["zzz"], hierarchy_df)

    # --- get_states_in_region ---

    def test_get_states_in_region_unique(self, cluster_app, hierarchy_df):
        result = cluster_app.get_states_in_region(["ca", "nv"], hierarchy_df)
        assert result == {"ca", "nv"}

    def test_get_states_in_region_single(self, cluster_app, hierarchy_df):
        result = cluster_app.get_states_in_region(["tx"], hierarchy_df)
        assert result == {"tx"}

    # --- can_states_trade ---

    def test_value_1_both_modes(self, cluster_app, rectable_df):
        # CA-NV have value=1
        assert cluster_app.can_states_trade("CA", "NV", rectable_df) is True
        assert (
            cluster_app.can_states_trade("CA", "NV", rectable_df, transitive_only=True)
            is True
        )

    def test_value_2_direct_only(self, cluster_app):
        # Build a rectable with value=2 for AZ-CA
        states = ["CA", "AZ"]
        rect = pd.DataFrame([[1, 2], [2, 1]], index=states, columns=states)
        # transitive_only=False → True (any positive)
        assert cluster_app.can_states_trade("CA", "AZ", rect) is True
        # transitive_only=True → False (2 != 1)
        assert (
            cluster_app.can_states_trade("CA", "AZ", rect, transitive_only=True)
            is False
        )

    def test_value_0_false(self, cluster_app, rectable_df):
        assert cluster_app.can_states_trade("CA", "TX", rectable_df) is False

    def test_missing_state_false(self, cluster_app, rectable_df):
        assert cluster_app.can_states_trade("ZZ", "CA", rectable_df) is False

    def test_case_insensitive(self, cluster_app, rectable_df):
        # should work regardless of input case since function uppercases
        assert cluster_app.can_states_trade("ca", "nv", rectable_df) is True

    # --- can_generator_satisfy_policy ---

    def test_can_satisfy_policy_true(self, cluster_app, rectable_df):
        # CA policy, NV generator: rectable.loc["CA", "NV"] = 1
        assert cluster_app.can_generator_satisfy_policy("NV", "CA", rectable_df) is True

    def test_can_satisfy_policy_false_no_trade(self, cluster_app, rectable_df):
        assert (
            cluster_app.can_generator_satisfy_policy("TX", "CA", rectable_df) is False
        )

    def test_can_satisfy_policy_missing_state(self, cluster_app, rectable_df):
        assert (
            cluster_app.can_generator_satisfy_policy("ZZ", "CA", rectable_df) is False
        )

    # --- can_states_trade_transitively ---

    def test_single_state_true(self, cluster_app, rectable_df):
        assert cluster_app.can_states_trade_transitively({"CA"}, rectable_df) is True

    def test_empty_set_true(self, cluster_app, rectable_df):
        assert cluster_app.can_states_trade_transitively(set(), rectable_df) is True

    def test_connected_pair_true(self, cluster_app, rectable_df):
        # CA and NV can trade (value=1)
        assert (
            cluster_app.can_states_trade_transitively({"CA", "NV"}, rectable_df) is True
        )

    def test_disconnected_pair_false(self, cluster_app, rectable_df):
        # CA and TX cannot trade
        assert (
            cluster_app.can_states_trade_transitively({"CA", "TX"}, rectable_df)
            is False
        )

    # --- split_bas_by_trading_zones ---

    def test_split_all_one_zone(self, cluster_app, hierarchy_df, rectable_df):
        # CA and NV are in the same trading zone
        result = cluster_app.split_bas_by_trading_zones(
            {"ca", "nv"}, hierarchy_df, rectable_df
        )
        assert len(result) == 1
        assert result[0] == {"ca", "nv"}

    def test_split_two_zones(self, cluster_app, hierarchy_df, rectable_df):
        # CA+NV vs TX
        result = cluster_app.split_bas_by_trading_zones(
            {"ca", "nv", "tx"}, hierarchy_df, rectable_df
        )
        assert len(result) == 2
        all_bas = set()
        for group in result:
            all_bas.update(group)
        assert all_bas == {"ca", "nv", "tx"}

    def test_split_none_rectable_single_group(self, cluster_app, hierarchy_df):
        result = cluster_app.split_bas_by_trading_zones(
            {"ca", "tx"}, hierarchy_df, None
        )
        assert len(result) == 1

    def test_split_single_ba(self, cluster_app, hierarchy_df, rectable_df):
        result = cluster_app.split_bas_by_trading_zones(
            {"ca"}, hierarchy_df, rectable_df
        )
        assert result == [{"ca"}]

    # --- build_state_to_interconnect_map ---

    def test_state_to_interconnect_basic(self, cluster_app, hierarchy_df):
        result = cluster_app.build_state_to_interconnect_map(hierarchy_df)
        assert result["ca"] == "Western"
        assert result["tx"] == "ERCOT"

    def test_state_to_interconnect_empty(self, cluster_app):
        result = cluster_app.build_state_to_interconnect_map(pd.DataFrame())
        assert result == {}

    def test_state_spanning_two_interconnects_picks_dominant(self, cluster_app):
        # "NV" appears twice in Western, once in Eastern → should be Western
        df = pd.DataFrame(
            {
                "ba": ["nv1", "nv2", "nv3"],
                "st": ["NV", "NV", "NV"],
                "interconnect": ["Western", "Western", "Eastern"],
            }
        )
        result = cluster_app.build_state_to_interconnect_map(df)
        assert result["nv"] == "Western"

    # --- build_state_trading_zones ---

    def test_trading_zones_basic(self, cluster_app, rectable_df):
        states = {"CA", "NV", "TX"}
        zones = cluster_app.build_state_trading_zones(states, rectable_df)
        assert len(zones) == 2

    def test_trading_zones_single_state(self, cluster_app, rectable_df):
        zones = cluster_app.build_state_trading_zones({"CA"}, rectable_df)
        assert len(zones) == 1

    def test_trading_zones_cross_interconnect_blocked(self, cluster_app, rectable_df):
        """States in different interconnects should not merge even if rectable allows."""
        # Add a cross-interconnect entry: CA (Western) and OK (Eastern), value=1
        states = ["CA", "NV", "TX", "OK", "CO", "WA"]
        data = [
            [1, 1, 0, 1, 0, 1],
            [1, 1, 0, 0, 0, 1],
            [0, 0, 1, 1, 0, 0],
            [1, 0, 1, 1, 0, 0],
            [0, 0, 0, 0, 1, 0],
            [1, 1, 0, 0, 0, 1],
        ]
        rect = pd.DataFrame(data, index=states, columns=states)
        state_to_ic = {
            "ca": "Western",
            "ok": "Eastern",
            "wa": "Western",
            "nv": "Western",
            "tx": "ERCOT",
            "co": "Western",
        }
        zones = cluster_app.build_state_trading_zones({"ca", "ok"}, rect, state_to_ic)
        # Should be split because different interconnects
        assert len(zones) == 2

    # --- build_esr_zones ---

    def test_build_esr_zones_returns_tuple(
        self, cluster_app, hierarchy_df, rectable_df
    ):
        region_aggs = {"RegA": ["ca", "nv"], "RegB": ["tx"]}
        result = cluster_app.build_esr_zones(region_aggs, hierarchy_df, rectable_df)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_build_esr_zones_all_states_in_one_zone(
        self, cluster_app, hierarchy_df, rectable_df
    ):
        region_aggs = {"RegA": ["ca", "nv"], "RegB": ["tx"]}
        state_zones, state_to_zone = cluster_app.build_esr_zones(
            region_aggs, hierarchy_df, rectable_df
        )
        # All states should appear in state_to_zone
        all_states_in_zones = set()
        for zone in state_zones:
            all_states_in_zones.update(zone)
        assert "ca" in all_states_in_zones
        assert "nv" in all_states_in_zones
        assert "tx" in all_states_in_zones

    def test_build_esr_zones_state_to_zone_mapping(
        self, cluster_app, hierarchy_df, rectable_df
    ):
        region_aggs = {"RegA": ["ca", "nv"], "RegB": ["tx"]}
        state_zones, state_to_zone = cluster_app.build_esr_zones(
            region_aggs, hierarchy_df, rectable_df
        )
        # CA and NV should be in same zone (they can trade)
        assert state_to_zone["ca"] == state_to_zone["nv"]
        # TX should be in a different zone
        assert state_to_zone["tx"] != state_to_zone["ca"]

    def test_build_esr_zones_count(self, cluster_app, hierarchy_df, rectable_df):
        region_aggs = {"RegA": ["ca", "nv"], "RegB": ["tx"]}
        state_zones, state_to_zone = cluster_app.build_esr_zones(
            region_aggs, hierarchy_df, rectable_df
        )
        assert len(state_zones) == len(set(state_to_zone.values()))

    # --- compute_state_demand_fractions ---

    def test_fractions_sum_to_one(self, cluster_app, hierarchy_df):
        pop_df = pd.DataFrame(
            {
                "region": ["ca", "nv"],
                "st": ["ca", "nv"],
                "total_population": [1000.0, 500.0],
            }
        )
        result = cluster_app.compute_state_demand_fractions(
            ["ca", "nv"], hierarchy_df, pop_df
        )
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_fractions_equal_weight_fallback(self, cluster_app, hierarchy_df):
        # pop_df doesn't have any of the BAs → equal fallback
        pop_df = pd.DataFrame(columns=["region", "st", "total_population"]).astype(
            {"total_population": float}
        )
        result = cluster_app.compute_state_demand_fractions(
            ["ca", "nv"], hierarchy_df, pop_df
        )
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_fractions_single_ba(self, cluster_app, hierarchy_df):
        pop_df = pd.DataFrame(
            {
                "region": ["tx"],
                "st": ["tx"],
                "total_population": [2000.0],
            }
        )
        result = cluster_app.compute_state_demand_fractions(
            ["tx"], hierarchy_df, pop_df
        )
        assert result == {"tx": 1.0}

    # --- get_state_policy_value ---

    @pytest.fixture()
    def rps_df(self):
        return pd.DataFrame(
            {"year": [2030, 2035], "st": ["ca", "ca"], "rps_all": [0.5, 0.7]}
        )

    def test_get_state_policy_rps(self, cluster_app, rps_df):
        val = cluster_app.get_state_policy_value("ca", 2030, "RPS", rps_df)
        assert val == pytest.approx(0.5)

    def test_get_state_policy_missing_row(self, cluster_app, rps_df):
        val = cluster_app.get_state_policy_value("tx", 2030, "RPS", rps_df)
        assert val == 0.0

    def test_get_state_policy_missing_column(self, cluster_app):
        df = pd.DataFrame({"year": [2030], "st": ["ca"], "other_col": [0.3]})
        val = cluster_app.get_state_policy_value("ca", 2030, "RPS", df)
        assert val == 0.0

    def test_get_state_policy_ces(self, cluster_app):
        ces_df = pd.DataFrame({"year": [2030], "st": ["ca"], "Value": [0.6]})
        val = cluster_app.get_state_policy_value("ca", 2030, "CES", ces_df)
        assert val == pytest.approx(0.6)

    # --- aggregate_policy_for_region ---

    def test_aggregate_policy_single_state(self, cluster_app, hierarchy_df, rps_df):
        pop_df = pd.DataFrame(
            {"region": ["ca"], "st": ["ca"], "total_population": [1000.0]}
        )
        val = cluster_app.aggregate_policy_for_region(
            ["ca"], 2030, "RPS", hierarchy_df, pop_df, rps_df
        )
        assert val == pytest.approx(0.5)

    def test_aggregate_policy_multi_state(self, cluster_app, hierarchy_df):
        # CA: 0.5, NV: 0.2, equal weight → 0.35
        rps = pd.DataFrame(
            {
                "year": [2030, 2030],
                "st": ["ca", "nv"],
                "rps_all": [0.5, 0.2],
            }
        )
        pop = pd.DataFrame(
            {
                "region": ["ca", "nv"],
                "st": ["ca", "nv"],
                "total_population": [1000.0, 1000.0],
            }
        )
        val = cluster_app.aggregate_policy_for_region(
            ["ca", "nv"], 2030, "RPS", hierarchy_df, pop, rps
        )
        assert val == pytest.approx(0.35)

    # --- generate_emission_policies_csv ---

    def test_generate_emission_policies_returns_tuple(
        self, cluster_app, hierarchy_df, rectable_df
    ):
        pop_df = pd.DataFrame(
            {
                "region": ["ca", "nv", "tx"],
                "st": ["ca", "nv", "tx"],
                "total_population": [1000.0, 500.0, 800.0],
            }
        )
        rps = pd.DataFrame({"year": [2030], "st": ["ca"], "rps_all": [0.3]})
        ces = pd.DataFrame({"year": [2030], "st": ["ca"], "Value": [0.4]})
        region_aggs = {"RegA": ["ca", "nv"], "RegB": ["tx"]}
        state_zones, state_to_zone = cluster_app.build_esr_zones(
            region_aggs, hierarchy_df, rectable_df
        )
        result = cluster_app.generate_emission_policies_csv(
            region_aggs,
            [2030],
            state_zones,
            state_to_zone,
            hierarchy_df,
            pop_df,
            rps,
            ces,
        )
        assert len(result) == 4  # (df, esr_map, esr_type_map, esr_policy_states)

    def test_generate_emission_policies_df_shape(
        self, cluster_app, hierarchy_df, rectable_df
    ):
        pop_df = pd.DataFrame(
            {
                "region": ["ca", "nv", "tx"],
                "st": ["ca", "nv", "tx"],
                "total_population": [1000.0, 500.0, 800.0],
            }
        )
        rps = pd.DataFrame({"year": [2030], "st": ["ca"], "rps_all": [0.3]})
        ces = pd.DataFrame({"year": [2030], "st": ["ca"], "Value": [0.4]})
        region_aggs = {"RegA": ["ca", "nv"], "RegB": ["tx"]}
        state_zones, state_to_zone = cluster_app.build_esr_zones(
            region_aggs, hierarchy_df, rectable_df
        )
        df, esr_map, esr_type_map, _ = cluster_app.generate_emission_policies_csv(
            region_aggs,
            [2030],
            state_zones,
            state_to_zone,
            hierarchy_df,
            pop_df,
            rps,
            ces,
        )
        # n_regions × n_years
        assert len(df) == 2 * 1
        assert "case_id" in df.columns
        assert "year" in df.columns
        assert "region" in df.columns

    def test_generate_emission_policies_ces_gte_rps(
        self, cluster_app, hierarchy_df, rectable_df
    ):
        pop_df = pd.DataFrame(
            {
                "region": ["ca", "nv"],
                "st": ["ca", "nv"],
                "total_population": [1000.0, 1000.0],
            }
        )
        rps = pd.DataFrame({"year": [2030], "st": ["ca"], "rps_all": [0.5]})
        ces = pd.DataFrame({"year": [2030], "st": ["ca"], "Value": [0.4]})
        region_aggs = {"RegA": ["ca", "nv"]}
        state_zones, state_to_zone = cluster_app.build_esr_zones(
            region_aggs, hierarchy_df, rectable_df
        )
        df, _, _, _ = cluster_app.generate_emission_policies_csv(
            region_aggs,
            [2030],
            state_zones,
            state_to_zone,
            hierarchy_df,
            pop_df,
            rps,
            ces,
        )
        esr_cols = [c for c in df.columns if c.startswith("ESR_")]
        # Identify RPS and CES columns by index (RPS < CES if both exist)
        if len(esr_cols) >= 2:
            rps_col = esr_cols[0]
            ces_col = esr_cols[1]
            for _, row in df.iterrows():
                assert row[ces_col] >= row[rps_col], "CES must be >= RPS for each row"

    def test_esr_map_keys_start_with_esr(self, cluster_app, hierarchy_df, rectable_df):
        pop_df = pd.DataFrame(
            {
                "region": ["ca"],
                "st": ["ca"],
                "total_population": [1000.0],
            }
        )
        rps = pd.DataFrame({"year": [2030], "st": ["ca"], "rps_all": [0.3]})
        ces = pd.DataFrame({"year": [2030], "st": ["ca"], "Value": [0.4]})
        region_aggs = {"RegA": ["ca"]}
        state_zones, state_to_zone = cluster_app.build_esr_zones(
            region_aggs, hierarchy_df, rectable_df
        )
        _, esr_map, _, _ = cluster_app.generate_emission_policies_csv(
            region_aggs,
            [2030],
            state_zones,
            state_to_zone,
            hierarchy_df,
            pop_df,
            rps,
            ces,
        )
        for key in esr_map:
            assert key.startswith("ESR_")


# ---------------------------------------------------------------------------
# 11. Technology normalization
# ---------------------------------------------------------------------------


class TestTechnologyNormalization:
    """Tests for normalize_technology, clone_group_map, apply_default_grouping."""

    def test_steam_coal(self, cluster_app):
        assert (
            cluster_app.normalize_technology("Steam Coal") == "Conventional Steam Coal"
        )

    def test_natural_gas_combined_cycle(self, cluster_app):
        assert (
            cluster_app.normalize_technology("Natural Gas Combined Cycle")
            == "Natural Gas Fired Combined Cycle"
        )

    def test_solar_thermal_omitted_by_default(self, cluster_app):
        # Default omit list includes "solar thermal"
        assert (
            cluster_app.normalize_technology("Solar Thermal with Energy Storage")
            is None
        )

    def test_solar_pv(self, cluster_app):
        assert (
            cluster_app.normalize_technology("Solar Photovoltaic")
            == "Solar Photovoltaic"
        )

    def test_offshore_wind(self, cluster_app):
        assert (
            cluster_app.normalize_technology("Offshore Wind Turbine")
            == "Offshore Wind Turbine"
        )

    def test_onshore_wind(self, cluster_app):
        assert (
            cluster_app.normalize_technology("Onshore Wind Turbine")
            == "Onshore Wind Turbine"
        )

    def test_wind_maps_to_onshore(self, cluster_app):
        assert cluster_app.normalize_technology("Wind") == "Onshore Wind Turbine"

    def test_batteries(self, cluster_app):
        assert cluster_app.normalize_technology("Batteries") == "Batteries"

    def test_energy_storage_maps_to_batteries(self, cluster_app):
        assert cluster_app.normalize_technology("Energy Storage") == "Batteries"

    def test_non_string_returns_none(self, cluster_app):
        assert cluster_app.normalize_technology(123) is None
        assert cluster_app.normalize_technology(None) is None

    def test_custom_omit_tokens_override(self, cluster_app):
        # With empty omit list, "solar thermal" should come through
        result = cluster_app.normalize_technology(
            "Solar Thermal with Energy Storage", omit_tokens=[]
        )
        # The function maps "solar thermal with energy storage" but note the ordering:
        # solar thermal with energy storage → "Solar Thermal with Energy Storage"
        # (as long as "solar thermal" is not in the omit list)
        assert result is not None

    def test_flywheel_omitted(self, cluster_app):
        assert cluster_app.normalize_technology("flywheel storage") is None

    # --- clone_group_map ---

    def test_clone_group_map_new_object(self, cluster_app):
        original = {"G1": {"a", "b"}}
        clone = cluster_app.clone_group_map(original)
        assert clone is not original

    def test_clone_group_map_sets_are_new(self, cluster_app):
        original = {"G1": {"a", "b"}}
        clone = cluster_app.clone_group_map(original)
        clone["G1"].add("c")
        assert "c" not in original["G1"]

    # --- apply_default_grouping ---

    def test_member_of_default_group(self, cluster_app):
        # "Biomass" is in DEFAULT_TECH_GROUPS["Biomass"]
        result = cluster_app.apply_default_grouping("Biomass", enabled=True)
        assert result == "Biomass"

    def test_enabled_false_passthrough(self, cluster_app):
        assert cluster_app.apply_default_grouping("Biomass", enabled=False) == "Biomass"

    def test_unknown_tech_returned_unchanged(self, cluster_app):
        assert cluster_app.apply_default_grouping("Nuclear", enabled=True) == "Nuclear"

    def test_custom_group_map_overrides(self, cluster_app):
        custom = {"MyGroup": {"Geothermal", "Nuclear"}}
        assert (
            cluster_app.apply_default_grouping(
                "Geothermal", enabled=True, group_map=custom
            )
            == "MyGroup"
        )


# ---------------------------------------------------------------------------
# 12. Plant clustering helpers
# ---------------------------------------------------------------------------


class TestPlantClusteringHelpers:
    """Tests for inertia_single_cluster, build_ba_to_model_region_map,
    prepare_plants_dataframe, suggest_plant_clusters."""

    def test_inertia_identical_rows_zero(self, cluster_app):
        features = np.array([[1.0, 2.0], [1.0, 2.0], [1.0, 2.0]])
        assert cluster_app.inertia_single_cluster(features) == pytest.approx(0.0)

    def test_inertia_two_distant_clusters_positive(self, cluster_app):
        features = np.array([[0.0, 0.0], [0.0, 0.0], [100.0, 100.0], [100.0, 100.0]])
        assert cluster_app.inertia_single_cluster(features) > 0

    def test_inertia_with_weights(self, cluster_app):
        features = np.array([[0.0], [10.0]])
        weights_equal = np.array([1.0, 1.0])
        weights_heavy_left = np.array([10.0, 1.0])
        inertia_equal = cluster_app.inertia_single_cluster(features, weights_equal)
        inertia_heavy = cluster_app.inertia_single_cluster(features, weights_heavy_left)
        # Both should be positive; different weights → different inertia
        assert inertia_equal > 0
        assert inertia_heavy > 0
        assert inertia_equal != inertia_heavy

    def test_build_ba_to_model_region_map(self, cluster_app):
        orig_ra = cluster_app.state.region_aggregations
        orig_sb = cluster_app.state.selected_bas
        orig_ab = cluster_app.state.all_bas
        try:
            cluster_app.state.region_aggregations = {
                "RegA": ["ba1", "ba2"],
                "RegB": ["ba3"],
            }
            cluster_app.state.selected_bas = {"ba1", "ba2", "ba3"}
            cluster_app.state.all_bas = {"ba1", "ba2", "ba3"}
            result = cluster_app.build_ba_to_model_region_map()
            assert result == {"ba1": "RegA", "ba2": "RegA", "ba3": "RegB"}
        finally:
            cluster_app.state.region_aggregations = orig_ra
            cluster_app.state.selected_bas = orig_sb
            cluster_app.state.all_bas = orig_ab

    def _setup_plant_state(self, cluster_app):
        """Helper: inject minimal plant-related state."""
        cluster_app.state.plants_df = pd.DataFrame(
            {
                "plant_id": [1, 2, 3],
                "technology": [
                    "Natural Gas Fired Combined Cycle",
                    "Conventional Steam Coal",
                    "Onshore Wind Turbine",
                ],
                "capacity_mw": [500.0, 200.0, 100.0],
                "heat_rate_mmbtu_mwh": [6.5, 10.0, float("nan")],
                "fom_per_mwyr": [20.0, 15.0, 10.0],
            }
        )
        cluster_app.state.plant_region_map = pd.DataFrame(
            {"plant_id": [1, 2, 3], "region": ["ba1", "ba1", "ba2"]}
        )
        cluster_app.state.region_aggregations = {"RegA": ["ba1"], "RegB": ["ba2"]}
        cluster_app.state.selected_bas = {"ba1", "ba2"}
        cluster_app.state.all_bas = {"ba1", "ba2"}

    def _teardown_plant_state(self, cluster_app):
        cluster_app.state.plants_df = None
        cluster_app.state.plant_region_map = None
        cluster_app.state.region_aggregations = None
        cluster_app.state.selected_bas = set()
        cluster_app.state.all_bas = set()

    def test_prepare_plants_dataframe_columns(self, cluster_app):
        self._setup_plant_state(cluster_app)
        try:
            df = cluster_app.prepare_plants_dataframe()
            assert "model_region" in df.columns
            assert "tech_group" in df.columns
            assert "capacity_mw" in df.columns
        finally:
            self._teardown_plant_state(cluster_app)

    def test_prepare_plants_unknown_ba_dropped(self, cluster_app):
        self._setup_plant_state(cluster_app)
        # Add a plant in an unknown BA
        cluster_app.state.plants_df = pd.concat(
            [
                cluster_app.state.plants_df,
                pd.DataFrame(
                    {
                        "plant_id": [99],
                        "technology": ["Nuclear"],
                        "capacity_mw": [1000.0],
                        "heat_rate_mmbtu_mwh": [10.0],
                        "fom_per_mwyr": [30.0],
                    }
                ),
            ],
            ignore_index=True,
        )
        cluster_app.state.plant_region_map = pd.concat(
            [
                cluster_app.state.plant_region_map,
                pd.DataFrame({"plant_id": [99], "region": ["unknown_ba"]}),
            ],
            ignore_index=True,
        )
        try:
            df = cluster_app.prepare_plants_dataframe()
            assert 99 not in df["plant_id"].values
        finally:
            self._teardown_plant_state(cluster_app)

    def test_prepare_plants_coal_tech_group(self, cluster_app):
        self._setup_plant_state(cluster_app)
        try:
            df = cluster_app.prepare_plants_dataframe(group_enabled=False)
            coal_rows = df[df["plant_id"] == 2]
            assert len(coal_rows) == 1
            assert coal_rows.iloc[0]["tech_group"] == "Conventional Steam Coal"
        finally:
            self._teardown_plant_state(cluster_app)

    def test_suggest_plant_clusters_returns_tuple(self, cluster_app):
        self._setup_plant_state(cluster_app)
        try:
            result = cluster_app.suggest_plant_clusters()
            assert len(result) == 3
        finally:
            self._teardown_plant_state(cluster_app)

    def test_suggest_plant_clusters_valid_yaml(self, cluster_app):
        self._setup_plant_state(cluster_app)
        try:
            yaml_str, total_clusters, effective_budget = (
                cluster_app.suggest_plant_clusters()
            )
            parsed = yaml.safe_load(yaml_str)
            assert "num_clusters" in parsed
        finally:
            self._teardown_plant_state(cluster_app)

    def test_suggest_plant_clusters_budget_respected(self, cluster_app):
        self._setup_plant_state(cluster_app)
        try:
            yaml_str, total_clusters, effective_budget = (
                cluster_app.suggest_plant_clusters(budget=200)
            )
            assert total_clusters <= effective_budget
        finally:
            self._teardown_plant_state(cluster_app)

    # -----------------------------------------------------------------------
    # suggest_plant_clusters – state side-effects
    # -----------------------------------------------------------------------

    def test_suggest_plant_clusters_stores_plant_groups(self, cluster_app):
        """state.plant_groups is a non-empty list after suggest_plant_clusters()."""
        self._setup_plant_state(cluster_app)
        try:
            cluster_app.suggest_plant_clusters()
            assert isinstance(cluster_app.state.plant_groups, list)
            assert len(cluster_app.state.plant_groups) > 0
        finally:
            self._teardown_plant_state(cluster_app)

    def test_suggest_plant_clusters_plant_data_field_present(self, cluster_app):
        """Every group in state.plant_groups has a 'plant_data' key that is a list
        of dicts containing 'heat_rate' and 'capacity'."""
        self._setup_plant_state(cluster_app)
        try:
            cluster_app.suggest_plant_clusters()
            for g in cluster_app.state.plant_groups:
                assert "plant_data" in g, f"Missing 'plant_data' in group {g}"
                assert isinstance(g["plant_data"], list)
                for entry in g["plant_data"]:
                    assert "heat_rate" in entry, f"Missing 'heat_rate' in {entry}"
                    assert "capacity" in entry, f"Missing 'capacity' in {entry}"
        finally:
            self._teardown_plant_state(cluster_app)

    def test_suggest_plant_clusters_plant_data_matches_group_n_units(self, cluster_app):
        """len(g['plant_data']) == g['n_units'] for every group."""
        self._setup_plant_state(cluster_app)
        try:
            cluster_app.suggest_plant_clusters()
            for g in cluster_app.state.plant_groups:
                assert len(g["plant_data"]) == g["n_units"], (
                    f"plant_data length {len(g['plant_data'])} != "
                    f"n_units {g['n_units']} for group {g['tech_group']}"
                )
        finally:
            self._teardown_plant_state(cluster_app)

    def test_suggest_plant_clusters_resets_candidate_overrides(self, cluster_app):
        """Pre-existing plant_candidate_overrides are cleared on each call."""
        self._setup_plant_state(cluster_app)
        try:
            cluster_app.state.plant_candidate_overrides = {("X", "Y"): 3}
            cluster_app.suggest_plant_clusters()
            assert cluster_app.state.plant_candidate_overrides == {}
        finally:
            self._teardown_plant_state(cluster_app)

    def test_suggest_plant_clusters_resets_overrides_on_rerun(self, cluster_app):
        """Overrides set between two calls are cleared on the second call."""
        self._setup_plant_state(cluster_app)
        try:
            cluster_app.suggest_plant_clusters()
            cluster_app.state.plant_candidate_overrides = {("RegA", "NGCC"): 5}
            cluster_app.suggest_plant_clusters()
            assert cluster_app.state.plant_candidate_overrides == {}
        finally:
            self._teardown_plant_state(cluster_app)

    # -----------------------------------------------------------------------
    # _candidate_svg
    # -----------------------------------------------------------------------

    def test_candidate_svg_empty_returns_empty_string(self, cluster_app):
        """_candidate_svg with empty plant_data returns ''."""
        assert cluster_app._candidate_svg([], 1) == ""

    def test_candidate_svg_returns_svg_element(self, cluster_app):
        """Two-plant list with k=2 returns an SVG string with circle elements."""
        plant_data = [
            {"heat_rate": 6.5, "capacity": 500.0},
            {"heat_rate": 10.0, "capacity": 300.0},
        ]
        result = cluster_app._candidate_svg(plant_data, 2)
        assert result.startswith("<svg"), f"Expected SVG, got: {result[:50]}"
        assert "<circle" in result

    def test_candidate_svg_single_point(self, cluster_app):
        """A single-element list produces SVG with exactly one <circle element."""
        plant_data = [{"heat_rate": 7.0, "capacity": 400.0}]
        result = cluster_app._candidate_svg(plant_data, 1)
        assert result.count("<circle") == 1

    def test_candidate_svg_k1_all_same_color(self, cluster_app):
        """With k=1 and two different heat rates, all bubbles use only _BUBBLE_COLORS[0]."""
        bubble_colors = cluster_app._BUBBLE_COLORS
        plant_data = [
            {"heat_rate": 6.5, "capacity": 500.0},
            {"heat_rate": 10.0, "capacity": 300.0},
        ]
        result = cluster_app._candidate_svg(plant_data, 1)
        assert bubble_colors[0] in result
        # None of the other bubble colors should appear
        for color in bubble_colors[1:]:
            assert color not in result, f"Unexpected color {color} found with k=1"

    def test_candidate_svg_k_exceeds_n_clamped(self, cluster_app):
        """k larger than n_units is clamped to n; result is still valid SVG."""
        plant_data = [
            {"heat_rate": 6.5, "capacity": 500.0},
            {"heat_rate": 10.0, "capacity": 300.0},
        ]
        result = cluster_app._candidate_svg(plant_data, 10)
        assert result.startswith("<svg")
        assert "<circle" in result

    def test_candidate_svg_identical_heat_rates_all_same_color(self, cluster_app):
        """Two entries with the same heat_rate get label 0 regardless of k."""
        bubble_colors = cluster_app._BUBBLE_COLORS
        plant_data = [
            {"heat_rate": 7.0, "capacity": 200.0},
            {"heat_rate": 7.0, "capacity": 300.0},
        ]
        result = cluster_app._candidate_svg(plant_data, 2)
        assert bubble_colors[0] in result
        assert bubble_colors[1] not in result

    def test_candidate_svg_multicluster_has_multiple_colors(self, cluster_app):
        """4 plants with two clearly separated heat-rate clusters → ≥2 fill colors."""
        bubble_colors = cluster_app._BUBBLE_COLORS
        plant_data = [
            {"heat_rate": 4.0, "capacity": 200.0},
            {"heat_rate": 4.1, "capacity": 210.0},
            {"heat_rate": 12.0, "capacity": 180.0},
            {"heat_rate": 12.1, "capacity": 195.0},
        ]
        result = cluster_app._candidate_svg(plant_data, 2)
        colors_present = [c for c in bubble_colors if c in result]
        assert (
            len(colors_present) >= 2
        ), f"Expected ≥2 distinct colors for 2-cluster SVG, found: {colors_present}"

    def test_candidate_svg_ends_with_svg_close_tag(self, cluster_app):
        """The returned SVG string ends with </svg>."""
        plant_data = [{"heat_rate": 6.5, "capacity": 500.0}]
        result = cluster_app._candidate_svg(plant_data, 1)
        assert result.endswith("</svg>")

    def test_candidate_svg_circles_have_title_tooltips(self, cluster_app):
        """Each circle element has a <title> child with capacity and heat rate."""
        plant_data = [
            {"heat_rate": 6.5, "capacity": 500.0},
            {"heat_rate": 10.0, "capacity": 300.0},
        ]
        result = cluster_app._candidate_svg(plant_data, 2)
        assert result.count("<title>") == 2
        assert "500 MW" in result
        assert "300 MW" in result
        assert "6.50 MMBtu/MWh" in result
        assert "10.00 MMBtu/MWh" in result

    # -----------------------------------------------------------------------
    # regenerate_plant_yaml_with_overrides
    # -----------------------------------------------------------------------

    def test_regenerate_plant_yaml_noop_when_no_groups(self, cluster_app):
        """Returns early without error when state.plant_groups is empty."""
        orig_groups = cluster_app.state.plant_groups
        orig_settings = cluster_app.state.plant_cluster_settings
        try:
            cluster_app.state.plant_groups = []
            cluster_app.state.plant_cluster_settings = {"num_clusters": {"NGCC": 1}}
            # Should not raise
            cluster_app.regenerate_plant_yaml_with_overrides()
            # Settings must be unchanged (early return)
            assert cluster_app.state.plant_cluster_settings == {
                "num_clusters": {"NGCC": 1}
            }
        finally:
            cluster_app.state.plant_groups = orig_groups
            cluster_app.state.plant_cluster_settings = orig_settings

    def test_regenerate_plant_yaml_noop_when_no_settings(self, cluster_app):
        """Returns early without error when state.plant_cluster_settings is None."""
        self._setup_plant_state(cluster_app)
        orig_settings = cluster_app.state.plant_cluster_settings
        try:
            cluster_app.suggest_plant_clusters()
            cluster_app.state.plant_cluster_settings = None
            # Should not raise
            cluster_app.regenerate_plant_yaml_with_overrides()
            assert cluster_app.state.plant_cluster_settings is None
        finally:
            cluster_app.state.plant_cluster_settings = orig_settings
            self._teardown_plant_state(cluster_app)

    def test_regenerate_plant_yaml_no_overrides_preserves_num_clusters(
        self, cluster_app
    ):
        """With no overrides, regenerate preserves the original num_clusters values."""
        self._setup_plant_state(cluster_app)
        orig_settings = cluster_app.state.plant_cluster_settings
        try:
            yaml_str, _total, _budget = cluster_app.suggest_plant_clusters()
            original_parsed = yaml.safe_load(yaml_str)
            cluster_app.state.plant_cluster_settings = original_parsed
            original_num_clusters = dict(original_parsed["num_clusters"])

            cluster_app.regenerate_plant_yaml_with_overrides()

            assert cluster_app.state.plant_cluster_settings is not None
            assert "num_clusters" in cluster_app.state.plant_cluster_settings
            assert (
                cluster_app.state.plant_cluster_settings["num_clusters"]
                == original_num_clusters
            )
        finally:
            cluster_app.state.plant_cluster_settings = orig_settings
            self._teardown_plant_state(cluster_app)

    def test_regenerate_plant_yaml_with_override_updates_settings(self, cluster_app):
        """Applying an override causes plant_cluster_settings to be re-parsed as dict."""
        self._setup_plant_state(cluster_app)
        orig_settings = cluster_app.state.plant_cluster_settings
        orig_overrides = cluster_app.state.plant_candidate_overrides
        try:
            yaml_str, _total, _budget = cluster_app.suggest_plant_clusters()
            cluster_app.state.plant_cluster_settings = yaml.safe_load(yaml_str)

            g = cluster_app.state.plant_groups[0]
            cluster_app.state.plant_candidate_overrides[
                (g["model_region"], g["tech_group"])
            ] = 2

            cluster_app.regenerate_plant_yaml_with_overrides()

            result = cluster_app.state.plant_cluster_settings
            assert isinstance(result, dict), "plant_cluster_settings should be a dict"
            assert "num_clusters" in result
        finally:
            cluster_app.state.plant_cluster_settings = orig_settings
            cluster_app.state.plant_candidate_overrides = orig_overrides
            self._teardown_plant_state(cluster_app)

    def test_regenerate_plant_yaml_override_in_alt_num_clusters(self, cluster_app):
        """An override differing from the tech default appears in alt_num_clusters.

        Uses a custom 2-plant state where both plants share the same tech_group but
        live in different model regions.  Overriding one region to 2 while the other
        stays at 1 keeps the tech default at 1, so the overridden region must appear
        in alt_num_clusters.
        """
        state = cluster_app.state
        # Save everything we'll touch so teardown is safe.
        orig_plants_df = state.plants_df
        orig_plant_region_map = state.plant_region_map
        orig_region_agg = state.region_aggregations
        orig_selected_bas = state.selected_bas
        orig_all_bas = state.all_bas
        orig_settings = state.plant_cluster_settings
        orig_overrides = state.plant_candidate_overrides

        try:
            # Two NGCC plants, one per model region, so we get two groups that share
            # the same tech_group.
            state.plants_df = pd.DataFrame(
                {
                    "plant_id": [1, 2],
                    "technology": [
                        "Natural Gas Fired Combined Cycle",
                        "Natural Gas Fired Combined Cycle",
                    ],
                    "capacity_mw": [500.0, 400.0],
                    "heat_rate_mmbtu_mwh": [6.5, 6.8],
                    "fom_per_mwyr": [20.0, 21.0],
                }
            )
            state.plant_region_map = pd.DataFrame(
                {"plant_id": [1, 2], "region": ["ba1", "ba2"]}
            )
            state.region_aggregations = {"RegA": ["ba1"], "RegB": ["ba2"]}
            state.selected_bas = {"ba1", "ba2"}
            state.all_bas = {"ba1", "ba2"}

            yaml_str, _total, _budget = cluster_app.suggest_plant_clusters()
            state.plant_cluster_settings = yaml.safe_load(yaml_str)

            assert (
                len(state.plant_groups) == 2
            ), f"Expected 2 groups (one per region), got {len(state.plant_groups)}"

            # Force both to num_clusters=1 so the tech default is 1.
            for grp in state.plant_groups:
                grp["num_clusters"] = 1

            # Override the first region's group to 2 — the second stays at 1, so the
            # tech default remains 1 and this region must appear in alt_num_clusters.
            target = state.plant_groups[0]
            state.plant_candidate_overrides[
                (target["model_region"], target["tech_group"])
            ] = 2

            cluster_app.regenerate_plant_yaml_with_overrides()

            result = state.plant_cluster_settings
            assert "alt_num_clusters" in result
            alt = result["alt_num_clusters"]
            region = target["model_region"]
            tech = target["tech_group"]
            assert (
                region in alt
            ), f"Expected region '{region}' in alt_num_clusters, got: {alt}"
            assert alt[region].get(tech) == 2, (
                f"Expected override value 2 for tech '{tech}' in region '{region}', "
                f"got: {alt[region]}"
            )
        finally:
            state.plants_df = orig_plants_df
            state.plant_region_map = orig_plant_region_map
            state.region_aggregations = orig_region_agg
            state.selected_bas = orig_selected_bas
            state.all_bas = orig_all_bas
            state.plant_cluster_settings = orig_settings
            state.plant_candidate_overrides = orig_overrides


# ---------------------------------------------------------------------------
# 13. Settings helpers
# ---------------------------------------------------------------------------


class TestSettingsHelpers:
    """Tests for parse_int_list, parse_new_resources_text, build_fuel_scenario_index,
    _prefix_from_new_technology, _default_scenario_for_fuel, _safe_float,
    _format_number_short, _extract_cluster_* helpers."""

    # --- parse_int_list ---

    def test_parse_int_list_comma_separated(self, cluster_app):
        assert cluster_app.parse_int_list("2030, 2035, 2040") == [2030, 2035, 2040]

    def test_parse_int_list_space_separated(self, cluster_app):
        assert cluster_app.parse_int_list("2030 2035") == [2030, 2035]

    def test_parse_int_list_empty_string(self, cluster_app):
        assert cluster_app.parse_int_list("") == []

    def test_parse_int_list_none(self, cluster_app):
        assert cluster_app.parse_int_list(None) == []

    def test_parse_int_list_invalid_raises(self, cluster_app):
        with pytest.raises((ValueError, Exception)):
            cluster_app.parse_int_list("2030,abc")

    # --- parse_new_resources_text ---

    def test_parse_new_resources_valid_line(self, cluster_app):
        result = cluster_app.parse_new_resources_text(
            "NaturalGas | CCS | moderate | 500"
        )
        assert result == [["NaturalGas", "CCS", "moderate", 500]]

    def test_parse_new_resources_float_size_truncated(self, cluster_app):
        result = cluster_app.parse_new_resources_text(
            "NaturalGas | CCS | moderate | 500.7"
        )
        assert result == [["NaturalGas", "CCS", "moderate", 500]]

    def test_parse_new_resources_comment_skipped(self, cluster_app):
        result = cluster_app.parse_new_resources_text("# this is a comment")
        assert result == []

    def test_parse_new_resources_blank_lines_skipped(self, cluster_app):
        result = cluster_app.parse_new_resources_text("\n\n  \n")
        assert result == []

    def test_parse_new_resources_fewer_parts_skipped(self, cluster_app):
        result = cluster_app.parse_new_resources_text("NaturalGas | CCS | moderate")
        assert result == []

    def test_parse_new_resources_empty_field_skipped(self, cluster_app):
        result = cluster_app.parse_new_resources_text("NaturalGas |  | moderate | 500")
        assert result == []

    # --- build_fuel_scenario_index ---

    def test_build_fuel_scenario_index_normal(self, cluster_app):
        df = pd.DataFrame(
            {
                "data_year": [2025, 2025, 2025],
                "fuel": ["gas", "gas", "coal"],
                "scenario": ["reference", "high", "reference"],
            }
        )
        idx = cluster_app.build_fuel_scenario_index(df)
        assert 2025 in idx
        assert "gas" in idx[2025]
        assert sorted(idx[2025]["gas"]) == ["high", "reference"]
        assert "coal" in idx[2025]

    def test_build_fuel_scenario_index_empty(self, cluster_app):
        df = pd.DataFrame(columns=["data_year", "fuel", "scenario"])
        idx = cluster_app.build_fuel_scenario_index(df)
        assert idx == {}

    def test_build_fuel_scenario_index_non_int_year_skipped(self, cluster_app):
        df = pd.DataFrame(
            {
                "data_year": ["bad_year", 2025],
                "fuel": ["gas", "gas"],
                "scenario": ["reference", "reference"],
            }
        )
        idx = cluster_app.build_fuel_scenario_index(df)
        assert "bad_year" not in idx

    def test_build_fuel_scenario_index_sorted(self, cluster_app):
        df = pd.DataFrame(
            {
                "data_year": [2025, 2025],
                "fuel": ["gas", "gas"],
                "scenario": ["z_scenario", "a_scenario"],
            }
        )
        idx = cluster_app.build_fuel_scenario_index(df)
        assert idx[2025]["gas"] == ["a_scenario", "z_scenario"]

    # --- _prefix_from_new_technology ---

    def test_prefix_basic(self, cluster_app):
        assert cluster_app._prefix_from_new_technology("HydrogenCT") == "HydrogenCT_"

    def test_prefix_empty_string(self, cluster_app):
        assert cluster_app._prefix_from_new_technology("") == ""

    def test_prefix_already_ends_with_underscore(self, cluster_app):
        result = cluster_app._prefix_from_new_technology("MyTech_")
        assert result == "MyTech_"
        assert not result.endswith("__")

    # --- _default_scenario_for_fuel ---

    def test_default_coal_no_111d(self, cluster_app):
        assert (
            cluster_app._default_scenario_for_fuel("coal", ["no_111d", "reference"])
            == "no_111d"
        )

    def test_default_gas_reference(self, cluster_app):
        assert (
            cluster_app._default_scenario_for_fuel("gas", ["high", "reference", "low"])
            == "reference"
        )

    def test_default_gas_no_reference(self, cluster_app):
        assert cluster_app._default_scenario_for_fuel("gas", ["high", "low"]) == "high"

    def test_default_empty_scenarios_none(self, cluster_app):
        assert cluster_app._default_scenario_for_fuel("gas", []) is None

    # --- _safe_float ---

    def test_safe_float_int(self, cluster_app):
        assert cluster_app._safe_float(1.5) == pytest.approx(1.5)

    def test_safe_float_string(self, cluster_app):
        assert cluster_app._safe_float("3.14") == pytest.approx(3.14)

    def test_safe_float_bad_string(self, cluster_app):
        assert cluster_app._safe_float("bad", default=99.0) == 99.0

    def test_safe_float_none(self, cluster_app):
        assert cluster_app._safe_float(None, default=0.0) == 0.0

    def test_safe_float_inf_returns_default(self, cluster_app):
        assert cluster_app._safe_float(float("inf"), default=-1.0) == -1.0

    def test_safe_float_nan_returns_default(self, cluster_app):
        assert cluster_app._safe_float(float("nan"), default=-1.0) == -1.0

    # --- _format_number_short ---

    def test_format_500(self, cluster_app):
        assert cluster_app._format_number_short(500) == "500"

    def test_format_1500(self, cluster_app):
        assert cluster_app._format_number_short(1500) == "1.5k"

    def test_format_2_million(self, cluster_app):
        assert cluster_app._format_number_short(2_000_000) == "2.0M"

    def test_format_zero(self, cluster_app):
        assert cluster_app._format_number_short(0) == "0"

    def test_format_negative_1500(self, cluster_app):
        assert cluster_app._format_number_short(-1500) == "-1.5k"

    # --- _extract_cluster_lcoe_max ---

    def test_extract_lcoe_max_found(self, cluster_app):
        item = {"filter": [{"feature": "lcoe", "max": 70}]}
        assert cluster_app._extract_cluster_lcoe_max(item) == pytest.approx(70.0)

    def test_extract_lcoe_max_empty_filter(self, cluster_app):
        assert cluster_app._extract_cluster_lcoe_max({"filter": []}) is None

    def test_extract_lcoe_max_not_dict(self, cluster_app):
        assert cluster_app._extract_cluster_lcoe_max("not_a_dict") is None

    def test_extract_lcoe_max_different_feature(self, cluster_app):
        item = {"filter": [{"feature": "cf", "max": 0.5}]}
        assert cluster_app._extract_cluster_lcoe_max(item) is None

    # --- _extract_cluster_q ---

    def test_extract_q_found(self, cluster_app):
        assert cluster_app._extract_cluster_q({"bin": [{"q": 5}]}) == 5

    def test_extract_q_missing_returns_1(self, cluster_app):
        assert cluster_app._extract_cluster_q({}) == 1

    def test_extract_q_not_dict_returns_1(self, cluster_app):
        assert cluster_app._extract_cluster_q("nope") == 1

    # --- _extract_cluster_feature ---

    def test_extract_feature_found(self, cluster_app):
        assert (
            cluster_app._extract_cluster_feature({"cluster": [{"feature": "cf"}]})
            == "cf"
        )

    def test_extract_feature_default_lcoe(self, cluster_app):
        assert cluster_app._extract_cluster_feature({}) == "lcoe"

    # --- _extract_cluster_n_clusters ---

    def test_extract_n_clusters_found(self, cluster_app):
        assert (
            cluster_app._extract_cluster_n_clusters({"cluster": [{"n_clusters": 3}]})
            == 3
        )

    def test_extract_n_clusters_default_1(self, cluster_app):
        assert cluster_app._extract_cluster_n_clusters({}) == 1


# ---------------------------------------------------------------------------
# 14. Supply curve helpers
# ---------------------------------------------------------------------------


class TestSupplyCurveHelpers:
    """Tests for get_line_weight, _build_individual_supply_curve_bars,
    _assign_weighted_bins, _compute_suggested_budget."""

    def test_get_line_weight_min_cap(self, cluster_app):
        # At min_cap (100 MW) → min_weight (1.0)
        assert cluster_app.get_line_weight(100) == pytest.approx(1.0)

    def test_get_line_weight_max_cap(self, cluster_app):
        # At max_cap (12000 MW) → max_weight (8.0)
        assert cluster_app.get_line_weight(12000) == pytest.approx(8.0)

    def test_get_line_weight_below_min_clamped(self, cluster_app):
        assert cluster_app.get_line_weight(0) == pytest.approx(1.0)

    def test_get_line_weight_above_max_clamped(self, cluster_app):
        assert cluster_app.get_line_weight(999999) == pytest.approx(8.0)

    def test_get_line_weight_midpoint(self, cluster_app):
        mid = (100 + 12000) / 2
        weight = cluster_app.get_line_weight(mid)
        assert 4.0 < weight < 5.0

    # --- _build_individual_supply_curve_bars ---

    def test_build_bars_skips_zero_capacity(self, cluster_app):
        region_df = pd.DataFrame(
            {
                "capacity_mw": [100.0, 200.0, 0.0],
                "lcoe": [30.0, 40.0, 25.0],
            }
        )
        bars = cluster_app._build_individual_supply_curve_bars(region_df)
        assert len(bars) == 2

    def test_build_bars_keys(self, cluster_app):
        region_df = pd.DataFrame({"capacity_mw": [100.0, 200.0], "lcoe": [30.0, 40.0]})
        bars = cluster_app._build_individual_supply_curve_bars(region_df)
        for bar in bars:
            assert "label" in bar
            assert "capacity_mw" in bar
            assert "lcoe" in bar

    def test_build_bars_capacity_values(self, cluster_app):
        region_df = pd.DataFrame({"capacity_mw": [100.0, 200.0], "lcoe": [30.0, 40.0]})
        bars = cluster_app._build_individual_supply_curve_bars(region_df)
        caps = [b["capacity_mw"] for b in bars]
        assert 100.0 in caps
        assert 200.0 in caps

    # --- _assign_weighted_bins ---

    def test_assign_bins_q1_all_zeros(self, cluster_app):
        df = pd.DataFrame({"lcoe": list(range(10)), "capacity_mw": [100] * 10})
        bins = cluster_app._assign_weighted_bins(df, "lcoe", 1)
        assert (bins == 0).all()

    def test_assign_bins_q5_range(self, cluster_app):
        df = pd.DataFrame({"lcoe": list(range(10)), "capacity_mw": [100] * 10})
        bins = cluster_app._assign_weighted_bins(df, "lcoe", 5)
        assert len(bins) == 10
        assert bins.min() >= 0
        assert bins.max() < 5

    def test_assign_bins_empty_df(self, cluster_app):
        df = pd.DataFrame(columns=["lcoe", "capacity_mw"])
        bins = cluster_app._assign_weighted_bins(df, "lcoe", 5)
        assert len(bins) == 0

    def test_assign_bins_non_negative(self, cluster_app):
        df = pd.DataFrame({"lcoe": [10, 20, 30, 40, 50], "capacity_mw": [1] * 5})
        bins = cluster_app._assign_weighted_bins(df, "lcoe", 3)
        assert (bins >= 0).all()

    # --- _compute_suggested_budget ---

    def test_compute_suggested_budget_basic(self, cluster_app):
        region_data = {
            "RegA": {
                "lcoe": np.array([30.0, 40.0, 50.0]),
                "capacity_mw": np.array([1000.0, 1000.0, 1000.0]),
                "cum_mwh": np.array([8_760_000.0, 17_520_000.0, 26_280_000.0]),
            }
        }
        region_targets = {"RegA": 10_000_000}
        result = cluster_app._compute_suggested_budget(
            region_data, region_targets, 2000
        )
        assert isinstance(result, int)
        assert result >= 0

    def test_compute_suggested_budget_target_zero(self, cluster_app):
        region_data = {
            "RegA": {
                "lcoe": np.array([30.0]),
                "capacity_mw": np.array([1000.0]),
                "cum_mwh": np.array([8_760_000.0]),
            }
        }
        result = cluster_app._compute_suggested_budget(
            region_data, {"RegA": 0}, avg_resource_mw=2000
        )
        assert result == 0

    def test_compute_suggested_budget_target_exceeds_all(self, cluster_app):
        region_data = {
            "RegA": {
                "lcoe": np.array([30.0]),
                "capacity_mw": np.array([1000.0]),
                "cum_mwh": np.array([8_760_000.0]),
            }
        }
        # Target >> all available cum_mwh → take all available capacity (1000 MW)
        result = cluster_app._compute_suggested_budget(
            region_data, {"RegA": 999_999_999_999}, avg_resource_mw=2000
        )
        # ceil(1000 / 2000) = 1
        assert result == 1


# ---------------------------------------------------------------------------
# Helpers shared by the two new test classes
# ---------------------------------------------------------------------------


def _all_nodes(clusters, expected):
    found = set()
    for nodes in clusters.values():
        found.update(nodes)
    assert found == expected


def _no_duplicates(clusters):
    all_nodes = []
    for nodes in clusters.values():
        all_nodes.extend(nodes)
    assert len(all_nodes) == len(set(all_nodes))


# ---------------------------------------------------------------------------
# Minimal data shared by TestHierarchicalClusterExtended
# ---------------------------------------------------------------------------

_HC_HIERARCHY = pd.DataFrame(
    {
        "ba": ["a", "b", "c", "d", "e", "f"],
        "st": ["CA", "CA", "NV", "TX", "TX", "OK"],
        "nercr": ["WECC", "WECC", "WECC", "TRE", "TRE", "SPP"],
        "cendiv": ["PAC", "PAC", "MTN", "WSC", "WSC", "WSC"],
    }
)

_HC_TX = pd.DataFrame(
    {
        "region_from": ["a", "b", "c", "d", "e"],
        "region_to": ["b", "c", "d", "e", "f"],
        "firm_ttc_mw": [1000.0, 800.0, 200.0, 900.0, 700.0],
    }
)

_HC_STATES = ["CA", "NV", "TX", "OK"]
_HC_RECTABLE = pd.DataFrame(
    [[1, 1, 0, 0], [1, 1, 0, 0], [0, 0, 1, 1], [0, 0, 1, 1]],
    index=_HC_STATES,
    columns=_HC_STATES,
)

# Mixed-group hierarchy: CA and TX BAs forced into the same group value
_HC_MIXED_HIERARCHY = pd.DataFrame(
    {
        "ba": ["a", "b", "c", "d"],
        "st": ["CA", "TX", "CA", "TX"],
        "mixed_group": ["G1", "G1", "G2", "G2"],
    }
)

_HC_MIXED_TX = pd.DataFrame(
    {
        "region_from": ["a", "c"],
        "region_to": ["b", "d"],
        "firm_ttc_mw": [500.0, 500.0],
    }
)

_HC_NO_TRADE = pd.DataFrame([[1, 0], [0, 1]], index=["CA", "TX"], columns=["CA", "TX"])


# ---------------------------------------------------------------------------
# Minimal data shared by TestRunClustering
# ---------------------------------------------------------------------------

_RC_HIER = pd.DataFrame(
    {
        "ba": ["a", "b", "c", "d", "e"],
        "st": ["CA", "CA", "NV", "TX", "TX"],
        "nercr": ["WECC", "WECC", "WECC", "TRE", "TRE"],
        "cendiv": ["PAC", "PAC", "MTN", "WSC", "WSC"],
        "transgrp": ["WEC", "WEC", "WEC", "TexA", "TexA"],
        "transreg": ["W", "W", "W", "S", "S"],
        "interconnect": ["Western", "Western", "Western", "Eastern", "Eastern"],
    }
)

_RC_TX = pd.DataFrame(
    {
        "region_from": ["a", "b", "c", "d"],
        "region_to": ["b", "c", "d", "e"],
        "firm_ttc_mw": [1000.0, 800.0, 200.0, 900.0],
    }
)

_RC_RECTABLE = pd.DataFrame(
    [[1, 1, 0], [1, 1, 0], [0, 0, 1]],
    index=["CA", "NV", "TX"],
    columns=["CA", "NV", "TX"],
)


# ---------------------------------------------------------------------------
# TestHierarchicalClusterExtended
# ---------------------------------------------------------------------------


class TestHierarchicalClusterExtended:
    """Covers previously-untested code paths in hierarchical_cluster()."""

    def test_spectral_method_parses_correctly(self, cluster_app):
        """method='spectral' sets algo='spectral', target >= groups → spectral per-group path."""
        cluster_bas = {"a", "b", "c", "d", "e", "f"}
        # 3 groups in nercr: WECC, TRE, SPP → target=3 (>= num_groups) triggers per-group spectral
        clusters = cluster_app.hierarchical_cluster(
            _HC_HIERARCHY,
            _HC_TX,
            cluster_bas,
            grouping_column="nercr",
            target_regions=3,
            method="spectral",
        )
        _all_nodes(clusters, cluster_bas)
        _no_duplicates(clusters)

    def test_default_fallback_method(self, cluster_app):
        """An unknown method string hits the else branch → treated as hierarchical-sum."""
        cluster_bas = {"a", "b", "c", "d", "e", "f"}
        clusters = cluster_app.hierarchical_cluster(
            _HC_HIERARCHY,
            _HC_TX,
            cluster_bas,
            grouping_column="nercr",
            target_regions=2,
            method="something-else",
        )
        _all_nodes(clusters, cluster_bas)
        _no_duplicates(clusters)

    def test_esr_splits_group_with_non_trading_states(self, cluster_app):
        """ESR group splitting: a group containing CA and TX BAs is split into 2 subgroups."""
        cluster_bas = {"a", "b", "c", "d"}
        # grouping_column='mixed_group' — G1 has CA(a) and TX(b), which can't trade
        clusters = cluster_app.hierarchical_cluster(
            _HC_MIXED_HIERARCHY,
            _HC_MIXED_TX,
            cluster_bas,
            grouping_column="mixed_group",
            target_regions=4,  # >= groups after splitting → cluster within each subgroup
            method="hierarchical-sum",
            esr_rectable_df=_HC_NO_TRADE,
        )
        _all_nodes(clusters, cluster_bas)
        _no_duplicates(clusters)

    def test_spectral_when_target_gte_groups(self, cluster_app):
        """Full spectral per-group path: target (6) >= num_groups (3)."""
        cluster_bas = {"a", "b", "c", "d", "e", "f"}
        clusters = cluster_app.hierarchical_cluster(
            _HC_HIERARCHY,
            _HC_TX,
            cluster_bas,
            grouping_column="nercr",
            target_regions=6,  # >= 3 groups
            method="spectral",
        )
        _all_nodes(clusters, cluster_bas)
        _no_duplicates(clusters)

    def test_esr_removes_cross_state_edges_within_group(self, cluster_app):
        """ESR edge removal: cross-state edges inside a group are dropped.

        WECC group contains CA (a,b) and NV (c) — they CAN trade, so no split.
        TRE group has only TX (d,e).
        With esr_rectable_df set so WECC-TRE states can't trade, cross-group edges
        get skipped in the edge-removal loop for the intra-group ESR check.
        """
        cluster_bas = {"a", "b", "c", "d", "e"}
        # Use a 3-BA hierarchy where two BAs in the same cendiv but different
        # non-trading states so the ESR removal code is exercised.
        hierarchy = pd.DataFrame(
            {
                "ba": ["a", "b", "c", "d", "e"],
                "st": ["CA", "TX", "CA", "TX", "CA"],
                "cendiv": ["X", "X", "X", "Y", "Y"],
            }
        )
        tx = pd.DataFrame(
            {
                "region_from": ["a", "b", "c", "d"],
                "region_to": ["b", "c", "d", "e"],
                "firm_ttc_mw": [800.0, 700.0, 300.0, 600.0],
            }
        )
        rectable = pd.DataFrame(
            [[1, 0], [0, 1]], index=["CA", "TX"], columns=["CA", "TX"]
        )
        # target >= 2 groups (X and Y) → intra-group clustering path with ESR edge removal
        clusters = cluster_app.hierarchical_cluster(
            hierarchy,
            tx,
            cluster_bas,
            grouping_column="cendiv",
            target_regions=3,
            method="hierarchical-sum",
            esr_rectable_df=rectable,
        )
        _all_nodes(clusters, cluster_bas)
        _no_duplicates(clusters)

    def test_merge_groups_when_target_lt_num_groups(self, cluster_app):
        """target=1 forces all 3 nercr groups to merge into a single cluster."""
        cluster_bas = {"a", "b", "c", "d", "e", "f"}
        clusters = cluster_app.hierarchical_cluster(
            _HC_HIERARCHY,
            _HC_TX,
            cluster_bas,
            grouping_column="nercr",
            target_regions=1,
        )
        _all_nodes(clusters, cluster_bas)
        _no_duplicates(clusters)
        assert len(clusters) == 1

    def test_merge_groups_esr_skips_nontrading_edges(self, cluster_app):
        """Merge path: ESR causes inter-group edges between non-trading states to be skipped.

        WECC contains CA/NV BAs; TRE contains TX BAs; SPP contains OK BAs.
        _HC_RECTABLE has CA/NV↔TX edges = 0, so the only WECC-TRE inter-group edge
        (c→d, NV→TX) is skipped by the ESR 'continue' branch.  WECC becomes isolated;
        TRE+SPP still share a TX↔OK edge (value=1) and can merge.
        With target=1 the function still produces the best possible merge (2 clusters),
        and all nodes must be covered with no duplicates.
        """
        cluster_bas = {"a", "b", "c", "d", "e", "f"}
        clusters = cluster_app.hierarchical_cluster(
            _HC_HIERARCHY,
            _HC_TX,
            cluster_bas,
            grouping_column="nercr",
            target_regions=1,
            method="hierarchical-sum",
            esr_rectable_df=_HC_RECTABLE,
        )
        _all_nodes(clusters, cluster_bas)
        _no_duplicates(clusters)
        # ESR blocks the WECC-TRE edge → WECC can't merge, so we get 2 clusters
        assert len(clusters) == 2

    def test_merge_spectral_when_target_lt_groups(self, cluster_app):
        """Merge path with spectral algo: target < num_groups uses spectral on group graph."""
        cluster_bas = {"a", "b", "c", "d", "e", "f"}
        clusters = cluster_app.hierarchical_cluster(
            _HC_HIERARCHY,
            _HC_TX,
            cluster_bas,
            grouping_column="nercr",
            target_regions=2,
            method="spectral",
        )
        _all_nodes(clusters, cluster_bas)
        _no_duplicates(clusters)


# ---------------------------------------------------------------------------
# TestRunClustering
# ---------------------------------------------------------------------------


class TestRunClustering:
    """Covers previously-untested code paths in run_clustering()."""

    def _set_state(self, cluster_app, hier=None, tx=None, rect=None):
        """Return (orig_hier, orig_tx, orig_rect) after setting new values."""
        orig = (
            cluster_app.state.hierarchy_df,
            cluster_app.state.transmission_df,
            cluster_app.state.rectable_df,
        )
        if hier is not None:
            cluster_app.state.hierarchy_df = hier
        if tx is not None:
            cluster_app.state.transmission_df = tx
        if rect is not None:
            cluster_app.state.rectable_df = rect
        return orig

    def _restore_state(self, cluster_app, orig):
        cluster_app.state.hierarchy_df = orig[0]
        cluster_app.state.transmission_df = orig[1]
        cluster_app.state.rectable_df = orig[2]

    def test_empty_selected_bas_returns_error(self, cluster_app):
        """BAs not in hierarchy → returns error string."""
        orig = self._set_state(cluster_app, hier=_RC_HIER.copy(), tx=_RC_TX.copy())
        try:
            result = cluster_app.run_clustering(
                {"z1", "z2"},  # not in hierarchy
                grouping_column="nercr",
                target_regions=2,
                no_cluster_groups=None,
            )
            # run_clustering returns 4-tuple
            _, _, err, _ = result
            assert err is not None
            assert isinstance(err, str)
        finally:
            self._restore_state(cluster_app, orig)

    def test_no_cluster_groups_separates_bas(self, cluster_app):
        """no_cluster_groups removes matched BAs from clustering pool."""
        orig = self._set_state(
            cluster_app,
            hier=_RC_HIER.copy(),
            tx=_RC_TX.copy(),
            rect=_RC_RECTABLE.copy(),
        )
        try:
            regions, agg, err, info = cluster_app.run_clustering(
                {"a", "b", "c", "d", "e"},
                grouping_column="nercr",
                target_regions=3,
                no_cluster_groups=["WECC"],
            )
            assert err is None
            # All BAs should appear somewhere
            all_bas = {ba for bas in agg.values() for ba in bas}
            assert all_bas == {"a", "b", "c", "d", "e"}
        finally:
            self._restore_state(cluster_app, orig)

    def test_single_clusterable_ba_fallback(self, cluster_app):
        """If cluster_bas < 2 after no_cluster_groups, region names derive from state."""
        orig = self._set_state(
            cluster_app,
            hier=_RC_HIER.copy(),
            tx=_RC_TX.copy(),
            rect=_RC_RECTABLE.copy(),
        )
        try:
            # Exclude all TRE BAs (d,e) and all but one WECC BA → cluster_bas = {c}
            regions, agg, err, info = cluster_app.run_clustering(
                {"a", "b", "c", "d", "e"},
                grouping_column="nercr",
                target_regions=5,
                no_cluster_groups=["WECC", "TRE"],
            )
            assert err is None
            all_bas = {ba for bas in agg.values() for ba in bas}
            assert all_bas == {"a", "b", "c", "d", "e"}
            # Region names should be state-based (e.g. "CA1", "TX1")
            for name in regions:
                assert any(c.isalpha() for c in name)
        finally:
            self._restore_state(cluster_app, orig)

    def test_single_ba_not_in_hierarchy(self, cluster_app):
        """Fallback naming when BA not found in hierarchy uses BA id as name."""
        # Build a hierarchy with only one BA, plus an extra BA not in hierarchy
        small_hier = pd.DataFrame(
            {
                "ba": ["a"],
                "st": ["CA"],
                "nercr": ["WECC"],
                "cendiv": ["PAC"],
                "transgrp": ["WEC"],
                "transreg": ["W"],
                "interconnect": ["Western"],
            }
        )
        orig = self._set_state(
            cluster_app,
            hier=small_hier.copy(),
            tx=_RC_TX.copy(),
            rect=_RC_RECTABLE.copy(),
        )
        try:
            # selected_bas includes "a" (in hierarchy) and "z" (not in hierarchy)
            # Both will be selected but hierarchy filter retains only "a"
            # After filtering, cluster_bas = {"z"} which is 0 due to hierarchy length being 1
            # Actually the hierarchy filters to just "a" for hierarchy. "z" is absent.
            # The run_clustering code: hierarchy = state.hierarchy_df[ba.isin(selected_bas)]
            # → only row for "a". no_cluster_groups="WECC" excludes "a" from cluster_bas.
            # cluster_bas = {} but the selected_bas also has "z" which the loop iterates.
            regions, agg, err, info = cluster_app.run_clustering(
                {"a"},
                grouping_column="nercr",
                target_regions=2,
                no_cluster_groups=["WECC"],  # excludes "a" → cluster_bas is empty < 2
            )
            assert err is None
            # "a" gets a state-based name since it's in hierarchy
            all_bas = {ba for bas in agg.values() for ba in bas}
            assert "a" in all_bas
        finally:
            self._restore_state(cluster_app, orig)

    def test_esr_compatible_with_trading_zones(self, cluster_app):
        """esr_compatible=True records trading_zone_splits in info when zones differ."""
        orig = self._set_state(
            cluster_app,
            hier=_RC_HIER.copy(),
            tx=_RC_TX.copy(),
            rect=_RC_RECTABLE.copy(),
        )
        try:
            regions, agg, err, info = cluster_app.run_clustering(
                {"a", "b", "c", "d", "e"},
                grouping_column="nercr",
                target_regions=2,
                no_cluster_groups=None,
                esr_compatible=True,
            )
            assert err is None
            # With CA/NV in WECC and TX in TRE, the rectable shows they can't trade →
            # split into 2 trading zones → info["trading_zone_splits"] == 2
            assert "trading_zone_splits" in info
            assert info["trading_zone_splits"] == 2
        finally:
            self._restore_state(cluster_app, orig)

    def test_auto_optimize_mode(self, cluster_app):
        """auto_optimize=True returns correct 4-tuple and populates info keys."""
        orig = self._set_state(
            cluster_app,
            hier=_RC_HIER.copy(),
            tx=_RC_TX.copy(),
            rect=_RC_RECTABLE.copy(),
        )
        try:
            result = cluster_app.run_clustering(
                {"a", "b", "c", "d", "e"},
                grouping_column="nercr",
                target_regions=None,
                no_cluster_groups=None,
                auto_optimize=True,
                min_regions=2,
                max_regions=4,
            )
            assert len(result) == 4
            regions, agg, err, info = result
            assert err is None
            assert isinstance(regions, list)
            assert isinstance(agg, dict)
            assert "chosen_n" in info
            assert "modularity" in info
        finally:
            self._restore_state(cluster_app, orig)

    def test_louvain_method(self, cluster_app):
        """method='louvain' uses find_optimal_clusters internally."""
        orig = self._set_state(
            cluster_app,
            hier=_RC_HIER.copy(),
            tx=_RC_TX.copy(),
            rect=_RC_RECTABLE.copy(),
        )
        try:
            regions, agg, err, info = cluster_app.run_clustering(
                {"a", "b", "c", "d", "e"},
                grouping_column="nercr",
                target_regions=2,
                no_cluster_groups=None,
                method="louvain",
            )
            assert err is None
            assert isinstance(regions, list)
            assert isinstance(agg, dict)
            # modularity should be calculated
            assert "modularity" in info
        finally:
            self._restore_state(cluster_app, orig)

    def test_hierarchical_method_default(self, cluster_app):
        """Normal hierarchical-sum run returns valid region structure."""
        orig = self._set_state(
            cluster_app,
            hier=_RC_HIER.copy(),
            tx=_RC_TX.copy(),
            rect=_RC_RECTABLE.copy(),
        )
        try:
            regions, agg, err, info = cluster_app.run_clustering(
                {"a", "b", "c", "d", "e"},
                grouping_column="nercr",
                target_regions=2,
                no_cluster_groups=None,
                method="hierarchical-sum",
            )
            assert err is None
            all_bas = {ba for bas in agg.values() for ba in bas}
            assert all_bas == {"a", "b", "c", "d", "e"}
            assert len(regions) == len(agg)
        finally:
            self._restore_state(cluster_app, orig)

    def test_unclustered_bas_in_output(self, cluster_app):
        """BAs from no_cluster_groups appear in region_aggregations with state-based names."""
        orig = self._set_state(
            cluster_app,
            hier=_RC_HIER.copy(),
            tx=_RC_TX.copy(),
            rect=_RC_RECTABLE.copy(),
        )
        try:
            regions, agg, err, info = cluster_app.run_clustering(
                {"a", "b", "c", "d", "e"},
                grouping_column="nercr",
                target_regions=2,
                no_cluster_groups=["TRE"],  # d and e are unclustered
            )
            assert err is None
            all_bas = {ba for bas in agg.values() for ba in bas}
            assert {"d", "e"}.issubset(all_bas)
            assert all_bas == {"a", "b", "c", "d", "e"}
        finally:
            self._restore_state(cluster_app, orig)

    def test_return_structure(self, cluster_app):
        """Output is (list, dict, None, dict) with correct types."""
        orig = self._set_state(
            cluster_app,
            hier=_RC_HIER.copy(),
            tx=_RC_TX.copy(),
            rect=_RC_RECTABLE.copy(),
        )
        try:
            result = cluster_app.run_clustering(
                {"a", "b", "c", "d", "e"},
                grouping_column="nercr",
                target_regions=2,
                no_cluster_groups=None,
            )
            assert len(result) == 4
            model_regions, region_aggregations, err, info = result
            assert err is None
            assert isinstance(model_regions, list)
            assert isinstance(region_aggregations, dict)
            assert isinstance(info, dict)
            # Every region in model_regions must be a key in region_aggregations
            assert set(model_regions) == set(region_aggregations.keys())
        finally:
            self._restore_state(cluster_app, orig)

    def test_exception_returns_error_tuple(self, cluster_app):
        """Passing selected_bas=None triggers an exception → returns (None, None, str, {})."""
        orig = self._set_state(
            cluster_app,
            hier=_RC_HIER.copy(),
            tx=_RC_TX.copy(),
            rect=_RC_RECTABLE.copy(),
        )
        try:
            result = cluster_app.run_clustering(
                None,  # will cause TypeError inside run_clustering
                grouping_column="nercr",
                target_regions=2,
                no_cluster_groups=None,
            )
            assert len(result) == 4
            regions, agg, err, info = result
            assert regions is None
            assert agg is None
            assert isinstance(err, str)
            assert info == {}
        finally:
            self._restore_state(cluster_app, orig)


# ---------------------------------------------------------------------------
# 14. ATB attribute overrides
# ---------------------------------------------------------------------------


class TestAtbAttributeOverrides:
    """Tests for _ATB_TECH_DEFAULTS constant, _auto_modified_key function,
    and generate_fuels_settings behavior with fuel_type == "none"."""

    # --- _ATB_TECH_DEFAULTS structure ---

    def test_atb_defaults_naturalgas_structure(self, cluster_app):
        """NaturalGas should have fuel=naturalgas, tag_class=THERM, is_commit=True."""
        defaults = cluster_app._ATB_TECH_DEFAULTS.get("NaturalGas")
        assert defaults is not None
        assert defaults["fuel"] == "naturalgas"
        assert defaults["tag_class"] == "THERM"
        assert defaults["is_commit"] is True

    def test_atb_defaults_coal_structure(self, cluster_app):
        """Coal should have fuel=coal, tag_class=THERM, is_commit=True."""
        defaults = cluster_app._ATB_TECH_DEFAULTS.get("Coal")
        assert defaults is not None
        assert defaults["fuel"] == "coal"
        assert defaults["tag_class"] == "THERM"
        assert defaults["is_commit"] is True

    def test_atb_defaults_nuclear_structure(self, cluster_app):
        """Nuclear should have fuel=uranium, tag_class=THERM, is_commit=True."""
        defaults = cluster_app._ATB_TECH_DEFAULTS.get("Nuclear")
        assert defaults is not None
        assert defaults["fuel"] == "uranium"
        assert defaults["tag_class"] == "THERM"
        assert defaults["is_commit"] is True

    def test_atb_defaults_petroleum_structure(self, cluster_app):
        """Petroleum Liquids should have fuel=distillate, tag_class=THERM, is_commit=True."""
        defaults = cluster_app._ATB_TECH_DEFAULTS.get("Petroleum Liquids")
        assert defaults is not None
        assert defaults["fuel"] == "distillate"
        assert defaults["tag_class"] == "THERM"
        assert defaults["is_commit"] is True

    def test_atb_defaults_utilitypv_structure(self, cluster_app):
        """UtilityPV should have fuel=None, tag_class=VRE, is_commit=False."""
        defaults = cluster_app._ATB_TECH_DEFAULTS.get("UtilityPV")
        assert defaults is not None
        assert defaults["fuel"] is None
        assert defaults["tag_class"] == "VRE"
        assert defaults["is_commit"] is False

    def test_atb_defaults_landbasedwind_structure(self, cluster_app):
        """LandbasedWind should have fuel=None, tag_class=VRE, is_commit=False."""
        defaults = cluster_app._ATB_TECH_DEFAULTS.get("LandbasedWind")
        assert defaults is not None
        assert defaults["fuel"] is None
        assert defaults["tag_class"] == "VRE"
        assert defaults["is_commit"] is False

    def test_atb_defaults_offshorewind_structure(self, cluster_app):
        """OffshoreWind should have fuel=None, tag_class=VRE, is_commit=False."""
        defaults = cluster_app._ATB_TECH_DEFAULTS.get("OffshoreWind")
        assert defaults is not None
        assert defaults["fuel"] is None
        assert defaults["tag_class"] == "VRE"
        assert defaults["is_commit"] is False

    def test_atb_defaults_battery_storage_structure(self, cluster_app):
        """Utility-Scale Battery Storage should have fuel=None, tag_class=STOR, is_commit=False."""
        defaults = cluster_app._ATB_TECH_DEFAULTS.get("Utility-Scale Battery Storage")
        assert defaults is not None
        assert defaults["fuel"] is None
        assert defaults["tag_class"] == "STOR"
        assert defaults["is_commit"] is False

    def test_atb_defaults_pumped_hydro_structure(self, cluster_app):
        """Hydroelectric Pumped Storage should have fuel=None, tag_class=HYDRO, is_commit=False."""
        defaults = cluster_app._ATB_TECH_DEFAULTS.get("Hydroelectric Pumped Storage")
        assert defaults is not None
        assert defaults["fuel"] is None
        assert defaults["tag_class"] == "HYDRO"
        assert defaults["is_commit"] is False

    def test_atb_defaults_conventional_steam_coal_structure(self, cluster_app):
        """Conventional Steam Coal should have fuel=coal, tag_class=THERM, is_commit=True."""
        defaults = cluster_app._ATB_TECH_DEFAULTS.get("Conventional Steam Coal")
        assert defaults is not None
        assert defaults["fuel"] == "coal"
        assert defaults["tag_class"] == "THERM"
        assert defaults["is_commit"] is True

    # --- _auto_modified_key ---

    def test_auto_modified_key_basic(self, cluster_app):
        """Basic key generation from tech+detail."""
        # Save original state
        orig_modified = cluster_app.state.modified_new_resources.copy()
        try:
            cluster_app.state.modified_new_resources = {}
            key = cluster_app._auto_modified_key("NaturalGas", "Combined Cycle")
            assert key == "naturalgas_combined_cycle"
        finally:
            cluster_app.state.modified_new_resources = orig_modified

    def test_auto_modified_key_sanitization(self, cluster_app):
        """Spaces and special chars become underscores."""
        orig_modified = cluster_app.state.modified_new_resources.copy()
        try:
            cluster_app.state.modified_new_resources = {}
            key = cluster_app._auto_modified_key("Natural Gas", "CC-CCS (90%)")
            # Expected: spaces → _, special chars → _, multiple underscores collapsed
            # "Natural Gas_CC-CCS (90%)" → "natural_gas_cc_ccs_90_"
            # The function uses re.sub(r"[^a-z0-9]+", "_", ...).strip("_")
            # So "Natural Gas_CC-CCS (90%)" → "natural_gas_cc_ccs_90"
            assert key == "natural_gas_cc_ccs_90"
        finally:
            cluster_app.state.modified_new_resources = orig_modified

    def test_auto_modified_key_uniqueness_collision(self, cluster_app):
        """When key exists, appends _1, _2 etc."""
        orig_modified = cluster_app.state.modified_new_resources.copy()
        try:
            cluster_app.state.modified_new_resources = {
                "naturalgas_combined_cycle": {"technology": "NaturalGas"}
            }
            key = cluster_app._auto_modified_key("NaturalGas", "Combined Cycle")
            assert key == "naturalgas_combined_cycle_1"
        finally:
            cluster_app.state.modified_new_resources = orig_modified

    def test_auto_modified_key_multiple_collisions(self, cluster_app):
        """Multiple collisions increment counter."""
        orig_modified = cluster_app.state.modified_new_resources.copy()
        try:
            cluster_app.state.modified_new_resources = {
                "naturalgas_combined_cycle": {"technology": "NaturalGas"},
                "naturalgas_combined_cycle_1": {"technology": "NaturalGas"},
                "naturalgas_combined_cycle_2": {"technology": "NaturalGas"},
            }
            key = cluster_app._auto_modified_key("NaturalGas", "Combined Cycle")
            assert key == "naturalgas_combined_cycle_3"
        finally:
            cluster_app.state.modified_new_resources = orig_modified

    def test_auto_modified_key_empty_state(self, cluster_app):
        """When state is empty, no suffix is added."""
        orig_modified = cluster_app.state.modified_new_resources.copy()
        try:
            cluster_app.state.modified_new_resources = {}
            key = cluster_app._auto_modified_key("OffshoreWind", "Class A")
            assert key == "offshorewind_class_a"
            # Verify no uniqueness suffix was added by checking state is still empty
            # (i.e., the key was not added to state by the function itself)
            assert len(cluster_app.state.modified_new_resources) == 0
        finally:
            cluster_app.state.modified_new_resources = orig_modified

    def test_auto_modified_key_case_insensitive(self, cluster_app):
        """Key generation is case-insensitive."""
        orig_modified = cluster_app.state.modified_new_resources.copy()
        try:
            cluster_app.state.modified_new_resources = {}
            key1 = cluster_app._auto_modified_key("NaturalGas", "Combined Cycle")
            key2 = cluster_app._auto_modified_key("naturalgas", "combined cycle")
            # Both should produce the same base key
            assert key1 == key2
            assert key1 == "naturalgas_combined_cycle"
        finally:
            cluster_app.state.modified_new_resources = orig_modified

    # --- generate_fuels_settings with fuel_type == "none" ---

    def test_generate_fuels_settings_skips_vre_fuel_mapping(self, cluster_app):
        """VRE resources with fuel_type=none should not appear in tech_fuel_map."""
        orig_modified = cluster_app.state.modified_new_resources.copy()
        try:
            # Set up a VRE resource with fuel_type == "none"
            cluster_app.state.modified_new_resources = {
                "utilitypv_class_1": {
                    "technology": "UtilityPV",
                    "tech_detail": "Class 1",
                    "cost_case": "moderate",
                    "size_mw": 100,
                    "new_technology": "UtilityPV",
                    "new_tech_detail": "Class 1",
                    "new_cost_case": "moderate",
                    "attr_modifiers": {"capex_mw": 1200000.0},
                    "fuel_type": "none",
                    "standard_fuel": "naturalgas",
                    "tag_class": "VRE",
                    "is_commit": False,
                }
            }

            # Mock document.getElementById to return sensible values
            mock_doc = MagicMock()

            def mock_get_element(element_id):
                mock_elem = MagicMock()
                # Fuel scenario selects should return valid values
                if element_id == "fuelDataYear":
                    mock_elem.value = "2025"
                elif element_id in [
                    "fuelScenarioCoal",
                    "fuelScenarioNaturalGas",
                    "fuelScenarioDistillate",
                    "fuelScenarioUranium",
                ]:
                    mock_elem.value = "reference"
                else:
                    mock_elem.value = ""
                return mock_elem

            mock_doc.getElementById = mock_get_element
            orig_doc = cluster_app.document
            cluster_app.document = mock_doc

            try:
                yaml_str = cluster_app.generate_fuels_settings()
                parsed = yaml.safe_load(yaml_str)

                # Check that tech_fuel_map exists
                assert "tech_fuel_map" in parsed

                # Check that "UtilityPV_" prefix is NOT in tech_fuel_map
                tech_fuel_map = parsed["tech_fuel_map"]
                for tech_key in tech_fuel_map.keys():
                    assert not tech_key.startswith(
                        "UtilityPV_"
                    ), f"Found VRE tech in fuel map: {tech_key}"

            finally:
                cluster_app.document = orig_doc
        finally:
            cluster_app.state.modified_new_resources = orig_modified

    def test_generate_fuels_settings_includes_thermal_fuel_mapping(self, cluster_app):
        """Thermal resources with fuel_type=standard should appear in tech_fuel_map."""
        orig_modified = cluster_app.state.modified_new_resources.copy()
        try:
            # Set up a thermal resource with fuel_type == "standard"
            cluster_app.state.modified_new_resources = {
                "naturalgas_cc_ccs": {
                    "technology": "NaturalGas",
                    "tech_detail": "CC-CCS",
                    "cost_case": "moderate",
                    "size_mw": 500,
                    "new_technology": "NaturalGas",
                    "new_tech_detail": "CC-CCS",
                    "new_cost_case": "moderate",
                    "attr_modifiers": {"capex_mw": 2000000.0},
                    "fuel_type": "standard",
                    "standard_fuel": "naturalgas",
                    "tag_class": "THERM",
                    "is_commit": True,
                }
            }

            # Mock document.getElementById to return sensible values
            mock_doc = MagicMock()

            def mock_get_element(element_id):
                mock_elem = MagicMock()
                if element_id == "fuelDataYear":
                    mock_elem.value = "2025"
                elif element_id in [
                    "fuelScenarioCoal",
                    "fuelScenarioNaturalGas",
                    "fuelScenarioDistillate",
                    "fuelScenarioUranium",
                ]:
                    mock_elem.value = "reference"
                else:
                    mock_elem.value = ""
                return mock_elem

            mock_doc.getElementById = mock_get_element
            orig_doc = cluster_app.document
            cluster_app.document = mock_doc

            try:
                yaml_str = cluster_app.generate_fuels_settings()
                parsed = yaml.safe_load(yaml_str)

                # Check that tech_fuel_map exists
                assert "tech_fuel_map" in parsed

                # Check that "NaturalGas_" prefix IS in tech_fuel_map
                tech_fuel_map = parsed["tech_fuel_map"]
                found_naturalgas = any(
                    tech_key.startswith("NaturalGas_")
                    for tech_key in tech_fuel_map.keys()
                )
                assert (
                    found_naturalgas
                ), "Expected NaturalGas_ prefix in tech_fuel_map for thermal resource"

            finally:
                cluster_app.document = orig_doc
        finally:
            cluster_app.state.modified_new_resources = orig_modified


# ---------------------------------------------------------------------------
# 22. CCS capture fraction extraction and resource tags
# ---------------------------------------------------------------------------


class TestCCSFunctionality:
    """Tests for CCS capture fraction extraction and resource tags generation."""

    @pytest.mark.parametrize(
        "tech_detail,expected_fraction",
        [
            # Standard patterns with spaces
            ("1-on-1 Combined Cycle (H-Frame) 95% CCS", 0.95),
            ("F-Frame CC 97% CCS", 0.97),
            ("Fuel Cell - 98% CCS", 0.98),
            ("Natural Gas CT 90% CCS", 0.90),
            # Patterns with hyphens
            ("99%-CCS", 0.99),
            ("85%-CCS Technology", 0.85),
            # Patterns without spaces
            ("Nuclear 100%CCS", 1.00),
            ("Coal 80%CCS Advanced", 0.80),
            # Case insensitivity
            ("Advanced Tech 95% ccs", 0.95),
            ("Tech 92% Ccs", 0.92),
            ("Tech 88% CCS", 0.88),
            # Non-CCS technologies
            ("Natural Gas Combined Cycle", None),
            ("Solar Photovoltaic", None),
            ("Onshore Wind", None),
            ("Battery Storage", None),
            ("Regular Technology", None),
            # Edge cases
            ("", None),
            (None, None),
            ("CCS without percentage", None),
            ("100 CCS no percent sign", None),
            # Single and double digit percentages
            ("Tech 5% CCS", 0.05),
            ("Tech 100% CCS", 1.00),
        ],
    )
    def test_extract_ccs_capture_fraction(
        self, cluster_app, tech_detail, expected_fraction
    ):
        """Test CCS capture fraction extraction from various technology detail strings."""
        result = cluster_app._extract_ccs_capture_fraction(tech_detail)
        if expected_fraction is None:
            assert result is None
        else:
            assert result == pytest.approx(expected_fraction, abs=1e-6)

    def test_ccs_in_modified_resources(self, cluster_app):
        """Test that CCS technologies are correctly detected in modified resources."""
        orig_modified = cluster_app.state.modified_new_resources

        try:
            # Set up modified resources with and without CCS
            cluster_app.state.modified_new_resources = {
                "ng_ccs": {
                    "new_technology": "NaturalGas",
                    "new_tech_detail": "F-Frame CC 95% CCS",
                    "ccs_capture_fraction": 0.95,
                    "tag_class": "THERM",
                    "is_commit": True,
                },
                "ng_regular": {
                    "new_technology": "NaturalGas",
                    "new_tech_detail": "F-Frame CC",
                    "ccs_capture_fraction": None,
                    "tag_class": "THERM",
                    "is_commit": True,
                },
                "coal_ccs": {
                    "new_technology": "Coal",
                    "new_tech_detail": "Subcritical 90% CCS",
                    "ccs_capture_fraction": 0.90,
                    "tag_class": "THERM",
                    "is_commit": True,
                },
            }

            # Set disposal cost
            cluster_app.state.ccs_disposal_cost = 25

            # Generate resource tags (returns YAML string)
            result_yaml = cluster_app.generate_resource_tags_settings()
            result = yaml.safe_load(result_yaml)

            # Check that CCS tags exist
            assert "model_tag_values" in result
            values = result["model_tag_values"]

            assert "CO2_Capture_Fraction" in values
            assert "CO2_Capture_Fraction_Startup" in values
            assert "CCS_Disposal_Cost_per_Metric_Ton" in values

            # Check CCS technology entries
            capture_fractions = values["CO2_Capture_Fraction"]
            assert "NaturalGas_F-Frame CC 95% CCS" in capture_fractions
            assert capture_fractions["NaturalGas_F-Frame CC 95% CCS"] == pytest.approx(
                0.95
            )
            assert "Coal_Subcritical 90% CCS" in capture_fractions
            assert capture_fractions["Coal_Subcritical 90% CCS"] == pytest.approx(0.90)

            # Check startup fractions match
            startup_fractions = values["CO2_Capture_Fraction_Startup"]
            assert startup_fractions["NaturalGas_F-Frame CC 95% CCS"] == pytest.approx(
                0.95
            )
            assert startup_fractions["Coal_Subcritical 90% CCS"] == pytest.approx(0.90)

            # Check disposal costs
            disposal_costs = values["CCS_Disposal_Cost_per_Metric_Ton"]
            assert disposal_costs["NaturalGas_F-Frame CC 95% CCS"] == 25
            assert disposal_costs["Coal_Subcritical 90% CCS"] == 25

            # Non-CCS technology should not be in CCS tags
            assert "NaturalGas_F-Frame CC" not in capture_fractions

        finally:
            cluster_app.state.modified_new_resources = orig_modified

    def test_ccs_in_regular_new_resources(self, cluster_app):
        """Test that CCS technologies are correctly detected in regular new resources."""
        orig_modified = cluster_app.state.modified_new_resources
        orig_doc = cluster_app.document

        try:
            # Mock document with new resources textarea containing CCS technologies
            mock_doc = MagicMock()
            mock_textarea = MagicMock()
            mock_textarea.value = """
NaturalGas | F-Frame CC 95% CCS | Mid | 500
Coal | Supercritical 90% CCS | Mid | 800
NaturalGas | H-Frame CC | Mid | 600
"""
            mock_doc.getElementById.return_value = mock_textarea
            cluster_app.document = mock_doc

            # Clear modified resources to test only regular resources
            cluster_app.state.modified_new_resources = {}

            # Set disposal cost
            cluster_app.state.ccs_disposal_cost = 30

            # Generate resource tags (returns YAML string)
            result_yaml = cluster_app.generate_resource_tags_settings()
            result = yaml.safe_load(result_yaml)

            # Check that CCS tags exist
            assert "model_tag_values" in result
            values = result["model_tag_values"]

            assert "CO2_Capture_Fraction" in values
            assert "CO2_Capture_Fraction_Startup" in values
            assert "CCS_Disposal_Cost_per_Metric_Ton" in values

            # Check CCS technology entries
            capture_fractions = values["CO2_Capture_Fraction"]
            assert "NaturalGas_F-Frame CC 95% CCS" in capture_fractions
            assert capture_fractions["NaturalGas_F-Frame CC 95% CCS"] == pytest.approx(
                0.95
            )
            assert "Coal_Supercritical 90% CCS" in capture_fractions
            assert capture_fractions["Coal_Supercritical 90% CCS"] == pytest.approx(
                0.90
            )

            # Check disposal costs
            disposal_costs = values["CCS_Disposal_Cost_per_Metric_Ton"]
            assert disposal_costs["NaturalGas_F-Frame CC 95% CCS"] == 30
            assert disposal_costs["Coal_Supercritical 90% CCS"] == 30

            # Non-CCS technology should not be in CCS tags
            assert "NaturalGas_H-Frame CC" not in capture_fractions

        finally:
            cluster_app.state.modified_new_resources = orig_modified
            cluster_app.document = orig_doc

    def test_ccs_tags_not_present_without_ccs_technologies(self, cluster_app):
        """Test that CCS tags are not added when no CCS technologies are present."""
        orig_modified = cluster_app.state.modified_new_resources
        orig_doc = cluster_app.document

        try:
            # Mock document with new resources textarea containing NO CCS technologies
            mock_doc = MagicMock()
            mock_textarea = MagicMock()
            mock_textarea.value = """
NaturalGas, H-Frame CC, Mid, 500
UtilityPV, Class1, Mid, 100
LandbasedWind, Class4, Mid, 200
"""
            mock_doc.getElementById.return_value = mock_textarea
            cluster_app.document = mock_doc

            # No modified resources with CCS
            cluster_app.state.modified_new_resources = {
                "solar": {
                    "new_technology": "UtilityPV",
                    "new_tech_detail": "Class1",
                    "ccs_capture_fraction": None,
                    "tag_class": "VRE",
                    "is_commit": False,
                }
            }

            # Generate resource tags (returns YAML string)
            result_yaml = cluster_app.generate_resource_tags_settings()
            result = yaml.safe_load(result_yaml)

            # CCS tags should NOT be in model_tag_values
            values = result["model_tag_values"]
            assert "CO2_Capture_Fraction" not in values
            assert "CO2_Capture_Fraction_Startup" not in values
            assert "CCS_Disposal_Cost_per_Metric_Ton" not in values

        finally:
            cluster_app.state.modified_new_resources = orig_modified
            cluster_app.document = orig_doc

    def test_ccs_mixed_sources(self, cluster_app):
        """Test CCS detection when technologies come from both modified and regular resources."""
        orig_modified = cluster_app.state.modified_new_resources
        orig_doc = cluster_app.document

        try:
            # Modified resource with CCS
            cluster_app.state.modified_new_resources = {
                "hydrogen_ct": {
                    "new_technology": "NaturalGas",
                    "new_tech_detail": "H2 CT 98% CCS",
                    "ccs_capture_fraction": 0.98,
                    "tag_class": "THERM",
                    "is_commit": True,
                }
            }

            # Regular resources with CCS
            mock_doc = MagicMock()
            mock_textarea = MagicMock()
            mock_textarea.value = "NaturalGas | F-Frame CC 95% CCS | Mid | 500"
            mock_doc.getElementById.return_value = mock_textarea
            cluster_app.document = mock_doc

            # Set disposal cost
            cluster_app.state.ccs_disposal_cost = 22

            # Generate resource tags (returns YAML string)
            result_yaml = cluster_app.generate_resource_tags_settings()
            result = yaml.safe_load(result_yaml)

            # Check that both technologies are present
            values = result["model_tag_values"]
            capture_fractions = values["CO2_Capture_Fraction"]

            assert "NaturalGas_H2 CT 98% CCS" in capture_fractions
            assert capture_fractions["NaturalGas_H2 CT 98% CCS"] == pytest.approx(0.98)
            assert "NaturalGas_F-Frame CC 95% CCS" in capture_fractions
            assert capture_fractions["NaturalGas_F-Frame CC 95% CCS"] == pytest.approx(
                0.95
            )

            # Both should have same disposal cost
            disposal_costs = values["CCS_Disposal_Cost_per_Metric_Ton"]
            assert disposal_costs["NaturalGas_H2 CT 98% CCS"] == 22
            assert disposal_costs["NaturalGas_F-Frame CC 95% CCS"] == 22

        finally:
            cluster_app.state.modified_new_resources = orig_modified
            cluster_app.document = orig_doc

    def test_ccs_disposal_cost_default(self, cluster_app):
        """Test that default CCS disposal cost is applied correctly."""
        orig_modified = cluster_app.state.modified_new_resources
        orig_disposal_cost = cluster_app.state.ccs_disposal_cost

        try:
            # Use default disposal cost (should be 20)
            default_cost = 20
            cluster_app.state.ccs_disposal_cost = default_cost

            cluster_app.state.modified_new_resources = {
                "ccs_tech": {
                    "new_technology": "Coal",
                    "new_tech_detail": "Advanced 92% CCS",
                    "ccs_capture_fraction": 0.92,
                    "tag_class": "THERM",
                    "is_commit": True,
                }
            }

            result_yaml = cluster_app.generate_resource_tags_settings()
            result = yaml.safe_load(result_yaml)
            values = result["model_tag_values"]
            disposal_costs = values["CCS_Disposal_Cost_per_Metric_Ton"]

            assert disposal_costs["Coal_Advanced 92% CCS"] == default_cost

        finally:
            cluster_app.state.modified_new_resources = orig_modified
            cluster_app.state.ccs_disposal_cost = orig_disposal_cost

    def test_ccs_edge_cases_empty_and_whitespace(self, cluster_app):
        """Test edge cases with empty strings and whitespace in tech details."""
        # Empty string
        assert cluster_app._extract_ccs_capture_fraction("") is None

        # Whitespace only
        assert cluster_app._extract_ccs_capture_fraction("   ") is None
        assert cluster_app._extract_ccs_capture_fraction("\t\n") is None

        # CCS keyword but no percentage
        assert cluster_app._extract_ccs_capture_fraction("CCS Technology") is None
        assert (
            cluster_app._extract_ccs_capture_fraction("Carbon Capture Storage") is None
        )

    def test_ccs_various_percentage_formats(self, cluster_app):
        """Test various percentage format edge cases."""
        # Valid formats
        assert cluster_app._extract_ccs_capture_fraction(
            "Tech 95% CCS"
        ) == pytest.approx(0.95)
        assert cluster_app._extract_ccs_capture_fraction(
            "Tech 95%-CCS"
        ) == pytest.approx(0.95)
        assert cluster_app._extract_ccs_capture_fraction(
            "Tech 95%CCS"
        ) == pytest.approx(0.95)
        assert cluster_app._extract_ccs_capture_fraction(
            "Tech 95% - CCS"
        ) == pytest.approx(0.95)

        # Invalid formats (missing %)
        assert cluster_app._extract_ccs_capture_fraction("Tech 95 CCS") is None

        # Multiple occurrences (should match first)
        result = cluster_app._extract_ccs_capture_fraction("Tech 80% CCS and 90% CCS")
        assert result == pytest.approx(0.80)

    def test_ccs_with_three_digit_percentages(self, cluster_app):
        """Test 100% CCS capture (three-digit percentage)."""
        assert cluster_app._extract_ccs_capture_fraction(
            "Full Capture 100% CCS"
        ) == pytest.approx(1.00)
        assert cluster_app._extract_ccs_capture_fraction("100%-CCS") == pytest.approx(
            1.00
        )

    def test_ccs_tags_ordering(self, cluster_app):
        """Test that CCS tags are added in expected order."""
        orig_modified = cluster_app.state.modified_new_resources

        try:
            cluster_app.state.modified_new_resources = {
                "ccs1": {
                    "new_technology": "NaturalGas",
                    "new_tech_detail": "CT 95% CCS",
                    "ccs_capture_fraction": 0.95,
                    "tag_class": "THERM",
                    "is_commit": True,
                }
            }

            result_yaml = cluster_app.generate_resource_tags_settings()
            result = yaml.safe_load(result_yaml)
            values = result["model_tag_values"]

            # All three CCS tags should exist
            assert "CO2_Capture_Fraction" in values
            assert "CO2_Capture_Fraction_Startup" in values
            assert "CCS_Disposal_Cost_per_Metric_Ton" in values

            # Each should have the same technology keys
            tech_name = "NaturalGas_CT 95% CCS"
            assert tech_name in values["CO2_Capture_Fraction"]
            assert tech_name in values["CO2_Capture_Fraction_Startup"]
            assert tech_name in values["CCS_Disposal_Cost_per_Metric_Ton"]

        finally:
            cluster_app.state.modified_new_resources = orig_modified


# ---------------------------------------------------------------------------
# TestFuelChartHelpers
# ---------------------------------------------------------------------------


def _make_fuel_df(**overrides):
    """Return a minimal multi-row DataFrame matching the fuel_prices.csv schema."""
    base = {
        "year": [2024, 2026, 2024, 2026, 2024, 2026],
        "price": [2.0, 2.5, 3.0, 3.5, 2.1, 2.6],
        "data_year": [2025, 2025, 2025, 2025, 2025, 2025],
        "scenario": [
            "reference",
            "reference",
            "high",
            "high",
            "reference",
            "reference",
        ],
        "fuel": ["coal", "coal", "coal", "coal", "coal", "coal"],
        "region": ["p1", "p1", "p1", "p1", "p2", "p2"],
        "dollar_year": [2024, 2024, 2024, 2024, 2024, 2024],
    }
    base.update(overrides)
    return pd.DataFrame(base)


class TestFuelChartHelpers:
    """Tests for _build_fuel_chart_data and _render_fuel_price_chart_svg."""

    # ------------------------------------------------------------------
    # _build_fuel_chart_data
    # ------------------------------------------------------------------

    def test_returns_empty_when_state_df_is_none(self, cluster_app):
        orig = cluster_app.state.fuel_prices_df
        try:
            cluster_app.state.fuel_prices_df = None
            result = cluster_app._build_fuel_chart_data(2025)
            assert result == {}
        finally:
            cluster_app.state.fuel_prices_df = orig

    def test_returns_empty_when_state_df_is_empty(self, cluster_app):
        orig = cluster_app.state.fuel_prices_df
        try:
            cluster_app.state.fuel_prices_df = pd.DataFrame(
                columns=[
                    "year",
                    "price",
                    "data_year",
                    "scenario",
                    "fuel",
                    "region",
                    "dollar_year",
                ]
            )
            result = cluster_app._build_fuel_chart_data(2025)
            assert result == {}
        finally:
            cluster_app.state.fuel_prices_df = orig

    def test_returns_empty_for_missing_data_year(self, cluster_app):
        orig = cluster_app.state.fuel_prices_df
        try:
            cluster_app.state.fuel_prices_df = _make_fuel_df()
            result = cluster_app._build_fuel_chart_data(9999)
            assert result == {}
        finally:
            cluster_app.state.fuel_prices_df = orig

    def test_returns_empty_when_year_column_missing(self, cluster_app):
        orig = cluster_app.state.fuel_prices_df
        try:
            df = _make_fuel_df()
            df = df.drop(columns=["year"])
            cluster_app.state.fuel_prices_df = df
            result = cluster_app._build_fuel_chart_data(2025)
            assert result == {}
        finally:
            cluster_app.state.fuel_prices_df = orig

    def test_returns_empty_when_price_column_missing(self, cluster_app):
        orig = cluster_app.state.fuel_prices_df
        try:
            df = _make_fuel_df()
            df = df.drop(columns=["price"])
            cluster_app.state.fuel_prices_df = df
            result = cluster_app._build_fuel_chart_data(2025)
            assert result == {}
        finally:
            cluster_app.state.fuel_prices_df = orig

    def test_averages_price_across_regions(self, cluster_app):
        """For coal/reference/2024: regions p1 (2.0) and p2 (2.1) -> avg 2.05."""
        orig = cluster_app.state.fuel_prices_df
        try:
            cluster_app.state.fuel_prices_df = _make_fuel_df()
            result = cluster_app._build_fuel_chart_data(2025)

            assert "coal" in result
            assert "reference" in result["coal"]

            pts_by_year = dict(result["coal"]["reference"])
            assert pts_by_year[2024] == pytest.approx(2.05)
            assert pts_by_year[2026] == pytest.approx(2.55)
        finally:
            cluster_app.state.fuel_prices_df = orig

    def test_points_sorted_by_year(self, cluster_app):
        """Years in each scenario list must be in ascending order."""
        orig = cluster_app.state.fuel_prices_df
        try:
            # Insert rows in reverse year order to make sure sorting is applied
            df = _make_fuel_df()
            df = df.iloc[::-1].reset_index(drop=True)
            cluster_app.state.fuel_prices_df = df
            result = cluster_app._build_fuel_chart_data(2025)

            for fuel_scenarios in result.values():
                for pts in fuel_scenarios.values():
                    years = [yr for yr, _ in pts]
                    assert years == sorted(years)
        finally:
            cluster_app.state.fuel_prices_df = orig

    def test_multiple_fuels_and_scenarios(self, cluster_app):
        """Ensure distinct fuels and scenarios all appear in the result."""
        orig = cluster_app.state.fuel_prices_df
        try:
            df = pd.DataFrame(
                {
                    "year": [2024, 2024, 2024, 2024],
                    "price": [1.0, 2.0, 5.0, 6.0],
                    "data_year": [2025, 2025, 2025, 2025],
                    "scenario": ["ref", "high", "ref", "high"],
                    "fuel": ["coal", "coal", "naturalgas", "naturalgas"],
                    "region": ["r1", "r1", "r1", "r1"],
                    "dollar_year": [2024, 2024, 2024, 2024],
                }
            )
            cluster_app.state.fuel_prices_df = df
            result = cluster_app._build_fuel_chart_data(2025)

            assert "coal" in result
            assert "naturalgas" in result
            assert "ref" in result["coal"]
            assert "high" in result["coal"]
        finally:
            cluster_app.state.fuel_prices_df = orig

    def test_non_numeric_price_rows_dropped(self, cluster_app):
        """Rows with non-numeric prices should be silently dropped."""
        orig = cluster_app.state.fuel_prices_df
        try:
            df = pd.DataFrame(
                {
                    "year": [2024, 2026],
                    "price": ["bad", 3.0],
                    "data_year": [2025, 2025],
                    "scenario": ["ref", "ref"],
                    "fuel": ["coal", "coal"],
                    "region": ["r1", "r1"],
                    "dollar_year": [2024, 2024],
                }
            )
            cluster_app.state.fuel_prices_df = df
            result = cluster_app._build_fuel_chart_data(2025)

            # Only the valid row (2026) should survive
            assert "coal" in result
            pts_by_year = dict(result["coal"]["ref"])
            assert 2024 not in pts_by_year
            assert pts_by_year[2026] == pytest.approx(3.0)
        finally:
            cluster_app.state.fuel_prices_df = orig

    def test_single_data_point_included(self, cluster_app):
        """A fuel/scenario with only a single year should still appear in the result."""
        orig = cluster_app.state.fuel_prices_df
        try:
            df = pd.DataFrame(
                {
                    "year": [2030],
                    "price": [4.5],
                    "data_year": [2025],
                    "scenario": ["reference"],
                    "fuel": ["uranium"],
                    "region": ["r1"],
                    "dollar_year": [2024],
                }
            )
            cluster_app.state.fuel_prices_df = df
            result = cluster_app._build_fuel_chart_data(2025)

            assert "uranium" in result
            assert result["uranium"]["reference"] == [(2030, pytest.approx(4.5))]
        finally:
            cluster_app.state.fuel_prices_df = orig

    def test_all_non_numeric_prices_returns_empty(self, cluster_app):
        """If every price is non-numeric, the result should be empty."""
        orig = cluster_app.state.fuel_prices_df
        try:
            df = pd.DataFrame(
                {
                    "year": [2024],
                    "price": ["n/a"],
                    "data_year": [2025],
                    "scenario": ["ref"],
                    "fuel": ["coal"],
                    "region": ["r1"],
                    "dollar_year": [2024],
                }
            )
            cluster_app.state.fuel_prices_df = df
            result = cluster_app._build_fuel_chart_data(2025)
            assert result == {}
        finally:
            cluster_app.state.fuel_prices_df = orig

    # ------------------------------------------------------------------
    # _render_fuel_price_chart_svg
    # ------------------------------------------------------------------

    def test_returns_empty_string_for_empty_fuel_data(self, cluster_app):
        result = cluster_app._render_fuel_price_chart_svg({}, "reference")
        assert result == ""

    def test_returns_empty_string_for_none_selected_and_empty_data(self, cluster_app):
        result = cluster_app._render_fuel_price_chart_svg({}, None)
        assert result == ""

    def test_svg_tag_present_for_valid_data(self, cluster_app):
        fuel_data = {"reference": [(2024, 2.0), (2026, 2.5)]}
        result = cluster_app._render_fuel_price_chart_svg(fuel_data, "reference")
        assert "<svg" in result

    def test_polyline_rendered_for_multi_point_scenario(self, cluster_app):
        fuel_data = {"reference": [(2024, 2.0), (2026, 2.5)]}
        result = cluster_app._render_fuel_price_chart_svg(fuel_data, "reference")
        assert "<polyline" in result

    def test_circle_rendered_for_single_point_scenario(self, cluster_app):
        fuel_data = {"reference": [(2024, 2.0)]}
        result = cluster_app._render_fuel_price_chart_svg(fuel_data, "reference")
        assert "<circle" in result
        assert "<polyline" not in result

    def test_selected_scenario_gets_blue_color(self, cluster_app):
        fuel_data = {
            "reference": [(2024, 2.0), (2026, 2.5)],
            "high": [(2024, 3.0), (2026, 3.5)],
        }
        result = cluster_app._render_fuel_price_chart_svg(fuel_data, "reference")
        assert "#1a56c4" in result

    def test_selected_scenario_gets_stroke_width_2(self, cluster_app):
        fuel_data = {"reference": [(2024, 2.0), (2026, 2.5)]}
        result = cluster_app._render_fuel_price_chart_svg(fuel_data, "reference")
        assert 'stroke-width="2"' in result

    def test_non_selected_scenario_gets_gray_color(self, cluster_app):
        fuel_data = {
            "reference": [(2024, 2.0), (2026, 2.5)],
            "high": [(2024, 3.0), (2026, 3.5)],
        }
        result = cluster_app._render_fuel_price_chart_svg(fuel_data, "reference")
        assert "#c8cdd8" in result

    def test_non_selected_scenario_gets_stroke_width_1_25(self, cluster_app):
        fuel_data = {
            "reference": [(2024, 2.0), (2026, 2.5)],
            "high": [(2024, 3.0), (2026, 3.5)],
        }
        result = cluster_app._render_fuel_price_chart_svg(fuel_data, "reference")
        assert 'stroke-width="1.25"' in result

    def test_none_selected_scenario_all_gray(self, cluster_app):
        """When selected_scenario is None, no line should be blue."""
        fuel_data = {
            "reference": [(2024, 2.0), (2026, 2.5)],
            "high": [(2024, 3.0), (2026, 3.5)],
        }
        result = cluster_app._render_fuel_price_chart_svg(fuel_data, None)
        assert "#1a56c4" not in result
        assert "#c8cdd8" in result

    def test_single_scenario_single_point_renders_circle(self, cluster_app):
        """Single fuel/scenario with one point -> circle, no polyline."""
        fuel_data = {"low": [(2030, 1.5)]}
        result = cluster_app._render_fuel_price_chart_svg(fuel_data, "low")
        assert "<circle" in result
        assert "<polyline" not in result
        assert "#1a56c4" in result  # selected colour on the dot

    def test_scenario_title_in_polyline(self, cluster_app):
        """Scenario name should appear as a <title> inside each <polyline>."""
        fuel_data = {"my-scenario": [(2024, 1.0), (2026, 1.5)]}
        result = cluster_app._render_fuel_price_chart_svg(fuel_data, "my-scenario")
        assert "my-scenario" in result

    def test_svg_is_well_formed_closes_tag(self, cluster_app):
        """SVG output must end with </svg>."""
        fuel_data = {"reference": [(2024, 2.0), (2026, 2.5)]}
        result = cluster_app._render_fuel_price_chart_svg(fuel_data, "reference")
        assert result.rstrip().endswith("</svg>")
