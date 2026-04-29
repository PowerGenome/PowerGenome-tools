"""Build the in-browser demand summary CSV.

Reads the source demand parquet (reeds_load_transformed.parquet) and writes a
smaller CSV file (web/data/demand_summary.csv) with one row per unique
(region, year, scenario, weather_year) combination and the total annual demand
in MWh.

The source parquet has columns: time_index, weather_year, region, load_mw,
year, scenario.  This script sums load_mw over time_index to compute total
annual demand in MWh (since load_mw * 1 hour = MWh).

Usage:
    # Use the default parquet hosted on GitHub (PowerGenome-data, via Git LFS media URL)
    python web/build_demand_data.py

    # Or provide a local parquet
    python web/build_demand_data.py \\
        --parquet /path/to/reeds_load_transformed.parquet \\
        --out web/data/demand_summary.csv

    # Or download from a specific GitHub repo/path/ref
    python web/build_demand_data.py \\
        --github gschivley/PowerGenome-data:data/reeds_load_transformed.parquet@main \\
        --out web/data/demand_summary.csv

Notes:
- Requires pandas + a parquet engine (pyarrow recommended).
- The source file is large (~800 MB); processing may take a few minutes.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

import pandas as pd

DEFAULT_GITHUB_SPEC = (
    "gschivley/PowerGenome-data:data/reeds_load_transformed.parquet@main"
)

_LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"


def _is_lfs_pointer(data: bytes) -> bool:
    """Return True if data looks like a Git LFS pointer file."""
    return data.lstrip().startswith(_LFS_POINTER_PREFIX)


def _parse_github_spec(spec: str) -> tuple[str, str, str, str]:
    """Parse 'owner/repo:path/to/file.parquet@ref' into components."""
    if "@" in spec:
        repo_path, ref = spec.rsplit("@", 1)
    else:
        repo_path, ref = spec, "main"
    if ":" in repo_path:
        repo, file_path = repo_path.split(":", 1)
    else:
        raise ValueError(f"Invalid GitHub spec (missing ':'): {spec!r}")
    if "/" not in repo:
        raise ValueError(f"Invalid repo in GitHub spec (expected owner/repo): {repo!r}")
    owner, repo_name = repo.split("/", 1)
    return owner, repo_name, file_path, ref


def _github_raw_url(owner: str, repo: str, path: str, ref: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"


def _github_media_url(owner: str, repo: str, path: str, ref: str) -> str:
    return f"https://media.githubusercontent.com/media/{owner}/{repo}/{ref}/{path}"


def download_file(url: str) -> bytes:
    """Download a file from *url*, automatically resolving Git LFS pointers."""
    url = str(url).strip()
    if not url:
        raise ValueError("Empty URL")

    try:
        with urllib.request.urlopen(url) as resp:  # nosec
            data = resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Download failed ({exc.code}) for {url}") from exc
    except Exception as exc:
        raise RuntimeError(f"Download failed for {url}: {exc}") from exc

    if _is_lfs_pointer(data):
        media_url = url.replace(
            "raw.githubusercontent.com", "media.githubusercontent.com"
        )
        if media_url != url:
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
                "actual file, then use --parquet to point to it."
            )

    return data


def build_demand_summary(source: Path | bytes) -> pd.DataFrame:
    """Read the demand parquet and compute total annual demand by
    (region, year, scenario, weather_year).

    Args:
        source: Path to local parquet file, or raw bytes.

    Returns:
        DataFrame with columns: region, year, scenario, weather_year,
        annual_demand_mwh.
    """
    try:
        import pyarrow.parquet as pq
        import pyarrow as pa

        if isinstance(source, bytes):
            buf = BytesIO(source)
            pf = pq.ParquetFile(buf)
        else:
            pf = pq.ParquetFile(source)

        print(
            f"  Source: {pf.metadata.num_row_groups} row groups, "
            f"{pf.metadata.num_rows:,} rows"
        )

        # Process in chunks to avoid loading all hourly data at once
        chunks = []
        n_rg = pf.metadata.num_row_groups
        for i in range(n_rg):
            if i % 20 == 0:
                print(f"  Row group {i}/{n_rg} ...")
            rg = pf.read_row_group(
                i, columns=["weather_year", "region", "load_mw", "year", "scenario"]
            )
            chunk = rg.to_pandas()
            agg = chunk.groupby(
                ["region", "year", "scenario", "weather_year"], as_index=False
            )["load_mw"].sum()
            chunks.append(agg)

        print("  Combining row-group aggregates ...")
        combined = pd.concat(chunks, ignore_index=True)
        final = combined.groupby(
            ["region", "year", "scenario", "weather_year"], as_index=False
        )["load_mw"].sum()
        final = final.rename(columns={"load_mw": "annual_demand_mwh"})

    except ImportError:
        # Fall back to pandas parquet reading (requires pyarrow or fastparquet)
        if isinstance(source, bytes):
            df = pd.read_parquet(BytesIO(source))
        else:
            df = pd.read_parquet(source)
        final = df.groupby(
            ["region", "year", "scenario", "weather_year"], as_index=False
        )["load_mw"].sum()
        final = final.rename(columns={"load_mw": "annual_demand_mwh"})

    # Ensure correct dtypes and sort
    final["year"] = pd.to_numeric(final["year"], errors="coerce").astype("Int64")
    final["weather_year"] = pd.to_numeric(
        final["weather_year"], errors="coerce"
    ).astype("Int64")
    final["region"] = final["region"].astype(str).str.strip()
    final["scenario"] = final["scenario"].astype(str).str.strip()
    final["annual_demand_mwh"] = final["annual_demand_mwh"].round(2)
    final = final.dropna(subset=["year", "weather_year"])
    final = final.sort_values(
        ["region", "year", "scenario", "weather_year"]
    ).reset_index(drop=True)

    return final


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download and save demand_summary.csv for the web app"
    )
    source_group = parser.add_mutually_exclusive_group(required=False)
    source_group.add_argument(
        "--parquet",
        type=Path,
        help="Path to local reeds_load_transformed.parquet file",
    )
    source_group.add_argument(
        "--url",
        type=str,
        help="URL to download the parquet from",
    )
    source_group.add_argument(
        "--github",
        type=str,
        default=DEFAULT_GITHUB_SPEC,
        help=(
            "GitHub spec 'owner/repo:path@ref' (default: %(default)s). "
            "Automatically tries the media CDN for LFS files."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("web/data/demand_summary.csv"),
        help="Output CSV path (default: web/data/demand_summary.csv)",
    )
    args = parser.parse_args()

    try:
        if args.parquet:
            print(f"Reading from local file: {args.parquet}")
            df = build_demand_summary(args.parquet)
        elif args.url:
            print(f"Downloading from: {args.url}")
            data = download_file(args.url)
            df = build_demand_summary(data)
        else:
            # Use GitHub spec (default)
            owner, repo, file_path, ref = _parse_github_spec(args.github)
            # Try media CDN first (LFS files are served there)
            media_url = _github_media_url(owner, repo, file_path, ref)
            raw_url = _github_raw_url(owner, repo, file_path, ref)
            print(f"Downloading from: {media_url}")
            try:
                data = download_file(media_url)
                df = build_demand_summary(data)
            except RuntimeError as exc:
                print(f"  -> media CDN failed ({exc}), trying raw URL ...")
                print(f"Downloading from: {raw_url}")
                data = download_file(raw_url)
                df = build_demand_summary(data)

        # Save to output
        args.out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out, index=False)

        # Report stats
        n_regions = df["region"].nunique()
        n_years = df["year"].nunique()
        n_scenarios = df["scenario"].nunique()
        n_weather_years = df["weather_year"].nunique()
        print(f"Saved {len(df):,} rows to {args.out}")
        print(
            f"  Regions: {n_regions}, Years: {n_years}, "
            f"Scenarios: {n_scenarios}, Weather years: {n_weather_years}"
        )

        return 0

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
