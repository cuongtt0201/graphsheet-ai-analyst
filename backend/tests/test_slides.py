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
    monkeypatch.setattr(slides_mod, "call_ai", MagicMock(return_value={"title": "Deck", "slides": [
        {"layout": "title", "heading": "Phần 1"},
        {"layout": "closing", "bullets": ["Làm tiếp"]},
    ]}))

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


def test_one_slide_is_not_a_deck():
    """Shipping the remnant of a failed build as a "presentation" hides the failure."""
    from app.agent.slides import MIN_SLIDES

    mock = MagicMock(return_value={"title": "D", "slides": [{"layout": "section", "heading": "Một mình"}]})
    res = build_deck("tạo slide", kpis=[{"title": "T", "value": "1"}], charts=[], call_ai_fn=mock)

    assert MIN_SLIDES == 2
    assert res["ok"] is False
    assert "chỉ 1 slide" in res["error"]


def test_the_error_separates_a_short_deck_from_a_broken_one():
    """"Wrote three slides" and "wrote nine, six unusable" look identical in the
    result. They are different problems, so the message names which happened."""
    slides = [{"layout": "section", "heading": "Giữ"}] + [{"layout": "bullets", "bullets": []}] * 8
    mock = MagicMock(return_value={"title": "D", "slides": slides})

    res = build_deck("tạo slide", kpis=[{"title": "T", "value": "1"}], charts=[], call_ai_fn=mock)

    assert res["ok"] is False
    assert "8 slide bị loại" in res["error"]


def test_two_slides_is_enough():
    mock = MagicMock(return_value={"title": "D", "slides": [
        {"layout": "title", "heading": "Kết quả"},
        {"layout": "closing", "bullets": ["Làm tiếp"]},
    ]})
    res = build_deck("tạo slide", kpis=[{"title": "T", "value": "1"}], charts=[], call_ai_fn=mock)

    assert res["ok"] is True
    assert len(res["deck"]["slides"]) == 2
    # The bookkeeping field is internal and must not reach the browser.
    assert "dropped" not in res["deck"]


def test_clamp_deck_reports_what_it_threw_away():
    deck = clamp_deck({"title": "x", "slides": [
        {"layout": "section", "heading": "Giữ"},
        {"layout": "khong_co_that"},
        "không phải dict",
        {"layout": "kpi", "kpis": []},
    ]}, n_charts=0)

    assert len(deck["slides"]) == 1
    assert deck["dropped"] == 3


def test_the_cap_counts_kept_slides_not_examined_ones():
    """A run of unusable slides must not eat the budget for good ones."""
    junk = [{"layout": "bullets", "bullets": []}] * 30
    good = [{"layout": "section", "heading": f"Phần {i}"} for i in range(MAX_SLIDES)]
    deck = clamp_deck({"title": "x", "slides": junk + good}, n_charts=0)

    assert len(deck["slides"]) == MAX_SLIDES
    assert deck["dropped"] == 30


def test_a_chart_that_cannot_be_drawn_is_not_offered_to_the_model():
    """A slide framing an empty chart is worse than no slide.

    MiniChart refuses per type -- scatter needs points, grouped-bar needs
    series, and so on -- so a chart missing its own required field renders as
    "Không đủ dữ liệu để vẽ biểu đồ này" under a heading confidently describing
    a trend. Such charts are dropped before the model can reference one.
    """
    from app.agent.chart_utils import is_renderable

    good = {"type": "bar", "title": "Doanh thu", "labels": ["B"], "values": [1]}
    empty = {"type": "bar", "title": "Rỗng", "labels": [], "values": []}
    wrong_shape = {"type": "grouped-bar", "title": "Sai kiểu", "labels": ["B"], "values": [1]}

    assert is_renderable(good)
    assert not is_renderable(empty)
    # Values alone are not enough for a type whose renderer reads `series`.
    assert not is_renderable(wrong_shape)

    captured = {}

    def capture(prompt, schema, **kw):
        captured["prompt"] = prompt
        return {"title": "D", "slides": [
            {"layout": "title", "heading": "A"},
            {"layout": "chart", "chart_index": 0, "takeaway": "K"},
        ]}

    res = build_deck("tạo slide", kpis=[{"title": "T", "value": "1"}],
                     charts=[empty, good, wrong_shape], call_ai_fn=capture)

    # Only the drawable chart is listed, and it is index 0.
    assert "[0] Doanh thu" in captured["prompt"]
    assert "Rỗng" not in captured["prompt"]
    assert "Sai kiểu" not in captured["prompt"]
    assert res["deck"]["charts"] == [good]
    assert res["deck"]["slides"][1]["chart_index"] == 0


def test_filtering_happens_before_indices_are_assigned():
    """Filtering after the fact would shift indices out from under the model."""
    a = {"type": "bar", "title": "Một", "labels": ["x"], "values": [1]}
    dud = {"type": "bar", "title": "Hỏng", "values": []}
    b = {"type": "bar", "title": "Hai", "labels": ["y"], "values": [2]}

    mock = MagicMock(return_value={"title": "D", "slides": [
        {"layout": "chart", "chart_index": 0, "takeaway": "K1"},
        {"layout": "chart", "chart_index": 1, "takeaway": "K2"},
    ]})
    res = build_deck("tạo slide", kpis=[], charts=[a, dud, b], call_ai_fn=mock)

    assert [c["title"] for c in res["deck"]["charts"]] == ["Một", "Hai"]
    assert res["deck"]["charts"][1]["title"] == "Hai"


def test_no_usable_chart_and_no_kpi_is_refused():
    res = build_deck("tạo slide", kpis=[], charts=[{"type": "bar", "values": []}])
    assert res["ok"] is False
    assert "biểu đồ" in res["error"]


def test_the_backend_requirements_match_what_the_renderer_checks():
    """These two lists sit in different languages and must not drift apart.

    MiniChart returns null -- and the slide shows an empty frame -- for exactly
    these type/field pairs.
    """
    from pathlib import Path
    from app.agent.chart_utils import _CHART_REQUIREMENTS

    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "chat" / "MiniChart.tsx")
    if not src.exists():
        import pytest
        pytest.skip("frontend source not available")
    body = src.read_text(encoding="utf-8")

    for chart_type, field in _CHART_REQUIREMENTS.items():
        assert f'"{chart_type}"' in body, f"{chart_type} is no longer a MiniChart type"
        if field != "vegaLiteSpec":
            assert f"!{field}?.length" in body or f"{field}?.length" in body, (
                f"{chart_type} claims to need {field}, which MiniChart no longer checks"
            )


def test_dashboard_charts_are_normalized_before_anything_reads_them():
    """The auto-dashboard and the renderer disagree on chart shape.

    The dashboard emits {title, type, data: [{label, value}]}; MiniChart reads
    {labels, values}. The browser bridges them in layoutChartToSpec, but the
    deck is planned server-side and never went through it -- so a chart full of
    data looked empty, was described to the model as having none, and reached a
    slide as a blank frame under a confident heading.
    """
    from app.agent.chart_utils import is_renderable, normalize_chart

    raw = {
        "title": "Xu hướng doanh thu theo tháng",
        "type": "line",
        "role": "trend",
        "data": [{"label": "2025-05", "value": 913883329.0}, {"label": "2025-06", "value": 1166601453.0}],
    }
    assert not is_renderable(raw)          # the shape the bug saw
    norm = normalize_chart(raw)
    assert is_renderable(norm)             # the same chart, read correctly
    assert norm["labels"] == ["2025-05", "2025-06"]
    assert norm["values"] == [913883329.0, 1166601453.0]


def test_build_deck_normalizes_and_then_describes_the_real_data():
    captured = {}

    def capture(prompt, schema, **kw):
        captured["prompt"] = prompt
        return {"title": "D", "slides": [
            {"layout": "title", "heading": "A"},
            {"layout": "chart", "chart_index": 0, "takeaway": "K"},
        ]}

    charts = [{"title": "Theo tháng", "type": "line",
               "data": [{"label": "2025-05", "value": 100}, {"label": "2025-06", "value": 200}]}]
    res = build_deck("tạo slide", kpis=[], charts=charts, call_ai_fn=capture)

    assert "2025-05=100" in captured["prompt"]
    assert res["ok"] is True
    assert res["deck"]["charts"][0]["values"] == [100, 200]


def test_small_kpi_numbers_are_not_dropped_from_the_prompt():
    """fmt_vi_compact returns "" below a million by design. Used alone it left
    a count of 53.799 as an empty string, so the model was shown a KPI with no
    value and wrote whatever it liked."""
    from app.agent.slides import _fmt_kpis

    text = _fmt_kpis([
        {"name": "Tổng doanh thu", "value": 5168825563.0},
        {"name": "Số hóa đơn", "value": 53799},
        {"name": "AOV", "value": 96076.61},
    ])

    assert "53.799" in text
    assert "96.076,61" in text
    # Large numbers keep both the exact figure and the readable magnitude.
    assert "5.168.825.563" in text and "5,2 tỷ" in text
    assert not any(line.rstrip().endswith(":") for line in text.splitlines())


def test_a_comparison_baseline_reaches_the_prompt_when_the_dashboard_computed_one():
    """Without this the model has no grounded way to say "so với kỳ trước" --
    and the prompt forbids inventing one."""
    from app.agent.slides import DECK_PROMPT, _fmt_kpis

    text = _fmt_kpis([{"name": "Doanh thu", "value": 5_168_825_563.0,
                       "compare_value": 4_475_576_864.0, "compare_label": "tháng trước"}])
    assert "so với tháng trước" in text
    assert "4.475.576.864" in text
    assert "CẤM SO SÁNH VỚI KỲ TRƯỚC" in DECK_PROMPT
    assert "CẤM DỰ BÁO" in DECK_PROMPT


def test_two_charts_needs_two_distinct_charts_or_it_becomes_one():
    """Half a comparison is worse than the single chart the model had."""
    deck = clamp_deck({"title": "x", "slides": [
        {"layout": "two_charts", "chart_index": 0, "chart_index_b": 1, "heading": "Đủ hai"},
        {"layout": "two_charts", "chart_index": 0, "chart_index_b": 99, "heading": "Thiếu một"},
        {"layout": "two_charts", "chart_index": 1, "chart_index_b": 1, "heading": "Trùng nhau"},
        {"layout": "two_charts", "chart_index": 99, "chart_index_b": 99, "bullets": ["Còn chữ"]},
        {"layout": "two_charts", "chart_index": 99, "chart_index_b": 99},
    ]}, n_charts=2)

    assert [s["layout"] for s in deck["slides"]] == ["two_charts", "chart", "chart", "bullets"]
    assert deck["slides"][0]["chart_index_b"] == 1
    assert deck["slides"][2]["chart_index"] == 1
    assert deck["dropped"] == 1


def test_a_quote_slide_without_the_quote_is_nothing():
    deck = clamp_deck({"title": "x", "slides": [
        {"layout": "quote", "takeaway": "Miền Bắc dẫn đầu ba miền."},
        {"layout": "quote", "heading": "Chỉ có tiêu đề"},
    ]}, n_charts=0)

    assert len(deck["slides"]) == 1
    assert deck["slides"][0]["takeaway"] == "Miền Bắc dẫn đầu ba miền."


def test_compare_and_timeline_need_at_least_two_entries():
    """One column is not a comparison and one date is not a timeline."""
    deck = clamp_deck({"title": "x", "slides": [
        {"layout": "compare", "items": [{"label": "Bắc", "value": "11,4 tỷ"}, {"label": "Nam", "value": "10,8 tỷ"}]},
        {"layout": "compare", "items": [{"label": "Một mình", "value": "1"}]},
        {"layout": "timeline", "items": [{"label": "05/2025", "value": "Bắt đầu"}, {"label": "01/2026", "value": "Đỉnh"}]},
        {"layout": "timeline", "items": []},
    ]}, n_charts=0)

    assert [s["layout"] for s in deck["slides"]] == ["compare", "timeline"]
    assert len(deck["slides"][0]["items"]) == 2


def test_item_text_is_clamped_like_every_other_field():
    from app.agent.slides import LIMITS, MAX_ITEMS

    deck = clamp_deck({"title": "x", "slides": [{
        "layout": "compare",
        "items": [{"label": "L" * 200, "value": "V" * 200, "note": "N" * 200}] * 9,
    }]}, n_charts=0)

    items = deck["slides"][0]["items"]
    assert len(items) == MAX_ITEMS
    assert all(len(i["label"]) <= LIMITS["item_label"] for i in items)
    assert all(len(i["value"]) <= LIMITS["item_value"] for i in items)
    assert all(len(i["note"]) <= LIMITS["item_note"] for i in items)


def test_every_layout_the_backend_offers_is_one_the_renderer_draws():
    """A layout the model can pick but the browser cannot draw renders blank."""
    import re
    from pathlib import Path
    from app.agent.slides import LAYOUTS

    src = Path(__file__).resolve().parents[2] / "frontend" / "src" / "chat" / "deckHtml.ts"
    if not src.exists():
        import pytest
        pytest.skip("frontend source not available")
    body = src.read_text(encoding="utf-8")

    handled = set(re.findall(r'case "([a-z_]+)":', body))
    missing = [l for l in LAYOUTS if l not in handled]
    # "bullets" is the default branch rather than a case label.
    assert missing == ["bullets"], f"renderer has no branch for: {missing}"
