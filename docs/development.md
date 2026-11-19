# Development Guide

This guide covers how to set up the development environment for the
PowerGenome tools web application.

## Installation

### Requirements

* Python 3.8+
* `uv` (recommended) or `pip`

### Setup

1. Clone the repository:

    ```bash
    git clone https://github.com/PowerGenome/PowerGenome-tools.git
    cd PowerGenome-tools
    ```

2. Install dependencies using `uv`:

    ```bash
    uv sync
    ```

    Or install manually with pip:

    ```bash
    pip install networkx scipy scikit-learn pandas pyyaml numpy
    # Optional for map generation
    pip install geopandas matplotlib
    ```

## Web Application Development

The web application is located in the `web/` directory. It uses PyScript
to run Python code in the browser.

### Running Locally

To test changes to the web app, you need to serve the `web/` directory via HTTP.

1. Navigate to the `web` directory:

    ```bash
    cd web
    ```

2. Start a simple Python server:

    ```bash
    python -m http.server 8000
    ```

3. Open your browser to `http://localhost:8000`.

### Key Files

* `web/index.html`: The main HTML structure and UI.
* `web/cluster_app.py`: The Python logic that runs in the browser (PyScript).
* `web/pyscript.toml`: PyScript configuration and dependencies.

### Regenerating the ATB Index

If New Resources reports a missing or outdated ATB index, regenerate it
from the repository root:

```bash
uv run python web/build_atb_options.py --out data/atb_options.parquet
```

The app accesses this asset through the `web/data` symlink to `data/`.
For a load failure, also check that your local server can serve the asset.

The default CLI build downloads both sources from
`gschivley/PowerGenome-data` at `main`:

* Costs: `data/technology_costs_atb.parquet`.
* Canonical heat rates: `data/technology_heat_rates_nrelatb.csv`.

The bundled asset retains the prototype's ATB 2023/2024
cost catalog (26,753 output options). The cost parquet has heat rates for
ATB 2023 only. The canonical
CSV contains heat rates for ATB 2023, 2024, and 2025. All 1,804 overlapping
2023 values in that snapshot are identical to the CSV values. Missing 2024
heat rates in an index built from that parquet alone are a source
integration gap, not an absence of upstream data.

The live default cost source also includes ATB 2025, so a default rebuild
can expand the bundled catalog. To preserve an existing catalog's costs and
keys, retain its cost input snapshot and publication-year selection, and
rebuild with a fresh canonical CSV rather than replacing the cost input.

For a reproducible, fully local build, retain both input snapshots and run:

```bash
uv run python web/build_atb_options.py \
    --parquet /path/to/technology_costs_atb.parquet \
    --heat-rates-csv /path/to/technology_heat_rates_nrelatb.csv \
    --out data/atb_options.parquet
```

Passing `--parquet` alone remains local and parquet-only; it does not
implicitly download the CSV. The Python API
`build_index(parquet_path, heat_rates_path=None)` is strictly offline:
pass a local CSV path to enrich heat rates, or omit it for parquet-only
behavior.

For remote builds, `--github OWNER/REPO:PATH@REF` selects the cost source,
and `--heat-rates-github OWNER/REPO:PATH@REF` explicitly overrides the
canonical CSV source. Use both flags with pinned commit refs rather than
`main` to make remote inputs reproducible.

The canonical CSV is authoritative for heat rates matching the complete
key: publication year (`data_year`), `technology`, `tech_detail`,
`cost_case`, and projection year (`basis_year`). There is no cross-year
borrowing. The CSV only enriches existing option keys; unmatched CSV rows
do not add catalog options or publication years. In particular, its 2025
heat rates do not add ATB 2025 options to the bundled asset, whose original
cost snapshot contains only ATB 2023/2024 costs. Builds using live default
costs may include newer years.

The builder preserves fractional heat rates. Monetary `dollar_year` comes
from the cost parquet, not the heat-rate CSV. Absent heat rates, including
those for nonthermal technologies, remain null rather than becoming zero.
Identical observations across capital recovery periods and financial cases
are deduplicated. Conflicting values within a source are rejected, not
averaged; resolve source conflicts before rebuilding.

### Playwright UI Tests

UI tests live in `tests/ui`. For wizard-step suites, prefer the
shared-page pattern used in `model-setup.spec.ts` and
`new-resources.spec.ts`: create one browser context and one booted page
through the shared suite helper in `tests/ui/fixtures/shared-app.ts`.
That helper loads the app once, verifies startup state, and runs the
suite in serial mode. Reset app state in `beforeEach` with a page-object
or test-only reset hook, as shown by `model-setup.spec.ts` and
`new-resources.spec.ts`.

Keep `smoke.spec.ts` as true cold-start coverage. If a test is
validating PyScript startup, page boot, or initial navigation, use
Playwright's per-test `page` and call `loadApp()` inside the test
instead of reusing a shared page.

Run the UI suite with:

```bash
npm run test:ui
```

Playwright starts a static server for `web/` itself; it uses `python3` on
macOS/Linux and `python` on Windows, so no `python` alias is needed. The
pinned Playwright version must stay at 1.65 or newer, because CI runs on
`ubuntu-latest`, which resolves to Ubuntu 26.04 — a platform older
Playwright releases cannot install Chromium for.

## Documentation

The documentation is built with [MkDocs](https://www.mkdocs.org/).

### Building Docs

1. Install documentation dependencies:

    ```bash
    uv sync --group dev
    ```

2. Serve the documentation locally:

    ```bash
    uv run mkdocs serve
    ```

3. Build the static site:

    ```bash
    uv run mkdocs build
    ```

### Deployment

The documentation is deployed to GitHub Pages. The build process
automatically includes the `web/` application directory in the deployed
site, ensuring the web app remains accessible at `/web/`.

To deploy manually:

```bash
uv run mkdocs gh-deploy
```
