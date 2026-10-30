"""The silver layer: one clean row per report date, item, market and price type.

    data/silver/prices/     Delta table, partitioned by year
    data/silver/rejects/    Delta table: PDFs that could not be read, and why

Partitioned by year, not by day: a day is only about 160 rows, and ten years of
daily partitions would be thousands of tiny files, which is slower to read than a
few larger ones.

Loads are a MERGE on the key, so loading a report twice changes nothing. A PDF
that doesn't parse is recorded in `rejects` instead of stopping the run, which is
how a new layout shows up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
from deltalake import DeltaTable, write_deltalake

from . import items
from .bronze import DATA_DIR
from .parse import Price

MONEY = pl.Decimal(12, 2)
SCHEMA = {
    "year": pl.Int32,
    # The date printed in the PDF. The listing's title and file name are wrong for
    # a few dozen reports (a 2019 report titled 2018, an old file name reused), so
    # the PDF's own date is the one used; the listing's is kept to show where they differ.
    "report_date": pl.Date,
    "listed_date": pl.Date,
    "section": pl.String,
    "item": pl.String,  # canonical, e.g. "Red Dhal" for "Dhal (Small)" (see items.py)
    "item_as_printed": pl.String,
    "unit": pl.String,
    "unit_as_printed": pl.String,  # None when the table printed no unit
    "market": pl.String,
    "price_type": pl.String,
    "price": MONEY,
    "previous_price": MONEY,
    # What previous_price compares with: "Yesterday", "Last Friday", or in 2019
    # "Previous Week". A day-on-day change is only that when this says so.
    "compared_with": pl.String,
    "layout": pl.String,
    "source_file": pl.String,
    "loaded_at": pl.Datetime("us", "UTC"),
}
KEY = ("report_date", "item", "market", "price_type")
REJECT_SCHEMA = {
    "source_file": pl.String,
    "error": pl.String,
    "rejected_at": pl.Datetime("us", "UTC"),
}


def quarantine_path(root: Path = DATA_DIR) -> Path:
    return root / "silver" / "quarantine"


def prices_path(root: Path = DATA_DIR) -> Path:
    return root / "silver" / "prices"


def rejects_path(root: Path = DATA_DIR) -> Path:
    return root / "silver" / "rejects"


def tidy_item(name: str) -> str:
    """Fix the typing slips in item names so one item has one name over the years."""
    name = " ".join(name.split())
    # "Big-Onion (Imp)" in 2019 and 2020 reports.
    name = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", " ", name)
    # "Red Onion (lmp)" uses a lower-case L for "Imp" (imported).
    name = re.sub(r"\((?:lmp|imp|Imp\.?)\)", "(Imp)", name)
    return name


def _item(printed: str) -> str:
    # An unknown name is kept as printed, so the checks can name it.
    tidy = tidy_item(printed)
    return items.canonical(tidy) or tidy


def _unit(p: Price) -> str | None:
    # The printed unit, unless the table printed none or is a known misprint.
    name = _item(p.item)
    if p.unit is None or items.is_unit_misprint(name, p.unit, p.report_date):
        return items.unit(name)
    return p.unit


def rows_from(
    prices: list[Price], layout: str, source_file: str, listed_date: date | None = None
) -> pl.DataFrame:
    now = datetime.now(UTC)
    return pl.DataFrame(
        [
            {
                "year": p.report_date.year,
                "report_date": p.report_date,
                "listed_date": listed_date or p.report_date,
                "section": items.section(_item(p.item)) or p.section,
                "item": _item(p.item),
                "item_as_printed": p.item,
                "unit": _unit(p),
                "unit_as_printed": p.unit,
                "market": p.market,
                "price_type": p.price_type,
                "price": p.price,
                "previous_price": p.previous_price,
                "compared_with": p.compared_with,
                "layout": layout,
                "source_file": source_file,
                "loaded_at": now,
            }
            for p in prices
        ],
        schema=SCHEMA,
    )


def upload_rank(file_name: str) -> int:
    """How late a file was uploaded among files of the same name.

    When a file is uploaded again under a name already taken, the website adds
    _0, _1, ... ("Daily Price Report - 17 05 2017_0.pdf"). The later upload is
    CBSL's correction, so it should win.
    """
    match = re.search(r"_(\d+)\.pdf$", file_name)
    return int(match.group(1)) + 1 if match else 0


def latest_upload(rows: pl.DataFrame) -> pl.DataFrame:
    """Keep one row per key when two files report the same day.

    A file whose listed date matches the date inside it is preferred over one
    whose listing is wrong; after that, the later upload wins.
    """
    ranked = rows.with_columns(
        _agrees=pl.col("listed_date") == pl.col("report_date"),
        _rank=pl.col("source_file").map_elements(upload_rank, return_dtype=pl.Int32),
    )
    return (
        ranked.sort("_agrees", "_rank")
        .unique(subset=list(KEY), keep="last", maintain_order=True)
        .drop("_agrees", "_rank")
    )


@dataclass(frozen=True)
class MergeResult:
    inserted: int
    updated: int


def merge(rows: pl.DataFrame, root: Path = DATA_DIR) -> MergeResult:
    """Upsert rows into silver, creating the table on the first load."""
    if rows.is_empty():
        return MergeResult(0, 0)
    path = prices_path(root)
    if not DeltaTable.is_deltatable(str(path)):
        path.parent.mkdir(parents=True, exist_ok=True)
        write_deltalake(str(path), rows, partition_by=["year"])
        return MergeResult(rows.height, 0)
    # A day may already be in silver from another file (CBSL uploaded the 21 June
    # 2022 report again as "22 June"). Decide between the stored rows and the new
    # ones with the same rule as within a batch, so the load order doesn't matter.
    days = rows["report_date"].unique()
    stored = pl.scan_delta(str(path)).filter(pl.col("report_date").is_in(days.implode())).collect()
    if stored.height:
        rows = latest_upload(pl.concat([stored, rows], how="vertical_relaxed"))
    # Naming the years being loaded lets Delta skip every other partition.
    years = ", ".join(str(y) for y in rows["year"].unique().sort())
    predicate = f"t.year IN ({years}) AND " + " AND ".join(f"t.{k} = s.{k}" for k in KEY)
    metrics = (
        DeltaTable(str(path))
        .merge(source=rows, predicate=predicate, source_alias="s", target_alias="t")
        # Only rewrite a row if a value changed, so a re-run writes no new files.
        .when_matched_update_all(
            # Each test in brackets: DataFusion misreads IS DISTINCT FROM next to OR.
            predicate="(s.price IS DISTINCT FROM t.price) "
            "OR (s.previous_price IS DISTINCT FROM t.previous_price) "
            "OR (s.source_file <> t.source_file)"
        )
        .when_not_matched_insert_all()
        .execute()
    )
    return MergeResult(metrics["num_target_rows_inserted"], metrics["num_target_rows_updated"])


def overwrite(rows: pl.DataFrame, root: Path = DATA_DIR) -> None:
    """Replace the whole table. The old version stays readable by time travel."""
    path = prices_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_deltalake(
        str(path), rows, mode="overwrite", partition_by=["year"], schema_mode="overwrite"
    )


def read(root: Path = DATA_DIR) -> pl.DataFrame:
    path = prices_path(root)
    if not DeltaTable.is_deltatable(str(path)):
        return pl.DataFrame(schema=SCHEMA)
    return pl.read_delta(str(path))


def loaded_files(root: Path = DATA_DIR) -> set[str]:
    path = prices_path(root)
    if not DeltaTable.is_deltatable(str(path)):
        return set()
    return set(pl.scan_delta(str(path)).select("source_file").unique().collect()["source_file"])


def quarantine(rows: pl.DataFrame, reason: str, root: Path = DATA_DIR) -> None:
    """Set single implausible prices aside, with the reason, instead of loading them."""
    held = rows.with_columns(reason=pl.lit(reason), held_at=pl.lit(datetime.now(UTC)))
    path = quarantine_path(root)
    if not DeltaTable.is_deltatable(str(path)):
        path.parent.mkdir(parents=True, exist_ok=True)
        write_deltalake(str(path), held)
        return
    predicate = " AND ".join(f"t.{k} = s.{k}" for k in KEY)
    (
        DeltaTable(str(path))
        .merge(source=held, predicate=predicate, source_alias="s", target_alias="t")
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute()
    )


def read_quarantine(root: Path = DATA_DIR) -> pl.DataFrame:
    path = quarantine_path(root)
    if not DeltaTable.is_deltatable(str(path)):
        return pl.DataFrame()
    return pl.read_delta(str(path))


def record_reject(source_file: str, error: str, root: Path = DATA_DIR) -> None:
    row = pl.DataFrame(
        [{"source_file": source_file, "error": error, "rejected_at": datetime.now(UTC)}],
        schema=REJECT_SCHEMA,
    )
    path = rejects_path(root)
    if not DeltaTable.is_deltatable(str(path)):
        path.parent.mkdir(parents=True, exist_ok=True)
        write_deltalake(str(path), row)
        return
    (
        DeltaTable(str(path))
        .merge(
            source=row,
            predicate="t.source_file = s.source_file",
            source_alias="s",
            target_alias="t",
        )
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute()
    )


def clear_reject(source_file: str, root: Path = DATA_DIR) -> None:
    path = rejects_path(root)
    if DeltaTable.is_deltatable(str(path)):
        DeltaTable(str(path)).delete(f"source_file = '{source_file}'")


def read_rejects(root: Path = DATA_DIR) -> pl.DataFrame:
    path = rejects_path(root)
    if not DeltaTable.is_deltatable(str(path)):
        return pl.DataFrame(schema=REJECT_SCHEMA)
    return pl.read_delta(str(path))
