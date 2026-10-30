"""Flags on prices that silver keeps as printed but the index shouldn't trust.

Silver holds CBSL's numbers exactly as printed, typing slips included: changing
them there would hide what the source said. Instead, each price is compared with
its neighbours in the same series (item, market and price type), which needs the
whole table, not one report, so it happens here and not in checks.py.

- jump: the price is 3x (or a third of) the previous one in its series.
- spike: a jump that goes straight back on the next report, while the prices
  either side agree. Coconut oil at Rs 570, 85, 570 is a dropped digit, not a
  market that crashed for a day.
- restated: the next report's own "Yesterday" column gives a different number.
  CBSL itself remembers the price differently: chilli was printed at Rs 25 on
  14 February 2017, and the next day's report says yesterday was 250.

`suspect` is what the index leaves out: a spike, or a jump CBSL restated.
"""

from __future__ import annotations

import polars as pl

SERIES = ("item", "market", "price_type")
# A change this large in one report is rare (24 in ten years at 3x).
JUMP = 3.0
# For a spike, the prices either side must be within this of each other.
SETTLED = 1.5


def _ratio_at_least(a: pl.Expr, b: pl.Expr, limit: float) -> pl.Expr:
    """True when a and b differ by `limit` times or more, either way."""
    return pl.max_horizontal(a / b, b / a) >= limit


def flag(prices: pl.DataFrame) -> pl.DataFrame:
    """Silver's rows with jump, spike, restated and suspect columns added.

    `checked` says whether the restated test could be run at all: the next report
    has to be the very next one and compare with the previous day.
    """
    days = prices.select(pl.col("report_date").unique().sort())
    next_report = days.with_columns(next_report=pl.col("report_date").shift(-1))
    rows = (
        prices.join(next_report, on="report_date", how="left")
        .with_columns(value=pl.col("price").cast(pl.Float64))
        .sort(*SERIES, "report_date")
    )
    # The next row of the series, priced or not: its comparison column can still
    # say what yesterday's price was.
    following = rows.with_columns(
        next_date=pl.col("report_date").shift(-1).over(SERIES),
        next_said=pl.col("previous_price").shift(-1).over(SERIES).cast(pl.Float64),
        next_compares=pl.col("compared_with").shift(-1).over(SERIES),
    )
    # Neighbours among priced rows only: a blank day isn't a price to compare with.
    priced = (
        following.filter(pl.col("value").is_not_null())
        .with_columns(
            prev_value=pl.col("value").shift(1).over(SERIES),
            next_value=pl.col("value").shift(-1).over(SERIES),
        )
        .select(*SERIES, "report_date", "prev_value", "next_value")
    )
    flagged = following.join(priced, on=[*SERIES, "report_date"], how="left").with_columns(
        jump=_ratio_at_least(pl.col("value"), pl.col("prev_value"), JUMP).fill_null(False),
        spike=(
            _ratio_at_least(pl.col("value"), pl.col("prev_value"), JUMP)
            & _ratio_at_least(pl.col("value"), pl.col("next_value"), JUMP)
            # Both neighbours on the same side of this price.
            & ((pl.col("value") > pl.col("prev_value")) == (pl.col("value") > pl.col("next_value")))
            & ~_ratio_at_least(pl.col("prev_value"), pl.col("next_value"), SETTLED)
        ).fill_null(False),
        # Only when the next row is the very next report and compares with the
        # previous day ("Yesterday", "Last Friday"), not a week or an average.
        checked=(
            (pl.col("next_date") == pl.col("next_report"))
            & ~pl.col("next_compares").str.contains("Week|Average")
            & pl.col("value").is_not_null()
            & pl.col("next_said").is_not_null()
        ).fill_null(False),
    )
    flagged = flagged.with_columns(
        restated=pl.col("checked") & ((pl.col("value") - pl.col("next_said")).abs() >= 0.005)
    )
    return flagged.with_columns(
        suspect=pl.col("spike") | (pl.col("jump") & pl.col("restated"))
    ).drop(
        "value",
        "next_report",
        "next_date",
        "next_said",
        "next_compares",
        "prev_value",
        "next_value",
    )
