"""
Self-contained network cost calculator for the webapp (PyScript-compatible).

Reads slim pre-built data files produced by create_data.py, applies
model-region aggregations from a settings dict, and computes inter-regional
transmission costs, line losses, and distances.

Dependencies: pandas, networkx  (no geopandas, no powergenome)

Usage as a module:
    from calc_network import calculate_network_from_frames
    result_df = calculate_network_from_frames(nodes_df, edges_df, topology_base_df, settings=settings_dict)

    Or using the file-based convenience wrapper:
    from calc_network import calculate_network
    result_df = calculate_network(data_dir="data/network_data", settings=settings_dict)
"""

import math
from itertools import combinations
from pathlib import Path

import networkx as nx
import pandas as pd

# ── Region mapping ─────────────────────────────────────────────────────────────


def build_base_to_model_map(settings: dict) -> dict:
    """Build a dict mapping each base IPM region to its model region.

    Uses ``settings["model_regions"]`` and ``settings["region_aggregations"]``.
    """
    model_regions = settings.get("model_regions", []) or []
    region_aggregations = settings.get("region_aggregations") or {}

    base_to_model: dict = {}
    if region_aggregations:
        for model_region in model_regions:
            members = region_aggregations.get(model_region)
            if isinstance(members, list) and members:
                for base in members:
                    base_to_model[base] = model_region
            else:
                base_to_model[model_region] = model_region
    else:
        for region in model_regions:
            base_to_model[region] = region

    return base_to_model


def apply_region_mapping(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    topology_base: pd.DataFrame,
    settings: dict | None,
) -> tuple:
    """Map base region labels to model region labels across all three DataFrames.

    When *settings* is ``None`` the base-region columns are used directly (i.e.
    every base region is its own model region).  When *settings* is provided,
    ``build_base_to_model_map`` is called to derive the mapping and any rows
    that cannot be mapped are dropped.

    Parameters
    ----------
    nodes : pd.DataFrame
        MSA-level node table.  Must contain ``msa_id`` and ``base_region``
        columns.  After mapping a ``region`` column is added; rows whose
        ``base_region`` is not present in the mapping are dropped.
    edges : pd.DataFrame
        Substation-level edge table.  Must contain ``u``, ``v``
        (MSA identifiers), ``u_base_region``, and ``v_base_region`` columns.
        After mapping ``region_u`` and ``region_v`` columns are added; edges
        where either endpoint cannot be mapped (or whose MSA no longer appears
        in the filtered node table) are dropped.
    topology_base : pd.DataFrame
        Region-pair connectivity table.  Must contain ``region_from_base``
        and ``region_to_base`` columns that enumerate which region pairs are
        considered adjacent.  After mapping the columns are renamed/replaced
        with ``start_region`` and ``dest_region``; pairs where either side
        cannot be mapped are dropped and duplicates are removed.
    settings : dict or None
        Settings dict passed to ``build_base_to_model_map``.  Expected keys
        are ``"model_regions"`` (list of model region names) and
        ``"region_aggregations"`` (dict mapping each model region to a list of
        base IPM regions).  Pass ``None`` to use base regions as-is.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]
        ``(nodes, edges, topology)`` — copies of the input DataFrames with the
        additional/renamed region columns described above.
    """
    nodes = nodes.copy()
    edges = edges.copy()
    topo = topology_base.copy()

    if settings is None:
        nodes["region"] = nodes["base_region"]
        edges["region_u"] = edges["u_base_region"]
        edges["region_v"] = edges["v_base_region"]
        topo = topo.rename(
            columns={
                "region_from_base": "start_region",
                "region_to_base": "dest_region",
            }
        )
        return nodes, edges, topo

    base_to_model = build_base_to_model_map(settings)

    nodes["region"] = nodes["base_region"].map(base_to_model)
    edges["region_u"] = edges["u_base_region"].map(base_to_model)
    edges["region_v"] = edges["v_base_region"].map(base_to_model)
    topo["start_region"] = topo["region_from_base"].map(base_to_model)
    topo["dest_region"] = topo["region_to_base"].map(base_to_model)

    nodes = nodes.dropna(subset=["region"]).copy()
    valid_msas = set(nodes["msa_id"])
    edges = edges[
        edges["u"].isin(valid_msas)
        & edges["v"].isin(valid_msas)
        & edges["region_u"].notna()
        & edges["region_v"].notna()
    ].copy()
    topo = topo.dropna(subset=["start_region", "dest_region"])
    topo = topo[["start_region", "dest_region"]].drop_duplicates().copy()

    return nodes, edges, topo


# ── Main calculation ───────────────────────────────────────────────────────────


def calculate_network_from_frames(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    topology_base: pd.DataFrame,
    settings: dict | None = None,
    output_path=None,
    pop_threshold: int = 1_000_000,
) -> pd.DataFrame:
    """Calculate inter-regional network costs from pre-loaded DataFrames.

    This is the core computation function.  Use this in environments where data
    files are already in memory (e.g. a browser-based PyScript app).

    Parameters
    ----------
    nodes : pd.DataFrame
        Node data with columns: msa_id (str), pop, base_region.
    edges : pd.DataFrame
        Edge data with columns: start_id, dest_id, u (msa_id), v (msa_id),
        u_base_region, v_base_region, cost ($/MW), dist (km), line_loss_frac.
    topology_base : pd.DataFrame
        Region-pair topology with columns: region_from_base, region_to_base.
    settings : dict or None
        Settings dict with ``model_regions`` and ``region_aggregations``.
        If None, base IPM regions are used as-is.
    output_path : str or Path or None
        If given, save the result CSV to this path.
    pop_threshold : int
        Population threshold for a metro to be used as an intraregional MST
        terminal and as a candidate endpoint for interregional lines.
        Default 1,000,000.

    Returns
    -------
    pd.DataFrame
        One row per directed region pair with cost, loss, and distance columns.
    """
    # ── Apply region mapping ───────────────────────────────────────────────────
    nodes, edges, topology = apply_region_mapping(nodes, edges, topology_base, settings)

    # Also handle extra topology pairs from settings (e.g. network_lines)
    extra_lines = settings.get("network_lines") if settings else None
    if extra_lines:
        base_to_model = build_base_to_model_map(settings) if settings else {}
        extra_rows = []
        for pair in extra_lines:
            r1 = base_to_model.get(pair[0], pair[0])
            r2 = base_to_model.get(pair[1], pair[1])
            if r1 and r2:
                extra_rows.append({"start_region": r1, "dest_region": r2})
                extra_rows.append({"start_region": r2, "dest_region": r1})
        if extra_rows:
            topology = pd.concat(
                [topology, pd.DataFrame(extra_rows)], ignore_index=True
            ).drop_duplicates()

    # Drop self-loops from topology
    topology = topology[topology["start_region"] != topology["dest_region"]].copy()

    # ── Identify major MSAs per region ─────────────────────────────────────────
    msa_pop = nodes.set_index("msa_id")["pop"].astype(float)

    major_by_region: dict[str, set] = {}
    for region, group in nodes.groupby("region"):
        large = set(group.loc[group["pop"] >= pop_threshold, "msa_id"].astype(str))
        if large:
            major_by_region[region] = large
        else:
            # Fallback: use the single largest MSA so the region isn't empty
            largest = group.sort_values("pop", ascending=False).iloc[0]["msa_id"]
            major_by_region[region] = {str(largest)}

    # ── Intraregional MST computation ──────────────────────────────────────────
    within_region_cost_adder: dict[str, float] = {}
    within_region_loss_adder: dict[str, float] = {}
    within_region_dist_adder: dict[str, float] = {}

    for region, major_msas in major_by_region.items():
        if len(major_msas) < 2:
            continue

        region_edges = edges[
            (edges["region_u"] == region)
            & (edges["region_v"] == region)
            & edges["u"].isin(major_msas)
            & edges["v"].isin(major_msas)
            & (edges["u"] != edges["v"])
        ].copy()

        if region_edges.empty:
            continue

        # Build substation-level graph
        graph = nx.Graph()
        for row in region_edges.itertuples(index=False):
            s = int(row.start_id)
            d = int(row.dest_id)
            c = float(row.cost)
            if not graph.has_edge(s, d) or c < graph[s][d]["cost"]:
                graph.add_edge(
                    s, d, cost=c, dist=float(row.dist), loss=float(row.line_loss_frac)
                )

        # Map substations → MSA (within-graph nodes only)
        sub_to_msa_local: dict[int, str] = {}
        for row in region_edges.itertuples(index=False):
            sub_to_msa_local[int(row.start_id)] = str(row.u)
            sub_to_msa_local[int(row.dest_id)] = str(row.v)

        major_subs_by_msa: dict[str, list] = {}
        for sub_id in graph.nodes():
            msa = sub_to_msa_local.get(sub_id)
            if msa in major_msas:
                major_subs_by_msa.setdefault(msa, []).append(sub_id)

        # Find best cost path between every pair of major MSAs
        msa_pair_data: dict[tuple, dict] = {}
        for msa_a, msa_b in combinations(sorted(major_msas), 2):
            subs_a = major_subs_by_msa.get(msa_a, [])
            subs_b = major_subs_by_msa.get(msa_b, [])
            best_cost = float("inf")
            best = None
            for sa in subs_a:
                for sb in subs_b:
                    if sa not in graph or sb not in graph:
                        continue
                    try:
                        path = nx.shortest_path(graph, sa, sb, weight="cost")
                        segs = list(zip(path[:-1], path[1:]))
                        c = sum(graph[u][v]["cost"] for u, v in segs)
                        if c < best_cost:
                            best_cost = c
                            best = {
                                "start_msa": msa_a,
                                "dest_msa": msa_b,
                                "cost": c,
                                "dist": sum(graph[u][v]["dist"] for u, v in segs),
                                "loss": 1.0
                                - math.prod(1.0 - graph[u][v]["loss"] for u, v in segs),
                            }
                    except (nx.NetworkXNoPath, nx.NodeNotFound):
                        continue
            if best:
                msa_pair_data[(msa_a, msa_b)] = best

        if not msa_pair_data:
            continue

        links = pd.DataFrame(list(msa_pair_data.values()))

        # Build MSA-level graph and find MST
        mst_g = nx.Graph()
        for row in links.itertuples(index=False):
            mst_g.add_edge(row.start_msa, row.dest_msa, weight=row.cost)
        mst = nx.minimum_spanning_tree(mst_g, weight="weight")
        mst_edges = {tuple(sorted(e)) for e in mst.edges()}

        links["mst_conn"] = links.apply(
            lambda r: tuple(sorted((r.start_msa, r.dest_msa))) in mst_edges, axis=1
        )
        mst_only = links[links["mst_conn"]].copy()

        if mst_only.empty:
            continue

        # Population-weighted adders (same logic as create_network_costs in run_msa_assignment.py)
        mst_only["start_pop"] = mst_only["start_msa"].map(msa_pop)
        mst_only["dest_pop"] = mst_only["dest_msa"].map(msa_pop)
        mst_only["combined_pop"] = mst_only["start_pop"] + mst_only["dest_pop"]
        denom = mst_only["combined_pop"].sum()
        if denom == 0:
            continue
        mst_only["frac"] = mst_only["combined_pop"] / denom

        within_region_cost_adder[region] = float(
            (mst_only["frac"] * mst_only["cost"]).sum()
        )
        within_region_loss_adder[region] = float(
            (mst_only["frac"] * mst_only["loss"]).sum()
        )
        within_region_dist_adder[region] = float(
            (mst_only["frac"] * mst_only["dist"]).sum()
        )

    # ── If topology is empty, derive from cross-region edges ──────────────────
    if topology.empty:
        cross = edges[edges["region_u"] != edges["region_v"]][
            ["region_u", "region_v"]
        ].drop_duplicates()
        if not cross.empty:
            fwd = cross.rename(
                columns={"region_u": "start_region", "region_v": "dest_region"}
            )
            rev = fwd.rename(
                columns={"start_region": "dest_region", "dest_region": "start_region"}
            )
            topology = pd.concat([fwd, rev], ignore_index=True).drop_duplicates()

    # ── Interregional cost assembly ────────────────────────────────────────────
    inter_rows = []
    for row in topology.itertuples(index=False):
        start_region = row.start_region
        dest_region = row.dest_region
        if start_region == dest_region:
            continue

        start_major = major_by_region.get(start_region, set())
        dest_major = major_by_region.get(dest_region, set())
        if not start_major or not dest_major:
            continue

        candidates = edges[
            (
                (edges["region_u"] == start_region)
                & (edges["region_v"] == dest_region)
                & edges["u"].isin(start_major)
                & edges["v"].isin(dest_major)
            )
            | (
                (edges["region_u"] == dest_region)
                & (edges["region_v"] == start_region)
                & edges["u"].isin(dest_major)
                & edges["v"].isin(start_major)
            )
        ]

        if candidates.empty:
            continue

        best = candidates.loc[candidates["cost"].idxmin()]

        # Normalise direction so start_region/dest_region match the topology row
        if best["region_u"] == start_region:
            s_id, d_id = int(best["start_id"]), int(best["dest_id"])
        else:
            s_id, d_id = int(best["dest_id"]), int(best["start_id"])

        cost_inter = float(best["cost"])
        loss_inter = float(best["line_loss_frac"])
        dist_inter = float(best["dist"])

        start_cost_add = within_region_cost_adder.get(start_region, 0.0)
        dest_cost_add = within_region_cost_adder.get(dest_region, 0.0)
        start_loss_add = within_region_loss_adder.get(start_region, 0.0)
        dest_loss_add = within_region_loss_adder.get(dest_region, 0.0)
        start_dist_add = within_region_dist_adder.get(start_region, 0.0)
        dest_dist_add = within_region_dist_adder.get(dest_region, 0.0)

        inter_rows.append(
            {
                "start_region": start_region,
                "dest_region": dest_region,
                "start_id": s_id,
                "dest_id": d_id,
                "interconnect_cost_mw": cost_inter,
                "line_loss_frac": loss_inter,
                "mw-km_per_mw": dist_inter,
                "start_intraregion_cost_mw": start_cost_add,
                "dest_intraregion_cost_mw": dest_cost_add,
                "start_intraregion_loss_frac": start_loss_add,
                "dest_intraregion_loss_frac": dest_loss_add,
                "start_mw-km_per_mw": start_dist_add,
                "dest_mw-km_per_mw": dest_dist_add,
                "total_interconnect_cost_mw": cost_inter
                + start_cost_add
                + dest_cost_add,
                "total_line_loss_frac": 1.0
                - (1.0 - loss_inter) * (1.0 - start_loss_add) * (1.0 - dest_loss_add),
                "total_mw-km_per_mw": dist_inter + start_dist_add + dest_dist_add,
            }
        )

    result = pd.DataFrame(inter_rows)

    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(out, index=False)

    return result


def calculate_network(
    data_dir,
    settings: dict | None = None,
    output_path=None,
    pop_threshold: int = 1_000_000,
) -> pd.DataFrame:
    """Calculate inter-regional network costs, loading data files from disk.

    Convenience wrapper around :func:`calculate_network_from_frames` that reads
    ``nodes.csv``, ``edges.parquet``, and ``topology_base.csv`` from *data_dir*
    before delegating to the core computation.

    Parameters
    ----------
    data_dir : str or Path
        Directory containing edges.parquet, nodes.csv, topology_base.csv.
    settings : dict or None
        Settings dict with ``model_regions`` and ``region_aggregations``.
        If None, base IPM regions are used as-is.
    output_path : str or Path or None
        If given, save the result CSV to this path.
    pop_threshold : int
        Population threshold for a metro to be used as an intraregional MST
        terminal and as a candidate endpoint for interregional lines.
        Default 1,000,000.

    Returns
    -------
    pd.DataFrame
        One row per directed region pair with cost, loss, and distance columns.
    """
    data_dir = Path(data_dir)
    nodes = pd.read_csv(data_dir / "nodes.csv", dtype={"msa_id": str})
    edges = pd.read_parquet(data_dir / "edges.parquet")
    topo_path = data_dir / "topology_base.csv"
    topology_base = (
        pd.read_csv(topo_path)
        if topo_path.exists()
        else pd.DataFrame(columns=["region_from_base", "region_to_base"])
    )
    return calculate_network_from_frames(
        nodes,
        edges,
        topology_base,
        settings=settings,
        output_path=output_path,
        pop_threshold=pop_threshold,
    )
