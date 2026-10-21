"""The command line: `lk-prices fetch`, `silver`, `run`, `rebuild`, `stats`."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from . import __version__, bronze, glomark, silver
from .checks import has_errors, run_checks

COLOMBO = ZoneInfo("Asia/Colombo")


def today_in_colombo() -> date:
    return datetime.now(COLOMBO).date()


def fetch(snapshot_date: date, root: Path, client: glomark.Client | None = None) -> int:
    """Land today's Glomark category pages in bronze. Returns how many failed."""
    client = client or glomark.Client()
    categories = client.categories()
    print(f"glomark: {len(categories)} categories in the sitemap")
    failed = 0
    for i, category in enumerate(categories, 1):
        path = bronze.bronze_path(glomark.STORE, snapshot_date, category.id, root)
        if path.exists():
            continue
        try:
            products = client.products(category)
        except glomark.SourceError as error:
            failed += 1
            print(f"  [{i}/{len(categories)}] category {category.id}: {error}", file=sys.stderr)
            continue
        bronze.save(
            bronze.BronzeFile(
                store=glomark.STORE,
                snapshot_date=snapshot_date,
                category_id=category.id,
                url=category.url,
                fetched_at=datetime.now(UTC),
                products=products,
            ),
            root,
        )
        print(f"  [{i}/{len(categories)}] category {category.id}: {len(products)} products")
    return failed


def load_silver(snapshot_date: date, root: Path) -> bool:
    """Move one day from bronze to silver, if it passes the checks."""
    rows = silver.glomark_rows(bronze.iter_day(glomark.STORE, snapshot_date, root))
    earlier = [d for d in bronze.days(glomark.STORE, root) if d < snapshot_date]
    previous = silver.read(root, earlier[-1]).height if earlier else None

    findings = run_checks(rows, previous)
    for f in findings:
        print(f"  {f.level}: {f.check}: {f.detail}", file=sys.stderr)
    if has_errors(findings):
        print(f"silver: {snapshot_date} not loaded, fix the errors above", file=sys.stderr)
        return False

    result = silver.merge(rows, root)
    print(
        f"silver: {snapshot_date}: {rows.height} products, "
        f"{result.inserted} inserted, {result.updated} updated"
    )
    return True


def rebuild_silver(root: Path) -> bool:
    """Rebuild silver from every bronze day, after a change to the parsing."""
    frames = []
    for day in bronze.days(glomark.STORE, root):
        rows = silver.glomark_rows(bronze.iter_day(glomark.STORE, day, root))
        findings = run_checks(rows)
        if has_errors(findings):
            for f in findings:
                print(f"  {day}: {f.level}: {f.check}: {f.detail}", file=sys.stderr)
            print("rebuild: stopped, silver left as it was", file=sys.stderr)
            return False
        frames.append(rows)
    if not frames:
        print("rebuild: no bronze files", file=sys.stderr)
        return False
    total = silver.rebuild(pl.concat(frames), root)
    print(f"rebuild: silver now has {total} rows from {len(frames)} days")
    return True


def stats(root: Path) -> None:
    rows = silver.read(root)
    if rows.is_empty():
        print("silver is empty")
        return
    by_day = rows.group_by("store", "snapshot_date").len().sort("snapshot_date")
    print(by_day)
    print(f"{rows['product_id'].n_unique()} distinct products over {by_day.height} store-days")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lk-prices", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data", type=Path, default=bronze.DATA_DIR, help="data folder")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [
        ("fetch", "land today's store pages in bronze"),
        ("silver", "load a day from bronze into the silver Delta table"),
        ("run", "fetch, then load silver"),
    ]:
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument(
            "--date",
            type=date.fromisoformat,
            default=None,
            help="snapshot date (default: today in Colombo)",
        )
    sub.add_parser("rebuild", help="rebuild silver from all of bronze")
    sub.add_parser("stats", help="row counts per day in silver")

    args = parser.parse_args(argv)
    day = getattr(args, "date", None) or today_in_colombo()

    if args.command == "fetch":
        return 1 if fetch(day, args.data) else 0
    if args.command == "silver":
        return 0 if load_silver(day, args.data) else 1
    if args.command == "run":
        failed = fetch(day, args.data)
        loaded = load_silver(day, args.data)
        return 0 if loaded and not failed else 1
    if args.command == "rebuild":
        return 0 if rebuild_silver(args.data) else 1
    if args.command == "stats":
        stats(args.data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
