# Data Preparation Scripts

This section documents scripts used to prepare input data for the PowerGenome Settings Builder web application.

## Build ReEDS Annual Demand 2050

The `build_reeds_annual_demand_2050.py` script generates annual demand data for ReEDS Balancing Authorities in 2050. This data is used by the web application to display demand information for each BA and help users make informed decisions when clustering regions.

### Purpose

The script processes hourly demand data and aggregates it to calculate the total annual electricity demand (in MWh) for each ReEDS BA in the year 2050. This aggregated data provides a quick reference for:

- Understanding the relative size of different BAs
- Evaluating the impact of regional clustering decisions
- Validating that all BAs are included in the dataset

### Usage

```bash
python bin/build_reeds_annual_demand_2050.py \
    --input /path/to/reeds_load_transformed.parquet \
    --output data/reeds_annual_demand_2050.csv
```

#### Arguments

- `--input` (required): Path to the input parquet file containing hourly demand data
- `--output` (optional): Path to the output CSV file (default: `data/reeds_annual_demand_2050.csv`)

### Input Format

The input parquet file must contain the following columns:

| Column | Type | Description |
|--------|------|-------------|
| `ba_code` | string | ReEDS Balancing Authority identifier (e.g., "p1", "p10") |
| `year` | integer | Year of the data point |
| `demand_mw` or `load_mw` | float | Hourly demand/load in megawatts (MW) |

!!! note "Flexible Column Names"
    The script automatically detects demand columns with common names: `demand_mw`, `load_mw`, `demand`, or `load`.

### Output Format

The output CSV file contains two columns:

| Column | Type | Description |
|--------|------|-------------|
| `ba_code` | string | ReEDS Balancing Authority identifier |
| `annual_demand_mwh` | float | Total annual demand in megawatt-hours (MWh) for 2050 |

**Example output:**

```csv
ba_code,annual_demand_mwh
p1,32252115.7
p10,29779064.71
p100,48690398.67
```

The output file contains 134 BAs, sorted alphabetically by BA code.

### How It Works

1. **Load Data**: Reads the parquet file and validates that required columns exist
2. **Filter to 2050**: Extracts only records where `year == 2050`
3. **Aggregate**: Groups by `ba_code` and sums hourly demand values
   - Since the input is hourly data in MW, summing gives MWh directly
   - Formula: `annual_demand_mwh = sum(demand_mw)` for all hours in 2050
4. **Export**: Saves the results to CSV, sorted by BA code

### Example Output During Execution

```
Loading hourly demand data from /path/to/reeds_load_transformed.parquet...
Loaded 1,173,360 hourly records
Years available: [2050]
Number of BAs: 134

Processing 1,173,360 hourly records for 2050...
Calculated annual demand for 134 BAs
Total annual demand (2050): 4,238,567,890 MWh

Sample of results:
ba_code  annual_demand_mwh
p1              32252115.7
p10             29779064.71
p100            48690398.67
...

Saving results to data/reeds_annual_demand_2050.csv...
Successfully saved 134 records to data/reeds_annual_demand_2050.csv

✓ Annual demand data generated successfully!
```

### Error Handling

The script validates inputs and provides clear error messages:

- **Missing input file**: `FileNotFoundError: Input file not found: /path/to/file`
- **Missing columns**: `ValueError: Input data must contain 'ba_code' column`
- **No 2050 data**: `ValueError: No data found for year 2050`

### Integration with Web Application

The generated `data/reeds_annual_demand_2050.csv` file is loaded by the PowerGenome Settings Builder web application to:

- Display annual demand values in the region selection interface
- Provide context when users are clustering BAs into model regions
- Show aggregate demand statistics for selected regions
