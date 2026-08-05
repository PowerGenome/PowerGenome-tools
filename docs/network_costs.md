# Network Cost Calculation

This page explains, in plain language, how the web app estimates network costs between model regions.

The calculation runs automatically after model regions are finalized in Step 1
(automatic clustering or manual finalization). If successful, results are stored
in app state and exported in Step 9 under `data/` with a descriptive generated
filename, such as `data/network_costs_10r_eastern-western_nercr.csv`.

For overall workflow context, see [Web Application](web_app.md).

## Plain-English Method

The core assumption is simple: each model region represents one or more major population centers, and the network cost between regions should reflect what it takes to connect those centers.

The method has two parts:

1. **Between-region connection**
    - For each region pair, identify plausible links between major population centers in the two regions.
    - Use the lowest-cost available cross-region link as the interregional component.

2. **Within-region backbone (intra-regional adder)**
    - A region can contain multiple major population centers.
    - The model builds a simplified internal network to represent the cost of connecting those centers to each other.
    - This internal cost is added on both ends of an interregional connection (start region and destination region).

So, the total for a route from region A to region B is:

- the best available A-to-B cross-region link
- plus A's internal connection adder
- plus B's internal connection adder

This produces consistent region-level values for:

- connection cost
- line losses
- distance proxy

The goal is not to replicate a full transmission expansion model. The goal is to provide a practical, transparent proxy that scales with user-defined model regions.

### Example

Suppose region **SouthEast** contains Atlanta and Charlotte, and region **MidAtlantic** contains Philadelphia and Washington D.C.

To estimate the cost of a new transmission path from SouthEast to MidAtlantic:

1. The app identifies Atlanta and Charlotte as the major population centers in SouthEast, and Philadelphia and Washington D.C. as those in MidAtlantic.
2. It checks available cross-region network links that touch any of those four cities and picks the cheapest one — say, the Charlotte-to-Washington D.C. corridor.
3. It then calculates SouthEast's internal adder: the estimated cost of moving power from Atlanta to Charlotte (so Atlanta can reach Charlotte, then onward to Washington D.C.).
4. It calculates MidAtlantic's internal adder: the cost of connecting Philadelphia and Washington D.C. internally.
5. The final interregional estimate is **Charlotte–Washington D.C. link cost + SouthEast internal adder + MidAtlantic internal adder**.

This approach means that a region's total network cost to reach any other region reflects both the direct cross-region link and the cost of aggregating power from across that region.

## Purpose

The network cost calculation produces one canonical row per unordered region pair with:

* Interregional transmission upgrade proxy cost
* Interregional line losses
* Interregional distance proxy
* Intraregional "adder" terms for both start and destination regions
* Total metrics used for scenario inputs and downstream analysis

This is a heuristic approximation intended to provide consistent, model-region-specific network cost signals after regional aggregation.

## How Major Population Centers Are Chosen

Each model region gets a set of "major" metro areas (MSAs):

- Default rule: keep MSAs above a population threshold (`pop_threshold`, default 1,000,000).
- Fallback rule: if no MSA clears the threshold, use the largest MSA in that region.

This ensures every region can participate in the network calculation, including less-populous regions.

## How Intra-Regional Adders Are Built

Within each model region, the app creates a simplified internal network among major MSAs:

1. Build a graph using valid within-region links between those major MSAs.
2. Find least-cost paths among major MSA pairs.
3. Build a minimum spanning tree to avoid over-counting redundant routes.
4. Compute population-weighted average internal cost/loss/distance.

Those weighted averages become that region's intra-regional adders.

If a region has too little internal structure (for example, only one major MSA or no valid paths), intra-regional adders remain zero.

## How Between-Region Links Are Chosen

For each region pair in the topology:

1. Find candidate links that connect major MSAs across the two regions.
2. Choose the single lowest-cost candidate.
3. Keep that link as the interregional component for the canonical pair row.

If no valid candidate exists for a region pair, that row is omitted.

## Total Metrics

For each output row, totals are the interregional value plus start-region and destination-region adders:

* `total_interconnect_cost_mw = interconnect_cost_mw + start_intraregion_cost_mw + dest_intraregion_cost_mw`
* `total_line_loss_frac = line_loss_frac + start_intraregion_loss_frac + dest_intraregion_loss_frac`
* `total_mw-km_per_mw = mw-km_per_mw + start_mw-km_per_mw + dest_mw-km_per_mw`

## What Gets Exported

When Step 9 Download All is used:

* `settings/*.yml` are always included.
* `extra_inputs/emission_policies.csv` is included only when ESR policies exist.
* `data/network_costs_<regions>r_<interconnections>_<grouping>.csv` is included
  only when network costs were successfully computed and stored in state. The
  generated filename is inserted into the ZIP under `data/`.

## Technical Reference

The sections below summarize data dependencies and implementation details for developers.

### Inputs and Dependencies

#### Required network data files

The calculator uses preprocessed files from:

* `web/data/network_data/nodes.csv`
* `web/data/network_data/edges.parquet`
* `web/data/network_data/topology_base.csv`

At runtime in the web app, these are fetched once and cached.

#### Required settings fields

The calculation depends on:

* `model_regions`
* `region_aggregations`

Region mapping is built from these fields. If no aggregation is provided, each listed region maps to itself.

#### Optional settings fields

* `network_lines`: Additional region pairs to force into topology consideration. Each pair is treated as an unordered connection and canonicalized to one output row.

#### Key parameter

* `pop_threshold` (default: 1,000,000): Threshold used to identify "major" MSAs for intraregional and interregional calculations.

### Implementation Sequence

The implementation is in `web/calc_network.py`, function `calculate_network_from_frames`.

1. Apply model-region mapping:
   * Map base regions in nodes, edges, and topology to model regions using `model_regions` + `region_aggregations`.
   * Drop rows that cannot be mapped.
2. Add optional topology pairs:
   * If `settings.network_lines` is present, map each pair to model regions.
   * Canonicalize the pair so only one row is kept per connection.
3. Select major MSAs in each region:
   * Major MSA set = MSAs with population >= `pop_threshold`.
   * Fallback: if none meet threshold, use the single largest-population MSA for that region.
4. Compute intraregional adders per region:
   * Build a within-region subgraph among edges whose endpoints are major MSAs in that same region.
   * For each pair of major MSAs, find the minimum-cost path (cost-weighted shortest path).
   * Create an MSA-level graph from those pairwise links.
   * Compute the minimum spanning tree (MST) on that MSA-level graph.
   * Compute population-weighted average cost/loss/distance over MST links to get intraregional adders.
5. Compute interregional component per topology pair:
   * For each canonical topology row (`start_region`, `dest_region`), evaluate candidate edges connecting major MSAs across the two regions.
   * Choose the single candidate edge with minimum `cost`.
   * Store the result under a deterministic region ordering so reverse duplicates are not emitted.
6. Assemble totals:
   * Total metrics are interregional value plus start-region intraregional adder plus destination-region intraregional adder.

## Output Schema

Output rows are canonical unordered region pairs. `start_region` and `dest_region` are stored in deterministic sorted order so the CSV contains only one row for each connection.

| Column | Description | Units |
| ------ | ----------- | ----- |
| `start_region` | Source model region label | n/a |
| `dest_region` | Destination model region label | n/a |
| `start_id` | Selected source substation ID for interregional link | integer ID |
| `dest_id` | Selected destination substation ID for interregional link | integer ID |
| `interconnect_cost_mw` | Cost of cheapest interregional candidate edge | $/MW |
| `line_loss_frac` | Loss fraction of cheapest interregional candidate edge | fraction |
| `mw-km_per_mw` | Distance proxy for interregional candidate edge | MW-km/MW |
| `start_intraregion_cost_mw` | Intraregional cost adder for source region | $/MW |
| `dest_intraregion_cost_mw` | Intraregional cost adder for destination region | $/MW |
| `start_intraregion_loss_frac` | Intraregional loss adder for source region | fraction |
| `dest_intraregion_loss_frac` | Intraregional loss adder for destination region | fraction |
| `start_mw-km_per_mw` | Intraregional distance adder for source region | MW-km/MW |
| `dest_mw-km_per_mw` | Intraregional distance adder for destination region | MW-km/MW |
| `total_interconnect_cost_mw` | Interregional + source adder + destination adder cost | $/MW |
| `total_line_loss_frac` | Interregional + source adder + destination adder loss | fraction |
| `total_mw-km_per_mw` | Interregional + source adder + destination adder distance | MW-km/MW |
| `total_interconnect_annuity_mw` | Annualized cost using 4.4% WACC and 60-year lifetime | $/MW/yr |
| `dollar_year` | Dollar-year for cost figures | year (2018) |

## Edge Cases and Fallback Behavior

* Regions without any MSA above the threshold use their largest MSA as fallback.
* If a region has fewer than two major MSAs, intraregional adders for that region remain zero.
* If no valid within-region paths exist among major MSAs, intraregional adders remain zero.
* If no candidate interregional edge exists for a topology pair, that pair is omitted from output.
* If mapped topology is empty, topology is inferred from observed cross-region edges and canonicalized to one row per pair.
* Unmapped rows (nodes/edges/topology) are dropped during region mapping.
* Topology self-loops are removed.

!!! note
    In the web app, if this calculation fails, a warning is shown and the app continues; no network cost CSV is exported for that session.

## Limitations and Caveats

* This is a heuristic approximation, not a full transmission expansion optimization.
* Results depend on the quality and assumptions of preprocessed network data (`nodes.csv`, `edges.parquet`, `topology_base.csv`).
* Interregional links are represented by a single cheapest candidate edge per canonical region pair.
* Intraregional adders are based on major-MSA filtering plus MST aggregation, which intentionally simplifies full network detail.
