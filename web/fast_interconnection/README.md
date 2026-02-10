# Fast Interconnection Cost Estimator

A lightweight approximation of the full CPA-to-metro assignment algorithm, designed to run quickly in CI/CD environments (GitHub Actions) where the full algorithm's multi-hour runtime and 15-20GB RAM requirements are prohibitive.

## Overview

The full `MSA_Algorithms_List.py` algorithm assigns ~500k Candidate Project Areas (CPAs) to ~100 metros using iterative LCOE-based selection with dynamic curtailment. This fast estimator pre-computes lookup tables from existing case runs and uses a penalty-based heuristic to approximate the saturation dynamics.

## Approach

### Pre-computed Lookup Tables

From 24 existing case runs, we extract:

1. **`cpa_metro_candidates.parquet`** - For each CPA, the candidate metros sorted by interconnection cost
2. **`metro_saturation.parquet`** - Observed saturation patterns (fill ratios at assignment time)
3. **`metro_region_map.parquet`** - Metro-to-region mappings for each case
4. **`substation_metro_region.parquet`** - Substation assignments

### Dynamic LCOE Penalty Strategy

The key insight is that metros "fill up" during assignment, making later assignments more expensive due to curtailment. We approximate this with:

```
effective_lcoe = base_lcoe * (1 + penalty_factor * fill_ratio)
```

Where:
- `base_lcoe` = interconnection cost from lookup table
- `fill_ratio` = current MW assigned / total capacity
- `penalty_factor` = tunable parameter (see below)

This causes the algorithm to naturally spread assignments across metros rather than greedily filling the cheapest ones first.

### Zero-MW CPA Filtering

Zero-MW CPAs add noise and slow the assignment loop. We now drop them early in `fast_assign.py`, which improves runtime and reduces low-impact mismatches without affecting meaningful capacity allocation.

## Penalty Factor Tuning Results

We tested penalty factors across 6 test cases with varying numbers of regions:

| Penalty | Accuracy | Cost MAPE | Notes |
| ------- | -------- | --------- | ----- |
| 0.10 | 61.6% ± 18.1% | 20.1% | Too low - behaves like pure greedy |
| 0.25 | 66.8% ± 16.9% | 18.8% | |
| 0.50 | 72.4% ± 14.6% | 17.1% | |
| 1.00 | 79.0% ± 10.9% | 14.1% | |
| 2.00 | 84.5% ± 7.6% | 10.8% | |
| 3.00 | 86.8% ± 6.0% | 9.4% | |
| 6.00 | 89.3% ± 4.3% | 7.5% | |
| **10.00** | **90.4% ± 3.7%** | **6.8%** | **Recommended default** |
| 15.00 | 90.7% ± 3.5% | 6.5% | Diminishing returns |
| 20.00 | 91.0% ± 3.4% | 6.2% | Plateau reached |

### Accuracy by Number of Regions (at penalty=10.0)

| Regions | Solar Accuracy | Wind Accuracy |
| ------- | -------------- | ------------- |
| 100 (transgrp_100_spectral) | 95.7% | 94.3% |
| 75 (transgrp_esr_75_spectral) | 94.3% | 92.6% |
| 60 (transgrp_50_spectral) | 93.0% | 91.9% |
| 25 (nerc_esr_25_spectral) | 88.3% | 87.2% |
| 14 (transreg_14_spectral) | 85.8% | 84.7% |
| 7 (transreg_7_spectral) | 87.1% | 86.6% |

**Key finding**: More regions = better accuracy, likely because finer regional granularity better constrains assignment choices.

### Why Not Use Higher Penalties?

While accuracy continues to improve slightly up to penalty=20, there are downsides:

1. **Diminishing returns**: Going from 10→20 gains only 0.6% accuracy, while 3→10 gained 3.6%

2. **Loss of physical meaning**: At penalty=10, a half-full metro (fill_ratio=0.5) has its LCOE multiplied by 6x. At penalty=20, it's 11x. The original curtailment dynamics don't scale this aggressively.

3. **Overfitting risk**: Extreme penalties may overfit to training case patterns. Moderate penalties (10-15) are more likely to generalize to unseen regional configurations.

4. **Forced uniformity**: Very high penalties essentially force even distribution across all metros, regardless of cost differences. This could mask real cost advantages of certain metros.

**Recommendation**: Use penalty=10 as the default. It achieves 90%+ accuracy while staying in a physically reasonable range.

### Metro Size Allocation Effects (Population < 1M)

We tested how higher penalties shift capacity between large metros (population >= 1M or infinite sinks) and smaller metros. Results show **higher penalties increase the share of capacity assigned to large metros**, rather than reducing it:

Solar (large metro share):

- 0.5 → 33.4%
- 2.0 → 37.8%
- 5.0 → 39.7%
- 10.0 → 40.4%
- 15.0 → 40.7%
- 20.0 → 40.8%

Onshore wind (large metro share):

- 0.5 → 22.8%
- 2.0 → 26.4%
- 5.0 → 27.4%
- 10.0 → 27.7%
- 15.0 → 27.8%
- 20.0 → 27.8%

Metro coverage (count of metros used) stays constant across penalties. The penalty shifts **allocation**, not **coverage**.

## Key Findings

1. **Higher penalties improve accuracy** - Accuracy improves from 62% (penalty=0.1) to 91% (penalty=20), but plateaus around penalty=10-15.

2. **Penalty simulates curtailment dynamics** - The original algorithm increases curtailment as metros fill, which effectively raises LCOE. Our penalty factor approximates this behavior.

3. **Regional granularity matters** - Cases with more regions (50+) achieve 90%+ accuracy, while coarse regional aggregations (7-14 regions) top out around 85-88%.

4. **Error pattern**: Mismatches concentrate in the mid-LCOE range ($35-50/MWh). The cheapest sites (<$30) achieve 94-99% accuracy. Errors almost always have `predicted_lcoe < truth_lcoe` (we pick a cheaper option than the original algorithm).

5. **Pseudo-metro handling**: Regions without real metros get "pseudo-metros" with 100MW capacity caps to prevent over-allocation.

6. **Penalty shifts capacity to large metros**: Increasing penalties consistently increases the share of capacity assigned to large metros (pop >= 1M), but does not change how many metros are used.

## Usage

### Running Fast Assignment

```python
from fast_interconnection.fast_assign import fast_assign_cpas, load_settings

settings = load_settings("path/to/settings.yml")
assignments = fast_assign_cpas(
    candidates=candidates_df,
    saturation=saturation_df,
    settings=settings,
    metro_region_map=metro_region_map_df,
    substation_metro_region=substation_metro_region_df,
    strategy="dynamic_lcoe",
    lcoe_penalty_factor=10.0,  # Recommended
)
```

## Files

- `fast_assign.py` - Main assignment algorithm with multiple strategies
- `resource_groups.py` - Resource grouping utilities
- `data/` - Pre-computed lookup tables (parquet files)

## Performance

- **Runtime**: ~30 seconds per case (vs hours for full algorithm)
- **Memory**: ~2GB (vs 15-20GB for full algorithm)
- **Accuracy**: 85-95% depending on regional granularity and penalty factor

## Recommendations

1. Use `penalty_factor=10.0` as the default (or higher after further testing)
2. Use `strategy="dynamic_lcoe"`
3. Expect 90%+ accuracy for cases with 50+ regions
4. Expect 85-88% accuracy for cases with <25 regions
