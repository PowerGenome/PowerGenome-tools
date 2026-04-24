"""Build the in-browser fuel prices CSV.

The web app loads fuel price scenarios from `web/data/fuel_prices.csv`.
This script downloads the latest data from PowerGenome-data and saves it locally.

Usage:
    # Download from default PowerGenome-data location (tries parquet first, falls back to CSV)
    python web/build_fuel_prices.py

    # Or provide a custom URL (parquet or CSV auto-detected from extension)
    python web/build_fuel_prices.py \
        --url https://example.com/fuel_prices.parquet \
        --out web/data/fuel_prices.csv

    # Or use a local file (parquet or CSV)
    python web/build_fuel_prices.py \
        --file /path/to/fuel_prices.parquet \
        --out web/data/fuel_prices.csv

Notes:
- Requires pandas (and pyarrow or fastparquet for parquet support).
- Validates required columns: data_year, fuel, scenario.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

import pandas as pd

DEFAULT_PARQUET_URL = "https://raw.githubusercontent.com/gschivley/PowerGenome-data/main/data/fuel_prices.parquet"
DEFAULT_CSV_URL = "https://raw.githubusercontent.com/gschivley/PowerGenome-data/main/data/fuel_prices.csv"


def _url_format(url: str) -> str | None:
    """Return 'parquet' or 'csv' based on URL extension, or None if unknown."""
    lower = url.lower().split("?")[0]
    if lower.endswith(".parquet"):
        return "parquet"
    if lower.endswith(".csv"):
        return "csv"
    return None


_LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"


def _is_lfs_pointer(data: bytes) -> bool:
    """Return True if data looks like a Git LFS pointer file."""
    return data.lstrip().startswith(_LFS_POINTER_PREFIX)


def _lfs_media_url(url: str) -> str | None:
    """Convert a raw.githubusercontent.com URL to its media.githubusercontent.com
    equivalent, which serves actual LFS content.  Returns None if the URL is not
    a raw GitHub URL.
    """
    if "raw.githubusercontent.com" in url:
        return url.replace("raw.githubusercontent.com", "media.githubusercontent.com")
    return None


def download_file(url: str) -> bytes:
    """Download file from URL and return raw bytes.

    If the response is a Git LFS pointer (e.g. the file is stored in LFS on
    GitHub) and the URL is a raw.githubusercontent.com URL, the download is
    automatically retried via media.githubusercontent.com which serves the
    actual LFS content.
    """
    url = str(url).strip()
    if not url:
        raise ValueError("Empty URL")

    try:
        with urllib.request.urlopen(url) as resp:  # nosec - URL is user-provided
            data = resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Download failed ({exc.code}) for {url}") from exc
    except Exception as exc:
        raise RuntimeError(f"Download failed for {url}: {exc}") from exc

    if _is_lfs_pointer(data):
        media_url = _lfs_media_url(url)
        if media_url:
            print(f"  -> LFS pointer detected; retrying via media CDN: {media_url}")
            try:
                with urllib.request.urlopen(media_url) as resp:  # nosec
                    data = resp.read()
            except urllib.error.HTTPError as exc:
                raise RuntimeError(
                    f"LFS media download failed ({exc.code}) for {media_url}"
                ) from exc
            except Exception as exc:
                raise RuntimeError(
                    f"LFS media download failed for {media_url}: {exc}"
                ) from exc
        else:
            raise RuntimeError(
                f"Downloaded file from {url} appears to be a Git LFS pointer. "
                "Run 'git lfs pull' in the PowerGenome-data repo to fetch the "
                "actual file, then use --file to point to it."
            )

    return data


def read_dataframe(source: Path | bytes, fmt: str) -> pd.DataFrame:
    """Read a DataFrame from a local path or raw bytes in 'parquet' or 'csv' format."""
    if fmt == "parquet":
        if isinstance(source, bytes):
            return pd.read_parquet(BytesIO(source))
        return pd.read_parquet(source)
    else:
        if isinstance(source, bytes):
            from io import StringIO

            return pd.read_csv(StringIO(source.decode("utf-8")))
        return pd.read_csv(source)


def validate_and_clean(df: pd.DataFrame) -> pd.DataFrame:
    """Validate and clean the fuel prices DataFrame.

    If a ``year`` and ``price`` column are present, aggregates prices across
    regions by averaging, returning one row per (data_year, fuel, scenario, year).
    This keeps multi-year price trends for in-browser charting while staying
    compact.  Without ``year``/``price`` the output retains one row per
    (data_year, fuel, scenario).
    """
    # Check for required columns (case-insensitive)
    required = {"data_year", "fuel", "scenario"}
    lower_cols = {c.lower() for c in df.columns}

    if not required.issubset(lower_cols):
        missing = required - lower_cols
        raise ValueError(
            f"Missing required columns: {missing}. Found: {list(df.columns)}"
        )

    # Normalize column names
    col_map = {c: c.lower() for c in df.columns}
    df = df.rename(columns=col_map)

    # Ensure proper types
    df["data_year"] = pd.to_numeric(df["data_year"], errors="coerce").astype("Int64")
    df["fuel"] = df["fuel"].astype(str).str.strip()
    df["scenario"] = df["scenario"].astype(str).str.strip()

    # Drop rows with missing critical data
    df = df.dropna(subset=["data_year"])
    df = df[(df["fuel"] != "") & (df["scenario"] != "")]

    # Aggregate across regions: average price per (data_year, fuel, scenario, year)
    # when price/year columns are available.
    if "year" in df.columns and "price" in df.columns:
        df["year"] = pd.to_numeric(df["year"], errors="coerce")
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df = df.dropna(subset=["year", "price"])
        df = (
            df.groupby(["data_year", "fuel", "scenario", "year"], as_index=False)[
                "price"
            ]
            .mean()
            .sort_values(["data_year", "fuel", "scenario", "year"])
            .reset_index(drop=True)
        )
    else:
        df = df.drop_duplicates(subset=["data_year", "fuel", "scenario"])

    if df.empty:
        raise ValueError("No valid data rows after cleaning")

    return df


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download and save fuel_prices.csv for the web app"
    )
    source_group = parser.add_mutually_exclusive_group(required=False)
    source_group.add_argument(
        "--file",
        type=Path,
        help="Path to local fuel_prices file (.parquet or .csv)",
    )
    source_group.add_argument(
        "--url",
        type=str,
        help="URL to download fuel prices from (.parquet or .csv; default tries parquet then CSV)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("web/data/fuel_prices.csv"),
        help="Output CSV path (default: web/data/fuel_prices.csv)",
    )
    args = parser.parse_args()

    try:
        if args.file:
            fmt = "parquet" if args.file.suffix.lower() == ".parquet" else "csv"
            print(f"Reading from local file ({fmt}): {args.file}")
            df = read_dataframe(args.file, fmt)
        else:
            url = args.url
            if url:
                fmt = _url_format(url) or "csv"
                print(f"Downloading from: {url}")
                data = download_file(url)
                df = read_dataframe(data, fmt)
            else:
                # Auto-detect: try parquet first, fall back to CSV
                print(f"Trying parquet: {DEFAULT_PARQUET_URL}")
                try:
                    data = download_file(DEFAULT_PARQUET_URL)
                    df = read_dataframe(data, "parquet")
                    print("  -> parquet download succeeded")
                except RuntimeError as parquet_exc:
                    print(f"  -> parquet unavailable ({parquet_exc}), trying CSV...")
                    print(f"Downloading from: {DEFAULT_CSV_URL}")
                    data = download_file(DEFAULT_CSV_URL)
                    df = read_dataframe(data, "csv")

        # Validate and clean
        df = validate_and_clean(df)

        # Save to output
        args.out.parent.mkdir(parents=True, exist_ok=True)
        # Save as csv, only write 2 decimal places for price if present
        df.to_csv(
            args.out,
            index=False,
            float_format="%.2f" if "price" in df.columns else None,
        )

        # Report stats
        n_years = df["data_year"].nunique()
        n_fuels = df["fuel"].nunique()
        n_scenarios = df["scenario"].nunique()
        print(f"Saved {len(df):,} rows to {args.out}")
        print(f"  Years: {n_years}, Fuels: {n_fuels}, Scenarios: {n_scenarios}")

        return 0

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
