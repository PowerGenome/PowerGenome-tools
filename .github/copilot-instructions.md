# PowerGenome Web Copilot Instructions

## Scope
Web app in `/web` only. For detailed user documentation see [docs/web_app.md](../docs/web_app.md); for algorithms see [docs/algorithms.md](../docs/algorithms.md); for setup see [docs/development.md](../docs/development.md).

## Architecture
- **Browser-based**: Runs entirely in-browser via PyScript/Pyodide; no server needed.
- **Core files**: UI scaffolding in [index.html](../web/index.html), logic in [cluster_app.py](../web/cluster_app.py).
- **Data sources**: Loads GeoJSON, CSVs, and JSON from `web/data/` (BA boundaries, transmission, plants, ATB, fuels).
- **Wizard flow**: Multi-step wizard (Regions → Model Setup → Existing Plants → New Resources → Fuels → ESR Policies → Interconnection → Renewables Clustering → Export) guides users to build PowerGenome settings YAML files.

## Key Workflow Concepts
1. **Regions** are foundational—users select BAs and either cluster them algorithmically or manually assign them to model regions.
2. **Clustering** builds transmission graphs and uses various algorithms (spectral, Louvain, hierarchical) to aggregate BAs. See algorithms.md for details.
3. **Region naming** follows a hierarchical convention (state → NERC → interconnect), with counters to avoid duplicates.
4. **Plant clustering** aggregates existing generators within model regions using normalized technology names and k-means.
5. **Settings generation** produces YAML files from accumulated state across all wizard steps.

## UI Conventions
- BA IDs are lowercase; model region names use CamelCase.
- Grouping column values drive outline colors and "no cluster" exclusions.
- Selection states: selected BAs show blue, clustered regions show assigned colors.
- Transmission lines toggle on/off; weight reflects capacity between clustered regions.
- Box select mode enabled by default for multi-BA selection.

## Performance Considerations
- Pyodide is slower than native Python—avoid heavy loops where possible.
- Clustering algorithms run on graphs with ~140 nodes; O(n²) is acceptable in-browser.
- Color palettes are finite; many clusters may reuse colors.

## Gotchas
- **Data fetch errors**: If fetch returns HTML instead of CSV/JSON, check file paths and serving setup (first 100 chars shown in error).
- **Unknown BAs**: BAs in hierarchy but not in transmission data have edges skipped; plants mapped to missing BAs are dropped.
- **Auto-optimize clustering**: `min_regions`/`max_regions` apply *after* accounting for "no cluster" exclusions.
- **Manual region mode**: Switching modes or re-clustering resets downstream state (see State Dependency Management).

## PowerGenome Context
This web app helps users build settings for [PowerGenome](https://powergenome.github.io/PowerGenome/beta/), an open-source ETL tool that generates inputs for capacity expansion and production cost models. Settings files define model regions, plant groupings, resource costs, fuel prices, and other key parameters.

## Development Workflow
- **Testing**: ALWAYS use the `test-writer` subagent (via `runSubagent` tool) to write tests for new or changed functionality.
- **Documentation**: ALWAYS use the `docs-writer` subagent (via `runSubagent` tool) to update documentation when adding features.
- **Environment**: Use `uv` or local venv when running tests or scripts.
- **Debugging**: Use browser console for PyScript errors; `js.console.log()` for Python logging.
- **Local testing**: Serve `/web` via HTTP server (e.g., `python -m http.server 8000`). See development.md for details.

## State Dependency Management and Cascading Resets

The web app has many AppState attributes that depend on upstream selections. When upstream choices change, dependent state must be reset to maintain consistency.

### Current Dependencies

**Region-Dependent State** (depends on `region_aggregations`, `is_clustered`, `ba_to_region`):
- Plant clustering settings (`plant_cluster_settings`)
- Resource group files and assignments (`resource_group_files`, `resource_group_assignments`)
- Renewables clustering and all capacity attributes (`renewables_clusters`, `renewables_clusters_info`, `renewables_region_capacity_mw`, etc.)
- ESR zones and policies (`esr_zones`, `esr_map`, `esr_type_map`, `esr_policy_states`, `emission_policies_df`)

**Planning Year-Dependent State** (depends on model years from Model Setup):
- ESR policies (`esr_map`, `esr_type_map`, `esr_policy_states`, `emission_policies_df`)

### Reset Functions

Two helper functions manage cascading resets:

1. **`reset_region_dependent_state()`**: Resets all state that depends on region clustering. Called when:
   - Regions are cleared (`on_clear_selection`)
   - Regions are re-clustered (`on_run_clustering`)
   - Manual regions are cleared (`on_clear_manual_regions`)
   - Manual regions are finalized (`on_finalize_manual`)
   - Switching between clustering and manual modes (`on_region_mode_change`)

2. **`reset_planning_year_dependent_state()`**: Resets all state that depends on planning years. Called when:
   - Model years input changes (`on_model_years_change`)

### Adding New Dependencies

When adding new features to the app that depend on upstream state:

1. **Identify the dependency**: Determine which upstream selections your feature depends on (e.g., regions, planning years, etc.)

2. **Add reset logic**: Update the appropriate reset function to clear your new state attributes:
   ```python
   def reset_region_dependent_state():
       # ... existing resets ...
       state.your_new_attribute = None  # or appropriate reset value
   ```

3. **Document the dependency**: Update this section of copilot-instructions.md to list the new dependent attribute.

4. **Test the reset**: Verify that changing upstream selections properly resets your feature's state.

### Example

If adding a new feature that computes data based on model regions:

```python
# In AppState.__init__
self.my_region_data = None  # computed from regions

# In reset_region_dependent_state()
state.my_region_data = None  # reset when regions change

# In your computation function
def compute_my_region_data():
    if not state.region_aggregations:
        set_status("Please cluster regions first", "error")
        return
    # ... compute and set state.my_region_data ...
```

This pattern ensures consistency and prevents stale data from upstream changes affecting downstream features.
