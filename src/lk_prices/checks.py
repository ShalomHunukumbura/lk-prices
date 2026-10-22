"""Quality checks one report's rows must pass before they reach silver.

A report that fails is rejected, not loaded: a misread column would put wrong
prices in ten years of history, and that is much harder to find later than a
report that is missing and listed in `rejects`.
"""

from __future__ import annotations

import polars as pl

# A normal report has 155 to 170 prices. Fewer than this means the table was only
# partly read. It is set low on purpose: just after a holiday some markets are
# still closed (17 April 2026 had 57 prices, with Pettah and Narahenpita closed).
MIN_PRICES = 30
KNOWN_UNITS = {"kg", "l", "each", "nut"}
# Retail below half of wholesale for the same item and market is not a real
# price; it means a number landed in the wrong column.
MIN_RETAIL_TO_WHOLESALE = 0.5


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
    if rows["unit"].null_count():
        found.append(f"{rows['unit'].null_count()} rows without a unit")
    if rows["report_date"].n_unique() > 1:
        found.append("more than one report date in one report")

    both = rows.filter(pl.col("price").is_not_null()).pivot(
        on="price_type", index=["item", "market"], values="price", aggregate_function="first"
    )
    if {"wholesale", "retail"} <= set(both.columns):
        odd = both.filter(
            pl.col("retail").cast(pl.Float64)
            < pl.col("wholesale").cast(pl.Float64) * MIN_RETAIL_TO_WHOLESALE
        )
        if odd.height:
            sample = ", ".join(
                f"{r['item']} at {r['market']}" for r in odd.head(3).iter_rows(named=True)
            )
            found.append(f"{odd.height} retail prices below half of wholesale ({sample})")
    return found
