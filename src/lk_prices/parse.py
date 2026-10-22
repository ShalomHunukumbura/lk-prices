"""Reads prices out of CBSL Daily Price Report PDFs.

The reports changed layout over the years, so each layout gets its own reader
and `detect_layout` decides which one a PDF needs. A PDF that matches no known
layout is an error, never a guess: silently misreading columns would put wrong
prices in the archive.

The table can't be read as plain text. Empty cells (no Dambulla price for coconut
oil, say) make the numbers shift left. So every word keeps its position on the
page, and each number is matched to the column header above it.

Layouts:

- "summary" (2021 onwards): two pages, a chart summary, then one table of wholesale
  and retail prices, "Yesterday" (or "Last Friday" after a weekend or holiday) and
  "Today", for 5 markets. Rice and fish have
  their own sub-header naming the markets for those rows (Marandagahamula,
  Peliyagoda, Negombo), which replace the main header only for the columns they
  sit over.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from itertools import pairwise

import pdfplumber
import pypdfium2 as pdfium

TABLE_TITLE = "Wholesale and Retail Prices"
# Words on the same row can sit a point or two apart (highlighted cells do).
ROW_TOLERANCE = 3.0
# Pieces of one number ("5" + "50.00") touch; separate cells are far apart.
JOIN_GAP = 1.0
# Item names and units sit left of the first price column.
LABEL_MAX_X = 150.0


class ParseError(ValueError):
    """The PDF doesn't look like any report layout this parser knows."""


class NotAReport(ParseError):
    """The PDF is real but holds no prices: a notice, or pictures with no text."""


@dataclass(frozen=True)
class Price:
    report_date: date
    section: str  # "vegetables", "other", "fruits", "rice", "fish"
    item: str  # as printed, e.g. "Red Onion (Local)"
    unit: str  # "kg", "l", "each", "nut"
    market: str  # "Pettah", "Dambulla", ...
    price_type: str  # "wholesale" or "retail"
    price: Decimal | None  # today; None when CBSL prints "n.a." or leaves it blank
    previous_price: Decimal | None  # the "Yesterday" or "Last Friday" column


@dataclass
class _Word:
    text: str
    x0: float
    x1: float
    top: float

    @property
    def mid(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(frozen=True)
class _Column:
    mid: float
    pair_mid: float  # centre of this column's Yesterday/Today pair
    market: str
    price_type: str
    day: str  # "previous" or "today"


def _rows(words: list[dict]) -> list[list[_Word]]:
    """Group words into rows by their vertical position, then join split numbers."""
    items = sorted(
        (_Word(w["text"], w["x0"], w["x1"], w["top"]) for w in words), key=lambda w: w.top
    )
    rows: list[list[_Word]] = []
    for word in items:
        if rows and abs(word.top - rows[-1][0].top) <= ROW_TOLERANCE:
            rows[-1].append(word)
        else:
            rows.append([word])
    joined = []
    for row in rows:
        row.sort(key=lambda w: w.x0)
        merged: list[_Word] = []
        for word in row:
            prev = merged[-1] if merged else None
            if prev and word.x0 - prev.x1 <= JOIN_GAP and _numeric(prev.text + word.text):
                merged[-1] = _Word(prev.text + word.text, prev.x0, word.x1, prev.top)
            else:
                merged.append(word)
        joined.append(merged)
    return joined


def _numeric(text: str) -> bool:
    return bool(re.fullmatch(r"[\d,]*\.?\d*", text)) and any(c.isdigit() for c in text)


def _text(row: list[_Word]) -> str:
    return " ".join(w.text for w in row)


def _money(text: str) -> Decimal | None:
    cleaned = text.replace(",", "")
    if not re.fullmatch(r"\d+(\.\d+)?", cleaned):
        return None
    value = Decimal(cleaned).quantize(Decimal("0.01"))
    return value if value > 0 else None


_UNITS = {"rs./kg": "kg", "rs./ltr": "l", "rs./l": "l", "rs./each": "each", "rs./nut": "nut"}


def _unit(text: str) -> str | None:
    return _UNITS.get(text.lower())


def _section(row: list[_Word]) -> str | None:
    # Section titles are spaced out: "V E G E T A B L E S", "R I C E".
    if len(row) >= 3 and all(len(w.text) == 1 and w.text.isalpha() for w in row):
        return "".join(w.text for w in row).lower()
    return None


def _nearest(x: float, options: list[tuple[float, str]]) -> str:
    return min(options, key=lambda o: abs(o[0] - x))[1]


_WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}


def _previous_labels(row: list[_Word]) -> list[_Word]:
    """The previous-day column labels in a row: "Yesterday", or after a weekend or
    holiday "Last Friday" (two words, joined here into one label) or just "Friday"."""
    labels = []
    for k, word in enumerate(row):
        if word.text == "Yesterday":
            labels.append(word)
        elif word.text == "Last" and k + 1 < len(row) and row[k + 1].text in _WEEKDAYS:
            nxt = row[k + 1]
            labels.append(_Word(f"Last {nxt.text}", word.x0, nxt.x1, word.top))
        elif word.text in _WEEKDAYS and not (k and row[k - 1].text == "Last"):
            # Some reports print just "Tuesday", with its date on a row below.
            labels.append(word)
    return labels


def _header_columns(rows: list[list[_Word]]) -> tuple[list[_Column], int]:
    """Find the Wholesale/Retail, market and previous-day/Today header rows.

    Usually "Yesterday" and "Today" share a row. After a weekend or holiday the
    previous day is "Last Friday" (or another weekday), printed on its own row just
    above "Today".
    """
    for i, row in enumerate(rows):
        todays = [w for w in row if w.text == "Today"]
        if not todays:
            continue
        previous = _previous_labels(row)
        label_rows = 1
        if not previous and i > 0:
            previous = _previous_labels(rows[i - 1])
            label_rows = 2
        if previous and len(previous) == len(todays):
            days = sorted([*previous, *todays], key=lambda w: w.x0)
            break
    else:
        raise ParseError("no previous-day/Today header row")
    # The nearest row above that names both. The table title also says
    # "Wholesale and Retail Prices", but its words don't sit over the columns.
    types = next(
        (r for r in reversed(rows[:i]) if "Wholesale" in _text(r) and "Retail" in _text(r)),
        None,
    )
    markets = rows[i - label_rows] if i >= label_rows else []
    if types is None or not markets:
        raise ParseError("no Wholesale/Retail or market header above the table")
    type_x = [(w.mid, w.text.lower()) for w in types if w.text in ("Wholesale", "Retail")]
    market_x = [(w.mid, _market(w.text)) for w in markets]

    columns = []
    for j, word in enumerate(days):
        is_today = word.text == "Today"
        # A market name is centred over its previous-day/Today pair.
        pair = days[j - 1 : j + 1] if is_today else days[j : j + 2]
        pair_mid = (pair[0].mid + pair[-1].mid) / 2
        columns.append(
            _Column(
                mid=word.mid,
                pair_mid=pair_mid,
                market=_nearest(pair_mid, market_x),
                price_type=_nearest(pair_mid, type_x),
                day="today" if is_today else "previous",
            )
        )
    return columns, i


def _report_date(rows: list[list[_Word]]) -> date:
    for row in rows[:3]:
        # "20 October 2026", and sometimes "25 June, 2026".
        match = re.search(r"(\d{1,2}) ([A-Za-z]+),? (\d{4})", _text(row))
        if match:
            return datetime.strptime(" ".join(match.groups()), "%d %B %Y").date()
    raise ParseError("no date in the table title")


def parse_summary_page(words: list[dict]) -> list[Price]:
    """Read the price table of a 2021-onwards report from its words."""
    rows = _rows(words)
    report_date = _report_date(rows)
    columns, header_at = _header_columns(rows)
    # Half the distance between neighbouring pairs: a sub-header word this close
    # to a pair's centre is that pair's label.
    pair_mids = sorted({c.pair_mid for c in columns})
    reach = min((b - a for a, b in pairwise(pair_mids)), default=80.0) / 2

    prices: list[Price] = []
    section = "unknown"
    current = list(columns)
    for row in rows[header_at + 1 :]:
        name = _section(row)
        if name:
            section, current = name, list(columns)
            continue
        labels = [w for w in row if w.x1 <= LABEL_MAX_X]
        unit_word = next((w for w in labels if _unit(w.text)), None)
        if unit_word is None:
            # A row of market names under a section title re-labels the columns
            # it sits over and leaves the others alone.
            if row and all(w.x0 > LABEL_MAX_X and _market(w.text).isalpha() for w in row):
                current = [
                    replace(c, market=_label_over(c, row, reach) or c.market) for c in current
                ]
            continue
        item = " ".join(w.text for w in labels if w.x1 <= unit_word.x0)
        cells: dict[int, str] = {}
        for word in row:
            if word.x0 <= LABEL_MAX_X:
                continue
            k = min(range(len(current)), key=lambda c: abs(current[c].mid - word.mid))
            cells[k] = word.text
        # Pair up Yesterday and Today for each market and price type.
        by_market: dict[tuple[str, str], dict[str, Decimal | None]] = {}
        for k, column in enumerate(current):
            key = (column.market, column.price_type)
            by_market.setdefault(key, {})[column.day] = _money(cells.get(k, ""))
        for (market, price_type), values in by_market.items():
            if values.get("today") is None and values.get("previous") is None:
                continue
            prices.append(
                Price(
                    report_date=report_date,
                    section=section,
                    item=item,
                    unit=_unit(unit_word.text),
                    market=market,
                    price_type=price_type,
                    price=values.get("today"),
                    previous_price=values.get("previous"),
                )
            )
    if not prices:
        raise ParseError("the price table had no rows")
    return prices


def _market(text: str) -> str:
    # "Pettah*" carries a footnote mark (e.g. "partly closed for the New Year").
    return text.rstrip("*")


def _label_over(column: _Column, row: list[_Word], reach: float) -> str | None:
    return next((_market(w.text) for w in row if abs(w.mid - column.pair_mid) < reach), None)


def page_texts(body: bytes) -> list[str]:
    """The plain text of each page, read with pdfium.

    pdfium is a C library and reads a page in milliseconds. pdfplumber (pdfminer)
    gives word positions, which the table needs, but on an image-only PDF it can
    spend 15 seconds finding no text at all. So layouts are detected with pdfium,
    and pdfplumber is only used on the table page.
    """
    try:
        doc = pdfium.PdfDocument(body)
    except pdfium.PdfiumError as error:
        raise ParseError(f"not a readable PDF: {error}") from error
    try:
        return [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
    finally:
        doc.close()


def detect_layout(texts: list[str]) -> str:
    pages = len(texts)
    if pages == 2 and TABLE_TITLE in texts[-1] and "Today" in texts[-1]:
        return "summary"
    if "Non-Publication" in " ".join(texts):
        # Markets closed (e.g. for the New Year), so CBSL posted a notice instead.
        raise NotAReport("notice of non-publication, no prices that day")
    if not "".join(texts).strip():
        # Made with "Print to PDF": the pages are pictures, with no text to read.
        raise NotAReport(f"image-only PDF ({pages} pages, no text layer)")
    raise ParseError(f"unknown layout ({pages} pages)")


def parse_pdf(body: bytes) -> tuple[str, list[Price]]:
    """Return the layout name and every price in a report."""
    layout = detect_layout(page_texts(body))
    if layout == "summary":
        with pdfplumber.open(io.BytesIO(body)) as pdf:
            return layout, parse_summary_page(pdf.pages[-1].extract_words())
    raise ParseError(f"no reader for layout {layout}")
