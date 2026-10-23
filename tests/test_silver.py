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
        ("Big-Onion (Imp)", "Big Onion (Imp)"),
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
    assert any("only" in p for p in problems(rows.head(5)))


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


def test_a_mislisted_file_loaded_later_doesnt_replace_the_right_one(root):
    # Load the correctly listed file first, then a re-upload listed under the next
    # day that holds the same report, in a separate MERGE.
    layout, prices = parse_pdf((FIXTURES / "price_report_20211013.pdf").read_bytes())
    right = silver.rows_from(prices, layout, "price_report_20211013.pdf", date(2021, 10, 13))
    wrong = silver.rows_from(prices, layout, "price_report_20211013_0.pdf", date(2021, 10, 14))
    silver.merge(right, root)
    assert silver.merge(wrong, root) == silver.MergeResult(0, 0)
    assert silver.read(root)["source_file"].unique().to_list() == ["price_report_20211013.pdf"]


def test_an_unknown_market_blocks_the_load():
    layout, prices = parse_pdf((FIXTURES / "price_report_20211013.pdf").read_bytes())
    rows = silver.rows_from(prices, layout, "x.pdf")
    misread = rows.with_columns(
        market=pl.when(pl.col("market") == "Pettah").then(pl.lit("Last")).otherwise("market")
    )
    assert any("unknown markets ['Last']" in p for p in problems(misread))


def test_old_item_names_map_to_one_name_and_section():
    from lk_prices.items import CATALOGUE, canonical

    assert canonical("Dhal (Small)") == canonical("Dhal") == canonical("Red Dhal") == "Red Dhal"
    assert canonical("Kekulu Red") == "Kekulu (Red)"
    assert canonical("Mystery Fruit") is None
    # Every alias points at a real catalogue entry.
    from lk_prices.items import ALIASES

    assert set(ALIASES.values()) <= set(CATALOGUE)


def test_an_unknown_item_blocks_the_load():
    layout, prices = parse_pdf((FIXTURES / "price_report_20211013.pdf").read_bytes())
    rows = silver.rows_from(prices, layout, "x.pdf")
    renamed = rows.with_columns(
        item=pl.when(pl.col("item") == "Beans").then(pl.lit("Bean")).otherwise("item")
    )
    assert any("unknown items ['Bean']" in p for p in problems(renamed))


def test_a_single_typo_is_quarantined_and_the_report_loads(tmp_path):
    # 15 May 2020: retail imported red onion at Dambulla is "21.00", the day after 228.00.
    land(tmp_path, date(2020, 5, 15), "price_report_20200515e.pdf", "price_report_20200515e.pdf")
    assert cli.load_silver(tmp_path) == (1, 0)
    held = silver.read_quarantine(tmp_path)
    assert held.select("item", "market", "price_type").rows() == [
        ("Red Onion (Imp)", "Dambulla", "retail")
    ]
    assert str(held["price"][0]) == "21.00"
    loaded = silver.read(tmp_path)
    assert loaded.height > 80
    assert loaded.filter(
        (pl.col("item") == "Red Onion (Imp)")
        & (pl.col("market") == "Dambulla")
        & (pl.col("price_type") == "retail")
    ).is_empty()


def test_units_come_from_the_catalogue_and_must_agree_with_it():
    from lk_prices.items import unit

    assert unit("Beans") == "kg"
    assert unit("Egg (White)") == "each"
    assert unit("Mystery Fruit") is None

    layout, prices = parse_pdf((FIXTURES / "price_report_20211013.pdf").read_bytes())
    rows = silver.rows_from(prices, layout, "x.pdf")
    assert problems(rows) == []
    misread = rows.with_columns(
        unit=pl.when(pl.col("item") == "Beans").then(pl.lit("l")).otherwise("unit")
    )
    assert any("unexpected unit (Beans)" in p for p in problems(misread))
