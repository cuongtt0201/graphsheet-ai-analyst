"""Vietnamese text / character-encoding repair for uploaded files.

Two distinct problems, two fixes:
  1. A CSV saved in a non-UTF-8 charset (cp1258, cp1252, latin-1). Read with the
     wrong codec, Vietnamese comes out as garbage. Fix: detect the charset from
     the bytes (charset_normalizer) and decode with it.
  2. "Mojibake" — text that was UTF-8 but got decoded as latin-1 somewhere
     upstream, so "á" became "Ã¡", "đ" became "Ä'". Fix: re-encode latin-1 then
     decode utf-8. Applied ONLY when it demonstrably reduces the tell-tale
     Ã/Â/Ä/Æ noise, so correct text is never corrupted.

xlsx read via calamine is already proper Unicode, so #2's marker check finds
nothing there and it's a no-op — the repair only touches genuinely broken text.
"""

from __future__ import annotations

import io

import pandas as pd

# Substrings that flag Vietnamese UTF-8-misread-as-latin1 mojibake. Vietnamese
# letters live at U+00C0+ / U+1Exx; when their multi-byte UTF-8 is misread as
# latin-1 you get sequences full of Ã/Â/Ä/Æ and U+0080–U+00BF punctuation.
_SUSPECT = ("Ã", "Â", "Ä", "Æ", "áº", "á»", "Ã©", "Ã¡", "Ä‘")


def _noise(t: str) -> int:
    # C1 controls + Latin-1 punctuation/symbols (U+0080–U+00BF) barely occur in
    # real Vietnamese text but flood mojibake — a reliable before/after signal.
    return sum(1 for ch in t if 0x80 <= ord(ch) <= 0xBF)


def repair_text(s):
    """Repair one mojibaked string; leave everything else untouched."""
    if not isinstance(s, str) or not any(m in s for m in _SUSPECT):
        return s
    try:
        candidate = s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
    # Accept only if the roundtrip actually cleaned up the mojibake markers.
    return candidate if _noise(candidate) < _noise(s) else s


def fix_mojibake_df(df: pd.DataFrame) -> pd.DataFrame:
    """Repair mojibaked column names + object-column values in place-ish.
    Cheap: a column is only rewritten if a sample of it shows mojibake."""
    df = df.rename(columns={c: repair_text(c) if isinstance(c, str) else c for c in df.columns})
    for col in df.select_dtypes(include=["object", "string"]).columns:
        sample = df[col].dropna().astype(str).head(50)
        if sample.map(lambda v: any(m in v for m in _SUSPECT)).any():
            df[col] = df[col].map(repair_text)
    return df


def read_csv_smart(content: bytes, **kwargs) -> pd.DataFrame:
    """Read a CSV with charset auto-detection so non-UTF-8 Vietnamese files
    don't come in as garbage. Falls back to utf-8 (then latin-1) on any trouble."""
    enc = "utf-8"
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(content).best()
        if best and best.encoding:
            enc = best.encoding
    except Exception:
        pass
    for candidate in (enc, "utf-8-sig", "utf-8", "latin-1"):
        try:
            df = pd.read_csv(io.BytesIO(content), encoding=candidate, **kwargs)
            return fix_mojibake_df(df)
        except (UnicodeDecodeError, LookupError):
            continue
    # Last resort: replace undecodable bytes rather than fail the upload.
    df = pd.read_csv(io.BytesIO(content), encoding="utf-8", encoding_errors="replace", **kwargs)
    return fix_mojibake_df(df)
