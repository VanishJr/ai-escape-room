"""Digit normalisation shared by the intent parser and the secrets guard.

Players (and models) can write a code in many ways: "7294", "7 2 9 4", "7-2-9-4",
full-width "７２９４" or with invisible zero-width characters in between. Both the
parser (reading what the player typed) and the guard (checking what the narrator
wrote) first reduce all of these to plain ASCII digit runs.
"""

from __future__ import annotations

import re
import unicodedata

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
# up to three non-letter characters between two digits: spaces, commas, dots, dashes, slashes, "·", "_"
_SEPARATOR_BETWEEN_DIGITS = re.compile(r"(?<=[0-9])(?:[^\w\n]|_){1,3}(?=[0-9])")
CODE_PATTERN = re.compile(r"(?<![0-9])[0-9]{4}(?![0-9])")


def ascii_digits(text: str) -> str:
    """NFKC-normalise and map every Unicode decimal digit (Arabic-Indic, full-width, ...) to 0-9."""
    text = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)
    return "".join(str(unicodedata.decimal(ch)) if unicodedata.decimal(ch, None) is not None else ch for ch in text)


def join_digits(text: str) -> str:
    """ascii_digits() plus removal of separators between digits: "7 - 2 9.4" -> "7294"."""
    return _SEPARATOR_BETWEEN_DIGITS.sub("", ascii_digits(text))


def four_digit_codes(text: str) -> list[str]:
    return CODE_PATTERN.findall(join_digits(text))
