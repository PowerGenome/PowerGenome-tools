# Web Application

PowerGenome System Design is a comprehensive web-based interface for building complete PowerGenome settings files. It guides users through a step-by-step workflow to define model regions, configure resources, and export ready-to-use configuration files. The application runs entirely in the browser using PyScript—no installation required.

[Launch Web App](https://gschivley.github.io/PowerGenome-tools/web/){ .md-button .md-button--primary }

## Understanding Capacity Expansion Models

Before diving into the workflow, it's helpful to understand what capacity expansion models do and what data they need. Capacity expansion models answer questions like "What's the least-cost way to meet future electricity demand while achieving policy goals?" or "How much wind and solar should we build by 2035?"

These models require several key types of data:

**Geographic Zones**: Model regions define the geographic boundaries for your analysis. Each region has:

* **Hourly load profiles** - How much electricity is needed each hour of the year
* **Transmission constraints** - How much power can flow between regions
* **Existing resources** - What power plants already exist in each region
* **Renewable potential** - Where wind and solar resources can be built and at what cost

Why this matters: The way you define regions affects model complexity, run time, and how well the model captures real transmission constraints and policy boundaries.

**Cost Alignment**: Technology costs, fuel prices, and financial parameters must all be expressed in the same dollar-year (e.g., all costs in 2024 dollars). This ensures that when the model compares the cost of building solar in 2030 versus natural gas in 2040, it's making an apples-to-apples comparison.

**Timeseries Data**: All hourly data (demand, solar generation, wind generation) are stored in UTC (Coordinated Universal Time). This ensures consistency across regions and data sources. Shifting to local timezones is only done for visualization purposes (e.g., to show that peak solar generation occurs at local noon, not UTC noon).

**Planning Periods**: Each planning period has a start year and an end year. The model uses:

* **Final year** - For selecting demand forecasts and fuel prices (representing conditions at the end of the period)
* **Averaged capital costs** - Technology costs are averaged across all years in the period to represent the "average" cost of building during that timeframe

**Policy Constraints**: Policies like Renewable Portfolio Standards (RPS) or Clean Energy Standards (CES) are modeled as Energy Share Requirements (ESR). These specify that a certain fraction of electricity demand must be met by qualifying resources (e.g., "50% of demand from renewable energy by 2030").

**Resource Selection**: There is far more potential wind and solar capacity in the US than would ever be needed (especially in the western US). Including all possible sites would either:

* Make resources very large with high average costs (lumping expensive and cheap sites together), or
* Create too many resources (increasing model size and run time)

The solution is to filter to the cheapest sites needed to meet some multiple of regional demand. This gives the model a good selection of low-cost options without overwhelming it with every possible site.

## Overview

The guided workflow ensures you configure all necessary settings in the correct order:

1. **Regions** - Define model regions through automatic clustering or manual assignment of Balancing Authorities
2. **Model Setup** - Configure planning years and financial parameters
3. **Existing Plants** - Aggregate existing generators within regions
4. **New Resources** - Select new-build technologies and define custom resources
5. **Fuels** - Choose fuel price scenarios
6. **ESR Policies** - Configure Energy Share Requirements for state-level policies (optional)
7. **Interconnection** - Configure default interconnection costs, generate resource group files for wind and solar, and review how region-level network costs are produced for export
8. **Renewables Clustering** - Build renewables_clusters settings using demand shares and resource group LCOE tables
9. **Export** - Generate and download complete settings YAML files

Each step builds on the previous ones, with the Regions step being the foundation that determines how plants are aggregated and how model boundaries are defined.

## Step 1: Regions

The Regions step allows you to define model regions using two different approaches: **Automatic Clustering** (algorithm-driven) or **Manual Definition** (user-controlled). This is the foundation of your PowerGenome model configuration, determining how plants are aggregated and how model boundaries are defined.

### Why Regions Matter

Model regions are the fundamental geographic units in capacity expansion models. Each region defines:

* **Nodal demand** - The hourly electricity load that must be met in that region
* **Transmission constraints** - How much power can flow between this region and neighboring regions
* **Resource aggregation** - Which existing power plants are grouped together within the region
* **Renewable potential** - What wind and solar resources are available for new construction

The number and boundaries of regions create important trade-offs:

* **Fewer regions** (e.g., 5-10) = Faster model solve times, but less spatial detail and potentially oversimplified transmission constraints
* **More regions** (e.g., 30-50) = Better representation of transmission bottlenecks and policy boundaries, but longer solve times
* **Aligned with policy boundaries** = Easier to model state-level policies, but may not reflect transmission network structure
* **Aligned with transmission** = Better representation of grid operations, but may cross policy jurisdictions

There's no single "correct" number of regions—it depends on your analysis goals, computational resources, and whether you care more about policy boundaries or grid operations.

### Choosing Your Approach

The Regions step offers two modes for defining model regions:

| Mode | Best For | Approach |
| ---- | -------- | -------- |
| **Automatic Clustering** | Grid-operational coherence, exploratory analysis, transmission-based regions | Uses clustering algorithms and transmission capacity data to automatically group Balancing Authorities |
| **Manual Definition** | Policy-driven regions, specific jurisdictional boundaries, full control over regional structure | Create named regions and manually assign Balancing Authorities to them |

**Use Automatic Clustering when:**

* You want regions that reflect transmission network structure
* You're exploring different regional configurations
* You need balanced regions optimized for grid operations
* You want algorithmic optimization based on transmission capacity

**Use Manual Definition when:**

* You have predetermined regional boundaries (e.g., ISO/RTO territories, state groupings)
* Your analysis requires specific policy jurisdictions
* You need complete control over BA assignments
* You want to match regions from previous studies or external requirements

!!! tip
    Both modes produce identical outputs for downstream steps. Once regions are defined (either automatically or manually), plant clustering, transmission lines, and YAML generation work exactly the same way.

### Automatic Clustering

Automatic clustering uses transmission capacity between BAs to create aggregated regions. BAs are first clustered within their selected grouping (for example, **NERC - latest**), then groups are merged to reach the target number of regions.

#### How to Use Region Clustering

1. **Select BAs**: Click on regions in the map to select them. Use **Box Select** mode to drag and select multiple BAs at once.
2. **Choose grouping**: The *Grouping Column* determines how BAs are grouped for clustering (colored outlines show groups).
3. **Set target regions**: Enter how many final regions you want after clustering. Optionally enable *Auto-optimize* to find the best number of regions within a range.
4. **Exclude groups**: Check any groups in *Groups to Keep Unclustered* to keep them as individual BAs.
5. **Run clustering**: Click *Run Clustering* to generate the region aggregations.
6. **Export**: Copy or download the YAML output to use in PowerGenome.

!!! tip
    The clustering uses transmission capacity between BAs to create aggregated regions. BAs are first clustered within their selected grouping (for example, **NERC - latest**), then groups are merged to reach the target number of regions. The selected grouping affects the clustering results, so experiment with different options to see what works best for your case!

### Manual Definition

Manual definition mode allows you to create named regions and assign Balancing Authorities to them with complete control. This is ideal when you have predetermined regional boundaries or specific jurisdictional requirements.

#### How to Use Manual Region Definition

1. **Switch to Manual mode**: Click the **✏️ Manual** button at the top of the Regions step.
2. **Select BAs**: Click on regions in the map to select them. Use **Box Select** mode to drag and select multiple BAs at once. Selected BAs appear in the "Selected BAs (not yet assigned)" list.
3. **Create a region**: Enter a name in the "Region name" field and click **Add Region**. The new region appears in the "Manual Regions" list.
4. **Assign BAs to regions**:
   * Click on a region in the "Manual Regions" list to select it (highlighted in blue)
   * Select the BAs you want to assign (using the map)
   * Click **Assign Selected BAs** to add them to the selected region
5. **Review assignments**: Each region shows its BA count and the list of assigned BAs. Unassigned selected BAs remain in the "Selected BAs" list.
6. **Manage regions**: Use **Remove** buttons to delete individual regions (BAs become unassigned) or **Clear All Regions** to start over.
7. **Finalize**: Click **Finalize Manual Regions** when complete. This converts your manual regions to the format used by all downstream steps.

#### Manual Region Best Practices

**Naming conventions:**

* Use clear, descriptive names (e.g., "California", "PJM", "Southeast", "MISO_North")
* Avoid spaces in region names if possible (use underscores instead)
* Be consistent with capitalization and naming patterns

**BA assignments:**

* Each BA can only be assigned to one region (the app prevents duplicates automatically)
* All selected BAs should be assigned before finalizing
* Review the BA count for each region to ensure balanced regions if needed
* Use the map visualization to verify geographic coherence

**Workflow tips:**

* **Start with large regions first**: Define major territories before subdividing
* **Group by proximity**: Assign geographically adjacent BAs to minimize transmission line complexity
* **Check for orphans**: Before finalizing, verify the "Selected BAs" list is empty
* **Test incrementally**: Finalize early versions to test downstream steps, then return to refine

!!! warning
    Switching between Automatic and Manual modes clears the data from the other mode. If you want to preserve work, export your YAML before switching modes.

#### Pasting YAML Region Definitions

The Manual Definition mode includes a **YAML paste feature** that allows you to quickly load pre-defined region structures. This is especially useful for users who:

* Want to copy/paste regions from previous clustering runs
* Have predefined region structures they use regularly
* Want to modify automatically clustered regions and paste them back
* Need to quickly recreate regions from documentation or templates

**How to use it:**

1. In Manual Definition mode, locate the "Paste YAML Region Definition" text area
2. Paste your YAML region definition into the text box
3. Click **Load from YAML** to parse and load the regions
4. The regions will appear in the "Manual Regions" list
5. Optionally modify them further using the interactive map
6. Click **Finalize Manual Regions** when complete

!!! warning
    Loading YAML regions **clears any existing manual regions**. Make sure to export your current work before loading new YAML if you want to preserve it.

##### Supported YAML Formats

The YAML paste feature supports three different formats to accommodate different workflows:

**Format 1: Full Definition (with `model_regions` and `region_aggregations`)**

This is the complete format generated by the automatic clustering output:

```yaml
model_regions:
  - CA
  - AZ
  - Pacific_Northwest
region_aggregations:
  CA:
    - p8
    - p9
  AZ:
    - p27
    - p28
  Pacific_Northwest:
    - p4
    - p5
    - p38
```

**Format 2: Region Aggregations Only**

If you only have the region mappings (model_regions list is inferred from the keys):

```yaml
region_aggregations:
  CA:
    - p8
    - p9
  AZ:
    - p27
    - p28
  Pacific_Northwest:
    - p4
    - p5
    - p38
```

**Format 3: Direct Region Mappings**

The simplest format—just region names mapping directly to BA lists:

```yaml
CA:
  - p8
  - p9
AZ:
  - p27
  - p28
Pacific_Northwest:
  - p4
  - p5
  - p38
```

All three formats produce the same result and are fully interchangeable.

##### Modifying Clustered Regions Workflow

A powerful use case is to start with automatic clustering, then modify and reload the regions in manual mode:

1. **Run automatic clustering** in the Automatic Clustering mode (select BAs, configure settings, click "Run Clustering")
2. **Copy the YAML output** from the results section (use the copy button or select and copy the text)
3. **Modify the YAML** in your text editor:
   * Rename regions for clarity
   * Move BAs between regions
   * Split large regions or merge small ones
   * Remove regions or BAs you don't need
4. **Switch to Manual mode** by clicking the **✏️ Manual** button
5. **Paste the modified YAML** into the "Paste YAML Region Definition" text area
6. **Click "Load from YAML"** to import your modifications
7. **Further refine** using the interactive map if needed
8. **Finalize** when complete

This workflow combines the power of algorithmic clustering with the flexibility of manual control.

##### Validation and Error Checking

When you load YAML, the app performs several validation checks:

* **YAML syntax**: Must be valid YAML format (proper indentation, syntax)
* **Structure validation**: Regions must map to lists of BA codes
* **BA code validation**: All BA codes must exist in the dataset (e.g., `p8`, `p27`, etc.)
* **Duplicate checking**: Each BA can only appear in one region (no duplicates across regions)

If any validation fails, you'll see an error message explaining the problem. Common errors:

* **"Invalid BA codes in YAML"**: One or more BA codes don't exist in the dataset. Check for typos.
* **"Duplicate BAs found in multiple regions"**: A BA appears in more than one region. Each BA can only belong to one region.
* **"Invalid YAML format"**: Syntax error in the YAML (check indentation, colons, dashes).
* **"Region must map to a list of BAs"**: A region name doesn't have a list of BAs (check that you used `- ba_code` format).

##### Tips and Best Practices

**Reusing previous work:**

* Keep a library of YAML region definitions for common scenarios (state-by-state, ISO/RTO territories, custom aggregations)
* Export and save your YAML after finalizing regions for future reuse

**Iterative refinement:**

* Start with clustering, export YAML, make small modifications, reload, and test
* Build up complex region structures incrementally rather than all at once

**Version control:**

* Save different versions of your region YAML files with descriptive names
* Document why you chose specific regional structures in comments (YAML supports `#` comments)

**Combining with interactive selection:**

* After loading YAML, you can still use the map to make adjustments
* Select additional BAs and assign them to existing regions
* Create new regions and populate them interactively

#### When Manual Regions Are Especially Useful

**ISO/RTO boundaries**: If your analysis focuses on specific market operators, manually define regions matching their territories (e.g., CAISO, ERCOT, PJM, MISO, ISO-NE).

**State-level analysis**: Create one region per state or group states according to policy coalitions, regional compacts, or shared regulatory frameworks.

**Policy scenarios**: Model regions that align with multi-state policy initiatives (e.g., Western Climate Initiative, Regional Greenhouse Gas Initiative) or federal policy zones.

**Comparison studies**: Match regions from previous research, regulatory dockets, or industry reports to enable direct comparison of results.

**Hybrid approaches**: Start with automatic clustering to explore network structure, note the results, switch to manual mode, and use clustering insights to inform your manual assignments.

### Clustering Configuration Guide

Selecting the right grouping column and clustering algorithm is essential for producing model regions that are both computationally tractable and physically meaningful. This section explains how different choices affect your results.

### Choosing a Grouping Column

The **Grouping Column** determines how BAs are initially partitioned before clustering. This choice significantly influences region balance and grid operational fidelity.

| Option | Groups | Details | Strengths | Weaknesses | Best For |
| -------- | -------- | --------- | ----------- | ----------- | ---------- |
| **NERC - latest** (`nercr-latest`, Default) | NERC regions | Uses the latest NERC regional definitions in the hierarchy data | Current NERC boundaries; suitable default for new analyses | Results may differ from older studies that used previous definitions | New analyses and current planning studies |
| **NERC - legacy** (`nercr`) | NERC regions | Uses legacy NERC regional definitions in the hierarchy data | Supports comparisons with older studies | Not the current default | Reproducing older analyses |
| **Transmission Group** | 18 | ~7 BAs per group (median 6) | More balanced regions; aligns with ISOs/RTOs; better convergence | None significant | Most general analyses; grid-operational alignment |
| **Transmission Region** | 11 | ~12 BAs per group (median 11) | Still grid-aligned; moderate flexibility | Large NorthernGrid group in WECC can lead to unbalanced regions in national studies | Broader transmission boundaries |
| **Interconnect** | 3 | ~45 BAs per group (median 35) | None noted | Highly imbalanced regions; misses operational detail | Rarely used |
| **Census Division** | 9 | ~15 BAs per group (median 17) | Regional policy rollups | Doesn't reflect grid ops; splits transmission clusters | High-level regional summaries |
| **State** | 48 | ~3 BAs per group (median 2) | Aligns to state policy boundaries | Doesn't reflect grid ops; splits transmission clusters | State-level policy analysis |

**Recommendation**: Use **NERC - latest** (default) for new analyses. Use **NERC - legacy** when reproducing results based on the previous NERC definitions; use **Transmission Group** or **Transmission Region** when ISO/RTO-aligned boundaries are more appropriate.

### Choosing a Clustering Algorithm and Target

Once you've selected a grouping column, you need to decide how many regions you want and which algorithm to use. The interaction between these choices matters.

#### Auto-Optimize vs. Fixed Target

**Auto-Optimize Mode**:

* Finds the number of regions that **maximizes modularity** (a measure of how well the network divides into clusters).
* Set a range (e.g., Min: 20, Max: 40), and the tool searches within that range.
* **Advantage**: Respects the natural structure of the transmission network rather than imposing an arbitrary target.
* **Disadvantage**: The optimal modularity may not align with your computational budget or modeling goals.

**Best for**: Exploratory analysis, understanding network structure, or when you're unsure how many regions you need.

**Fixed Target Mode**:

* You specify exactly how many model regions you want.
* The clustering algorithm works to achieve that target.
* **Advantage**: Direct control; use this when your computational constraints or project requirements specify a fixed region count.
* **Disadvantage**: You may override the network's natural divisions, potentially creating inefficient aggregations or unbalanced regions.

**Best for**: Production models where region count is a hard constraint, or when you're matching a predetermined regional structure.

#### Algorithm Choices

| Algorithm | How It Works | Strengths | Weaknesses | Best For |
| ----------- | -------------- | ----------- | ----------- | ---------- |
| **Spectral Clustering** (Default) | Uses eigenvalues of transmission graph for dimensionality reduction | Balanced regions; finds cuts minimizing flow disruption; works well with grouping constraints | — | Default choice for most analyses |
| **Louvain Community Detection** | Iteratively merges to maximize modularity | Effective in auto-optimize; finds natural network structure; identifies cohesive communities | Less coherent in fixed-target mode | Auto-optimize mode; exploratory analysis |
| **Hierarchical (Average Linkage)** | Greedy merging by edge weight, penalizing large cluster merges | Produces balanced region sizes; predictable; deterministic | — | Fixed-target mode; balanced regions needed |
| **Hierarchical (Sum Linkage)** | Merges by total transmission capacity | Captures strong corridors | Creates imbalanced "snowballing" (few very large + many small clusters) | Rarely; corridor-focused analysis only |
| **Hierarchical (Max Linkage)** | Merges by single strongest edge | — | Produces imbalanced clusters; many isolated BAs | Generally not recommended |

### Recommended Approaches

**For a new analysis**:

1. Start with **NERC - latest** grouping.
2. Enable **Auto-Optimize** with a reasonable range (e.g., 15–40 regions).
3. Try switching to a fixed number of regions. Select **Spectral** or **Louvain** algorithm.
4. Review the resulting regions and modularity score. Experiment with different ranges to see how modularity changes.

**For balanced, fixed-region models**:

1. Use **NERC - latest** grouping.
2. Set a **Fixed Target** (e.g., 20 regions).
3. Select **Spectral**, **Louvain**, or **Hierarchical Clustering (Average Linkage)** to explore how region clustering varies.

**If auto-optimize produces too many or too few regions**:

1. Try **Transmission Region** grouping (fewer initial groups = fewer final regions).
2. Or adjust your auto-optimize range to be narrower.

**Key Takeaway**: Different combinations of grouping, algorithm, and target will produce different results. **Try multiple configurations** and evaluate whether the resulting regions make sense for your use case (balanced sizes, grid-operational coherence, computational feasibility). Your judgment about the appropriateness of the results is as important as the algorithmic quality metrics.

### ESR-Compatible Clustering

When modeling state-level energy policies like Renewable Portfolio Standards (RPS) or Clean Energy Standards (CES), you may want model regions that respect state trading boundaries. The **ESR-compatible clustering** option ensures that Balancing Authorities are only grouped together if their states can trade renewable energy credits (RECs) or clean energy credits with each other.

#### When to Enable ESR-Compatible Clustering

This option is **not required** for ESR policy modeling—the ESR step (Step 6) will automatically handle any incompatibilities. However, enabling it during clustering can produce cleaner results:

**Enable this option when:**

* You want to prevent model regions from being split later in the ESR step
* You prefer simpler region names without `_esr1`, `_esr2` suffixes
* Your analysis heavily depends on state trading boundaries

**Leave it disabled when:**

* You prioritize transmission-based clustering over trading constraints
* You're okay with some regions being split for ESR purposes
* You're not modeling state-level energy policies

#### How It Works

When ESR-compatible clustering is enabled, the algorithm checks whether BAs can be grouped based on their states' trading relationships:

1. **Trading relationships** are defined in `rectable.csv`, which specifies which states can trade REC/ESR credits with each other.
2. **Transitive trading** is applied: if State A can trade with State C, and State B can trade with State C, then BAs in States A and B can be in the same model region. This captures indirect trading relationships through common trading partners.
3. BAs in states that cannot trade (even transitively) will **never** be placed in the same model region, regardless of transmission capacity.

#### Impact on Results

ESR-compatible clustering adds constraints that may affect your clustering results:

* The algorithm may create **more regions** than your target number if trading boundaries require separation
* When this happens, you'll see a warning explaining why the target couldn't be achieved
* Regions will be smaller but will correctly represent policy trading zones

!!! tip
    If you skip ESR-compatible clustering and your model regions contain states that cannot trade with each other, the ESR step will automatically split those regions into sub-regions (e.g., `RegionName_esr1`, `RegionName_esr2`) for policy tracking purposes.

For detailed technical information about the clustering algorithms used in this step, see the [Algorithms documentation](algorithms.md).

## Step 2: Model Setup

The Model Setup step allows you to configure the temporal and financial parameters for your PowerGenome model.

### Configuration Options

* **Target USD Year**: The dollar year for all cost values (e.g., 2024)
* **UTC Offset**: Timezone offset for demand and weather data
* **Planning Periods**: A row-based editor with one planning period per row
* **Planning Year**: The end year for each planning period (the first row defaults to 2030)
* **Period Start**: The first investment year for each planning period (the first row defaults to the current calendar year)
* **Value of Lost Load (VOLL)**: The cost of non-served energy in $/MWh (default 5000), with a demand segments table for tiered curtailment penalties

### Understanding These Parameters

#### Target USD Year (Dollar-Year Alignment)

All costs in capacity expansion models must be expressed in the same dollar-year to ensure fair comparisons. For example, if you're comparing a solar plant that costs $1,000/kW in 2024 dollars versus a battery that costs $300/kWh in 2020 dollars, you need to adjust one of them to account for inflation.

PowerGenome handles this by:

* Converting all ATB technology costs to your target dollar-year
* Adjusting fuel price forecasts to the target dollar-year
* Ensuring that when the model compares options, it's truly comparing costs on an equal basis

Choose a recent year (e.g., 2024) to minimize the need for inflation adjustments.

#### UTC Offset (Timezone Considerations)

All timeseries data (hourly demand, solar generation profiles, wind generation profiles) are stored in UTC (Coordinated Universal Time). This ensures consistency across data sources and prevents timezone confusion when combining data from different regions.

The UTC offset is used primarily for visualization and debugging:

* It shifts timestamps when plotting results so that "noon" appears at local solar noon
* It helps you verify that peak solar generation aligns with expected local times
* It does **not** change how the model solves—the optimization always uses UTC timestamps

#### Planning Years and Planning Periods

Planning periods define the time windows your model will optimize. Each period has:

* **Period Start** - The start of the investment period in the Step 2 editor
* **Planning Year** - The end of the period in the Step 2 editor

For example, with Period Start = 2025 and Planning Year = 2030, you're modeling the 2025-2030 period.

In the web app, Step 2 starts with a single row that defaults to:

* **Period Start** = the current year
* **Planning Year** = 2030

Click the **+** button to add another planning period. Each added row derives its **Period Start** from the previous row's **Planning Year** plus one year. For example, if the first Planning Year is 2030, the next Period Start is automatically set to 2031. These are editable suggestions, so you can keep the auto-filled value or replace it with a custom Period Start in any row.

How PowerGenome uses these:

* **Demand and fuel prices** - Selected based on the Planning Year (representing conditions at the end of the period)
* **Capital costs** - Averaged across all years in the period (2025-2030 in the example) to represent the "typical" cost of building during that timeframe
* **Investment decisions** - The model decides what to build in each period to meet demand and policy goals

Multiple planning periods (e.g., 2030, 2035, 2040) allow the model to make sequential decisions, building infrastructure over time rather than all at once.

!!! note
    Each planning period row must include both a Period Start and a Planning Year. The app exports these as a `model_periods` list of `[period_start, planning_year]` pairs for PowerGenome.

#### Value of Lost Load (VOLL) and Demand Segments

The Value of Lost Load (VOLL) section, below Planning Periods, controls how the model prices non-served energy (demand curtailment).

* **VOLL ($/MWh)**: The value of lost load in USD/MWh. Defaults to 5000.
* **Demand segments table**: A row-based editor, like Planning Periods, where you can add or remove segments with the **+** and remove buttons. Segments are numbered automatically starting at 1. Each row has:
    * **Cost (fraction of VOLL)** - The cost of non-served energy for that segment, as a fraction of VOLL
    * **Max Curtailment (fraction of demand)** - The maximum fraction of demand in each zone and period that can be curtailed in that segment
    * **$/MWh** - A read-only computed column (VOLL × cost fraction) showing the implied curtailment price

The table defaults to four segments:

| Cost (fraction of VOLL) | Max Curtailment (fraction of demand) |
| --- | --- |
| 1.0 | 1.0 |
| 0.9 | 0.04 |
| 0.55 | 0.024 |
| 0.2 | 0.003 |

Using multiple segments lets the model curtail a small amount of demand at a lower penalty before allowing deeper, more expensive curtailment—approximating a demand response curve instead of a single flat penalty.

!!! note
    On export (Download All Settings ZIP), the VOLL value and segment rows are written to `extra_inputs/demand_segments_voll.csv` with the columns `Voll`, `Demand_Segment`, `Cost_of_Demand_Curtailment_per_MW`, `Max_Demand_Curtailment`, and `$/MWh` (the `Voll` column is populated only on the first row), matching PowerGenome's `Demand_data.csv` structure. The exported `settings/data.yml` already references this file via `demand_segments_fn: demand_segments_voll.csv`, and both the VOLL value and segment rows are saved and restored in the workflow state (`workflow_state.yml`) on export and import.

## Step 3: Existing Plants

The Existing Plants step allows you to cluster existing generators within each model region. This reduces model complexity while preserving the operational diversity of the fleet.

### Plant Clustering

1. **Select technologies to omit**: Choose which technology types to exclude from clustering (e.g., "All Other", "Solar thermal", "Flywheel" are pre-selected).
2. **Group similar technologies**: Check "Group similar technologies" to automatically group related tech types (Biomass and Other peaker by default). Uncheck to treat each technology individually.
3. **Customize groups (optional)**: Add new groups and move technologies between "Available" and "In selected group" lists to create custom groupings.
4. **Set cluster budget**: Specify the total number of clusters across all technologies and regions. The default is automatically set to 15% above the minimum required (one cluster per tech/region combination).
5. **Adjust thresholds (optional)**: Modify *Capacity Threshold* (MW) and *Heat-rate IQR Threshold* to control when generators are suggested for splitting.
6. **Run clustering**: Click *Run Plant Clustering* to generate cluster assignments.
7. **Review suggestions**: Above the Plant YAML output, click **Show** next to "Top candidates for more splits" to expand the section (it is hidden by default). Each row shows a tech/region group that could benefit from additional clusters, along with:
    * A **bubble chart** (to the right of each row) showing individual generators in the group. The x-axis is heat rate (MMBtu/MWh), all bubbles share the same vertical position, bubble size reflects plant capacity, and bubble color indicates cluster assignment.
    * A **Clusters** number input pre-filled with the current cluster count for that group. Change this value to immediately re-assign clusters — both the bubble chart and the Plant YAML update in real time.
8. **Export**: Copy or download the Plant YAML output.

!!! tip
    Plant clustering respects the model regions created in the Regions step. If you haven't run region clustering yet, plants are grouped by their BA. The system uses heat rate variability and capacity to suggest clusters; larger, more varied generator fleets get more clusters when budget allows.

## Step 4: New Resources

The New Resources step allows you to select new-build technologies from NREL's Annual Technology Baseline (ATB) and define custom modified resources.

### Pre-Populated Default Resources

When the app loads, Step 4 comes pre-populated with **6 ATB 2024 new-build resources** so you have a realistic starting point without needing to configure everything from scratch. All 6 defaults use the **"All (default)"** planning year, meaning they apply to every model year.

| Technology | Tech Detail | Cost Case |
| --- | --- | --- |
| NaturalGas | 2-on-1 Combined Cycle (H-Frame) | Moderate |
| NaturalGas | Combustion Turbine (F-Frame) | Moderate |
| LandbasedWind | Class3 | Moderate |
| UtilityPV | Class1 | Moderate |
| Utility-Scale Battery Storage | Lithium Ion | Moderate |
| Nuclear | Nuclear - Large | Moderate |

You can work with the defaults in any combination:

* **Keep them as-is** — accept the ATB 2024 Moderate cost assumptions with no changes.
* **Delete any you don't need** — remove technologies not relevant to your model using the delete button on each resource card.
* **Adjust a default's cost/performance** — select the same Technology / Tech Detail / Cost Case in the ATB picker above the list, use the **"Optional: Override cost/performance attributes"** panel there to tune CAPEX, O&M, heat rate, and more, then click **"Add New-build Resource"** to create a modified entry (you can delete the original default card if you no longer need it) (see [Standard ATB Resources](#standard-atb-resources)).
* **Reload a resource into the ATB picker** — select its row in the New Resources list to load those values back into the picker. This works with both mouse and keyboard: tab to the row, then press Enter or Space.
* **Add more resources** — use the dropdowns and **"Add New-build Resource"** button to include additional technologies alongside the defaults.
* **Add year-specific versions** — select a specific planning year from the Planning Year dropdown to layer year-by-year cost assumptions on top of the "All (default)" baseline (e.g., a lower-cost battery case for 2035).

### Planning Year Tagging

Each resource you add can optionally be tagged to a specific **planning year**. A **Planning Year** dropdown appears next to the "Add New-build Resource" and "Add Modified Resource" buttons. It is populated from the Model Years you entered in Step 2 (Model Setup), plus an "All (default)" option.

* **All (default)**: The resource applies to every planning year. This is the base configuration written to `resources.yml`.
* **Specific year** (e.g., 2030, 2035): The resource applies only to that year. Year-specific resources are embedded directly in `resources.yml` using PowerGenome's year-keyed settings format (see [Export](#step-9-export) for details).

Resources tagged to a specific year show a blue **year badge** (e.g., "2030") next to their name in the resource list.
For standard (non-modified) resources, any inline attribute overrides are also shown directly in that same resource row so adjustments are visible at a glance (for example, battery `variable_o_m_mwh` and `variable_o_m_mwh_in`, which are $0 in ATB but we default to $0.15/MWh to prevent simultaneous charge/discharge in models).

!!! tip "Required: always add an 'All (default)' version"
    If you add a year-specific resource but no "All (default)" version exists for the same technology/detail/case combination, a soft yellow warning appears: *"Consider also adding an 'All (default)' version…"*. The "All" version serves as the baseline; year-specific entries override or supplement it.

### Standard ATB Resources

If ATB data is available, use the dropdowns to select:

* **ATB Data Year** → **Technology** → **Tech Detail** → **Cost Case**
* Specify **Size (MW)** for each resource

#### CCS Disposal Cost (Conditional)

For CCS technologies, the ATB picker shows a **CCS disposal cost** input. This field only appears for CCS resources and defaults to 20 (USD per tonne). This value should be inflation-adjusted to the desired dollar-year -- it will not be modified by PowerGenome.

#### Optional: Override Cost/Performance Attributes

Below the Size field, you can expand the **"Optional: Override cost/performance attributes"** panel to modify specific cost and performance parameters for the selected ATB resource. This panel allows you to adjust values from NREL's ATB database using either absolute values or relative adjustments.

##### Available Attributes

The following attributes can be modified:

* **CAPEX ($/MW)** - Capital expenditure per megawatt
* **CAPEX Storage ($/MWh)** - Capital expenditure per megawatt-hour (storage technologies only)
* **Heat Rate (MMBtu/MWh)** - Thermal efficiency for fuel-consuming technologies
* **Fixed O&M ($/MW-yr)** - Fixed operations and maintenance costs
* **Variable O&M ($/MWh)** - Variable operations and maintenance costs
* **Variable O&M In ($/MWh)** - Variable O&M for charging (storage technologies only)
* **WACC real (0–1)** - Weighted average cost of capital (real)

!!! note "Storage-Specific Fields"
    **CAPEX Storage** and **Variable O&M In** are only applicable to battery and other storage technologies. For batteries, CAPEX Storage represents the cost per MWh of energy capacity, while Variable O&M In represents the cost per MWh of charging.

##### Two Types of Modifications

You can modify ATB attributes in two ways:

**1. Absolute Values** - Set a specific value directly:

* Enter a plain number (e.g., `500000` for CAPEX)
* The resource will use this exact value instead of the ATB default
* Use this when you know the specific parameter value you want

**2. Relative Changes** - Apply an operation to the ATB default:

* Use the format `operator:value` (e.g., `mul:1.1`, `add:100000`)
* The operation is applied to the ATB default value for that resource
* Available operators:
  * `add` - Add a value (e.g., `add:100000` increases by $100k/MW)
  * `sub` - Subtract a value (e.g., `sub:50000` decreases by $50k/MW)
  * `mul` - Multiply by a factor (e.g., `mul:1.1` increases by 10%)
  * `truediv` - Divide by a factor (e.g., `truediv:2` cuts in half)

##### Examples

**Increasing costs by 10%:**

```
CAPEX ($/MW): mul:1.1
Fixed O&M ($/MW-yr): mul:1.1
```

**Setting absolute CAPEX and adjusting O&M:**

```
CAPEX ($/MW): 850000
Variable O&M ($/MWh): add:2.5
```

**Lowering battery costs:**

```
CAPEX ($/MW): mul:0.85
CAPEX Storage ($/MWh): truediv:1.2
Variable O&M In ($/MWh): 0.10
```

**Improving heat rate:**

```
Heat Rate (MMBtu/MWh): mul:0.95
```

##### How to Use

1. The panel is collapsed by default—click to expand it
2. Enter values for the attributes you want to override
3. Use either absolute values or relative operations (with `operator:value` format)
4. Leave fields blank to use the ATB default values
5. Click **"Add New-build Resource"** to add the resource

##### Generated Output

All new resources added to your model automatically generate entries in the `resource_modifiers` section of `resources.yml`. The web app includes the following for every new resource:

* **technology** - The technology name you selected or entered
* **tech_detail** - The technology detail/variant you selected or entered
* **Automatic defaults** - For certain resource types (see [Automatic Battery Storage Defaults](#automatic-battery-storage-defaults))
* **Attribute overrides** - Any cost/performance adjustments you specified

This format matches PowerGenome's expected structure for modifying ATB resources.

**Example output in resources.yml:**

```yaml
resource_modifiers:
  utility_scale_battery_storage_lithium_ion:
    technology: Utility-Scale Battery Storage
    tech_detail: Lithium Ion
    Var_OM_Cost_per_MWh: 0.15                # Automatic default
    Var_OM_Cost_per_MWh_In: 0.15            # Automatic default
  solar_utc_mid:
    technology: Solar Photovoltaic
    tech_detail: Utility-scale
    capex_mw: [mul, 0.95]                    # User-specified override
  wind_mid:
    technology: Onshore Wind
    tech_detail: ""
    capex_mw: 1250000                        # User-specified override
```

In this example:

* The Utility Battery entries automatically include variable O&M defaults
* The Solar entry includes a user-specified relative adjustment (multiply CAPEX by 0.95)
* The Wind entry includes a user-specified absolute CAPEX value
* Attribute overrides can be either absolute values (e.g., `1250000`) or relative operations (e.g., `[add, 1000]`, `[mul, 1.1]`)

##### Automatic Battery Storage Defaults

Battery storage and other storage technologies automatically receive default variable O&M cost values. This simplifies the initial configuration of storage resources and ensures reasonable default operating costs.

**Detection Logic**:
The web app identifies battery storage resources when:

* The **technology** name contains "battery" or "storage" (case-insensitive), **AND**
* The **tech_detail** contains "lithium" (case-insensitive, e.g., "Lithium Ion")

**Automatic Default Values**:
When a battery storage resource is detected, the following defaults are automatically:

1. **Displayed in the UI** - The override attributes panel shows the default values ($0.15/MWh)
2. **Added to the output** - These values are included in the generated `resource_modifiers` section

The defaults are:

* `Var_OM_Cost_per_MWh: 0.15` - Variable O&M cost per unit of energy discharged ($0.15/MWh)
* `Var_OM_Cost_per_MWh_In: 0.15` - Variable O&M cost per unit of energy charged ($0.15/MWh)

**Example**: If you add "Utility-Scale Battery Storage" with detail "Lithium Ion", these default values are automatically populated in the override panel, and they're included in the output even if you don't modify them.

**Overriding Defaults**: If you want different values for battery storage:

1. Expand the **"Optional: Override cost/performance attributes"** panel
2. The default values ($0.15/MWh) will be automatically filled in the **Variable O&M ($/MWh)** and **Variable O&M In ($/MWh)** fields
3. Simply clear these fields and enter your desired values, or modify the displayed defaults directly
4. Your values will override the automatic defaults in the generated output

##### Appearance in Scenario Files

**Base Resources (resources.yml)**:
All "All (default)" new resources generate entries in `resource_modifiers` within `resources.yml`.

**Year-Specific Resources (year-keyed in resources.yml)**:
If you tag any resources to specific planning years (see [Planning Year Tagging](#planning-year-tagging)), the Export step embeds year-keyed values directly in `resources.yml` using PowerGenome's native year-keyed settings format.

For `resource_modifiers`, the top-level structure remains keyed by resource name. When a modifier field differs by year, the year keys are nested under that field (not at the top level of `resource_modifiers`).

If a modifier field only has year-specific values and no base/default value, the export includes a neutral default (for example, `[mul, 1]`) so the field is valid in non-explicit years.

For each planning year, the generated settings include:

* All "All (default)" entries under the `default` key (base configuration)
* All year-specific entries under their respective year key

This allows you to adjust resource definitions across different planning years while maintaining a consistent base configuration. For example, you might tag a cheaper battery cost case to a specific future year to reflect anticipated technology cost reductions.

##### Comparing Standard and Modified Resources

The web app supports two ways to define new resources:

| Feature | Purpose | When to Use | Output Sections |
|---------|---------|-------------|-----------------|
| **Standard New Resources** | Use ATB technologies with optional attribute adjustments | Most cases—when ATB covers your needs and you just want to tune parameters or accept defaults | `resource_modifiers` in resources.yml (resource-keyed; year keys nested only on changed fields) |
| **Modified New Resources** | Create entirely new resource types with custom fuels | Only when you need completely new technologies, custom fuel types (hydrogen, ammonia), or entirely different resource definitions | `modified_new_resources` (and related updates to `fuels.yml` and `resource_tags.yml`) |

**Use Standard Resources when:**

* You want an ATB technology and are satisfied with defaults
* You want to adjust cost/performance parameters (CAPEX, O&M, heat rate, etc.)
* You need regional cost multipliers (e.g., "solar costs 15% more in this region")
* You want to test sensitivity to specific cost/performance assumptions
* The technology exists in ATB with the fuel type and characteristics you need

**Use Modified Resources when:**

* You need a completely new fuel type (e.g., hydrogen, ammonia)
* You're modeling a technology not in ATB
* You need to change multiple fundamental characteristics (technology name, fuel type, resource class)
* You want a completely different resource definition

!!! tip
    **Standard resources are simpler and faster to configure.** They use the ATB structure and automatically generate `resource_modifiers` entries. Only use Modified Resources when you genuinely need to create something outside ATB's scope.

#### Manual Entry

You can also paste resources manually, one per line:

    Technology | Tech Detail | Cost Case | Size

### Modified New Resources

Create custom resources by modifying existing ATB entries:

1. **Base Resource**: Select an ATB technology/detail/cost case/size as the starting point
2. **New Identity**: Define new technology/detail names for this modified resource (cost case is automatically copied from the base resource)
3. **Fuel Type**: Choose a standard fuel (coal, natural gas, distillate, uranium) or define a new fuel with price and emissions
4. **Resource Class Tag**: Select THERM, VRE, STOR, or other resource class
5. **Commit Tag**: For thermal resources, optionally add to the "Commit" tag
6. **Attribute Modifiers**: Add custom YAML attributes to override base resource properties

!!! note
    Modified resources are added to the `modified_new_resources` section of `resources.yml`. They also automatically update related settings in `fuels.yml` and `resource_tags.yml`.

    For **simple attribute adjustments without identity changes**, these are also reflected in `resource_modifiers` (the same section generated for standard resources). This means you can use either standard resources or modified resources to adjust parameters—choose based on your complexity needs.

## Step 5: Fuels

The Fuels step allows you to select fuel price scenarios for each fuel type in your model.

### Fuel Price Scenarios

Select a **Fuel Data Year** from the dropdown, which loads scenarios from [PowerGenome-data fuel_prices.csv](https://github.com/gschivley/PowerGenome-data/blob/main/data/fuel_prices.csv).

For each fuel (coal, natural gas, distillate, uranium), choose a price scenario:

* **Default**: Coal uses `no_111d` if available (otherwise `reference`); other fuels use `reference`
* **Available scenarios**: Varies by fuel data year and fuel type

!!! tip
    If fuel scenario options can't be loaded (offline use), the app falls back to `reference` for all fuels.

## Step 6: ESR Policies

The ESR Policies step allows you to configure Energy Share Requirements for state-level policies like Renewable Portfolio Standards (RPS) and Clean Energy Standards (CES). This step is optional—uncheck "Include ESR policies" if your analysis doesn't require policy constraints.

### What is ESR?

**ESR (Energy Share Requirement)** is a generalized framework for modeling state-level clean energy policies. It encompasses:

* **RPS (Renewable Portfolio Standard)** - Policies that require a certain percentage of electricity to come from renewable sources (wind, solar, hydro, geothermal, biomass)
* **CES (Clean Energy Standard)** - Policies that require a certain percentage of electricity to come from low-carbon sources (renewables plus nuclear, CCS-equipped plants, etc.)

For example, a state might have:

* An RPS requiring 50% renewable energy by 2030
* A CES requiring 80% clean energy by 2040

These policies create constraints in the capacity expansion model: the model must select enough qualifying resources to meet the policy requirements, even if slightly more expensive options would otherwise be preferred.

### Why Use ESR Instead of Separate RPS/CES?

Different states have different policy designs, eligibility rules, and trading arrangements. Some states:

* Can trade renewable energy credits (RECs) with neighboring states
* Have policies that overlap or transition from RPS to CES over time
* Apply different multipliers or carve-outs for specific technologies

The ESR framework provides a flexible way to model all these variations using a consistent structure. Instead of hardcoding specific policy types, it allows you to define:

* Which states share trading zones
* What fraction of demand must be met by qualifying resources
* Which technologies qualify for each policy type

For detailed technical information about how ESR zones are created and calculated, see the [ESR Policies documentation](esr_policies.md).

### How ESR Zones Work

The app automatically groups your model regions into ESR zones based on:

1. **State trading rules**: Which states can trade RECs/clean energy credits with each other
2. **Interconnect boundaries**: Trading is limited to within the same interconnect (Eastern, Western, or ERCOT)
3. **Transitive relationships**: If State A trades with C and State B trades with C, all three are in the same zone

### ESR Configuration Options

* **Include ESR policies**: Uncheck to skip ESR generation entirely
* **Include RPS constraints**: Generate RPS columns in emission_policies.csv
* **Include CES constraints**: Generate CES columns in emission_policies.csv

### Multi-Zone Regions

If a model region contains states from different trading zones, the app handles this by assigning values to **multiple ESR columns**. Each column represents the weighted contribution from states in that zone, using population as a proxy for demand.

!!! tip
    To avoid multi-zone regions, enable **ESR-compatible clustering** in Step 1. This ensures BAs are only clustered together if their states can trade within the same ESR zone.

### Generated Output

The ESR step generates `emission_policies.csv` containing:

* **ESR zone columns** (ESR_1, ESR_2, ...): Each zone gets an RPS column and a CES column
* **Policy requirements**: Fraction of demand that must be met by qualifying resources
* **Technology tags**: Updated in `resource_tags.yml` to mark qualifying technologies

## Step 7: Interconnection

The Interconnection step allows you to configure interconnection costs for your model resources and generate the resource group files needed for wind and solar resources.

After model regions are finalized in Step 1, the app also computes a region-level network cost table used for export. See [Network Cost Calculation](network_costs.md) for the full method and output schema.

### Default Interconnection Cost

The default interconnection cost applies to resources like natural gas, batteries, or nuclear that don't have specific geospatial locations. This value is written to the `interconnect_capex_mw` setting in `resources.yml`.

* **Default value**: $100,000/MW
* **Editable**: You can adjust this value based on your analysis needs
* **Documentation**: See [PowerGenome's interconnection cost documentation](https://powergenome.github.io/PowerGenome/beta/reference/settings/new-build/#interconnection-costs) for advanced configuration options

### Geospatial Resource Files

For wind and solar resources with specific geospatial locations and generation profiles tied to those locations, interconnection costs are calculated dynamically based on how regions are aggregated. These calculations use the fast interconnection algorithm to estimate the cost of connecting each candidate project area (CPA) to the transmission network.

The results are saved in parquet files that include:

<!-- markdownlint-disable MD033 -->
<ul>
  <li><strong>Interconnection costs</strong> - Estimated cost to connect each site to the grid</li>
  <li>
    <strong>LCOE (Levelized Cost of Energy)</strong> - Approximate total cost based on:
    <ul>
      <li>ATB resource costs (capital and O&amp;M)</li>
      <li>Interconnection costs</li>
      <li>Average capacity factor of the resource at that location</li>
    </ul>
  </li>
</ul>
<!-- markdownlint-enable MD033 -->

These parquet files, along with accompanying JSON files containing generation profiles and site metadata, are referred to as **"Resource Group" files**. While this terminology may not be widely familiar, it's the term used within PowerGenome to refer to these geospatially-explicit renewable resource datasets.

**Why LCOE matters**: The LCOE values calculated in this step are used in the "Renewables" tab (Step 8) to select the lowest-cost sites to include as resources in each model region. By filtering to the cheapest sites needed to meet some multiple of regional demand, the model gets a good selection of cost-effective options without being overwhelmed by every possible site.

### Inputs

* **Region name**: Label used in the output filenames (for example, `solar_lcoe_<name>.parquet`)

### Generated Output

* **Solar LCOE parquet**: Regional LCOE table for solar resources
* **Onshore wind LCOE parquet**: Regional LCOE table for onshore wind resources
* **Resource group JSON**: Resource group metadata (profiles + site maps)
* **Network costs table (in app state)**: Region-to-region cost/loss/distance metrics, exported in Step 9 when available

Downloads from this tab are provided as a single ZIP archive.

### Upload Pre-calculated LCOE Files

If you have LCOE files from a previous session, you can upload them directly instead of re-running the resource-group generation. This lets you skip the expensive CPA interconnection calculation when the underlying data has not changed.

**When to use this:** You ran Step 7 in a prior session, downloaded the parquet outputs, and now want to proceed to Step 8 (Renewables Clustering) without regenerating those files.

**Supported formats:** `.parquet` or `.csv`

**Required columns:** `region`, `cpa_mw`, `cf`, `lcoe`

Upload separate files for wind and solar as needed. The resource group JSON files are **not** required for this workflow — only the LCOE parquet/CSV files are needed.

!!! note
    If you generate new files in the current session using the **Generate Resource Group Files** button, those in-session results take priority over any uploaded files.

## Step 8: Renewables Clustering

The Renewables Clustering step builds `renewables_clusters` settings for wind and solar using regional demand shares and the resource group LCOE tables generated in Step 7.

### Why Filter Renewable Resources?

The United States has far more potential wind and solar capacity than would ever be needed in any realistic scenario, especially in the western states. If you included every possible wind and solar site in a capacity expansion model, you would face a dilemma:

**Option 1: Few large resources with high average costs**

* Lump all sites in a region into a handful of large resources (e.g., one "West Texas Wind" resource with 500 GW)
* Each resource has high average cost because it mixes cheap and expensive sites
* The model can't selectively choose the best sites—it's all or nothing

**Option 2: Many small resources with accurate costs**

* Represent each site individually (e.g., thousands of 100 MW wind resources)
* Costs are accurate, but the model becomes too large to solve efficiently
* Computational time becomes prohibitive for iterative analysis

**The Solution: Smart Filtering**

PowerGenome filters to include only the cheapest sites needed to meet some multiple of regional demand (e.g., 100% or 200% of annual demand). This approach:

* Gives the model a good selection of low-cost options in each region
* Keeps the number of resources manageable
* Ensures the model has more capacity available than it would likely select, so it's not artificially constrained
* Filters out very expensive sites that would never be cost-competitive anyway

The result is a model that can make realistic decisions about which wind and solar resources to build, without being overwhelmed by thousands of unnecessary options.

### Inputs

* **Annual demand CSV**: The app loads `web/data/reeds_annual_demand_2050.csv` at startup. It must contain `region`, `weather_year`, and `annual_demand_mwh` columns. The `region` values are BA IDs (lowercase). The app averages demand across weather years for each BA, then sums BA demand within each model region.
* **Wind share (%)** and **Solar share (%)**: Percent of each region's annual demand used to select wind and solar resources. Shares apply to every model region. For example, 100% means "include enough wind capacity to generate 100% of annual regional demand at average capacity factor."
* **Average resource size (MW/resource)**: Used to convert selected wind/solar capacity into suggested budget counts. Defaults are 2,000 MW/resource for wind and 5,000 MW/resource for solar. These values are editable, and suggested budgets refresh from the updated inputs.
* **Wind/Solar budget counts**: Users can edit wind and solar budget totals directly. Leaving a budget blank uses the suggested value.

### Advanced Renewables Workflow

The **Advanced** section in Step 8 is collapsed by default to keep the base workflow simple. Click the **Advanced** header/caret to expand it and show interactive regional controls.

When expanded, the app shows side-by-side wind and solar model-region maps. Region shading is sequential: darker shading means a larger fraction of that region's available capacity is currently included in the clustering selection, while lighter shading means a smaller included fraction.

**Click behavior:**

* Maps are click-driven for supply-curve inspection.
* Clicking a region opens the full supply curve panel below the maps for that selected region/technology.
* The panel includes a regional capacity slider for that selected region/technology.

**Overrides and recalculation:**

* Capacity overrides let users increase or decrease included capacity per region within the available regional range.
* Override values are session-only and autosaved in app state while the page remains open.
* **Recalculate** reruns the full Step 8 computation using current overrides and refreshes the generated `renewables_clusters` output used by Step 9 export.

### How Renewables Clusters Are Computed

1. **Prerequisites**: Requires region aggregations from Step 1 and LCOE tables for onshore wind and solar. These can come from either generating resource group files in Step 7 during the current session, or from LCOE files uploaded via the **Upload Pre-calculated LCOE Files** section in Step 7.
2. **Regional demand target**: For each region, compute target energy as $\text{region demand} \times \text{share}$.
3. **Low-cost resource selection**: Within each region, sort candidates by LCOE and select enough capacity to meet the target energy (using $\text{annual MWh} = \text{capacity} \times \text{CF} \times 8760$). The highest selected LCOE becomes the region's filter threshold.
4. **Capacity totals**: For each region, include all resources with LCOE below the threshold and sum their capacity to build a regional capacity total and LCOE range.
5. **Suggested budgets**: For each technology, the app sums selected regional capacity and converts it to a suggested total budget using average resource size assumptions (default wind: 2,000 MW/resource, solar: 5,000 MW/resource). Suggested budgets update when demand shares or average resource sizes change.
6. **Budget floor and allocation**: During compute, each technology budget is checked against the minimum feasible budget (at least one cluster per required bin). If a user-entered budget is below this minimum, it is automatically raised. Any extra budget above the minimum is allocated using agglomerative-based residual LCOE standard deviation scoring (weighted quantile bins plus agglomerative within-bin clustering).
7. **Renewables output**: Each region gets a `renewables_clusters` entry for each technology, with `filter`, `bin`, and `cluster` settings tuned to the selected capacity and budget. Bin entries now include integer `q` values (a quantile-count proxy) computed as regional MW divided by configured `mw_per_bin`, rounded to the nearest integer.

!!! tip
  If any BA demand is missing from the CSV, the app flags this and computes renewables clusters with the remaining demand.

### Output

* **Preview**: The step renders a YAML preview of `renewables_clusters`.
* **Supply curve plots**: After `renewables_clusters` is computed, the app renders per-region supply curve charts below the YAML preview and navigation controls.
  For each model region, one row shows four charts: wind aggregated CPAs, wind individual CPAs, solar aggregated CPAs, and solar individual CPAs.
  All charts use cumulative capacity (MW) on the x-axis and LCOE on the y-axis.
  Aggregated bars represent final groups formed by weighted quantile binning followed by agglomerative clustering within each bin, using that region/technology's `renewables_clusters` settings.
* **Export**: The `renewables_clusters` list is written into `resources.yml` during Step 9. Bin entries include integer `q` values derived from regional MW / `mw_per_bin` (rounded to nearest integer). During the transition, output still includes `mw_per_bin` for compatibility.

### Interpreting Aggregated vs Individual Curves

* **Individual CPA curves** show raw site-level cost progression as capacity is added in LCOE order.
* **Aggregated CPA curves** show grouped resources that align with the `renewables_clusters` binning concept, making it easier to compare the clustered representation against the underlying site-level shape.
* Plots refresh whenever renewables clusters are recomputed (for example, after changing demand shares, average resource sizes, or budgets and running compute again).

## Step 9: Export

The Export step generates complete PowerGenome settings files based on all previous configuration steps.

### Generated Files

The app generates these seven core YAML files:

* `model_definition.yml` - Model regions, years, and financial settings
* `resources.yml` - Existing plant clusters, new resources, resource attribute modifiers, and modified resources. Year-specific resources are embedded directly using year-keyed values (PowerGenome v0.8.0+ format).
* `fuels.yml` - Fuel prices and emission factors
* `transmission.yml` - Transmission line definitions
* `distributed_gen.yml` - Distributed generation settings
* `resource_tags.yml` - Resource classification tags
* `startup_costs.yml` - Startup cost parameters

The **Download All** ZIP also includes `settings/data.yml`. This file is a
starter template for the resource and data locations used by PowerGenome.
Replace its placeholder resource path and primary data path with locations that
match your local data installation before running a model. The generated
`data.yml` does not include `data` as a secondary `data_location`.

#### Download All ZIP Structure

When you click **Download All**, the ZIP archive contains:

* `settings/` - All generated YAML files
* `extra_inputs/` - Generated CSV inputs used by YAML settings (when applicable)

This includes:

* `settings/*.yml` (core settings plus the `data.yml` path template)
* `DATA_SOURCES.md` - Input-data download instructions, placed at the ZIP root (Zenodo deposits, example `data.yml` paths, data versioning)
* `extra_inputs/emission_policies.csv` (only when ESR policies are generated)
* `extra_inputs/<generated network-cost filename>.csv` (only when network costs were computed successfully)

#### Conditional ESR Policy File

If ESR policies are generated in Step 6, the export also includes:

* `emission_policies.csv` - Policy constraints table (written to `extra_inputs/` in **Download All**).

#### Conditional Network Cost File

If network costs were computed after regions were finalized, the export also includes
a CSV whose descriptive filename is generated from the region count,
interconnections, and grouping:

* `extra_inputs/network_costs_<regions>r_<interconnections>_<grouping>.csv` - Region-to-region transmission proxy table. The generated filename is inserted into the ZIP under `extra_inputs/`; use that filename when referencing the export. See [Network Cost Calculation](network_costs.md).

##### How Year-Specific Resources Work

When any New Resources (Step 4) are tagged to a **specific planning year** (rather than "All (default)"), PowerGenome's year-keyed settings format is used directly in `resources.yml`.

For example, if you have NaturalGas as an "All" resource and add UtilityPV for 2030 only:

```yaml
new_resources:
  default:
    - [NaturalGas, CC, Moderate, 500]  # applies to all years
  2030:
    - [NaturalGas, CC, Moderate, 500]  # base list repeated
    - [UtilityPV, Class1, Moderate, 100]  # 2030-specific addition

resource_modifiers:
  naturalgas_cc:
    technology: NaturalGas
    tech_detail: CC
  utilitypv_class1:
    technology: UtilityPV
    tech_detail: Class1
    capex_mw:
      default: [mul, 1]
      2030: [mul, 0.9]
```

* Resources tagged "All (default)" form the `default` list in `resources.yml`.
* For each year that has year-specific resources, the year key contains the full "all" list **plus** year-specific additions.
* PowerGenome resolves one value per planning year: years with an explicit key use that value; other years fall back to `default`.
* In `resource_modifiers`, resource keys stay at the top level; only changed fields become year-keyed.
* If a field is only specified for particular years, a neutral `default` value is included (for example, `[mul, 1]`).

The app intentionally **does not generate** these files (configure separately):

* `time_clustering.yml`
* `demand.yml`

### How to Export

1. Review the configuration summary
2. Click **Generate Settings YAMLs**
3. Use the file dropdown to preview each settings file
4. Click **Download** to save individual files or **Download All** for a zip archive
5. Review the **Download input data** section for the Zenodo deposits referenced by the generated `data.yml` (see [Downloading Input Data](#downloading-input-data))

!!! note
    `model_definition.yml` and several downstream defaults require region aggregations from Step 1.
    If you haven't clustered regions yet, the Export step will prompt you to complete Step 1 first.

#### Downloading Input Data

Below the **Download All as ZIP** button, the **Download input data** section explains where to find the input data that the generated `data.yml` references. Three Zenodo deposits are documented; each entry lists what the deposit contains, which settings key it feeds, its Zenodo link/DOI, and a suggested local folder:

* **PowerGenome Input Data (core)** - The 15 files referenced by `data.yml` (`reeds_generators_transformed.csv`, `plant_region_map.csv`, `technology_costs_atb.parquet`, `technology_heat_rates_nrelatb.csv`, `operational_constraints_reeds.csv`, `transmission_capacity_reeds.csv`, `fuel_prices.parquet`, `dollar_year_adjustment.csv`, `reeds_load_transformed.parquet`, `regional_cost_multipliers.csv`, `distributed_capacity.parquet`, `distributed_profiles.parquet`, `reserve_margins.csv`, `nerc_reserve_margins.csv`, `cpi_data.csv`). Feeds the `data_location` settings key. Only a sandbox record exists so far (https://sandbox.zenodo.org/records/590994, DOI `10.5072/zenodo.590994`); a production DOI is pending. Suggested folder: `~/PowerGenome-data/data`.
* **PowerGenome Renewable Resource Profiles** - Hourly capacity-factor profiles for new-build renewables. Feeds `RESOURCE_GROUP_PROFILES`. Zenodo link and DOI are placeholders until the deposit is published. Suggested folder: `~/PowerGenome-data/resource_profiles`.
* **PowerGenome Existing Renewable Resource Groups** - Resource group metadata for existing renewable generators. Feeds `RESOURCE_GROUPS`. Zenodo link and DOI are placeholders until the deposit is published. Suggested folder: `~/PowerGenome-data/existing_resource_groups`.

The section also includes a copy-paste-ready `data.yml` snippet pointing each setting at its suggested folder:

```yaml
data_location: ["~/PowerGenome-data/data"]
RESOURCE_GROUPS: "~/PowerGenome-data/existing_resource_groups"
RESOURCE_GROUP_PROFILES: "~/PowerGenome-data/resource_profiles"
```

**Data versioning**: Releases are versioned by date (`data_version`, for example `2026.08.14`; same-day releases get a suffix). Each file also carries its own element version recording when the data was last updated. Zenodo keeps every version of a deposit, and release notes list files added, updated, or removed. Settings generated by this tool target the latest release—if you already have an older copy of the data, check the Zenodo release notes for changed files before reusing it.

The same instructions are written to `DATA_SOURCES.md` at the root of the **Download All** ZIP so they travel with your settings. The section is rendered from a single `DATA_SOURCES` configuration block in `web/cluster_app.py`—once production Zenodo records exist, that block is the one place to update the deposit URLs and DOIs.

### Saving and Restoring Workflow Defaults

The app lets you save all of your current settings choices to a file and reload them in a later session—so you can pause work, share a configuration, or create a reusable starting point without re-entering every option by hand.

#### Exporting Workflow Defaults

Every time you click **Download All**, the resulting ZIP archive automatically includes a `workflow_state.yml` file at its root. This file is a **versioned manifest** that captures:

* All form control values (grouping column, clustering algorithm, planning periods, fuel scenarios, no-cluster selections, etc.)
* The full application state (selected BAs, region assignments, new resources, plant cluster settings, renewables clusters, ESR policies, network costs, resource group assignments, and pre-generated settings YAMLs)
* References to any supplemental files—such as resource group files—that are stored in the ZIP alongside the manifest

Renewable supply-curve arrays are an exception: they are **derived during workflow
import** from the available renewable LCOE/resource data and are not stored in
`workflow_state.yml`. The manifest stores the renewable cluster settings and
capacity selections needed to restore the workflow, then the app rebuilds the
curve arrays when the corresponding data is available.

The Existing Plants (Step 3) clustering outputs are similarly **rebuilt during
import**: the manifest stores the plant cluster settings and any per-candidate
cluster-count overrides, and the app re-runs the clustering with the restored
inputs to regenerate the YAML preview and split-candidate list, then re-applies
the imported overrides.

When generated wind or solar LCOE Parquet files are included under
`resource_groups/`, the manifest also omits the matching source tables and
restores them directly from those Parquet files. This avoids storing the same
resource data twice.

You do not need to take any extra steps to produce this manifest; it is always written into **Download All**.

#### The `workflow_state.yml` Manifest Format

The manifest is a YAML file that begins with two required header fields:

```yaml
schema: powergenome-tools-workflow-state
version: 1
required_supplemental_files:
  - resource_groups/my_region_groups.json   # listed only when supplemental files exist
forms: { ... }
state: { ... }
tables: { ... }
```

* `schema` must be `powergenome-tools-workflow-state`.
* `version` must be `1` (the current format version).
* `required_supplemental_files` lists every supplemental file path (relative to the ZIP root) that must be present for a successful import. This list is empty when no supplemental files exist.

The app rejects any manifest whose `schema` or `version` does not match. **Do not edit these header fields manually.**

#### Importing Workflow Defaults

A "Continue an Existing Workflow" upload control appears on the app's main page. It accepts either a full settings ZIP or a standalone `workflow_state.yml` file.

##### When a standalone `workflow_state.yml` is enough

If `required_supplemental_files` is empty in the manifest—meaning the workflow did not generate any resource group files—you can upload just the `workflow_state.yml` file on its own. The app will restore all form values and state without needing anything else.

Because curve arrays are recalculated from source data, the imported curves are
only reproducible when the available LCOE/resource files are consistent with
those used when the workflow was exported. Changed resource-group inputs,
different LCOE files, or changed region assignments can produce different
curves and capacity limits. If the required source data is unavailable, the
workflow settings can still be restored, but supply-curve arrays are not
available until the data is supplied and renewables clusters are recomputed.

##### When you must upload the full ZIP

If `required_supplemental_files` lists one or more paths, the workflow depends on supplemental files (for example, resource group JSON files generated in Step 7). In this case, **uploading just the `workflow_state.yml` will be rejected** with the error:

> *This workflow state requires supplemental files. Upload the complete settings ZIP.*

Upload the complete `powergenome_settings.zip` instead. The app verifies that every path listed in `required_supplemental_files` is actually present inside the ZIP before restoring state.

##### What the app accepts and rejects

| Upload | Accepted? | Condition |
| -------- | ----------- | ----------- |
| `powergenome_settings.zip` | ✅ Always | Must be a valid ZIP containing `workflow_state.yml` at its root |
| `workflow_state.yml` (standalone) | ✅ Only when `required_supplemental_files` is empty | — |
| Any other filename (e.g. `my_config.yml`) | ❌ Rejected | Must be named exactly `workflow_state.yml` or `workflow_state.yaml` |
| ZIP missing `workflow_state.yml` | ❌ Rejected | The manifest must be at the ZIP root |
| ZIP missing a listed supplemental file | ❌ Rejected | All paths in `required_supplemental_files` must exist in the ZIP |
| Manifest with wrong `schema` or `version` | ❌ Rejected | Incompatible format |

!!! tip
    The easiest way to resume a session is to keep the **Download All** ZIP. It always contains both the settings files and a complete, importable `workflow_state.yml` with all supplemental files bundled together.

---

## Additional Features

### Interactive Map

* **View Balancing Authorities**: Click on regions to select them; colored outlines show grouping
* **Box Select Mode**: Drag to select multiple BAs at once (enabled by default)
* **Transmission Lines**: Toggle to view transmission capacity between clustered regions
* **Tooltips**: Hover over regions to see BA name, state, grouping, and model region (after clustering)

---

## Running Locally

Since the app uses PyScript and fetches local data files, it must be served via a local HTTP server to avoid CORS errors.

1. Navigate to the `web` directory:

    cd web

2. Start a simple Python server:

    python -m http.server 8000

3. Open your browser to `http://localhost:8000`.
