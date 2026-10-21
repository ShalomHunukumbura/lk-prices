"""Reads products and prices from Glomark's online store (glomark.lk).

Glomark has no public API, but it doesn't need one. Every category page
(`/<department>/<category>/c/<id>`) carries the full product list for that
category as a JSON array in an inline script (`productList = [...]`), which the
page's own JavaScript turns into product cards. This module reads the sitemap to
find the category pages, fetches each one, and pulls that array out as-is.

glomark.lk/robots.txt allows everything and sets no crawl delay. A daily run is
about 90 requests, spaced a few seconds apart.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

STORE = "glomark"
BASE_URL = "https://glomark.lk"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
USER_AGENT = os.environ.get(
    "LK_PRICES_USER_AGENT",
    "lk-prices/0.1 (+https://github.com/ShalomHunukumbura/lk-prices)",
)
DELAY_SECONDS = 3.0
# Each product names the staff member responsible for its brand, by email.
# That is personal data and has no place in a price archive, so it is removed
# before anything is written to disk.
PERSONAL_FIELDS = ("brandResponsible",)

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_CATEGORY_URL = re.compile(r"/c/(\d+)$")
# The page declares `let productList = [];` first and assigns the real list later.
_PRODUCT_LIST = re.compile(r"productList\s*=\s*\[")


class SourceError(RuntimeError):
    """The store could not be reached or answered with something unusable."""


@dataclass(frozen=True)
class Category:
    id: int
    url: str


def categories_from_sitemap(xml: str) -> list[Category]:
    """Return every category page the sitemap lists, sorted by id."""
    found: dict[int, Category] = {}
    for url in _LOC.findall(xml):
        match = _CATEGORY_URL.search(url)
        if match:
            category_id = int(match.group(1))
            found.setdefault(category_id, Category(category_id, url))
    if not found:
        raise SourceError("no category pages in the sitemap; has it changed?")
    return [found[key] for key in sorted(found)]


def extract_products(html: str) -> list[dict]:
    """Pull the product list out of a category page.

    The page assigns `productList` more than once (an empty placeholder, then the
    data), so this takes the longest list found.
    """
    decoder = json.JSONDecoder()
    best: list | None = None
    for match in _PRODUCT_LIST.finditer(html):
        try:
            value, _ = decoder.raw_decode(html, match.end() - 1)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and (best is None or len(value) > len(best)):
            best = value
    if best is None:
        raise SourceError("no productList in the page; has the page changed?")
    return [scrub(product) for product in best]


def scrub(product: dict) -> dict:
    """Drop fields that hold personal data, keep everything else as served."""
    return {key: value for key, value in product.items() if key not in PERSONAL_FIELDS}


class Client:
    """A polite HTTP client: one request at a time, with a pause in between."""

    def __init__(self, delay: float = DELAY_SECONDS, retries: int = 3, timeout: float = 60.0):
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self._last_request_at: float | None = None

    def _wait_for_turn(self) -> None:
        if self._last_request_at is not None:
            remaining = self.delay - (time.monotonic() - self._last_request_at)
            if remaining > 0:
                time.sleep(remaining)

    def get(self, url: str) -> str:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        last_error: Exception | None = None
        for _ in range(self.retries):
            self._wait_for_turn()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read().decode("utf-8", errors="replace")
            except (urllib.error.URLError, TimeoutError) as error:
                last_error = error
            finally:
                self._last_request_at = time.monotonic()
        raise SourceError(f"{url} failed after {self.retries} attempts: {last_error}")

    def categories(self) -> list[Category]:
        return categories_from_sitemap(self.get(SITEMAP_URL))

    def products(self, category: Category, attempts: int = 3, backoff: float = 30.0) -> list[dict]:
        """Fetch a category's products.

        Under load the store sometimes answers 200 with a page that has no product
        list, and the same URL works a minute later. So a page without products is
        retried after a longer pause before it counts as a failure.
        """
        for attempt in range(1, attempts + 1):
            try:
                return extract_products(self.get(category.url))
            except SourceError:
                if attempt == attempts:
                    raise
                time.sleep(backoff * attempt)
        raise AssertionError("unreachable")
