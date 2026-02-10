"""
Comprehensive test suite for build_reeds_annual_demand_2050.py script.

This module tests all functions in the script that generate annual demand data
for ReEDS Balancing Authorities in 2050, including:
- Loading hourly demand data from parquet files
- Validating input data structure
- Calculating annual demand for 2050
- Saving results to CSV files
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

# Add bin directory to path to import the script
sys.path.insert(0, str(Path(__file__).parent.parent / "bin"))

from build_reeds_annual_demand_2050 import (
    calculate_annual_demand_2050,
    load_hourly_demand,
    save_to_csv,
)


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def sample_hourly_data():
    """Create sample hourly demand data for testing.
    
    Returns DataFrame with multiple years and BAs, including 2050 data.
    """
    # Create hourly data for 2 BAs over 3 days in 2050 (72 hours each)
    # Plus some 2049 data to test filtering
    data = []
    
    # BA1 in 2050: 24 hours/day * 3 days = 72 hours
    # Using constant 100 MW demand = 7200 MWh annual (for 72 hours)
    for day in range(3):
        for hour in range(24):
            data.append({
                'ba_code': 'BA1',
                'year': 2050,
                'demand_mw': 100.0,
                'timestamp': pd.Timestamp(f'2050-01-{day+1:02d} {hour:02d}:00:00')
            })
    
    # BA2 in 2050: 24 hours/day * 3 days = 72 hours
    # Using constant 200 MW demand = 14400 MWh annual (for 72 hours)
    for day in range(3):
        for hour in range(24):
            data.append({
                'ba_code': 'BA2',
                'year': 2050,
                'demand_mw': 200.0,
                'timestamp': pd.Timestamp(f'2050-01-{day+1:02d} {hour:02d}:00:00')
            })
    
    # BA3 in 2050: Same pattern, 150 MW
    for day in range(3):
        for hour in range(24):
            data.append({
                'ba_code': 'BA3',
                'year': 2050,
                'demand_mw': 150.0,
                'timestamp': pd.Timestamp(f'2050-01-{day+1:02d} {hour:02d}:00:00')
            })
    
    # Add some 2049 data to test filtering
    for day in range(2):
        for hour in range(24):
            data.append({
                'ba_code': 'BA1',
                'year': 2049,
                'demand_mw': 95.0,
                'timestamp': pd.Timestamp(f'2049-01-{day+1:02d} {hour:02d}:00:00')
            })
    
    return pd.DataFrame(data)


@pytest.fixture
def sample_hourly_data_with_load_mw():
    """Create sample data with 'load_mw' column instead of 'demand_mw'."""
    data = []
    for day in range(2):
        for hour in range(24):
            data.append({
                'ba_code': 'BA1',
                'year': 2050,
                'load_mw': 100.0,
                'timestamp': pd.Timestamp(f'2050-01-{day+1:02d} {hour:02d}:00:00')
            })
    return pd.DataFrame(data)


@pytest.fixture
def sample_hourly_data_no_2050():
    """Create sample data without any 2050 records."""
    data = []
    for day in range(2):
        for hour in range(24):
            data.append({
                'ba_code': 'BA1',
                'year': 2049,
                'demand_mw': 100.0,
                'timestamp': pd.Timestamp(f'2049-01-{day+1:02d} {hour:02d}:00:00')
            })
    return pd.DataFrame(data)


# ============================================================================
# Test load_hourly_demand function
# ============================================================================


class TestLoadHourlyDemand:
    """Test loading hourly demand data from parquet files."""
    
    def test_load_valid_parquet_file(self, tmp_path, sample_hourly_data):
        """Test loading a valid parquet file with all required columns."""
        # Create temporary parquet file
        parquet_file = tmp_path / "test_demand.parquet"
        sample_hourly_data.to_parquet(parquet_file)
        
        # Load the data
        result = load_hourly_demand(str(parquet_file))
        
        # Verify data loaded correctly
        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(sample_hourly_data)
        assert 'ba_code' in result.columns
        assert 'year' in result.columns
        assert 'demand_mw' in result.columns
        assert result['ba_code'].nunique() == 3  # BA1, BA2, BA3
        assert 2050 in result['year'].values
        assert 2049 in result['year'].values
    
    def test_load_with_load_mw_column(self, tmp_path, sample_hourly_data_with_load_mw):
        """Test loading data with 'load_mw' column that gets renamed to 'demand_mw'."""
        parquet_file = tmp_path / "test_load.parquet"
        sample_hourly_data_with_load_mw.to_parquet(parquet_file)
        
        result = load_hourly_demand(str(parquet_file))
        
        # Verify that load_mw was renamed to demand_mw
        assert 'demand_mw' in result.columns
        assert 'load_mw' not in result.columns
        assert len(result) == len(sample_hourly_data_with_load_mw)
    
    def test_load_with_demand_column(self, tmp_path):
        """Test loading data with generic 'demand' column."""
        data = pd.DataFrame({
            'ba_code': ['BA1'] * 10,
            'year': [2050] * 10,
            'demand': [100.0] * 10
        })
        parquet_file = tmp_path / "test_demand_col.parquet"
        data.to_parquet(parquet_file)
        
        result = load_hourly_demand(str(parquet_file))
        
        assert 'demand_mw' in result.columns
        assert 'demand' not in result.columns
    
    def test_load_missing_file(self, tmp_path):
        """Test that FileNotFoundError is raised for non-existent file."""
        nonexistent_file = tmp_path / "nonexistent.parquet"
        
        with pytest.raises(FileNotFoundError, match="Input file not found"):
            load_hourly_demand(str(nonexistent_file))
    
    def test_load_missing_ba_code_column(self, tmp_path):
        """Test that ValueError is raised when 'ba_code' column is missing."""
        data = pd.DataFrame({
            'year': [2050] * 10,
            'demand_mw': [100.0] * 10
        })
        parquet_file = tmp_path / "no_ba_code.parquet"
        data.to_parquet(parquet_file)
        
        with pytest.raises(ValueError, match="must contain 'ba_code' column"):
            load_hourly_demand(str(parquet_file))
    
    def test_load_missing_year_column(self, tmp_path):
        """Test that ValueError is raised when 'year' column is missing."""
        data = pd.DataFrame({
            'ba_code': ['BA1'] * 10,
            'demand_mw': [100.0] * 10
        })
        parquet_file = tmp_path / "no_year.parquet"
        data.to_parquet(parquet_file)
        
        with pytest.raises(ValueError, match="must contain 'year' column"):
            load_hourly_demand(str(parquet_file))
    
    def test_load_missing_demand_column(self, tmp_path):
        """Test that ValueError is raised when no demand column exists."""
        data = pd.DataFrame({
            'ba_code': ['BA1'] * 10,
            'year': [2050] * 10,
            'some_other_column': [100.0] * 10
        })
        parquet_file = tmp_path / "no_demand.parquet"
        data.to_parquet(parquet_file)
        
        with pytest.raises(ValueError, match="must contain a demand column"):
            load_hourly_demand(str(parquet_file))
    
    def test_load_empty_parquet(self, tmp_path):
        """Test loading an empty parquet file."""
        data = pd.DataFrame({
            'ba_code': [],
            'year': [],
            'demand_mw': []
        })
        parquet_file = tmp_path / "empty.parquet"
        data.to_parquet(parquet_file)
        
        result = load_hourly_demand(str(parquet_file))
        
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0
        assert 'ba_code' in result.columns
        assert 'year' in result.columns
        assert 'demand_mw' in result.columns


# ============================================================================
# Test calculate_annual_demand_2050 function
# ============================================================================


class TestCalculateAnnualDemand2050:
    """Test calculation of annual demand for 2050."""
    
    def test_calculate_annual_demand_basic(self, sample_hourly_data):
        """Test basic annual demand calculation for 2050."""
        result = calculate_annual_demand_2050(sample_hourly_data)
        
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 3  # BA1, BA2, BA3
        assert 'ba_code' in result.columns
        assert 'annual_demand_mwh' in result.columns
        
        # Verify the calculated values
        # BA1: 100 MW * 72 hours = 7200 MWh
        # BA2: 200 MW * 72 hours = 14400 MWh
        # BA3: 150 MW * 72 hours = 10800 MWh
        ba1_demand = result[result['ba_code'] == 'BA1']['annual_demand_mwh'].values[0]
        ba2_demand = result[result['ba_code'] == 'BA2']['annual_demand_mwh'].values[0]
        ba3_demand = result[result['ba_code'] == 'BA3']['annual_demand_mwh'].values[0]
        
        assert ba1_demand == pytest.approx(7200.0, rel=1e-6)
        assert ba2_demand == pytest.approx(14400.0, rel=1e-6)
        assert ba3_demand == pytest.approx(10800.0, rel=1e-6)
    
    def test_calculate_filters_2050_only(self, sample_hourly_data):
        """Test that calculation only uses 2050 data, filtering out other years."""
        result = calculate_annual_demand_2050(sample_hourly_data)
        
        # BA1 has data in both 2049 and 2050
        # The annual demand should only include 2050 data
        ba1_demand = result[result['ba_code'] == 'BA1']['annual_demand_mwh'].values[0]
        
        # 2050 has 72 hours at 100 MW = 7200 MWh
        # 2049 has 48 hours at 95 MW = 4560 MWh (should be excluded)
        assert ba1_demand == pytest.approx(7200.0, rel=1e-6)
        assert ba1_demand != pytest.approx(11760.0, rel=1e-6)  # Not 2049+2050
    
    def test_calculate_sorted_by_ba_code(self, sample_hourly_data):
        """Test that results are sorted by ba_code."""
        result = calculate_annual_demand_2050(sample_hourly_data)
        
        ba_codes = result['ba_code'].tolist()
        assert ba_codes == sorted(ba_codes)
        assert ba_codes == ['BA1', 'BA2', 'BA3']
    
    def test_calculate_no_2050_data(self, sample_hourly_data_no_2050):
        """Test that ValueError is raised when no 2050 data exists."""
        with pytest.raises(ValueError, match="No data found for year 2050"):
            calculate_annual_demand_2050(sample_hourly_data_no_2050)
    
    def test_calculate_with_single_ba(self):
        """Test calculation with a single BA."""
        data = pd.DataFrame({
            'ba_code': ['BA1'] * 24,
            'year': [2050] * 24,
            'demand_mw': [100.0] * 24
        })
        
        result = calculate_annual_demand_2050(data)
        
        assert len(result) == 1
        assert result['ba_code'].values[0] == 'BA1'
        assert result['annual_demand_mwh'].values[0] == pytest.approx(2400.0, rel=1e-6)
    
    def test_calculate_with_varying_demand(self):
        """Test calculation with time-varying demand values."""
        # Create data with different demand values
        data = []
        for hour in range(24):
            data.append({
                'ba_code': 'BA1',
                'year': 2050,
                'demand_mw': 100.0 + hour  # Ramps from 100 to 123 MW
            })
        df = pd.DataFrame(data)
        
        result = calculate_annual_demand_2050(df)
        
        # Sum = 100 + 101 + 102 + ... + 123 = 24*100 + (0+1+2+...+23)
        # = 2400 + 276 = 2676 MWh
        expected_sum = sum(100.0 + h for h in range(24))
        assert result['annual_demand_mwh'].values[0] == pytest.approx(expected_sum, rel=1e-6)
    
    def test_calculate_with_zero_demand(self):
        """Test calculation when some hours have zero demand."""
        data = pd.DataFrame({
            'ba_code': ['BA1'] * 24,
            'year': [2050] * 24,
            'demand_mw': [0.0] * 12 + [100.0] * 12  # Half hours with zero
        })
        
        result = calculate_annual_demand_2050(data)
        
        assert result['annual_demand_mwh'].values[0] == pytest.approx(1200.0, rel=1e-6)
    
    def test_calculate_preserves_all_bas(self):
        """Test that all BAs are included in results."""
        # Create data with many BAs
        data = []
        for i in range(10):
            for hour in range(24):
                data.append({
                    'ba_code': f'BA{i}',
                    'year': 2050,
                    'demand_mw': float(i * 10)
                })
        df = pd.DataFrame(data)
        
        result = calculate_annual_demand_2050(df)
        
        assert len(result) == 10
        assert set(result['ba_code']) == {f'BA{i}' for i in range(10)}
    
    def test_calculate_handles_negative_values(self):
        """Test calculation with negative demand values (e.g., net exports)."""
        data = pd.DataFrame({
            'ba_code': ['BA1'] * 24,
            'year': [2050] * 24,
            'demand_mw': [-50.0] * 24
        })
        
        result = calculate_annual_demand_2050(data)
        
        # Should correctly sum negative values
        assert result['annual_demand_mwh'].values[0] == pytest.approx(-1200.0, rel=1e-6)
    
    def test_calculate_total_annual_demand(self, sample_hourly_data):
        """Test that total annual demand is correctly summed across all BAs."""
        result = calculate_annual_demand_2050(sample_hourly_data)
        
        total_demand = result['annual_demand_mwh'].sum()
        
        # BA1: 7200, BA2: 14400, BA3: 10800
        expected_total = 7200.0 + 14400.0 + 10800.0
        assert total_demand == pytest.approx(expected_total, rel=1e-6)


# ============================================================================
# Test save_to_csv function
# ============================================================================


class TestSaveToCSV:
    """Test saving annual demand data to CSV files."""
    
    def test_save_to_csv_basic(self, tmp_path):
        """Test basic CSV saving functionality."""
        data = pd.DataFrame({
            'ba_code': ['BA1', 'BA2', 'BA3'],
            'annual_demand_mwh': [1000.0, 2000.0, 3000.0]
        })
        
        output_file = tmp_path / "output.csv"
        save_to_csv(data, str(output_file))
        
        # Verify file was created
        assert output_file.exists()
        
        # Verify content
        loaded = pd.read_csv(output_file)
        assert len(loaded) == 3
        assert list(loaded.columns) == ['ba_code', 'annual_demand_mwh']
        assert loaded['ba_code'].tolist() == ['BA1', 'BA2', 'BA3']
        assert loaded['annual_demand_mwh'].tolist() == pytest.approx([1000.0, 2000.0, 3000.0])
    
    def test_save_creates_parent_directory(self, tmp_path):
        """Test that parent directories are created if they don't exist."""
        output_file = tmp_path / "subdir" / "nested" / "output.csv"
        
        data = pd.DataFrame({
            'ba_code': ['BA1'],
            'annual_demand_mwh': [1000.0]
        })
        
        # Parent directories don't exist yet
        assert not output_file.parent.exists()
        
        save_to_csv(data, str(output_file))
        
        # Verify directories were created
        assert output_file.parent.exists()
        assert output_file.exists()
    
    def test_save_overwrites_existing_file(self, tmp_path):
        """Test that saving overwrites an existing file."""
        output_file = tmp_path / "output.csv"
        
        # Create initial file
        data1 = pd.DataFrame({
            'ba_code': ['BA1'],
            'annual_demand_mwh': [1000.0]
        })
        save_to_csv(data1, str(output_file))
        
        # Verify initial content
        loaded1 = pd.read_csv(output_file)
        assert len(loaded1) == 1
        
        # Overwrite with new data
        data2 = pd.DataFrame({
            'ba_code': ['BA1', 'BA2'],
            'annual_demand_mwh': [1500.0, 2500.0]
        })
        save_to_csv(data2, str(output_file))
        
        # Verify file was overwritten
        loaded2 = pd.read_csv(output_file)
        assert len(loaded2) == 2
        assert loaded2['annual_demand_mwh'].tolist() == pytest.approx([1500.0, 2500.0])
    
    def test_save_empty_dataframe(self, tmp_path):
        """Test saving an empty DataFrame."""
        output_file = tmp_path / "empty.csv"
        
        data = pd.DataFrame({
            'ba_code': [],
            'annual_demand_mwh': []
        })
        
        save_to_csv(data, str(output_file))
        
        assert output_file.exists()
        loaded = pd.read_csv(output_file)
        assert len(loaded) == 0
        assert list(loaded.columns) == ['ba_code', 'annual_demand_mwh']
    
    def test_save_preserves_data_types(self, tmp_path):
        """Test that numeric data types are preserved when saving."""
        output_file = tmp_path / "output.csv"
        
        data = pd.DataFrame({
            'ba_code': ['BA1', 'BA2'],
            'annual_demand_mwh': [1234.5678, 9876.5432]
        })
        
        save_to_csv(data, str(output_file))
        
        loaded = pd.read_csv(output_file)
        assert loaded['annual_demand_mwh'].dtype == float
        assert loaded['annual_demand_mwh'].tolist() == pytest.approx(
            [1234.5678, 9876.5432], rel=1e-6
        )
    
    def test_save_large_dataframe(self, tmp_path):
        """Test saving a large DataFrame with many BAs."""
        output_file = tmp_path / "large.csv"
        
        # Create data with 100 BAs
        data = pd.DataFrame({
            'ba_code': [f'BA{i:03d}' for i in range(100)],
            'annual_demand_mwh': [float(i * 1000) for i in range(100)]
        })
        
        save_to_csv(data, str(output_file))
        
        loaded = pd.read_csv(output_file)
        assert len(loaded) == 100
        assert loaded['ba_code'].tolist() == [f'BA{i:03d}' for i in range(100)]
    
    def test_save_with_special_characters_in_ba_code(self, tmp_path):
        """Test saving data with special characters in ba_code."""
        output_file = tmp_path / "special.csv"
        
        data = pd.DataFrame({
            'ba_code': ['BA-1', 'BA_2', 'BA.3', 'BA/4'],
            'annual_demand_mwh': [1000.0, 2000.0, 3000.0, 4000.0]
        })
        
        save_to_csv(data, str(output_file))
        
        loaded = pd.read_csv(output_file)
        assert loaded['ba_code'].tolist() == ['BA-1', 'BA_2', 'BA.3', 'BA/4']


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """Integration tests for the complete workflow."""
    
    def test_full_workflow(self, tmp_path, sample_hourly_data):
        """Test the complete workflow from loading to saving."""
        # Save sample data to parquet
        input_file = tmp_path / "input.parquet"
        sample_hourly_data.to_parquet(input_file)
        
        # Load the data
        hourly_df = load_hourly_demand(str(input_file))
        
        # Calculate annual demand
        annual_df = calculate_annual_demand_2050(hourly_df)
        
        # Save to CSV
        output_file = tmp_path / "output.csv"
        save_to_csv(annual_df, str(output_file))
        
        # Verify the final output
        result = pd.read_csv(output_file)
        assert len(result) == 3
        assert set(result['ba_code']) == {'BA1', 'BA2', 'BA3'}
        assert result['annual_demand_mwh'].sum() == pytest.approx(32400.0, rel=1e-6)
    
    def test_workflow_with_load_mw_column(self, tmp_path, sample_hourly_data_with_load_mw):
        """Test workflow with data using 'load_mw' instead of 'demand_mw'."""
        input_file = tmp_path / "input_load.parquet"
        sample_hourly_data_with_load_mw.to_parquet(input_file)
        
        hourly_df = load_hourly_demand(str(input_file))
        annual_df = calculate_annual_demand_2050(hourly_df)
        
        output_file = tmp_path / "output_load.csv"
        save_to_csv(annual_df, str(output_file))
        
        result = pd.read_csv(output_file)
        assert len(result) == 1
        assert result['ba_code'].values[0] == 'BA1'
        # 48 hours * 100 MW = 4800 MWh
        assert result['annual_demand_mwh'].values[0] == pytest.approx(4800.0, rel=1e-6)
    
    def test_workflow_rejects_missing_2050_data(self, tmp_path, sample_hourly_data_no_2050):
        """Test that workflow fails gracefully when 2050 data is missing."""
        input_file = tmp_path / "no_2050.parquet"
        sample_hourly_data_no_2050.to_parquet(input_file)
        
        hourly_df = load_hourly_demand(str(input_file))
        
        with pytest.raises(ValueError, match="No data found for year 2050"):
            calculate_annual_demand_2050(hourly_df)
    
    def test_workflow_with_multiple_years(self, tmp_path):
        """Test workflow correctly handles data with multiple years."""
        # Create data spanning multiple years
        data = []
        for year in [2045, 2047, 2050, 2052]:
            for hour in range(24):
                data.append({
                    'ba_code': 'BA1',
                    'year': year,
                    'demand_mw': 100.0 + year - 2050  # Varies by year
                })
        df = pd.DataFrame(data)
        
        input_file = tmp_path / "multi_year.parquet"
        df.to_parquet(input_file)
        
        hourly_df = load_hourly_demand(str(input_file))
        annual_df = calculate_annual_demand_2050(hourly_df)
        
        # Should only include 2050 data (100 MW * 24 hours)
        assert len(annual_df) == 1
        assert annual_df['annual_demand_mwh'].values[0] == pytest.approx(2400.0, rel=1e-6)
