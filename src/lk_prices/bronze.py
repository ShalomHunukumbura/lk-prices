"""The bronze layer: every report CBSL has listed, and its PDF exactly as served.

    data/bronze/reports/                          Delta table: one row per report link
    data/bronze/pdf/year=2021/price_report_20211013.pdf

The index is a Delta table so the crawl can be re-run at any time: it is merged on
the URL and only new links are inserted. PDFs are written once and never
rewritten, so a download that stops halfway picks up where it left off.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
from deltalake import DeltaTable, write_deltalake

from .cbsl import Report

DATA_DIR = Path(os.environ.get("LK_PRICES_DATA", "data"))

INDEX_SCHEMA = {
    "report_date": pl.Date,
    "url": pl.String,
    "file_name": pl.String,
    "title": pl.String,
    "discovered_at": pl.Datetime("us", "UTC"),
}


def index_path(root: Path = DATA_DIR) -> Path:
    return root / "bronze" / "reports"


def pdf_path(report_date: date, file_name: str, root: Path = DATA_DIR) -> Path:
    return root / "bronze" / "pdf" / f"year={report_date.year}" / file_name


def add_to_index(reports: list[Report], root: Path = DATA_DIR) -> int:
    """Record newly found reports. Returns how many were new."""
    if not reports:
        return 0
    now = datetime.now(UTC)
    rows = pl.DataFrame(
        [
            {
                "report_date": r.report_date,
                "url": r.url,
                "file_name": r.file_name,
                "title": r.title,
                "discovered_at": now,
            }
            for r in reports
        ],
        schema=INDEX_SCHEMA,
    ).unique("url", keep="first")
    path = index_path(root)
    if not DeltaTable.is_deltatable(str(path)):
        path.parent.mkdir(parents=True, exist_ok=True)
        write_deltalake(str(path), rows)
        return rows.height
    metrics = (
        DeltaTable(str(path))
        .merge(source=rows, predicate="t.url = s.url", source_alias="s", target_alias="t")
        .when_not_matched_insert_all()
        .execute()
    )
    return metrics["num_target_rows_inserted"]


def read_index(root: Path = DATA_DIR) -> pl.DataFrame:
    path = index_path(root)
    if not DeltaTable.is_deltatable(str(path)):
        return pl.DataFrame(schema=INDEX_SCHEMA)
    return pl.read_delta(str(path)).sort("report_date", "url")


def known_urls(root: Path = DATA_DIR) -> set[str]:
    return set(read_index(root)["url"].to_list())


def missing_pdfs(root: Path = DATA_DIR) -> list[Report]:
    """Reports in the index whose PDF is not on disk yet, newest first."""
    index = read_index(root)
    missing = [
        Report(row["report_date"], row["url"], row["file_name"], row["title"])
        for row in index.iter_rows(named=True)
        if not is_saved(pdf_path(row["report_date"], row["file_name"], root))
    ]
    return sorted(missing, key=lambda r: r.report_date, reverse=True)


def is_saved(path: Path) -> bool:
    """A PDF counts as downloaded only if it is really there and looks like a PDF."""
    try:
        with path.open("rb") as f:
            return f.read(4) == b"%PDF"
    except FileNotFoundError:
        return False


def save_pdf(report: Report, body: bytes, root: Path = DATA_DIR) -> Path:
    """Write a PDF so that a crash or power cut never leaves a broken file behind.

    Write to a temporary file, flush it to disk, then rename it into place. Without
    the flush, a power cut can keep the rename but lose the data, leaving an empty
    file under the real name (this happened once, on 2026-10-21).
    """
    path = pdf_path(report.report_date, report.file_name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
    folder = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)
    return path
