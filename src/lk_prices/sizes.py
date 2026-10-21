"""Pack sizes, so prices can be compared per kg, per litre or per item.

A 400 g pack at Rs 990 and a 1 kg pack at Rs 2,300 can't be compared until both
are in Rs per kg. Stores rarely give the size as data, so it is read from:

1. The store's own fields, for weighed items such as chicken or fish
   (Glomark: unit "g" and displayQuantity 1300 means the price is for 1.3 kg).
2. The product name: "Anchor Milk Powder 400G", "Harpic 10X 500Ml",
   "Dilmah Green Tea 20S 40G" (20 bags, 40 g: the weight is what counts).

Everything is converted to kg, litres or items. A name with no recognisable size
gets no unit price, which is better than a wrong one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

# Each unit as written in names, and what it converts to.
_MASS = {"kg": 1.0, "g": 0.001, "gm": 0.001, "gms": 0.001, "gram": 0.001, "grams": 0.001}
_VOLUME = {
    "l": 1.0,
    "ltr": 1.0,
    "litre": 1.0,
    "litres": 1.0,
    "liter": 1.0,
    "liters": 1.0,
    "ml": 0.001,
    "cl": 0.01,
}
# fmt: off
_COUNT = {
    "s", "pc", "pcs", "piece", "pieces", "sachet", "sachets", "bag", "bags", "tabs",
    "tablets", "caps", "capsules", "rolls", "pack", "packs", "pkts", "nos", "units",
}
# fmt: on

_NUMBER = r"(\d+(?:\.\d+)?)"
_MEASURE_UNITS = "|".join(sorted([*_MASS, *_VOLUME], key=len, reverse=True))
_COUNT_UNITS = "|".join(sorted(_COUNT, key=len, reverse=True))
# "10X18G", "4X250Ml", "5*8G", "2X 8G"
_MULTIPACK = re.compile(rf"(?i)\b(\d+)\s*[x×*]\s*{_NUMBER}\s*({_MEASURE_UNITS})\b")
# "28G*4", the same thing written the other way round
_MULTIPACK_REVERSED = re.compile(rf"(?i){_NUMBER}\s*({_MEASURE_UNITS})\s*[x×*]\s*(\d+)\b")
# "400G", "1.5 L", "500Ml"
_MEASURE = re.compile(rf"(?i)(?<![\d.]){_NUMBER}\s*({_MEASURE_UNITS})\b")
# "20S", "12Pcs", "20'S", "10 Pack"
_COUNTED = re.compile(rf"(?i)(?<![\d.])(\d+)\s*'?\s*({_COUNT_UNITS})\b")


@dataclass(frozen=True)
class Size:
    quantity: float  # in `unit`
    unit: str  # "kg", "l" or "each"
    source: str  # "weighed" or "name"


def _base(amount: float, unit: str) -> tuple[float, str]:
    unit = unit.lower()
    if unit in _MASS:
        return amount * _MASS[unit], "kg"
    return amount * _VOLUME[unit], "l"


def from_name(name: str) -> Size | None:
    """Read a pack size from a product name, or None if there isn't a clear one."""
    multi = _MULTIPACK.search(name)
    if multi:
        quantity, unit = _base(float(multi.group(2)), multi.group(3))
        return Size(int(multi.group(1)) * quantity, unit, "name")
    multi = _MULTIPACK_REVERSED.search(name)
    if multi:
        quantity, unit = _base(float(multi.group(1)), multi.group(2))
        return Size(int(multi.group(3)) * quantity, unit, "name")

    # A weight or volume wins over a count: "20S 40G" is priced by the 40 g.
    measures = _MEASURE.findall(name)
    if len(measures) > 1:
        # Bundles and free extras ("Shampoo 180Ml+Conditioner 180Ml",
        # "3Kg + 1.2 Kg Free") have no single size to compare.
        return None
    if measures:
        quantity, unit = _base(float(measures[0][0]), measures[0][1])
        return Size(quantity, unit, "name") if quantity > 0 else None

    counts = _COUNTED.findall(name)
    if counts:
        count = int(counts[-1][0])
        return Size(float(count), "each", "name") if count > 0 else None
    return None


def glomark_size(name: str, unit: str | None, display_quantity: float | None) -> Size | None:
    """Glomark gives weighed items a mass unit and quantity; the rest are 'unit'."""
    if unit and unit.lower() in _MASS and display_quantity:
        quantity, base = _base(display_quantity, unit)
        return Size(quantity, base, "weighed")
    return from_name(name)


def unit_price(price: Decimal | None, size: Size | None) -> Decimal | None:
    """Price per kg, per litre or per item, to the cent."""
    if price is None or size is None or size.quantity <= 0:
        return None
    return (price / Decimal(str(size.quantity))).quantize(Decimal("0.01"))
