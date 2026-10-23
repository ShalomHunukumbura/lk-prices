"""Reads prices out of CBSL Daily Price Report PDFs.

The reports changed layout over the years, so each layout gets its own reader
and `detect_layout` decides which one a PDF needs. A PDF that matches no known
layout is an error, never a guess: silently misreading columns would put wrong
prices in the archive.

The table can't be read as plain text. Empty cells (no Dambulla price for coconut
oil, say) make the numbers shift left. So every word keeps its position on the
page, and each number is matched to the column header above it.

Layouts:

- "summary" (from late July 2020): two pages, a chart summary, then one table of
  wholesale and retail prices for 5 markets, "Yesterday" (or "Last Friday" after a
  weekend or holiday) and "Today". Rice and fish have a sub-header naming their
  markets (Marandagahamula, Peliyagoda, Negombo), which replaces the main header
  only for the columns it sits over.
- "sectioned" (before late July 2020): a chart summary, then one table per page
  (vegetables, rice, other), each with its own header, a row of units over the
  columns, and charts underneath.

Both are read by `parse_table_page`. CBSL's typing varies a lot ("Yesteday",
"Last Friay", "25 June, 2026", page sizes from 595 to 1,000 points), so labels are
matched loosely and positions are measured from each page, never fixed.
"""

from __future__ import annotations

import difflib
import io
import re
from collections import Counter
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


class ParseError(ValueError):
    """The PDF doesn't look like any report layout this parser knows."""


class NotAReport(ParseError):
    """The PDF is real but holds no prices: a notice, or pictures with no text."""


@dataclass(frozen=True)
class Price:
    report_date: date
    section: str  # "vegetables", "other", "fruits", "rice", "fish"
    item: str  # as printed, e.g. "Red Onion (Local)"
    unit: str | None  # "kg", "l", "each", "nut", or None if the table printed none
    market: str  # "Pettah", "Dambulla", ...
    price_type: str  # "wholesale" or "retail"
    price: Decimal | None  # today; None when CBSL prints "n.a." or leaves it blank
    previous_price: Decimal | None  # the comparison column
    compared_with: str  # that column's label: "Yesterday", "Last Friday", "Previous Week"


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
    day: str  # "previous", "today", or "ignored" (a second comparison column)
    compared_with: str  # the comparison column's label, e.g. "Last Friday"


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


# "24.07.2020", and in 2019 "2019.04.30".
_DATE_WORD = re.compile(r"\d{1,2}\.\d{1,2}\.\d{4}|\d{4}\.\d{1,2}\.\d{1,2}")
_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# CBSL's typing slips in this row so far: "Yesteday", "Last Friay".
_DAY_WORDS = ["Yesterday", *_WEEKDAYS]
# "Previous 5 Days Average" (2018) is spread over two or three rows.
_LABEL_WORDS = {"Last", "Previous", "Week", "Day", "Days", "Average", "5"}


def _is_day_word(text: str) -> bool:
    """Is this "Yesterday" or a weekday, allowing for a dropped or swapped letter?

    Close enough to catch "Yesteday" and "Friay"; not so loose that a market name
    like "Pettah" or "Dambulla" passes.
    """
    return bool(difflib.get_close_matches(text, _DAY_WORDS, n=1, cutoff=0.8))


_MONTHS = {
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
}  # fmt: skip
# "27-Apr": in mid-2018 a market's latest prices were from an earlier day, and its
# "Today" column carries that date instead.
_STALE_DAY = re.compile(r"\d{1,2}-[A-Z][a-z]{2}")


def _is_label_word(text: str) -> bool:
    """A word that can be part of a comparison column's label: "Yesterday", "Last",
    "Previous 5 Days Average", "April - W3" (a week of April), or a date."""
    return (
        text in _LABEL_WORDS
        or text in _MONTHS
        or text == "-"
        or bool(re.fullmatch(r"W\d", text))
        or _is_day_word(text)
        or bool(_DATE_WORD.fullmatch(text))
    )


def _label_text(words: list[_Word]) -> str:
    """ "Previous" / "2019.04.30" / "Week" read top to bottom -> "Previous Week".

    The date is only used when there are no words ("18.02.2020" alone, Feb 2020).
    """
    ordered = sorted(words, key=lambda w: (round(w.top), w.x0))
    named = [w.text for w in ordered if not _DATE_WORD.fullmatch(w.text)]
    return " ".join(named) if named else ordered[0].text


def _day_pairs(rows: list[list[_Word]]) -> tuple[int, list[tuple[list[_Word], _Word]]]:
    """Find the header row and pair each Today column with its comparison label.

    The comparison label is whatever label words sit just left of a Today, on that
    row or the rows around it. That one rule covers every shape seen so far:
    "Yesterday Today" on one row; "Last Friday" or "Friday" on the row above;
    "Last" above and "Wednesday Today" below; "Previous" / "2019.04.30 Today" /
    "Week" over three rows; "Friday" for one market and "Yesterday" for the next
    on the same page; and "#REF!" (an Excel error) printed in place of a "Today".
    """
    for i, row in enumerate(rows):
        if any(w.text == "Today" for w in row):
            # "Today", a stale date like "27-Apr", or "#REF!" printed in its place.
            # A footnote mark ("Today *", July 2018) is not a column.
            todays = sorted(
                (w for w in row if not _is_label_word(w.text) and any(c.isalnum() for c in w.text)),
                key=lambda w: w.x0,
            )
            band = [w for r in rows[max(0, i - 2) : i + 2] for w in r if _is_label_word(w.text)]
            pairs = []
            for k, today in enumerate(todays):
                left = todays[k - 1].x1 if k else float("-inf")
                slot = [w for w in band if left < w.mid < today.mid and w.x0 < today.x0]
                if not slot:
                    break
                pairs.append((slot, today))
            if pairs and len(pairs) == len(todays):
                return i, pairs
        # 19 February 2020 has no words at all, just dates: "18.02.2020 19.02.2020 ...".
        dates = [w for w in row if _DATE_WORD.fullmatch(w.text)]
        if len(dates) >= 4 and len(dates) == len(row) and len(dates) % 2 == 0:
            ordered = sorted(dates, key=lambda w: w.x0)
            return i, [([ordered[k]], ordered[k + 1]) for k in range(0, len(ordered), 2)]
    raise ParseError("no previous-day/Today header row")


def _header_columns(rows: list[list[_Word]]) -> tuple[list[_Column], int, float]:
    """Find the Wholesale/Retail, market and comparison/Today header rows."""
    i, pairs = _day_pairs(rows)
    # The nearest row above that names both. The table title also says
    # "Wholesale and Retail Prices", but its words don't sit over the columns.
    types = next(
        (r for r in reversed(rows[:i]) if "Wholesale" in _text(r) and "Retail" in _text(r)),
        None,
    )
    # Item names and units sit left of the first column label. Page sizes differ
    # (some reports are 1,000 points wide, not 595), so the edge is read, not fixed.
    label_edge = min(w.x0 for w in pairs[0][0])
    # The market row is the first one above with words over the columns that
    # aren't label words. That skips "Previous" rows and an "Item Unit" row.
    m = i - 1
    # Words left of the label edge don't count, so "Item Unit Previous 5" is skipped
    # and so is a row with nothing over the columns.
    while m >= 0 and all(_is_label_word(w.text) for w in rows[m] if w.x1 > label_edge):
        m -= 1
    markets = rows[m] if m >= 0 else []
    if types is None or not markets:
        raise ParseError("no Wholesale/Retail or market header above the table")
    type_x = [(w.mid, w.text.lower()) for w in types if w.text in ("Wholesale", "Retail")]
    # Some reports put "Item" and "Unit" on the market row; they sit left of the prices.
    market_x = [(w.mid, _market(w.text)) for w in markets if w.x0 > label_edge]

    columns = []
    for slot, today in pairs:
        groups = _label_groups(slot)
        # The label nearest Today is the comparison. In April and May 2019 each
        # market has three columns (Previous Week, Yesterday, Today); the extra one
        # is kept as an ignored column so its numbers can't land in another market.
        nearest, *others = sorted(groups, key=lambda g: -max(w.x1 for w in g))
        previous_mid = _span_mid(nearest)
        # A market name is centred over its columns.
        pair_mid = (min(w.x0 for w in slot) + today.x1) / 2
        market = _nearest(pair_mid, market_x)
        price_type = _nearest(pair_mid, type_x)
        compared_with = _label_text(nearest)
        # A market whose "Today" is an earlier date has no prices for the report's
        # day; both its columns are read and set aside.
        stale = bool(_STALE_DAY.fullmatch(today.text))
        for mid, day in ((previous_mid, "previous"), (today.mid, "today")):
            columns.append(
                _Column(
                    mid, pair_mid, market, price_type, "ignored" if stale else day, compared_with
                )
            )
        for group in others:
            columns.append(
                _Column(
                    _span_mid(group), pair_mid, market, price_type, "ignored", _label_text(group)
                )
            )
    return columns, i, label_edge


def _span_mid(words: list[_Word]) -> float:
    return (min(w.x0 for w in words) + max(w.x1 for w in words)) / 2


def _label_groups(words: list[_Word], gap: float = 6.0) -> list[list[_Word]]:
    """Split label words into columns: words that overlap or nearly touch side by
    side ("Previous" over "Week", "Last Tuesday") are one label; a clear gap starts
    the next one."""
    groups: list[list[_Word]] = []
    for word in sorted(words, key=lambda w: w.x0):
        if groups and word.x0 <= max(w.x1 for w in groups[-1]) + gap:
            groups[-1].append(word)
        else:
            groups.append([word])
    return groups


def _report_date(rows: list[list[_Word]]) -> date:
    for row in rows[:3]:
        # "20 October 2026", "25 June, 2026", and "27 J uly 2020" where the PDF's
        # letter spacing splits the month in two.
        # "2019-04-30", "2019 - 04 - 30", "2018/04/12"
        iso = re.search(r"(\d{4}) ?[-/] ?(\d{1,2}) ?[-/] ?(\d{1,2})", _text(row))
        if iso:
            return date(*(int(g) for g in iso.groups()))
        match = re.search(r"(\d{1,2}) ([A-Za-z][A-Za-z ]*?),? (\d{4})", _text(row))
        if match:
            day, month, year = match.groups()
            try:
                return datetime.strptime(
                    f"{day} {month.replace(' ', '')} {year}", "%d %B %Y"
                ).date()
            except ValueError:
                continue
    raise ParseError("no date in the table title")


_SECTION_WORDS = {"vegetables", "rice", "other", "fish", "fruits"}
# The table ends where its footnotes begin; charts with axis numbers follow.
_FOOTER = re.compile(r"Price (?:increased|decreased) by|^n\.a\. -|Daily (?:Retail|Wholesale)")
# "Coconut (Rs./Nut)", "Egg (Rs./Each )": the unit written into the item name.
_UNIT_IN_NAME = re.compile(r"\(\s*(Rs\./\s*\w+)\s*\)")


def _majority_date(pages: list[list[list[_Word]]]) -> date:
    """The date most table pages agree on. A garbled title ("Commodities1 -6 M a y
    2019" on 16 May 2019, "Commo2d0i1t9ie-s0 1- - 11" on 11 January 2019) is
    outvoted by the other pages; a real split is an error."""
    dates = Counter()
    for rows in pages:
        try:
            dates[_report_date(rows)] += 1
        except ParseError:
            continue
    if not dates:
        raise ParseError("no date in the table title")
    ranked = dates.most_common(2)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        raise ParseError("the table pages carry different dates")
    return ranked[0][0]


def parse_table_page(words: list[dict], report_date: date | None = None) -> list[Price]:
    """Read one page's price table from its words (see `_parse_rows`)."""
    return _parse_rows(_rows(words), report_date)


def _parse_rows(rows: list[list[_Word]], report_date: date | None = None) -> list[Price]:
    """Read one page's price table from its rows of words.

    Works for both table layouts:
    - 2020-onwards "summary": all sections in one table, section titles spaced out
      ("V E G E T A B L E S"), and the unit beside each item.
    - before mid-2020 "sectioned": one table per page, the section named above it
      ("Vegetables"), a row of units over the columns, and some units written
      into the item name ("Coconut (Rs./Nut)"). The rice table there has three
      wholesale markets and two retail ones.
    """
    report_date = report_date or _report_date(rows)
    columns, header_at, label_edge = _header_columns(rows)
    # Half the distance between neighbouring pairs: a sub-header word this close
    # to a pair's centre is that pair's label.
    pair_mids = sorted({c.pair_mid for c in columns})
    reach = min((b - a for a, b in pairwise(pair_mids)), default=80.0) / 2

    section = next(
        (_text(r).lower() for r in rows[:header_at] if _text(r).lower() in _SECTION_WORDS),
        "unknown",
    )
    table_unit: str | None = None
    item_rows = 0
    prices: list[Price] = []
    current = list(columns)
    for row in rows[header_at + 1 :]:
        text = _text(row)
        if _FOOTER.search(text):
            break
        name = _section(row)
        if name:
            section, current = name, list(columns)
            continue
        labels = [w for w in row if w.x1 <= label_edge]
        if not labels:
            if row and all(_unit(w.text) for w in row):
                # "Rs./kg" over every column: the unit for rows that don't give one.
                table_unit = _unit(row[0].text)
            elif all(_is_label_word(w.text) for w in row):
                # The rest of a header label ("Week" under "Previous"), or its date.
                continue
            elif row and all(_market(w.text).isalpha() for w in row):
                # A row of market names under a section title re-labels the columns
                # it sits over and leaves the others alone.
                current = [
                    replace(c, market=_label_over(c, row, reach) or c.market) for c in current
                ]
            continue
        unit_word = next((w for w in labels if _unit(w.text)), None)
        item = " ".join(w.text for w in labels if unit_word is None or w.x1 <= unit_word.x0)
        unit = _unit(unit_word.text) if unit_word else None
        embedded = _UNIT_IN_NAME.search(item)
        if embedded:
            unit = _unit(embedded.group(1).replace(" ", ""))
            item = _UNIT_IN_NAME.sub("", item).strip()
        unit = unit or table_unit
        if not any(c.isalpha() for c in item):
            continue
        # unit may be None (11 January 2019 prints none); silver takes it from the
        # item catalogue.
        item_rows += 1
        cells: dict[int, str] = {}
        for word in row:
            if word.x1 <= label_edge:
                continue
            k = min(range(len(current)), key=lambda c: abs(current[c].mid - word.mid))
            cells[k] = word.text
        # Pair up the previous day and Today for each market and price type.
        by_market: dict[tuple[str, str], dict[str, Decimal | None]] = {}
        compared: dict[tuple[str, str], str] = {}
        for k, column in enumerate(current):
            if column.day == "ignored":
                continue
            key = (column.market, column.price_type)
            by_market.setdefault(key, {})[column.day] = _money(cells.get(k, ""))
            compared[key] = column.compared_with
        for (market, price_type), values in by_market.items():
            if values.get("today") is None and values.get("previous") is None:
                continue
            prices.append(
                Price(
                    report_date=report_date,
                    section=section,
                    item=item,
                    unit=unit,
                    market=market,
                    price_type=price_type,
                    price=values.get("today"),
                    previous_price=values.get("previous"),
                    compared_with=compared[(market, price_type)],
                )
            )
    # No item rows means the table wasn't understood. Item rows with no prices
    # are real: during the April 2020 lockdown whole tables were "n.a.".
    if not item_rows:
        raise ParseError("the price table had no rows")
    return prices


# Market names as CBSL has misspelt them.
_MARKET_SPELLINGS = {"Maradagahamula": "Marandagahamula"}


def _market(text: str) -> str:
    # "Pettah*" carries a footnote mark (e.g. "partly closed for the New Year").
    text = text.rstrip("*")
    return _MARKET_SPELLINGS.get(text, text)


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
    if pages >= 3 and TABLE_TITLE in texts[1]:
        # A chart summary, then one table per page (before mid-2020). Some end
        # with a page of charts, which is skipped.
        return "sectioned"
    if pages == 1 and "Summary of Price" in texts[0]:
        # Only the chart page went out (16 March 2022, during the fuel crisis).
        raise NotAReport("summary page only, no price table")
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
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        if layout == "summary":
            return layout, parse_table_page(pdf.pages[-1].extract_words())
        if layout == "sectioned":
            pages = [
                _rows(page.extract_words())
                for page in pdf.pages[1:]
                if TABLE_TITLE in (page.extract_text() or "")
            ]
            day = _majority_date(pages)
            prices = [p for rows in pages for p in _parse_rows(rows, day)]
            if not prices:
                raise NotAReport("every price in the report is n.a.")
            return layout, prices
    raise ParseError(f"no reader for layout {layout}")
