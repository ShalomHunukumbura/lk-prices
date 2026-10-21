from decimal import Decimal

import pytest

from lk_prices.sizes import Size, from_name, glomark_size, unit_price


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Anchor Plus Milk Powder 1-5Y 300G", Size(0.3, "kg", "name")),
        ("Coco Fresh Coconut Milk Powder 800 G", Size(0.8, "kg", "name")),
        ("Farmlanka Goat Milk Plain 500Ml", Size(0.5, "l", "name")),
        ("Elephant House Ginger Beer 1.5L", Size(1.5, "l", "name")),
        ("Samba Rice 5Kg", Size(5.0, "kg", "name")),
        # Multipacks count every pack.
        ("Nescafe 3In1 Sachet 10X18G", Size(0.18, "kg", "name")),
        ("Red Bull Energy Drink 4 Pack 4X250Ml", Size(1.0, "l", "name")),
        ("Dreamron 5 Minutes Black Henna 2X 8G", Size(0.016, "kg", "name")),
        ("Knorr Beef Broth 28G*4 112G", Size(0.112, "kg", "name")),
        # Tea is priced by weight, not by the bag count.
        ("Dilmah Tea Green Pure Ceylon 20S 40G", Size(0.04, "kg", "name")),
        # Counts when there is nothing else.
        ("Pampers Baby Pants Large 20'S", Size(20.0, "each", "name")),
        ("Besto Eggs 10Pcs", Size(10.0, "each", "name")),
    ],
)
def test_sizes_from_names(name, expected):
    size = from_name(name)
    assert size is not None
    assert size.unit == expected.unit
    assert size.quantity == pytest.approx(expected.quantity)


@pytest.mark.parametrize(
    "name",
    [
        "Chicken Boneless Breast",
        "Gillette Vector Razor",
        # Bundles have no single size.
        "Sunsilk Thick & Long Shampoo 180Ml+Conditioner 180Ml",
        "Drools Chicken And Egg Adult Food 3Kg + 1.2 Kg Free",
        "Pears Rathmal Eco Pack 325G+Soap 70G",
    ],
)
def test_no_size_is_better_than_a_wrong_one(name):
    assert from_name(name) is None


def test_weighed_items_use_the_store_quantity():
    # Glomark prices a whole chicken for 1300 g, and the name says nothing.
    size = glomark_size("Nelna Whole Chicken Without Giblet", "g", 1300)
    assert size == Size(1.3, "kg", "weighed")
    assert unit_price(Decimal("1807.00"), size) == Decimal("1390.00")


def test_unit_items_fall_back_to_the_name():
    assert glomark_size("Samba Rice 5Kg", "unit", 1) == Size(5.0, "kg", "name")


def test_unit_price():
    assert unit_price(Decimal("990.00"), Size(0.3, "kg", "name")) == Decimal("3300.00")
    assert unit_price(Decimal("990.00"), None) is None
