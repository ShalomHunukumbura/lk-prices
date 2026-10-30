from datetime import timedelta

import polars as pl
from deltalake import DeltaTable, write_deltalake
from silver_rows import D, rows

from lk_prices.maintain import maintain, maintain_all


def test_maintenance_compacts_and_vacuums_without_changing_rows(tmp_path):
    path = tmp_path / "silver" / "prices"
    for day in D:
        write_deltalake(str(path), rows((day, "Beans", "Pettah", "retail", 100)), mode="append")
    before = pl.read_delta(str(path)).sort("report_date")

    result = maintain(path, "silver/prices", retention=timedelta(0))

    assert (result.files_before, result.compacted, result.files_after) == (5, 5, 1)
    assert result.vacuumed == 5
    assert pl.read_delta(str(path)).sort("report_date").equals(before)


def test_default_retention_keeps_recent_versions_for_time_travel(tmp_path):
    path = tmp_path / "gold" / "index"
    for day in D[:2]:
        write_deltalake(str(path), rows((day, "Beans", "Pettah", "retail", 100)), mode="append")
    [result] = maintain_all(tmp_path)
    assert result.table == "gold/index" and result.vacuumed == 0
    table = DeltaTable(str(path))
    table.load_as_version(0)
    assert len(table.file_uris()) == 1
