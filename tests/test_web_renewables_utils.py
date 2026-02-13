import os
import sys

import numpy as np

# Add web directory to path to allow importing renewables_utils
sys.path.append(os.path.join(os.path.dirname(__file__), "../web"))

from renewables_utils import (
    optimize_bin_allocation,
    optimize_cluster_allocation,
    value_bin,
    weighted_quantile,
)


def test_optimize_bin_allocation_prioritizes_higher_spread_region():
    data = {
        "HighSpread": {
            "lcoe": np.array([0.0, 10.0, 20.0]),
            "capacity": np.array([100.0, 100.0, 100.0]),
        },
        "LowSpread": {
            "lcoe": np.array([10.0, 10.1, 10.2]),
            "capacity": np.array([100.0, 100.0, 100.0]),
        },
    }
    bins = optimize_bin_allocation(data, 4)
    assert bins["HighSpread"] > bins["LowSpread"]
    assert sum(bins.values()) == 4


def test_optimize_cluster_allocation_prioritizes_weighted_spread_when_cost_equal():
    region_lcoe_data = {
        "HighSpread": {
            "lcoe": np.array([0.0, 20.0]),
            "capacity": np.array([100.0, 100.0]),
        },
        "LowSpread": {
            "lcoe": np.array([9.0, 11.0]),
            "capacity": np.array([100.0, 100.0]),
        },
    }
    bins = {"HighSpread": 1, "LowSpread": 1}

    n_clusters = optimize_cluster_allocation(region_lcoe_data, bins, 3)

    assert n_clusters["HighSpread"] == 2
    assert n_clusters["LowSpread"] == 1


def test_optimize_cluster_allocation_respects_budget_cost_constraints():
    region_lcoe_data = {
        "HighSpreadHighCost": {
            "lcoe": np.array([0.0, 20.0]),
            "capacity": np.array([100.0, 100.0]),
        },
        "LowSpreadLowCost": {
            "lcoe": np.array([9.0, 11.0]),
            "capacity": np.array([100.0, 100.0]),
        },
    }
    bins = {"HighSpreadHighCost": 2, "LowSpreadLowCost": 1}

    # Minimum resources is 3. With target 4 there is only +1 resource left,
    # so the high-spread region (cost 2) cannot receive additional allocation.
    n_clusters = optimize_cluster_allocation(region_lcoe_data, bins, 4)

    assert n_clusters["HighSpreadHighCost"] == 1
    assert n_clusters["LowSpreadLowCost"] == 2


def test_value_bin_two_bins_split_at_weighted_median():
    obs = np.array([1, 2, 3, 4, 5])
    w = np.ones(5)
    b = value_bin(obs, w, 2)
    assert np.array_equal(b, np.array([1, 1, 2, 2, 2]))


def test_weighted_quantile_matches_expected_weighted_median():
    values = np.array([10.0, 20.0, 30.0])
    weights = np.array([1.0, 8.0, 1.0])
    q50 = weighted_quantile(values, [0.5], sample_weight=weights)[0]
    assert q50 == 20.0
