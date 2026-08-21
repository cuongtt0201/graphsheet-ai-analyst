"""Unit tests for Server-Side Chart Renderer."""

from app.agent.chart_renderer import render_chart_to_png


def test_render_vertical_bar_chart():
    chart = {
        "type": "bar",
        "title": "Doanh thu theo vùng",
        "labels": ["Bắc", "Trung", "Nam"],
        "values": [120, 85, 210],
    }
    buf = render_chart_to_png(chart)
    assert buf is not None
    data = buf.getvalue()
    assert len(data) > 1000
    assert data[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic header


def test_render_pie_chart():
    chart = {
        "type": "pie",
        "title": "Cơ cấu sản phẩm",
        "labels": ["SP1", "SP2", "SP3"],
        "values": [40, 35, 25],
    }
    buf = render_chart_to_png(chart)
    assert buf is not None
    assert buf.getvalue()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_line_chart():
    chart = {
        "type": "line",
        "title": "Xu hướng theo tháng",
        "labels": ["T1", "T2", "T3", "T4"],
        "values": [10.5, 15.2, 14.8, 22.0],
    }
    buf = render_chart_to_png(chart)
    assert buf is not None
    assert buf.getvalue()[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_empty_chart_returns_none():
    assert render_chart_to_png({}) is None
    assert render_chart_to_png({"type": "bar", "values": []}) is None


def test_chart_values_are_numeric_after_condensing():
    """Gemini schemas take one type per field, so the model returns `values` as
    strings. Everything downstream expects numbers, so the boundary converts."""
    from app.agent.chart_utils import condense_chat_chart

    chart = condense_chat_chart({
        "type": "bar",
        "title": "Doanh thu theo miền",
        "labels": ["Bắc", "Trung", "Nam"],
        "values": ["736000000", "653000000", "635000000"],
    })

    assert chart["values"] == [736000000.0, 653000000.0, 635000000.0]
    assert all(isinstance(v, float) for v in chart["values"])


def test_unparseable_chart_values_become_zero_not_a_crash():
    from app.agent.chart_utils import condense_chat_chart

    chart = condense_chat_chart({
        "type": "bar",
        "labels": ["A", "B"],
        "values": ["", "n/a"],
    })
    assert chart["values"] == [0.0, 0.0]
