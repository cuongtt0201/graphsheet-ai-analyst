"""Entity resolution and placeholder detection — runs BEFORE every other
analysis in the data layer.

Ordering matters and is not negotiable: if "Miền Bắc", "MIỀN BẮC" and
"miền bắc" are still three separate values, then every group-by downstream is
wrong — concentration shares, Simpson checks, rankings, the dashboard's own
charts. Normalising after those have run fixes the labels but not the numbers.

Two jobs, deliberately split by how confident we can be:

  merge  — values whose NORMALISED form is byte-identical are provably the same
           label typed differently (case, accents, spacing, punctuation). Safe
           to merge automatically.
  report — abbreviations ("MB" vs "Miền Bắc") and near-misses are NOT merged.
           They cannot be proven equal from the data alone, and wrongly fusing
           two real stores corrupts every total silently. They are surfaced for
           the user (and the LLM) to judge.

Placeholder detection closes the failure where "0000" was reported as the
chain's biggest customer: a missing-data marker treated as a real entity.
"""

from __future__ import annotations

import logging
import re
import unicodedata

import pandas as pd

logger = logging.getLogger(__name__)

MAX_DISTINCT = 500          # above this a column is free text, not an entity set
MIN_ROWS_FOR_CHECK = 5

# A value is a placeholder because of its STRUCTURE, not its vocabulary.
#
# Word lists do not generalise: "Khác", "Other" and "Unknown" are legitimate
# category names in real data — this system's own chart condensing creates a
# "Khác" bucket, and a live dashboard had 724.7M VND of genuine revenue under
# it. Auto-excluding those would delete real money from every ranking.
#
# What DOES generalise is that a placeholder carries no distinguishing
# information: it is empty, it is punctuation only, or it is one character
# repeated. That holds in any language and for any file.
_SENTINEL_PUNCT = re.compile(r"^[\s\-_.=/\\|*#?]*$")
_SENTINEL_REPEATED = re.compile(r"^(.)\1{1,}$")   # "000", "----", "XXXX", "999"

# The only exceptions are machine-emitted error tokens, which are never a
# human's chosen label for anything.
_SPREADSHEET_ERRORS = {"na", "nan", "null", "nil", "#na", "#value", "#ref", "#div0", "#name"}


def normalize_label(value) -> str:
    """Canonical form used ONLY for comparison, never for display: lowercase,
    accents stripped, punctuation and spacing removed.

    "TP.HCM" / "tp hcm" / "Tp. HCM" all collapse to "tphcm". Two originals
    sharing a normalised form are the same label typed differently — that is
    the strongest evidence available without guessing."""
    if value is None:
        return ""
    s = str(value).strip().lower()
    # NFD splits accented characters into base + combining mark, so dropping
    # the marks turns "miền bắc" into "mien bac" without a lookup table.
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.replace("đ", "d")
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def is_placeholder(value) -> bool:
    """True only when a value is STRUCTURALLY incapable of naming something.

    Deliberately conservative. A false positive here silently removes a real
    category from every ranking and total, which is far worse than leaving a
    placeholder in — that one is at least visible on screen."""
    if value is None:
        return True
    raw = str(value).strip()
    if not raw:
        return True
    if _SENTINEL_PUNCT.match(raw):
        return True
    # A single repeated character ("0000", "----", "XXXX") distinguishes
    # nothing, whatever the character is.
    if _SENTINEL_REPEATED.match(raw):
        return True
    norm = normalize_label(raw)
    if not norm:
        return True
    return norm in _SPREADSHEET_ERRORS


def _entity_columns(df: pd.DataFrame) -> list[str]:
    out = []
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]) or pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        try:
            n = df[col].nunique(dropna=True)
        except Exception:  # noqa: BLE001
            continue
        if 1 < n <= MAX_DISTINCT:
            out.append(col)
    return out


def find_variants(series: pd.Series) -> dict[str, list[str]]:
    """{canonical: [other spellings]} for values that normalise identically.
    The canonical form is the most frequent original spelling — the one the
    user's own data uses most, rather than an invented "clean" version."""
    counts = series.dropna().astype("string").value_counts()
    groups: dict[str, list[tuple[str, int]]] = {}
    for raw, n in counts.items():
        key = normalize_label(raw)
        if not key:
            continue
        groups.setdefault(key, []).append((str(raw), int(n)))

    variants = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: m[1], reverse=True)
        canonical = members[0][0]
        variants[canonical] = [m[0] for m in members[1:]]
    return variants


def find_suspected_aliases(series: pd.Series, max_pairs: int = 6) -> list[tuple[str, str]]:
    """Abbreviation candidates: a SHORT value whose letters appear, in order, as
    the initials of a longer value ("MB" ~ "Miền Bắc", "HCM" ~ "Hồ Chí Minh").

    Reported only, never merged. "MB" could equally be a store code, and fusing
    two genuinely different entities would understate one and inflate the other
    with nothing on screen to reveal it."""
    values = [str(v) for v in series.dropna().astype("string").unique()[:MAX_DISTINCT]]
    shorts = [v for v in values if len(normalize_label(v)) <= 4]
    longs = [v for v in values if len(normalize_label(v)) > 4]
    if not shorts or not longs:
        return []

    pairs = []
    for s in shorts:
        s_norm = normalize_label(s)
        if not s_norm:
            continue
        for l in longs:
            initials = "".join(w[0] for w in re.split(r"\s+", normalize_label_words(l)) if w)
            if initials and initials == s_norm:
                pairs.append((s, l))
                break
        if len(pairs) >= max_pairs:
            break
    return pairs


def normalize_label_words(value) -> str:
    """Like normalize_label but KEEPS word boundaries, so initials can be taken."""
    s = unicodedata.normalize("NFD", str(value).strip().lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.replace("đ", "d")
    return re.sub(r"[^a-z0-9\s]+", " ", s).strip()


def resolve_entities(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Merge provably-identical spellings in place; return (df, report lines).

    Only the merge is applied. Suspected abbreviations and placeholders are
    reported so the model and the user can act on them with full information.
    """
    if df is None or len(df) < MIN_ROWS_FOR_CHECK:
        return df, []

    out = df
    notes: list[str] = []
    copied = False

    for col in _entity_columns(df):
        try:
            variants = find_variants(out[col])
        except Exception:  # noqa: BLE001
            continue

        if variants:
            mapping = {alt: canon for canon, alts in variants.items() for alt in alts}
            if not copied:
                out = out.copy()
                copied = True
            out[col] = out[col].astype("string").replace(mapping)
            shown = "; ".join(
                f'"{canon}" ← {", ".join(chr(34) + a + chr(34) for a in alts[:3])}'
                for canon, alts in list(variants.items())[:3]
            )
            notes.append(
                f'Cột "{col}": đã gộp {len(mapping)} cách viết khác nhau của cùng một giá trị ({shown}).'
            )

        aliases = find_suspected_aliases(out[col])
        if aliases:
            shown = ", ".join(f'"{s}" ≈ "{l}"' for s, l in aliases[:3])
            notes.append(
                f'Cột "{col}": NGHI NGỜ viết tắt cùng nghĩa ({shown}) — CHƯA gộp, '
                f"hãy tính riêng và nhắc người dùng nếu con số bị chia nhỏ bất thường."
            )

    return out, notes


def find_placeholders(df: pd.DataFrame) -> list[str]:
    """Report placeholder values that carry real weight in an entity column.

    A "0000" holding 65% of revenue is not a customer — it is missing data
    wearing a customer's clothes, and it will otherwise be reported as the
    chain's top account."""
    if df is None or df.empty:
        return []

    notes = []
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    measure = max(numeric, key=lambda c: abs(float(df[c].sum() or 0)), default=None)

    for col in _entity_columns(df):
        try:
            values = df[col].dropna().astype("string")
        except Exception:  # noqa: BLE001
            continue
        bad = sorted({str(v) for v in values.unique() if is_placeholder(v)})
        if not bad:
            continue

        mask = values.isin(bad)
        row_share = float(mask.mean())
        detail = f"{row_share * 100:.0f}% số dòng"

        # A placeholder that also carries money is far more dangerous than one
        # that only pads the row count.
        if measure:
            try:
                total = float(df[measure].sum())
                held = float(df.loc[mask.reindex(df.index, fill_value=False), measure].sum())
                if total:
                    detail += f", chiếm {held / total * 100:.0f}% {measure}"
            except Exception:  # noqa: BLE001
                pass

        listed = ", ".join(f'"{b}"' for b in bad[:4])
        notes.append(
            f'Cột "{col}": {listed} là GIÁ TRỊ RỖNG/GIẢ, không phải thực thể thật ({detail}). '
            f"LOẠI BỎ khỏi mọi bảng xếp hạng và biểu đồ top N."
        )
    return notes
