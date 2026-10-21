"""Quality checks a day's rows must pass before they reach silver.

Errors stop the load: a price of zero or a promo above the shelf price means the
parse is wrong, and loading it would put a fake spike in the index. Warnings are
reported but don't block, because a store really can drop part of its range.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

# A day with fewer products than this share of the previous day's count is suspect.
MIN_SHARE_OF_PREVIOUS = 0.7


@dataclass(frozen=True)
class Finding:
    level: str  # "error" or "warning"
    check: str
    detail: str


def run_checks(rows: pl.DataFrame, previous_count: int | None = None) -> list[Finding]:
    findings: list[Finding] = []

    def add(level: str, check: str, count: int, what: str) -> None:
        if count:
            findings.append(Finding(level, check, f"{count} {what}"))

    if rows.is_empty():
        return [Finding("error", "not_empty", "no rows at all")]

    add(
        "error",
        "price_positive",
        rows.filter(pl.col("price").is_null() | (pl.col("price") <= 0)).height,
        "products with a missing or non-positive price",
    )
    add(
        "error",
        "promo_not_above_price",
        rows.filter(pl.col("promo_price") > pl.col("price")).height,
        "products whose promo price is above the shelf price",
    )
    add(
        "error",
        "unique_key",
        rows.height - rows.select("store", "snapshot_date", "product_id").n_unique(),
        "duplicate (store, date, product) rows",
    )
    add(
        "warning",
        "has_name",
        rows.filter(pl.col("name").is_null() | (pl.col("name") == "")).height,
        "products without a name",
    )

    if previous_count and rows.height < previous_count * MIN_SHARE_OF_PREVIOUS:
        findings.append(
            Finding(
                "warning",
                "row_count",
                f"{rows.height} products, down from {previous_count} the day before",
            )
        )
    return findings


def has_errors(findings: list[Finding]) -> bool:
    return any(f.level == "error" for f in findings)
