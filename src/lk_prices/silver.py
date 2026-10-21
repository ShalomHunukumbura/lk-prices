"""The silver layer: one clean, typed row per store, product and day.

Bronze keeps each store's own nested JSON. Silver flattens it into a single
schema and writes it to a Delta Lake table at `data/silver/prices`, partitioned
by `snapshot_date`.

Loads are a MERGE on (store, snapshot_date, product_id), not an append, so
loading the same day twice leaves the table exactly as it was.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import polars as pl
from deltalake import DeltaTable, write_deltalake

from .bronze import DATA_DIR, BronzeFile

MONEY = pl.Decimal(12, 2)
SCHEMA = {
    "store": pl.String,
    "snapshot_date": pl.Date,
    "product_id": pl.Int64,
    "sku": pl.String,
    "name": pl.String,
    "brand": pl.String,
    "department": pl.String,
    "category": pl.String,
    "subcategory": pl.String,
    "unit": pl.String,
    "display_quantity": pl.Float64,
    "price": MONEY,
    "promo_price": MONEY,
    "promo_rate": pl.Float64,
    "has_promo": pl.Boolean,
    "is_out_of_stock": pl.Boolean,
    "fetched_at": pl.Datetime("us", "UTC"),
}
KEY = ("store", "snapshot_date", "product_id")


def silver_path(root: Path = DATA_DIR) -> Path:
    return root / "silver" / "prices"


def _money(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _name(obj) -> str | None:
    return obj.get("name") if isinstance(obj, dict) else None


def glomark_rows(files: Iterable[BronzeFile]) -> pl.DataFrame:
    """Flatten a day of Glomark bronze files.

    A product can sit in more than one category page. It is the same product at
    the same price, so it is kept once, from the first page it appears on.
    """
    rows = []
    seen: set[int] = set()
    for file in files:
        for p in file.products:
            if p["id"] in seen:
                continue
            seen.add(p["id"])
            prices = p.get("priceDetails") or {}
            price = _money(prices.get("price", p.get("price")))
            promo_price = _money(prices.get("promoPrice", p.get("promoPrice")))
            rows.append(
                {
                    "store": file.store,
                    "snapshot_date": file.snapshot_date,
                    "product_id": p["id"],
                    "sku": p.get("erpCode"),
                    "name": " ".join((p.get("name") or "").split()),
                    "brand": _name(p.get("brandDetails")),
                    "department": _name(p.get("department")),
                    "category": _name(p.get("categoryDetails")),
                    "subcategory": _name(p.get("subCategoryDetails")),
                    "unit": p.get("unit"),
                    "display_quantity": p.get("displayQuantity"),
                    "price": price,
                    "promo_price": promo_price,
                    "promo_rate": prices.get("promoRate", p.get("promoRate")),
                    # Glomark's own hasPromo flag is false even on discounted
                    # products, so a promo is read from the prices instead.
                    "has_promo": promo_price is not None
                    and price is not None
                    and promo_price < price,
                    "is_out_of_stock": bool(p.get("isOutOfStock")),
                    "fetched_at": file.fetched_at,
                }
            )
    return pl.DataFrame(rows, schema=SCHEMA)


@dataclass(frozen=True)
class MergeResult:
    inserted: int
    updated: int


def merge(rows: pl.DataFrame, root: Path = DATA_DIR) -> MergeResult:
    """Upsert rows into the silver table, creating it on the first load."""
    path = silver_path(root)
    if rows.is_empty():
        return MergeResult(0, 0)
    if not DeltaTable.is_deltatable(str(path)):
        path.parent.mkdir(parents=True, exist_ok=True)
        write_deltalake(str(path), rows, partition_by=["snapshot_date"])
        return MergeResult(rows.height, 0)

    # Naming the days being loaded lets Delta skip every other partition.
    days = ", ".join(f"'{d.isoformat()}'" for d in rows["snapshot_date"].unique().sort())
    predicate = f"t.snapshot_date IN ({days}) AND " + " AND ".join(f"t.{k} = s.{k}" for k in KEY)
    metrics = (
        DeltaTable(str(path))
        .merge(source=rows, predicate=predicate, source_alias="s", target_alias="t")
        # Bronze files are never rewritten, so an unchanged fetched_at means an
        # unchanged row. Skipping those keeps a re-run from writing new files.
        .when_matched_update_all(predicate="s.fetched_at <> t.fetched_at")
        .when_not_matched_insert_all()
        .execute()
    )
    return MergeResult(metrics["num_target_rows_inserted"], metrics["num_target_rows_updated"])


def rebuild(rows: pl.DataFrame, root: Path = DATA_DIR) -> int:
    """Replace the whole table with rows rebuilt from bronze.

    This is for when the parsing changes. It is an overwrite, not a delete, so the
    previous version stays readable through Delta's time travel until vacuumed.
    """
    path = silver_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_deltalake(
        str(path),
        rows,
        mode="overwrite",
        partition_by=["snapshot_date"],
        schema_mode="overwrite",
    )
    return rows.height


def read(root: Path = DATA_DIR, snapshot_date: date | None = None) -> pl.DataFrame:
    path = silver_path(root)
    if not DeltaTable.is_deltatable(str(path)):
        return pl.DataFrame(schema=SCHEMA)
    frame = pl.scan_delta(str(path))
    if snapshot_date is not None:
        frame = frame.filter(pl.col("snapshot_date") == snapshot_date)
    return frame.collect()
