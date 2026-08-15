"""The anti-hallucination gate.

This is the product's core promise ("số liệu tin được"), and it is pure
deterministic string/number matching — no LLM — so it is fully testable. Both
directions matter and are tested here:

  false NEGATIVE (a made-up number slips through) breaks the promise;
  false POSITIVE (a correct number flagged) silently DELETES a real insight,
  because the caller drops any paragraph that fails.

Several of these cases are regressions from bugs found in real use, marked
below — they exist so the same mistake cannot come back.
"""

from app.ai.harness import collect_ground_truth, collect_numbers_from_text, verify_numbers


def _gt():
    kpis = [{"name": "Tổng Doanh Thu", "value": 25284625156},
            {"name": "Tổng Số Bill", "value": 268020},
            {"name": "DS TB/Bill", "value": 94338.58}]
    charts = [{"data": [{"label": "Miền Bắc", "value": 11381885961},
                        {"label": "Miền Nam", "value": 10811014919}]}]
    return collect_ground_truth(kpis, charts) | collect_numbers_from_text("- Tăng trưởng: -35.8%")


# ── must NOT flag correct prose ──────────────────────────────────────────────

def test_accepts_vietnamese_formatting():
    text = ("Tổng doanh thu đạt 25.284.625.156 VND từ 268.020 giao dịch, "
            "trung bình 94.338,58 VND. Tăng trưởng -35.8%.")
    assert verify_numbers(text, _gt()) == []


def test_accepts_english_formatting():
    text = "Revenue 25284625156 VND from 268020 bills, avg 94338.58, growth -35.8%."
    assert verify_numbers(text, _gt()) == []


def test_accepts_compact_scaled_forms():
    """Prose says "11.38 tỷ" for 11381885961 — the readable form the product
    now deliberately asks for, so it must never be treated as invented."""
    text = "Miền Bắc 11.38 tỷ, Miền Nam 10.81 tỷ, tổng 25,3 tỷ VNĐ."
    assert verify_numbers(text, _gt()) == []


def test_ignores_years_and_small_counts():
    text = "Trong năm 2025, tháng 5, top 3 cửa hàng, 12 chi nhánh."
    assert verify_numbers(text, _gt()) == []


def test_empty_inputs_are_not_violations():
    assert verify_numbers("", _gt()) == []
    assert verify_numbers("Doanh thu 999 tỷ", set()) == []


# ── must flag invented numbers ───────────────────────────────────────────────

def test_flags_invented_numbers():
    text = "Doanh thu 27.5 tỷ, tăng +14258.8% tại Cát Linh với 300000 giao dịch."
    flagged = {v["token"] for v in verify_numbers(text, _gt())}
    assert "27.5 tỷ" in flagged
    assert "+14258.8%" in flagged
    assert "300000" in flagged


def test_flags_order_of_magnitude_error():
    """REGRESSION: "86,6 triệu" (10x below the real 866,8 triệu) used to pass.
    The English reading of "86,6" was "866", which times 1e6 landed inside the
    2% tolerance of 866_838_347. A separator only groups thousands when exactly
    three digits follow it."""
    gt = collect_ground_truth([{"value": 866838347}], [])
    assert verify_numbers("đạt 866,8 triệu VNĐ", gt) == []
    assert [v["token"] for v in verify_numbers("đạt 86,6 triệu VNĐ", gt)] == ["86,6 triệu"]


def test_tolerance_accepts_rounding_but_not_drift():
    gt = collect_ground_truth([{"value": 1000000}], [])
    assert verify_numbers("khoảng 1,0 triệu", gt) == []      # rounding: fine
    assert verify_numbers("khoảng 1,5 triệu", gt) != []      # 50% off: invented


# ── ground-truth collection ──────────────────────────────────────────────────

def test_collect_walks_nested_structures():
    """Values must be harvested wherever they sit: a truth the collector misses
    shows up later as a FALSE hallucination report on a correct number."""
    gt = collect_ground_truth({"a": [{"b": {"c": 12345.6}}]}, [{"d": "789"}])
    assert 12345.6 in gt
    assert 789.0 in gt


def test_collect_ignores_booleans():
    """bool is a subclass of int in Python; True must not become the number 1
    and quietly legitimise a stray "1" in the prose."""
    assert collect_ground_truth({"ok": True, "bad": False}, []) == set()
