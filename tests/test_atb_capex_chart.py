"""
Tests for the _build_atb_capex_chart_data and _render_atb_capex_chart_svg logic.

Rather than importing cluster_app.py (which requires mocking heavy PyScript/browser
dependencies), these tests replicate the *pure* chart-building and SVG-rendering
logic from those two functions directly.  This keeps the suite fast and
dependency-free while still validating the real behaviour described in the function
bodies.

_build_atb_capex_chart_data (cluster_app.py)
--------------------------------------------
    result: dict = {}
    for row in options:          # options replaces getattr(state, "atb_options", [])
        if not isinstance(row, dict):
            continue
        if int(row.get("data_year", 0)) != data_year:
            continue
        if str(row.get("technology", "")) != technology:
            continue
        if str(row.get("tech_detail", "")) != tech_detail:
            continue
        case  = str(row.get("cost_case", ""))
        yr    = row.get("year")
        capex = row.get("capex_mw")
        if not case or yr is None or capex is None:
            continue
        try:
            yr_int      = int(yr)
            capex_float = float(capex)
        except (TypeError, ValueError):
            continue
        result.setdefault(case, []).append((yr_int, capex_float))
    for case in result:
        result[case].sort(key=lambda x: x[0])
    return result

_render_atb_capex_chart_svg (cluster_app.py)
--------------------------------------------
    Builds a small inline SVG (320×65 px) with polylines/circles per cost-case.
    Selected case is rendered last (on top) in blue; all others in grey.
"""

from __future__ import annotations

import html as _html
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Pure-logic helpers (replicated from cluster_app.py to avoid DOM imports)
# ---------------------------------------------------------------------------


def _build_atb_capex_chart_data(
    options: list[Any],
    data_year: int,
    technology: str,
    tech_detail: str,
) -> dict:
    """Replicate the filtering/grouping loop from the ATB CAPEX chart builder."""
    result: dict = {}
    for row in options:
        if not isinstance(row, dict):
            continue
        if int(row.get("data_year", 0)) != data_year:
            continue
        if str(row.get("technology", "")) != technology:
            continue
        if str(row.get("tech_detail", "")) != tech_detail:
            continue
        case = str(row.get("cost_case", ""))
        yr = row.get("year")
        capex = row.get("capex_mw")
        if not case or yr is None or capex is None:
            continue
        try:
            yr_int = int(yr)
            capex_float = float(capex)
        except (TypeError, ValueError):
            continue
        result.setdefault(case, []).append((yr_int, capex_float))

    for case in result:
        result[case].sort(key=lambda x: x[0])
    return result


def _render_atb_capex_chart_svg(chart_data: dict, selected_case) -> str:
    """Replicate the SVG rendering logic from the ATB CAPEX chart function."""
    if not chart_data:
        return ""

    width = 320
    height = 65
    ml = 44
    mr = 6
    mt = 6
    mb = 18
    pw = width - ml - mr
    ph = height - mt - mb

    all_years: list[int] = []
    all_capex: list[float] = []
    for pts in chart_data.values():
        for yr, capex in pts:
            all_years.append(yr)
            all_capex.append(capex)

    if not all_years:
        return ""

    x_min = min(all_years)
    x_max = max(all_years)
    y_min = min(all_capex)
    y_max = max(all_capex)

    y_range = y_max - y_min
    if y_range < 1e-6:
        y_min = max(0.0, y_min - 1000.0)
        y_max = y_max + 1000.0
        y_range = y_max - y_min
    else:
        pad = y_range * 0.08
        y_min = max(0.0, y_min - pad)
        y_max = y_max + pad
        y_range = y_max - y_min

    x_range = max(1, x_max - x_min)

    def to_x(yr):
        return ml + (yr - x_min) / x_range * pw

    def to_y(capex):
        return mt + ph - (capex - y_min) / y_range * ph

    def _fmt_capex(val):
        if val >= 1_000_000:
            return f"{val / 1_000_000:.1f}M"
        if val >= 1_000:
            return f"{val / 1_000:.0f}K"
        return f"{val:.0f}"

    svg = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}"'
        f' role="img" aria-label="CAPEX by cost case" style="display:block;">',
        f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="#ccc" stroke-width="1"/>',
        f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" stroke="#ccc" stroke-width="1"/>',
    ]

    svg.append(
        f'<text x="{ml - 3}" y="{mt + ph}" text-anchor="end" font-size="9" fill="#666">'
        f"{_fmt_capex(y_min)}</text>"
    )
    svg.append(
        f'<text x="{ml - 3}" y="{mt + 6}" text-anchor="end" font-size="9" fill="#666">'
        f"{_fmt_capex(y_max)}</text>"
    )
    svg.append(
        f'<text x="{ml}" y="{mt + ph + 11}" text-anchor="middle" font-size="9" fill="#666">'
        f"{x_min}</text>"
    )
    if x_max != x_min:
        svg.append(
            f'<text x="{ml + pw}" y="{mt + ph + 11}" text-anchor="end" font-size="9" fill="#666">'
            f"{x_max}</text>"
        )

    SELECTED_COLOR = "#1a56c4"
    GRAY = "#c8cdd8"
    SELECTED_WIDTH = "2"
    GRAY_WIDTH = "1.25"

    cases_sorted = sorted(
        chart_data.keys(),
        key=lambda c: (0 if c == selected_case else 1),
        reverse=True,
    )
    for case in cases_sorted:
        pts = chart_data[case]
        if len(pts) < 1:
            continue
        is_sel = case == selected_case
        color = SELECTED_COLOR if is_sel else GRAY
        stroke_w = SELECTED_WIDTH if is_sel else GRAY_WIDTH
        opacity = "1" if is_sel else "0.85"
        if len(pts) == 1:
            cx = to_x(pts[0][0])
            cy = to_y(pts[0][1])
            svg.append(
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="2.5" fill="{color}" opacity="{opacity}"/>'
            )
        else:
            coords = " ".join(f"{to_x(yr):.2f},{to_y(capex):.2f}" for yr, capex in pts)
            title = _html.escape(case)
            svg.append(
                f'<polyline points="{coords}" fill="none" stroke="{color}"'
                f' stroke-width="{stroke_w}" stroke-linejoin="round" stroke-linecap="round"'
                f' opacity="{opacity}"><title>{title}</title></polyline>'
            )

    svg.append("</svg>")
    return "".join(svg)


# ===========================================================================
# Fixtures shared across test classes
# ===========================================================================


@pytest.fixture()
def base_row() -> dict:
    """A fully valid single row that matches year=2024, tech='Wind', detail='Class4'."""
    return {
        "data_year": 2024,
        "technology": "Wind",
        "tech_detail": "Class4",
        "cost_case": "Mid",
        "year": 2025,
        "capex_mw": 1200.0,
    }


@pytest.fixture()
def multi_year_options() -> list[dict]:
    """Three rows covering two cost-cases and two projection years."""
    return [
        {
            "data_year": 2024,
            "technology": "Wind",
            "tech_detail": "Class4",
            "cost_case": "Mid",
            "year": 2025,
            "capex_mw": 1200.0,
        },
        {
            "data_year": 2024,
            "technology": "Wind",
            "tech_detail": "Class4",
            "cost_case": "Mid",
            "year": 2030,
            "capex_mw": 1000.0,
        },
        {
            "data_year": 2024,
            "technology": "Wind",
            "tech_detail": "Class4",
            "cost_case": "Low",
            "year": 2025,
            "capex_mw": 950.0,
        },
    ]


# ===========================================================================
# Tests for _build_atb_capex_chart_data
# ===========================================================================


class TestBuildAtbCapexChartData:
    """Verify the row-filtering and grouping logic of _build_atb_capex_chart_data."""

    # ------------------------------------------------------------------
    # Empty / degenerate inputs
    # ------------------------------------------------------------------

    def test_empty_options_returns_empty_dict(self):
        result = _build_atb_capex_chart_data([], 2024, "Wind", "Class4")
        assert result == {}

    def test_non_dict_rows_are_all_skipped(self):
        options = ["string row", 42, None, [], ("tuple",)]
        result = _build_atb_capex_chart_data(options, 2024, "Wind", "Class4")
        assert result == {}

    # ------------------------------------------------------------------
    # Filtering by data_year, technology, tech_detail
    # ------------------------------------------------------------------

    def test_wrong_data_year_excludes_row(self, base_row):
        result = _build_atb_capex_chart_data([base_row], 2025, "Wind", "Class4")
        assert result == {}

    def test_correct_data_year_includes_row(self, base_row):
        result = _build_atb_capex_chart_data([base_row], 2024, "Wind", "Class4")
        assert "Mid" in result

    def test_wrong_technology_excludes_row(self, base_row):
        result = _build_atb_capex_chart_data([base_row], 2024, "Solar", "Class4")
        assert result == {}

    def test_wrong_tech_detail_excludes_row(self, base_row):
        result = _build_atb_capex_chart_data([base_row], 2024, "Wind", "Class5")
        assert result == {}

    def test_empty_tech_detail_filter_matches_only_empty_string_detail(self, base_row):
        # base_row has tech_detail="Class4"; filtering with "" should miss it
        result = _build_atb_capex_chart_data([base_row], 2024, "Wind", "")
        assert result == {}

    def test_empty_tech_detail_in_row_matches_empty_filter(self):
        row = {
            "data_year": 2024,
            "technology": "Wind",
            "tech_detail": "",
            "cost_case": "Mid",
            "year": 2025,
            "capex_mw": 1100.0,
        }
        result = _build_atb_capex_chart_data([row], 2024, "Wind", "")
        assert result == {"Mid": [(2025, 1100.0)]}

    # ------------------------------------------------------------------
    # Rows missing required fields → skipped
    # ------------------------------------------------------------------

    def test_row_with_empty_cost_case_is_skipped(self, base_row):
        base_row["cost_case"] = ""
        result = _build_atb_capex_chart_data([base_row], 2024, "Wind", "Class4")
        assert result == {}

    def test_row_with_missing_cost_case_key_is_skipped(self, base_row):
        del base_row["cost_case"]
        result = _build_atb_capex_chart_data([base_row], 2024, "Wind", "Class4")
        assert result == {}

    def test_row_with_year_none_is_skipped(self, base_row):
        base_row["year"] = None
        result = _build_atb_capex_chart_data([base_row], 2024, "Wind", "Class4")
        assert result == {}

    def test_row_with_capex_mw_none_is_skipped(self, base_row):
        base_row["capex_mw"] = None
        result = _build_atb_capex_chart_data([base_row], 2024, "Wind", "Class4")
        assert result == {}

    @pytest.mark.parametrize(
        "year_val, capex_val",
        [
            ("not-a-year", 1200.0),
            (2025, "not-a-capex"),
            ("abc", "xyz"),
        ],
    )
    def test_non_numeric_year_or_capex_is_skipped(self, base_row, year_val, capex_val):
        base_row["year"] = year_val
        base_row["capex_mw"] = capex_val
        result = _build_atb_capex_chart_data([base_row], 2024, "Wind", "Class4")
        assert result == {}

    # ------------------------------------------------------------------
    # Happy-path structure
    # ------------------------------------------------------------------

    def test_single_valid_row_produces_one_case_with_one_tuple(self, base_row):
        result = _build_atb_capex_chart_data([base_row], 2024, "Wind", "Class4")
        assert result == {"Mid": [(2025, 1200.0)]}

    def test_multiple_cost_cases_produce_separate_lists(self, multi_year_options):
        result = _build_atb_capex_chart_data(
            multi_year_options, 2024, "Wind", "Class4"
        )
        assert set(result.keys()) == {"Mid", "Low"}
        assert result["Low"] == [(2025, 950.0)]

    def test_tuples_within_case_are_sorted_ascending_by_year(self, multi_year_options):
        # Insert a row with earlier year after a later one to confirm sorting
        extra = {
            "data_year": 2024,
            "technology": "Wind",
            "tech_detail": "Class4",
            "cost_case": "Mid",
            "year": 2020,
            "capex_mw": 1400.0,
        }
        result = _build_atb_capex_chart_data(
            [*multi_year_options, extra], 2024, "Wind", "Class4"
        )
        years = [yr for yr, _ in result["Mid"]]
        assert years == sorted(years)

    # ------------------------------------------------------------------
    # Type coercion
    # ------------------------------------------------------------------

    def test_year_as_string_integer_is_coerced(self, base_row):
        base_row["year"] = "2030"
        result = _build_atb_capex_chart_data([base_row], 2024, "Wind", "Class4")
        yr, _ = result["Mid"][0]
        assert yr == 2030
        assert isinstance(yr, int)

    def test_capex_as_string_float_is_coerced(self, base_row):
        base_row["capex_mw"] = "987.65"
        result = _build_atb_capex_chart_data([base_row], 2024, "Wind", "Class4")
        _, capex = result["Mid"][0]
        assert capex == pytest.approx(987.65)
        assert isinstance(capex, float)

    def test_data_year_as_string_integer_in_row_is_coerced(self, base_row):
        # The logic does int(row.get("data_year", 0)) so a string "2024" should match
        base_row["data_year"] = "2024"
        result = _build_atb_capex_chart_data([base_row], 2024, "Wind", "Class4")
        assert "Mid" in result

    # ------------------------------------------------------------------
    # Mixed valid/invalid rows
    # ------------------------------------------------------------------

    def test_only_matching_rows_appear_in_result(self):
        options = [
            # valid
            {
                "data_year": 2024,
                "technology": "Wind",
                "tech_detail": "Class4",
                "cost_case": "Mid",
                "year": 2025,
                "capex_mw": 1200.0,
            },
            # wrong year
            {
                "data_year": 2023,
                "technology": "Wind",
                "tech_detail": "Class4",
                "cost_case": "High",
                "year": 2025,
                "capex_mw": 1500.0,
            },
            # wrong tech
            {
                "data_year": 2024,
                "technology": "Solar",
                "tech_detail": "Class4",
                "cost_case": "Low",
                "year": 2025,
                "capex_mw": 800.0,
            },
            # non-dict
            "skip me",
        ]
        result = _build_atb_capex_chart_data(options, 2024, "Wind", "Class4")
        assert list(result.keys()) == ["Mid"]
        assert result["Mid"] == [(2025, 1200.0)]


# ===========================================================================
# Tests for _render_atb_capex_chart_svg
# ===========================================================================


class TestRenderAtbCapexChartSvg:
    """Verify the SVG rendering logic of _render_atb_capex_chart_svg."""

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------

    @pytest.fixture()
    def two_case_data(self) -> dict:
        """Two cost-cases spanning 2025-2035, suitable for polyline rendering."""
        return {
            "Mid": [(2025, 1200.0), (2030, 1000.0), (2035, 850.0)],
            "Low": [(2025, 950.0), (2030, 800.0), (2035, 650.0)],
        }

    @pytest.fixture()
    def single_point_data(self) -> dict:
        """Each case has exactly one point → circle rendering."""
        return {
            "Mid": [(2025, 1200.0)],
            "Low": [(2025, 900.0)],
        }

    # ------------------------------------------------------------------
    # Empty / degenerate inputs
    # ------------------------------------------------------------------

    def test_empty_dict_returns_empty_string(self):
        assert _render_atb_capex_chart_svg({}, "Mid") == ""

    def test_dict_with_only_empty_point_lists_returns_empty_string(self):
        # chart_data is non-empty but has no actual data points
        result = _render_atb_capex_chart_svg({"Mid": [], "Low": []}, "Mid")
        assert result == ""

    # ------------------------------------------------------------------
    # SVG structure
    # ------------------------------------------------------------------

    def test_output_starts_with_svg_tag(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        assert result.startswith("<svg")

    def test_output_ends_with_closing_svg_tag(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        assert result.endswith("</svg>")

    def test_viewbox_is_320_by_65(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        assert 'viewBox="0 0 320 65"' in result

    def test_svg_contains_axis_lines(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        assert result.count("<line") >= 2

    # ------------------------------------------------------------------
    # Colour, stroke-width, and opacity
    # ------------------------------------------------------------------

    def test_selected_case_polyline_uses_blue_stroke(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        assert 'stroke="#1a56c4"' in result

    def test_non_selected_case_polyline_uses_gray_stroke(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        assert 'stroke="#c8cdd8"' in result

    def test_selected_case_stroke_width_is_2(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        assert 'stroke-width="2"' in result

    def test_non_selected_case_stroke_width_is_1_25(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        assert 'stroke-width="1.25"' in result

    def test_selected_case_opacity_is_1(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        assert 'opacity="1"' in result

    def test_non_selected_case_opacity_is_0_85(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        assert 'opacity="0.85"' in result

    # ------------------------------------------------------------------
    # Circle vs. polyline rendering
    # ------------------------------------------------------------------

    def test_multi_point_case_renders_as_polyline(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        assert "<polyline" in result

    def test_single_point_case_renders_as_circle(self, single_point_data):
        result = _render_atb_capex_chart_svg(single_point_data, "Mid")
        assert "<circle" in result

    def test_single_point_case_does_not_produce_polyline(self, single_point_data):
        result = _render_atb_capex_chart_svg(single_point_data, "Mid")
        assert "<polyline" not in result

    def test_single_point_selected_case_circle_uses_blue_fill(self, single_point_data):
        result = _render_atb_capex_chart_svg(single_point_data, "Mid")
        assert 'fill="#1a56c4"' in result

    def test_single_point_non_selected_case_circle_uses_gray_fill(self, single_point_data):
        result = _render_atb_capex_chart_svg(single_point_data, "Mid")
        assert 'fill="#c8cdd8"' in result

    # ------------------------------------------------------------------
    # Title element and HTML escaping
    # ------------------------------------------------------------------

    def test_polyline_contains_title_with_case_name(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        assert "<title>Mid</title>" in result
        assert "<title>Low</title>" in result

    @pytest.mark.parametrize(
        "case_name, expected_title",
        [
            ("Case <1>", "Case &lt;1&gt;"),
            ("A & B", "A &amp; B"),
            ('Say "hi"', "Say &quot;hi&quot;"),
            ("a<b>c", "a&lt;b&gt;c"),
        ],
    )
    def test_case_names_with_special_chars_are_html_escaped_in_title(
        self, case_name, expected_title
    ):
        chart_data = {case_name: [(2020, 500.0), (2025, 400.0)]}
        result = _render_atb_capex_chart_svg(chart_data, case_name)
        assert f"<title>{expected_title}</title>" in result

    # ------------------------------------------------------------------
    # Y-axis label formatting
    # ------------------------------------------------------------------

    def test_y_axis_label_gte_1000_formatted_with_K(self):
        # capex values in the low thousands → labels get "K" suffix
        chart_data = {"Mid": [(2020, 2000.0), (2025, 4000.0)]}
        result = _render_atb_capex_chart_svg(chart_data, "Mid")
        assert "K" in result

    def test_y_axis_label_gte_1_000_000_formatted_with_M(self):
        chart_data = {"Mid": [(2020, 1_000_000.0), (2025, 2_000_000.0)]}
        result = _render_atb_capex_chart_svg(chart_data, "Mid")
        assert "M" in result

    def test_y_axis_label_lt_1000_formatted_as_plain_integer(self):
        # capex values well below 1000 → y-axis label text nodes contain only digits
        import re

        chart_data = {"Mid": [(2020, 100.0), (2025, 300.0)]}
        result = _render_atb_capex_chart_svg(chart_data, "Mid")
        # Y-axis labels are rendered at x="{ml-3}" = x="41" (44 - 3).
        # Extract their text content and confirm no "K" or "M" suffix is present.
        y_labels = re.findall(r'<text x="41"[^>]*>([^<]+)<', result)
        assert len(y_labels) == 2, f"Expected 2 y-axis labels, got: {y_labels}"
        for label in y_labels:
            assert "K" not in label, f"Unexpected K in y-axis label: {label!r}"
            assert "M" not in label, f"Unexpected M in y-axis label: {label!r}"

    # ------------------------------------------------------------------
    # X-axis year labels
    # ------------------------------------------------------------------

    def test_x_axis_shows_x_min_year(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        assert ">2025<" in result  # x_min = 2025

    def test_x_axis_shows_x_max_year_when_different_from_x_min(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        assert ">2035<" in result  # x_max = 2035

    def test_x_axis_shows_only_one_year_label_when_single_year(self):
        chart_data = {"Mid": [(2025, 1000.0), (2025, 1100.0)]}  # both same year
        result = _render_atb_capex_chart_svg(chart_data, "Mid")
        # x_min == x_max == 2025; label should appear exactly once
        assert result.count(">2025<") == 1

    # ------------------------------------------------------------------
    # Flat CAPEX range (y_range < 1e-6 branch)
    # ------------------------------------------------------------------

    def test_flat_capex_range_spreads_y_axis_by_1000(self):
        # All points have the same capex → y_range < 1e-6 → ±1000 padding applied
        capex_val = 500.0
        chart_data = {"Mid": [(2020, capex_val), (2025, capex_val)]}
        result = _render_atb_capex_chart_svg(chart_data, "Mid")
        # y_max becomes 500 + 1000 = 1500 → formatted as "2K" (1500/1000=1.5 → 2K)
        # y_min becomes max(0, 500 - 1000) = 0 → formatted as "0"
        assert "2K" in result
        assert ">0<" in result

    def test_flat_capex_zero_clamps_y_min_to_zero(self):
        # capex = 0 → y_min would be -1000, clamped to 0
        chart_data = {"Mid": [(2020, 0.0), (2025, 0.0)]}
        result = _render_atb_capex_chart_svg(chart_data, "Mid")
        # y_min = max(0.0, 0.0 - 1000.0) = 0.0 → label is "0"
        assert ">0<" in result

    # ------------------------------------------------------------------
    # Rendering order – selected case drawn on top
    # ------------------------------------------------------------------

    def test_selected_case_appears_after_non_selected_in_svg(self, two_case_data):
        # "Low" is non-selected, "Mid" is selected; Mid must appear later in the string
        result = _render_atb_capex_chart_svg(two_case_data, "Mid")
        pos_low = result.index("<title>Low</title>")
        pos_mid = result.index("<title>Mid</title>")
        assert pos_mid > pos_low, "Selected case must be rendered last (on top)"

    # ------------------------------------------------------------------
    # selected_case=None → everything renders as gray
    # ------------------------------------------------------------------

    def test_selected_case_none_renders_all_cases_as_gray(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, None)
        assert 'stroke="#1a56c4"' not in result
        assert 'stroke="#c8cdd8"' in result

    def test_selected_case_none_all_opacities_are_0_85(self, two_case_data):
        result = _render_atb_capex_chart_svg(two_case_data, None)
        assert 'opacity="1"' not in result
        assert result.count('opacity="0.85"') == len(two_case_data)
