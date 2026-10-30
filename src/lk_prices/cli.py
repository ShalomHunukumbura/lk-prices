"""The command line: `lk-prices discover`, `fetch`, `silver`, `rebuild`, `gold`, `audit`,
`maintain`, `run`, `stats`."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import polars as pl

from . import __version__, bronze, cbsl, flags, gold, maintain, silver
from .checks import implausible, missing_items, problems
from .parse import NotAReport, ParseError, parse_pdf


def discover(
    root: Path, crawl_all: bool = False, start: int = 0, client: cbsl.Client | None = None
) -> int:
    """Read the report listing, newest first, and add new reports to the index.

    By default it stops at the first page with nothing new, which for a daily run
    is page 0. With crawl_all it reads every page, for the first backfill or to
    catch reports CBSL added out of order.
    """
    client = client or cbsl.Client()
    known = bronze.known_urls(root)
    page, last, added = start, None, 0
    while last is None or page <= last:
        html = client.listing(page)
        if last is None:
            last = cbsl.last_page(html)
        reports = cbsl.parse_listing(html)
        if not reports:
            raise cbsl.SourceError(f"listing page {page} has no reports; has the page changed?")
        new = [r for r in reports if r.url not in known]
        added += bronze.add_to_index(new, root)
        known.update(r.url for r in new)
        print(
            f"  page {page}/{last}: {len(reports)} reports, {len(new)} new "
            f"({reports[-1].report_date} to {reports[0].report_date})"
        )
        if not new and not crawl_all:
            break
        page += 1
    print(f"discover: {added} new reports")
    return added


def fetch(root: Path, limit: int | None = None, client: cbsl.Client | None = None) -> int:
    """Download PDFs that are in the index but not on disk. Returns how many failed."""
    client = client or cbsl.Client()
    missing = bronze.missing_pdfs(root)
    todo = missing[:limit] if limit else missing
    print(f"fetch: {len(missing)} PDFs missing, fetching {len(todo)}")
    failed = 0
    for i, report in enumerate(todo, 1):
        try:
            body = client.pdf(report)
        except cbsl.SourceError as error:
            failed += 1
            print(f"  [{i}/{len(todo)}] {report.report_date}: {error}", file=sys.stderr)
            continue
        bronze.save_pdf(report, body, root)
        print(f"  [{i}/{len(todo)}] {report.report_date}: {len(body) // 1024} KB")
    return failed


def _parse_file(path: Path, listed_date: date, root: Path) -> pl.DataFrame:
    """Parse and check one PDF. Raises ParseError with the reason if it can't be used."""
    layout, prices = parse_pdf(path.read_bytes(), listed_date)
    rows = silver.rows_from(prices, layout, path.name, listed_date)
    found = problems(rows)
    if found:
        raise ParseError("; ".join(found))
    # A typing slip or two: keep the report, set those prices aside with the reason.
    odd = implausible(rows)
    if odd.height:
        silver.quarantine(odd, "retail below half of wholesale", root)
        rows = rows.join(odd.select(silver.KEY), on=list(silver.KEY), how="anti")
    return rows


def _pdfs_on_disk(root: Path) -> list[tuple[date, Path]]:
    """(listed date, PDF) for every PDF on disk, oldest first, later uploads last."""
    index = bronze.read_index(root)
    found = [
        (d, silver.upload_rank(f), bronze.pdf_path(d, f, root))
        for d, f in index.select("report_date", "file_name").iter_rows()
    ]
    return [(d, path) for d, _, path in sorted(found) if bronze.is_saved(path)]


def load_silver(root: Path, batch_size: int = 200) -> tuple[int, int]:
    """Parse PDFs not yet in silver and merge them in.

    Returns (loaded, rejected), where rejected leaves out notices and image-only
    PDFs, which are listed in `rejects` but are not errors.
    """
    done = silver.loaded_files(root)
    todo = [(d, p) for d, p in _pdfs_on_disk(root) if p.name not in done]
    print(f"silver: {len(todo)} reports to load")
    loaded = rejected = 0
    for start in range(0, len(todo), batch_size):
        frames, parsed, errors = [], [], {}
        for listed, path in todo[start : start + batch_size]:
            try:
                frames.append(_parse_file(path, listed, root))
                parsed.append(path.name)
            except ParseError as error:
                # Notices and image-only PDFs are recorded but aren't failures:
                # there is nothing to fix. Anything else needs a look.
                if not isinstance(error, NotAReport):
                    rejected += 1
                errors[path.name] = str(error)
        silver.update_rejects(errors, parsed, root)
        if frames:
            result = silver.merge(silver.latest_upload(pl.concat(frames)), root)
            loaded += len(frames)
            print(
                f"  {start + len(todo[start : start + batch_size])}/{len(todo)}: "
                f"{result.inserted} rows inserted, {result.updated} updated"
            )
    print(f"silver: {loaded} loaded, {rejected} rejected")
    return loaded, rejected


def rebuild_silver(root: Path) -> None:
    """Re-read every PDF and replace silver, after a change to the parser."""
    frames, parsed, errors = [], [], {}
    for listed, path in _pdfs_on_disk(root):
        try:
            frames.append(_parse_file(path, listed, root))
            parsed.append(path.name)
        except ParseError as error:
            errors[path.name] = str(error)
    silver.update_rejects(errors, parsed, root)
    if not frames:
        print("rebuild: nothing parsed", file=sys.stderr)
        return
    rows = silver.latest_upload(pl.concat(frames))
    silver.overwrite(rows, root)
    print(
        f"rebuild: {rows.height} rows from {len(frames)} reports, "
        f"{silver.read_rejects(root).height} rejected"
    )


def build_gold(root: Path, version: int | None = None) -> None:
    tables = gold.build(root, version)
    headline = tables["index"].filter(
        (pl.col("basket") == "all") & (pl.col("price_type") == "retail")
    )
    latest = headline.sort("month").row(-1, named=True)
    print(
        f"gold: index to {latest['month']:%B %Y} at {latest['index']:.1f} "
        f"(August 2016 = 100), from silver version {latest['silver_version']}"
    )


def audit(root: Path) -> None:
    """The data quality picture across the whole of silver."""
    flagged = flags.flag(silver.read(root))
    checked = flagged["checked"].sum()
    restated = flagged["restated"].sum()
    print(f"{flagged.height} prices, {flagged['report_date'].n_unique()} reports")
    print(
        f"cross-check: {checked} prices have a next-day report that repeats them; "
        f"{(1 - restated / checked) * 100:.2f}% agree"
    )
    print(
        f"jumps of 3x or more: {flagged['jump'].sum()}; left out of the index as "
        f"typing slips: {flagged['suspect'].sum()}"
    )
    gaps = missing_items(flagged)
    print(
        f"reports missing an item both neighbours carry: {gaps.height} "
        f"({gaps['missing'].list.len().sum()} items in all)"
    )
    with pl.Config(tbl_rows=20, tbl_hide_dataframe_shape=True, fmt_str_lengths=80):
        print(gaps.sort(pl.col("missing").list.len(), descending=True).head(5))
        print(
            flagged.filter("suspect")
            .select("report_date", "item", "market", "price_type", "price")
            .sort("report_date")
        )
        by_month = gold.monthly(gold.trusted(silver.read(root)))
        index_rows = pl.concat([gold.index(by_month, "retail"), gold.index(by_month, "wholesale")])
        for start, end in ((2017, 2021), (2021, 2025)):
            print(f"index change {start} to {end} against the truth (see gold.validate):")
            print(
                gold.validate(by_month, index_rows, start, end).with_columns(
                    pl.col("truth", "index").round(2), pl.col("off_pct").round(1)
                )
            )


def stats(root: Path) -> None:
    index = bronze.read_index(root)
    if index.is_empty():
        print("the report index is empty; run `lk-prices discover` first")
        return
    downloaded = [
        bronze.is_saved(bronze.pdf_path(d, f, root))
        for d, f in index.select("report_date", "file_name").iter_rows()
    ]
    by_year = (
        index.with_columns(
            year=index["report_date"].dt.year(), downloaded=pl.Series(downloaded, dtype=pl.Boolean)
        )
        .group_by("year")
        .agg(reports=pl.len(), downloaded=pl.col("downloaded").sum())
        .sort("year")
    )
    loaded = silver.read(root).group_by("year").agg(silver_reports=pl.col("report_date").n_unique())
    rejects = silver.read_rejects(root)
    print(by_year.join(loaded, on="year", how="left"))
    print(f"{index.height} reports, {index['report_date'].min()} to {index['report_date'].max()}")
    if rejects.height:
        print(f"{rejects.height} rejected; first few:")
        print(rejects.sort("source_file").head(10))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lk-prices", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data", type=Path, default=bronze.DATA_DIR, help="data folder")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("discover", help="find new reports on CBSL's listing")
    cmd.add_argument("--all", action="store_true", help="read every listing page")
    cmd.add_argument("--start", type=int, default=0, help="first page to read (to resume)")
    cmd = sub.add_parser("fetch", help="download PDFs not on disk yet")
    cmd.add_argument("--limit", type=int, default=None, help="at most this many")
    cmd = sub.add_parser("silver", help="parse new PDFs into the silver table")
    cmd.add_argument("--batch", type=int, default=200, help="reports per MERGE")
    sub.add_parser("rebuild", help="re-parse every PDF and replace silver")
    cmd = sub.add_parser("gold", help="build the index, monthly prices and margins")
    cmd.add_argument("--version", type=int, default=None, help="silver version to build from")
    sub.add_parser("audit", help="cross-checks, flagged prices and index validation")
    sub.add_parser("maintain", help="compact, checkpoint and vacuum every table (weekly)")
    sub.add_parser("run", help="discover, fetch, load silver, build gold (the daily job)")
    sub.add_parser("stats", help="reports and downloads per year")

    args = parser.parse_args(argv)
    if args.command == "discover":
        discover(args.data, crawl_all=args.all, start=args.start)
        return 0
    if args.command == "fetch":
        return 1 if fetch(args.data, args.limit) else 0
    if args.command == "silver":
        _, rejected = load_silver(args.data, args.batch)
        return 1 if rejected else 0
    if args.command == "rebuild":
        rebuild_silver(args.data)
        return 0
    if args.command == "gold":
        build_gold(args.data, args.version)
        return 0
    if args.command == "audit":
        audit(args.data)
        return 0
    if args.command == "maintain":
        for r in maintain.maintain_all(args.data):
            print(
                f"  {r.table}: {r.files_before} -> {r.files_after} files "
                f"({r.compacted} compacted, {r.vacuumed} vacuumed)"
            )
        return 0
    if args.command == "run":
        client = cbsl.Client()
        discover(args.data, client=client)
        failed = fetch(args.data, client=client)
        _, rejected = load_silver(args.data)
        build_gold(args.data)
        return 1 if failed or rejected else 0
    if args.command == "stats":
        stats(args.data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
