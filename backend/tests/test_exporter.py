"""Unit tests for Multi-Format Enterprise Exporter (.docx, .pptx, .xlsx)."""

import pandas as pd
from app.agent.exporter import (
    export_data_and_charts_to_xlsx,
    export_report_to_docx,
    export_report_to_pptx,
)


def test_export_report_to_docx_with_embedded_charts():
    report = {
        "executive_summary": "Doanh thu Q3 tăng trưởng 25%, vượt chỉ tiêu đề ra.",
        "key_findings": [
            "Miền Nam đóng góp 55% tổng doanh số.",
            "Ngành hàng tiêu dùng tăng trưởng nhanh nhất.",
        ],
        "anomalies": ["Chi phí logistics tăng đột biến trong tháng 8."],
        "recommendations": ["Tái đàm phán hợp đồng vận chuyển với đối tác B."],
    }
    charts = [
        {
            "type": "bar",
            "title": "Doanh số theo miền",
            "labels": ["Bắc", "Trung", "Nam"],
            "values": [300, 150, 550],
        }
    ]

    buf = export_report_to_docx("Báo Cáo Hoạt Động Kinh Doanh Q3", report, charts=charts)
    assert buf is not None
    data = buf.getvalue()
    assert len(data) > 3000
    # ZIP / DOCX magic signature PK\x03\x04
    assert data[:4] == b"PK\x03\x04"


def test_export_report_to_pptx_presentation():
    report = {
        "executive_summary": "Tổng quan kết quả kinh doanh quý 3.",
        "key_findings": ["Doanh số tăng 20%."],
        "recommendations": ["Tối ưu kênh phân phối."],
    }
    charts = [
        {
            "type": "line",
            "title": "Biến động theo tuần",
            "labels": ["W1", "W2", "W3"],
            "values": [10, 20, 30],
        }
    ]

    buf = export_report_to_pptx("Báo Cáo Slide Ban Giám Đốc", report, charts=charts)
    assert buf is not None
    data = buf.getvalue()
    assert len(data) > 3000
    assert data[:4] == b"PK\x03\x04"


def test_export_data_and_charts_to_xlsx():
    dfs = {
        "DoanhThu": pd.DataFrame({"KhuVuc": ["Bac", "Nam"], "DoanhSo": [1000000.0, 2500000.0]}),
    }
    charts = [
        {
            "type": "pie",
            "title": "Cơ cấu doanh thu",
            "labels": ["Bac", "Nam"],
            "values": [1000000, 2500000],
        }
    ]

    buf = export_data_and_charts_to_xlsx("Báo Cáo Excel Tổng Hợp", dfs, charts=charts, summary="Tóm tắt doanh số.")
    assert buf is not None
    data = buf.getvalue()
    assert len(data) > 3000
    assert data[:4] == b"PK\x03\x04"


def test_missing_render_library_raises_instead_of_empty_buffer(monkeypatch):
    """A 0-byte .pptx downloads as a "success" no program can open.

    The failure has to reach the router as an exception so it becomes a 503.
    """
    import builtins

    import pytest

    from app.agent.exporter import ExportDependencyError

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pptx" or name.startswith("pptx."):
            raise ImportError("No module named 'pptx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ExportDependencyError):
        export_report_to_pptx("Báo Cáo", {"executive_summary": "x"})
