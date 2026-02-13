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


def _agglomerative_1d_labels(values, weights, k):
    """Simple 1D agglomerative clustering labels (adjacent Ward-style merges)."""
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)

    valid = np.isfinite(values) & np.isfinite(weights)
    values = values[valid]
    weights = weights[valid]

    if values.size == 0:
        return np.array([], dtype=int)

    weights = np.maximum(weights, 1e-9)
    k = max(1, min(int(k), values.size))
    if k == 1:
        return np.zeros(values.size, dtype=int)

    order = np.argsort(values, kind="mergesort")
    sorted_vals = values[order]
    sorted_weights = weights[order]

    clusters = [
        {
            "positions": [idx],
            "weight": float(sorted_weights[idx]),
            "mean": float(sorted_vals[idx]),
        }
        for idx in range(sorted_vals.size)
    ]

    def merge_cost(left_cluster, right_cluster):
        left_w = left_cluster["weight"]
        right_w = right_cluster["weight"]
        denom = left_w + right_w
        if denom <= 0:
            return 0.0
        diff = left_cluster["mean"] - right_cluster["mean"]
        return (left_w * right_w / denom) * (diff**2)

    while len(clusters) > k:
        best_idx = 0
        best_cost = None
        for idx in range(len(clusters) - 1):
            cost = merge_cost(clusters[idx], clusters[idx + 1])
            if best_cost is None or cost < best_cost:
                best_cost = cost
                best_idx = idx

        left_cluster = clusters[best_idx]
        right_cluster = clusters[best_idx + 1]
        merged_weight = left_cluster["weight"] + right_cluster["weight"]
        if merged_weight <= 0:
            merged_mean = 0.5 * (left_cluster["mean"] + right_cluster["mean"])
        else:
            merged_mean = (
                left_cluster["mean"] * left_cluster["weight"]
                + right_cluster["mean"] * right_cluster["weight"]
            ) / merged_weight

        merged_cluster = {
            "positions": left_cluster["positions"] + right_cluster["positions"],
            "weight": merged_weight,
            "mean": float(merged_mean),
        }
        clusters[best_idx] = merged_cluster
        del clusters[best_idx + 1]

    labels_sorted = np.zeros(sorted_vals.size, dtype=int)
    for label, cluster_data in enumerate(clusters):
        for pos in cluster_data["positions"]:
            labels_sorted[pos] = label

    labels = np.zeros(sorted_vals.size, dtype=int)
    labels[order] = labels_sorted
    return labels


def _residual_std_after_bin_and_agg(lcoe, capacity, n_bins, n_clusters):
    """Weighted residual LCOE std after weighted-quantile bins + within-bin agglomerative clustering."""
    lcoe = np.asarray(lcoe, dtype=float)
    capacity = np.asarray(capacity, dtype=float)

    valid = np.isfinite(lcoe) & np.isfinite(capacity)
    lcoe = lcoe[valid]
    capacity = np.maximum(capacity[valid], 0.0)

    total_cap = float(np.sum(capacity))
    if lcoe.size == 0 or total_cap <= 0:
        return 0.0

    n_bins = max(1, int(n_bins))
    n_clusters = max(1, int(n_clusters))

    bin_labels = value_bin(lcoe, capacity, n_bins)
    residual_ss = 0.0

    for b in range(1, n_bins + 1):
        mask = bin_labels == b
        if not np.any(mask):
            continue

        vals_bin = lcoe[mask]
        cap_bin = capacity[mask]
        k_eff = max(1, min(n_clusters, vals_bin.size))
        labels = _agglomerative_1d_labels(vals_bin, cap_bin, k_eff)
        if labels.size == 0:
            continue

        for cluster_idx in np.unique(labels):
            c_mask = labels == cluster_idx
            c_vals = vals_bin[c_mask]
            c_caps = cap_bin[c_mask]
            c_total = float(np.sum(c_caps))
            if c_total <= 0:
                continue
            c_mean = float(np.average(c_vals, weights=c_caps))
            residual_ss += float(np.sum(c_caps * (c_vals - c_mean) ** 2))

    return float(np.sqrt(residual_ss / total_cap))


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

    std_cache = {}

    def get_std(region_name, n_clust):
        key = (region_name, int(n_clust))
        if key in std_cache:
            return std_cache[key]
        data = region_lcoe_data.get(region_name)
        if data is None:
            std_cache[key] = 0.0
            return 0.0
        lcoe = data.get("lcoe", np.array([]))
        cap = data.get("capacity", np.array([]))
        std_val = _residual_std_after_bin_and_agg(
            lcoe=lcoe,
            capacity=cap,
            n_bins=bins.get(region_name, 1),
            n_clusters=n_clust,
        )
        std_cache[key] = std_val
        return std_val

    region_total_cap = {}
    for r, data in region_lcoe_data.items():
        cap = np.asarray(data.get("capacity", np.array([])), dtype=float)
        cap = np.maximum(cap[np.isfinite(cap)], 0.0)
        region_total_cap[r] = float(np.sum(cap))

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
            total_cap = region_total_cap.get(r, 0.0)
            if total_cap <= 0:
                continue

            std_now = get_std(r, n)
            std_next = get_std(r, n + 1)
            gain = total_cap * max(0.0, std_now - std_next)

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
