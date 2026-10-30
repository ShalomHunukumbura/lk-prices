import polars as pl
from silver_rows import D, rows

from lk_prices import flags
from lk_prices.checks import missing_items


def oil(*values):
    return rows(*[(D[i], "Coconut oil", "Pettah", "retail", v) for i, v in enumerate(values)])


def flagged_at(frame, day):
    return flags.flag(frame).filter(pl.col("report_date") == day).row(0, named=True)


def test_a_dropped_digit_that_goes_straight_back_is_a_spike():
    row = flagged_at(oil(570, 570, 85, 570, 570), D[2])
    assert row["jump"] and row["spike"] and row["suspect"]
    # The day after jumps back, but its neighbours don't agree around it.
    after = flagged_at(oil(570, 570, 85, 570, 570), D[3])
    assert after["jump"] and not after["spike"]


def test_a_real_step_change_is_a_jump_but_not_suspect():
    row = flagged_at(oil(100, 100, 400, 400, 400), D[2])
    assert row["jump"] and not row["spike"] and not row["suspect"]


def test_the_next_reports_yesterday_column_checks_each_price():
    frame = rows(
        (D[0], "Beans", "Pettah", "retail", 25, 250),
        (D[1], "Beans", "Pettah", "retail", 250, 250),  # "yesterday was 250", not 25
        (D[2], "Beans", "Pettah", "retail", 250, 250),
    )
    first = flagged_at(frame, D[0])
    assert first["checked"] and first["restated"]
    assert flagged_at(frame, D[1])["checked"] and not flagged_at(frame, D[1])["restated"]


def test_a_week_comparison_or_a_skipped_report_isnt_checked():
    weekly = rows(
        (D[0], "Beans", "Pettah", "retail", 25),
        (D[1], "Beans", "Pettah", "retail", 250, 250),
        compared_with="Previous Week",
    )
    assert not flagged_at(weekly, D[0])["checked"]
    # Beans skips D[1], so D[2]'s "Yesterday" is about a day with no Beans row.
    skipped = rows(
        (D[0], "Beans", "Pettah", "retail", 25),
        (D[1], "Carrot", "Pettah", "retail", 90),
        (D[2], "Beans", "Pettah", "retail", 250, 250),
    )
    assert not flagged_at(skipped, D[0])["checked"]


def test_missing_items_are_ones_both_neighbours_carry():
    frame = rows(
        (D[0], "Beans", "Pettah", "retail", 100),
        (D[0], "Carrot", "Pettah", "retail", 90),
        (D[1], "Beans", "Pettah", "retail", 100),  # no carrot
        (D[2], "Beans", "Pettah", "retail", 100),
        (D[2], "Carrot", "Pettah", "retail", 90),
        (D[3], "Beans", "Pettah", "retail", 100),  # no carrot, but D[4] has none either
        (D[4], "Beans", "Pettah", "retail", 100),
    )
    assert missing_items(frame).rows() == [(D[1], ["Carrot"])]
