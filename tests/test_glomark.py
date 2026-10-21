import pytest

from lk_prices.glomark import (
    Category,
    Client,
    SourceError,
    categories_from_sitemap,
    extract_products,
)


def test_sitemap_gives_category_pages_only(fixture_text):
    categories = categories_from_sitemap(fixture_text("glomark_sitemap.xml"))
    # Department (/dp/), subcategory (/sc/) and product (/p/) pages are skipped,
    # and the category listed twice comes back once.
    assert [c.id for c in categories] == [118, 119, 120]
    assert categories[0].url == "https://glomark.lk/bakery/bread/c/118"


def test_empty_sitemap_is_an_error():
    with pytest.raises(SourceError):
        categories_from_sitemap("<urlset></urlset>")


def test_takes_the_real_list_not_the_placeholder(products):
    # The page declares `productList = []` before assigning the data.
    assert [p["id"] for p in products] == [112924, 112923, 13843]
    assert products[0]["name"] == "Roast Bread Large"
    assert products[0]["priceDetails"]["price"] == 125


def test_personal_data_is_removed(products):
    assert all("brandResponsible" not in p for p in products)
    # Everything else is kept as served.
    assert "branchStocks" in products[0]


def test_page_without_products_is_an_error():
    with pytest.raises(SourceError):
        extract_products("<html><body>Something Went Wrong</body></html>")


def test_page_without_products_is_retried(fixture_text):
    pages = iter(["<html>Something Went Wrong</html>", fixture_text("glomark_category.html")])
    client = Client(delay=0)
    client.get = lambda url: next(pages)
    products = client.products(Category(118, "https://glomark.lk/bakery/bread/c/118"), backoff=0)
    assert len(products) == 3


def test_page_that_never_has_products_fails():
    client = Client(delay=0)
    client.get = lambda url: "<html>Something Went Wrong</html>"
    with pytest.raises(SourceError):
        client.products(Category(118, "https://glomark.lk/bakery/bread/c/118"), backoff=0)
