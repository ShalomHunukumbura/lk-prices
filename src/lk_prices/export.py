"""The page's data: derived figures only, as small JSON files in site/data/.

CBSL's site says "All Rights Reserved" and offers no reuse licence, so the page
publishes what this project computes - the index, monthly medians, margins and
the latest report's biggest moves - credited to CBSL, and links to CBSL's own
PDFs for the prices themselves. It does not republish the daily price tables.

    index.json     monthly index per basket and price type (null where unmeasured)
    items.json     monthly median price per item, market and price type
    margins.json   median retail markup over wholesale per year and market
    movers.json    the latest report's largest retail moves against the one before
    usd.json       monthly LKR per USD, from lkr-rates (A1), if it's next door
    meta.json      when it was built, from which silver version, the latest report
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from . import flags, gold
from .bronze import DATA_DIR

SITE_DATA = Path("site/data")
# lkr-rates keeps its clean rates here; the USD view is left out if it isn't.
RATES = Path(os.environ.get("LKR_RATES_DATA", "../01-lkr-rates/data/clean/rates.parquet"))
MOVERS = 8


def _months(frame: pl.DataFrame) -> list[str]:
    return [m.strftime("%Y-%m") for m in frame["month"].unique().sort()]


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def index_json(index_rows: pl.DataFrame) -> dict:
    months = _months(index_rows)
    series = {}
    for (basket, price_type), part in index_rows.sort("month").group_by(
        "basket", "price_type", maintain_order=True
    ):
        by_month = dict(zip(part["month"].dt.strftime("%Y-%m"), part["index"], strict=True))
        series.setdefault(basket, {})[price_type] = [_round(by_month.get(m)) for m in months]
    return {"months": months, "base": "2016-08", "series": series}


def items_json(monthly: pl.DataFrame) -> dict:
    months = _months(monthly)
    items: dict = {}
    for (section, item, market, price_type), part in monthly.group_by(
        "section", "item", "market", "price_type"
    ):
        by_month = dict(zip(part["month"].dt.strftime("%Y-%m"), part["price"], strict=True))
        entry = items.setdefault(item, {"section": section, "markets": {}})
        entry["markets"].setdefault(market, {})[price_type] = [
            _round(by_month.get(m), 2) for m in months
        ]
    return {"months": months, "items": dict(sorted(items.items()))}


def margins_json(margins: pl.DataFrame) -> dict:
    yearly = (
        margins.with_columns(year=pl.col("month").dt.year())
        .group_by("year", "market")
        .agg(markup=pl.col("markup").median())
        .sort("year")
    )
    years = sorted(yearly["year"].unique().to_list())
    markets = {}
    for (market,), part in yearly.group_by("market"):
        by_year = dict(zip(part["year"], part["markup"], strict=True))
        markets[market] = [_round(by_year[y] * 100) if y in by_year else None for y in years]
    return {"years": years, "markets": markets}


def movers_json(prices: pl.DataFrame) -> dict:
    """The latest report's largest retail moves against the report before it."""
    days = prices["report_date"].unique().sort()
    latest, previous = days[-1], days[-2]
    retail = prices.filter(pl.col("price_type") == "retail")
    pairs = (
        retail.filter(pl.col("report_date") == latest)
        .join(
            retail.filter(pl.col("report_date") == previous).select(*flags.SERIES, before="value"),
            on=list(flags.SERIES),
        )
        .with_columns(change=(pl.col("value") / pl.col("before") - 1) * 100)
        .filter(pl.col("change") != 0)
    )

    def rows(frame: pl.DataFrame) -> list[dict]:
        return [
            {
                "item": r["item"],
                "market": r["market"],
                "price": r["value"],
                "change": round(r["change"], 1),
            }
            for r in frame.iter_rows(named=True)
        ]

    return {
        "date": latest.isoformat(),
        "previous": previous.isoformat(),
        "up": rows(pairs.filter(pl.col("change") > 0).sort("change", descending=True).head(MOVERS)),
        "down": rows(pairs.filter(pl.col("change") < 0).sort("change").head(MOVERS)),
    }


def usd_json(months: list[str], rates: Path = RATES) -> dict | None:
    if not rates.exists():
        return None
    usd = (
        pl.read_parquet(rates)
        .filter((pl.col("currency") == "USD") & (pl.col("rate_type") == "indicative"))
        .group_by(month=pl.col("rate_date").dt.strftime("%Y-%m"))
        .agg(pl.col("rate").cast(pl.Float64).mean())
    )
    by_month = dict(zip(usd["month"], usd["rate"], strict=True))
    return {"months": months, "lkr_per_usd": [_round(by_month.get(m), 2) for m in months]}


def export(root: Path = DATA_DIR, out: Path = SITE_DATA, rates: Path = RATES) -> list[Path]:
    """Write the page's JSON files from gold and silver. Returns the files written."""
    silver_rows, version = gold.read_silver(root)
    prices = gold.trusted(silver_rows)
    by_month = gold.monthly(prices)
    index_rows = pl.concat([gold.index(by_month, "retail"), gold.index(by_month, "wholesale")])
    items = items_json(by_month)
    files = {
        "index.json": index_json(index_rows),
        "items.json": items,
        "margins.json": margins_json(gold.margins(prices)),
        "movers.json": movers_json(prices),
        "meta.json": {
            "built": datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ"),
            "silver_version": version,
            "first_report": silver_rows["report_date"].min().isoformat(),
            "latest_report": silver_rows["report_date"].max().isoformat(),
            "reports": silver_rows["report_date"].n_unique(),
        },
    }
    usd = usd_json(items["months"], rates)
    if usd:
        files["usd.json"] = usd
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in files.items():
        path = out / name
        path.write_text(json.dumps(content, separators=(",", ":")) + "\n")
        written.append(path)
    if not usd:
        (out / "usd.json").unlink(missing_ok=True)
    return written
