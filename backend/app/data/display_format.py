"""Per-column display formats for the spreadsheet grid.

The grid used to render every number the same way, which is wrong in both
directions: a revenue column loses its thousands separators and becomes an
unreadable run of digits, while a year or an order code would be *damaged* by
adding them ("2026" -> "2.026"). The distinction is not in the values, it is in
what the column means, so it is decided here from the profile role and the unit
the semantics pass recorded, and never guessed from the digits.
"""

from __future__ import annotations

import re
from typing import Any

# Currency units the semantics pass emits, plus the symbols that reach us from
# raw headers. Matched on a normalized, lowercased token.
_CURRENCY_TOKENS = {
    "vnd", "vnđ", "đ", "d", "dong", "đồng", "dồng", "usd", "$", "eur", "€",
    "jpy", "¥", "gbp", "£", "tiền", "tien", "money", "currency", "₫",
}

_PERCENT_TOKENS = {"%", "percent", "pct", "phần trăm", "phan tram"}

# Column names that mean money even when no unit was recorded. Whole-word
# matched, for the same reason the identifier hints are: "chiphi" is fine but a
# substring hit inside an unrelated word is not.
_MONEY_NAME_HINTS = (
    "doanh thu", "doanh số", "doanh so", "chi phí", "chi phi", "lợi nhuận",
    "loi nhuan", "giá", "gia", "giá trị", "gia tri", "thành tiền", "thanh tien",
    "tiền", "tien", "revenue", "cost", "profit", "price", "amount", "total",
    "sales", "budget", "salary", "lương", "luong",
)

_SPLIT = re.compile(r"[^0-9a-zà-ỹ%$€¥£₫]+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return [t for t in _SPLIT.split(str(text).strip().lower()) if t]


def _is_currency_unit(unit: str | None) -> bool:
    if not unit:
        return False
    return any(t in _CURRENCY_TOKENS for t in _tokens(unit))


def _is_percent_unit(unit: str | None) -> bool:
    if not unit:
        return False
    return any(t in _PERCENT_TOKENS for t in _tokens(unit))


def _name_says_money(name: str) -> bool:
    tokens = _tokens(name)
    for hint in _MONEY_NAME_HINTS:
        parts = _tokens(hint)
        n = len(parts)
        if n and any(tokens[i:i + n] == parts for i in range(len(tokens) - n + 1)):
            return True
    return False


def column_formats(profile: dict[str, Any], semantics: dict[str, Any] | None = None) -> dict[str, str]:
    """Map column name -> one of "currency" | "percent" | "id" | "plain".

    "plain" is the caller's existing behaviour and is the default: a column is
    only given separators when something actually says it is money. Guessing
    from magnitude would put separators on years and postcodes.
    """
    units: dict[str, str] = {}
    for measure in ((semantics or {}).get("target_measures") or []):
        col, unit = measure.get("column"), measure.get("unit")
        if col and unit:
            units[str(col)] = str(unit)

    out: dict[str, str] = {}
    for entry in (profile.get("column_profiles") or []):
        name = str(entry.get("name", ""))
        if not name:
            continue
        role = entry.get("role")

        if role == "id":
            out[name] = "id"
            continue
        if role in ("date", "category", "text", "mostly_empty"):
            out[name] = "plain"
            continue

        unit = units.get(name)
        if _is_percent_unit(unit):
            # A "%" column holds either a fraction (0.85) or an already-scaled
            # number (85). A percent format multiplies by 100, so applying it to
            # the second kind turns 85 into "8500%". Only the fraction form can
            # be formatted; the other is already readable as written.
            top = entry.get("max")
            out[name] = "percent" if isinstance(top, (int, float)) and top <= 1 else "plain"
        elif _is_currency_unit(unit) or _name_says_money(name):
            out[name] = "currency"
        else:
            out[name] = "plain"
    return out
