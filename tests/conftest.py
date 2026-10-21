from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from lk_prices import glomark
from lk_prices.bronze import BronzeFile

FIXTURES = Path(__file__).parent / "fixtures"
DAY = date(2026, 10, 21)


@pytest.fixture
def fixture_text():
    def load(name: str) -> str:
        return (FIXTURES / name).read_text()

    return load


@pytest.fixture
def products(fixture_text):
    """The three products in the saved Glomark bread page, as served (scrubbed)."""
    return glomark.extract_products(fixture_text("glomark_category.html"))


@pytest.fixture
def bronze_file(products):
    def make(day: date = DAY, items: list[dict] | None = None, category_id: int = 118):
        return BronzeFile(
            store="glomark",
            snapshot_date=day,
            category_id=category_id,
            url=f"https://glomark.lk/bakery/bread/c/{category_id}",
            fetched_at=datetime(day.year, day.month, day.day, 1, 30, tzinfo=UTC),
            products=products if items is None else items,
        )

    return make
