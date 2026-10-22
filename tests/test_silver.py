import shutil
from datetime import date

import polars as pl
import pytest
from conftest import FIXTURES
from deltalake import DeltaTable

from lk_prices import bronze, cli, silver
from lk_prices.cbsl import Report
from lk_prices.checks import problems
from lk_prices.parse import parse_pdf

PRICERPT = "https://www.cbsl.gov.lk/sites/default/files/cbslweb_documents/statistics/pricerpt"


def land(root, report_date, file_name, fixture):
    """Put a fixture PDF into bronze as if it had been discovered and fetched."""
    report = Report(report_date, f"{PRICERPT}/{file_name}", file_name, "Daily Price Report")
    bronze.add_to_index([report], root)
    path = bronze.pdf_path(report_date, file_name, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / fixture, path)


@pytest.fixture
def root(tmp_path):
    land(tmp_path, date(2021, 10, 13), "price_report_20211013.pdf", "price_report_20211013.pdf")
    return tmp_path


def test_load_and_reload(root):
    assert cli.load_silver(root) == (1, 0)
    rows = silver.read(root)
    assert rows.height == 169
    assert rows["year"].unique().to_list() == [2021]
    assert rows["source_file"].unique().to_list() == ["price_report_20211013.pdf"]
    # Nothing new: the second run parses nothing and writes nothing.
    version = DeltaTable(str(silver.prices_path(root))).version()
    assert cli.load_silver(root) == (0, 0)
    assert DeltaTable(str(silver.prices_path(root))).version() == version


def test_merge_of_the_same_rows_changes_nothing(root):
    layout, prices = parse_pdf((FIXTURES / "price_report_20211013.pdf").read_bytes())
    rows = silver.rows_from(prices, layout, "price_report_20211013.pdf")
    assert silver.merge(rows, root) == silver.MergeResult(169, 0)
    assert silver.merge(rows, root) == silver.MergeResult(0, 0)


def test_a_pdf_without_prices_is_listed_but_not_a_failure(root):
    land(root, date(2021, 10, 14), "price_report_20211014.pdf", "blank.pdf")
    assert cli.load_silver(root) == (1, 0)
    rejects = silver.read_rejects(root)
    assert rejects["source_file"].to_list() == ["price_report_20211014.pdf"]
    assert "image-only" in rejects["error"][0]
    assert silver.read(root)["report_date"].unique().to_list() == [date(2021, 10, 13)]


def test_a_broken_pdf_is_rejected_and_counted(root):
    path = bronze.pdf_path(date(2021, 10, 15), "price_report_20211015.pdf", root)
    land(root, date(2021, 10, 15), "price_report_20211015.pdf", "blank.pdf")
    path.write_bytes(b"%PDF-1.4\nthis is not really a PDF")
    assert cli.load_silver(root) == (1, 1)
    assert "not a readable PDF" in silver.read_rejects(root)["error"][0]


def test_rebuild_keeps_the_old_version(root):
    cli.load_silver(root)
    cli.rebuild_silver(root)
    table = DeltaTable(str(silver.prices_path(root)))
    assert table.version() == 1
    assert pl.read_delta(str(silver.prices_path(root)), version=0).height == 169


@pytest.mark.parametrize(
    ("printed", "tidy"),
    [
        ("Red Onion (lmp)", "Red Onion (Imp)"),
        ("Big Onion  (Imp)", "Big Onion (Imp)"),
        ("Samba", "Samba"),
    ],
)
def test_tidy_item(printed, tidy):
    assert silver.tidy_item(printed) == tidy


def test_checks_catch_a_misread_column():
    layout, prices = parse_pdf((FIXTURES / "price_report_20211013.pdf").read_bytes())
    rows = silver.rows_from(prices, layout, "x.pdf")
    assert problems(rows) == []
    # Retail at a tenth of wholesale is what a shifted column looks like.
    broken = rows.with_columns(
        price=pl.when(pl.col("price_type") == "retail")
        .then(pl.col("price") / 10)
        .otherwise(pl.col("price"))
        .cast(silver.MONEY)
    )
    assert any("below half of wholesale" in p for p in problems(broken))
    assert any("only" in p for p in problems(rows.head(10)))


def test_the_later_upload_of_a_day_wins():
    assert silver.upload_rank("Daily Price Report - 17 05 2017.pdf") == 0
    assert silver.upload_rank("Daily Price Report - 17 05 2017_0.pdf") == 1
    assert silver.upload_rank("Daily Price Report - 17 05 2017_1.pdf") == 2

    layout, prices = parse_pdf((FIXTURES / "price_report_20211013.pdf").read_bytes())
    first = silver.rows_from(prices, layout, "Daily Price Report - 17 05 2017.pdf")
    second = silver.rows_from(prices, layout, "Daily Price Report - 17 05 2017_0.pdf")
    kept = silver.latest_upload(pl.concat([second, first]))
    assert kept.height == first.height
    assert kept["source_file"].unique().to_list() == ["Daily Price Report - 17 05 2017_0.pdf"]


def test_a_correctly_listed_file_beats_a_mislisted_one():
    # The PDF says 13 October 2021. One listing gets the date right; the other is a
    # later upload listed under the wrong date, as CBSL's listing sometimes does.
    layout, prices = parse_pdf((FIXTURES / "price_report_20211013.pdf").read_bytes())
    right = silver.rows_from(prices, layout, "price_report_20211013.pdf", date(2021, 10, 13))
    wrong = silver.rows_from(prices, layout, "price_report_20201013_0.pdf", date(2020, 10, 13))
    kept = silver.latest_upload(pl.concat([right, wrong]))
    assert kept["source_file"].unique().to_list() == ["price_report_20211013.pdf"]
    assert kept["report_date"].unique().to_list() == [date(2021, 10, 13)]
