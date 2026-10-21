"""The bronze layer: what each store served, saved as it arrived.

One gzipped JSON file per store, day and category:

    data/bronze/store=glomark/date=2026-10-21/category=118.json.gz

The `key=value` folder names are Hive-style partitions, so DuckDB or Polars can
read the whole tree and filter on store and date without opening other files.
A file is never rewritten: the first fetch of the day is the record. That also
makes an interrupted run resumable, because finished categories are skipped.
"""

from __future__ import annotations

import gzip
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("LK_PRICES_DATA", "data"))


@dataclass(frozen=True)
class BronzeFile:
    store: str
    snapshot_date: date
    category_id: int
    url: str
    fetched_at: datetime
    products: list[dict]


def bronze_path(store: str, snapshot_date: date, category_id: int, root: Path = DATA_DIR) -> Path:
    return (
        root
        / "bronze"
        / f"store={store}"
        / f"date={snapshot_date.isoformat()}"
        / f"category={category_id}.json.gz"
    )


def save(file: BronzeFile, root: Path = DATA_DIR) -> Path:
    """Write a bronze file atomically, so a crash never leaves half a file behind."""
    path = bronze_path(file.store, file.snapshot_date, file.category_id, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "store": file.store,
        "snapshot_date": file.snapshot_date.isoformat(),
        "category_id": file.category_id,
        "url": file.url,
        "fetched_at": file.fetched_at.isoformat(),
        "products": file.products,
    }
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as out:
        json.dump(body, out, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)
    return path


def load(path: Path) -> BronzeFile:
    with gzip.open(path, "rt", encoding="utf-8") as src:
        body = json.load(src)
    return BronzeFile(
        store=body["store"],
        snapshot_date=date.fromisoformat(body["snapshot_date"]),
        category_id=body["category_id"],
        url=body["url"],
        fetched_at=datetime.fromisoformat(body["fetched_at"]),
        products=body["products"],
    )


def iter_day(store: str, snapshot_date: date, root: Path = DATA_DIR) -> Iterator[BronzeFile]:
    folder = bronze_path(store, snapshot_date, 0, root).parent
    for path in sorted(folder.glob("category=*.json.gz")):
        yield load(path)


def days(store: str, root: Path = DATA_DIR) -> list[date]:
    """Every day that has bronze files for a store, oldest first."""
    folder = root / "bronze" / f"store={store}"
    return sorted(
        date.fromisoformat(p.name.removeprefix("date="))
        for p in folder.glob("date=*")
        if p.is_dir()
    )
