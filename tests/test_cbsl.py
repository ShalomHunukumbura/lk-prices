from datetime import date

import pytest

from lk_prices.cbsl import SourceError, last_page, parse_listing

PRICERPT = "https://www.cbsl.gov.lk/sites/default/files/cbslweb_documents/statistics/pricerpt"


def test_listing_from_2021(fixture_text):
    reports = parse_listing(fixture_text("cbsl_listing_2021.html"))
    # Twelve reports a page, each linked twice (title and icon) but kept once.
    assert len(reports) == 12
    assert len({r.url for r in reports}) == 12
    # Newest first.
    assert [r.report_date for r in reports] == sorted(
        (r.report_date for r in reports), reverse=True
    )
    oct13 = next(r for r in reports if r.report_date == date(2021, 10, 13))
    assert oct13.url == f"{PRICERPT}/price_report_20211013.pdf"
    assert oct13.file_name == "price_report_20211013.pdf"
    assert oct13.title == "Daily Price Report - 13 October 2021"


def test_listing_from_2016_uses_the_older_file_names(fixture_text):
    reports = parse_listing(fixture_text("cbsl_listing_2016.html"))
    assert reports[-1].report_date == date(2016, 8, 1)
    assert reports[-1].file_name == "price_report_20160801e.pdf"


def test_mixed_file_names_on_one_page(fixture_text):
    # The 2021 page straddles a naming change: some files end in "e.pdf", some don't.
    names = {r.file_name for r in parse_listing(fixture_text("cbsl_listing_2021.html"))}
    assert any(n.endswith("e.pdf") for n in names)
    assert any(n[-12:-4].isdigit() for n in names)


def test_last_page(fixture_text):
    assert last_page(fixture_text("cbsl_listing_2021.html")) == 203


def test_page_without_reports():
    assert parse_listing("<html><body>Something went wrong</body></html>") == []


def test_listing_from_2017_uses_spaced_day_month_year_names(fixture_text):
    reports = parse_listing(fixture_text("cbsl_listing_2017.html"))
    assert len(reports) == 12
    first = reports[-1]
    assert first.report_date == date(2017, 5, 5)
    # The URL keeps CBSL's encoding; the local file name is decoded.
    assert first.url.endswith("Daily%20Price%20Report%20-%2005%2005%202017.pdf")
    assert first.file_name == "Daily Price Report - 05 05 2017.pdf"
    assert first.title == "Daily Price Report 05.05.2017"


def test_a_day_uploaded_twice_keeps_both_files(fixture_text):
    may17 = [
        r.file_name
        for r in parse_listing(fixture_text("cbsl_listing_2017.html"))
        if r.report_date == date(2017, 5, 17)
    ]
    assert sorted(may17) == [
        "Daily Price Report - 17 05 2017.pdf",
        "Daily Price Report - 17 05 2017_0.pdf",
    ]


def test_a_report_link_that_cant_be_read_stops_the_crawl():
    # A new naming scheme must fail loudly, not be skipped.
    html = (
        f'<a href="{PRICERPT}/price_report_20211013.pdf">Daily Price Report - 13 October 2021</a>'
        f'<a href="{PRICERPT}/prices_october.pdf">Prices for October</a>'
    )
    with pytest.raises(SourceError, match="can't tell the date"):
        parse_listing(html)


def test_an_icon_link_without_its_title_link_stops_the_crawl():
    html = f'<a href="{PRICERPT}/price_report_20211013.pdf"><img src="pdf.png"></a>'
    with pytest.raises(SourceError, match="not read"):
        parse_listing(html)
