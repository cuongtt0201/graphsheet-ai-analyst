"""Foreign-key detection: still exact after being made fast.

detect_keys grew to 5s on a 20-sheet workbook, which matters because it runs on
every upload. The speedup came from skipping work, not from sampling — a
non-unique column can never be a parent, and a parent smaller than
MIN_CONTAINMENT × the child cannot possibly contain enough of it. Both are
provable, so the result set must be byte-identical to the exhaustive scan.

That equivalence is what these tests hold down; a future "optimization" that
starts approximating will fail here rather than quietly losing a link.
"""

import numpy as np
import pandas as pd

from app.data import relations
from app.data.relations import MIN_CONTAINMENT, detect_keys


def _reference(dataframes: dict) -> set:
    """Exhaustive O(all pairs) scan with no pruning — the definition the fast
    path must agree with."""
    sets = {}
    for sid, df in dataframes.items():
        for col in df.columns:
            s = df[col].dropna()
            if s.empty or pd.api.types.is_datetime64_any_dtype(s):
                continue
            vals = set(s.astype("string"))
            if not 1 < len(vals) <= 100_000:
                continue
            sets[(sid, col)] = (vals, len(vals) == len(s))

    out = set()
    for (c_sid, c_col), (c_vals, _) in sets.items():
        for (p_sid, p_col), (p_vals, p_unique) in sets.items():
            if c_sid == p_sid or not p_unique or not c_vals:
                continue
            if len(c_vals & p_vals) / len(c_vals) >= MIN_CONTAINMENT:
                out.add((c_sid, c_col, p_sid, p_col))
    return out


def _workbook(seed=0):
    """A shape real files have: a fact table referencing two dimensions, plus
    an unrelated sheet that must NOT produce links."""
    rng = np.random.default_rng(seed)
    products = pd.DataFrame({
        "ma_sp": [f"SP{i:03d}" for i in range(60)],
        "ten_sp": [f"San pham {i}" for i in range(60)],
        "gia": rng.integers(10, 900, 60),
    })
    stores = pd.DataFrame({
        "ma_ch": [f"CH{i:02d}" for i in range(12)],
        "ten_ch": [f"Cua hang {i}" for i in range(12)],
    })
    sales = pd.DataFrame({
        "ma_dh": [f"DH{i:05d}" for i in range(4000)],
        "ma_sp": rng.choice(products["ma_sp"], 4000),
        "ma_ch": rng.choice(stores["ma_ch"], 4000),
        "sl": rng.integers(1, 9, 4000),
    })
    unrelated = pd.DataFrame({
        "ma_nv": [f"NV{i:03d}" for i in range(40)],
        "phong": rng.choice(["KT", "KD", "SX"], 40),
    })
    return {"f::SanPham": products, "f::CuaHang": stores,
            "f::BanHang": sales, "f::NhanSu": unrelated}


def test_fast_path_finds_exactly_what_the_exhaustive_scan_finds():
    dfs = _workbook()
    found = {(l["child"], l["child_col"], l["parent"], l["parent_col"]) for l in detect_keys(dfs)}
    assert found == _reference(dfs)


def test_the_real_foreign_keys_are_among_them():
    """Guards against the equivalence test passing because BOTH sides broke."""
    found = {(l["child"], l["child_col"], l["parent"], l["parent_col"]) for l in detect_keys(_workbook())}
    assert ("f::BanHang", "ma_sp", "f::SanPham", "ma_sp") in found
    assert ("f::BanHang", "ma_ch", "f::CuaHang", "ma_ch") in found


def test_unrelated_sheet_produces_no_link():
    links = detect_keys(_workbook())
    assert not any(l["parent"] == "f::NhanSu" or l["child"] == "f::NhanSu" for l in links)


def test_size_prefilter_never_discards_a_real_link():
    """The prefilter is the one pruning step that could lose a result if the
    inequality were wrong, so a child larger than its parent — exactly the case
    it reasons about — is checked directly."""
    parent = pd.DataFrame({"k": [f"K{i}" for i in range(50)]})
    child = pd.DataFrame({"k": [f"K{i % 50}" for i in range(5000)], "v": range(5000)})
    links = detect_keys({"p": parent, "c": child})
    assert any(l["child"] == "c" and l["parent"] == "p" for l in links)


def test_partial_containment_below_threshold_is_rejected():
    parent = pd.DataFrame({"k": [f"K{i}" for i in range(50)]})
    child = pd.DataFrame({"k": [f"K{i}" for i in range(100)]})   # only 50% covered
    assert not any(l["parent"] == "p" for l in detect_keys({"p": parent, "c": child}))


def test_non_unique_parent_is_never_offered():
    """Joining onto a repeated key multiplies rows; such a pair must not be
    proposed as a foreign key in the first place."""
    repeated = pd.DataFrame({"k": ["A", "A", "B", "B", "C", "C"]})
    child = pd.DataFrame({"k": ["A", "B", "C"] * 10})
    assert not any(l["parent"] == "p" for l in detect_keys({"p": repeated, "c": child}))


def test_single_sheet_and_empty_input_are_no_ops():
    assert detect_keys({}) == []
    assert detect_keys({"only": pd.DataFrame({"a": [1, 2]})}) == []


def test_result_is_capped_and_ordered_strongest_first():
    dfs = _workbook()
    links = detect_keys(dfs)
    assert len(links) <= relations.MAX_KEY_CANDIDATES
    assert links == sorted(links, key=lambda l: (l["containment"], l["rows_per_key"]), reverse=True)
