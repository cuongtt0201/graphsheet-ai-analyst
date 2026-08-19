"""Semantics was the last per-sheet LLM call left at upload.

A 30-sheet accounting workbook spent 30 round-trips and 30 rate-limit slots
deciding what one row means in each sheet — by far the largest cost of opening
such a file. Batching it is only safe if three things survive: the deterministic
override still gets the last word, a dropped sheet is retried instead of
silently losing its grain, and a total batch failure degrades to the old path
rather than to nothing.
"""

from unittest.mock import patch

from app.data import semantics


def _profile(sid, row_count=100, distinct=100):
    return {
        "source_id": sid,
        "row_count": row_count,
        "column_profiles": [
            {"name": "ma", "dtype": "string", "role": "id", "distinct": distinct, "sample": ["A1"]},
            {"name": "tien", "dtype": "float64", "role": "measure", "distinct": 50, "sample": [10]},
        ],
        "sample_rows": [{"ma": "A1", "tien": 10}],
    }


ANSWER = {"grain_type": "transaction", "grain_description": "1 giao dịch",
          "dedup_safe": True, "sheet_role": "fact"}


def test_many_sheets_cost_one_call_per_batch_not_one_per_sheet():
    profiles = [_profile(f"f::S{i}") for i in range(8)]
    calls = []

    def fake_batch(ctx, tasks, **kw):
        calls.append(len(tasks))
        return {k: dict(ANSWER) for k in tasks}

    with patch("app.ai.harness.batch_tasks", side_effect=fake_batch):
        out = semantics.analyze_all(profiles)

    assert len(out) == 8
    assert calls == [3, 3, 2]          # 8 sheets -> 3 calls (batch size = 3)


def test_deterministic_override_still_applies_to_batched_answers():
    """REGRESSION GUARD: the pandas-over-LLM check lived inside the per-sheet
    function. If batching bypassed it, the model's grain would ship unchecked —
    and grain is what decides whether duplicate rows get deleted."""
    # Model claims line-items, but the id is unique on every row.
    claim = dict(ANSWER, grain_type="transaction_line", dedup_safe=False, entity_key=["ma"])
    profiles = [_profile("f::S0", row_count=100, distinct=100)]

    with patch("app.ai.harness.batch_tasks", return_value={"sheet_0": claim}):
        out = semantics.analyze_all(profiles)

    assert out["f::S0"]["grain_type"] == "transaction"   # overridden by the data
    assert out["f::S0"]["dedup_safe"] is True


def test_sheet_missing_from_the_batch_is_retried_individually():
    profiles = [_profile("f::S0"), _profile("f::S1")]

    with patch("app.ai.harness.batch_tasks", return_value={"sheet_0": dict(ANSWER)}), \
         patch.object(semantics, "analyze_sheet_semantics", return_value=dict(ANSWER)) as one:
        out = semantics.analyze_all(profiles)

    assert set(out) == {"f::S0", "f::S1"}
    assert one.call_count == 1      # only the dropped sheet, not both


def test_batch_failure_degrades_to_the_per_sheet_path():
    profiles = [_profile("f::S0"), _profile("f::S1")]

    with patch("app.ai.harness.batch_tasks", side_effect=RuntimeError("429")), \
         patch.object(semantics, "analyze_sheet_semantics", return_value=dict(ANSWER)):
        out = semantics.analyze_all(profiles)

    assert set(out) == {"f::S0", "f::S1"}


def test_total_failure_returns_empty_rather_than_raising():
    """Upload must complete without semantics; every downstream consumer already
    treats an absent profile as 'unknown'."""
    profiles = [_profile("f::S0")]
    with patch("app.ai.harness.batch_tasks", side_effect=RuntimeError("down")), \
         patch.object(semantics, "analyze_sheet_semantics", return_value=None):
        assert semantics.analyze_all(profiles) == {}


def test_check_still_runs_when_the_model_omits_entity_key():
    """REGRESSION: entity_key is not a required field, so an answer that simply
    left it out disabled the grain check entirely — the careless answers were
    exactly the ones going unchecked. One unambiguous id column stands in."""
    claim = dict(ANSWER, grain_type="transaction_line")     # no entity_key at all
    with patch("app.ai.harness.batch_tasks", return_value={"sheet_0": claim}):
        out = semantics.analyze_all([_profile("f::S0", row_count=100, distinct=100)])
    assert out["f::S0"]["grain_type"] == "transaction"


def test_fallback_refuses_to_guess_between_two_id_columns():
    """"Mã HĐ" and "Mã KH" behave oppositely; picking wrong flips the grain and
    decides whether duplicate rows get deleted. Ambiguity must mean no override,
    not a coin flip."""
    p = _profile("f::S0", row_count=100, distinct=100)
    p["column_profiles"].append(
        {"name": "ma_kh", "dtype": "string", "role": "id", "distinct": 20, "sample": ["K1"]})
    claim = dict(ANSWER, grain_type="transaction_line")
    with patch("app.ai.harness.batch_tasks", return_value={"sheet_0": claim}):
        out = semantics.analyze_all([p])
    assert out["f::S0"]["grain_type"] == "transaction_line"   # left untouched


def test_hallucinated_entity_key_falls_back_instead_of_skipping():
    claim = dict(ANSWER, grain_type="transaction_line", entity_key=["cot_khong_ton_tai"])
    with patch("app.ai.harness.batch_tasks", return_value={"sheet_0": claim}):
        out = semantics.analyze_all([_profile("f::S0", row_count=100, distinct=100)])
    assert out["f::S0"]["grain_type"] == "transaction"


def test_composite_key_is_left_alone():
    """Uniqueness of a column PAIR cannot be read off per-column counts, so the
    check must decline rather than measure the wrong thing."""
    claim = dict(ANSWER, grain_type="transaction_line", entity_key=["ma", "tien"])
    with patch("app.ai.harness.batch_tasks", return_value={"sheet_0": claim}):
        out = semantics.analyze_all([_profile("f::S0", row_count=100, distinct=100)])
    assert out["f::S0"]["grain_type"] == "transaction_line"


def test_no_profiles_makes_no_calls():
    with patch("app.ai.harness.batch_tasks", side_effect=AssertionError("must not be called")):
        assert semantics.analyze_all([]) == {}
