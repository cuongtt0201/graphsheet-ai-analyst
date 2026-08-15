"""Batched header detection.

Each sheet needs only a one-line answer, so N separate calls spent N
round-trips and N rate-limit slots to move almost no information. Batching
fixes that, but it also turns N independent failures into one correlated
failure — so the fallback matters as much as the batching, and both are tested
here with the LLM stubbed out.
"""

import pytest

from app.data import llm_header


@pytest.fixture
def grids():
    banner = [
        ["CÔNG TY ABC", None, None],
        ["Báo cáo quý 1", None, None],
        ["Cửa hàng", "Doanh thu", "Khu vực"],
        ["A", 100, "Bắc"],
    ]
    plain = [
        ["Mã", "Tên"],
        ["A1", "Bánh"],
        ["A2", "Kẹo"],
    ]
    return {"f.xlsx::Banner": banner, "f.xlsx::Plain": plain}


def test_batches_every_sheet_into_one_call(monkeypatch, grids):
    calls = []

    def fake_batch(context, tasks, tier="strong", session_id=None, schemas=None):
        calls.append(tasks)
        # Sheets arrive in insertion order as sheet_0, sheet_1, ...
        return {"sheet_0": {"header_row": 2}, "sheet_1": {"header_row": 0}}

    monkeypatch.setattr("app.ai.harness.batch_tasks", fake_batch)
    out = llm_header.llm_detect_headers(grids)

    assert len(calls) == 1                     # one call, not one per sheet
    assert len(calls[0]) == 2                  # both sheets inside it
    assert out["f.xlsx::Banner"] == 2
    assert out["f.xlsx::Plain"] == 0


def test_per_sheet_schema_is_passed_so_output_stays_constrained(monkeypatch, grids):
    """Without a schema per task the model may return any shape, which throws
    away the guarantee the rest of the pipeline depends on."""
    seen = {}

    def fake_batch(context, tasks, tier="strong", session_id=None, schemas=None):
        seen["schemas"] = schemas
        return {k: {"header_row": 0} for k in tasks}

    monkeypatch.setattr("app.ai.harness.batch_tasks", fake_batch)
    llm_header.llm_detect_headers(grids)

    assert seen["schemas"] and all(s is llm_header._SCHEMA for s in seen["schemas"].values())


def test_falls_back_per_sheet_when_the_batch_raises(monkeypatch, grids):
    """Batching concentrates failure: one bad response would otherwise cost the
    header check for every sheet at once."""
    def boom(*a, **k):
        raise RuntimeError("model exploded")

    monkeypatch.setattr("app.ai.harness.batch_tasks", boom)
    monkeypatch.setattr(llm_header, "llm_detect_header", lambda grid: 1)

    out = llm_header.llm_detect_headers(grids)
    assert out == {"f.xlsx::Banner": 1, "f.xlsx::Plain": 1}


def test_falls_back_when_the_batch_answers_nothing_usable(monkeypatch, grids):
    """An all-None batch is indistinguishable from never having asked."""
    monkeypatch.setattr("app.ai.harness.batch_tasks",
                        lambda *a, **k: {"sheet_0": {}, "sheet_1": {}})
    monkeypatch.setattr(llm_header, "llm_detect_header", lambda grid: 0)

    assert llm_header.llm_detect_headers(grids) == {"f.xlsx::Banner": 0, "f.xlsx::Plain": 0}


def test_out_of_range_answers_are_rejected(monkeypatch, grids):
    """The model is shown a fixed window of rows; an index outside it points at
    a row it never saw, so it cannot be trusted."""
    monkeypatch.setattr("app.ai.harness.batch_tasks",
                        lambda *a, **k: {"sheet_0": {"header_row": 99},
                                         "sheet_1": {"header_row": -1}})
    monkeypatch.setattr(llm_header, "llm_detect_header", lambda grid: None)

    out = llm_header.llm_detect_headers(grids)
    assert out["f.xlsx::Banner"] is None
    assert out["f.xlsx::Plain"] is None


def test_large_sheet_counts_are_chunked(monkeypatch):
    """A single enormous batch would both bloat the prompt and put every sheet
    behind one fragile response."""
    many = {f"f::S{i}": [["A", "B"], [1, 2]] for i in range(20)}
    sizes = []

    def fake_batch(context, tasks, tier="strong", session_id=None, schemas=None):
        sizes.append(len(tasks))
        return {k: {"header_row": 0} for k in tasks}

    monkeypatch.setattr("app.ai.harness.batch_tasks", fake_batch)
    out = llm_header.llm_detect_headers(many)

    assert len(out) == 20
    assert max(sizes) <= llm_header.BATCH_MAX_SHEETS


def test_unusable_grids_are_skipped_entirely(monkeypatch):
    """No call should be made for sheets with nothing to look at."""
    called = []
    monkeypatch.setattr("app.ai.harness.batch_tasks",
                        lambda *a, **k: called.append(1) or {})

    assert llm_header.llm_detect_headers({"a": [], "b": [["only one row"]]}) == {}
    assert not called
