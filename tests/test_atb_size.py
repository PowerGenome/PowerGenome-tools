"""
Tests for the ATB size loading and lookup logic from web/cluster_app.py.

Covers:
- parse_atb_size_payload()  — replicates load_atb_size() lines 4488-4528
- lookup_size_from_map()    — replicates update_size_field_from_atb_size() lines 4924-4955
- format_size_value()       — replicates the size-formatting branch inside
                              update_size_field_from_atb_size()

NOTE: These helpers are replicated inline (without DOM / PyScript dependencies)
following the same pattern used in test_cluster_app.py and
test_resource_attribute_overrides.py.
"""

import pytest


# ============================================================================
# Helpers replicated from web/cluster_app.py (no DOM / fetch dependencies)
# ============================================================================


def parse_atb_size_payload(payload):
    """Parse an atb_size.json payload dict and return a size_map dict.

    Replicates the core logic of load_atb_size() without DOM/fetch dependencies.
    Keys are (technology, tech_detail) where tech_detail may be None.
    """
    sizes = payload.get("size", []) if isinstance(payload, dict) else []
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
        tech_detail = row.get("tech_detail")
        if tech_detail:
            key = (tech, str(tech_detail).strip())
            size_map[key] = float(size_mw)
        else:
            key = (tech, None)
            size_map[key] = float(size_mw)
    return size_map


def lookup_size_from_map(size_map, tech, detail):
    """Look up size from atb_size_map for given tech and tech_detail.

    Replicates the lookup logic of update_size_field_from_atb_size() without
    DOM dependencies.  Returns the size_mw float, or None if not found.
    """
    if not tech:
        return None
    size_mw = None
    if detail:
        size_mw = size_map.get((tech, detail))
    if size_mw is None:
        size_mw = size_map.get((tech, None))
    return size_mw


def format_size_value(size_mw):
    """Format a size value for display in the size field.

    Replicates the formatting branch inside update_size_field_from_atb_size():
    - None          → "100"  (default)
    - sub-1 MW      → str(size_mw) preserving decimal  (e.g. "0.5")
    - >= 1 MW       → str(int(round(size_mw)))          (e.g. "100")
    """
    if size_mw is not None:
        if size_mw < 1:
            return str(size_mw)
        else:
            return str(int(round(size_mw)))
    else:
        return "100"


# ============================================================================
# Tests – parse_atb_size_payload
# ============================================================================


class TestParseAtbSizePayload:
    """Tests for the payload → size_map parsing logic."""

    # ------------------------------------------------------------------
    # 1. Basic valid row WITH tech_detail
    # ------------------------------------------------------------------
    def test_basic_row_with_tech_detail(self):
        payload = {"size": [{"technology": "Solar", "tech_detail": "Class1", "size": 50}]}
        result = parse_atb_size_payload(payload)
        assert result == {("Solar", "Class1"): 50.0}

    # ------------------------------------------------------------------
    # 2. Row with empty / None tech_detail falls back to (tech, None) key
    # ------------------------------------------------------------------
    @pytest.mark.parametrize("td", [None, ""])
    def test_row_with_empty_tech_detail_uses_none_key(self, td):
        payload = {"size": [{"technology": "Wind", "tech_detail": td, "size": 200}]}
        result = parse_atb_size_payload(payload)
        assert ("Wind", None) in result
        assert result[("Wind", None)] == 200.0

    # ------------------------------------------------------------------
    # 3. Row missing "technology" field is skipped
    # ------------------------------------------------------------------
    def test_row_missing_technology_is_skipped(self):
        payload = {"size": [{"tech_detail": "Class1", "size": 100}]}
        result = parse_atb_size_payload(payload)
        assert result == {}

    # ------------------------------------------------------------------
    # 4. Row with empty-string technology is skipped
    # ------------------------------------------------------------------
    def test_row_with_empty_technology_is_skipped(self):
        payload = {"size": [{"technology": "   ", "tech_detail": "Class1", "size": 100}]}
        result = parse_atb_size_payload(payload)
        assert result == {}

    # ------------------------------------------------------------------
    # 5. Row with None size is skipped
    # ------------------------------------------------------------------
    def test_row_with_none_size_is_skipped(self):
        payload = {"size": [{"technology": "Solar", "tech_detail": "Class1", "size": None}]}
        result = parse_atb_size_payload(payload)
        assert result == {}

    # ------------------------------------------------------------------
    # 6. Non-dict row in sizes list is skipped
    # ------------------------------------------------------------------
    def test_non_dict_row_is_skipped(self):
        payload = {"size": ["not-a-dict", 42, None]}
        result = parse_atb_size_payload(payload)
        assert result == {}

    # ------------------------------------------------------------------
    # 7. Payload is not a dict → empty map
    # ------------------------------------------------------------------
    @pytest.mark.parametrize("bad_payload", ["string", 42, [1, 2, 3]])
    def test_non_dict_payload_returns_empty_map(self, bad_payload):
        result = parse_atb_size_payload(bad_payload)
        assert result == {}

    # ------------------------------------------------------------------
    # 8. Payload is None → empty map
    # ------------------------------------------------------------------
    def test_none_payload_returns_empty_map(self):
        result = parse_atb_size_payload(None)
        assert result == {}

    # ------------------------------------------------------------------
    # 9. Payload has no "size" key → empty map
    # ------------------------------------------------------------------
    def test_payload_without_size_key_returns_empty_map(self):
        result = parse_atb_size_payload({"other_key": []})
        assert result == {}

    # ------------------------------------------------------------------
    # 10. Float and int sizes are both stored as float
    # ------------------------------------------------------------------
    def test_int_size_stored_as_float(self):
        payload = {"size": [{"technology": "Wind", "size": 100}]}
        result = parse_atb_size_payload(payload)
        assert isinstance(result[("Wind", None)], float)

    def test_float_size_stored_as_float(self):
        payload = {"size": [{"technology": "Wind", "size": 99.9}]}
        result = parse_atb_size_payload(payload)
        assert isinstance(result[("Wind", None)], float)
        assert result[("Wind", None)] == pytest.approx(99.9)

    # ------------------------------------------------------------------
    # 11. Whitespace in technology name is stripped
    # ------------------------------------------------------------------
    def test_whitespace_in_technology_is_stripped(self):
        payload = {"size": [{"technology": "  Solar  ", "size": 50}]}
        result = parse_atb_size_payload(payload)
        assert ("Solar", None) in result

    # ------------------------------------------------------------------
    # 12. Whitespace in tech_detail is stripped
    # ------------------------------------------------------------------
    def test_whitespace_in_tech_detail_is_stripped(self):
        payload = {"size": [{"technology": "Solar", "tech_detail": "  Class1  ", "size": 50}]}
        result = parse_atb_size_payload(payload)
        assert ("Solar", "Class1") in result

    # ------------------------------------------------------------------
    # 13. Multiple rows build a map with multiple keys
    # ------------------------------------------------------------------
    def test_multiple_rows_build_full_map(self):
        payload = {
            "size": [
                {"technology": "Solar", "tech_detail": "Class1", "size": 50},
                {"technology": "Wind", "tech_detail": "Class2", "size": 200},
                {"technology": "Battery", "size": 10},
            ]
        }
        result = parse_atb_size_payload(payload)
        assert len(result) == 3
        assert result[("Solar", "Class1")] == 50.0
        assert result[("Wind", "Class2")] == 200.0
        assert result[("Battery", None)] == 10.0

    # ------------------------------------------------------------------
    # 14. Row with tech_detail=None is treated as the fallback (tech, None) key
    # ------------------------------------------------------------------
    def test_tech_detail_none_treated_as_fallback_key(self):
        payload = {"size": [{"technology": "Nuclear", "tech_detail": None, "size": 1000}]}
        result = parse_atb_size_payload(payload)
        assert result == {("Nuclear", None): 1000.0}

    # ------------------------------------------------------------------
    # 15. Row with tech_detail=0 (falsy int) is treated as fallback  (edge case)
    # ------------------------------------------------------------------
    def test_falsy_int_tech_detail_treated_as_fallback_key(self):
        payload = {"size": [{"technology": "Hydro", "tech_detail": 0, "size": 30}]}
        result = parse_atb_size_payload(payload)
        # 0 is falsy, so the `if tech_detail:` branch is skipped → (tech, None) key
        assert result == {("Hydro", None): 30.0}


# ============================================================================
# Tests – lookup_size_from_map
# ============================================================================


class TestLookupSizeFromMap:
    """Tests for the size-map lookup logic."""

    def _make_map(self):
        return {
            ("Solar", "Class1"): 50.0,
            ("Solar", None): 75.0,
            ("Wind", None): 200.0,
        }

    # ------------------------------------------------------------------
    # 1. Exact match with tech AND detail
    # ------------------------------------------------------------------
    def test_exact_match_with_tech_and_detail(self):
        size_map = self._make_map()
        assert lookup_size_from_map(size_map, "Solar", "Class1") == 50.0

    # ------------------------------------------------------------------
    # 2. No exact detail match → falls back to (tech, None)
    # ------------------------------------------------------------------
    def test_no_detail_match_falls_back_to_none_key(self):
        size_map = self._make_map()
        assert lookup_size_from_map(size_map, "Solar", "ClassXXX") == 75.0

    # ------------------------------------------------------------------
    # 3. No match at all → returns None
    # ------------------------------------------------------------------
    def test_no_match_returns_none(self):
        size_map = self._make_map()
        assert lookup_size_from_map(size_map, "Nuclear", "Class1") is None

    # ------------------------------------------------------------------
    # 4. Empty / falsy tech → returns None
    # ------------------------------------------------------------------
    @pytest.mark.parametrize("tech", ["", None, 0])
    def test_empty_tech_returns_none(self, tech):
        size_map = self._make_map()
        assert lookup_size_from_map(size_map, tech, "Class1") is None

    # ------------------------------------------------------------------
    # 5. Empty detail → only tries (tech, None) key
    # ------------------------------------------------------------------
    @pytest.mark.parametrize("detail", ["", None])
    def test_empty_detail_uses_none_key_only(self, detail):
        size_map = self._make_map()
        # "Wind" has a (Wind, None) entry but no detail entry
        assert lookup_size_from_map(size_map, "Wind", detail) == 200.0

    # ------------------------------------------------------------------
    # 6. Detail match takes priority over fallback
    # ------------------------------------------------------------------
    def test_detail_match_takes_priority_over_fallback(self):
        size_map = self._make_map()
        # "Solar" has both (Solar, Class1)=50 and (Solar, None)=75
        # Detail match must win
        result = lookup_size_from_map(size_map, "Solar", "Class1")
        assert result == 50.0
        assert result != 75.0


# ============================================================================
# Tests – format_size_value
# ============================================================================


class TestFormatSizeValue:
    """Tests for the size-value formatting logic."""

    # ------------------------------------------------------------------
    # 1. None → default "100"
    # ------------------------------------------------------------------
    def test_none_returns_default_100(self):
        assert format_size_value(None) == "100"

    # ------------------------------------------------------------------
    # 2. Whole MW value (>= 1) → rounded integer string
    # ------------------------------------------------------------------
    def test_100_mw_returns_integer_string(self):
        assert format_size_value(100.0) == "100"

    # ------------------------------------------------------------------
    # 3. 99.5 MW rounds to "100"
    # ------------------------------------------------------------------
    def test_99_5_rounds_to_100(self):
        assert format_size_value(99.5) == "100"

    # ------------------------------------------------------------------
    # 4. Sub-1 MW (0.5) preserves decimal
    # ------------------------------------------------------------------
    def test_sub_1_mw_preserves_decimal(self):
        assert format_size_value(0.5) == "0.5"

    # ------------------------------------------------------------------
    # 5. Very small sub-1 MW value preserves decimal
    # ------------------------------------------------------------------
    def test_very_small_sub_1_mw_preserves_decimal(self):
        assert format_size_value(0.001) == "0.001"

    # ------------------------------------------------------------------
    # 6. Exactly 1.0 MW → integer string "1"
    # ------------------------------------------------------------------
    def test_exactly_1_mw_returns_integer_string(self):
        assert format_size_value(1.0) == "1"
