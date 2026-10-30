"""Quality checks one report's rows must pass before they reach silver.

A report that fails is rejected, not loaded: a misread column would put wrong
prices in ten years of history, and that is much harder to find later than a
report that is missing and listed in `rejects`.
"""

from __future__ import annotations

import polars as pl

from . import items

# A normal report has 115 to 170 prices. The parser already fails if it finds no
# item rows, which is what a misread table looks like; this only catches a table
# read in part. It is low on purpose: during the April 2020 lockdown most markets
# were shut and a day had as few as 10 prices.
MIN_PRICES = 10
KNOWN_UNITS = {"kg", "l", "each", "nut"}
# Any other name means a header row was misread (2 October 2020 once gave "Last").
KNOWN_MARKETS = {"Pettah", "Dambulla", "Narahenpita", "Marandagahamula", "Peliyagoda", "Negombo"}
# Retail below half of wholesale for the same item and market is not a real
# price; it means a number landed in the wrong column.
MIN_RETAIL_TO_WHOLESALE = 0.5
# Up to this many such prices in one report are treated as typing slips and set
# aside; more than this is a misread table.
MAX_TYPOS = 2


def problems(rows: pl.DataFrame) -> list[str]:
    found = []
    if rows.height < MIN_PRICES:
        found.append(f"only {rows.height} prices (expected at least {MIN_PRICES})")
    duplicates = rows.height - rows.select("report_date", "item", "market", "price_type").n_unique()
    if duplicates:
        found.append(f"{duplicates} duplicate (date, item, market, price type) rows")
    units = set(rows["unit"].drop_nulls().unique()) - KNOWN_UNITS
    if units:
        found.append(f"unknown units {sorted(units)}")
    unknown = sorted(set(rows["item"].unique()) - set(items.CATALOGUE))
    if unknown:
        found.append(f"unknown items {unknown}; add them to items.py")
    wrong_unit = rows.filter(
        pl.col("unit").is_not_null()
        & (pl.col("unit") != pl.col("item").map_elements(items.unit, return_dtype=pl.String))
        & pl.col("item").is_in(list(items.CATALOGUE))
    )
    if wrong_unit.height:
        sample = ", ".join(sorted(set(wrong_unit["item"].head(3))))
        found.append(f"{wrong_unit.height} prices in an unexpected unit ({sample})")
    markets = set(rows["market"].unique()) - KNOWN_MARKETS
    if markets:
        found.append(f"unknown markets {sorted(markets)}")
    if rows["unit"].null_count():
        found.append(f"{rows['unit'].null_count()} rows without a unit")
    if rows["report_date"].n_unique() > 1:
        found.append("more than one report date in one report")

    odd = implausible(rows)
    if odd.height > MAX_TYPOS:
        sample = ", ".join(
            f"{r['item']} at {r['market']}" for r in odd.head(3).iter_rows(named=True)
        )
        found.append(f"{odd.height} retail prices below half of wholesale ({sample})")
    return found


def implausible(rows: pl.DataFrame) -> pl.DataFrame:
    """Retail rows priced below half of wholesale for the same item and market.

    One or two of these in a report are CBSL's typing slips (15 May 2020 has retail
    onions at Rs 21.00, the day after Rs 228.00). Many at once means a column was
    misread, and `problems` rejects the report.
    """
    priced = rows.filter(pl.col("price").is_not_null())
    wholesale = priced.filter(pl.col("price_type") == "wholesale").select(
        "item", "market", wholesale_price=pl.col("price").cast(pl.Float64)
    )
    return (
        priced.filter(pl.col("price_type") == "retail")
        .join(wholesale, on=["item", "market"])
        .filter(
            pl.col("price").cast(pl.Float64) < pl.col("wholesale_price") * MIN_RETAIL_TO_WHOLESALE
        )
        .drop("wholesale_price")
    )


def missing_items(prices: pl.DataFrame) -> pl.DataFrame:
    """Items a report left out that the reports either side of it both carry.

    Not a reason to reject the report: CBSL really does leave items out (a market
    closed, nothing to price). It's for spotting a table or page the parser
    skipped, which looks the same from inside one report.
    """
    days = prices.select(pl.col("report_date").unique().sort()).with_columns(
        before=pl.col("report_date").shift(1), after=pl.col("report_date").shift(-1)
    )
    carried = prices.select("report_date", "item").unique()
    expected = (
        days.join(carried.rename({"report_date": "before"}), on="before")
        .join(carried.rename({"report_date": "after"}), on=["after", "item"])
        .select("report_date", "item")
    )
    return (
        expected.join(carried, on=["report_date", "item"], how="anti")
        .group_by("report_date")
        .agg(missing=pl.col("item").sort())
        .sort("report_date")
    )
