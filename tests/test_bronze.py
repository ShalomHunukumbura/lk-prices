import re

import pytest

from lk_prices import bronze, cli
from lk_prices.cbsl import SourceError, parse_listing


@pytest.fixture
def pages(fixture_text):
    # Page 0 is the newer listing, page 1 the older one; the pager says 1 is last.
    newer = re.sub(r"page=\d+", "page=1", fixture_text("cbsl_listing_2021.html"))
    older = fixture_text("cbsl_listing_2016.html")
    return {0: newer, 1: older}


def test_discover_everything(tmp_path, pages, fake_client):
    client = fake_client(pages)
    assert cli.discover(tmp_path, crawl_all=True, client=client) == 24
    index = bronze.read_index(tmp_path)
    assert index.height == 24
    assert index["url"].n_unique() == 24


def test_discover_again_adds_nothing(tmp_path, pages, fake_client):
    cli.discover(tmp_path, crawl_all=True, client=fake_client(pages))
    assert cli.discover(tmp_path, crawl_all=True, client=fake_client(pages)) == 0
    assert bronze.read_index(tmp_path).height == 24


def test_daily_discover_stops_at_the_first_page_with_nothing_new(tmp_path, pages, fake_client):
    cli.discover(tmp_path, crawl_all=True, client=fake_client(pages))
    client = fake_client(pages)
    cli.discover(tmp_path, client=client)
    assert client.listing_calls == [0]


def test_broken_listing_page_stops_the_crawl(tmp_path, fake_client):
    client = fake_client({0: "<html>Something went wrong</html>"})
    with pytest.raises(SourceError):
        cli.discover(tmp_path, client=client)


def test_fetch_saves_pdfs_and_resumes(tmp_path, pages, fake_client):
    cli.discover(tmp_path, crawl_all=True, client=fake_client(pages))
    reports = parse_listing(pages[0])
    pdfs = {r.url: b"%PDF-1.5 fake" for r in reports}

    # Only page 0's PDFs are available; page 1's twelve fail.
    assert cli.fetch(tmp_path, client=fake_client(pages, pdfs)) == 12
    saved = bronze.pdf_path(reports[0].report_date, reports[0].file_name, tmp_path)
    assert saved.read_bytes() == b"%PDF-1.5 fake"
    assert "year=2021" in str(saved)
    assert len(bronze.missing_pdfs(tmp_path)) == 12

    # A second run only asks for what is still missing.
    assert cli.fetch(tmp_path, limit=5, client=fake_client(pages, pdfs)) == 5
    assert not list(saved.parent.glob("*.tmp"))


def test_an_empty_file_left_by_a_power_cut_counts_as_missing(tmp_path, pages, fake_client):
    cli.discover(tmp_path, crawl_all=True, client=fake_client(pages))
    report = parse_listing(pages[0])[0]
    path = bronze.pdf_path(report.report_date, report.file_name, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    assert report.url in {r.url for r in bronze.missing_pdfs(tmp_path)}
    cli.fetch(tmp_path, client=fake_client(pages, {report.url: b"%PDF-1.5 fake"}))
    assert path.read_bytes() == b"%PDF-1.5 fake"
