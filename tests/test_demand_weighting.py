"""Tests for the demand-weighting feature in clustering_algorithms.py.

Covers:
- compute_demand_boost_factors with demand-sqrt method
- compute_demand_boost_factors with demand-log method
- compute_demand_boost_factors with empty demand_weights
- compute_demand_boost_factors with unknown method
- compute_demand_boost_factors with missing BA keys
- build_transmission_graph with demand_weight_method=None (no change)
- build_transmission_graph with demand-sqrt (low-demand endpoint raises weight)
- build_transmission_graph with demand_weight_method="none" (no change)
"""

import math
import sys
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Import the module under test directly (no PyScript environment needed)
# ---------------------------------------------------------------------------
web_dir = Path(__file__).parent.parent / "web"
sys.path.insert(0, str(web_dir))
import clustering_algorithms
from clustering_algorithms import (
    build_transmission_graph,
    compute_demand_boost_factors,
    connect_disconnected_group_components,
    find_optimal_clusters,
    hierarchical_cluster,
)

_DATA_DIR = Path(__file__).parent.parent / "data"

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def simple_tx_df():
    """Minimal transmission DataFrame with two edges: A-B and B-C."""
    return pd.DataFrame(
        {
            "region_from": ["A", "B"],
            "region_to": ["B", "C"],
            "firm_ttc_mw": [100.0, 200.0],
        }
    )


@pytest.fixture()
def valid_bas_abc():
    return {"A", "B", "C"}


# ---------------------------------------------------------------------------
# compute_demand_boost_factors – demand-sqrt
# ---------------------------------------------------------------------------


class TestComputeDemandBoostFactorsSqrt:
    method = "demand-sqrt"

    def test_low_demand_ba_gets_boost_greater_than_one(self):
        """A BA with demand well below the median should receive boost > 1."""
        valid_bas = {"high", "med", "low"}
        demand_weights = {
            "high": 10_000.0,
            "med": 1_000.0,
            "low": 100.0,
        }
        boosts = compute_demand_boost_factors(valid_bas, demand_weights, self.method)

        # median of [10000, 1000, 100] = 1000; sqrt(1000/100) = sqrt(10) ≈ 3.16
        assert boosts["low"] > 1.0, "Low-demand BA should have boost > 1"

    def test_median_demand_ba_gets_boost_close_to_one(self):
        """A BA at exactly the median should receive boost ≈ 1.0."""
        valid_bas = {"high", "med", "low"}
        demand_weights = {
            "high": 10_000.0,
            "med": 1_000.0,
            "low": 100.0,
        }
        boosts = compute_demand_boost_factors(valid_bas, demand_weights, self.method)

        # sqrt(1000/1000) = 1.0 exactly
        assert boosts["med"] == pytest.approx(1.0, abs=1e-9)

    def test_high_demand_ba_clamped_to_one(self):
        """A BA above the median would compute boost < 1, but must be clamped to 1."""
        valid_bas = {"high", "med", "low"}
        demand_weights = {
            "high": 10_000.0,
            "med": 1_000.0,
            "low": 100.0,
        }
        boosts = compute_demand_boost_factors(valid_bas, demand_weights, self.method)

        # sqrt(1000/10000) = 0.316 → clamped to 1.0
        assert boosts["high"] == pytest.approx(1.0, abs=1e-9)

    def test_sqrt_boost_formula_correctness(self):
        """Verify the exact sqrt formula: boost = sqrt(median / demand)."""
        valid_bas = {"a", "b", "c"}
        demand_weights = {"a": 400.0, "b": 100.0, "c": 900.0}
        boosts = compute_demand_boost_factors(valid_bas, demand_weights, self.method)

        # median([400, 100, 900]) = 400; sqrt(400/100)=2, sqrt(400/400)=1, sqrt(400/900)<1→1
        assert boosts["b"] == pytest.approx(2.0, rel=1e-6)
        assert boosts["a"] == pytest.approx(1.0, rel=1e-6)
        assert boosts["c"] == pytest.approx(1.0, rel=1e-6)

    def test_all_boosts_at_least_one(self):
        """No BA should ever receive a boost below 1.0."""
        valid_bas = {"x", "y", "z"}
        demand_weights = {"x": 50.0, "y": 500.0, "z": 5_000.0}
        boosts = compute_demand_boost_factors(valid_bas, demand_weights, self.method)

        for ba, b in boosts.items():
            assert b >= 1.0, f"BA {ba!r} boost {b} is below 1.0"


# ---------------------------------------------------------------------------
# compute_demand_boost_factors – demand-log
# ---------------------------------------------------------------------------


class TestComputeDemandBoostFactorsLog:
    method = "demand-log"

    def test_very_low_demand_gets_larger_boost_than_moderate(self):
        """A very-low-demand BA should receive a strictly larger boost than moderate."""
        valid_bas = {"tiny", "moderate", "large"}
        demand_weights = {
            "tiny": 1.0,
            "moderate": 1_000.0,
            "large": 100_000.0,
        }
        boosts = compute_demand_boost_factors(valid_bas, demand_weights, self.method)

        assert (
            boosts["tiny"] > boosts["moderate"]
        ), "Tiny-demand BA should have a larger boost than moderate-demand BA"

    def test_max_demand_ba_gets_boost_of_one(self):
        """The BA with the highest demand should get boost = 1.0 (log_max / log_max)."""
        valid_bas = {"small", "big"}
        demand_weights = {"small": 10.0, "big": 1_000.0}
        boosts = compute_demand_boost_factors(valid_bas, demand_weights, self.method)

        assert boosts["big"] == pytest.approx(1.0, abs=1e-9)

    def test_log_boost_formula_correctness(self):
        """Verify the exact log formula for a specific example."""
        valid_bas = {"a", "b"}
        demand_weights = {"a": 99.0, "b": 9.0}  # max=99
        boosts = compute_demand_boost_factors(valid_bas, demand_weights, self.method)

        expected_a = math.log(100.0) / math.log(100.0)  # = 1.0
        expected_b = math.log(100.0) / math.log(10.0)  # = 2.0
        assert boosts["a"] == pytest.approx(expected_a, rel=1e-6)
        assert boosts["b"] == pytest.approx(expected_b, rel=1e-6)

    def test_all_boosts_at_least_one(self):
        """No BA should ever receive a boost below 1.0."""
        valid_bas = {"p", "q", "r"}
        demand_weights = {"p": 1.0, "q": 100.0, "r": 10_000.0}
        boosts = compute_demand_boost_factors(valid_bas, demand_weights, self.method)

        for ba, b in boosts.items():
            assert b >= 1.0, f"BA {ba!r} boost {b} is below 1.0"


# ---------------------------------------------------------------------------
# compute_demand_boost_factors – edge cases
# ---------------------------------------------------------------------------


class TestComputeDemandBoostFactorsEdgeCases:
    def test_empty_demand_weights_all_ones(self):
        """When demand_weights is empty, all boosts should be exactly 1.0."""
        valid_bas = {"a", "b", "c"}
        boosts = compute_demand_boost_factors(valid_bas, {}, method="demand-sqrt")

        assert boosts == {ba: 1.0 for ba in valid_bas}

    def test_unknown_method_all_ones(self):
        """An unrecognised method string should produce all boosts = 1.0."""
        valid_bas = {"a", "b"}
        demand_weights = {"a": 100.0, "b": 200.0}
        boosts = compute_demand_boost_factors(
            valid_bas, demand_weights, method="invalid-method"
        )

        assert boosts == {"a": 1.0, "b": 1.0}

    def test_missing_ba_keys_get_boost_one(self):
        """BAs absent from demand_weights should receive boost = 1.0."""
        valid_bas = {"present", "absent"}
        demand_weights = {"present": 500.0}  # "absent" not in dict
        boosts = compute_demand_boost_factors(
            valid_bas, demand_weights, method="demand-sqrt"
        )

        assert boosts["absent"] == pytest.approx(1.0, abs=1e-9)

    def test_missing_ba_keys_log_get_boost_one(self):
        """BAs absent from demand_weights should receive boost = 1.0 (log method)."""
        valid_bas = {"present", "absent"}
        demand_weights = {"present": 500.0}
        boosts = compute_demand_boost_factors(
            valid_bas, demand_weights, method="demand-log"
        )

        assert boosts["absent"] == pytest.approx(1.0, abs=1e-9)

    def test_keys_matched_case_insensitively(self):
        """BA ids are lower-cased when looking up demand_weights keys."""
        valid_bas = {"UPPER", "lower"}
        demand_weights = {
            "upper": 200.0,  # lowercase key for the uppercase BA id
            "lower": 200.0,
        }
        boosts = compute_demand_boost_factors(
            valid_bas, demand_weights, method="demand-sqrt"
        )

        # Both BAs should have equal demand → both boosts ≈ 1.0
        assert boosts["UPPER"] == pytest.approx(1.0, abs=1e-9)
        assert boosts["lower"] == pytest.approx(1.0, abs=1e-9)

    def test_returns_all_valid_bas(self):
        """The returned dict should contain every BA in valid_bas."""
        valid_bas = {"a", "b", "c", "d"}
        demand_weights = {"a": 10.0, "b": 20.0}
        boosts = compute_demand_boost_factors(
            valid_bas, demand_weights, method="demand-sqrt"
        )

        assert set(boosts.keys()) == valid_bas


# ---------------------------------------------------------------------------
# build_transmission_graph – demand weighting integration
# ---------------------------------------------------------------------------


class TestBuildTransmissionGraphDemandWeighting:
    def _edge_weight(self, G, u, v):
        return G[u][v]["weight"]

    def test_no_demand_weight_method_unchanged(self, simple_tx_df, valid_bas_abc):
        """demand_weight_method=None should produce the same graph as no arguments."""
        G_plain = build_transmission_graph(simple_tx_df, valid_bas_abc)
        G_none = build_transmission_graph(
            simple_tx_df, valid_bas_abc, demand_weight_method=None
        )

        assert self._edge_weight(G_plain, "A", "B") == pytest.approx(
            self._edge_weight(G_none, "A", "B")
        )
        assert self._edge_weight(G_plain, "B", "C") == pytest.approx(
            self._edge_weight(G_none, "B", "C")
        )

    def test_demand_weight_method_string_none_unchanged(
        self, simple_tx_df, valid_bas_abc
    ):
        """demand_weight_method='none' (string) should be treated as no weighting."""
        demand_weights = {"a": 100.0, "b": 100.0, "c": 100.0}
        G_plain = build_transmission_graph(simple_tx_df, valid_bas_abc)
        G_none_str = build_transmission_graph(
            simple_tx_df,
            valid_bas_abc,
            demand_weights=demand_weights,
            demand_weight_method="none",
        )

        assert self._edge_weight(G_plain, "A", "B") == pytest.approx(
            self._edge_weight(G_none_str, "A", "B")
        )
        assert self._edge_weight(G_plain, "B", "C") == pytest.approx(
            self._edge_weight(G_none_str, "B", "C")
        )

    def test_demand_sqrt_low_demand_endpoint_increases_edge_weight(
        self, simple_tx_df, valid_bas_abc
    ):
        """An edge touching a low-demand BA should have a larger weight after boosting."""
        # A has very low demand → its edge A-B should be boosted
        demand_weights = {
            "a": 1.0,  # very low
            "b": 1_000.0,  # high
            "c": 1_000.0,  # high
        }
        G_plain = build_transmission_graph(simple_tx_df, valid_bas_abc)
        G_boosted = build_transmission_graph(
            simple_tx_df,
            valid_bas_abc,
            demand_weights=demand_weights,
            demand_weight_method="demand-sqrt",
        )

        w_plain_ab = self._edge_weight(G_plain, "A", "B")
        w_boosted_ab = self._edge_weight(G_boosted, "A", "B")

        assert (
            w_boosted_ab > w_plain_ab
        ), "Edge A-B weight should increase when A has low demand"

    def test_demand_sqrt_equal_demand_no_change(self, simple_tx_df, valid_bas_abc):
        """When all BAs have identical demand, boosts are all 1.0 → weights unchanged."""
        demand_weights = {"a": 500.0, "b": 500.0, "c": 500.0}
        G_plain = build_transmission_graph(simple_tx_df, valid_bas_abc)
        G_boosted = build_transmission_graph(
            simple_tx_df,
            valid_bas_abc,
            demand_weights=demand_weights,
            demand_weight_method="demand-sqrt",
        )

        assert self._edge_weight(G_plain, "A", "B") == pytest.approx(
            self._edge_weight(G_boosted, "A", "B")
        )
        assert self._edge_weight(G_plain, "B", "C") == pytest.approx(
            self._edge_weight(G_boosted, "B", "C")
        )

    def test_demand_sqrt_boost_uses_max_of_two_endpoints(self):
        """Edge weight = raw_weight * max(boost_u, boost_v)."""
        tx_df = pd.DataFrame(
            {
                "region_from": ["X"],
                "region_to": ["Y"],
                "firm_ttc_mw": [100.0],
            }
        )
        valid_bas = {"X", "Y"}
        # X has demand 100 (low), Y has demand 10_000 (high)
        # median([100, 10000]) = 5050; sqrt(5050/100) ≈ 7.11 for X
        # sqrt(5050/10000) < 1 → clamped to 1.0 for Y
        demand_weights = {"x": 100.0, "y": 10_000.0}
        G = build_transmission_graph(
            tx_df,
            valid_bas,
            demand_weights=demand_weights,
            demand_weight_method="demand-sqrt",
        )

        median_d = np.median([100.0, 10_000.0])
        expected_boost_x = np.sqrt(median_d / 100.0)
        expected_boost_y = 1.0  # clamped
        expected_weight = 100.0 * max(expected_boost_x, expected_boost_y)

        assert G["X"]["Y"]["weight"] == pytest.approx(expected_weight, rel=1e-6)

    def test_demand_log_low_demand_endpoint_increases_edge_weight(
        self, simple_tx_df, valid_bas_abc
    ):
        """demand-log method also boosts edges connecting low-demand BAs."""
        demand_weights = {
            "a": 1.0,  # tiny
            "b": 10_000.0,
            "c": 10_000.0,
        }
        G_plain = build_transmission_graph(simple_tx_df, valid_bas_abc)
        G_boosted = build_transmission_graph(
            simple_tx_df,
            valid_bas_abc,
            demand_weights=demand_weights,
            demand_weight_method="demand-log",
        )

        assert G_boosted["A"]["B"]["weight"] > G_plain["A"]["B"]["weight"]

    def test_graph_contains_all_valid_bas_as_nodes(self, simple_tx_df, valid_bas_abc):
        """All valid BAs should be present as nodes even when demand weighting is on."""
        demand_weights = {"a": 100.0, "b": 1_000.0, "c": 10_000.0}
        G = build_transmission_graph(
            simple_tx_df,
            valid_bas_abc,
            demand_weights=demand_weights,
            demand_weight_method="demand-sqrt",
        )

        assert set(G.nodes()) == valid_bas_abc

    def test_no_demand_weights_dict_no_boost(self, simple_tx_df, valid_bas_abc):
        """Passing demand_weight_method but no demand_weights dict means no boost."""
        G_plain = build_transmission_graph(simple_tx_df, valid_bas_abc)
        G_method_only = build_transmission_graph(
            simple_tx_df,
            valid_bas_abc,
            demand_weights=None,
            demand_weight_method="demand-sqrt",
        )

        assert self._edge_weight(G_plain, "A", "B") == pytest.approx(
            self._edge_weight(G_method_only, "A", "B")
        )


class TestHierarchicalClusterDemandWeighting:
    def test_group_merge_branch_demand_log_changes_merged_pair(self):
        """Demand weighting should affect group merges when target_regions < num_groups."""
        hierarchy_df = pd.DataFrame(
            {
                "ba": ["A", "B", "C"],
                "grp": ["g1", "g2", "g3"],
            }
        )
        transmission_df = pd.DataFrame(
            {
                "region_from": ["A", "B"],
                "region_to": ["B", "C"],
                "firm_ttc_mw": [90.0, 100.0],
            }
        )
        cluster_bas = {"A", "B", "C"}

        clusters_unweighted = hierarchical_cluster(
            hierarchy_df,
            transmission_df,
            cluster_bas,
            grouping_column="grp",
            target_regions=2,
            method="hierarchical-sum",
        )
        clusters_weighted = hierarchical_cluster(
            hierarchy_df,
            transmission_df,
            cluster_bas,
            grouping_column="grp",
            target_regions=2,
            method="hierarchical-sum",
            demand_weights={"a": 1.0, "b": 1000.0, "c": 1000.0},
            demand_weight_method="demand-log",
        )

        merged_unweighted = next(c for c in clusters_unweighted.values() if len(c) == 2)
        merged_weighted = next(c for c in clusters_weighted.values() if len(c) == 2)

        assert merged_unweighted == {"B", "C"}
        assert merged_weighted == {"A", "B"}


class TestFindOptimalClustersGroupingBoundaries:
    def test_merge_down_does_not_cross_group_boundaries_when_max_matches_groups(self):
        """Merge-down should stay within grouping boundaries even with a dominant cross-group edge."""
        hierarchy_df = pd.DataFrame(
            {
                "ba": ["A", "B", "C", "D", "E", "F"],
                "grp": ["g1", "g1", "g1", "g2", "g2", "g2"],
            }
        )
        transmission_df = pd.DataFrame(
            {
                "region_from": ["A", "B", "C", "D", "E"],
                "region_to": ["B", "C", "D", "E", "F"],
                "firm_ttc_mw": [40.0, 1.0, 1_000.0, 40.0, 1.0],
            }
        )

        clusters, num_clusters, _modularity, _scores, _optimal = find_optimal_clusters(
            hierarchy_df,
            transmission_df,
            cluster_bas={"A", "B", "C", "D", "E", "F"},
            grouping_column="grp",
            min_regions=2,
            max_regions=2,
        )

        assert num_clusters == 2

        ba_to_group = dict(zip(hierarchy_df["ba"], hierarchy_df["grp"]))
        cluster_sets = [set(nodes) for nodes in clusters.values()]

        assert {"A", "B", "C"} in cluster_sets
        assert {"D", "E", "F"} in cluster_sets
        assert all(
            len({ba_to_group[ba] for ba in cluster}) == 1 for cluster in cluster_sets
        )


class TestDisconnectedGroupConnectivity:
    def test_connect_disconnected_group_components_adds_single_weak_edge(self):
        """A disconnected BA in the same group should get one synthetic connector edge."""
        graph = nx.Graph()
        graph.add_edge("A", "B", weight=100.0)
        graph.add_node("C")
        groups = {"g1": {"A", "B", "C"}}

        before_edges = set(frozenset((u, v)) for u, v in graph.edges())
        connect_disconnected_group_components(graph, groups)
        after_edges = set(frozenset((u, v)) for u, v in graph.edges())

        added_edges = after_edges - before_edges

        assert len(added_edges) == 1
        added_edge = next(iter(added_edges))
        assert "C" in added_edge
        assert nx.is_connected(graph.subgraph(["A", "B", "C"]))

    def test_connect_disconnected_group_components_uses_weakest_internal_edge(self):
        """Synthetic bridges should use the weakest real internal edge in the group."""
        graph = nx.Graph()
        graph.add_edge("A", "B", weight=40.0)
        graph.add_edge("B", "C", weight=25.0)
        graph.add_node("D")
        groups = {"g1": {"A", "B", "C", "D"}}

        before_edges = set(frozenset((u, v)) for u, v in graph.edges())
        connect_disconnected_group_components(graph, groups)
        after_edges = set(frozenset((u, v)) for u, v in graph.edges())

        added_edges = after_edges - before_edges
        edges_touching_d = [edge for edge in added_edges if "D" in edge]

        assert len(added_edges) == 1
        assert len(edges_touching_d) == 1

        added_edge = next(iter(edges_touching_d))
        u, v = tuple(added_edge)
        assert graph[u][v]["weight"] == pytest.approx(25.0)

    def test_hierarchical_spectral_target_ge_groups_avoids_singleton_isolated_ba(self):
        """Spectral target>=groups should avoid forcing isolated C into a singleton cluster."""
        hierarchy_df = pd.DataFrame(
            {
                "ba": ["A", "B", "C", "D", "E"],
                "grp": ["g1", "g1", "g1", "g2", "g2"],
            }
        )
        transmission_df = pd.DataFrame(
            {
                "region_from": ["A", "D"],
                "region_to": ["B", "E"],
                "firm_ttc_mw": [100.0, 0.0],
            }
        )

        clusters = hierarchical_cluster(
            hierarchy_df,
            transmission_df,
            cluster_bas={"A", "B", "C", "D", "E"},
            grouping_column="grp",
            target_regions=3,
            method="spectral",
        )

        cluster_sets = [set(nodes) for nodes in clusters.values()]

        assert {"C"} not in cluster_sets

    def test_hierarchical_average_target_ge_groups_merges_isolated_ba_within_group(
        self,
    ):
        """Hierarchical-average should merge an isolated BA into its group once bridged."""
        hierarchy_df = pd.DataFrame(
            {
                "ba": ["A", "B", "C", "D", "E"],
                "grp": ["g1", "g1", "g1", "g2", "g2"],
            }
        )
        transmission_df = pd.DataFrame(
            {
                "region_from": ["A", "D"],
                "region_to": ["B", "E"],
                "firm_ttc_mw": [100.0, 20.0],
            }
        )

        clusters = hierarchical_cluster(
            hierarchy_df,
            transmission_df,
            cluster_bas={"A", "B", "C", "D", "E"},
            grouping_column="grp",
            target_regions=2,
            method="hierarchical-average",
        )

        cluster_sets = [set(nodes) for nodes in clusters.values()]

        assert {"C"} not in cluster_sets
        assert {"A", "B", "C"} in cluster_sets
        assert {"D", "E"} in cluster_sets

    def test_hierarchical_spectral_grouped_path_uses_constrained_subgraph(
        self, monkeypatch
    ):
        """Grouped spectral clustering should receive the constrained graph with synthetic links."""
        hierarchy_df = pd.DataFrame(
            {
                "ba": ["A", "B", "C", "D", "E"],
                "grp": ["g1", "g1", "g1", "g2", "g2"],
            }
        )
        transmission_df = pd.DataFrame(
            {
                "region_from": ["A", "D"],
                "region_to": ["B", "E"],
                "firm_ttc_mw": [100.0, 50.0],
            }
        )
        captured_subgraphs = {}

        def fake_agglomerative_cluster(graph, n_clusters, linkage):
            assert n_clusters == 3
            assert linkage == "average"
            return {
                0: {"A", "B"},
                1: {"C"},
                2: {"D", "E"},
            }

        def fake_spectral_cluster(graph, n_clusters):
            captured_subgraphs[frozenset(graph.nodes())] = graph.copy()
            nodes = sorted(graph.nodes())
            if n_clusters <= 1:
                return {0: set(nodes)}
            return {0: {nodes[0]}, 1: set(nodes[1:])}

        monkeypatch.setattr(
            clustering_algorithms,
            "agglomerative_cluster",
            fake_agglomerative_cluster,
        )
        monkeypatch.setattr(
            clustering_algorithms,
            "spectral_cluster",
            fake_spectral_cluster,
        )

        hierarchical_cluster(
            hierarchy_df,
            transmission_df,
            cluster_bas={"A", "B", "C", "D", "E"},
            grouping_column="grp",
            target_regions=3,
            method="spectral",
        )

        g1_subgraph = captured_subgraphs[frozenset({"A", "B", "C"})]

        assert any("C" in {u, v} for u, v in g1_subgraph.edges())


@pytest.fixture(scope="module")
def real_clustering_inputs():
    hierarchy_df = pd.read_csv(_DATA_DIR / "hierarchy.csv")
    transmission_df = pd.read_csv(_DATA_DIR / "transmission_capacity_reeds.csv")
    demand_df = pd.read_csv(_DATA_DIR / "reeds_annual_demand_2050.csv")

    demand_df["region"] = demand_df["region"].astype(str).str.lower()
    demand_weights = demand_df.groupby("region")["annual_demand_mwh"].mean().to_dict()
    cluster_bas = set(hierarchy_df["ba"])
    grouping_by_ba = {
        column: hierarchy_df.set_index("ba")[column].to_dict()
        for column in ["transgrp", "transreg"]
    }

    return hierarchy_df, transmission_df, cluster_bas, demand_weights, grouping_by_ba


@pytest.mark.parametrize(
    ("grouping_column", "target_regions", "method"),
    [
        ("transgrp", 18, "hierarchical-average"),
        ("transgrp", 18, "spectral"),
        ("transgrp", 26, "hierarchical-average"),
        ("transgrp", 26, "spectral"),
        ("transreg", 11, "hierarchical-average"),
        ("transreg", 11, "spectral"),
        ("transreg", 16, "hierarchical-average"),
        ("transreg", 16, "spectral"),
    ],
    ids=[
        "transgrp-18-hierarchical-average",
        "transgrp-18-spectral",
        "transgrp-26-hierarchical-average",
        "transgrp-26-spectral",
        "transreg-11-hierarchical-average",
        "transreg-11-spectral",
        "transreg-16-hierarchical-average",
        "transreg-16-spectral",
    ],
)
def test_real_data_hierarchical_cluster_preserves_grouping_boundaries(
    real_clustering_inputs, grouping_column, target_regions, method
):
    hierarchy_df, transmission_df, cluster_bas, _, grouping_by_ba = (
        real_clustering_inputs
    )

    clusters = hierarchical_cluster(
        hierarchy_df,
        transmission_df,
        cluster_bas=cluster_bas,
        grouping_column=grouping_column,
        target_regions=target_regions,
        method=method,
    )

    covered_bas = set().union(*clusters.values())

    assert covered_bas == cluster_bas

    for members in clusters.values():
        assert len({grouping_by_ba[grouping_column][ba] for ba in members}) == 1


def test_real_data_demand_weighted_hierarchical_average_keeps_p8_non_singleton(
    real_clustering_inputs,
):
    hierarchy_df, transmission_df, cluster_bas, demand_weights, grouping_by_ba = (
        real_clustering_inputs
    )

    clusters = hierarchical_cluster(
        hierarchy_df,
        transmission_df,
        cluster_bas=cluster_bas,
        grouping_column="transgrp",
        target_regions=26,
        method="hierarchical-average",
        demand_weights=demand_weights,
        demand_weight_method="demand-log",
    )

    p8_cluster = next(members for members in clusters.values() if "p8" in members)

    assert p8_cluster != {"p8"}
    assert {grouping_by_ba["transgrp"][ba] for ba in p8_cluster} == {
        "NorthernGrid_South"
    }
