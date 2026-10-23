"""One name per item over ten years of reports.

CBSL renamed items as it went: "Dhal (Small)" became "Dhal" in September 2020
and "Red Dhal" in December 2022; "Sprat" became "Sprat (Imp)"; "Kekulu Red"
became "Kekulu (Red)". Each rename was checked against the data before being
listed here: the old and new names never appear in the same report, and the
price carries on across the switch.

The section and the unit belong to the item, not to the row it was printed in
(11 January 2019's vegetable table prints no units at all): green
chillies sat under "other" until December 2020 and under "vegetables" after, and
a few reports in January 2021 lost the FRUITS heading.

A name that is in neither list is an error (see checks.py), so a new name is
looked at and mapped on purpose instead of quietly becoming a new item.
"""

from __future__ import annotations

# Canonical name -> section.
CATALOGUE = {
    # vegetables
    "Beans": "vegetables",
    "Brinjal": "vegetables",
    "Cabbage": "vegetables",
    "Carrot": "vegetables",
    "Green Chilli": "vegetables",
    "Lime": "vegetables",
    "Pumpkin": "vegetables",
    "Snake gourd": "vegetables",
    "Tomato": "vegetables",
    # other staples
    "Big Onion (Imp)": "other",
    "Big Onion (Local)": "other",
    "Coconut (Avg.)": "other",
    "Coconut oil": "other",
    # Whole dhal, not split red dhal: only reported in April 2020, when markets were
    # mostly shut and "Dhal (Small)" wasn't. A different product, so not merged.
    "Dhal (Whole)": "other",
    "Dried Chilli (Imp)": "other",
    "Egg (White)": "other",
    "Katta (Imp)": "other",
    "Potato (Imp)": "other",
    "Potato (Local)": "other",
    "Red Dhal": "other",
    "Red Onion (Imp)": "other",
    "Red Onion (Local)": "other",
    "Sprat (Imp)": "other",
    "Sugar (White)": "other",
    # fruits
    "Apple (Imp)": "fruits",
    "Banana (Sour)": "fruits",
    "Orange (Imp)": "fruits",
    "Papaw": "fruits",
    "Pineapple": "fruits",
    # rice
    "Kekulu (Red)": "rice",
    "Kekulu (White)": "rice",
    "Kekulu (White) (Imp)": "rice",
    "Nadu": "rice",
    "Nadu (Imp)": "rice",
    "Ponni Samba (Imp)": "rice",
    "Samba": "rice",
    # fish
    "Balaya": "fish",
    "Hurulla": "fish",
    "Kelawalla": "fish",
    "Linna": "fish",
    "Paraw": "fish",
    "Salaya": "fish",
    "Thalapath": "fish",
}

# Older or misspelt name -> canonical name, with when CBSL switched.
# (Hyphens are already gone by this point: "Big-Onion" is read as "Big Onion".)
ALIASES = {
    # Plurals used in late 2019 and early 2020.
    "Big Onions (Imp)": "Big Onion (Imp)",
    "Big Onions (local)": "Big Onion (Local)",
    "Dried Chillies (Imp)": "Dried Chilli (Imp)",
    "Eggs": "Egg (White)",
    "Green Chillies": "Green Chilli",
    "Potatoes (Imp)": "Potato (Imp)",
    "Potatoes (local)": "Potato (Local)",
    "Red Onions (Imp)": "Red Onion (Imp)",
    "Red Onions (local)": "Red Onion (Local)",
    "Sprats": "Sprat (Imp)",
    "Tomatoes": "Tomato",
    "Dhal (whole)": "Dhal (Whole)",
    "Apple": "Apple (Imp)",  # 2018
    "Big Onion (local)": "Big Onion (Local)",  # until 2020-09-21
    "Coconut": "Coconut (Avg.)",  # until 2020-09-29
    "Dhal (Small)": "Red Dhal",  # until 2020-09-21
    "Dhal": "Red Dhal",  # 2020-09-22 to 2022-12-14
    "Dried Chillie (Imp)": "Dried Chilli (Imp)",  # until 2020-10-28
    "Egg": "Egg (White)",  # until 2020-09-21
    "Green Chillie": "Green Chilli",  # until 2020-10-28
    "Katta": "Katta (Imp)",  # until 2023-12-22
    "Kekulu Red": "Kekulu (Red)",  # until 2020-09-21
    "Kekulu White": "Kekulu (White)",  # until 2020-09-21
    "Potato (local)": "Potato (Local)",  # until 2020-09-21
    "Red Onion (local)": "Red Onion (Local)",  # until 2020-09-21
    "Sprat": "Sprat (Imp)",  # until 2022-03-24
    "Sugar": "Sugar (White)",  # until 2020-09-21
}


# Every item is priced per kg except these. Checked against every report: no item
# has ever been printed with two different units.
UNITS = {
    "Apple (Imp)": "each",
    "Coconut (Avg.)": "nut",
    "Coconut oil": "l",
    "Egg (White)": "each",
    "Orange (Imp)": "each",
}


def unit(name: str) -> str | None:
    """The unit an item is priced in, or None if the item isn't in the catalogue."""
    return UNITS.get(name, "kg") if name in CATALOGUE else None


def canonical(name: str) -> str | None:
    """The canonical name for an item as printed, or None if it isn't known."""
    if name in CATALOGUE:
        return name
    return ALIASES.get(name)


def section(name: str) -> str | None:
    return CATALOGUE.get(name)
