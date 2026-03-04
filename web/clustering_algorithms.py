"""
Clustering algorithm functions for the PowerGenome web app.

These pure functions implement network clustering algorithms used to group
Balancing Authorities (BAs) into model regions based on transmission topology.
"""

import networkx as nx
import numpy as np
from esr_utils import can_states_trade, split_bas_by_trading_zones


def standardize_features(matrix):
    """Standardize columns to zero mean, unit variance."""
    means = np.nanmean(matrix, axis=0)
    stds = np.nanstd(matrix, axis=0)
    stds = np.where(stds == 0, 1.0, stds)
    return (matrix - means) / stds


def run_kmeans_simple(features, k, weights=None, max_iter=40, seed=42):
    """Simple k-means implementation returning (inertia, centers, labels)."""
    rng = np.random.default_rng(seed)
    n_samples = features.shape[0]
    if k <= 0 or n_samples == 0:
        return 0.0, None, None

    # Initialize centers from samples
    init_idx = rng.choice(n_samples, size=min(k, n_samples), replace=False)
    centers = features[init_idx]

    labels = np.zeros(n_samples, dtype=int)

    for _ in range(max_iter):
        # Assign
        dists = np.linalg.norm(features[:, None, :] - centers[None, :, :], axis=2) ** 2
        labels = np.argmin(dists, axis=1)

        # Update
        new_centers = []
        for i in range(k):
            mask = labels == i
            if not np.any(mask):
                # Keep old center if empty
                new_centers.append(centers[i])
                continue

            cluster_points = features[mask]
            if weights is not None:
                cluster_weights = weights[mask][:, None]
                new_center = (cluster_points * cluster_weights).sum(
                    axis=0
                ) / cluster_weights.sum()
            else:
                new_center = cluster_points.mean(axis=0)
            new_centers.append(new_center)

        new_centers = np.vstack(new_centers)
        if np.allclose(new_centers, centers):
            centers = new_centers
            break
        centers = new_centers

    # Compute inertia
    inertia = 0.0
    for i in range(k):
        mask = labels == i
        if not np.any(mask):
            continue
        cluster_points = features[mask]
        cluster_center = centers[i]
        sq_dists = np.sum((cluster_points - cluster_center) ** 2, axis=1)
        if weights is not None:
            inertia += float((sq_dists * weights[mask]).sum())
        else:
            inertia += float(sq_dists.sum())

    return inertia, centers, labels


def build_transmission_graph(transmission_df, valid_bas):
    """Build undirected weighted graph from transmission data."""
    G = nx.Graph()

    for _, row in transmission_df.iterrows():
        region_from = row["region_from"]
        region_to = row["region_to"]
        capacity = row["firm_ttc_mw"]

        # Only include edges between valid BAs
        if region_from not in valid_bas or region_to not in valid_bas:
            continue

        if G.has_edge(region_from, region_to):
            G[region_from][region_to]["weight"] += capacity
        else:
            G.add_edge(region_from, region_to, weight=capacity)

    # Add isolated nodes for BAs with no connections
    for ba in valid_bas:
        if ba not in G:
            G.add_node(ba)

    return G


def get_regional_groups(hierarchy_df, grouping_column, valid_bas):
    """Map regional groups to their BAs."""
    groups = {}

    for _, row in hierarchy_df.iterrows():
        ba = row["ba"]
        if ba not in valid_bas:
            continue

        group = row[grouping_column]
        if group not in groups:
            groups[group] = set()
        groups[group].add(ba)

    return groups


def agglomerative_cluster(graph, n_clusters, linkage="sum"):
    """
    Perform agglomerative clustering on a graph.

    Linkage methods:
    - 'sum': Merge based on sum of edge weights (standard).
    - 'average': Merge based on average edge weight (sum / (size_a * size_b)).
    - 'max': Merge based on maximum single edge weight (single linkage).
    """
    nodes = list(graph.nodes())
    n = len(nodes)

    if n <= n_clusters:
        # Each node is its own cluster
        return {i: {node} for i, node in enumerate(nodes)}

    # Initialize: each node is its own cluster
    clusters = {i: {node} for i, node in enumerate(nodes)}
    node_to_cluster = {node: i for i, node in enumerate(nodes)}
    cluster_sizes = {i: 1 for i in range(n)}

    # Build initial inter-cluster weights
    cluster_weights = {}
    for u, v, data in graph.edges(data=True):
        c1, c2 = node_to_cluster[u], node_to_cluster[v]
        if c1 != c2:
            key = (min(c1, c2), max(c1, c2))
            weight = data.get("weight", 1.0)

            if linkage == "max":
                current = cluster_weights.get(key, -float("inf"))
                cluster_weights[key] = max(current, weight)
            else:
                cluster_weights[key] = cluster_weights.get(key, 0) + weight

    # Merge until we reach target number of clusters
    while len(clusters) > n_clusters:
        if not cluster_weights:
            break

        # Find the pair with maximum score
        if linkage == "average":

            def get_score(k):
                c1, c2 = k
                w = cluster_weights[k]
                return w / (cluster_sizes[c1] * cluster_sizes[c2])

            best_pair = max(cluster_weights.keys(), key=get_score)
        else:
            best_pair = max(cluster_weights.keys(), key=lambda k: cluster_weights[k])

        c1, c2 = best_pair

        # Merge c2 into c1
        clusters[c1].update(clusters[c2])
        cluster_sizes[c1] += cluster_sizes[c2]
        del cluster_sizes[c2]

        for node in clusters[c2]:
            node_to_cluster[node] = c1
        del clusters[c2]

        # Update cluster weights
        new_weights = {}
        keys_to_remove = []

        for (ca, cb), weight in cluster_weights.items():
            if ca == c2 or cb == c2:
                keys_to_remove.append((ca, cb))
                # Redirect to c1
                other = cb if ca == c2 else ca
                if other != c1:
                    new_key = (min(c1, other), max(c1, other))

                    # Get existing weight between c1 and other
                    w1 = cluster_weights.get(new_key)

                    if linkage == "max":
                        val1 = w1 if w1 is not None else -float("inf")
                        new_val = max(val1, weight)
                    else:
                        val1 = w1 if w1 is not None else 0
                        new_val = val1 + weight

                    new_weights[new_key] = new_val

        for key in keys_to_remove:
            del cluster_weights[key]

        for key, weight in new_weights.items():
            cluster_weights[key] = weight

    # Renumber clusters to be sequential
    result = {}
    for i, (cluster_id, nodes_set) in enumerate(clusters.items()):
        result[i] = nodes_set

    return result


def spectral_cluster(graph, n_clusters):
    """
    Perform spectral clustering on the graph using Normalized Laplacian.
    """
    nodes = list(graph.nodes())
    n = len(nodes)
    if n <= n_clusters:
        return {i: {node} for i, node in enumerate(nodes)}

    node_to_idx = {node: i for i, node in enumerate(nodes)}

    # Adjacency matrix
    A = np.zeros((n, n))
    for u, v, data in graph.edges(data=True):
        i, j = node_to_idx[u], node_to_idx[v]
        w = data.get("weight", 1.0)
        A[i, j] = w
        A[j, i] = w

    # Degree matrix
    d = np.sum(A, axis=1)

    # Normalized Laplacian: L_sym = I - D^-1/2 * A * D^-1/2
    d_inv_sqrt = np.power(d, -0.5, where=d > 0)
    d_inv_sqrt[d == 0] = 0
    D_inv_sqrt = np.diag(d_inv_sqrt)

    L = np.eye(n) - D_inv_sqrt @ A @ D_inv_sqrt

    # Eigen decomposition
    vals, vecs = np.linalg.eigh(L)

    # First k eigenvectors
    k = n_clusters
    X = vecs[:, :k]

    # Normalize rows
    rows_norm = np.linalg.norm(X, axis=1, keepdims=True)
    rows_norm[rows_norm == 0] = 1
    X_normalized = X / rows_norm

    # Run K-Means
    _, _, labels = run_kmeans_simple(X_normalized, k)

    # Convert labels back to clusters
    clusters = {}
    for idx, label in enumerate(labels):
        if label not in clusters:
            clusters[label] = set()
        clusters[label].add(nodes[idx])

    return clusters


def louvain_cluster(graph):
    """
    Perform Louvain community detection on a graph.

    Returns communities that maximize modularity.
    The number of clusters is determined automatically.

    This is used for auto-optimize mode.
    """
    if graph.number_of_nodes() == 0:
        return {}

    if graph.number_of_edges() == 0:
        # No edges - each node is its own community
        return {i: {node} for i, node in enumerate(graph.nodes())}

    try:
        # Use NetworkX's Louvain implementation
        communities = nx.community.louvain_communities(
            graph, weight="weight", resolution=1.0, seed=42
        )

        # Convert to our format: dict of label -> set of nodes
        return {i: set(community) for i, community in enumerate(communities)}
    except Exception:
        # Fallback: each node is its own cluster
        return {i: {node} for i, node in enumerate(graph.nodes())}


def hierarchical_cluster(
    hierarchy_df,
    transmission_df,
    cluster_bas,
    grouping_column,
    target_regions,
    method="hierarchical-sum",
    esr_rectable_df=None,
):
    """
    Hierarchical clustering that respects grouping column boundaries.

    Phase 1: Cluster BAs within each grouping column region
    Phase 2: Merge entire grouping column regions together if needed

    Grouping column regions are never split across model regions.

    If esr_rectable_df is provided, also removes edges between BAs in states
    that cannot trade (even transitively), ensuring ESR-compatible clustering.
    """
    # Parse method
    if method == "spectral":
        algo = "spectral"
        linkage = None
    elif method.startswith("hierarchical-"):
        algo = "hierarchical"
        linkage = method.split("-")[1]
    else:
        # Default
        algo = "hierarchical"
        linkage = "sum"

    # Group BAs by their grouping column value
    groups = {}
    for _, row in hierarchy_df[hierarchy_df["ba"].isin(cluster_bas)].iterrows():
        ba = row["ba"]
        group = row[grouping_column]
        if group not in groups:
            groups[group] = set()
        groups[group].add(ba)

    # Build BA to state mapping for ESR-compatible clustering
    ba_to_state = {}
    if esr_rectable_df is not None:
        for ba in cluster_bas:
            row = hierarchy_df[hierarchy_df["ba"] == ba]
            if not row.empty:
                ba_to_state[ba] = str(row.iloc[0]["st"]).lower()

        # Pre-split grouping column groups by trading zones
        # This ensures BAs in non-trading states are never in the same group
        new_groups = {}
        for group_name, group_bas in groups.items():
            trading_subgroups = split_bas_by_trading_zones(
                group_bas, hierarchy_df, esr_rectable_df
            )
            if len(trading_subgroups) == 1:
                # No split needed
                new_groups[group_name] = group_bas
            else:
                # Split into multiple subgroups with suffixed names
                for i, subgroup in enumerate(trading_subgroups):
                    new_groups[f"{group_name}_tz{i+1}"] = subgroup
        groups = new_groups

    num_groups = len(groups)

    # If target is >= number of groups, cluster within each group
    if target_regions >= num_groups:
        # Build map of BA to group
        ba_to_group = {}
        for group, bas in groups.items():
            for ba in bas:
                ba_to_group[ba] = group

        # Build full graph
        graph = build_transmission_graph(transmission_df, cluster_bas)

        # Remove edges between different groups to enforce group boundaries
        edges_to_remove = []
        for u, v in graph.edges():
            if ba_to_group.get(u) != ba_to_group.get(v):
                edges_to_remove.append((u, v))

        # Also remove edges between BAs in non-trading states if ESR-compatible
        if esr_rectable_df is not None:
            for u, v in graph.edges():
                if (u, v) in edges_to_remove:
                    continue  # Already marked for removal
                state_u = ba_to_state.get(u)
                state_v = ba_to_state.get(v)
                if state_u and state_v and state_u != state_v:
                    if not can_states_trade(state_u, state_v, esr_rectable_df):
                        edges_to_remove.append((u, v))

        graph.remove_edges_from(edges_to_remove)

        if algo == "spectral":
            # For spectral clustering, we must run it independently on each group
            # to avoid mixing eigenvectors of disconnected components.
            # We use agglomerative clustering to decide how many clusters each group gets.

            # 1. Get reference allocation using agglomerative clustering
            ref_clusters = agglomerative_cluster(
                graph, target_regions, linkage="average"
            )

            # 2. Count clusters per group
            group_allocations = {g: 0 for g in groups}
            for _, nodes in ref_clusters.items():
                # Pick a representative node to find the group
                # (All nodes in a cluster are in the same group because edges were removed)
                if not nodes:
                    continue
                rep_node = next(iter(nodes))
                grp = ba_to_group[rep_node]
                group_allocations[grp] += 1

            # 3. Run spectral clustering on each group independently
            final_clusters = {}
            cluster_id_counter = 0

            for group, count in group_allocations.items():
                if count == 0:
                    continue

                group_bas = groups[group]
                # Build subgraph for this group
                subgraph = build_transmission_graph(transmission_df, group_bas)

                # Run spectral on subgraph
                sub_clusters = spectral_cluster(subgraph, count)

                # Add to final result
                for _, nodes in sub_clusters.items():
                    final_clusters[cluster_id_counter] = nodes
                    cluster_id_counter += 1

            return final_clusters
        else:
            # Run agglomerative clustering on the whole graph
            # This prioritizes the strongest connections across all groups
            # instead of pre-allocating a fixed number of clusters per group
            return agglomerative_cluster(graph, target_regions, linkage=linkage)

    else:
        # target_regions < num_groups: need to merge entire groups
        # Phase 1: Each group becomes a single unit
        # Phase 2: Merge groups based on inter-group transmission capacity

        # Build a graph where nodes are groups and edges are total transmission between groups
        group_graph = nx.Graph()
        for group in groups:
            group_graph.add_node(group)

        # Calculate inter-group transmission capacity
        for _, row in transmission_df.iterrows():
            ba_from = row["region_from"]
            ba_to = row["region_to"]
            capacity = row["firm_ttc_mw"]

            # Find which groups these BAs belong to
            group_from = None
            group_to = None
            for group, bas in groups.items():
                if ba_from in bas:
                    group_from = group
                if ba_to in bas:
                    group_to = group

            if group_from and group_to and group_from != group_to:
                # If ESR-compatible, skip edges between BAs in non-trading states
                if esr_rectable_df is not None:
                    state_from = ba_to_state.get(ba_from)
                    state_to = ba_to_state.get(ba_to)
                    if state_from and state_to and state_from != state_to:
                        if not can_states_trade(state_from, state_to, esr_rectable_df):
                            continue  # Skip this edge

                if group_graph.has_edge(group_from, group_to):
                    group_graph[group_from][group_to]["weight"] += capacity
                else:
                    group_graph.add_edge(group_from, group_to, weight=capacity)

        # Cluster groups
        if algo == "spectral":
            group_clusters = spectral_cluster(group_graph, target_regions)
        else:
            group_clusters = agglomerative_cluster(
                group_graph, target_regions, linkage=linkage
            )

        # Convert group clusters to BA clusters
        all_clusters = {}
        cluster_id = 0

        for label, group_set in group_clusters.items():
            # Combine all BAs from all groups in this cluster
            combined_bas = set()
            for group in group_set:
                combined_bas.update(groups[group])
            all_clusters[cluster_id] = combined_bas
            cluster_id += 1

        return all_clusters


def calculate_modularity(graph, clusters):
    """
    Calculate the modularity score for a clustering result.

    Modularity measures how well a network is divided into communities.
    Higher values (closer to 1) indicate better clustering.
    Values typically range from -0.5 to 1.
    """
    if not clusters or graph.number_of_edges() == 0:
        return 0.0

    # Convert clusters dict to list of sets for networkx
    communities = [clusters[label] for label in sorted(clusters.keys())]

    # Filter to only include nodes that are in the graph
    graph_nodes = set(graph.nodes())
    communities = [c & graph_nodes for c in communities]
    communities = [c for c in communities if len(c) > 0]

    if not communities:
        return 0.0

    try:
        # Use networkx modularity function
        modularity = nx.community.modularity(graph, communities, weight="weight")
        return modularity
    except Exception:
        return 0.0


def find_optimal_clusters(
    hierarchy_df,
    transmission_df,
    cluster_bas,
    grouping_column,
    min_regions,
    max_regions,
):
    """
    Find the optimal clustering using Louvain community detection.

    Uses Louvain algorithm which directly maximizes modularity.
    The min_regions and max_regions are used to constrain the result:
    - If Louvain finds fewer clusters than min_regions, we don't split further
    - If Louvain finds more clusters than max_regions, we merge using agglomerative

    Returns (best_clusters, best_n, best_modularity, all_scores)
    """
    # Group BAs by their grouping column value
    groups = {}
    for _, row in hierarchy_df[hierarchy_df["ba"].isin(cluster_bas)].iterrows():
        ba = row["ba"]
        group = row[grouping_column]
        if group not in groups:
            groups[group] = set()
        groups[group].add(ba)

    # Build graph for the cluster BAs
    graph = build_transmission_graph(transmission_df, cluster_bas)

    # Use Louvain on within-group subgraphs, respecting grouping boundaries
    all_clusters = {}
    cluster_id = 0

    for group, group_bas in groups.items():
        if len(group_bas) == 1:
            all_clusters[cluster_id] = group_bas
            cluster_id += 1
        else:
            # Build subgraph for this group
            subgraph = build_transmission_graph(transmission_df, group_bas)
            # Use Louvain to find natural communities within this group
            sub_clusters = louvain_cluster(subgraph)

            for label, nodes in sub_clusters.items():
                all_clusters[cluster_id] = nodes
                cluster_id += 1

    num_clusters = len(all_clusters)

    # Capture optimal here
    optimal_clusters = dict(all_clusters)

    # If we have fewer clusters than min_regions, split using agglomerative clustering
    if num_clusters < min_regions:
        # We need to split clusters until we reach min_regions
        # We will greedily split the largest cluster (by number of BAs)

        # Convert to mutable dict
        current_clusters = dict(all_clusters)
        next_id = max(current_clusters.keys()) + 1 if current_clusters else 0

        while len(current_clusters) < min_regions:
            # Find cluster with most BAs that has at least 2 BAs
            candidates = [
                (cid, len(nodes))
                for cid, nodes in current_clusters.items()
                if len(nodes) > 1
            ]

            if not candidates:
                break  # Cannot split further

            # Pick largest
            cid_to_split, _ = max(candidates, key=lambda x: x[1])
            nodes_to_split = current_clusters[cid_to_split]

            # Build subgraph
            subgraph = build_transmission_graph(transmission_df, nodes_to_split)

            # Split into 2 using agglomerative clustering (average linkage for balanced splits)
            split_res = agglomerative_cluster(subgraph, 2, linkage="average")

            # Update clusters
            del current_clusters[cid_to_split]
            current_clusters[cid_to_split] = split_res[0]  # Reuse ID for one
            current_clusters[next_id] = split_res[1]  # New ID for other
            next_id += 1

        all_clusters = current_clusters
        num_clusters = len(all_clusters)

    # If we have more clusters than max_regions, merge using agglomerative
    if num_clusters > max_regions:
        # Build a graph of the current clusters
        cluster_graph = nx.Graph()
        for cid in all_clusters:
            cluster_graph.add_node(cid)

        # Add edges based on transmission between clusters
        for _, row in transmission_df.iterrows():
            ba_from = row["region_from"]
            ba_to = row["region_to"]
            capacity = row["firm_ttc_mw"]

            # Find which clusters these BAs belong to
            cluster_from = None
            cluster_to = None
            for cid, bas in all_clusters.items():
                if ba_from in bas:
                    cluster_from = cid
                if ba_to in bas:
                    cluster_to = cid

            if (
                cluster_from is not None
                and cluster_to is not None
                and cluster_from != cluster_to
            ):
                if cluster_graph.has_edge(cluster_from, cluster_to):
                    cluster_graph[cluster_from][cluster_to]["weight"] += capacity
                else:
                    cluster_graph.add_edge(cluster_from, cluster_to, weight=capacity)

        # Merge clusters down to max_regions
        merged = agglomerative_cluster(cluster_graph, max_regions)

        # Convert back to BA clusters
        new_clusters = {}
        for new_id, old_cluster_ids in merged.items():
            combined_bas = set()
            for old_id in old_cluster_ids:
                combined_bas.update(all_clusters[old_id])
            new_clusters[new_id] = combined_bas

        all_clusters = new_clusters

    # Calculate final modularity
    modularity = calculate_modularity(graph, all_clusters)
    num_clusters = len(all_clusters)

    return (
        all_clusters,
        num_clusters,
        modularity,
        {num_clusters: modularity},
        optimal_clusters,
    )


def generate_cluster_names(clusters, hierarchy_df):
    """Generate meaningful cluster names based on smallest containing grouping column.

    Naming rules allow only state plus one other grouping column:
    1) Use the state code when all BAs in the cluster share a state.
    2) Pick a single grouping column (other than state) that can name every
       remaining cluster; all non-state names must come from that one column.
    3) If no column satisfies (2), still pick one column (broadest available)
       and name every non-state cluster from that same column.

    Args:
        clusters: dict mapping label -> set of BA ids
        hierarchy_df: DataFrame with BA hierarchy info
    """
    # Grouping columns ordered from smallest to largest geographic scope
    GROUPING_HIERARCHY = [
        "st",
        "cendiv",
        "transgrp",
        "nercr",
        "transreg",
        "interconnect",
    ]

    cluster_names = {}
    name_counts = {}  # Track counts for each base name

    def common_value(nodes_set, column):
        vals = (
            hierarchy_df[hierarchy_df["ba"].isin(nodes_set)][column].dropna().unique()
        )
        return vals[0] if len(vals) == 1 else None

    # First pass: identify clusters that can use state names
    single_state_labels = set()
    for label, nodes in clusters.items():
        st_val = common_value(nodes, "st")
        if st_val:
            single_state_labels.add(label)

    # Choose one naming column for the remaining clusters
    candidate_columns = [col for col in GROUPING_HIERARCHY if col != "st"]
    naming_column = None

    for col in candidate_columns:
        if col not in hierarchy_df.columns:
            continue

        all_cover = True
        for label, nodes in clusters.items():
            if label in single_state_labels:
                continue
            if common_value(nodes, col) is None:
                all_cover = False
                break

        if all_cover:
            naming_column = col
            break

    # Fallback: pick the broadest available column
    if naming_column is None:
        for col in reversed(candidate_columns):
            if col in hierarchy_df.columns:
                naming_column = col
                break

    # Assign names
    for label, nodes in clusters.items():
        nodes_list = list(nodes)

        # Single BA or single-state cluster
        st_val = common_value(nodes, "st")
        if st_val:
            base_name = st_val if len(nodes_list) > 1 else st_val
        else:
            if naming_column and naming_column in hierarchy_df.columns:
                vals = (
                    hierarchy_df[hierarchy_df["ba"].isin(nodes)][naming_column]
                    .dropna()
                    .unique()
                )
                if len(vals) == 1:
                    base_name = vals[0]
                else:
                    # Still stick to one column; join values if mixed
                    base_name = "-".join(sorted([str(v) for v in vals])) or "Region"
            else:
                base_name = "Region"

        if base_name in name_counts:
            name_counts[base_name] += 1
            cluster_names[label] = f"{base_name}{name_counts[base_name]}"
        else:
            name_counts[base_name] = 1
            cluster_names[label] = f"{base_name}1"

    return cluster_names
