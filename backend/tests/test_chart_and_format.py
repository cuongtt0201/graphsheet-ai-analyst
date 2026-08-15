"""Chart density caps and Vietnamese number presentation.

Chart caps are the last line of defence: the prompt asks the model to
aggregate, but a chart with 200 raw points must be impossible to reach the UI
even when the model ignores that instruction entirely.

Number formatting is computed in the backend precisely so the model never has
to divide — a model that slips one power of ten writes a number that is wrong
by 10x while reading perfectly fluently.
"""

from app.agent.chart_utils import condense_chat_chart, condense_layout, sanitize_kpis
from app.agent.number_format import describe, fmt_vi, fmt_vi_compact


# ── chart caps ───────────────────────────────────────────────────────────────

def test_bar_chart_is_capped_with_a_remainder_bucket():
    layout = {"charts": [{
        "type": "bar",
        "data": [{"label": f"SP{i}", "value": 100 - i} for i in range(40)],
    }]}
    condense_layout(layout)
    data = layout["charts"][0]["data"]
    assert len(data) <= 12
    assert data[-1]["label"] == "Khác"
    # The fold-in must preserve the total, not discard the tail.
    assert data[-1]["value"] > 0


def test_line_chart_is_decimated_keeping_chronological_order():
    labels = [f"2025-{m:02d}" for m in range(1, 13)] * 5
    layout = {"charts": [{
        "type": "line",
        "data": [{"label": l, "value": i} for i, l in enumerate(labels)],
    }]}
    condense_layout(layout)
    data = layout["charts"][0]["data"]
    assert len(data) <= 40
    values = [d["value"] for d in data]
    assert values == sorted(values)  # order preserved, not re-sorted by size


def test_multi_series_axis_is_capped_with_all_series_aligned():
    labels = [f"C{i}" for i in range(30)]
    layout = {"charts": [{
        "type": "grouped-bar",
        "labels": labels,
        "series": [
            {"name": "S1", "values": list(range(30))},
            {"name": "S2", "values": list(range(30, 60))},
        ],
    }]}
    condense_layout(layout)
    chart = layout["charts"][0]
    assert len(chart["labels"]) <= 12
    # Misaligned series would silently plot the wrong value against a label.
    for s in chart["series"]:
        assert len(s["values"]) == len(chart["labels"])


def test_scatter_points_are_capped():
    layout = {"charts": [{"type": "scatter",
                          "points": [{"x": i, "y": i} for i in range(1000)]}]}
    condense_layout(layout)
    assert len(layout["charts"][0]["points"]) <= 300


def test_heatmap_matrix_is_truncated_with_its_labels():
    layout = {"charts": [{
        "type": "heatmap",
        "labels": [f"c{i}" for i in range(30)],
        "rowLabels": [f"r{i}" for i in range(30)],
        "matrix": [[1] * 30 for _ in range(30)],
    }]}
    condense_layout(layout)
    c = layout["charts"][0]
    assert len(c["matrix"]) == len(c["rowLabels"])
    assert len(c["matrix"][0]) == len(c["labels"])


def test_chat_chart_capping_keeps_labels_and_values_in_step():
    chart = condense_chat_chart({
        "type": "bar",
        "labels": [f"L{i}" for i in range(50)],
        "values": list(range(50)),
    })
    assert len(chart["labels"]) == len(chart["values"])
    assert len(chart["labels"]) <= 12


def test_malformed_layout_does_not_raise():
    """`layout` comes from executing AI-written code; a buggy script can put
    anything in there, and a crash would take the whole dashboard down."""
    condense_layout({})
    condense_layout({"charts": "not-a-list"})
    condense_layout({"charts": [None, 42, {"type": "bar"}]})
    assert condense_chat_chart(None) is None


# ── KPI sanity guards ────────────────────────────────────────────────────────

def test_implausible_period_delta_is_dropped():
    """REGRESSION: a live dashboard showed "Tổng Doanh Số 25,3 tỷ ▲389,2% so
    với tháng trước" — an all-time total compared against a single month. The
    prompt forbids it and the model did it anyway, so the guard is here."""
    kpis = [{"name": "Tổng Doanh Số", "value": 25283512656,
             "compare_value": 5170000000, "compare_label": "tháng trước"}]
    notes = sanitize_kpis(kpis)
    assert notes and "so nhầm phạm vi" in notes[0]
    assert "compare_value" not in kpis[0]
    assert "compare_label" not in kpis[0]


def test_plausible_delta_is_preserved():
    """The guard must not eat legitimate growth — only comparisons so large
    they imply mismatched scopes."""
    kpis = [{"name": "Doanh số tháng 01", "value": 5_200_000_000,
             "compare_value": 4_700_000_000, "compare_label": "tháng 12"}]
    assert sanitize_kpis(kpis) == []
    assert kpis[0]["compare_value"] == 4_700_000_000


def test_duplicate_kpis_are_marked():
    """"Tổng Doanh Số" and "Tổng Giá Trị Hóa Đơn" resolving to the same number
    are one KPI wearing two names."""
    kpis = [{"name": "Tổng Doanh Số", "value": 25283512656},
            {"name": "Tổng Giá Trị Hóa Đơn", "value": 25283512656}]
    sanitize_kpis(kpis)
    assert "_duplicate_of" not in kpis[0]
    assert kpis[1]["_duplicate_of"] == "Tổng Doanh Số"


def test_single_slice_composition_chart_is_removed():
    """A share chart with one category ("B2C: 100%") draws a picture of nothing."""
    layout = {"charts": [
        {"type": "pie", "title": "Tỷ trọng", "data": [{"label": "B2C", "value": 1}]},
        {"type": "bar", "title": "Khu vực", "data": [{"label": "A", "value": 1}, {"label": "B", "value": 2}]},
    ]}
    condense_layout(layout)
    assert [c["title"] for c in layout["charts"]] == ["Khu vực"]


def test_sanitize_kpis_tolerates_junk():
    assert sanitize_kpis([]) == []
    assert sanitize_kpis("not-a-list") == []
    assert sanitize_kpis([None, 42, {"name": "ok", "value": "n/a"}]) == []


# ── number formatting ────────────────────────────────────────────────────────

def test_full_form_uses_vietnamese_separators():
    assert fmt_vi(25284625156) == "25.284.625.156"
    assert fmt_vi(94338.58) == "94.338,58"


def test_compact_form_only_kicks_in_for_large_magnitudes():
    assert fmt_vi_compact(866838347) == "866,8 triệu"
    assert fmt_vi_compact(29717277717.5) == "29,7 tỷ"
    # A count like 38142 reads better in full than as "38,1 nghìn".
    assert fmt_vi_compact(38142) == ""
    assert fmt_vi_compact(999) == ""


def test_describe_offers_both_forms_so_the_model_never_divides():
    assert describe(866838347) == "866.838.347 (≈ 866,8 triệu)"
    assert describe(38142) == "38.142"


def test_formatting_survives_non_numeric_input():
    assert fmt_vi("n/a") == "n/a"
    assert fmt_vi_compact("n/a") == ""
