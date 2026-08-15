"""Which Vega specs survive the gatekeeper, and what happens to the ones that don't.

Both halves matter equally. Rejecting a valid spec cost a user their chart; and
the way rejection was HANDLED — substituting an empty bar — turned a missing
chart into an empty popup with a "Ghi vào Dashboard" button in it, which is worse
than showing nothing at all.
"""

from app.agent.chart_utils import (
    condense_chat_chart,
    condense_layout,
    verify_and_sanitize_vega,
)

ROWS = [
    {"nv": "SR LÂM", "tien": 177045000, "sl": 3747},
    {"nv": "SR THẢO", "tien": 103100904.76, "sl": 2152.5},
]


def _layered():
    """The real shape behind the bug: "tổng THÀNH TIỀN VÀ SỐ LƯỢNG theo TÊN NV"
    is two measures on one axis, which Vega-Lite expresses as a layer — and a
    layer spec has no top-level `mark`."""
    return {
        "data": {"values": ROWS},
        "layer": [
            {"mark": "bar", "encoding": {"x": {"field": "nv"}, "y": {"field": "tien"}}},
            {"mark": "line", "encoding": {"x": {"field": "nv"}, "y": {"field": "sl"}}},
        ],
    }


# ── specs that must be accepted ─────────────────────────────────────────────

def test_a_layered_two_measure_spec_is_valid():
    """REGRESSION: the check was `if "mark" not in spec: return None`, so this
    exact spec — the one the product's most common question produces — was
    declared invalid and replaced with an empty bar."""
    assert verify_and_sanitize_vega(_layered()) is not None


def test_single_view_specs_still_pass():
    assert verify_and_sanitize_vega(
        {"mark": "bar", "data": {"values": ROWS}, "encoding": {}}
    ) is not None


import pytest


@pytest.mark.parametrize("key", ["repeat", "facet", "hconcat", "vconcat", "concat"])
def test_every_view_composition_key_is_accepted(key):
    """vega-embed on the frontend renders all of these. A gatekeeper stricter
    than the renderer it guards only deletes working charts."""
    spec = {key: [], "spec": {"mark": "bar"}, "data": {"values": ROWS}}
    assert verify_and_sanitize_vega(spec) is not None


def test_data_nested_inside_sub_views_counts_as_data():
    """Layered specs often carry data per layer rather than at the top."""
    spec = {"layer": [{"mark": "bar", "data": {"values": ROWS}}]}
    assert verify_and_sanitize_vega(spec) is not None


def test_an_external_data_url_is_left_alone():
    spec = {"mark": "bar", "data": {"url": "https://example.com/x.json"}}
    assert verify_and_sanitize_vega(spec) is not None


# ── specs that must be rejected ─────────────────────────────────────────────

def test_a_spec_with_no_view_at_all_is_rejected():
    assert verify_and_sanitize_vega({"data": {"values": ROWS}}) is None


def test_a_spec_with_no_data_anywhere_is_rejected():
    """An empty picture is not better than no picture — it just costs a click
    to find out."""
    assert verify_and_sanitize_vega({"mark": "bar", "encoding": {}}) is None


def test_non_dict_input_is_rejected():
    for bad in (None, [], "mark", 3):
        assert verify_and_sanitize_vega(bad) is None


# ── value cleaning ──────────────────────────────────────────────────────────

def test_nan_and_infinity_become_null_wherever_they_sit():
    """json.dumps emits bare NaN/Infinity, which is not valid JSON and breaks
    the stream the frontend is parsing."""
    import numpy as np

    spec = {"layer": [{"mark": "bar",
                       "data": {"values": [{"a": float("nan"), "b": np.inf, "c": 5}]}}]}
    out = verify_and_sanitize_vega(spec)
    row = out["layer"][0]["data"]["values"][0]
    assert row["a"] is None and row["b"] is None and row["c"] == 5


def test_a_list_valued_field_does_not_crash_the_cleaner():
    """REGRESSION: pd.isna raises on containers. It used to be the FIRST check,
    so any spec carrying a list value blew up the whole sanitizer."""
    spec = {"mark": "bar", "data": {"values": [{"tags": ["a", "b"], "n": 1}]}}
    out = verify_and_sanitize_vega(spec)
    assert out["data"]["values"][0]["tags"] == ["a", "b"]


# ── what happens on rejection ───────────────────────────────────────────────

def test_an_unrenderable_chat_chart_is_dropped_not_emptied():
    """REGRESSION: the fallback set type="bar" with empty labels/values. That
    object is truthy, so the UI showed a "Xem biểu đồ" button and opened a popup
    containing nothing but a "Ghi vào Dashboard" button. Returning None removes
    the button instead."""
    assert condense_chat_chart({"type": "vega", "vegaLiteSpec": {"nonsense": 1}}) is None


def test_a_valid_chat_vega_chart_survives():
    chart = condense_chat_chart({"type": "vega", "title": "x", "vegaLiteSpec": _layered()})
    assert chart is not None and chart["type"] == "vega"
    assert "layer" in chart["vegaLiteSpec"]


def test_an_unrenderable_dashboard_chart_is_removed_from_the_layout():
    """An empty tile still occupies a dashboard slot and still invites a click."""
    layout = {"charts": [
        {"type": "vega", "vegaLiteSpec": {"nonsense": 1}},
        {"type": "bar", "labels": ["a"], "values": [1]},
    ]}
    condense_layout(layout)
    assert [c["type"] for c in layout["charts"]] == ["bar"]


def test_removing_a_broken_chart_leaves_the_others_untouched():
    layout = {"charts": [
        {"type": "vega", "vegaLiteSpec": _layered()},
        {"type": "vega", "vegaLiteSpec": {"nonsense": 1}},
    ]}
    condense_layout(layout)
    assert len(layout["charts"]) == 1
    assert "layer" in layout["charts"][0]["vegaLiteSpec"]
