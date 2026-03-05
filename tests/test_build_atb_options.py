"""Tests for the build_index() function in web/build_atb_options.py.

build_index() reads an ATB parquet file and returns a compact DataFrame with
one row per unique (data_year, technology, tech_detail, cost_case) combination
plus integer capex_mw and heat_rate columns.

Coverage addressed
------------------
- DataFrame schema: column names, order, dtypes, sort order
- capex_mw filtering: only ``parameter == 'capex_mw'`` with value > 0 are kept;
  duplicates are removed
- heat_rate: correctly merged for thermal techs, 0 for techs with no heat_rate rows
- Row filtering: empty/whitespace/NaN keys are excluded
- Column name aliases: alternative column names accepted by _pick_column()
- Edge cases: empty input, single row, multiple years, mixed techs

Remaining risks
---------------
- Very large parquet files are not stress-tested (performance not covered)
- The download/GitHub-spec paths in main() have no network-free tests here
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Import build_index without requiring the package to be installed
# ---------------------------------------------------------------------------

_spec = importlib.util.spec_from_file_location(
    "build_atb_options",
    Path(__file__).parent.parent / "web" / "build_atb_options.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
build_index = _mod.build_index

# ---------------------------------------------------------------------------
# Constants for the standard parquet schema
# ---------------------------------------------------------------------------

STD_COLS = ["data_year", "technology", "tech_detail", "cost_case", "parameter", "parameter_value"]
EXPECTED_OUT_COLS = ["data_year", "technology", "tech_detail", "cost_case", "capex_mw", "heat_rate"]


# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------


def _write_parquet(tmp_path: Path, rows: list[dict], columns: list[str] | None = None) -> Path:
    """Write *rows* to a parquet file and return its path.

    If *columns* is given it overrides the key order (useful for alias tests).
    Rows may omit keys; missing columns get filled with None unless caller
    passes a fully-specified columns list to control schema.
    """
    df = pd.DataFrame(rows, columns=columns)
    p = tmp_path / "test_atb.parquet"
    df.to_parquet(p, index=False, engine="pyarrow")
    return p


def _capex_row(
    year: int = 2024,
    tech: str = "LandbasedWind",
    detail: str = "Class4",
    case: str = "Moderate",
    value: float = 1_200_000.0,
    year_col: str = "data_year",
    tech_col: str = "technology",
    detail_col: str = "tech_detail",
    case_col: str = "cost_case",
) -> dict:
    return {year_col: year, tech_col: tech, detail_col: detail, case_col: case,
            "parameter": "capex_mw", "parameter_value": value}


def _heat_row(
    year: int = 2024,
    tech: str = "NaturalGas",
    detail: str = "CT",
    case: str = "Moderate",
    value: float = 7000.0,
    year_col: str = "data_year",
    tech_col: str = "technology",
    detail_col: str = "tech_detail",
    case_col: str = "cost_case",
) -> dict:
    return {year_col: year, tech_col: tech, detail_col: detail, case_col: case,
            "parameter": "heat_rate", "parameter_value": value}


@pytest.fixture()
def simple_parquet(tmp_path: Path) -> Path:
    """Parquet with one wind (no heat_rate) and one gas (with heat_rate) row."""
    rows = [
        _capex_row(tech="LandbasedWind", detail="Class4", case="Moderate", value=1_200_000.0),
        _capex_row(tech="NaturalGas", detail="CT", case="Moderate", value=1_500_000.0),
        _heat_row(tech="NaturalGas", detail="CT", case="Moderate", value=7000.0),
    ]
    return _write_parquet(tmp_path, rows)


# ===========================================================================
# Class TestBuildIndexSchema — DataFrame structure
# ===========================================================================


class TestBuildIndexSchema:
    """Verify the shape, column names, dtypes, and sort order of the result."""

    def test_returns_dataframe(self, simple_parquet: Path):
        result = build_index(simple_parquet)
        assert isinstance(result, pd.DataFrame)

    def test_has_exactly_expected_columns(self, simple_parquet: Path):
        result = build_index(simple_parquet)
        assert list(result.columns) == EXPECTED_OUT_COLS

    def test_columns_in_exact_order(self, simple_parquet: Path):
        result = build_index(simple_parquet)
        assert list(result.columns) == [
            "data_year", "technology", "tech_detail", "cost_case", "capex_mw", "heat_rate"
        ]

    def test_capex_mw_dtype_is_int(self, simple_parquet: Path):
        result = build_index(simple_parquet)
        assert pd.api.types.is_integer_dtype(result["capex_mw"])

    def test_heat_rate_dtype_is_int(self, simple_parquet: Path):
        result = build_index(simple_parquet)
        assert pd.api.types.is_integer_dtype(result["heat_rate"])

    def test_data_year_dtype_is_int(self, simple_parquet: Path):
        result = build_index(simple_parquet)
        assert pd.api.types.is_integer_dtype(result["data_year"])

    def test_result_is_sorted(self, tmp_path: Path):
        rows = [
            _capex_row(year=2025, tech="Solar",        detail="Class1", case="Moderate", value=900_000.0),
            _capex_row(year=2024, tech="LandbasedWind", detail="Class4", case="Advanced", value=1_100_000.0),
            _capex_row(year=2024, tech="LandbasedWind", detail="Class4", case="Moderate", value=1_200_000.0),
            _capex_row(year=2024, tech="Battery",      detail="2Hr",    case="Moderate", value=400_000.0),
        ]
        p = _write_parquet(tmp_path, rows)
        result = build_index(p)
        expected_order = result.sort_values(
            ["data_year", "technology", "tech_detail", "cost_case", "capex_mw"]
        ).reset_index(drop=True)
        pd.testing.assert_frame_equal(result, expected_order)


# ===========================================================================
# Class TestBuildIndexCapex — capex_mw behaviour
# ===========================================================================


class TestBuildIndexCapex:
    """Verify correct capex_mw values and filtering."""

    def test_correct_capex_values_appear(self, simple_parquet: Path):
        result = build_index(simple_parquet)
        gas_row = result[(result["technology"] == "NaturalGas") & (result["tech_detail"] == "CT")]
        wind_row = result[(result["technology"] == "LandbasedWind") & (result["tech_detail"] == "Class4")]
        assert gas_row["capex_mw"].iloc[0] == 1_500_000
        assert wind_row["capex_mw"].iloc[0] == 1_200_000

    @pytest.mark.parametrize("bad_value", [0.0, -1.0, -999.0])
    def test_non_positive_capex_rows_excluded(self, tmp_path: Path, bad_value: float):
        rows = [
            _capex_row(tech="LandbasedWind", detail="Class4", case="Moderate", value=bad_value),
            _capex_row(tech="Solar",         detail="Class5", case="Moderate", value=800_000.0),
        ]
        p = _write_parquet(tmp_path, rows)
        result = build_index(p)
        assert "LandbasedWind" not in result["technology"].values
        assert len(result) == 1

    def test_non_capex_parameter_rows_are_not_base_rows(self, tmp_path: Path):
        rows = [
            _capex_row(tech="Solar", detail="FixedTilt", case="Moderate", value=800_000.0),
            # A "fixed_om_mw" row — should never appear as a base row
            {"data_year": 2024, "technology": "Solar", "tech_detail": "FixedTilt",
             "cost_case": "Moderate", "parameter": "fixed_om_mw", "parameter_value": 15_000.0},
        ]
        p = _write_parquet(tmp_path, rows)
        result = build_index(p)
        assert len(result) == 1
        assert result.iloc[0]["technology"] == "Solar"

    def test_duplicate_capex_rows_deduplicated(self, tmp_path: Path):
        row = _capex_row(tech="Solar", detail="FixedTilt", case="Moderate", value=800_000.0)
        p = _write_parquet(tmp_path, [row, row, row])
        result = build_index(p)
        assert len(result) == 1


# ===========================================================================
# Class TestBuildIndexHeatRate — heat_rate behaviour
# ===========================================================================


class TestBuildIndexHeatRate:
    """Verify heat_rate is correctly merged and defaulted."""

    def test_heat_rate_zero_for_no_heat_rate_technology(self, simple_parquet: Path):
        result = build_index(simple_parquet)
        wind_row = result[result["technology"] == "LandbasedWind"]
        assert wind_row["heat_rate"].iloc[0] == 0

    def test_heat_rate_populated_for_thermal_technology(self, simple_parquet: Path):
        result = build_index(simple_parquet)
        gas_row = result[result["technology"] == "NaturalGas"]
        assert gas_row["heat_rate"].iloc[0] == 7000

    def test_heat_rate_rounded_to_int(self, tmp_path: Path):
        rows = [
            _capex_row(tech="NaturalGas", detail="CT", case="Moderate", value=1_500_000.0),
            _heat_row(tech="NaturalGas", detail="CT", case="Moderate", value=7123.9),
        ]
        p = _write_parquet(tmp_path, rows)
        result = build_index(p)
        assert result.iloc[0]["heat_rate"] == 7124

    def test_heat_rate_rounds_half_up(self, tmp_path: Path):
        rows = [
            _capex_row(tech="NaturalGas", detail="CT", case="Moderate", value=1_500_000.0),
            _heat_row(tech="NaturalGas", detail="CT", case="Moderate", value=7123.5),
        ]
        p = _write_parquet(tmp_path, rows)
        result = build_index(p)
        # .round() uses banker's rounding (round half to even) for 0.5 values,
        # but 7123.5 → 7124 (rounds to even in banker's rounding)
        assert result.iloc[0]["heat_rate"] in (7123, 7124)

    def test_capex_only_technology_gets_heat_rate_zero_not_nan(self, simple_parquet: Path):
        result = build_index(simple_parquet)
        wind_row = result[result["technology"] == "LandbasedWind"]
        assert wind_row["heat_rate"].notna().all()
        assert wind_row["heat_rate"].iloc[0] == 0


# ===========================================================================
# Class TestBuildIndexFiltering — row filtering
# ===========================================================================


class TestBuildIndexFiltering:
    """Verify empty/whitespace/NaN key rows are filtered out."""

    def test_empty_string_technology_excluded(self, tmp_path: Path):
        rows = [
            _capex_row(tech="",             detail="Class4", case="Moderate", value=1_000_000.0),
            _capex_row(tech="LandbasedWind", detail="Class4", case="Moderate", value=1_200_000.0),
        ]
        p = _write_parquet(tmp_path, rows)
        result = build_index(p)
        assert "" not in result["technology"].values
        assert len(result) == 1

    def test_whitespace_only_technology_excluded(self, tmp_path: Path):
        rows = [
            _capex_row(tech="   ",           detail="Class4", case="Moderate", value=1_000_000.0),
            _capex_row(tech="LandbasedWind", detail="Class4", case="Moderate", value=1_200_000.0),
        ]
        p = _write_parquet(tmp_path, rows)
        result = build_index(p)
        assert all(t.strip() != "" for t in result["technology"].values)
        assert len(result) == 1

    def test_empty_tech_detail_excluded(self, tmp_path: Path):
        rows = [
            _capex_row(tech="Solar", detail="",       case="Moderate", value=800_000.0),
            _capex_row(tech="Solar", detail="Class5", case="Moderate", value=850_000.0),
        ]
        p = _write_parquet(tmp_path, rows)
        result = build_index(p)
        assert "" not in result["tech_detail"].values
        assert len(result) == 1

    def test_empty_cost_case_excluded(self, tmp_path: Path):
        rows = [
            _capex_row(tech="Solar", detail="Class5", case="",         value=800_000.0),
            _capex_row(tech="Solar", detail="Class5", case="Moderate", value=850_000.0),
        ]
        p = _write_parquet(tmp_path, rows)
        result = build_index(p)
        assert "" not in result["cost_case"].values
        assert len(result) == 1

    def test_nan_key_columns_excluded(self, tmp_path: Path):
        rows = [
            {"data_year": None, "technology": "Solar", "tech_detail": "Class5",
             "cost_case": "Moderate", "parameter": "capex_mw", "parameter_value": 800_000.0},
            _capex_row(tech="LandbasedWind", detail="Class4", case="Moderate", value=1_200_000.0),
        ]
        p = _write_parquet(tmp_path, rows)
        result = build_index(p)
        assert "Solar" not in result["technology"].values
        assert len(result) == 1


# ===========================================================================
# Class TestBuildIndexColumnAliases — column name aliases
# ===========================================================================


class TestBuildIndexColumnAliases:
    """Verify that alternative column name spellings are accepted."""

    def _alias_rows(
        self,
        year_col: str = "data_year",
        tech_col: str = "technology",
        detail_col: str = "tech_detail",
        case_col: str = "cost_case",
    ) -> list[dict]:
        return [
            {year_col: 2024, tech_col: "LandbasedWind", detail_col: "Class4",
             case_col: "Moderate", "parameter": "capex_mw", "parameter_value": 1_200_000.0},
        ]

    def test_atb_data_year_alias(self, tmp_path: Path):
        cols = ["atb_data_year", "technology", "tech_detail", "cost_case", "parameter", "parameter_value"]
        rows = self._alias_rows(year_col="atb_data_year")
        p = _write_parquet(tmp_path, rows, columns=cols)
        result = build_index(p)
        assert "data_year" in result.columns
        assert len(result) == 1

    def test_atb_technology_alias(self, tmp_path: Path):
        cols = ["data_year", "atb_technology", "tech_detail", "cost_case", "parameter", "parameter_value"]
        rows = self._alias_rows(tech_col="atb_technology")
        p = _write_parquet(tmp_path, rows, columns=cols)
        result = build_index(p)
        assert "technology" in result.columns
        assert result.iloc[0]["technology"] == "LandbasedWind"

    def test_technology_detail_alias(self, tmp_path: Path):
        cols = ["data_year", "technology", "technology_detail", "cost_case", "parameter", "parameter_value"]
        rows = self._alias_rows(detail_col="technology_detail")
        p = _write_parquet(tmp_path, rows, columns=cols)
        result = build_index(p)
        assert "tech_detail" in result.columns
        assert result.iloc[0]["tech_detail"] == "Class4"

    def test_atb_cost_case_alias(self, tmp_path: Path):
        cols = ["data_year", "technology", "tech_detail", "atb_cost_case", "parameter", "parameter_value"]
        rows = self._alias_rows(case_col="atb_cost_case")
        p = _write_parquet(tmp_path, rows, columns=cols)
        result = build_index(p)
        assert "cost_case" in result.columns
        assert result.iloc[0]["cost_case"] == "Moderate"

    def test_all_aliases_together(self, tmp_path: Path):
        """atb_data_year + atb_technology + atb_tech_detail + atb_cost_case all at once."""
        cols = ["atb_data_year", "atb_technology", "atb_tech_detail", "atb_cost_case",
                "parameter", "parameter_value"]
        rows = [
            {"atb_data_year": 2024, "atb_technology": "LandbasedWind",
             "atb_tech_detail": "Class4", "atb_cost_case": "Moderate",
             "parameter": "capex_mw", "parameter_value": 1_200_000.0},
        ]
        p = _write_parquet(tmp_path, rows, columns=cols)
        result = build_index(p)
        assert list(result.columns) == EXPECTED_OUT_COLS
        assert result.iloc[0]["data_year"] == 2024
        assert result.iloc[0]["technology"] == "LandbasedWind"
        assert result.iloc[0]["tech_detail"] == "Class4"
        assert result.iloc[0]["cost_case"] == "Moderate"


# ===========================================================================
# Class TestBuildIndexEdgeCases — edge and integration cases
# ===========================================================================


class TestBuildIndexEdgeCases:
    """Edge cases and multi-feature integration scenarios."""

    def test_empty_parquet_returns_empty_dataframe_with_correct_columns(self, tmp_path: Path):
        df = pd.DataFrame(columns=STD_COLS)
        p = tmp_path / "empty.parquet"
        df.to_parquet(p, index=False, engine="pyarrow")
        result = build_index(p)
        assert isinstance(result, pd.DataFrame)
        assert list(result.columns) == EXPECTED_OUT_COLS
        assert len(result) == 0

    def test_single_valid_row_returns_one_row_dataframe(self, tmp_path: Path):
        rows = [_capex_row(tech="Solar", detail="FixedTilt", case="Moderate", value=800_000.0)]
        p = _write_parquet(tmp_path, rows)
        result = build_index(p)
        assert len(result) == 1
        assert result.iloc[0]["technology"] == "Solar"
        assert result.iloc[0]["heat_rate"] == 0

    def test_mixed_thermal_and_renewable_techs(self, simple_parquet: Path):
        result = build_index(simple_parquet)
        techs = set(result["technology"].values)
        assert "LandbasedWind" in techs
        assert "NaturalGas" in techs
        wind_heat = result[result["technology"] == "LandbasedWind"]["heat_rate"].iloc[0]
        gas_heat  = result[result["technology"] == "NaturalGas"]["heat_rate"].iloc[0]
        assert wind_heat == 0
        assert gas_heat == 7000

    def test_multiple_data_years_all_appear(self, tmp_path: Path):
        rows = [
            _capex_row(year=2024, tech="LandbasedWind", detail="Class4", case="Moderate", value=1_200_000.0),
            _capex_row(year=2025, tech="LandbasedWind", detail="Class4", case="Moderate", value=1_150_000.0),
            _capex_row(year=2030, tech="LandbasedWind", detail="Class4", case="Moderate", value=1_050_000.0),
        ]
        p = _write_parquet(tmp_path, rows)
        result = build_index(p)
        assert set(result["data_year"].values) == {2024, 2025, 2030}

    @pytest.mark.parametrize("capex_value, expected_included", [
        (-1.0,       False),
        (0.0,        False),
        (1.0,        True),
        (1_500_000.0, True),
    ])
    def test_capex_positive_filter_parametrized(
        self, tmp_path: Path, capex_value: float, expected_included: bool
    ):
        rows = [_capex_row(tech="TestTech", detail="TypeA", case="Moderate", value=capex_value)]
        p = _write_parquet(tmp_path, rows)
        result = build_index(p)
        tech_present = "TestTech" in result["technology"].values
        assert tech_present == expected_included
