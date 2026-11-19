"""Contract tests for the compact ATB index, using real-schema parquet fixtures.

Exercise projection/dollar years, financial-dimension invariance, normalized
joins, nullable metrics, filtering, aliases, deterministic output, and canonical
heat-rate overlays. CLI downloads are replaced with bounded local fixtures;
real network access and large-file performance are deliberately not covered.
"""

from __future__ import annotations

import importlib.util
import sys
from itertools import product
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
import pytest

_spec = importlib.util.spec_from_file_location(
    "build_atb_options",
    Path(__file__).parent.parent / "web" / "build_atb_options.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
build_index = _mod.build_index

OPTION_KEYS = ["data_year", "technology", "tech_detail", "cost_case", "basis_year"]
EXPECTED_OUT_COLS = [*OPTION_KEYS, "dollar_year", "capex_mw", "heat_rate"]
SOURCE_COLS = [
    *OPTION_KEYS,
    "dollar_year",
    "cap_recovery_years",
    "financial_case",
    "units",
    "parameter",
    "parameter_value",
]


def _row(parameter="capex_mw", value=1_200_000.0, **overrides):
    """One ATB observation; publication, projection, and dollar years differ."""
    row = {
        "data_year": 2024,
        "technology": "NaturalGas",
        "tech_detail": "CT",
        "cost_case": "Moderate",
        "basis_year": 2030,
        "dollar_year": 2022,
        "cap_recovery_years": 30,
        "financial_case": "Market",
        "units": "$/MW" if parameter == "capex_mw" else "MMBtu/MWh",
        "parameter": parameter,
        "parameter_value": value,
    }
    row.update(overrides)
    return row


def _write_parquet(tmp_path, rows, columns=None):
    path = tmp_path / "atb.parquet"
    pd.DataFrame(rows, columns=columns).to_parquet(path, index=False, engine="pyarrow")
    return path


@pytest.fixture
def mixed_parquet(tmp_path):
    return _write_parquet(
        tmp_path,
        [
            _row(technology="LandbasedWind", tech_detail="Class4"),
            _row(value=1_500_000.0),
            _row("heat_rate", 7.123456789),
        ],
    )


def test_exact_output_schema_and_year_semantics(mixed_parquet):
    result = build_index(mixed_parquet)

    assert isinstance(result, pd.DataFrame)
    assert list(result.columns) == EXPECTED_OUT_COLS
    assert len(result) == 2
    assert result["data_year"].tolist() == [2024, 2024]
    assert result["basis_year"].tolist() == [2030, 2030]
    assert result["dollar_year"].tolist() == [2022, 2022]
    for column in ["data_year", "basis_year", "capex_mw"]:
        assert pd.api.types.is_integer_dtype(result[column]), column
    assert pd.api.types.is_float_dtype(result["heat_rate"])
    by_tech = result.set_index("technology")
    assert by_tech.loc["NaturalGas", "capex_mw"] == 1_500_000
    assert by_tech.loc["NaturalGas", "heat_rate"] == pytest.approx(7.123456789)
    assert pd.isna(by_tech.loc["LandbasedWind", "heat_rate"])


@pytest.mark.parametrize(
    "value, expected",
    [(1.0, 1), (1_200_000.4, 1_200_000), (1_200_000.6, 1_200_001)],
)
def test_positive_capex_is_rounded_to_integer(tmp_path, value, expected):
    result = build_index(_write_parquet(tmp_path, [_row(value=value)]))

    assert len(result) == 1
    assert result.loc[0, "capex_mw"] == expected
    assert pd.api.types.is_integer_dtype(result["capex_mw"])


@pytest.mark.parametrize("value", [None, float("nan"), 0.0, -1.0, -999.0])
def test_invalid_capex_does_not_create_an_option(tmp_path, value):
    rows = [_row(value=value), _row(technology="Solar", value=800_000.0)]
    result = build_index(_write_parquet(tmp_path, rows))

    assert result["technology"].tolist() == ["Solar"]


@pytest.mark.parametrize("value", [7.123456789, 7.5, 6.000001])
def test_heat_rate_remains_a_positive_precision_float(tmp_path, value):
    result = build_index(_write_parquet(tmp_path, [_row(), _row("heat_rate", value)]))

    assert len(result) == 1
    assert pd.api.types.is_float_dtype(result["heat_rate"])
    assert result.loc[0, "heat_rate"] == pytest.approx(value, rel=1e-12)


@pytest.mark.parametrize("value", [None, float("nan"), 0.0, -1.0])
def test_missing_or_non_positive_heat_is_nan_not_zero(tmp_path, value):
    result = build_index(_write_parquet(tmp_path, [_row(), _row("heat_rate", value)]))

    assert len(result) == 1
    assert pd.isna(result.loc[0, "heat_rate"])
    assert pd.api.types.is_float_dtype(result["heat_rate"])


def test_outer_join_keeps_heat_only_option_and_projection_year(tmp_path):
    rows = [
        _row(),
        _row("heat_rate", 7.25),
        _row("heat_rate", 6.875, basis_year=2035),
        _row("heat_rate", 9.125, technology="Coal", tech_detail="New"),
    ]
    result = build_index(_write_parquet(tmp_path, rows))

    assert len(result) == 3
    assert not result.duplicated(OPTION_KEYS).any()
    indexed = result.set_index(["technology", "basis_year"])
    assert indexed.loc[("NaturalGas", 2030), "capex_mw"] == 1_200_000
    assert indexed.loc[("NaturalGas", 2030), "heat_rate"] == 7.25
    for key, heat in [(("NaturalGas", 2035), 6.875), (("Coal", 2030), 9.125)]:
        assert pd.isna(indexed.loc[key, "capex_mw"])
        assert indexed.loc[key, "heat_rate"] == heat
        assert indexed.loc[key, "dollar_year"] == 2022


def test_unrelated_parameters_do_not_create_options_or_conflicts(tmp_path):
    rows = [
        _row(),
        _row("fixed_om_mw", 15_000),
        _row("fixed_om_mw", 17_000, financial_case="R&D"),
        _row("variable_om_mwh", 3, technology="Other"),
    ]
    result = build_index(_write_parquet(tmp_path, rows))

    assert len(result) == 1
    assert result.loc[0, "technology"] == "NaturalGas"
    assert result.loc[0, "capex_mw"] == 1_200_000


def test_identical_financial_variants_collapse_without_many_to_many_join(tmp_path):
    rows = [
        _row(parameter, value, cap_recovery_years=recovery, financial_case=case)
        for parameter, value in [("capex_mw", 1_200_000.4), ("heat_rate", 7.125)]
        for recovery, case in product([20, 30], ["Market", "R&D"])
    ]
    rows.extend([rows[0].copy(), rows[-1].copy()])
    result = build_index(_write_parquet(tmp_path, rows))

    assert len(result) == 1
    assert not result.duplicated(OPTION_KEYS).any()
    assert result.loc[0, "capex_mw"] == 1_200_000
    assert result.loc[0, "heat_rate"] == 7.125


@pytest.mark.parametrize(
    "parameter, values",
    [
        ("capex_mw", (1_200_000.1, 1_200_000.2)),
        ("heat_rate", (7.123451, 7.123452)),
    ],
)
@pytest.mark.parametrize(
    "variant",
    [{}, {"financial_case": "R&D"}, {"cap_recovery_years": 20}],
    ids=["exact-key", "financial-case", "recovery-period"],
)
def test_conflicting_metric_values_raise_before_rounding_or_averaging(
    tmp_path, parameter, values, variant
):
    rows = [
        _row(parameter, values[0]),
        _row(parameter, values[1], **variant),
    ]
    with pytest.raises(ValueError):
        build_index(_write_parquet(tmp_path, rows))


@pytest.mark.parametrize(
    "key, second",
    [
        ("data_year", 2025),
        ("technology", "Coal"),
        ("tech_detail", "CC"),
        ("cost_case", "Advanced"),
        ("basis_year", 2035),
    ],
)
def test_distinct_option_keys_do_not_conflict_or_cross_join(tmp_path, key, second):
    rows = [
        _row(value=1_200_000),
        _row("heat_rate", 7.25),
        _row(value=900_000, **{key: second}),
        _row("heat_rate", 6.5, **{key: second}),
    ]
    result = build_index(_write_parquet(tmp_path, rows))

    assert len(result) == 2
    assert not result.duplicated(OPTION_KEYS).any()
    assert set(zip(result["capex_mw"], result["heat_rate"])) == {
        (1_200_000, 7.25),
        (900_000, 6.5),
    }
    assert set(result[key]) == {_row()[key], second}


def test_normalization_precedes_deduplication_and_metric_join(tmp_path):
    rows = [
        _row(
            data_year=" 2024 ",
            basis_year=" 2030 ",
            technology=" NaturalGas ",
            tech_detail=" CT ",
            cost_case=" Moderate ",
        ),
        _row(data_year="2024", basis_year="2030"),
        _row(
            "heat_rate",
            7.125,
            data_year="2024 ",
            basis_year="2030 ",
            technology="NaturalGas ",
            tech_detail=" CT",
            cost_case="Moderate ",
        ),
    ]
    result = build_index(_write_parquet(tmp_path, rows))

    assert len(result) == 1
    assert result.loc[0, OPTION_KEYS].tolist() == [
        2024,
        "NaturalGas",
        "CT",
        "Moderate",
        2030,
    ]
    assert result.loc[0, "heat_rate"] == 7.125


def test_conflicts_are_detected_after_key_normalization(tmp_path):
    rows = [_row(), _row(value=900_000, technology=" NaturalGas ")]
    with pytest.raises(ValueError):
        build_index(_write_parquet(tmp_path, rows))


@pytest.mark.parametrize("key", ["technology", "tech_detail", "cost_case"])
@pytest.mark.parametrize("invalid", ["", " \t ", None])
def test_blank_or_missing_text_keys_are_excluded(tmp_path, key, invalid):
    rows = [
        _row(**{key: invalid}),
        _row("heat_rate", 7.25, **{key: invalid}),
        _row(technology="Solar"),
    ]
    result = build_index(_write_parquet(tmp_path, rows))

    assert result["technology"].tolist() == ["Solar"]


def test_missing_publication_year_is_excluded(tmp_path):
    result = build_index(
        _write_parquet(tmp_path, [_row(data_year=None), _row(technology="Solar")])
    )
    assert result["technology"].tolist() == ["Solar"]


@pytest.mark.parametrize("missing", ["column", "all-values", "one-value"])
def test_missing_basis_year_raises_clear_error_without_publication_fallback(
    tmp_path, missing
):
    rows = [_row()]
    if missing == "column":
        del rows[0]["basis_year"]
    elif missing == "all-values":
        rows[0]["basis_year"] = None
    else:
        rows.append(_row(basis_year=None, technology="Solar"))

    with pytest.raises((ValueError, KeyError), match=r"(?i)basis.?year"):
        build_index(_write_parquet(tmp_path, rows))


@pytest.mark.parametrize("missing", ["column", "null"])
def test_optional_dollar_year_is_nullable_and_never_invented(tmp_path, missing):
    rows = [_row(), _row("heat_rate", 7.25)]
    for row in rows:
        if missing == "column":
            del row["dollar_year"]
        else:
            row["dollar_year"] = None
    result = build_index(_write_parquet(tmp_path, rows))

    assert list(result.columns) == EXPECTED_OUT_COLS
    assert len(result) == 1
    assert result["dollar_year"].isna().all()
    assert result.loc[0, "basis_year"] == 2030


def test_dollar_year_missing_for_one_option_does_not_borrow_from_another(tmp_path):
    rows = [_row(), _row(technology="Solar", dollar_year=None)]
    result = build_index(_write_parquet(tmp_path, rows)).set_index("technology")

    assert result.loc["NaturalGas", "dollar_year"] == 2022
    assert pd.isna(result.loc["Solar", "dollar_year"])


@pytest.mark.parametrize(
    "aliases",
    [
        {"data_year": "atb_data_year"},
        {"data_year": "atb_year"},
        {"data_year": "year"},
        {"technology": "atb_technology"},
        {"technology": "tech"},
        {"tech_detail": "technology_detail"},
        {"tech_detail": "detail"},
        {"cost_case": "case"},
        {
            "data_year": "atb_data_year",
            "technology": "atb_technology",
            "tech_detail": "atb_tech_detail",
            "cost_case": "atb_cost_case",
        },
        {column: column.upper() for column in SOURCE_COLS},
    ],
)
def test_column_aliases_preserve_canonical_schema_and_metric_join(tmp_path, aliases):
    rows = [
        {aliases.get(key, key): value for key, value in row.items()}
        for row in [_row(), _row("heat_rate", 7.25)]
    ]
    result = build_index(_write_parquet(tmp_path, rows))

    assert list(result.columns) == EXPECTED_OUT_COLS
    assert len(result) == 1
    assert result.loc[0, OPTION_KEYS].tolist() == [
        2024,
        "NaturalGas",
        "CT",
        "Moderate",
        2030,
    ]
    assert result.loc[0, "heat_rate"] == 7.25


def test_empty_real_schema_parquet_returns_exact_empty_schema(tmp_path):
    result = build_index(_write_parquet(tmp_path, [], columns=SOURCE_COLS))

    assert isinstance(result, pd.DataFrame)
    assert result.empty
    assert list(result.columns) == EXPECTED_OUT_COLS


def test_output_order_is_deterministic_across_input_order(tmp_path):
    rows = [
        _row(data_year=2025),
        _row(technology="Solar"),
        _row(cost_case="Advanced"),
        _row(basis_year=2035),
        _row(),
        _row("heat_rate", 7.25),
    ]
    forward = build_index(_write_parquet(tmp_path, rows))
    reverse = build_index(_write_parquet(tmp_path, rows[::-1]))

    assert len(forward) == 5
    pd.testing.assert_frame_equal(forward, reverse)
    pd.testing.assert_frame_equal(
        forward,
        forward.sort_values(OPTION_KEYS).reset_index(drop=True),
    )


def _heat_row(value=6.122, **overrides):
    row = {key: _row()[key] for key in OPTION_KEYS}
    row["heat_rate"] = value
    row.update(overrides)
    return row


def _write_heat_rates(tmp_path, rows, name="canonical-heat.csv"):
    path = tmp_path / name
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


@pytest.fixture
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("This contract test must not use the network")

    monkeypatch.setattr(_mod, "download_to_temp", forbidden)
    monkeypatch.setattr(_mod.urllib.request, "urlopen", forbidden)


def test_single_argument_and_explicit_none_keep_offline_parquet_semantics(
    tmp_path, no_network
):
    path = _write_parquet(
        tmp_path,
        [
            _row(),
            _row("heat_rate", 7.25),
            _row("heat_rate", 8.125, technology="Coal"),
        ],
    )

    result = build_index(path)
    pd.testing.assert_frame_equal(result, build_index(path, heat_rates_path=None))
    assert len(result) == 2
    indexed = result.set_index("technology")
    assert indexed.loc["NaturalGas", "heat_rate"] == 7.25
    assert indexed.loc["Coal", "heat_rate"] == 8.125
    assert pd.isna(indexed.loc["Coal", "capex_mw"])


def test_canonical_heat_overrides_overlap_without_changing_costs_or_dollar_year(
    tmp_path, no_network
):
    path = _write_parquet(
        tmp_path,
        [
            _row(),
            _row("heat_rate", 7.25),
            _row(data_year=2023, dollar_year=2021, value=1_100_000),
            _row("heat_rate", 8.5, data_year=2023, dollar_year=2021),
            _row(technology="LandbasedWind", tech_detail="Class4"),
            _row(technology="Solar", tech_detail="UtilityPV"),
        ],
    )
    csv = _write_heat_rates(
        tmp_path,
        [
            _heat_row(6.122, dollar_year=1900),
            _heat_row(6.987654321, data_year=2023, dollar_year=2099),
        ],
    )

    baseline = build_index(path)
    result = build_index(path, heat_rates_path=csv)

    assert list(result.columns) == EXPECTED_OUT_COLS
    pd.testing.assert_frame_equal(
        result.drop(columns="heat_rate"), baseline.drop(columns="heat_rate")
    )
    gas = result[result["technology"] == "NaturalGas"].set_index("data_year")
    assert gas.loc[2024, "heat_rate"] == pytest.approx(6.122, rel=1e-12)
    assert gas.loc[2023, "heat_rate"] == pytest.approx(6.987654321, rel=1e-12)
    assert (
        result.loc[result["technology"].isin(["LandbasedWind", "Solar"]), "heat_rate"]
        .isna()
        .all()
    )
    assert pd.api.types.is_float_dtype(result["heat_rate"])


@pytest.mark.parametrize(
    "key, second",
    [
        ("data_year", 2023),
        ("technology", "Coal"),
        ("tech_detail", "CC"),
        ("cost_case", "Advanced"),
        ("basis_year", 2035),
    ],
)
def test_canonical_heat_requires_every_option_key_to_match(
    tmp_path, no_network, key, second
):
    path = _write_parquet(
        tmp_path,
        [
            _row(),
            _row("heat_rate", 7.25),
            _row(value=900_000, **{key: second}),
            _row("heat_rate", 8.125, **{key: second}),
        ],
    )
    csv = _write_heat_rates(tmp_path, [_heat_row()])

    result = build_index(path, heat_rates_path=csv).set_index(key)

    assert len(result) == 2
    assert result.loc[_row()[key], "heat_rate"] == pytest.approx(6.122)
    assert result.loc[second, "heat_rate"] == 8.125


def test_canonical_csv_does_not_create_options_or_publication_years(
    tmp_path, no_network
):
    path = _write_parquet(
        tmp_path,
        [_row(), _row("heat_rate", 8.125, technology="Coal")],
    )
    csv = _write_heat_rates(
        tmp_path,
        [
            _heat_row(),
            _heat_row(7.75, technology="Coal"),
            _heat_row(5.0, technology="CSVOnly"),
            _heat_row(5.1, data_year=2099),
            _heat_row(5.2, basis_year=2090),
        ],
    )

    result = build_index(path, heat_rates_path=csv)

    assert len(result) == 2
    assert result["data_year"].tolist() == [2024, 2024]
    assert result["basis_year"].tolist() == [2030, 2030]
    indexed = result.set_index("technology")
    assert set(indexed.index) == {"NaturalGas", "Coal"}
    assert indexed.loc["Coal", "heat_rate"] == 7.75
    assert pd.isna(indexed.loc["Coal", "capex_mw"])
    assert indexed.loc["Coal", "dollar_year"] == 2022


@pytest.mark.parametrize(
    "value",
    [None, "", "not-a-number", float("nan"), 0, -1, float("inf"), -float("inf")],
)
def test_invalid_canonical_heat_is_authoritative_missing_not_parquet_fallback(
    tmp_path, no_network, value
):
    path = _write_parquet(tmp_path, [_row(), _row("heat_rate", 7.25)])
    csv = _write_heat_rates(tmp_path, [_heat_row(value)])

    result = build_index(path, heat_rates_path=csv)

    assert len(result) == 1
    assert result.loc[0, "capex_mw"] == 1_200_000
    assert pd.isna(result.loc[0, "heat_rate"])
    assert pd.api.types.is_float_dtype(result["heat_rate"])


@pytest.mark.parametrize("value", [6.122, 6.1234567890123, 0.00000123456789])
def test_canonical_heat_preserves_positive_finite_precision(
    tmp_path, no_network, value
):
    path = _write_parquet(tmp_path, [_row()])
    csv = _write_heat_rates(tmp_path, [_heat_row(value)])

    result = build_index(path, heat_rates_path=csv)

    assert result.loc[0, "heat_rate"] == pytest.approx(value, rel=1e-12, abs=0)


def test_canonical_keys_and_identical_duplicates_are_normalized_before_join(
    tmp_path, no_network
):
    path = _write_parquet(
        tmp_path,
        [
            _row(
                data_year=" 2024 ",
                basis_year="2030.0",
                technology=" NaturalGas ",
                tech_detail=" CT ",
                cost_case=" Moderate ",
            )
        ],
    )
    csv = _write_heat_rates(
        tmp_path,
        [
            _heat_row("6.122000", data_year="2024.0", basis_year=" 2030 "),
            _heat_row(
                " 6.122 ",
                data_year=" 2024 ",
                basis_year="2030.0",
                technology=" NaturalGas ",
                tech_detail=" CT ",
                cost_case=" Moderate ",
            ),
        ],
    )

    result = build_index(path, heat_rates_path=csv)

    assert len(result) == 1
    assert result.loc[0, OPTION_KEYS].tolist() == [
        2024,
        "NaturalGas",
        "CT",
        "Moderate",
        2030,
    ]
    assert result.loc[0, "heat_rate"] == pytest.approx(6.122, rel=1e-12)


@pytest.mark.parametrize(
    "values",
    [(6.122, 6.5), (6.123456781, 6.123456782), (6.122, None)],
    ids=["different", "different-before-rounding", "valid-versus-missing"],
)
@pytest.mark.parametrize("normalize_keys", [False, True])
def test_conflicting_canonical_duplicates_raise_without_rounding_or_averaging(
    tmp_path, no_network, values, normalize_keys
):
    path = _write_parquet(tmp_path, [_row()])
    variant = (
        {
            "data_year": "2024.0",
            "basis_year": " 2030 ",
            "technology": " NaturalGas ",
            "tech_detail": " CT ",
            "cost_case": " Moderate ",
        }
        if normalize_keys
        else {}
    )
    csv = _write_heat_rates(
        tmp_path, [_heat_row(values[0]), _heat_row(values[1], **variant)]
    )

    with pytest.raises(ValueError):
        build_index(path, heat_rates_path=csv)


@pytest.mark.parametrize("missing", ["column", "null", "one-value"])
def test_canonical_missing_basis_year_raises_without_publication_fallback(
    tmp_path, no_network, missing
):
    path = _write_parquet(tmp_path, [_row()])
    rows = [_heat_row()]
    if missing == "column":
        del rows[0]["basis_year"]
    elif missing == "null":
        rows[0]["basis_year"] = None
    else:
        rows.append(_heat_row(basis_year=None, technology="Coal"))
    csv = _write_heat_rates(tmp_path, rows)

    with pytest.raises((ValueError, KeyError), match=r"(?i)basis.?year"):
        build_index(path, heat_rates_path=csv)


def test_explicit_heat_csv_path_is_used_instead_of_adjacent_default(
    tmp_path, no_network
):
    path = _write_parquet(tmp_path, [_row(), _row("heat_rate", 7.25)])
    _write_heat_rates(
        tmp_path, [_heat_row(9.5)], name="technology_heat_rates_nrelatb.csv"
    )
    selected = tmp_path / "chosen"
    selected.mkdir()
    csv = _write_heat_rates(selected, [_heat_row(6.122)], name="custom-input.csv")

    result = build_index(path, heat_rates_path=csv)

    assert result.loc[0, "heat_rate"] == pytest.approx(6.122)


def test_missing_explicit_heat_csv_raises_instead_of_silent_fallback(
    tmp_path, no_network
):
    path = _write_parquet(tmp_path, [_row(), _row("heat_rate", 7.25)])

    with pytest.raises(FileNotFoundError):
        build_index(path, heat_rates_path=tmp_path / "does-not-exist.csv")


@pytest.mark.parametrize("cost_source", ["default", "github", "local"])
@pytest.mark.parametrize("heat_source", ["default", "github", "local"])
def test_cli_source_selection_downloads_only_requested_inputs_and_persists_index(
    tmp_path, monkeypatch, no_network, cost_source, heat_source
):
    parquet = _write_parquet(tmp_path, [_row(), _row("heat_rate", 7.25)])
    csv = _write_heat_rates(tmp_path, [_heat_row()])
    out = tmp_path / "output" / "options.parquet"
    argv = ["build_atb_options.py", "--out", str(out)]
    if cost_source == "local":
        argv += ["--parquet", str(parquet)]
    elif cost_source == "github":
        argv += ["--github", "fixture-owner/costs:inputs/costs.parquet@v2"]
    if heat_source == "local":
        argv += ["--heat-rates-csv", str(csv)]
    elif heat_source == "github":
        argv += [
            "--heat-rates-github",
            "fixture-owner/heats:inputs/custom-heat.csv@v3",
        ]

    download_cost = cost_source != "local"
    download_heat = heat_source == "github" or (
        heat_source == "default" and download_cost
    )
    expected_downloads = int(download_cost) + int(download_heat)
    calls = []

    def download(url):
        calls.append(url)
        assert len(calls) <= expected_downloads, "Unexpected extra download"
        parsed = urlsplit(url)
        assert parsed.scheme == "https"
        assert not parsed.query
        if parsed.path.endswith(".csv"):
            assert download_heat, "Local-only invocation downloaded heat rates"
            assert parsed.hostname == "raw.githubusercontent.com"
            if heat_source == "github":
                assert parsed.path == ("/fixture-owner/heats/v3/inputs/custom-heat.csv")
            else:
                # The canonical filename is documented in docs/web_app.md.
                # Do not require a new implementation-specific default constant.
                assert parsed.path.endswith("/data/technology_heat_rates_nrelatb.csv")
            contents, suffix = csv.read_bytes(), ".csv"
        else:
            assert download_cost, "Local parquet invocation downloaded costs"
            assert parsed.hostname == "media.githubusercontent.com"
            if cost_source == "github":
                assert parsed.path == (
                    "/media/fixture-owner/costs/v2/inputs/costs.parquet"
                )
            else:
                assert parsed.path.endswith("/data/technology_costs_atb.parquet")
            contents, suffix = parquet.read_bytes(), ".parquet"
        # Separate files let main() clean up downloads without removing fixtures.
        target = tmp_path / f"download-{len(calls)}{suffix}"
        target.write_bytes(contents)
        return target

    monkeypatch.setattr(_mod, "download_to_temp", download)
    monkeypatch.setattr(sys, "argv", argv)

    assert _mod.main() == 0

    assert len(calls) == expected_downloads
    assert sum(urlsplit(url).path.endswith(".csv") for url in calls) == int(
        download_heat
    )
    result = pd.read_parquet(out)
    assert list(result.columns) == EXPECTED_OUT_COLS
    assert len(result) == 1
    assert result.loc[0, OPTION_KEYS].tolist() == [
        2024,
        "NaturalGas",
        "CT",
        "Moderate",
        2030,
    ]
    assert result.loc[0, "capex_mw"] == 1_200_000
    assert result.loc[0, "dollar_year"] == 2022
    expected_heat = (
        7.25 if cost_source == "local" and heat_source == "default" else 6.122
    )
    assert result.loc[0, "heat_rate"] == pytest.approx(expected_heat, rel=1e-12)
