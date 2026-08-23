"""High-DPI Server-side Chart Rendering Engine.

Renders chart definitions (bar, line, pie, scatter, area, etc.) into high-quality
PNG image bytes for embedding directly into Word (.docx), PowerPoint (.pptx),
and Excel (.xlsx) reports.
"""

from __future__ import annotations

import io
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Modern theme color palette matching the web interface
DEFAULT_COLORS = [
    "#10B981", "#2563EB", "#F59E0B", "#EC4899", "#8B5CF6",
    "#06B6D4", "#F97316", "#14B8A6", "#6366F1", "#84CC16"
]


def render_chart_to_png(chart: dict[str, Any], width_in: float = 7.0, height_in: float = 4.2, dpi: int = 200) -> io.BytesIO | None:
    """Render a chart definition dictionary into PNG image bytes.
    
    Supports 'bar', 'horizontal-bar', 'line', 'area', 'pie', 'donut', 'scatter',
    'funnel' and 'vega' -- 9 of the 26 types MiniChart draws in the browser.
    Anything else returns None and the chart is silently missing from the file.
    """
    if not chart or not isinstance(chart, dict):
        return None

    # Accept either shape. Charts arrive here straight from state["layout"],
    # which stores {data: [{label, value}]}, while everything below reads
    # labels/values -- so every report exported from an auto-built dashboard
    # came out with no charts at all, and said nothing about it.
    from app.agent.chart_utils import normalize_chart

    chart = normalize_chart(chart)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker
    except ImportError:
        logger.warning("[chart_renderer] matplotlib is not installed, skipping chart image rendering")
        return None

    chart_type = chart.get("type", "bar")
    title = chart.get("title", "")
    labels = chart.get("labels") or []
    values = chart.get("values") or []

    # Handle Vega-Lite spec if labels/values are not extracted directly
    if chart_type == "vega" and not values and "vegaLiteSpec" in chart:
        spec = chart.get("vegaLiteSpec") or {}
        data_values = spec.get("data", {}).get("values", [])
        if isinstance(data_values, list) and data_values:
            # Try to infer x and y from encoding
            enc = spec.get("encoding", {})
            x_field = enc.get("x", {}).get("field")
            y_field = enc.get("y", {}).get("field")
            if x_field and y_field:
                labels = [str(d.get(x_field, "")) for d in data_values]
                values = [d.get(y_field, 0) for d in data_values]
                chart_type = "bar" if spec.get("mark") == "bar" else "line"

    if not values:
        return None

    # Clean numeric values
    clean_values = []
    clean_labels = []
    for lbl, val in zip(labels, values):
        try:
            v_float = float(val) if val is not None else 0.0
            clean_values.append(v_float)
            clean_labels.append(str(lbl))
        except (ValueError, TypeError):
            continue

    if not clean_values:
        return None

    # Truncate to top items for visual clarity if too many points
    if len(clean_values) > 20 and chart_type in ("bar", "pie", "donut", "doughnut"):
        clean_values = clean_values[:20]
        clean_labels = clean_labels[:20]

    # Setup Matplotlib Figure with clean aesthetics
    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=dpi)
    fig.patch.set_facecolor("#FFFFFF")
    ax.set_facecolor("#FAFAFA")

    # Styling grid & spines
    ax.grid(axis="y", color="#E5E7EB", linestyle="--", linewidth=0.8, alpha=0.7, zorder=0)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color("#D1D5DB")
        ax.spines[spine].set_linewidth(0.8)

    colors = [DEFAULT_COLORS[i % len(DEFAULT_COLORS)] for i in range(len(clean_values))]

    try:
        # ChartType calls it "donut"; this branch only ever matched "doughnut",
        # a spelling nothing in the system emits. So every donut chart fell
        # through to the bar default and a report showed a bar chart where the
        # dashboard showed a ring -- same numbers, different chart, no warning.
        if chart_type in ("pie", "donut", "doughnut"):
            # Pie / Doughnut Chart
            ax.grid(False)
            ax.axis("off")
            is_ring = chart_type in ("donut", "doughnut")
            wedgeprops = dict(width=0.45 if is_ring else 1.0, edgecolor="#FFFFFF", linewidth=2)
            wedges, texts, autotexts = ax.pie(
                clean_values,
                labels=clean_labels if len(clean_labels) <= 8 else None,
                autopct="%1.1f%%",
                startangle=140,
                colors=colors,
                wedgeprops=wedgeprops,
                textprops=dict(color="#374151", fontsize=9),
            )
            for autotext in autotexts:
                autotext.set_color("#FFFFFF")
                autotext.set_weight("bold")
                autotext.set_fontsize(8)

        elif chart_type in ("horizontal-bar", "funnel"):
            # Horizontal Bar Chart
            y_pos = range(len(clean_labels))
            bars = ax.barh(y_pos, clean_values, color=colors[0], edgecolor="none", height=0.6, zorder=3)
            ax.set_yticks(y_pos)
            ax.set_yticklabels(clean_labels, fontsize=9, color="#374151")
            ax.invert_yaxis()
            ax.grid(axis="x", color="#E5E7EB", linestyle="--", linewidth=0.8, alpha=0.7, zorder=0)
            ax.grid(False, axis="y")
            ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: f"{x:,.0f}"))

        elif chart_type in ("line", "area"):
            # Line / Area Chart
            x_pos = range(len(clean_labels))
            ax.plot(x_pos, clean_values, color=DEFAULT_COLORS[1], marker="o", linewidth=2.5, markersize=5, zorder=4)
            if chart_type == "area":
                ax.fill_between(x_pos, clean_values, color=DEFAULT_COLORS[1], alpha=0.15, zorder=3)
            ax.set_xticks(x_pos)
            ax.set_xticklabels(clean_labels, rotation=35 if len(clean_labels) > 6 else 0, ha="right" if len(clean_labels) > 6 else "center", fontsize=8.5, color="#374151")
            ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: f"{x:,.0f}"))

        elif chart_type == "scatter":
            # Scatter Plot
            x_pos = range(len(clean_labels))
            ax.scatter(x_pos, clean_values, color=DEFAULT_COLORS[4], s=50, alpha=0.8, edgecolors="none", zorder=4)
            ax.set_xticks(x_pos)
            ax.set_xticklabels(clean_labels, rotation=35 if len(clean_labels) > 6 else 0, ha="right" if len(clean_labels) > 6 else "center", fontsize=8.5, color="#374151")
            ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: f"{x:,.0f}"))

        else:
            # Default Vertical Bar Chart
            x_pos = range(len(clean_labels))
            bars = ax.bar(x_pos, clean_values, color=colors[0], edgecolor="none", width=0.55, zorder=3)
            ax.set_xticks(x_pos)
            ax.set_xticklabels(clean_labels, rotation=35 if len(clean_labels) > 6 else 0, ha="right" if len(clean_labels) > 6 else "center", fontsize=8.5, color="#374151")
            ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: f"{x:,.0f}"))

        if title:
            ax.set_title(title, fontsize=11, fontweight="bold", color="#111827", pad=12)

        plt.tight_layout()
        img_buffer = io.BytesIO()
        plt.savefig(img_buffer, format="png", dpi=dpi, facecolor="#FFFFFF", bbox_inches="tight")
        img_buffer.seek(0)
        return img_buffer

    except Exception as exc:
        logger.warning(f"[chart_renderer] Failed to render chart to image: {exc}")
        return None
    finally:
        # Exactly one close, on every path. Closing in both the try and the
        # except branches meant a failure raised BEFORE savefig closed the
        # figure twice, and any failure inside subplots() itself hit the
        # except with `fig` unbound.
        plt.close(fig)
