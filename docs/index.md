# PowerGenome System Design

Welcome to the documentation for PowerGenome System Design. This project provides a comprehensive web-based interface for building complete PowerGenome settings files, guiding users through the entire process of defining model regions, configuring resources, and exporting ready-to-use configuration files for energy system modeling (specifically PowerGenome/GenX).

!!! warning "Under Development"
 This tool is still under active development. Algorithms and generated outputs may contain errors and should be validated before using results in analysis or decision making.

## Overview

The project consists of an interactive **[Web Application](web_app.md)** that walks users through a guided workflow to configure all aspects of a PowerGenome model. Built with PyScript to run entirely in-browser, it allows users to visually define model regions, configure new and existing resources, select fuel scenarios, and export complete settings files in YAML format.

For details on the region-level transmission proxy table generated from clustered regions, see **[Network Cost Calculation](network_costs.md)**.

### Guided Workflow

The web application guides you through a 9-step workflow to build complete PowerGenome settings files. Each step builds on the previous ones, ensuring a logical progression from defining your model's geographic scope to exporting ready-to-use configuration files.

1. **Regions** - Select Balancing Authorities and cluster them into model regions using automatic clustering algorithms (Spectral, Louvain, Hierarchical) or manual assignment. Define the geographic boundaries that determine how load, transmission, and resources are aggregated.

2. **Model Setup** - Define planning periods in a row-based editor, along with financial parameters, target dollar-year for cost alignment, and timezone settings. These parameters establish the temporal and financial framework for your analysis.

3. **Existing Plants** - Cluster existing generators within model regions to reduce model complexity while preserving operational diversity. Use heat rate variability and capacity thresholds to determine optimal cluster counts.

4. **New Resources** - Select new-build technologies from NREL's Annual Technology Baseline (ATB), specify resource sizes, and optionally override cost/performance attributes. In the New Resources list, inline overrides for standard resources are shown directly in each resource row (for example, default battery variable O&M fields). Create custom modified resources for technologies not in the ATB (e.g., hydrogen combustion turbines).

5. **Fuels** - Choose fuel price scenarios for coal, natural gas, distillate, and uranium from EIA projections or other data sources. Fuel prices are automatically aligned to your target dollar-year.

6. **ESR Policies** - Build Energy Share Requirement (ESR) policies that generalize Renewable Portfolio Standards (RPS) and Clean Energy Standards (CES). The app automatically groups regions into trading zones based on state policy rules and interconnect boundaries.

7. **Interconnection** - Configure default interconnection costs for resources that are not tied to specific locations (e.g. natural gas, batteries, or nuclear). Generate resource group files with dynamically calculated interconnection costs and LCOE (Levelized Cost of Energy) for each potential wind and solar resource based on model region aggregation. A region-level network cost table is also computed after regions are finalized.

8. **Renewables Clustering** - Build `renewables_clusters` settings from demand shares and LCOE data. Filter to the most cost-effective wind and solar sites to keep model size manageable while providing sufficient capacity options. Visualize supply curves to understand resource selection.

9. **Export** - Generate and download complete PowerGenome settings YAML files, including `model_definition.yml`, `resources.yml`, `fuels.yml`, `transmission.yml`, and other configuration files ready for use with PowerGenome. When available, the Download All ZIP also includes `data/network_costs.csv`.

## Key Features

* **Interactive Visualization**: Select regions on a map and see clustering results in real-time with multiple algorithms (Spectral, Louvain, Hierarchical).
* **Comprehensive Configuration**: Define all major PowerGenome settings in one place, from regional boundaries to resource portfolios.
* **Flexible Grouping**: Cluster regions based on various hierarchies like NERC regions, Transmission Groups, or States.
* **Plant Aggregation**: Cluster power plants within regions based on technology and efficiency, allocating clusters based on a budget while minimizing variability.
* **NREL ATB Integration**: Select new-build resources directly from NREL's Annual Technology Baseline with support for custom modified resources and cost/performance attribute overrides.
* **Complete Settings Export**: Generate all seven major PowerGenome settings YAML files ready for use.

## Getting Started

* To use the interactive tool immediately, visit the [Web App](https://gschivley.github.io/PowerGenome-tools/web/).
* To run the tools locally, check the [Development](development.md) guide.
