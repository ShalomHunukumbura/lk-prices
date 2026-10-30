"""Silver-like rows for tests of what's built on top of silver."""

from datetime import date, timedelta
from decimal import Decimal

import polars as pl

from lk_prices import silver

SECTIONS = {"Paraw": "fish", "Samba": "rice"}
D = [date(2020, 1, 6) + timedelta(days=i) for i in range(5)]


def rows(*prices, compared_with="Yesterday"):
    """Silver-like rows from (date, item, market, price_type, price[, previous]) tuples."""
    records = []
    for p in prices:
        day, item, market, price_type, price, *previous = p
        records.append(
            {
                "report_date": day,
                "section": SECTIONS.get(item, "vegetables"),
                "item": item,
                "market": market,
                "price_type": price_type,
                "price": None if price is None else Decimal(str(price)),
                "previous_price": Decimal(str(previous[0])) if previous else None,
                "compared_with": compared_with,
            }
        )
    return pl.DataFrame(
        records,
        schema={k: silver.SCHEMA[k] for k in records[0]},
    )
