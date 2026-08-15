"""Executive "report-to-boss" narrative generator (Phase 6). One LLM call,
grounded on the SAME real computed KPI/chart values and deterministic trend
facts (app/data/trends.py) that already back the shorter dashboard insights —
this just re-shapes them into the structure an analyst hands to a manager:
summary → findings → anomalies → recommendations.
"""

from app.ai.pool import call_ai

REPORT_SCHEMA = {
    "type": "object",
    "required": ["executive_summary", "key_findings", "recommendations"],
    "properties": {
        "executive_summary": {
            "type": "string",
            "description": "2-3 câu tóm tắt tổng quan cho lãnh đạo, nêu con số quan trọng nhất trước tiên.",
        },
        "key_findings": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 6,
            "description": "Mỗi phát hiện gắn với một KPI/biểu đồ cụ thể và con số thật.",
        },
        "anomalies": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
            "description": "Điểm bất thường/đáng chú ý (có thể rỗng nếu không có).",
        },
        "recommendations": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 5,
            "description": "Đề xuất hành động tiếp theo, cụ thể và khả thi.",
        },
    },
}

REPORT_PROMPT = """Hãy chuẩn bị báo cáo trình lãnh đạo (sếp) dựa trên dashboard vừa dựng.
Đây là toàn bộ dữ liệu THẬT đã tính — KHÔNG được bịa thêm số nào ngoài các số dưới đây.

KPIs:
{kpi_lines}

Biểu đồ:
{chart_lines}
{trend_block}
Nhận xét đã có (tham khảo, có thể diễn giải lại):
{insight_lines}

Yêu cầu gốc của dashboard: "{user_prompt}"

Viết báo cáo điều hành (tiếng Việt) theo cấu trúc:
1. executive_summary: 2-3 câu, nêu con số/kết luận quan trọng nhất TRƯỚC TIÊN — sếp đọc câu đầu phải hiểu được điều cốt lõi.
2. key_findings: mỗi ý gắn với 1 KPI/biểu đồ cụ thể + con số thật.
3. anomalies: điểm bất thường (dùng phần "Phân tích xu hướng" nếu có — đó là số đã tính sẵn bằng pandas/statsmodels, không phải bạn tự đoán). Để mảng rỗng nếu không có gì bất thường.
4. recommendations: đề xuất hành động cụ thể, khả thi, bám sát số liệu (không đưa lời khuyên chung chung).

{number_style_rules}

Trả lời DUY NHẤT JSON đúng schema."""


def generate_executive_report(
    kpis: list[dict], charts: list[dict], insights: list[str],
    user_prompt: str, trend_context: str = "",
) -> dict:
    """Raises AllModelsFailedError on failure — caller decides how to degrade
    (the dashboard itself must already exist independently of this report).

    kpis/charts trace back to AI-generated code output — never write
    `x.get(...) or fallback` on their fields: if a buggy script assigned a raw
    DataFrame/Series somewhere, `or` would evaluate its truthiness and pandas
    raises "The truth value of a DataFrame is ambiguous" instead of a clean
    fallback. isinstance()/explicit None checks never call __bool__.
    """
    from app.agent.number_format import describe

    kpi_lines_parts = []
    for k in kpis:
        title = k.get("title")
        if not isinstance(title, str) or not title:
            title = k.get("name") if isinstance(k.get("name"), str) else "?"
        kpi_lines_parts.append(f"- {title}: {describe(k.get('value'))}{k.get('unit', '')}")
    kpi_lines = "\n".join(kpi_lines_parts) or "(không có)"

    chart_lines_parts = []
    for c in charts:
        data = c.get("data")
        data = data if isinstance(data, list) else []
        top = ", ".join(f"{d['label']}={describe(d['value'])}" for d in data[:5])
        chart_lines_parts.append(f"- {c.get('title', '?')} ({c.get('type', '?')}): {top}")
    chart_lines = "\n".join(chart_lines_parts) or "(không có)"

    insight_lines = "\n".join(f"- {t}" for t in insights) or "(chưa có)"
    trend_block = f"\n{trend_context}\n" if trend_context else ""

    from app.agent.number_format import NUMBER_STYLE_RULES

    prompt = REPORT_PROMPT.format(
        kpi_lines=kpi_lines, chart_lines=chart_lines, trend_block=trend_block,
        insight_lines=insight_lines, user_prompt=user_prompt,
        number_style_rules=NUMBER_STYLE_RULES,
    )
    report = call_ai(prompt, REPORT_SCHEMA, tier="strong")

    # Grounding gate (deterministic, no LLM): a report headed to a manager is
    # the LAST place a hallucinated number may survive. One retry with the
    # violations named; after that, still-failing list items are dropped and a
    # still-failing summary falls back to a template built purely from real
    # KPI values (grounded by construction).
    from app.ai.harness import collect_ground_truth, collect_numbers_from_text, verify_numbers

    ground_truth = (
        collect_ground_truth(kpis, charts)
        | collect_numbers_from_text(trend_context)
        | collect_numbers_from_text("\n".join(insights))
    )

    def _all_text(rep: dict) -> str:
        parts = [rep.get("executive_summary", "")]
        for key in ("key_findings", "anomalies", "recommendations"):
            parts.extend(x for x in rep.get(key, []) if isinstance(x, str))
        return "\n".join(parts)

    violations = verify_numbers(_all_text(report), ground_truth)
    if violations:
        bad_tokens = ", ".join(v["token"] for v in violations[:8])
        retry_prompt = (
            prompt
            + f"\n\nBÁO CÁO TRƯỚC CỦA BẠN chứa các con số KHÔNG có trong dữ liệu thật: {bad_tokens}. "
              "Viết lại toàn bộ báo cáo, CHỈ dùng đúng các con số xuất hiện trong phần KPI/Biểu đồ/Phân tích xu hướng/Nhận xét bên trên."
        )
        try:
            report = call_ai(retry_prompt, REPORT_SCHEMA, tier="strong")
        except Exception:  # noqa: BLE001 - keep first draft, filter below
            pass

        dropped = 0
        for key in ("key_findings", "anomalies", "recommendations"):
            items = [x for x in report.get(key, []) if isinstance(x, str)]
            kept = [x for x in items if not verify_numbers(x, ground_truth)]
            dropped += len(items) - len(kept)
            report[key] = kept
        if verify_numbers(report.get("executive_summary", ""), ground_truth):
            report["executive_summary"] = "Tổng quan số liệu chính: " + "; ".join(
                kpi_lines_parts) if kpi_lines_parts else "Xem số liệu KPI trong dashboard."
            dropped += 1
        if dropped:
            print(f"[grounding] report: dropped/replaced {dropped} block(s) with unverifiable numbers")

    return report
