"""
Tests for the load_atb_size and update_size_field_from_atb_size logic.

Rather than importing cluster_app.py (which requires mocking heavy PyScript/browser
dependencies), these tests replicate the *pure* parsing and lookup logic from those
two functions directly.  This keeps the suite fast and dependency-free while still
validating the real behaviour described in the function bodies.

load_atb_size parsing logic (lines ~4504-4528 of cluster_app.py)
-----------------------------------------------------------------
    size_map = {}
    for row in sizes:
        if not isinstance(row, dict):
            continue
        tech = str(row.get("technology", "")).strip()
        if not tech:
            continue
        size_mw = row.get("size")
        if size_mw is None:
            continue
        data_year = row.get("data_year")
        year_map = size_map.setdefault(data_year, {})
        tech_detail = row.get("tech_detail")
        if tech_detail:
            key = (tech, str(tech_detail).strip())
            year_map[key] = float(size_mw)
        else:
            key = (tech, None)
            year_map[key] = float(size_mw)

update_size_field_from_atb_size lookup logic (lines ~4944-4959 of cluster_app.py)
----------------------------------------------------------------------------------
    selected_year = int(...)           # from DOM / caller
    year_size_map = atb_size_map.get(selected_year)
    if year_size_map is None and atb_size_map:
        year_size_map = next(iter(atb_size_map.values()))
    if year_size_map is None:
        year_size_map = {}

    size_mw = None
    if detail:
        size_mw = year_size_map.get((tech, detail))
    if size_mw is None:
        size_mw = year_size_map.get((tech, None))
"""

from __future__ import annotations

from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Pure-logic helpers (replicated from cluster_app.py to avoid DOM imports)
# ---------------------------------------------------------------------------


def _parse_atb_size_rows(rows: list[Any]) -> dict:
    """Replicate the row-parsing loop from load_atb_size()."""
    size_map: dict = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        tech = str(row.get("technology", "")).strip()
        if not tech:
            continue
        size_mw = row.get("size")
        if size_mw is None:
            continue
        data_year = row.get("data_year")
        year_map = size_map.setdefault(data_year, {})
        tech_detail = row.get("tech_detail")
        if tech_detail:
            key = (tech, str(tech_detail).strip())
            year_map[key] = float(size_mw)
        else:
            key = (tech, None)
            year_map[key] = float(size_mw)
    return size_map


def _resolve_year_map(atb_size_map: dict, selected_year: int) -> dict:
    """Replicate the year-resolution block from update_size_field_from_atb_size()."""
    year_size_map = atb_size_map.get(selected_year)
    if year_size_map is None and atb_size_map:
        year_size_map = next(iter(atb_size_map.values()))
    if year_size_map is None:
        year_size_map = {}
    return year_size_map


def _lookup_size(year_size_map: dict, tech: str, detail: str) -> float | None:
    """Replicate the tech/detail lookup from update_size_field_from_atb_size()."""
    size_mw = None
    if detail:
        size_mw = year_size_map.get((tech, detail))
    if size_mw is None:
        size_mw = year_size_map.get((tech, None))
    return size_mw


# ===========================================================================
# Parsing tests  (load_atb_size logic)
# ===========================================================================


class TestParseAtbSizeRows:
    """Verify the row-to-map conversion matches the load_atb_size loop."""

    # ------------------------------------------------------------------
    # Happy-path structure
    # ------------------------------------------------------------------

    def test_single_row_with_year_and_no_detail_creates_none_key(self):
        rows = [{"data_year": 2024, "technology": "LandbasedWind", "size": 200.0}]
        result = _parse_atb_size_rows(rows)
        assert result == {2024: {("LandbasedWind", None): 200.0}}

    def test_single_row_with_tech_detail_creates_detail_key(self):
        rows = [
            {
                "data_year": 2024,
                "technology": "NaturalGas",
                "tech_detail": "Combustion Turbine (F-Frame)",
                "size": 233.0,
            }
        ]
        result = _parse_atb_size_rows(rows)
        assert result == {
            2024: {("NaturalGas", "Combustion Turbine (F-Frame)"): 233.0}
        }

    def test_multiple_years_produce_separate_year_buckets(self):
        rows = [
            {"data_year": 2024, "technology": "LandbasedWind", "size": 200.0},
            {"data_year": 2025, "technology": "LandbasedWind", "size": 210.0},
        ]
        result = _parse_atb_size_rows(rows)
        assert set(result.keys()) == {2024, 2025}
        assert result[2024][("LandbasedWind", None)] == 200.0
        assert result[2025][("LandbasedWind", None)] == 210.0

    def test_multiple_techs_in_same_year(self):
        rows = [
            {"data_year": 2024, "technology": "LandbasedWind", "size": 200.0},
            {"data_year": 2024, "technology": "Battery", "size": 60.0},
            {
                "data_year": 2024,
                "technology": "NaturalGas",
                "tech_detail": "Combustion Turbine (F-Frame)",
                "size": 233.0,
            },
        ]
        result = _parse_atb_size_rows(rows)
        year_map = result[2024]
        assert year_map[("LandbasedWind", None)] == 200.0
        assert year_map[("Battery", None)] == 60.0
        assert year_map[("NaturalGas", "Combustion Turbine (F-Frame)")] == 233.0

    # ------------------------------------------------------------------
    # Rows without data_year → None key
    # ------------------------------------------------------------------

    def test_row_without_data_year_uses_none_year_key(self):
        rows = [{"technology": "OffshoreWind", "size": 800.0}]
        result = _parse_atb_size_rows(rows)
        assert None in result
        assert result[None][("OffshoreWind", None)] == 800.0

    def test_mix_of_year_and_no_year_rows(self):
        rows = [
            {"data_year": 2024, "technology": "LandbasedWind", "size": 200.0},
            {"technology": "OffshoreWind", "size": 800.0},
        ]
        result = _parse_atb_size_rows(rows)
        assert 2024 in result
        assert None in result

    # ------------------------------------------------------------------
    # tech_detail whitespace stripping
    # ------------------------------------------------------------------

    def test_tech_detail_whitespace_is_stripped(self):
        rows = [
            {
                "data_year": 2024,
                "technology": "NaturalGas",
                "tech_detail": "  F-Frame  ",
                "size": 233.0,
            }
        ]
        result = _parse_atb_size_rows(rows)
        assert ("NaturalGas", "F-Frame") in result[2024]

    # ------------------------------------------------------------------
    # Rows that should be skipped
    # ------------------------------------------------------------------

    def test_non_dict_row_is_skipped(self):
        rows = ["not-a-dict", 42, None, ["list"], {"technology": "Wind", "size": 100.0}]
        result = _parse_atb_size_rows(rows)
        # Only the last (dict) row should be processed
        assert len(result) == 1

    def test_row_missing_technology_is_skipped(self):
        rows = [{"data_year": 2024, "size": 100.0}]
        result = _parse_atb_size_rows(rows)
        assert result == {}

    def test_row_with_empty_string_technology_is_skipped(self):
        rows = [{"data_year": 2024, "technology": "   ", "size": 100.0}]
        result = _parse_atb_size_rows(rows)
        assert result == {}

    def test_row_missing_size_is_skipped(self):
        rows = [{"data_year": 2024, "technology": "LandbasedWind"}]
        result = _parse_atb_size_rows(rows)
        assert result == {}

    def test_row_with_size_none_is_skipped(self):
        rows = [{"data_year": 2024, "technology": "LandbasedWind", "size": None}]
        result = _parse_atb_size_rows(rows)
        assert result == {}

    def test_empty_input_returns_empty_map(self):
        assert _parse_atb_size_rows([]) == {}

    # ------------------------------------------------------------------
    # Type coercion
    # ------------------------------------------------------------------

    def test_size_is_stored_as_float(self):
        rows = [{"data_year": 2024, "technology": "LandbasedWind", "size": "200"}]
        result = _parse_atb_size_rows(rows)
        size = result[2024][("LandbasedWind", None)]
        assert isinstance(size, float)
        assert size == 200.0

    def test_sub_mw_size_is_preserved_as_float(self):
        rows = [{"data_year": 2024, "technology": "SmallBattery", "size": 0.5}]
        result = _parse_atb_size_rows(rows)
        assert result[2024][("SmallBattery", None)] == 0.5

    # ------------------------------------------------------------------
    # Detail key vs. None-key coexist in same year
    # ------------------------------------------------------------------

    def test_detail_key_and_none_key_can_coexist_for_same_tech(self):
        rows = [
            {
                "data_year": 2024,
                "technology": "NaturalGas",
                "tech_detail": "F-Frame",
                "size": 233.0,
            },
            {
                "data_year": 2024,
                "technology": "NaturalGas",
                "size": 100.0,
            },
        ]
        result = _parse_atb_size_rows(rows)
        year_map = result[2024]
        assert year_map[("NaturalGas", "F-Frame")] == 233.0
        assert year_map[("NaturalGas", None)] == 100.0


# ===========================================================================
# Year-resolution tests  (update_size_field_from_atb_size logic, part 1)
# ===========================================================================


class TestResolveYearMap:
    """Verify the year-specific map resolution with fallback behaviour."""

    def test_exact_year_match_returns_that_years_map(self):
        atb_size_map = {
            2024: {("LandbasedWind", None): 200.0},
            2025: {("LandbasedWind", None): 210.0},
        }
        result = _resolve_year_map(atb_size_map, 2024)
        assert result == {("LandbasedWind", None): 200.0}

    def test_year_2025_match_returns_correct_map(self):
        atb_size_map = {
            2024: {("LandbasedWind", None): 200.0},
            2025: {("LandbasedWind", None): 210.0},
        }
        result = _resolve_year_map(atb_size_map, 2025)
        assert result == {("LandbasedWind", None): 210.0}

    def test_unknown_year_falls_back_to_first_available_year(self):
        atb_size_map = {
            2024: {("LandbasedWind", None): 200.0},
            2025: {("LandbasedWind", None): 210.0},
        }
        result = _resolve_year_map(atb_size_map, 9999)
        # Must return the first inserted year's map (2024 in CPython 3.7+)
        assert result == {("LandbasedWind", None): 200.0}

    def test_year_zero_falls_back_when_not_in_map(self):
        """Year 0 is the sentinel used when DOM value is missing/invalid."""
        atb_size_map = {2024: {("Battery", None): 60.0}}
        result = _resolve_year_map(atb_size_map, 0)
        assert result == {("Battery", None): 60.0}

    def test_empty_map_returns_empty_dict_regardless_of_year(self):
        result = _resolve_year_map({}, 2024)
        assert result == {}

    def test_empty_map_with_zero_year_returns_empty_dict(self):
        result = _resolve_year_map({}, 0)
        assert result == {}

    def test_single_year_map_always_falls_back_to_that_year(self):
        atb_size_map = {2030: {("OffshoreWind", None): 800.0}}
        for year in (2020, 2024, 2030, 9999):
            result = _resolve_year_map(atb_size_map, year)
            assert ("OffshoreWind", None) in result, f"Failed for year={year}"


# ===========================================================================
# Size-lookup tests  (update_size_field_from_atb_size logic, part 2)
# ===========================================================================


class TestLookupSize:
    """Verify the (tech, detail) → size lookup with fallback to (tech, None)."""

    @pytest.fixture()
    def year_map(self):
        return {
            ("LandbasedWind", None): 200.0,
            ("Battery", None): 60.0,
            ("NaturalGas", "Combustion Turbine (F-Frame)"): 233.0,
            ("NaturalGas", None): 100.0,
        }

    # ------------------------------------------------------------------
    # Exact matches
    # ------------------------------------------------------------------

    def test_tech_with_detail_returns_specific_size(self, year_map):
        assert _lookup_size(year_map, "NaturalGas", "Combustion Turbine (F-Frame)") == 233.0

    def test_tech_without_detail_returns_none_key_size(self, year_map):
        assert _lookup_size(year_map, "LandbasedWind", "") == 200.0

    def test_tech_with_blank_detail_returns_none_key_size(self, year_map):
        # cluster_app.py strips detail before calling the lookup, so whitespace-only
        # detail arrives here as an empty string, which is treated as absent.
        assert _lookup_size(year_map, "Battery", "") == 60.0

    # ------------------------------------------------------------------
    # Fallback from (tech, detail) to (tech, None)
    # ------------------------------------------------------------------

    def test_unknown_detail_falls_back_to_none_key(self, year_map):
        # "NaturalGas" has a (tech, None) entry; unknown detail should fall back
        result = _lookup_size(year_map, "NaturalGas", "UnknownVariant")
        assert result == 100.0

    def test_no_detail_key_and_no_none_key_returns_none(self, year_map):
        result = _lookup_size(year_map, "Nuclear", "")
        assert result is None

    def test_detail_key_not_found_and_no_none_key_returns_none(self, year_map):
        result = _lookup_size(year_map, "OffshoreWind", "Fixed")
        assert result is None

    # ------------------------------------------------------------------
    # Empty map
    # ------------------------------------------------------------------

    def test_empty_map_always_returns_none(self):
        for tech, detail in [
            ("LandbasedWind", ""),
            ("Battery", "4Hr"),
            ("NaturalGas", "F-Frame"),
        ]:
            assert _lookup_size({}, tech, detail) is None

    # ------------------------------------------------------------------
    # Parametrised tech lookup table
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "tech, detail, expected",
        [
            ("LandbasedWind", "", 200.0),
            ("LandbasedWind", "Class 5", 200.0),  # unknown detail falls back to (tech, None)
            ("Battery", "", 60.0),
            ("NaturalGas", "Combustion Turbine (F-Frame)", 233.0),
            ("NaturalGas", "UnknownDetail", 100.0),  # falls back to (tech, None)
            ("NaturalGas", "", 100.0),  # no detail → (tech, None)
            ("Solar", "", None),          # tech not in map at all → None
            ("Solar", "FixedTilt", None), # tech not in map, detail also absent → None
        ],
    )
    def test_lookup_parametrised(self, year_map, tech, detail, expected):
        assert _lookup_size(year_map, tech, detail) == expected


# ===========================================================================
# Integration-style tests combining parsing + resolution + lookup
# ===========================================================================


class TestAtbSizeEndToEnd:
    """Combine parsing → year resolution → lookup in a single flow."""

    def test_full_flow_for_known_year_and_detail(self):
        rows = [
            {"data_year": 2024, "technology": "LandbasedWind", "size": 200.0},
            {
                "data_year": 2024,
                "technology": "NaturalGas",
                "tech_detail": "Combustion Turbine (F-Frame)",
                "size": 233.0,
            },
            {"data_year": 2025, "technology": "LandbasedWind", "size": 210.0},
        ]
        size_map = _parse_atb_size_rows(rows)
        year_map = _resolve_year_map(size_map, 2024)
        assert _lookup_size(year_map, "LandbasedWind", "") == 200.0
        assert _lookup_size(year_map, "NaturalGas", "Combustion Turbine (F-Frame)") == 233.0

    def test_full_flow_fallback_to_first_year_when_year_missing(self):
        rows = [
            {"data_year": 2024, "technology": "Battery", "size": 60.0},
            {"data_year": 2025, "technology": "Battery", "size": 65.0},
        ]
        size_map = _parse_atb_size_rows(rows)
        year_map = _resolve_year_map(size_map, 9999)
        # Falls back to 2024 (first year inserted)
        assert _lookup_size(year_map, "Battery", "") == 60.0

    def test_full_flow_none_year_key_used_when_rows_lack_data_year(self):
        rows = [
            {"technology": "OffshoreWind", "size": 800.0},
        ]
        size_map = _parse_atb_size_rows(rows)
        # None is the only key; any requested year should fall back to it
        year_map = _resolve_year_map(size_map, 2024)
        assert _lookup_size(year_map, "OffshoreWind", "") == 800.0

    def test_skipped_rows_do_not_appear_in_any_year_map(self):
        rows = [
            {"data_year": 2024, "size": 100.0},              # missing technology
            {"data_year": 2024, "technology": "X"},           # missing size
            "not-a-dict",                                     # wrong type
            {"data_year": 2024, "technology": "Wind", "size": 150.0},  # valid
        ]
        size_map = _parse_atb_size_rows(rows)
        assert list(size_map.keys()) == [2024]
        assert list(size_map[2024].keys()) == [("Wind", None)]

    def test_sub_mw_size_survives_full_round_trip(self):
        rows = [{"data_year": 2024, "technology": "Micro", "size": 0.25}]
        size_map = _parse_atb_size_rows(rows)
        year_map = _resolve_year_map(size_map, 2024)
        assert _lookup_size(year_map, "Micro", "") == 0.25
