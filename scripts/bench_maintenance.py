"""What a year of daily loads does to silver, and what maintenance gets back.

Copies silver/prices to a scratch folder, replays one report a day for DAYS days
through the same MERGE the daily job uses (2025's reports, moved two years on so
they're new rows), then measures, runs maintenance with no retention, and
measures again. The real table is never touched.

    uv run python scripts/bench_maintenance.py [days]
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

import polars as pl
from deltalake import DeltaTable

from lk_prices import silver
from lk_prices.maintain import maintain

DATA = Path("data")


def best_ms(run, tries: int = 5) -> float:
    times = []
    for _ in range(tries):
        start = time.perf_counter()
        run()
        times.append(time.perf_counter() - start)
    return min(times) * 1000


def measure(path: Path) -> dict[str, float]:
    p = str(path)
    return {
        "data files on disk": sum(
            1 for f in path.rglob("*.parquet") if "_delta_log" not in f.parts
        ),
        "files in the current version": len(DeltaTable(p).file_uris()),
        "log commits": len(list((path / "_delta_log").glob("*.json"))),
        "open the table (ms)": best_ms(lambda: DeltaTable(p)),
        "read everything (ms)": best_ms(lambda: pl.read_delta(p)),
        "one item at one market (ms)": best_ms(
            lambda: (
                pl.scan_delta(p)
                .filter((pl.col("item") == "Samba") & (pl.col("market") == "Pettah"))
                .collect()
            )
        ),
        "the replayed year (ms)": best_ms(
            lambda: pl.scan_delta(p).filter(pl.col("year") == 2027).collect()
        ),
    }


def main(days: int) -> None:
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        shutil.copytree(silver.prices_path(DATA), silver.prices_path(root))
        source = silver.read(DATA).filter(pl.col("year") == 2025)
        report_days = sorted(source["report_date"].unique())[:days]
        start = time.perf_counter()
        for day in report_days:
            moved = source.filter(pl.col("report_date") == day).with_columns(
                report_date=pl.col("report_date").dt.offset_by("2y"),
                listed_date=pl.col("listed_date").dt.offset_by("2y"),
                year=pl.lit(2027, dtype=pl.Int32),
            )
            silver.merge(moved, root)
        load_s = time.perf_counter() - start

        path = silver.prices_path(root)
        before = measure(path)
        result = maintain(path, "silver/prices", retention=timedelta(0))
        after = measure(path)

    print(f"Replayed {len(report_days)} daily loads in {load_s:.0f} s, then maintained:")
    print(f"compacted {result.compacted} files, vacuumed {result.vacuumed}\n")
    print("| | before | after |")
    print("|---|---:|---:|")
    for key in before:
        fmt = "{:.0f}" if "ms" not in key else "{:.1f}"
        print(f"| {key} | {fmt.format(before[key])} | {fmt.format(after[key])} |")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 250)
