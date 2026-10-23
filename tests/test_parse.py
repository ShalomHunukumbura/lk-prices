from collections import Counter
from datetime import date
from decimal import Decimal

import pytest
from conftest import FIXTURES

from lk_prices.parse import NotAReport, ParseError, _is_day_word, parse_pdf


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


def test_a_misspelt_previous_day_and_item_unit_on_the_market_row():
    # 18 July 2022 says "Last Friay", and "Item Unit" shares the row with the markets.
    _, prices = parse_pdf((FIXTURES / "price_report_20220718.pdf").read_bytes())
    assert {p.report_date for p in prices} == {date(2022, 7, 18)}
    beans = {
        (p.market, p.price_type): (p.previous_price, p.price) for p in prices if p.item == "Beans"
    }
    assert beans[("Narahenpita", "retail")] == (Decimal("420.00"), Decimal("480.00"))
    assert beans[("Dambulla", "wholesale")] == (Decimal("345.00"), Decimal("345.00"))
    assert {p.market for p in prices} <= {
        "Pettah",
        "Dambulla",
        "Narahenpita",
        "Marandagahamula",
        "Peliyagoda",
        "Negombo",
    }


def test_a_summary_page_without_the_table_is_not_a_report():
    with pytest.raises(NotAReport, match="summary page only"):
        parse_pdf((FIXTURES / "price_report_20220316.pdf").read_bytes())


def test_a_wider_page_moves_the_label_edge():
    # Some 2021 reports are about 1,000 points wide, so units sit further right.
    _, prices = parse_pdf((FIXTURES / "price_report_20210819e.pdf").read_bytes())
    assert {p.report_date for p in prices} == {date(2021, 8, 19)}
    carrot = {
        (p.market, p.price_type): (p.previous_price, p.price) for p in prices if p.item == "Carrot"
    }
    assert carrot[("Dambulla", "wholesale")] == (Decimal("78.00"), Decimal("115.00"))
    assert carrot[("Narahenpita", "retail")] == (Decimal("160.00"), Decimal("160.00"))
    assert len(prices) > 100


@pytest.mark.parametrize(
    ("word", "is_day"),
    [
        ("Yesterday", True),
        ("Yesteday", True),
        ("Friay", True),
        ("Tuesday", True),
        ("Today", False),
        ("Pettah", False),
        ("Dambulla", False),
        ("Narahenpita", False),
        ("Negombo", False),
        ("Peliyagoda", False),
        ("Marandagahamula", False),
    ],
)
def test_day_words_allow_typos_but_not_market_names(word, is_day):
    assert _is_day_word(word) is is_day


def test_yesteday():
    # 2 February 2021 spells it "Yesteday".
    _, prices = parse_pdf((FIXTURES / "price_report_20210202e.pdf").read_bytes())
    assert {p.report_date for p in prices} == {date(2021, 2, 2)}
    carrot = find(prices, "Carrot", "Dambulla", "retail")
    assert (carrot.previous_price, carrot.price) == (None, Decimal("195.00"))


def test_last_and_the_weekday_on_separate_rows():
    # 2 October 2020: "Last" on one row, "Wednesday Today" on the next, the date below.
    _, prices = parse_pdf((FIXTURES / "price_report_20201002e.pdf").read_bytes())
    assert {p.market for p in prices} <= {
        "Pettah",
        "Dambulla",
        "Narahenpita",
        "Marandagahamula",
        "Peliyagoda",
        "Negombo",
    }
    beans = find(prices, "Beans", "Narahenpita", "retail")
    assert (beans.previous_price, beans.price) == (Decimal("240.00"), Decimal("220.00"))


@pytest.fixture(scope="module")
def sectioned():
    layout, prices = parse_pdf((FIXTURES / "price_report_20200727e.pdf").read_bytes())
    assert layout == "sectioned"
    return prices


def test_sectioned_layout_reads_every_table_page(sectioned):
    # Before mid-2020: a chart page, then vegetables, rice and "other" on their own pages.
    assert {p.report_date for p in sectioned} == {date(2020, 7, 27)}  # page 4 says "J uly"
    assert {p.section for p in sectioned} == {"vegetables", "rice", "other"}
    beans = find(sectioned, "Beans", "Pettah", "wholesale")
    assert (beans.previous_price, beans.price) == (Decimal("145.00"), Decimal("75.00"))


def test_sectioned_rice_has_its_own_markets(sectioned):
    rice = {(p.market, p.price_type) for p in sectioned if p.item == "Samba"}
    assert rice == {
        ("Marandagahamula", "wholesale"),
        ("Pettah", "wholesale"),
        ("Dambulla", "retail"),
        ("Pettah", "retail"),
        ("Narahenpita", "retail"),
    }


def test_units_written_into_item_names(sectioned):
    units = {p.item: p.unit for p in sectioned}
    assert units["Coconut"] == "nut"
    assert units["Coconut oil"] == "l"
    assert units["Egg"] == "each"
    assert units["Beans"] == "kg"  # from the row of units over the columns
    egg = find(sectioned, "Egg", "Narahenpita", "retail")
    assert egg.price == Decimal("20.00")


def test_charts_under_a_table_are_not_read(sectioned):
    # Axis numbers like "250" sit under each table; none may become an item.
    assert all(any(c.isalpha() for c in p.item) for p in sectioned)
    assert len(sectioned) == 115


def test_an_excel_error_in_place_of_today():
    # 8 June 2020's "Other" page prints "#REF!" where the last "Today" should be.
    layout, prices = parse_pdf((FIXTURES / "price_report_20200608_e.pdf").read_bytes())
    assert layout == "sectioned"
    assert {p.report_date for p in prices} == {date(2020, 6, 8)}
    assert "other" in {p.section for p in prices}
    beans = find(prices, "Beans", "Narahenpita", "retail")
    assert (beans.previous_price, beans.price) == (Decimal("240.00"), Decimal("200.00"))


def test_a_lockdown_day_with_whole_tables_of_na():
    # 20 April 2020: only Dambulla wholesale had prices; the rice table was all n.a.
    _, prices = parse_pdf((FIXTURES / "price_report_20200420_e.pdf").read_bytes())
    assert prices
    assert {p.report_date for p in prices} == {date(2020, 4, 20)}
    assert "rice" not in {p.section for p in prices}
    beans = find(prices, "Beans", "Dambulla", "wholesale")
    assert (beans.previous_price, beans.price) == (None, Decimal("90.00"))


def test_the_comparison_is_kept_with_each_price(prices):
    assert {p.compared_with for p in prices} == {"Yesterday"}


def test_a_date_beside_the_label_on_the_today_row():
    # 17 March 2020: "Friday 13.03.2020 Today" all on one row.
    _, prices = parse_pdf((FIXTURES / "price_report_20200317e.pdf").read_bytes())
    assert {p.report_date for p in prices} == {date(2020, 3, 17)}
    beans = find(prices, "Beans", "Pettah", "wholesale")
    assert (beans.previous_price, beans.price) == (Decimal("115.00"), Decimal("80.00"))
    assert {p.compared_with for p in prices} == {"Friday"}


def test_2019_compares_with_the_previous_week():
    _, prices = parse_pdf((FIXTURES / "price_report_20190701e.pdf").read_bytes())
    assert {p.report_date for p in prices} == {date(2019, 7, 1)}
    assert {p.compared_with for p in prices} == {"Previous Week"}
    beans = find(prices, "Beans", "Dambulla", "retail")
    assert (beans.previous_price, beans.price) == (Decimal("102.00"), Decimal("105.00"))
    # Four markets then: no Narahenpita.
    assert "Narahenpita" not in {p.market for p in prices}


def test_last_weekday_and_today_on_one_row():
    # 30 July 2026: "Last Tuesday Today" five times on a single row.
    _, prices = parse_pdf((FIXTURES / "price_report_20260730_e.pdf").read_bytes())
    assert {p.compared_with for p in prices} == {"Last Tuesday"}
    beans = find(prices, "Beans", "Narahenpita", "retail")
    assert (beans.previous_price, beans.price) == (Decimal("480.00"), Decimal("460.00"))


def test_dates_in_place_of_day_names():
    # 19 February 2020 labels the columns "18.02.2020" and "19.02.2020" only.
    _, prices = parse_pdf((FIXTURES / "price_report_20200219e.pdf").read_bytes())
    assert {p.report_date for p in prices} == {date(2020, 2, 19)}
    assert {p.compared_with for p in prices} == {"18.02.2020"}
    beans = find(prices, "Beans", "Dambulla", "wholesale")
    assert (beans.previous_price, beans.price) == (Decimal("155.00"), Decimal("165.00"))


def test_different_comparisons_on_one_page():
    # 24 March 2020's "Other" page: Pettah against "Friday", Dambulla against "Yesterday".
    _, prices = parse_pdf((FIXTURES / "price_report_20200324_e.pdf").read_bytes())
    other = {(p.market, p.compared_with) for p in prices if p.section == "other"}
    assert ("Pettah", "Friday") in other
    assert ("Dambulla", "Yesterday") in other


def test_three_columns_per_market_keeps_the_nearest_comparison():
    # 2 May 2019 has Previous Week | 2019.04.30 | Today for each market. The column
    # next to Today is the comparison; the week-ago column is read but not kept.
    _, prices = parse_pdf((FIXTURES / "price_report_20190502e.pdf").read_bytes())
    assert {p.report_date for p in prices} == {date(2019, 5, 2)}
    assert {p.compared_with for p in prices} == {"2019.04.30"}
    beans = find(prices, "Beans", "Pettah", "wholesale")
    assert (beans.previous_price, beans.price) == (Decimal("260.00"), Decimal("210.00"))
    dambulla = find(prices, "Beans", "Dambulla", "wholesale")
    assert (dambulla.previous_price, dambulla.price) == (Decimal("243.00"), Decimal("245.00"))


def test_previous_week_yesterday_today_and_an_iso_date():
    # 30 April 2019: "2019 - 04 - 30" in the title, and three columns per market.
    _, prices = parse_pdf((FIXTURES / "price_report_20190430e.pdf").read_bytes())
    assert {p.report_date for p in prices} == {date(2019, 4, 30)}
    assert {p.compared_with for p in prices} == {"Yesterday"}
    beans = find(prices, "Beans", "Pettah", "wholesale")
    assert (beans.previous_price, beans.price) == (Decimal("260.00"), Decimal("240.00"))


def test_a_garbled_title_is_outvoted_by_the_other_pages():
    # 16 May 2019's last page reads "Commodities1 -6 M a y 2019".
    _, prices = parse_pdf((FIXTURES / "price_report_20190516e.pdf").read_bytes())
    assert {p.report_date for p in prices} == {date(2019, 5, 16)}


def test_2018_compares_with_a_five_day_average():
    # 12 April 2018: six pages, one table per section, "2018/04/12" in the title,
    # and the comparison is "Previous 5 Days Average" stacked over three rows.
    layout, prices = parse_pdf((FIXTURES / "price_report_20180412e.pdf").read_bytes())
    assert layout == "sectioned"
    assert {p.report_date for p in prices} == {date(2018, 4, 12)}
    assert {p.compared_with for p in prices} == {"Previous 5 Days Average"}
    assert {p.section for p in prices} == {"vegetables", "rice", "fish", "fruits", "other"}
    beans = find(prices, "Beans", "Pettah", "retail")
    assert (beans.previous_price, beans.price) == (Decimal("79.00"), Decimal("144.00"))
    samba = {(p.market, p.price_type) for p in prices if p.item == "Samba"}
    assert samba == {
        ("Marandagahamula", "wholesale"),
        ("Pettah", "wholesale"),
        ("Pettah", "retail"),
    }


def test_a_market_with_stale_prices_is_set_aside():
    # 1 May 2018: Pettah is "Previous Week | Today", but Dambulla is "April - W3 |
    # 27-Apr": its latest prices are days old, so they aren't filed under 1 May.
    _, prices = parse_pdf((FIXTURES / "price_report_20180501e.pdf").read_bytes())
    assert {p.report_date for p in prices} == {date(2018, 5, 1)}
    assert "Dambulla" not in {p.market for p in prices}
    beans = {
        (p.market, p.price_type): (p.previous_price, p.price) for p in prices if p.item == "Beans"
    }
    assert beans == {
        ("Pettah", "wholesale"): (Decimal("127.00"), Decimal("147.00")),
        ("Pettah", "retail"): (Decimal("163.00"), Decimal("173.00")),
    }


def test_a_footnote_mark_after_today():
    # 16 July 2018's "Other" page prints "Today *" for Dambulla.
    _, prices = parse_pdf((FIXTURES / "price_report_20180716e.pdf").read_bytes())
    onion = find(prices, "Red-Onions (local)", "Dambulla", "wholesale")  # as printed
    assert (onion.previous_price, onion.price) == (Decimal("115.00"), Decimal("98.00"))


def test_a_page_whose_title_cant_be_read_takes_the_others_date():
    # 11 January 2019's first table title extracts as "Commo2d0i1t9ie-s0 1- - 11".
    _, prices = parse_pdf((FIXTURES / "price_report_20190111e.pdf").read_bytes())
    assert {p.report_date for p in prices} == {date(2019, 1, 11)}
    assert "vegetables" in {p.section for p in prices}


def test_a_trailing_chart_page_is_skipped():
    _, prices = parse_pdf((FIXTURES / "Price_Report_ 18012019e.pdf.pdf").read_bytes())
    assert {p.report_date for p in prices} == {date(2019, 1, 18)}
