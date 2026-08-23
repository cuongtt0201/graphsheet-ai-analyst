"""The deck spec is the only thing standing between a model and a broken slide.

CSS handles where things sit, so boxes cannot overlap. What CSS cannot fix is
too much text for the space, so every budget is clamped here -- the model is
told the limit and the code enforces it, because a told limit is a hope.
"""

from unittest.mock import MagicMock

import pytest
from app.agent.slides import (
    LIMITS,
    MAX_BULLETS,
    MAX_KPIS,
    MAX_SLIDES,
    _clip,
    build_deck,
    clamp_deck,
)


def test_clip_cuts_at_a_word_and_marks_the_cut():
    long = "Doanh thu toàn hệ thống đạt hai nghìn tỷ đồng trong quý vừa qua"
    out = _clip(long, 40)

    assert len(out) <= 40
    assert out.endswith("…")
    # Cutting mid-word reads as corruption; the cut lands on a boundary.
    assert not out[:-1].endswith(" ")
    assert long.startswith(out[:-1].rstrip("…").strip())


def test_clip_leaves_text_within_budget_alone():
    assert _clip("Ngắn gọn", 40) == "Ngắn gọn"
    # Whitespace is normalised so a stray newline cannot smuggle in extra width.
    assert _clip("  hai   dòng\nnối  ", 40) == "hai dòng nối"


def test_every_budget_is_enforced_not_merely_requested():
    deck = clamp_deck({
        "title": "T" * 500,
        "subtitle": "S" * 500,
        "slides": [{
            "layout": "bullets",
            "heading": "H" * 500,
            "takeaway": "K" * 500,
            "bullets": ["B" * 500] * 20,
        }],
    }, n_charts=0)

    assert len(deck["title"]) <= LIMITS["deck_title"]
    assert len(deck["subtitle"]) <= LIMITS["deck_subtitle"]
    slide = deck["slides"][0]
    assert len(slide["heading"]) <= LIMITS["heading"]
    assert len(slide["takeaway"]) <= LIMITS["takeaway"]
    assert len(slide["bullets"]) == MAX_BULLETS
    assert all(len(b) <= LIMITS["bullet"] for b in slide["bullets"])


def test_unknown_layouts_are_dropped():
    """The renderer only knows the closed set; anything else draws nothing."""
    deck = clamp_deck({"title": "x", "slides": [
        {"layout": "carousel_3d", "heading": "sáng tạo quá"},
        {"layout": "bullets", "bullets": ["giữ lại"]},
    ]}, n_charts=0)

    assert [s["layout"] for s in deck["slides"]] == ["bullets"]


def test_a_chart_slide_pointing_at_no_chart_is_downgraded_not_blanked():
    """An empty chart frame is worse than the words the model already wrote."""
    deck = clamp_deck({"title": "x", "slides": [
        {"layout": "chart", "chart_index": 99, "heading": "Doanh thu", "bullets": ["Miền Bắc dẫn đầu"]},
        {"layout": "chart", "chart_index": 99, "takeaway": "Chỉ còn một câu"},
        {"layout": "chart", "chart_index": 0, "takeaway": "Có biểu đồ thật"},
    ]}, n_charts=1)

    layouts = [s["layout"] for s in deck["slides"]]
    assert layouts == ["bullets", "section", "chart"]
    assert deck["slides"][0]["bullets"] == ["Miền Bắc dẫn đầu"]
    assert deck["slides"][2]["chart_index"] == 0


def test_a_chart_slide_with_nothing_at_all_is_dropped():
    deck = clamp_deck({"title": "x", "slides": [{"layout": "chart", "chart_index": 99}]}, n_charts=0)
    assert deck["slides"] == []


def test_empty_slides_are_dropped_rather_than_rendered_blank():
    deck = clamp_deck({"title": "x", "slides": [
        {"layout": "bullets", "bullets": []},
        {"layout": "kpi", "kpis": []},
        {"layout": "big_number", "big_caption": "thiếu mất con số"},
        {"layout": "section", "heading": "còn lại"},
    ]}, n_charts=0)

    assert [s["layout"] for s in deck["slides"]] == ["section"]


def test_kpi_slides_are_capped_and_require_a_value():
    deck = clamp_deck({"title": "x", "slides": [{
        "layout": "kpi",
        "kpis": [{"label": f"L{i}", "value": f"{i}"} for i in range(10)] + [{"label": "thiếu giá trị"}],
    }]}, n_charts=0)

    assert len(deck["slides"][0]["kpis"]) == MAX_KPIS
    assert all(k["value"] for k in deck["slides"][0]["kpis"])


def test_deck_length_is_capped():
    deck = clamp_deck({
        "title": "x",
        "slides": [{"layout": "section", "heading": f"Phần {i}"} for i in range(40)],
    }, n_charts=0)
    assert len(deck["slides"]) == MAX_SLIDES


def test_build_deck_refuses_when_there_is_nothing_to_present():
    res = build_deck("làm slide", kpis=[], charts=[])
    assert res["ok"] is False
    assert "dashboard" in res["error"].lower()


def test_build_deck_clamps_whatever_the_model_returns():
    charts = [{"title": "Doanh thu theo miền", "type": "bar",
               "labels": ["Bắc", "Nam"], "values": [736, 635]}]
    mock = MagicMock(return_value={
        "title": "T" * 300,
        "slides": [
            {"layout": "title", "heading": "Báo cáo doanh thu"},
            {"layout": "chart", "chart_index": 0, "takeaway": "K" * 400},
            {"layout": "closing", "bullets": ["Đề xuất " + "x" * 300]},
        ],
    })

    res = build_deck("tạo slide", kpis=[{"title": "Tổng", "value": "2,02 tỷ"}],
                     charts=charts, call_ai_fn=mock)

    assert res["ok"] is True
    deck = res["deck"]
    assert len(deck["title"]) <= LIMITS["deck_title"]
    assert len(deck["slides"][1]["takeaway"]) <= LIMITS["takeaway"]
    assert len(deck["slides"][2]["bullets"][0]) <= LIMITS["bullet"]
    # Charts travel with the deck so the browser draws them live.
    assert deck["charts"] == charts


def test_build_deck_reports_a_model_failure_instead_of_raising():
    def boom(*a, **k):
        raise RuntimeError("pool exhausted")

    res = build_deck("tạo slide", kpis=[{"title": "T", "value": "1"}], charts=[], call_ai_fn=boom)
    assert res["ok"] is False
    assert "pool exhausted" in res["error"]


def test_build_deck_rejects_a_model_that_returns_only_junk():
    mock = MagicMock(return_value={"title": "x", "slides": [{"layout": "khong_co_that"}]})
    res = build_deck("tạo slide", kpis=[{"title": "T", "value": "1"}], charts=[], call_ai_fn=mock)
    assert res["ok"] is False


_PROFILE = {
    "source_id": "S", "sheet": "S", "filename": "f.xlsx",
    "columns": ["Mien", "DoanhThu"], "row_count": 100,
    "dtypes": {"Mien": "string", "DoanhThu": "float64"},
    "column_profiles": [
        {"name": "Mien", "role": "category", "dtype": "string", "null_pct": 0, "distinct": 3},
        {"name": "DoanhThu", "role": "measure", "dtype": "float64", "null_pct": 0, "distinct": 90},
    ],
}


def test_slide_mode_builds_the_dashboard_it_needs(monkeypatch):
    """Asking for a deck with nothing pinned must not bounce the user to a button."""
    from app.agent import chat_agent

    built = {}

    def fake_build(prompt):
        built["prompt"] = prompt
        return {
            "kpis": [{"title": "Tổng doanh thu", "value": "2,02 tỷ"}],
            "charts": [{"title": "Theo miền", "type": "bar", "labels": ["B"], "values": [1]}],
            "insights": [],
        }

    monkeypatch.setattr(chat_agent, "call_ai", MagicMock(side_effect=[
        {"mode": "slide", "reason": "người dùng muốn bài thuyết trình"},
    ]))
    monkeypatch.setattr(chat_agent, "_load_chat_skills", lambda q, u: ("", "", {}))
    import app.agent.slides as slides_mod
    monkeypatch.setattr(slides_mod, "call_ai", MagicMock(return_value={"title": "Deck", "slides": [
        {"layout": "title", "heading": "Kết quả"},
        {"layout": "chart", "chart_index": 0, "takeaway": "Miền Bắc dẫn đầu"},
        {"layout": "closing", "bullets": ["Làm tiếp"]},
    ]}))

    reply = chat_agent.answer_question(
        profiles=[_PROFILE],
        dataframes={},
        question="tạo cho tôi bài thuyết trình",
        history=[],
        slide_source={"kpis": [], "charts": [], "insights": []},
        build_dashboard_fn=fake_build,
    )

    assert built["prompt"] == "tạo cho tôi bài thuyết trình"
    assert reply["deck"] is not None
    assert len(reply["deck"]["slides"]) == 3
    assert reply["deck"]["charts"][0]["title"] == "Theo miền"


def test_slide_mode_uses_an_existing_dashboard_without_rebuilding(monkeypatch):
    """A dashboard the user already has must not be silently recomputed."""
    from app.agent import chat_agent

    calls = []
    monkeypatch.setattr(chat_agent, "call_ai", MagicMock(side_effect=[
        {"mode": "slide", "reason": "muốn slide"},
    ]))
    monkeypatch.setattr(chat_agent, "_load_chat_skills", lambda q, u: ("", "", {}))
    import app.agent.slides as slides_mod
    monkeypatch.setattr(slides_mod, "call_ai",
                        MagicMock(return_value={"title": "Deck", "slides": [{"layout": "section", "heading": "Phần 1"}]}))

    reply = chat_agent.answer_question(
        profiles=[_PROFILE],
        dataframes={},
        question="làm slide",
        history=[],
        slide_source={"kpis": [{"title": "Đã ghim", "value": "1"}], "charts": [], "insights": []},
        build_dashboard_fn=lambda p: calls.append(p) or {},
    )

    assert calls == []
    assert reply["deck"] is not None


def test_the_deck_schema_stays_small_enough_for_every_model_in_the_pool():
    """Gemini refuses a schema that "produces a constraint that has too many
    states for serving", and names long descriptions as the cause. The first
    version of this schema carried the layout explanations inline and the
    lite models rejected the request outright -- on every slot at once, so the
    feature failed rather than degraded. Explanations belong in the prompt,
    where they cost nothing structural."""
    import json
    from app.agent.slides import DECK_SCHEMA, DECK_PROMPT

    encoded = json.dumps(DECK_SCHEMA, ensure_ascii=False)
    assert len(encoded) < 1500, f"schema grew to {len(encoded)} chars"

    # Long prose in a schema is exactly what the error message points at.
    def descriptions(node):
        if isinstance(node, dict):
            if isinstance(node.get("description"), str):
                yield node["description"]
            for v in node.values():
                yield from descriptions(v)
        elif isinstance(node, list):
            for v in node:
                yield from descriptions(v)

    longest = max((len(d) for d in descriptions(DECK_SCHEMA)), default=0)
    assert longest == 0, "put the explanation in DECK_PROMPT, not in the schema"

    # The error message names nested array length limits too, and those are what
    # actually broke it: 12 slides each holding a 4-item and a 5-item array.
    # The counts are enforced in clamp_deck, so declaring them here bought
    # nothing and cost every slot in the pool.
    assert "maxItems" not in encoded
    assert "minItems" not in encoded

    # And the guidance still has to reach the model somewhere.
    assert "chart_split" in DECK_PROMPT
    assert "chart_index" in DECK_PROMPT
