"""The gold layer: a daily food price index, monthly prices, and market margins.

    data/gold/index/      one row per month, price type and basket
    data/gold/monthly/    median price per month, item, market and price type
    data/gold/margins/    median retail markup over wholesale per month, item, market

Gold is rebuilt from silver in full on every run: it takes seconds, and it means
gold always matches silver. It can be built from any past silver version, so a
published index can be reproduced exactly (`lk-prices gold --version N`).

The index
---------
A monthly food price index since August 2016, for retail and wholesale prices, for
the whole basket and for each section.

CBSL's basket changes over the years: Narahenpita retail starts in May 2020, and
imported rice is only priced from December 2021 to December 2025. A fixed basket
priced against 2016 can't cope with that, so the index is chain-linked: each month
is compared with the one before, using the series (item, market, price type) priced
in both, and the monthly changes are multiplied into one series from 100.

Each monthly change is a Jevons index, the geometric mean of price relatives: first
across an item's markets, then across items, so every item counts the same however
many markets price it. A series' monthly price is its median over the month's
reports, which shrugs off a stray typing slip.

Why monthly and not daily: a daily chain was tried first and drifted badly. Over
ten years, retail vegetables came out at 7.7 times their 2016 level against 4.7
times from comparing the prices directly. Vegetables go "n.a." when supply is
short, which is when prices are high, and every time a series drops out and comes
back the movement across the gap is lost; over 2,400 daily links the losses add
up. A month almost always has some price for each series, and the monthly chain
lands within a few points of the direct comparison (vegetables 458 against 468).

Checked against the truth: each series' average price in one year against
another, for series priced in both. From 2021 to 2025 the index is within 10% of
that for every basket. Across 2019 and 2020 fish and fruit are less certain,
because CBSL didn't publish them (see `_relatives`).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
from deltalake import DeltaTable, write_deltalake

from . import flags
from .bronze import DATA_DIR
from .silver import prices_path

BASE = 100.0


def gold_path(name: str, root: Path = DATA_DIR) -> Path:
    return root / "gold" / name


def read_silver(root: Path = DATA_DIR, version: int | None = None) -> tuple[pl.DataFrame, int]:
    """Silver's rows, as of `version` if given, and the version read."""
    path = str(prices_path(root))
    read_version = DeltaTable(path).version() if version is None else version
    return pl.read_delta(path, version=read_version), read_version


def trusted(prices: pl.DataFrame) -> pl.DataFrame:
    """Priced rows that aren't suspect, with the price as a float."""
    return (
        flags.flag(prices)
        .filter(pl.col("price").is_not_null() & ~pl.col("suspect"))
        .select("report_date", "section", *flags.SERIES, value=pl.col("price").cast(pl.Float64))
    )


def monthly(prices: pl.DataFrame) -> pl.DataFrame:
    """Median price per month, item, market and price type."""
    return (
        prices.with_columns(month=pl.col("report_date").dt.truncate("1mo"))
        .group_by("month", "section", *flags.SERIES)
        .agg(price=pl.col("value").median(), days=pl.len().cast(pl.UInt32))
        .sort("month", "item", "market", "price_type")
    )


def _relatives(of_type: pl.DataFrame) -> pl.DataFrame:
    """Each series' log price relative against its previous month, and whether
    that relative counts for the headline basket, its section, or both.

    A series priced in the month just before counts for both. A series coming back
    after a gap counts only for its section, and only if the whole section was
    missing in between: CBSL dropped the fish and fruit tables from early 2019 to
    July 2020. Over that gap the section had no movement at all, so comparing with
    the last price before it counts the move once. The headline kept moving with the
    other items, so for it the returning series is linked in without a relative;
    counting its gap move again would count it twice.
    """
    months = of_type.select(pl.col("month").unique().sort())
    # How many months each section has had prices in, up to and including each month.
    present = of_type.select("section", "month").unique().with_columns(here=pl.lit(1))
    seen = (
        months.join(present.select("section").unique(), how="cross")
        .join(present, on=["section", "month"], how="left")
        .sort("section", "month")
        .with_columns(seen=pl.col("here").fill_null(0).cum_sum().over("section"))
        .select("section", "month", "seen")
    )
    return (
        of_type.sort(*flags.SERIES, "month")
        .with_columns(
            previous=pl.col("price").shift(1).over(flags.SERIES),
            previous_month=pl.col("month").shift(1).over(flags.SERIES),
        )
        .filter(pl.col("previous").is_not_null())
        .join(seen, on=["section", "month"])
        .join(
            seen.rename({"month": "previous_month", "seen": "seen_before"}),
            on=["section", "previous_month"],
        )
        .with_columns(
            log_relative=(pl.col("price") / pl.col("previous")).log(),
            headline=pl.col("previous_month") == pl.col("month").dt.offset_by("-1mo"),
            # No month of the section between the two prices.
            section_ok=pl.col("seen") - pl.col("seen_before") == 1,
        )
    )


def index(monthly_prices: pl.DataFrame, price_type: str) -> pl.DataFrame:
    """The chained monthly Jevons index for one price type, for every basket.

    Baskets are "all" and each section ("vegetables", "rice", ...). `items` is how
    many items the month's change was measured on. A section's index is null in
    months it had no prices (fish and fruit, 2019 to 2020).
    """
    of_type = monthly_prices.filter(pl.col("price_type") == price_type)
    relatives = _relatives(of_type)

    def by_item(rows: pl.DataFrame) -> pl.DataFrame:
        # Stage 1: one relative per item, the geometric mean over its markets.
        return rows.group_by("month", "section", "item").agg(
            log_relative=pl.col("log_relative").mean()
        )

    # Stage 2: equal weight per item, for the whole basket and for each section.
    baskets = pl.concat(
        [
            by_item(relatives.filter("headline")).with_columns(basket=pl.lit("all")),
            by_item(relatives.filter("section_ok")).with_columns(basket=pl.col("section")),
        ]
    )
    changes = baskets.group_by("month", "basket").agg(
        log_change=pl.col("log_relative").mean(), items=pl.len().cast(pl.UInt32)
    )
    priced = pl.concat(
        [
            of_type.select("month", basket=pl.lit("all")),
            of_type.select("month", basket=pl.col("section")),
        ]
    ).unique()
    # Every month for every basket, from 100 in the first month.
    months = of_type.select(pl.col("month").unique().sort())
    full = (
        months.join(priced.select("basket").unique(), how="cross")
        .join(changes, on=["month", "basket"], how="left")
        .join(priced.with_columns(priced=pl.lit(True)), on=["month", "basket"], how="left")
        .with_columns(pl.col("log_change").fill_null(0.0), pl.col("items").fill_null(0))
        .sort("basket", "month")
    )
    return full.with_columns(
        index=pl.when(pl.col("priced"))
        .then(BASE * pl.col("log_change").cum_sum().over("basket").exp())
        .otherwise(None),
        price_type=pl.lit(price_type),
    ).select("month", "price_type", "basket", "index", "items")


def margins(prices: pl.DataFrame) -> pl.DataFrame:
    """Median retail markup over wholesale per month, item and market.

    Only days with both prices for the same item in the same market count, so a
    market that only reports retail (Narahenpita) has no margin.
    """
    wholesale = prices.filter(pl.col("price_type") == "wholesale").select(
        "report_date", "item", "market", wholesale="value"
    )
    retail = prices.filter(pl.col("price_type") == "retail").select(
        "report_date", "section", "item", "market", retail="value"
    )
    return (
        retail.join(wholesale, on=["report_date", "item", "market"])
        .with_columns(
            month=pl.col("report_date").dt.truncate("1mo"),
            markup=pl.col("retail") / pl.col("wholesale") - 1,
        )
        .group_by("month", "section", "item", "market")
        .agg(markup=pl.col("markup").median(), days=pl.len().cast(pl.UInt32))
        .sort("month", "item", "market")
    )


def build(root: Path = DATA_DIR, version: int | None = None) -> dict[str, pl.DataFrame]:
    """Compute every gold table from silver (as of `version`) and write them."""
    silver, read_version = read_silver(root, version)
    prices = trusted(silver)
    by_month = monthly(prices)
    tables = {
        "index": pl.concat([index(by_month, "retail"), index(by_month, "wholesale")]),
        "monthly": by_month,
        "margins": margins(prices),
    }
    for name, table in tables.items():
        table = table.with_columns(silver_version=pl.lit(read_version, dtype=pl.Int64))
        tables[name] = table
        path = gold_path(name, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_deltalake(str(path), table, mode="overwrite", schema_mode="overwrite")
    return tables


def validate(
    by_month: pl.DataFrame, index_rows: pl.DataFrame, start: int, end: int
) -> pl.DataFrame:
    """How far the index's change from year `start` to `end` is from the truth.

    The truth is each series' average (log) price in `end` against `start`, for
    series priced in both years, combined the same way: markets, then items. It
    leaves out series that came or went in between, which the chained index
    counts, so a gap is not all error; a large one is worth a look.
    """
    series = [*flags.SERIES, "section"]

    def year_mean(year: int, name: str) -> pl.DataFrame:
        return (
            by_month.filter(pl.col("month").dt.year() == year)
            .group_by(series)
            .agg(pl.col("price").log().mean().alias(name))
        )

    items = (
        year_mean(start, "a")
        .join(year_mean(end, "b"), on=series)
        .group_by("section", "item", "price_type")
        .agg(log_change=(pl.col("b") - pl.col("a")).mean())
    )
    truth = (
        pl.concat(
            [items.with_columns(basket=pl.lit("all")), items.with_columns(basket=pl.col("section"))]
        )
        .group_by("basket", "price_type")
        .agg(truth=pl.col("log_change").mean().exp())
    )

    def index_mean(year: int, name: str) -> pl.DataFrame:
        return (
            index_rows.filter(pl.col("month").dt.year() == year)
            .group_by("basket", "price_type")
            .agg(pl.col("index").log().mean().alias(name))
        )

    measured = (
        index_mean(start, "a")
        .join(index_mean(end, "b"), on=["basket", "price_type"])
        .select("basket", "price_type", index=(pl.col("b") - pl.col("a")).exp())
    )
    return (
        truth.join(measured, on=["basket", "price_type"])
        .with_columns(off_pct=(pl.col("index") / pl.col("truth") - 1) * 100)
        .sort("basket", "price_type")
    )
