import math
from datetime import date

import polars as pl
import pytest
from deltalake import write_deltalake
from silver_rows import SECTIONS, D, rows

from lk_prices import gold, silver


def monthly(*series):
    """Monthly prices from (item, market, price_type, [prices by month]) tuples."""
    records = []
    for item, market, price_type, prices in series:
        for k, price in enumerate(prices):
            if price is not None:
                records.append(
                    {
                        "month": date(2020 + k // 12, k % 12 + 1, 1),
                        "section": SECTIONS.get(item, "vegetables"),
                        "item": item,
                        "market": market,
                        "price_type": price_type,
                        "price": float(price),
                    }
                )
    return pl.DataFrame(records)


def level(index_rows, basket, month_number):
    return index_rows.filter(
        (pl.col("basket") == basket) & (pl.col("month") == date(2020, month_number, 1))
    ).row(0, named=True)["index"]


def test_index_follows_one_series():
    ix = gold.index(monthly(("Beans", "Pettah", "retail", [100, 110, 121])), "retail")
    assert [round(level(ix, "all", m), 6) for m in (1, 2, 3)] == [100.0, 110.0, 121.0]


def test_each_item_counts_the_same_however_many_markets_price_it():
    ix = gold.index(
        monthly(
            ("Beans", "Pettah", "retail", [100, 110]),
            ("Beans", "Dambulla", "retail", [100, 110]),
            ("Beans", "Narahenpita", "retail", [100, 110]),
            ("Carrot", "Pettah", "retail", [100, 90]),
        ),
        "retail",
    )
    assert level(ix, "all", 2) == pytest.approx(100 * math.sqrt(1.1 * 0.9))


def test_a_series_that_starts_later_is_linked_in_without_a_jump():
    ix = gold.index(
        monthly(
            ("Beans", "Pettah", "retail", [100, 100, 100]),
            ("Carrot", "Pettah", "retail", [None, 500, 500]),  # new, and dear
        ),
        "retail",
    )
    assert level(ix, "all", 2) == pytest.approx(100)
    assert level(ix, "all", 3) == pytest.approx(100)


def test_a_section_missing_for_months_is_bridged_for_itself_only():
    # Fish isn't published in months 2 and 3, and comes back at double the price.
    ix = gold.index(
        monthly(
            ("Beans", "Pettah", "retail", [100, 100, 100, 100]),
            ("Paraw", "Negombo", "retail", [500, None, None, 1000]),
        ),
        "retail",
    )
    assert level(ix, "fish", 2) is None
    assert level(ix, "fish", 4) == pytest.approx(200)
    # The headline didn't move while fish was away, and doesn't count the gap move.
    assert level(ix, "all", 4) == pytest.approx(100)


def test_a_series_gap_within_a_section_isnt_bridged():
    # Beans is missing in month 2, but carrot kept vegetables going.
    ix = gold.index(
        monthly(
            ("Beans", "Pettah", "retail", [100, None, 300]),
            ("Carrot", "Pettah", "retail", [100, 100, 100]),
        ),
        "retail",
    )
    assert level(ix, "vegetables", 3) == pytest.approx(100)


def test_margins_compare_retail_with_wholesale_in_the_same_market():
    prices = gold.trusted(
        rows(
            (D[0], "Beans", "Pettah", "wholesale", 100),
            (D[0], "Beans", "Pettah", "retail", 120),
            (D[0], "Beans", "Narahenpita", "retail", 150),  # no wholesale there
        )
    )
    margins = gold.margins(prices)
    assert margins.select("market", "markup").rows() == [("Pettah", pytest.approx(0.2))]


def test_gold_can_be_rebuilt_from_an_earlier_silver_version(tmp_path):
    path = str(silver.prices_path(tmp_path))
    first = rows((D[0], "Beans", "Pettah", "retail", 100), (D[1], "Beans", "Pettah", "retail", 100))
    write_deltalake(path, first)
    later = rows((date(2020, 2, 3), "Beans", "Pettah", "retail", 150))
    write_deltalake(path, later, mode="append")

    now = gold.build(tmp_path)["index"]
    assert set(now["silver_version"]) == {1}
    assert now["index"].max() == pytest.approx(150)
    then = gold.build(tmp_path, version=0)["index"]
    assert set(then["silver_version"]) == {0}
    assert then["index"].max() == pytest.approx(100)
