"""The data-understanding layer, tested for GENERALITY rather than for one file.

The rule these tests enforce: the backend may only answer closed questions —
ones decidable from the data itself. Anything requiring world knowledge ("is
'Khác' a real category?", "is 60% concentration alarming?") belongs to the LLM
layer, and code that answers it will be wrong on some file we have not seen.

So the suite deliberately runs on domains the code has never been tuned for
(HR, sensors) and on pure noise, and asserts that nothing is invented.
"""

import numpy as np
import pandas as pd

from app.data.dispatcher import analyze, classify_columns
from app.data.entities import find_placeholders, is_placeholder, normalize_label, resolve_entities
from app.data.relations import detect_formulas, detect_keys


# ── entity resolution ───────────────────────────────────────────────────────

def test_merges_only_provably_identical_spellings():
    df = pd.DataFrame({
        "kv": ["Miền Bắc"] * 5 + ["MIỀN BẮC"] * 3 + ["miền bắc"] * 2 + ["Miền Nam"] * 4,
        "v": range(14),
    })
    out, notes = resolve_entities(df)
    counts = out["kv"].value_counts().to_dict()
    assert counts["Miền Bắc"] == 10        # all three spellings merged
    assert counts["Miền Nam"] == 4
    assert notes


def test_abbreviations_are_reported_but_never_merged():
    """"MB" might be "Miền Bắc" or a store code. Fusing two real entities
    understates one and inflates the other with nothing visible on screen, so
    the data layer reports and refuses to decide."""
    df = pd.DataFrame({"kv": ["Miền Bắc"] * 6 + ["MB"] * 4, "v": range(10)})
    out, notes = resolve_entities(df)
    assert set(out["kv"]) == {"Miền Bắc", "MB"}      # untouched
    assert any("NGHI NGỜ" in n for n in notes)


def test_normalisation_is_language_agnostic():
    assert normalize_label("TP.HCM") == normalize_label("tp hcm") == normalize_label("Tp. HCM")
    assert normalize_label("Miền Bắc") == normalize_label("MIEN BAC")


def test_placeholders_are_structural_not_vocabulary():
    """REGRESSION: a word list once flagged "Khác" as a placeholder — but the
    chart layer creates a "Khác" bucket itself, and a live dashboard held 724M
    VND of real revenue under it. Word lists do not generalise across
    languages or domains; structural degeneracy does."""
    for junk in ["0000", "000", "----", "XXXX", "", "   ", "-", "N/A", "null", "..."]:
        assert is_placeholder(junk), junk
    for real in ["Khác", "Other", "Unknown", "Chưa xác định", "Miền Bắc", "CH01", "A", "0"]:
        assert not is_placeholder(real), real


def test_placeholder_report_quantifies_the_damage():
    """Knowing "0000" exists is useless; knowing it holds 71% of revenue is
    what makes the LLM exclude it from rankings."""
    df = pd.DataFrame({
        "khach": ["0000"] * 4 + ["Cty A"] * 8 + ["Cty B"] * 8,
        "tien": [1_000_000] * 4 + [100_000] * 16,
    })
    notes = " ".join(find_placeholders(df))
    assert "0000" in notes
    assert "%" in notes and "tien" in notes


# ── relationships ───────────────────────────────────────────────────────────

def test_detects_real_formula_across_all_rows():
    rng = np.random.default_rng(0)
    n = 5_000
    sl = rng.integers(1, 20, n).astype(float)
    dg = rng.integers(1_000, 90_000, n).astype(float)
    df = pd.DataFrame({"So luong": sl, "Don gia": dg, "Thanh tien": sl * dg,
                       "Khac": rng.normal(size=n)})
    found = detect_formulas(df)
    assert any(f["target"] == "Thanh tien" and f["op"] == "×" for f in found)


def test_invents_no_formula_on_random_data():
    """The whole value of this module is that a reported relationship is real;
    one false positive would make every later 'price vs volume' split wrong."""
    rng = np.random.default_rng(1)
    df = pd.DataFrame({c: rng.normal(size=3_000) for c in "abcde"})
    assert detect_formulas(df) == []


def test_detects_foreign_key_by_containment():
    fact = pd.DataFrame({"ma": [f"S{i%5}" for i in range(200)], "v": range(200)})
    dim = pd.DataFrame({"ma": [f"S{i}" for i in range(5)], "ten": list("ABCDE")})
    links = detect_keys({"fact": fact, "dim": dim})
    assert any(l["child"] == "fact" and l["parent"] == "dim" for l in links)


def test_rejects_unrelated_columns_as_keys():
    a = pd.DataFrame({"x": [f"A{i}" for i in range(50)]})
    b = pd.DataFrame({"y": [f"B{i}" for i in range(50)]})
    assert detect_keys({"a": a, "b": b}) == []


# ── shape dispatcher ────────────────────────────────────────────────────────

def _hr_frame(n=4_000):
    """HR data — a domain none of this code was written against."""
    rng = np.random.default_rng(7)
    dept = rng.choice(["Marketing", "IT", "Kế toán"], n)
    tenure = rng.normal(5, 2, n)
    return pd.DataFrame({
        "Phong ban": dept,
        "Ngay nghi": np.where(dept == "Marketing", rng.normal(14, 3, n), rng.normal(5, 3, n)),
        "Tham nien": tenure,
        "Hieu suat": tenure * 8 + rng.normal(0, 3, n),
        "Ma NV": [f"NV{i:05d}" for i in range(n)],
        "Nhieu": rng.normal(0, 1, n),
    })


def test_works_on_a_domain_it_was_never_written_for():
    signals = analyze(_hr_frame(), {"column_profiles": [{"name": "Ma NV", "role": "id"}]})
    pairs = {tuple(sorted(s["columns"])) for s in signals}
    assert ("Ngay nghi", "Phong ban") in pairs      # ANOVA found the real gap
    assert ("Hieu suat", "Tham nien") in pairs      # correlation found the real link


def test_identifier_columns_are_excluded():
    """A transaction id is categorical with n distinct levels; testing it
    against anything yields noise dressed as a finding."""
    kinds = classify_columns(_hr_frame(), {"column_profiles": [{"name": "Ma NV", "role": "id"}]})
    assert "Ma NV" not in kinds["Cat"] + kinds["C"]


def test_pure_noise_produces_no_signals():
    """At 100k rows nearly every test reaches p < 0.05, so without multiplicity
    control and effect-size gating this would report ~190 'discoveries'."""
    rng = np.random.default_rng(3)
    noise = pd.DataFrame({f"c{i}": rng.normal(size=20_000) for i in range(6)})
    noise["g1"] = rng.choice(list("ABCD"), 20_000)
    noise["g2"] = rng.choice(list("XYZ"), 20_000)
    assert analyze(noise) == []


def test_arithmetic_relationships_are_not_reported_as_discoveries():
    """Thành tiền correlates with Số lượng by definition. Reporting it as a
    finding trains the user to distrust every other finding."""
    rng = np.random.default_rng(4)
    n = 3_000
    sl = rng.integers(1, 20, n).astype(float)
    dg = rng.integers(1_000, 90_000, n).astype(float)
    df = pd.DataFrame({"So luong": sl, "Don gia": dg, "Thanh tien": sl * dg})
    formulas = [{"target": "Thanh tien", "left": "So luong", "op": "×", "right": "Don gia"}]

    without = [s for s in analyze(df) if s["kind"] == "num_vs_num"]
    with_filter = [s for s in analyze(df, formulas=formulas) if s["kind"] == "num_vs_num"]
    assert without and not with_filter


def test_degenerate_input_returns_empty_rather_than_raising():
    """Statistical functions are brittle on degenerate data; an upload must
    never fail because a column happened to be constant."""
    assert analyze(None) == []
    assert analyze(pd.DataFrame()) == []
    assert analyze(pd.DataFrame({"a": [1] * 100, "b": ["x"] * 100})) == []
