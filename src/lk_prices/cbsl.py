"""Finds and downloads the Central Bank of Sri Lanka's Daily Price Reports.

Every working day CBSL publishes a PDF of wholesale and retail prices for about
50 food items in the main markets. The reports are listed, newest first, 12 to a
page, at /en/statistics/economic-indicators/price-report?page=N, going back to
August 2016.

The PDF file names are not regular: price_report_20160801e.pdf (2016),
price_report_20211013.pdf (2021), price_report_20261020_e.pdf (2026). So links are
read from the listing instead of being guessed from the date.

cbsl.gov.lk/robots.txt asks for 10 seconds between requests.
"""

from __future__ import annotations

import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime

SOURCE = "cbsl"
BASE_URL = "https://www.cbsl.gov.lk"
LISTING_URL = f"{BASE_URL}/en/statistics/economic-indicators/price-report"
USER_AGENT = os.environ.get(
    "LK_PRICES_USER_AGENT",
    "lk-prices/0.1 (+https://github.com/ShalomHunukumbura/lk-prices)",
)
CRAWL_DELAY_SECONDS = 10.0

# Any link to a PDF in the price report folder, with its link text:
#   <a href=".../pricerpt/price_report_20211013.pdf">Daily Price Report - 13 October 2021</a>
#   <a href=".../pricerpt/Daily%20Price%20Report%20-%2005%2005%202017.pdf">Daily Price Report 05.05.2017</a>
_REPORT_LINK = re.compile(
    r'<a[^>]*href="(?P<url>[^"]*/pricerpt/(?P<file>[^"/]+\.pdf))"[^>]*>(?P<title>[^<]+)</a>'
)
# Every PDF link in the folder, including the icon links that carry no text.
_ANY_REPORT_PDF = re.compile(r'href="([^"]*/pricerpt/[^"/]+\.pdf)"')
# Dates as they appear in file names: 20211013, or "05 05 2017" (day month year).
_FILE_DATES = [
    (re.compile(r"(?<!\d)(\d{8})(?!\d)"), "%Y%m%d"),
    (re.compile(r"(?<!\d)(\d{2} \d{2} \d{4})(?!\d)"), "%d %m %Y"),
]
# Dates as they appear in link text: "13 October 2021", "05.05.2017".
_TITLE_DATES = [
    (re.compile(r"(\d{1,2} [A-Za-z]+ \d{4})"), "%d %B %Y"),
    (re.compile(r"(\d{1,2}\.\d{1,2}\.\d{4})"), "%d.%m.%Y"),
]
_PAGE_NUMBER = re.compile(r"price-report\?page=(\d+)")


class SourceError(RuntimeError):
    """CBSL could not be reached or answered with something unusable."""


@dataclass(frozen=True)
class Report:
    report_date: date
    url: str
    file_name: str
    title: str


def _find_date(text: str, patterns) -> date | None:
    for pattern, fmt in patterns:
        match = pattern.search(text)
        if match:
            try:
                return datetime.strptime(match.group(1), fmt).date()
            except ValueError:
                continue
    return None


def parse_listing(html: str) -> list[Report]:
    """Return the reports on one listing page, newest first, each once.

    Raises SourceError if the page links to a report this can't date, so a new
    naming scheme stops the crawl instead of being skipped without a word.
    """
    reports: dict[str, Report] = {}
    for m in _REPORT_LINK.finditer(html):
        title = " ".join(m.group("title").split())
        if not title:
            continue
        file_name = urllib.parse.unquote(m.group("file"))
        # The file name and the link text should agree. When they don't, the
        # title is what CBSL shows people, so it wins.
        report_date = _find_date(title, _TITLE_DATES) or _find_date(file_name, _FILE_DATES)
        if report_date is None:
            raise SourceError(f"can't tell the date of {file_name!r} ({title!r})")
        url = m.group("url")
        reports.setdefault(url, Report(report_date, url, file_name, title))
    linked = set(_ANY_REPORT_PDF.findall(html))
    if linked - set(reports):
        raise SourceError(f"{len(linked - set(reports))} report links on the page were not read")
    return sorted(reports.values(), key=lambda r: (r.report_date, r.url), reverse=True)


def last_page(html: str) -> int:
    """The highest page number the pager links to (pages count from 0)."""
    numbers = [int(n) for n in _PAGE_NUMBER.findall(html)]
    return max(numbers, default=0)


class Client:
    """A polite HTTP client: one request at a time, with a pause in between."""

    def __init__(self, delay: float = CRAWL_DELAY_SECONDS, retries: int = 3, timeout: float = 60.0):
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self._last_request_at: float | None = None

    def _wait_for_turn(self) -> None:
        if self._last_request_at is not None:
            remaining = self.delay - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)

    def get(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        last_error: Exception | None = None
        for _ in range(self.retries):
            self._wait_for_turn()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                # A 404 won't fix itself; anything else might.
                if 400 <= error.code < 500:
                    raise SourceError(f"{url}: HTTP {error.code}") from error
                last_error = error
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
            finally:
                self._last_request_at = time.monotonic()
        raise SourceError(f"{url} failed after {self.retries} attempts: {last_error}")

    def listing(self, page: int) -> str:
        url = LISTING_URL if page == 0 else f"{LISTING_URL}?page={page}"
        return self.get(url).decode("utf-8", errors="replace")

    def pdf(self, report: Report) -> bytes:
        body = self.get(report.url)
        if not body.startswith(b"%PDF"):
            raise SourceError(f"{report.url} is not a PDF")
        return body
