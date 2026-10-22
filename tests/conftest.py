from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text():
    def load(name: str) -> str:
        return (FIXTURES / name).read_text()

    return load


class FakeClient:
    """Serves saved listing pages and PDFs instead of calling CBSL."""

    def __init__(self, pages: dict[int, str], pdfs: dict[str, bytes] | None = None):
        self.pages = pages
        self.pdfs = pdfs or {}
        self.listing_calls: list[int] = []

    def listing(self, page: int) -> str:
        self.listing_calls.append(page)
        return self.pages[page]

    def pdf(self, report) -> bytes:
        from lk_prices.cbsl import SourceError

        if report.url not in self.pdfs:
            raise SourceError(f"{report.url}: HTTP 404")
        return self.pdfs[report.url]


@pytest.fixture
def fake_client():
    return FakeClient
