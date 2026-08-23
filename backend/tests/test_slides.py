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
