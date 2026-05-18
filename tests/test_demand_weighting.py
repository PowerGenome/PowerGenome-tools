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

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Import the module under test directly (no PyScript environment needed)
# ---------------------------------------------------------------------------
web_dir = Path(__file__).parent.parent / "web"
sys.path.insert(0, str(web_dir))
from clustering_algorithms import build_transmission_graph, compute_demand_boost_factors


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

        assert boosts["tiny"] > boosts["moderate"], (
            "Tiny-demand BA should have a larger boost than moderate-demand BA"
        )

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
            "a": 1.0,      # very low
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

        assert w_boosted_ab > w_plain_ab, (
            "Edge A-B weight should increase when A has low demand"
        )

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
            "a": 1.0,      # tiny
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
