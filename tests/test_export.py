import json
from datetime import date

import polars as pl
from deltalake import write_deltalake
from silver_rows import rows

from lk_prices import export, silver


def test_export_writes_derived_figures_only(tmp_path):
    days = [date(2020, 1, 6), date(2020, 1, 7), date(2020, 2, 3), date(2020, 2, 4)]
    prices = [
        (d, "Beans", m, t, p)
        for d, base in zip(days, (100, 110, 120, 130), strict=True)
        for m, t, p in (
            ("Pettah", "wholesale", base),
            ("Pettah", "retail", base * 1.2),
            ("Dambulla", "retail", base * 1.3),
        )
    ]
    write_deltalake(str(silver.prices_path(tmp_path)), rows(*prices))
    rates = tmp_path / "rates.parquet"
    pl.DataFrame(
        {
            "rate_date": [date(2020, 1, 15), date(2020, 2, 14)],
            "currency": ["USD", "USD"],
            "rate_type": ["indicative", "indicative"],
            "rate": [180.0, 200.0],
        }
    ).write_parquet(rates)

    out = tmp_path / "site"
    written = {p.name for p in export.export(tmp_path, out, rates)}
    assert written == {
        "index.json",
        "items.json",
        "margins.json",
        "movers.json",
        "meta.json",
        "usd.json",
    }

    index = json.loads((out / "index.json").read_text())
    assert index["months"] == ["2020-01", "2020-02"]
    assert index["series"]["all"]["retail"][0] == 100.0

    items = json.loads((out / "items.json").read_text())
    assert items["items"]["Beans"]["markets"]["Pettah"]["wholesale"] == [105.0, 125.0]

    movers = json.loads((out / "movers.json").read_text())
    assert movers["date"] == "2020-02-04" and movers["previous"] == "2020-02-03"
    assert {(m["market"], m["change"]) for m in movers["up"]} == {
        ("Pettah", 8.3),
        ("Dambulla", 8.3),
    }

    assert json.loads((out / "usd.json").read_text())["lkr_per_usd"] == [180.0, 200.0]
    # No file is a copy of the daily table.
    assert not any("report_date" in p.read_text() for p in out.iterdir())


def test_usd_is_left_out_without_exchange_rates(tmp_path):
    write_deltalake(
        str(silver.prices_path(tmp_path)),
        rows(
            (date(2020, 1, 6), "Beans", "Pettah", "retail", 100),
            (date(2020, 1, 7), "Beans", "Pettah", "retail", 110),
        ),
    )
    out = tmp_path / "site"
    (out).mkdir()
    (out / "usd.json").write_text("{}")  # from an earlier run
    export.export(tmp_path, out, tmp_path / "missing.parquet")
    assert not (out / "usd.json").exists()
