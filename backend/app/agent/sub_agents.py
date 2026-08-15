"""Specialized sub-agents for the Orchestrator architecture.

Each sub-agent is a Python generator that yields event dicts
(same types as the old loop.py: step, need_join_confirm, error)
and operates on the shared in-memory state dict.

DataAgent:      list_tables → propose joins → apply  (0-1 LLM calls)
DashboardAgent: execute plan deterministically        (0-1 LLM calls for fixer)
InsightAgent:   write grounded insights               (1 LLM call)
"""

import copy
import json
from typing import Generator

import pandas as pd

from app.agent.sandbox import run_datagen
from app.agent.tools import tool_add_chart, tool_add_kpi, tool_list_tables
from app.ai.pool import AllModelsFailedError, call_ai
from app.ai.prompts import build_join_prompt, build_join_schema
from app.data.merge import apply_join_plan
from app.agent.swarm import build_swarm_context

# merge.py creates these numeric columns for time-grouping, but summing/
# averaging them is meaningless ("average year" isn't a real KPI). Exclude
# them from the numeric-aggregation candidates; they stay valid for group_by.
DERIVED_TIME_COLUMNS = {"month", "quarter", "year"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DataAgent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_datagen_agent(state: dict, user_prompt: str) -> Generator[dict, None, None]:
    """Synthesize a brand-new dataset from the prompt (AI writes a pandas
    script, sandbox executes it) and APPEND the resulting sheets to whatever
    the session already holds — generation must work both as the cold-start
    path (no upload yet) and on top of uploaded files ("thêm cho tôi bảng
    dữ liệu X" mid-session)."""
    yield {"type": "step", "message": "📝 Đang lên kế hoạch sinh bộ dữ liệu theo yêu cầu..."}
    
    user_id = state.get("user_id", "")
    swarm_memory = build_swarm_context(user_id, user_prompt)

    prompt = f"""The user wants to generate and analyze data matching this description: "{user_prompt}"

{swarm_memory}

Write a Python script that creates a dictionary of pandas DataFrames matching the user's description.
Requirements:
1. The script MUST define a dictionary named `dataframes` mapping sheet names (e.g. "Doanh số") to pandas DataFrames.
2. RESEARCH & ACCURACY: If the user's prompt references real-world entities, historical facts, or public metrics (e.g. GDP of countries, population of Vietnam, stock prices of Tesla/Apple, quarterly revenues of tech companies, gold price trends, historical weather), DO NOT generate random fake numbers. Instead, retrieve the ACTUAL historical figures or highly accurate factual estimates from your knowledge base, and hardcode those real-world values in the pandas DataFrame.
3. If the request is for generic mock data (e.g., "generates a coffee shop sales dataset"), use pandas and numpy to generate realistic, complete mock datasets. Make sure there are enough rows (e.g., 100-500 rows depending on the prompt) and typical columns with correct types (dates, text categories, numbers).
4. Do not include markdown output, only the Python code.
5. Name columns and values in Vietnamese if the user prompt is in Vietnamese.

Response format:
Output a SINGLE JSON matching this schema:
{{
  "code": "<clean python code block without code fences>"
}}
"""
    schema = {
        "type": "object",
        "required": ["code"],
        "properties": {
            "code": {"type": "string"}
        }
    }

    try:
        res = call_ai(prompt, schema, tier="strong")
        code = res["code"]
    except Exception as exc:
        yield {"type": "error", "message": f"Không thể lập kế hoạch sinh dữ liệu: {exc}"}
        return

    yield {"type": "step", "message": "⚙️ Đang thực thi mã Python sinh dữ liệu..."}

    # Clean potential code fences
    clean_code = code.strip()
    if clean_code.startswith("```"):
        lines = clean_code.splitlines()
        if lines[0].startswith("```python") or lines[0] == "```":
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        clean_code = "\n".join(lines).strip()

    # AI-generated code always runs in the sandbox, never raw exec.
    run = run_datagen(clean_code)
    if not run["ok"]:
        yield {"type": "error", "message": f"Lỗi khi thực thi mã sinh dữ liệu: {run['error']}"}
        return
    generated_dfs = run["dataframes"]

    from app.data.profiling import profile_dataframe, _grid_from_df
    from app.data.profiler import clean_and_profile

    # Append to (not replace) whatever is already loaded in the session.
    profiles = list(state.get("profiles") or [])
    dataframes = dict(state.get("dataframes") or {})
    raw_grids = dict(state.get("raw_grids") or {})
    n_new = 0

    for sheet_name, df in generated_dfs.items():
        source_id = f"Generated::{sheet_name}"
        # A repeated generation with the same sheet name must not silently
        # overwrite the previous one.
        suffix = 2
        while source_id in dataframes:
            source_id = f"Generated::{sheet_name} ({suffix})"
            suffix += 1
        final_sheet = source_id.split("::", 1)[1]

        df_cleaned, prof = clean_and_profile(df)
        prof_dict = profile_dataframe("Generated", final_sheet, df_cleaned, prof)
        prof_dict["source_id"] = source_id
        profiles.append(prof_dict)
        dataframes[source_id] = df_cleaned
        raw_grids[source_id] = {
            "sheet": final_sheet,
            "grid": _grid_from_df(df_cleaned)
        }
        n_new += 1

    state["dataframes"] = dataframes
    state["profiles"] = profiles
    state["raw_grids"] = raw_grids

    yield {"type": "step", "message": f"📊 Đã tạo xong {n_new} bảng dữ liệu mới."}
    yield {"type": "datagen_done", "n_new": n_new,
           "new_sheets": [p["source_id"] for p in profiles[-n_new:]] if n_new else []}


def run_data_agent(state: dict, user_prompt: str) -> Generator[dict, None, None]:
    """Deterministic flow: list tables → propose joins (query-aware) → apply.

    Uses 0-1 LLM calls.
    Stores ``cleaned_df`` and ``cleaned_schema`` in *state* on success.
    """
    profiles = state.get("profiles") or []
    dataframes = state.get("dataframes") or {}

    if not profiles:
        yield {"type": "step", "message": "📝 Không tìm thấy file dữ liệu. Bắt đầu sinh dữ liệu giả lập từ đầu..."}
        for event in run_datagen_agent(state, user_prompt):
            if event["type"] == "error":
                yield event
                return
            if event["type"] != "datagen_done":
                yield event
        profiles = state.get("profiles") or []
        dataframes = state.get("dataframes") or {}

    # ── Step 1: list tables (deterministic) ─────────────────
    tables_info = tool_list_tables(state, {})
    table_names = [t["source_id"] for t in tables_info["tables"]]
    yield {
        "type": "step",
        "message": f"🔍 Phát hiện {len(profiles)} bảng: {', '.join(table_names)}",
    }

    # ── Step 2: determine joins ─────────────────────────────
    base_table = None
    if state.get("confirmed_joins") is not None:
        # Second pass — user already confirmed joins via the UI.
        joins = state["confirmed_joins"]
        yield {"type": "step", "message": "🔗 Áp dụng cách ghép đã xác nhận..."}

    elif len(profiles) <= 1:
        joins = []
        base_table = profiles[0]["source_id"] if profiles else None

    else:
        # Multiple tables → AI proposes joins based on user request (1 LLM call)
        yield {"type": "step", "message": "🔗 Đang phân tích cách ghép bảng..."}
        source_ids = [p["source_id"] for p in profiles]
        
        user_id = state.get("user_id", "")
        swarm_memory = build_swarm_context(user_id, user_prompt)
        
        prompt = build_join_prompt(profiles, user_prompt)
        if swarm_memory:
            prompt = swarm_memory + "\n\n" + prompt
            
        schema = build_join_schema(source_ids)
        try:
            plan = call_ai(prompt, schema, tier="strong")
        except AllModelsFailedError as exc:
            yield {"type": "error", "message": f"AI không thể đề xuất cách join: {exc}"}
            return

        base_table = plan.get("base_table")
        joins = plan.get("joins", [])
        
        # Log join plan choice
        table_list = [base_table] + [j["right_file"] for j in joins]
        yield {
            "type": "step",
            "message": f"💡 AI chọn bảng gốc: \"{base_table}\" và ghép với: {', '.join(j['right_file'] for j in joins) or '(không ghép thêm)'}",
        }

    # ── Step 3: apply joins (deterministic pandas) ──────────
    yield {"type": "step", "message": "🧹 Đang ghép và làm sạch dữ liệu..."}
    print(f"\n[DataAgent] Applying join plan. Base table: {base_table}, Joins count: {len(joins)}")
    join_report: dict = {}
    try:
        cleaned = apply_join_plan(dataframes, joins, base_table,
                                  semantics=state.get("semantics"), report=join_report)
    except ValueError as exc:
        print(f"[DataAgent] ❌ Join error: {exc}")
        yield {"type": "error", "message": f"Lỗi ghép bảng: {exc}"}
        return

    # A merge that repeats a coarser table's numbers across many rows produces
    # a frame where nothing looks wrong and every SUM is a multiple of the
    # truth. Telling the model about it is not enough — it has no way to spot
    # the column by reading it — so those columns are dropped from the measure
    # list below and the sum is simply never offered.
    state["join_warnings"] = join_report.get("warnings") or []
    state["non_additive_columns"] = sorted(set(join_report.get("non_additive") or []))
    for w in state["join_warnings"]:
        print(f"[JoinGuard] ⚠ {w}")
        yield {"type": "step", "message": f"⚠️ {w}"}

    # Re-run the profiler on the MERGED frame: joins can introduce new type
    # ambiguity, and the planner needs per-column roles/stats on the final data.
    from app.data.profiler import clean_and_profile
    cleaned, merged_profile = clean_and_profile(cleaned)
    state["cleaned_df"] = cleaned
    print(f"[DataAgent] Cleaned df shape: {cleaned.shape}")

    profiles = merged_profile["column_profiles"]
    # Measures = numeric columns the planner may sum/avg; exclude derived time
    # cols (year/quarter as numbers) and id/mostly-empty columns.
    blocked = set(state.get("non_additive_columns") or [])
    numeric = [
        c["name"] for c in profiles
        if c["role"] == "measure" and c["name"] not in DERIVED_TIME_COLUMNS
        and c["name"] not in blocked
    ]
    all_cols = list(cleaned.columns)
    state["cleaned_schema"] = {
        "columns": all_cols,
        "numeric_columns": numeric,
        "column_profiles": profiles,
        # Blocked measures are numeric — they must not fall through into the
        # text bucket, where a planner would try to group by them instead.
        "text_columns": [c for c in all_cols if c not in set(numeric) and c not in blocked],
        "non_additive_columns": sorted(blocked),
        "row_count": len(cleaned),
        "flags": merged_profile["flags"],
    }
    print(f"[Profiler] roles: {[(c['name'], c['role']) for c in profiles]}")
    print(f"[Profiler] coercions: {merged_profile['coercions']} | flags: {merged_profile['flags']}")

    yield {
        "type": "step",
        "message": (
            f"✅ Dữ liệu sạch: {len(cleaned):,} dòng, "
            f"{len(all_cols)} cột ({len(numeric)} cột số)"
        ),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DashboardAgent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── Fixer internals ─────────────────────────────────────────

FIXER_SCHEMA = {
    "type": "object",
    "required": ["kpis", "charts"],
    "properties": {
        "kpis": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "aggregation", "value_column"],
                "properties": {
                    "title": {"type": "string"},
                    "aggregation": {"type": "string", "enum": ["sum", "count", "avg", "growth"]},
                    "value_column": {"type": "string"},
                    "group_by": {"type": "string"},
                },
            },
        },
        "charts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type", "title", "aggregation", "value_column", "group_by"],
                "properties": {
                    "type": {"type": "string", "enum": ["line", "bar", "pie"]},
                    "title": {"type": "string"},
                    "aggregation": {"type": "string", "enum": ["sum", "count", "avg", "growth"]},
                    "value_column": {"type": "string"},
                    "group_by": {"type": "string"},
                },
            },
        },
    },
}


def _fix_plan(errors: list[dict], schema_info: dict) -> dict:
    """One LLM call to fix the items that failed during Pass 1."""
    columns = schema_info.get("columns", [])
    numeric = schema_info.get("numeric_columns", [])
    text = schema_info.get("text_columns", [])

    error_lines = []
    for e in errors:
        item = e["item"]
        error_lines.append(
            f"- [{e['kind']}] \"{item.get('title', '')}\" "
            f"(value_column=\"{item.get('value_column', '')}\", "
            f"group_by=\"{item.get('group_by', '')}\") "
            f"lỗi: {e['error']}"
        )

    prompt = f"""Các mục sau bị lỗi khi tạo dashboard:

{chr(10).join(error_lines)}

Cột SỐ có sẵn: {", ".join(numeric) or "(không có)"}
Cột CHỮ có sẵn: {", ".join(text) or "(không có)"}

Hãy sửa lại các mục bị lỗi, CHỈ dùng tên cột trong danh sách trên.
- sum/avg/growth bắt buộc dùng cột SỐ cho value_column.
- growth cần group_by là cột thời gian.
- Nếu không sửa được, trả về mảng rỗng cho loại đó.

Trả lời DUY NHẤT JSON: {{"kpis": [...], "charts": [...]}}"""

    constrained = copy.deepcopy(FIXER_SCHEMA)
    for key in ("kpis", "charts"):
        items_props = constrained["properties"][key]["items"]["properties"]
        items_props["value_column"]["enum"] = columns  # count needs non-numeric cols too
        if "group_by" in items_props:
            items_props["group_by"]["enum"] = columns

    return call_ai(prompt, constrained, tier="strong")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# InsightAgent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INSIGHT_SCHEMA = {
    "type": "object",
    "required": ["insights"],
    "properties": {
        "insights": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 3,
        }
    },
}


def generate_insights(
    kpis: list[dict], charts: list[dict], user_prompt: str, trend_context: str = "",
) -> list[str]:
    """One LLM call → 1-3 insights grounded on real computed values.

    Pure function (no state coupling) so both the dashboard live path
    (code_interpreter) and the legacy orchestrator can reuse it. KPI titles
    are read from either "title" or "name" since the two layout producers use
    different keys for the same field. ``trend_context`` is the pre-formatted
    output of data.trends.format_trend_for_prompt() — deterministic
    trend/forecast/outlier facts computed by pandas/statsmodels, NOT by the
    LLM, so the model can cite a forecast without being able to invent one.
    Raises AllModelsFailedError - the caller decides how to degrade
    (dashboards must not fail just because the insight write-up failed)."""
    from app.agent.number_format import NUMBER_STYLE_RULES, describe

    kpi_lines = []
    for k in kpis:
        title = k.get("title") or k.get("name") or "?"
        kpi_lines.append(f"- {title}: {describe(k.get('value'))}{k.get('unit', '')}")

    chart_lines = []
    for c in charts:
        top = c["data"][:3] if c.get("data") else []
        top_str = ", ".join(f"{d['label']}={describe(d['value'])}" for d in top)
        chart_lines.append(f"- {c.get('title', '?')} ({c.get('type', '?')}): top → {top_str}")

    trend_block = f"\n{trend_context}\n" if trend_context else ""

    prompt = f"""Dưới đây là KPI và biểu đồ đã tính từ dữ liệu thật:

KPIs:
{chr(10).join(kpi_lines) or "(không có)"}

Biểu đồ:
{chr(10).join(chart_lines) or "(không có)"}
{trend_block}
Yêu cầu gốc: "{user_prompt}"

Viết 1-3 đoạn nhận xét/insight kinh doanh bằng tiếng Việt.
- CHỈ nhắc các con số thật ở trên, KHÔNG bịa.
- Nêu rõ điểm nổi bật, xu hướng, bất thường nếu có.
- Nếu có phần "Phân tích xu hướng" ở trên, hãy lồng dự báo/bất thường/biến động mạnh nhất vào nhận xét (đây là số liệu đã tính sẵn, không phải ước lượng của bạn).
- Mỗi đoạn 2-4 câu, súc tích.

{NUMBER_STYLE_RULES}

Trả lời DUY NHẤT JSON: {{"insights": ["đoạn 1", "đoạn 2"]}}"""

    result = call_ai(prompt, INSIGHT_SCHEMA, tier="strong")
    insights = result.get("insights", [])

    # Grounding gate (deterministic, no LLM): every material number in the
    # written insights must trace back to a real computed value. One retry
    # with the violations spelled out; paragraphs still failing after that
    # are dropped - a missing insight is better than a made-up number.
    from app.ai.harness import collect_ground_truth, collect_numbers_from_text, verify_numbers

    ground_truth = collect_ground_truth(kpis, charts) | collect_numbers_from_text(trend_context)

    def _violations_of(paras: list[str]) -> list[dict]:
        return verify_numbers("\n".join(paras), ground_truth)

    violations = _violations_of(insights)
    if violations:
        bad_tokens = ", ".join(v["token"] for v in violations[:8])
        retry_prompt = (
            prompt
            + f"\n\nBÀI VIẾT TRƯỚC CỦA BẠN chứa các con số KHÔNG có trong dữ liệu thật: {bad_tokens}. "
              "Viết lại, CHỈ dùng đúng các con số xuất hiện ở phần KPI/Biểu đồ/Phân tích xu hướng bên trên."
        )
        try:
            result = call_ai(retry_prompt, INSIGHT_SCHEMA, tier="strong")
            insights = result.get("insights", insights)
        except AllModelsFailedError:
            pass
        kept = [p for p in insights if not verify_numbers(p, ground_truth)]
        dropped = len(insights) - len(kept)
        if dropped:
            print(f"[grounding] dropped {dropped} insight paragraph(s) with unverifiable numbers")
        insights = kept

    return insights

