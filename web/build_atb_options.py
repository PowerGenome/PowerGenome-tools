"""Build the in-browser ATB options index.

Reads ATB costs (technology_costs_atb.parquet) and canonical heat rates
(technology_heat_rates_nrelatb.csv) and writes a
smaller parquet file (web/data/atb_options.parquet) with one row per unique
(data_year, technology, tech_detail, cost_case, basis_year) combination.
Publication year (data_year) and projection year (basis_year) are distinct.

This script extracts:
- data_year
- technology
- tech_detail
- cost_case
- basis_year, dollar_year (monetary basis, when provided)
- capex_mw (integer, $/MW, rounded to nearest whole number)
- heat_rate (float, MMBtu/MWh; null when absent, never replaced by zero)

Usage:
    # Use both default sources hosted on GitHub
    python web/build_atb_options.py

    # Or provide a local parquet
    python web/build_atb_options.py \
        --parquet /path/to/technology_costs_atb.parquet \
        --heat-rates-csv /path/to/technology_heat_rates_nrelatb.csv \
        --out web/data/atb_options.parquet

    # Or download from a specific GitHub repo/path/ref
    python web/build_atb_options.py \
        --github gschivley/PowerGenome-data:data/technology_costs_atb.parquet@main \
        --out web/data/atb_options.parquet

Notes:
- Requires pandas + a parquet engine (pyarrow recommended).
"""

from __future__ import annotations

import argparse
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

DEFAULT_GITHUB_SPEC = (
    "gschivley/PowerGenome-data:data/technology_costs_atb.parquet@main"
)
DEFAULT_HEAT_RATES_GITHUB_SPEC = (
    "gschivley/PowerGenome-data:data/technology_heat_rates_nrelatb.csv@main"
)


def _parse_github_spec(spec: str) -> tuple[str, str, str, str]:
    """Parse OWNER/REPO:PATH@REF -> (owner, repo, path, ref)."""
    spec = (spec or "").strip()
    if not spec:
        raise ValueError("Empty GitHub spec")

    if ":" not in spec:
        raise ValueError(
            "GitHub spec must look like 'OWNER/REPO:PATH@REF' (missing ':')"
        )

    repo_part, path_part = spec.split(":", 1)
    if "/" not in repo_part:
        raise ValueError(
            "GitHub spec must look like 'OWNER/REPO:PATH@REF' (missing 'OWNER/REPO')"
        )
    owner, repo = repo_part.split("/", 1)
    owner = owner.strip()
    repo = repo.strip()

    if "@" in path_part:
        path, ref = path_part.rsplit("@", 1)
    else:
        path, ref = path_part, "main"

    path = path.strip().lstrip("/")
    ref = ref.strip() or "main"

    if not owner or not repo or not path:
        raise ValueError("Invalid GitHub spec; expected OWNER/REPO:PATH@REF")

    return owner, repo, path, ref


def _github_media_url(owner: str, repo: str, ref: str, path: str) -> str:
    # media.githubusercontent.com serves Git LFS objects correctly.
    return f"https://media.githubusercontent.com/media/{owner}/{repo}/{ref}/{path}"


def _read_first_text_line(path: Path) -> str:
    try:
        with path.open("rb") as f:
            chunk = f.read(200)
        try:
            return chunk.decode("utf-8", errors="ignore").splitlines()[0].strip()
        except Exception:
            return ""
    except Exception:
        return ""


def download_to_temp(url: str) -> Path:
    """Download URL to a temporary file and return the path."""
    url = str(url).strip()
    if not url:
        raise ValueError("Empty URL")
    if not url.startswith("https://"):
        raise ValueError(f"Only 'https://' URLs are allowed (got {url!r})")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".parquet")
    tmp_path = Path(tmp.name)

    try:
        with urllib.request.urlopen(url) as resp:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                tmp.write(chunk)
    except urllib.error.HTTPError as exc:
        tmp.close()
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError(f"Download failed ({exc.code}) for {url}") from exc
    except Exception as exc:
        tmp.close()
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError(f"Download failed for {url}: {exc}") from exc
    finally:
        try:
            tmp.close()
        except Exception:
            pass

    # Detect Git LFS pointer downloads (common if using raw.githubusercontent.com)
    first_line = _read_first_text_line(tmp_path)
    if first_line.startswith("version https://git-lfs.github.com/spec/v1"):
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError(
            "Downloaded a Git LFS pointer file (not the parquet bytes). "
            "Use a media.githubusercontent.com URL, or pass --github so the script uses the media URL automatically."
        )

    return tmp_path


def _pick_column(df: pd.DataFrame, candidates: list[str]) -> str:
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        key = cand.lower()
        if key in lower_map:
            return lower_map[key]
    raise KeyError(
        f"None of these columns found: {candidates}. Available: {list(df.columns)}"
    )


def build_index(
    parquet_path: Path, heat_rates_path: Path | None = None
) -> pd.DataFrame:
    """Build from explicit local inputs only; canonical CSV wins on exact keys.

    CSV-only keys do not expand the cost-backed option catalog. Omitting the CSV
    preserves the legacy parquet-only API without triggering any downloads.
    """
    full_df = pd.read_parquet(parquet_path)

    col_year = _pick_column(full_df, ["data_year", "atb_data_year", "atb_year", "year"])
    col_basis = _pick_column(full_df, ["basis_year"])
    col_tech = _pick_column(full_df, ["technology", "tech", "atb_technology"])
    col_detail = _pick_column(
        full_df, ["tech_detail", "technology_detail", "detail", "atb_tech_detail"]
    )
    col_case = _pick_column(full_df, ["cost_case", "case", "atb_cost_case"])
    col_param = _pick_column(full_df, ["parameter"])
    col_value = _pick_column(full_df, ["parameter_value"])

    df = full_df.rename(
        columns={
            col_year: "data_year",
            col_basis: "basis_year",
            col_tech: "technology",
            col_detail: "tech_detail",
            col_case: "cost_case",
            col_param: "parameter",
            col_value: "parameter_value",
            **(
                {_pick_column(full_df, ["dollar_year"]): "dollar_year"}
                if "dollar_year" in {c.lower() for c in full_df.columns}
                else {}
            ),
        }
    ).copy()
    keys = ["data_year", "technology", "tech_detail", "cost_case", "basis_year"]
    if (
        df.loc[df["parameter"].isin(["capex_mw", "heat_rate"]), "basis_year"]
        .isna()
        .any()
    ):
        raise ValueError(
            "Missing basis_year: regenerate from projection-year source data"
        )
    df = df[df["parameter"].isin(["capex_mw", "heat_rate"])].dropna(subset=keys)
    for col in ["technology", "tech_detail", "cost_case"]:
        df[col] = df[col].astype(str).str.strip()
        df = df[df[col] != ""]
    for col in ["data_year", "basis_year"]:
        df[col] = pd.to_numeric(df[col], errors="raise").astype(int)
    if "dollar_year" not in df:
        df["dollar_year"] = pd.NA
    df["dollar_year"] = pd.to_numeric(df["dollar_year"], errors="raise").astype("Int64")
    df["parameter_value"] = pd.to_numeric(df["parameter_value"], errors="coerce")
    df.loc[
        ~df["parameter_value"].gt(0) | ~df["parameter_value"].lt(float("inf")),
        "parameter_value",
    ] = float("nan")

    # Financial case and recovery period do not affect these physical/overnight
    # metrics in the source. Collapse only *identical* observations; fail if a
    # future source changes that invariant, rather than silently averaging it.
    observations = df[keys + ["parameter", "parameter_value"]].drop_duplicates()
    if observations.duplicated(keys + ["parameter"]).any():
        raise ValueError("Conflicting ATB values across source dimensions")
    basis = df[keys + ["dollar_year"]].drop_duplicates()
    if basis.duplicated(keys).any():
        raise ValueError("Conflicting dollar_year values across source dimensions")
    capex = observations[observations.parameter == "capex_mw"].drop(columns="parameter")
    heat = observations[observations.parameter == "heat_rate"].drop(columns="parameter")
    out = capex.rename(columns={"parameter_value": "capex_mw"}).merge(
        heat.rename(columns={"parameter_value": "heat_rate"}),
        on=keys,
        how="outer",
        validate="one_to_one",
    )
    out = out.merge(basis, on=keys, how="left", validate="one_to_one")
    out = out.dropna(subset=["capex_mw", "heat_rate"], how="all")
    if heat_rates_path is not None:
        canonical = pd.read_csv(heat_rates_path)
        # Heat rates are physical metrics: any CSV dollar_year is irrelevant.
        canonical = canonical[keys + ["heat_rate"]].copy()
        if canonical["basis_year"].isna().any():
            raise ValueError(
                "Missing basis_year: regenerate from projection-year source data"
            )
        canonical = canonical.dropna(subset=keys)
        for col in ["technology", "tech_detail", "cost_case"]:
            canonical[col] = canonical[col].astype(str).str.strip()
            canonical = canonical[canonical[col] != ""]
        for col in ["data_year", "basis_year"]:
            canonical[col] = pd.to_numeric(canonical[col], errors="raise").astype(int)
        canonical["heat_rate"] = pd.to_numeric(canonical["heat_rate"], errors="coerce")
        canonical.loc[
            ~canonical["heat_rate"].gt(0) | ~canonical["heat_rate"].lt(float("inf")),
            "heat_rate",
        ] = float("nan")
        canonical = canonical.drop_duplicates()
        if canonical.duplicated(keys).any():
            raise ValueError(
                "Conflicting canonical heat_rate values across source dimensions"
            )
        out = out.merge(
            canonical.rename(columns={"heat_rate": "canonical_heat_rate"}),
            on=keys,
            how="left",
            validate="one_to_one",
            indicator=True,
        )
        # Presence, not non-nullness, determines authority. An explicitly missing
        # canonical rate must not silently fall back to an older parquet value.
        out["heat_rate"] = out["canonical_heat_rate"].where(
            out["_merge"] == "both", out["heat_rate"]
        )
    out["capex_mw"] = out["capex_mw"].round().astype("Int64")
    out["heat_rate"] = out["heat_rate"].astype(float)
    columns = keys + ["dollar_year", "capex_mw", "heat_rate"]
    return out[columns].sort_values(keys).reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build web/data/atb_options.parquet from ATB costs and heat rates"
    )
    source_group = parser.add_mutually_exclusive_group(required=False)
    source_group.add_argument(
        "--parquet",
        type=Path,
        help="Path to technology_costs_atb.parquet (local file)",
    )
    heat_group = parser.add_mutually_exclusive_group()
    heat_group.add_argument(
        "--heat-rates-csv",
        type=Path,
        help="Local canonical heat-rate CSV (no implicit downloads with --parquet)",
    )
    heat_group.add_argument(
        "--heat-rates-github",
        help=(
            "Canonical heat-rate CSV 'OWNER/REPO:PATH@REF' (raw GitHub URL). "
            f"Default for remote cost builds: {DEFAULT_HEAT_RATES_GITHUB_SPEC}"
        ),
    )
    source_group.add_argument(
        "--github",
        type=str,
        help=(
            "GitHub source spec 'OWNER/REPO:PATH@REF' (downloads via media.githubusercontent.com, works with Git LFS). "
            f"Default: {DEFAULT_GITHUB_SPEC}"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("web/data/atb_options.parquet"),
        help="Output parquet path (default: web/data/atb_options.parquet)",
    )
    args = parser.parse_args()

    parquet_path = None
    temp_downloads = []
    try:
        if args.parquet:
            parquet_path = args.parquet
        else:
            github_spec = args.github or DEFAULT_GITHUB_SPEC
            owner, repo, path, ref = _parse_github_spec(github_spec)
            url = _github_media_url(owner, repo, ref, path)
            print(f"Downloading parquet from: {url}")
            parquet_path = download_to_temp(url)
            temp_downloads.append(parquet_path)

        heat_rates_path = args.heat_rates_csv
        if args.heat_rates_github or (not args.parquet and heat_rates_path is None):
            heat_spec = args.heat_rates_github or DEFAULT_HEAT_RATES_GITHUB_SPEC
            owner, repo, path, ref = _parse_github_spec(heat_spec)
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
            print(f"Downloading heat rates from: {url}")
            heat_rates_path = download_to_temp(url)
            temp_downloads.append(heat_rates_path)
        df = build_index(parquet_path, heat_rates_path)
    finally:
        for temp_download in temp_downloads:
            try:
                temp_download.unlink(missing_ok=True)
            except Exception:
                pass

    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.out, index=False)

    print(f"Wrote {len(df):,} option rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
