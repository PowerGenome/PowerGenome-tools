# Web Application

The PowerGenome Settings Builder is a comprehensive web-based interface for building complete PowerGenome settings files. It guides users through a step-by-step workflow to define model regions, configure resources, and export ready-to-use configuration files. The application runs entirely in the browser using PyScript—no installation required.

[Launch Web App](https://gschivley.github.io/PowerGenome-tools/web/){ .md-button .md-button--primary }

## Overview

The guided workflow ensures you configure all necessary settings in the correct order:

1. **Regions** - Define model regions through automatic clustering or manual assignment of Balancing Authorities
2. **Model Setup** - Configure planning years and financial parameters
3. **Existing Plants** - Aggregate existing generators within regions
4. **New Resources** - Select new-build technologies and define custom resources
5. **Fuels** - Choose fuel price scenarios
6. **ESR Policies** - Configure Energy Share Requirements for state-level policies (optional)
7. **Export** - Generate and download complete settings YAML files

Each step builds on the previous ones, with the Regions step being the foundation that determines how plants are aggregated and how model boundaries are defined.

## Step 1: Regions

The Regions step allows you to define model regions using two different approaches: **Automatic Clustering** (algorithm-driven) or **Manual Definition** (user-controlled). This is the foundation of your PowerGenome model configuration, determining how plants are aggregated and how model boundaries are defined.

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

Automatic clustering uses transmission capacity between BAs to create aggregated regions. BAs are first clustered within their selected grouping (e.g., NERC region), then groups are merged to reach the target number of regions.

#### How to Use Region Clustering

1. **Select BAs**: Click on regions in the map to select them. Use **Box Select** mode to drag and select multiple BAs at once.
2. **Choose grouping**: The *Grouping Column* determines how BAs are grouped for clustering (colored outlines show groups).
3. **Set target regions**: Enter how many final regions you want after clustering. Optionally enable *Auto-optimize* to find the best number of regions within a range.
4. **Exclude groups**: Check any groups in *Groups to Keep Unclustered* to keep them as individual BAs.
5. **Run clustering**: Click *Run Clustering* to generate the region aggregations.
6. **Export**: Copy or download the YAML output to use in PowerGenome.

!!! tip
    The clustering uses transmission capacity between BAs to create aggregated regions. BAs are first clustered within their selected grouping (e.g., NERC region), then groups are merged to reach the target number of regions. The selected grouping affects the clustering results, so experiment with different options to see what works best for your case!

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
| **Transmission Group** | 18 | ~7 BAs per group (median 6) | More balanced regions; aligns with ISOs/RTOs; better convergence | None significant | Most general analyses; grid-operational alignment |
| **Transmission Region** | 11 | ~12 BAs per group (median 11) | Still grid-aligned; moderate flexibility | Large NorthernGrid group in WECC can lead to unbalanced regions in national studies | Broader transmission boundaries |
| **Interconnect** | 3 | ~45 BAs per group (median 35) | None noted | Highly imbalanced regions; misses operational detail | Rarely used |
| **Census Division** | 9 | ~15 BAs per group (median 17) | Regional policy rollups | Doesn't reflect grid ops; splits transmission clusters | High-level regional summaries |
| **State** | 48 | ~3 BAs per group (median 2) | Aligns to state policy boundaries | Doesn't reflect grid ops; splits transmission clusters | State-level policy analysis |

**Recommendation**: Use **Transmission Group** (default) or **Transmission Region** for grid-focused analyses. These options respect ISO/RTO boundaries that reflect how the grid is actually operated and studied.

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

1. Start with **Transmission Group** grouping.
2. Enable **Auto-Optimize** with a reasonable range (e.g., 15–40 regions).
3. Try switching to a fixed number of regions. Select **Spectral** or **Louvain** algorithm.
4. Review the resulting regions and modularity score. Experiment with different ranges to see how modularity changes.

**For balanced, fixed-region models**:

1. Use **Transmission Group** grouping.
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
* **Model Years**: Comma-separated list of years to model (e.g., 2030, 2035, 2040)
* **First Planning Years**: Comma-separated list of first planning years corresponding to each model year

!!! note
    Model Years and First Planning Years must be lists of the same length. These define the temporal scope of your capacity expansion analysis.

## Step 3: Existing Plants

The Existing Plants step allows you to cluster existing generators within each model region. This reduces model complexity while preserving the operational diversity of the fleet.

### Plant Clustering

1. **Select technologies to omit**: Choose which technology types to exclude from clustering (e.g., "All Other", "Solar thermal", "Flywheel" are pre-selected).
2. **Group similar technologies**: Check "Group similar technologies" to automatically group related tech types (Biomass and Other peaker by default). Uncheck to treat each technology individually.
3. **Customize groups (optional)**: Add new groups and move technologies between "Available" and "In selected group" lists to create custom groupings.
4. **Set cluster budget**: Specify the total number of clusters across all technologies and regions. The default is automatically set to 15% above the minimum required (one cluster per tech/region combination).
5. **Adjust thresholds (optional)**: Modify *Capacity Threshold* (MW) and *Heat-rate IQR Threshold* to control when generators are suggested for splitting.
6. **Run clustering**: Click *Run Plant Clustering* to generate cluster assignments.
7. **Review suggestions**: Check the "Top candidates for more splits" list to identify tech/region groups that could benefit from more clusters within the current budget.
8. **Export**: Copy or download the YAML output.

!!! tip
    Plant clustering respects the model regions created in the Regions step. If you haven't run region clustering yet, plants are grouped by their BA. The system uses heat rate variability and capacity to suggest clusters; larger, more varied generator fleets get more clusters when budget allows.

## Step 4: New Resources

The New Resources step allows you to select new-build technologies from NREL's Annual Technology Baseline (ATB) and define custom modified resources.

### Standard ATB Resources

If ATB data is available, use the dropdowns to select:

* **ATB Data Year** → **Technology** → **Tech Detail** → **Cost Case**
* Specify **Size (MW)** for each resource

You can also paste resources manually, one per line:

    Technology | Tech Detail | Cost Case | Size

### Modified New Resources

Create custom resources by modifying existing ATB entries:

1. **Base Resource**: Select an ATB technology/detail/cost case/size as the starting point
2. **New Identity**: Define new technology/detail/cost case names for this modified resource
3. **Fuel Type**: Choose a standard fuel (coal, natural gas, distillate, uranium) or define a new fuel with price and emissions
4. **Resource Class Tag**: Select THERM, VRE, STOR, or other resource class
5. **Commit Tag**: For thermal resources, optionally add to the "Commit" tag
6. **Attribute Modifiers**: Add custom YAML attributes to override base resource properties

!!! note
    Modified resources are added to the `modified_new_resources` section of `resources.yml` and automatically update related settings in `fuels.yml` and `resource_tags.yml`.

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

## Step 7: Export

The Export step generates complete PowerGenome settings files based on all previous configuration steps.

### Generated Files

The app generates seven YAML files:

* `model_definition.yml` - Model regions, years, and financial settings
* `resources.yml` - Existing plant clusters, new resources, and modified resources
* `fuels.yml` - Fuel prices and emission factors
* `transmission.yml` - Transmission line definitions
* `distributed_gen.yml` - Distributed generation settings
* `resource_tags.yml` - Resource classification tags
* `startup_costs.yml` - Startup cost parameters

The app intentionally **does not generate** these files (configure separately):

* `data.yml`
* `scenario_management.yml`
* `time_clustering.yml`
* `extra_inputs.yml`
* `demand.yml`

### How to Export

1. Review the configuration summary
2. Click **Generate Settings YAMLs**
3. Use the file dropdown to preview each settings file
4. Click **Download** to save individual files or **Download All** for a zip archive

!!! note
    `model_definition.yml` and several downstream defaults require region aggregations from Step 1.
    If you haven't clustered regions yet, the Export step will prompt you to complete Step 1 first.

### Supply Curve Visualization

The Export step includes a supply curve plotting feature that generates illustrative visualizations of renewable energy supply curves for wind and solar resources. This tool helps you understand how renewable resources are characterized in PowerGenome models.

#### What Supply Curves Show

Supply curves display the relationship between **Levelized Cost of Energy (LCOE)** and **available capacity** for renewable resources:

* **X-axis**: Cumulative capacity (MW) available at or below a given cost
* **Y-axis**: LCOE ($/MWh) - the cost per unit of energy over the project lifetime
* **Interpretation**: Each point represents an additional resource bin; curves rise from left to right as cheaper resources are exhausted and more expensive sites are added

Supply curves help identify:

* The cost range of available renewable resources in each region
* How much capacity is available at competitive price points
* Regional differences in renewable resource quality and economics

#### Site-Level vs Cluster-Level Curves

PowerGenome supports two approaches to representing renewable resources:

**Site-level supply curves:**

* Show individual resource sites before any aggregation
* More granular with many resource bins
* Higher computational cost in capacity expansion models
* Useful for detailed resource adequacy studies

**Cluster-level supply curves:**

* Aggregate similar sites within each region to reduce model complexity
* Fewer, larger resource bins
* Lower computational cost while preserving resource diversity
* Recommended for most capacity expansion studies

The visualization displays both approaches side-by-side in a small multiples layout, showing how clustering simplifies the resource representation while maintaining the overall cost-capacity relationship.

#### Generating Supply Curve Plots

1. Navigate to Step 7: Export
2. Ensure you have configured renewables clustering in a previous step (the plots use your `renewables_clusters` configuration)
3. Click **Generate Supply Curve Plots**
4. The plot appears inline below the button, showing supply curves for up to 3 regions

The plots use a small multiples layout with:

* One row per region (up to 3 regions shown)
* Two columns: site-level curves (left) and cluster-level curves (right)
* Separate lines for onshore wind, offshore wind, and utility-scale solar
* Color-coded by technology type for easy comparison

#### Relationship to Renewables Configuration

The supply curves reflect your model's renewable resource configuration:

* **Model regions**: Plots are generated for each region defined in Step 1
* **Renewables clusters**: The cluster-level plots use the number of clusters you configured (affects how many bins appear in the right-hand plots)
* **Technologies included**: Wind (onshore and offshore) and utility-scale solar
* **Cost assumptions**: Based on ATB cost cases and regional resource quality

#### Understanding the Plots

**Interpreting curve shapes:**

* **Steep curves**: Limited resource availability; costs rise quickly as capacity increases
* **Flat curves**: Abundant resources; large amounts of capacity available at similar costs
* **Multiple steps**: Distinct resource quality classes (e.g., wind capacity factor bins)

**Comparing regions:**

* Regions with lower-cost curves have better resource quality (higher capacity factors)
* The horizontal extent shows total technical potential
* Regional differences reflect geography, climate, and land availability

**Site vs Cluster comparison:**

* Cluster curves are smoother with fewer steps
* Total capacity should match between site and cluster views
* Cost ranges should be similar, though clustering averages within bins

#### Limitations and Important Notes

!!! warning "Synthetic Example Data"
    The supply curve plots use **synthetic/example data** for demonstration purposes. These are illustrative visualizations showing what supply curves look like, not actual resource data for your model regions.

**Key limitations:**

* **Example data only**: Curves are generated using synthetic parameters based on typical renewable resource characteristics, not real resource data for your specific regions
* **Limited regions**: Only the first 3 model regions are displayed to keep plots readable
* **Demonstration purpose**: Use these plots to understand supply curve concepts and validate that your renewables configuration produces reasonable results
* **Not for analysis**: Do not use these plots for resource planning decisions or cost estimates

**For production analysis:**

* Use PowerGenome's full renewable resource processing with actual NREL ATB data
* Run the complete PowerGenome pipeline with your configured settings
* Generate supply curves from actual wind/solar resource assessments
* Validate results against NREL Cambium or ReEDS outputs

The visualization tool is designed to help you understand how renewable resources are represented in PowerGenome models and verify that your configuration choices (number of clusters, regions, technologies) produce sensible results.

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
