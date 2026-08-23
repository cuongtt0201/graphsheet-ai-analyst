"""A month with five days of data must not be drawn beside nine full months.

The warning for this already existed and worked -- the model was told, and it
obeyed in prose. But the chart it wrote still plotted the raw partial value, so
a caption reading "tăng trưởng mạnh mẽ" sat under a cliff falling off the
bottom of the frame. Readers believe the picture.
"""

import pandas as pd
from app.agent.chart_utils import drop_incomplete_period
from app.data.trends import incomplete_last_period, period_coverage_note


def _months(*pairs):
    return [{"label": label, "value": value} for label, value in pairs]


REAL_TREND = _months(
    ("2025-05", 913_883_329), ("2025-06", 1_166_601_453), ("2025-07", 1_311_502_501),
    ("2025-08", 2_084_303_900), ("2025-09", 2_594_920_107), ("2025-10", 3_332_383_842),
    ("2025-11", 3_405_495_293), ("2025-12", 4_475_576_864), ("2026-01", 5_168_825_563),
    ("2026-02", 831_132_304),   # five days of February
)


def test_detects_a_month_that_stopped_on_the_fifth():
    df = pd.DataFrame({"Ngay": pd.date_range("2025-05-01", "2026-02-05", freq="D")})
    found = incomplete_last_period(df, "Ngay")

    assert found is not None
    label, coverage = found
    assert label == "2026-02"
    assert 0.15 < coverage < 0.20   # 5 of 28 days


def test_a_finished_month_is_left_alone():
    df = pd.DataFrame({"Ngay": pd.date_range("2025-05-01", "2026-01-31", freq="D")})
    assert incomplete_last_period(df, "Ngay") is None
    assert period_coverage_note(df, "Ngay") == ""


def test_the_warning_and_the_trim_agree_because_they_share_one_detector():
    """They disagreed before: the prose was corrected and the chart was not."""
    df = pd.DataFrame({"Ngay": pd.date_range("2025-05-01", "2026-02-05", freq="D")})
    label = incomplete_last_period(df, "Ngay")[0]

    assert label in period_coverage_note(df, "Ngay")
    layout = {"charts": [{"title": "Xu hướng doanh thu theo tháng", "type": "line", "data": list(REAL_TREND)}]}
    assert drop_incomplete_period(layout, label) == 1


def test_the_cliff_is_removed_and_the_growth_survives():
    layout = {"charts": [{"title": "Xu hướng doanh thu theo tháng", "type": "line", "data": list(REAL_TREND)}]}

    assert drop_incomplete_period(layout, "2026-02") == 1
    data = layout["charts"][0]["data"]
    assert data[-1]["label"] == "2026-01"
    assert len(data) == 9
    # What remains rises monotonically -- the drop was the artifact, not a fact.
    values = [d["value"] for d in data]
    assert values == sorted(values)


def test_the_trim_is_stated_in_the_title_not_done_quietly():
    layout = {"charts": [{"title": "Xu hướng doanh thu", "type": "line", "data": list(REAL_TREND)}]}
    drop_incomplete_period(layout, "2026-02")

    title = layout["charts"][0]["title"]
    assert "2026-02" in title and "chưa trọn" in title


def test_only_the_last_point_and_only_a_matching_label_is_touched():
    mid = _months(("2026-02", 1), ("2025-01", 2), ("2025-02", 3))
    other = _months(("A", 1), ("B", 2), ("C", 3))
    layout = {"charts": [
        {"title": "Nhãn khớp nhưng ở giữa", "data": list(mid)},
        {"title": "Không phải chuỗi thời gian", "data": list(other)},
    ]}

    assert drop_incomplete_period(layout, "2026-02") == 0
    assert layout["charts"][0]["data"] == mid
    assert layout["charts"][1]["data"] == other


def test_a_series_too_short_to_read_as_a_trend_is_left_whole():
    """Two points in, one point out is not a trend."""
    layout = {"charts": [{"title": "Ngắn", "data": _months(("2026-01", 5), ("2026-02", 1))}]}
    assert drop_incomplete_period(layout, "2026-02") == 0
    assert len(layout["charts"][0]["data"]) == 2


def test_no_label_means_no_change():
    layout = {"charts": [{"title": "x", "data": list(REAL_TREND)}]}
    assert drop_incomplete_period(layout, None) == 0
    assert len(layout["charts"][0]["data"]) == 10


def test_the_guard_runs_where_a_layout_is_read_not_only_where_it_is_built():
    """Trimming at build time alone leaves every existing dashboard broken.

    A layout lives in session state and is read back later by the slide builder
    and the report exporter. A dashboard built before this guard existed keeps
    its cliff forever unless the guard also runs on the way out -- which is
    exactly what happened: the fix shipped, and the next deck still drew it.
    """
    from app.agent.chart_utils import trim_incomplete_period

    state = {
        "cleaned_df": pd.DataFrame({
            "Ngay": pd.date_range("2025-05-01", "2026-02-05", freq="D"),
            "DoanhThu": 1.0,
        }),
        "cleaned_schema": {"column_profiles": [
            {"name": "Ngay", "role": "date", "dtype": "datetime64[ns]"},
            {"name": "DoanhThu", "role": "measure", "dtype": "float64", "sum": 100.0},
        ]},
    }
    layout = {"charts": [{"title": "Xu hướng", "type": "line", "data": list(REAL_TREND)}]}

    assert trim_incomplete_period(state, layout) == 1
    assert layout["charts"][0]["data"][-1]["label"] == "2026-01"


def test_trimming_twice_changes_nothing_the_second_time():
    """Producers and consumers both call it, so it has to be idempotent."""
    from app.agent.chart_utils import drop_incomplete_period

    layout = {"charts": [{"title": "Xu hướng", "type": "line", "data": list(REAL_TREND)}]}
    first = drop_incomplete_period(layout, "2026-02")
    after_first = [d["label"] for d in layout["charts"][0]["data"]]
    second = drop_incomplete_period(layout, "2026-02")

    assert (first, second) == (1, 0)
    assert [d["label"] for d in layout["charts"][0]["data"]] == after_first


def test_the_reader_does_not_mutate_the_stored_layout():
    """A view for one caller must not quietly edit what the session holds."""
    from app.routers.chat import _layout_view

    stored_charts = [{"title": "Xu hướng", "type": "line", "data": list(REAL_TREND)}]
    state = {
        "layout": {"charts": stored_charts, "kpis": [], "insights": []},
        "cleaned_df": pd.DataFrame({
            "Ngay": pd.date_range("2025-05-01", "2026-02-05", freq="D"),
            "DoanhThu": 1.0,
        }),
        "cleaned_schema": {"column_profiles": [
            {"name": "Ngay", "role": "date", "dtype": "datetime64[ns]"},
            {"name": "DoanhThu", "role": "measure", "dtype": "float64", "sum": 100.0},
        ]},
    }

    view = _layout_view(state)
    assert len(view["charts"][0]["data"]) == 9
    # The stored layout is untouched, so nothing else in the session shifts.
    assert len(stored_charts[0]["data"]) == 10


def test_the_verdict_is_recorded_so_later_requests_can_act_on_it():
    """cleaned_df is never persisted -- it lives only for the build request.

    A reader on any later request therefore has no dates to re-derive the
    verdict from, and the guard did exactly nothing while appearing to run.
    The build writes the answer onto the layout so the guard survives the
    request that produced it.
    """
    from app.agent.chart_utils import trim_incomplete_period

    state = {
        "cleaned_df": pd.DataFrame({
            "Ngay": pd.date_range("2025-05-01", "2026-02-05", freq="D"),
            "DoanhThu": 1.0,
        }),
        "cleaned_schema": {"column_profiles": [
            {"name": "Ngay", "role": "date"},
            {"name": "DoanhThu", "role": "measure", "sum": 100.0},
        ]},
    }
    layout = {"charts": [{"title": "Xu hướng", "type": "line", "data": list(REAL_TREND)}]}

    assert trim_incomplete_period(state, layout) == 1
    assert layout["incomplete_period"] == "2026-02"


def test_a_marked_layout_is_trimmed_with_no_dataframe_at_all():
    from app.agent.chart_utils import trim_incomplete_period

    layout = {
        "charts": [{"title": "Xu hướng", "type": "line", "data": list(REAL_TREND)}],
        "incomplete_period": "2026-02",
    }
    assert trim_incomplete_period({}, layout) == 1
    assert layout["charts"][0]["data"][-1]["label"] == "2026-01"


def test_an_unmarked_layout_with_no_dataframe_is_left_alone():
    """Nothing to go on is not licence to guess which point to delete."""
    from app.agent.chart_utils import trim_incomplete_period

    layout = {"charts": [{"title": "Xu hướng", "type": "line", "data": list(REAL_TREND)}]}
    assert trim_incomplete_period({}, layout) == 0
    assert len(layout["charts"][0]["data"]) == 10
