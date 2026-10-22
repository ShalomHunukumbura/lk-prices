from collections import Counter
from datetime import date
from decimal import Decimal

import pytest
from conftest import FIXTURES

from lk_prices.parse import NotAReport, ParseError, parse_pdf


@pytest.fixture(scope="module")
def prices():
    layout, prices = parse_pdf((FIXTURES / "price_report_20211013.pdf").read_bytes())
    assert layout == "summary"
    return prices


def find(prices, item, market, price_type):
    matches = [p for p in prices if (p.item, p.market, p.price_type) == (item, market, price_type)]
    assert len(matches) == 1, (item, market, price_type, matches)
    return matches[0]


def test_report_date(prices):
    assert {p.report_date for p in prices} == {date(2021, 10, 13)}


def test_every_market_and_price_type(prices):
    beans = {
        (p.market, p.price_type): (p.previous_price, p.price) for p in prices if p.item == "Beans"
    }
    assert beans == {
        ("Pettah", "wholesale"): (Decimal("280.00"), Decimal("200.00")),
        ("Dambulla", "wholesale"): (Decimal("245.00"), Decimal("205.00")),
        ("Pettah", "retail"): (Decimal("320.00"), Decimal("250.00")),
        ("Dambulla", "retail"): (Decimal("265.00"), Decimal("225.00")),
        ("Narahenpita", "retail"): (Decimal("360.00"), Decimal("320.00")),
    }


def test_empty_cells_dont_shift_prices(prices):
    # Coconut oil has no Dambulla prices; the Narahenpita price must not slide left.
    markets = {(p.market, p.price_type) for p in prices if p.item == "Coconut oil"}
    assert markets == {("Pettah", "wholesale"), ("Pettah", "retail"), ("Narahenpita", "retail")}
    oil = find(prices, "Coconut oil", "Narahenpita", "retail")
    assert (oil.previous_price, oil.price) == (Decimal("570.00"), Decimal("600.00"))
    assert oil.unit == "l"


def test_sections(prices):
    sections = Counter(p.section for p in prices)
    assert set(sections) == {"vegetables", "other", "fruits", "rice", "fish"}


def test_sub_headers_rename_only_their_columns(prices):
    # Under RICE, "Marandagahamula" sits over the second wholesale market.
    rice = {(p.market, p.price_type) for p in prices if p.section == "rice"}
    assert ("Marandagahamula", "wholesale") in rice
    assert ("Dambulla", "wholesale") not in rice
    assert ("Pettah", "retail") in rice
    # Under FISH, Peliyagoda and Negombo replace the wholesale markets.
    fish = {(p.market, p.price_type) for p in prices if p.section == "fish"}
    assert ("Peliyagoda", "wholesale") in fish
    assert ("Negombo", "wholesale") in fish
    assert ("Pettah", "wholesale") not in fish


def test_thousands_are_read_whole(prices):
    # The PDF splits "1,400.00" into "1" and ",400.00".
    big = [p.price for p in prices if p.price and p.price >= 1000]
    assert big
    assert all(p.price is None or p.price > 10 for p in prices)


def test_not_a_report():
    with pytest.raises(ParseError):
        parse_pdf((FIXTURES / "blank.pdf").read_bytes())


def test_an_empty_file_is_a_parse_error():
    with pytest.raises(ParseError, match="not a readable PDF"):
        parse_pdf(b"")


def test_after_a_weekend_the_previous_day_is_last_friday():
    # Monday reports compare with "Last Friday", printed on a row of its own.
    layout, prices = parse_pdf((FIXTURES / "price_report_20261019_e.pdf").read_bytes())
    assert layout == "summary"
    assert {p.report_date for p in prices} == {date(2026, 10, 19)}
    beans = {
        (p.market, p.price_type): (p.previous_price, p.price) for p in prices if p.item == "Beans"
    }
    assert beans == {
        ("Pettah", "wholesale"): (Decimal("550.00"), Decimal("550.00")),
        ("Dambulla", "wholesale"): (Decimal("335.00"), Decimal("400.00")),
        ("Pettah", "retail"): (Decimal("600.00"), Decimal("600.00")),
        ("Dambulla", "retail"): (Decimal("365.00"), Decimal("430.00")),
        ("Narahenpita", "retail"): (Decimal("700.00"), Decimal("700.00")),
    }
    kelawalla = find(prices, "Kelawalla", "Narahenpita", "retail")
    assert (kelawalla.previous_price, kelawalla.price) == (Decimal("2660.00"), Decimal("2380.00"))


def test_a_bare_weekday_header_with_its_date_underneath():
    # 5 February 2026 compares with "Tuesday", dated 03.02.2026 on a row below.
    _, prices = parse_pdf((FIXTURES / "price_report_20260205_e.pdf").read_bytes())
    assert {p.report_date for p in prices} == {date(2026, 2, 5)}
    beans = find(prices, "Beans", "Dambulla", "retail")
    assert (beans.previous_price, beans.price) == (Decimal("230.00"), Decimal("455.00"))
    samba = [p for p in prices if p.item == "Samba" and p.market == "Narahenpita"]
    assert samba == []  # "n.a." on both days


def test_a_date_with_a_comma():
    _, prices = parse_pdf((FIXTURES / "price_report_20260625_e.pdf").read_bytes())
    assert {p.report_date for p in prices} == {date(2026, 6, 25)}
    carrot = find(prices, "Carrot", "Narahenpita", "retail")
    assert (carrot.previous_price, carrot.price) == (Decimal("460.00"), Decimal("480.00"))


def test_a_non_publication_notice_is_not_a_report():
    with pytest.raises(NotAReport, match="non-publication"):
        parse_pdf((FIXTURES / "price_report_20260415_e.pdf").read_bytes())


def test_an_image_only_pdf_is_not_a_report():
    # Printed to PDF as pictures: there is no text to read.
    with pytest.raises(NotAReport, match="image-only"):
        parse_pdf((FIXTURES / "price_report_20250924_e.pdf").read_bytes())


def test_a_partial_day_after_new_year():
    # Pettah and Narahenpita were closed; their names carry a footnote mark.
    _, prices = parse_pdf((FIXTURES / "price_report_20260417_e.pdf").read_bytes())
    assert {p.market for p in prices} <= {
        "Pettah",
        "Dambulla",
        "Narahenpita",
        "Marandagahamula",
        "Peliyagoda",
        "Negombo",
    }
    beans = find(prices, "Beans", "Dambulla", "retail")
    assert (beans.previous_price, beans.price) == (Decimal("305.00"), Decimal("165.00"))
    assert not [p for p in prices if p.item == "Beans" and p.market == "Pettah"]
