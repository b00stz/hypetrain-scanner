"""Known-ticker whitelist + regex-based extraction (deliberately not NLP: a match must be an
exact, case-sensitive token against a curated symbol list, which keeps false positives low)."""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable

# $TICKER (cashtag) or a bare 1-5 letter uppercase word, both matched as whole tokens.
_CASHTAG_RE = re.compile(r"\$([A-Z]{1,5})\b")
_BARE_TOKEN_RE = re.compile(r"(?<![\w$])([A-Z]{1,5})(?![\w])")


def load_known_tickers(csv_path: str, excluded: Iterable[str] = ()) -> set[str]:
    excluded_set = {t.upper() for t in excluded}
    tickers: set[str] = set()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            symbol = row["ticker"].strip().upper()
            if symbol and symbol not in excluded_set:
                tickers.add(symbol)
    return tickers


def extract_tickers(text: str, known_tickers: set[str]) -> set[str]:
    """Return the set of known tickers mentioned in text.

    Cashtags ($GME) are always trusted if in the known list. Bare uppercase tokens (GME) are
    also matched against the known list, which is how we avoid needing free-text NLP while still
    catching plain mentions.
    """
    if not text:
        return set()

    found: set[str] = set()
    for match in _CASHTAG_RE.finditer(text):
        symbol = match.group(1)
        if symbol in known_tickers:
            found.add(symbol)

    for match in _BARE_TOKEN_RE.finditer(text):
        symbol = match.group(1)
        if symbol in known_tickers:
            found.add(symbol)

    return found


DEFAULT_TICKERS_CSV = str(Path(__file__).resolve().parent.parent / "data" / "tickers.csv")
