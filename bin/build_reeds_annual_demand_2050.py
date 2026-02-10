#!/usr/bin/env python3
"""
Script to generate annual demand data for ReEDS Balancing Authorities in 2050.

This script reads hourly demand data from a parquet file and calculates the total
annual demand (in MWh) for each ReEDS BA in 2050, outputting the results to a CSV file.

Usage:
    python bin/build_reeds_annual_demand_2050.py \
        --input /path/to/reeds_load_transformed.parquet \
        --output data/reeds_annual_demand_2050.csv

The input parquet file should contain:
- A 'ba_code' column with ReEDS BA identifiers
- A 'year' column with year values
- A 'demand_mw' or 'load_mw' column with hourly demand values in MW

The output CSV will contain:
- 'ba_code': ReEDS BA identifier
- 'annual_demand_mwh': Total annual demand in MWh for 2050
"""

import argparse
import sys
from pathlib import Path

import pandas as pd


def load_hourly_demand(input_path: str) -> pd.DataFrame:
    """Load hourly demand data from parquet file.
    
    Args:
        input_path: Path to the parquet file containing hourly demand data
        
    Returns:
        DataFrame with hourly demand data
        
    Raises:
        FileNotFoundError: If input file doesn't exist
        ValueError: If required columns are missing
    """
    input_file = Path(input_path)
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    
    print(f"Loading hourly demand data from {input_path}...")
    df = pd.read_parquet(input_path)
    
    # Check for required columns
    if 'ba_code' not in df.columns:
        raise ValueError("Input data must contain 'ba_code' column")
    if 'year' not in df.columns:
        raise ValueError("Input data must contain 'year' column")
    
    # Check for demand column (try multiple common names)
    demand_col = None
    for col in ['demand_mw', 'load_mw', 'demand', 'load']:
        if col in df.columns:
            demand_col = col
            break
    
    if demand_col is None:
        raise ValueError(
            "Input data must contain a demand column "
            "(e.g., 'demand_mw', 'load_mw', 'demand', or 'load')"
        )
    
    # Rename to standardized column name
    if demand_col != 'demand_mw':
        df = df.rename(columns={demand_col: 'demand_mw'})
    
    print(f"Loaded {len(df):,} hourly records")
    print(f"Years available: {sorted(df['year'].unique())}")
    print(f"Number of BAs: {df['ba_code'].nunique()}")
    
    return df


def calculate_annual_demand_2050(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate annual demand for each BA in 2050.
    
    Args:
        df: DataFrame with hourly demand data including 'ba_code', 'year', and 'demand_mw'
        
    Returns:
        DataFrame with ba_code and annual_demand_mwh for 2050
        
    Raises:
        ValueError: If no data exists for year 2050
    """
    # Filter to 2050 data
    df_2050 = df[df['year'] == 2050].copy()
    
    if len(df_2050) == 0:
        raise ValueError("No data found for year 2050")
    
    print(f"\nProcessing {len(df_2050):,} hourly records for 2050...")
    
    # Calculate annual demand (sum of hourly MW gives MWh for hourly data)
    annual_demand = df_2050.groupby('ba_code')['demand_mw'].sum().reset_index()
    annual_demand.columns = ['ba_code', 'annual_demand_mwh']
    
    # Sort by ba_code for consistent output
    annual_demand = annual_demand.sort_values('ba_code').reset_index(drop=True)
    
    print(f"Calculated annual demand for {len(annual_demand)} BAs")
    print(f"Total annual demand (2050): {annual_demand['annual_demand_mwh'].sum():,.0f} MWh")
    
    # Show sample of results
    print("\nSample of results:")
    print(annual_demand.head(10).to_string(index=False))
    
    return annual_demand


def save_to_csv(df: pd.DataFrame, output_path: str) -> None:
    """Save annual demand data to CSV file.
    
    Args:
        df: DataFrame with annual demand data
        output_path: Path to output CSV file
    """
    output_file = Path(output_path)
    
    # Create parent directory if it doesn't exist
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"\nSaving results to {output_path}...")
    df.to_csv(output_path, index=False)
    print(f"Successfully saved {len(df)} records to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate annual demand data for ReEDS BAs in 2050",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        '--input',
        required=True,
        help='Path to input parquet file with hourly demand data'
    )
    parser.add_argument(
        '--output',
        default='data/reeds_annual_demand_2050.csv',
        help='Path to output CSV file (default: data/reeds_annual_demand_2050.csv)'
    )
    
    args = parser.parse_args()
    
    try:
        # Load hourly demand data
        hourly_df = load_hourly_demand(args.input)
        
        # Calculate annual demand for 2050
        annual_df = calculate_annual_demand_2050(hourly_df)
        
        # Save to CSV
        save_to_csv(annual_df, args.output)
        
        print("\n✓ Annual demand data generated successfully!")
        return 0
        
    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
