"""
Renewables clustering utilities for PowerGenome web app.
Handles intelligent bin allocation based on LCOE variance minimization.
"""

import numpy as np


def weighted_quantile(
    values, quantiles, sample_weight=None, values_sorted=False, old_style=False
):
    """
    Simulate numpy.percentile but with weights.

    Parameters
    ----------
    values : np.array
        Data
    quantiles : float or list
        Quantiles to compute (0.0 - 1.0)
    sample_weight : np.array
        Weights for data
    values_sorted : bool
        If True, skip sorting of initial values
    old_style : bool
        If True, use old manual quantile calculation method

    Returns
    -------
    np.array
        Computed quantiles
    """
    values = np.array(values)
    quantiles = np.array(quantiles)
    if sample_weight is None:
        sample_weight = np.ones(len(values))
    sample_weight = np.array(sample_weight)

    if not values_sorted:
        sorter = np.argsort(values)
        values = values[sorter]
        sample_weight = sample_weight[sorter]

    weighted_quantiles = np.cumsum(sample_weight) - 0.5 * sample_weight
    if old_style:
        # To be convenient with numpy.percentile
        weighted_quantiles -= weighted_quantiles[0]
        weighted_quantiles /= weighted_quantiles[-1]
    else:
        weighted_quantiles /= np.sum(sample_weight)

    return np.interp(quantiles, weighted_quantiles, values)


def value_bin(values, weights, n_bins):
    """
    Bin data based on equal value bins (quantile cuts).

    Parameters
    ----------
    values : np.array
        Data values
    weights : np.array
        Weights (e.g. capacity)
    n_bins : int
        Number of bins

    Returns
    -------
    int or np.array
        Bin labels or single label
    """
    if n_bins == 1:
        return np.ones(len(values), dtype=int)

    qs = np.linspace(0, 1, n_bins + 1)
    bins = weighted_quantile(values, qs, sample_weight=weights)
    return np.digitize(values, bins[1:-1]) + 1


def agg_cluster_other(df, n_clusters, cluster_col="bin"):
    """
    Mimic aggregation clustering logic for other columns.
    For this utility, we just need to know how many clusters are targetted.
    This function is a placeholder if complex logic moves here later.
    Currently returns dummy labels since strict clustering logic is largely in JS/Pyodide side or unnecessary for bin count optimization.

    Returns
    -------
    np.array
        Cluster labels
    """
    return np.zeros(len(df), dtype=int)


def optimize_bin_allocation(region_lcoe_data, target_bins_total):
    """
    Allocate a total budget of bins across regions to minimize overall LCOE variance.

    Algorithm:
    Greedy allocation. Start with 1 bin per region.
    Iteratively add one bin to the region that offers the largest reduction in
    (Capacity * StdDev / k_bins).

    Heuristic for gain:
    Variance ~ 1/k^2? Standard Deviation ~ 1/k.
    We proxy the 'cost' of a region as TotalCapacity * StdDev.
    With k bins, the residual spread is approx (TotalCapacity * StdDev) / k.
    Gain from k -> k+1 is proportional to:
    (TotalCapacity * StdDev) * (1/k - 1/(k+1))

    Parameters
    ----------
    region_lcoe_data : dict
        {
            region: {
                "lcoe": np.array([...]),
                "capacity": np.array([...])
            }
        }
        Only includes regions with valid capacity > 0.
    target_bins_total : int
        Total number of bins to distribute

    Returns
    -------
    dict
        {region: n_bins}
    """
    regions = list(region_lcoe_data.keys())
    if not regions:
        return {}

    # Initialize 1 bin per region
    bins = {r: 1 for r in regions}
    current_total = len(regions)

    # Precompute constants describing the spread of each region
    # metric = Capacity * LCOE_StdDev
    # This represents the total 'variance mass' of the region.
    region_metrics = {}

    for r, data in region_lcoe_data.items():
        lcoe = data["lcoe"]
        cap = data["capacity"]
        total_cap = np.sum(cap)

        if total_cap == 0:
            region_metrics[r] = 0
            continue

        # Weighted standard deviation
        avg_lcoe = np.average(lcoe, weights=cap)
        variance = np.average((lcoe - avg_lcoe) ** 2, weights=cap)
        std_dev = np.sqrt(variance)

        # Metric: how much 'spread' is there to attack?
        # We weight by capacity because a 100MW region with high spread
        # is less important than a 10,000MW region with medium spread.
        region_metrics[r] = total_cap * std_dev

    # Greedy loop
    # If target is less than number of regions, we are already over budget (1 per region).
    # We don't remove bins (min 1).

    while current_total < target_bins_total:
        best_region = None
        best_gain = -1.0

        for r in regions:
            k = bins[r]
            metric = region_metrics[r]

            if metric == 0:
                continue

            # Estimated reduction in spread by going from k to k+1
            # Prop to 1/k - 1/(k+1)
            gain = metric * (1.0 / k - 1.0 / (k + 1))

            if gain > best_gain:
                best_gain = gain
                best_region = r

        if best_region is None:
            # No gains possible (all metrics 0?), just fill arbitrarily to reach target
            # or break if exhausted
            remainder = target_bins_total - current_total
            for i, r in enumerate(regions):
                if i < remainder:
                    bins[r] += 1
            break

        bins[best_region] += 1
        current_total += 1

    return bins


def optimize_cluster_allocation(region_lcoe_data, bins, target_total_resources):
    """
    Given fixed bins per region, optimize n_clusters per region to minimize LCOE variance.

    Parameters
    ----------
    region_lcoe_data : dict
        {r: { 'lcoe': [...], 'capacity': [...] }}
    bins : dict
        {r: number_of_bins} (fixed)
    target_total_resources : int
        Maximum total resources (sum of bins[r] * n_clusters[r])

    Returns
    -------
    dict
        {r: n_clusters} (integers >= 1)
    """
    regions = list(bins.keys())
    if not regions:
        return {}

    # Start with n_clusters = 1
    n_clusters = {r: 1 for r in regions}
    current_total = sum(bins[r] for r in regions)

    if current_total >= target_total_resources:
        return n_clusters

    # Precompute metrics
    region_metrics = {}
    for r, data in region_lcoe_data.items():
        lcoe = data["lcoe"]
        cap = data["capacity"]
        total_cap = np.sum(cap)
        if total_cap == 0:
            region_metrics[r] = 0
            continue

        avg_lcoe = np.average(lcoe, weights=cap)
        variance = np.average((lcoe - avg_lcoe) ** 2, weights=cap)
        std_dev = np.sqrt(variance)

        # Metric: Total spread to attack
        region_metrics[r] = total_cap * std_dev

    # Greedy loop
    # We add 1 to n_clusters[r].
    # Cost: bins[r] resources.
    # Gain: Reduction in spread.
    # Spread per resource is roughly proportional to 1 / (n_clusters * bins).
    # Since bins are fixed constant C_r, effective allocated resources is m = n * C.
    # Gain(n -> n+1) = Spread * (1/(n*C) - 1/((n+1)*C))
    #                = (Spread/C) * (1/n - 1/(n+1))

    while current_total < target_total_resources:
        best_region = None
        best_gain_per_cost = -1.0

        for r in regions:
            cost = bins[r]
            if current_total + cost > target_total_resources:
                continue

            n = n_clusters[r]
            metric = region_metrics[r]

            # Gain calculation
            # Factor out 1/C from spread metric? No, metrics[r] is purely capacity*stddev.
            # Spread reduction is proportional to 1/TotalResources.
            # Current Resources K = n * bins[r]. Next K' = (n+1) * bins[r].
            # Gain = Metric * (1/K - 1/K')

            K = n * bins[r]
            K_next = (n + 1) * bins[r]

            gain = metric * (1.0 / K - 1.0 / K_next)

            # Efficiency: Gain per resource cost
            efficiency = gain / cost

            if efficiency > best_gain_per_cost:
                best_gain_per_cost = efficiency
                best_region = r

        if best_region is None:
            break

        n_clusters[best_region] += 1
        current_total += bins[best_region]

    return n_clusters
