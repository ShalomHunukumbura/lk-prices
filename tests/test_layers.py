from datetime import timedelta
from decimal import Decimal

import polars as pl
from conftest import DAY
from deltalake import DeltaTable

from lk_prices import bronze, silver
from lk_prices.checks import has_errors, run_checks


def test_bronze_round_trip(tmp_path, bronze_file):
    path = bronze.save(bronze_file(), tmp_path)
    assert path == tmp_path / "bronze/store=glomark/date=2026-10-21/category=118.json.gz"
    assert bronze.load(path) == bronze_file()
    assert bronze.days("glomark", tmp_path) == [DAY]
    assert not list(path.parent.glob("*.tmp"))


def test_silver_rows(bronze_file):
    rows = silver.glomark_rows([bronze_file()])
    assert rows.height == 3
    bread = rows.row(by_predicate=pl.col("product_id") == 112924, named=True)
    assert bread["price"] == Decimal("125.00")
    assert bread["department"] == "Bakery"
    assert bread["category"] == "Bread"
    assert bread["snapshot_date"] == DAY
    promo = rows.row(by_predicate=pl.col("product_id") == 13843, named=True)
    assert promo["has_promo"] is True
    assert promo["promo_price"] == Decimal("306.00")


def test_product_on_two_pages_is_kept_once(bronze_file):
    rows = silver.glomark_rows([bronze_file(), bronze_file(category_id=119)])
    assert rows.height == 3


def test_merge_is_idempotent(tmp_path, bronze_file):
    rows = silver.glomark_rows([bronze_file()])
    assert silver.merge(rows, tmp_path) == silver.MergeResult(3, 0)
    # Loading the same day again changes nothing.
    assert silver.merge(rows, tmp_path) == silver.MergeResult(0, 0)
    assert silver.read(tmp_path).height == 3


def test_merge_adds_a_new_day(tmp_path, bronze_file):
    silver.merge(silver.glomark_rows([bronze_file()]), tmp_path)
    next_day = DAY + timedelta(days=1)
    result = silver.merge(silver.glomark_rows([bronze_file(day=next_day)]), tmp_path)
    assert result == silver.MergeResult(3, 0)
    assert silver.read(tmp_path, next_day).height == 3
    assert silver.read(tmp_path).height == 6


def test_checks_pass_on_good_rows(bronze_file):
    assert run_checks(silver.glomark_rows([bronze_file()])) == []


def test_zero_price_blocks_the_load(bronze_file, products):
    broken = [dict(products[0], priceDetails={"price": 0, "promoPrice": 0, "promoRate": None})]
    findings = run_checks(silver.glomark_rows([bronze_file(items=broken)]))
    assert has_errors(findings)
    assert findings[0].check == "price_positive"


def test_promo_above_price_blocks_the_load(bronze_file, products):
    broken = [dict(products[0], priceDetails={"price": 100, "promoPrice": 150, "promoRate": None})]
    assert has_errors(run_checks(silver.glomark_rows([bronze_file(items=broken)])))


def test_big_drop_in_products_is_a_warning(bronze_file):
    findings = run_checks(silver.glomark_rows([bronze_file()]), previous_count=100)
    assert not has_errors(findings)
    assert [f.check for f in findings] == ["row_count"]


def test_promo_is_read_from_prices_not_the_flag(bronze_file):
    # Glomark sends hasPromo false even when the promo price is lower.
    rows = silver.glomark_rows([bronze_file()])
    assert rows.filter(pl.col("has_promo"))["product_id"].to_list() == [13843]


def test_rebuild_replaces_rows_and_keeps_history(tmp_path, bronze_file):
    rows = silver.glomark_rows([bronze_file()])
    silver.merge(rows.with_columns(has_promo=pl.lit(False)), tmp_path)
    silver.rebuild(rows, tmp_path)
    assert silver.read(tmp_path).filter(pl.col("has_promo")).height == 1
    # Time travel: version 0 still has the old values.
    old = pl.read_delta(str(silver.silver_path(tmp_path)), version=0)
    assert old.filter(pl.col("has_promo")).height == 0
    assert DeltaTable(str(silver.silver_path(tmp_path))).version() == 1
