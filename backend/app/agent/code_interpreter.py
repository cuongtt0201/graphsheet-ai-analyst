import re

import pandas as pd
from app.ai.pool import AllModelsFailedError, call_ai
from app.agent.chart_utils import condense_layout
from app.agent.sandbox import run_layout_script
from app.agent.skills_manager import (
    get_relevant_skills,
    get_skills_source,
    load_skills_into_env,
    save_new_skill,
)
from app.agent.sub_agents import generate_insights
from app.data.trends import analyze_trend, format_trend_for_prompt, pick_trend_columns
from app.memory import graph
from app.agent.swarm import build_swarm_context

CODE_SCHEMA = {
    "type": "object",
    "required": ["plan", "code"],
    "properties": {
        "plan": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Step-by-step planning before writing code"
        },
        "code": {
            "type": "string",
            "description": "The complete Python script that calculates data and outputs a layout dictionary"
        }
    }
}

CODE_PROMPT = """The user wants to visualize their dataset as a dashboard.
An in-memory pandas DataFrame named `df` contains the uploaded clean dataset.
If multiple files/sheets were uploaded, they have ALREADY been joined/merged into
this single `df` — there is NO second dataframe, and any table/sheet names you
see below are just describing where `df`'s columns originally came from, NOT
separate variables you can reference. `df` is the ONLY data variable in scope.
Your task is to:
1. Write a step-by-step PLAN to solve the user request.
2. Write a SINGLE Python script to perform the data analysis, build the dashboard layout, and assign the output to a dictionary variable named `layout`.

{behaviors_text}

DATA SCHEMA / METADATA (BabelTele compressed format):
{schema_info}

BabelTele notation guide:
- T: {{Table Name}} (R: {{Row Count}})
- #: Numeric measure column (can be aggregated like sum, mean)
- $: Category / dimension column (usually for group-by)
- @: Datetime column
- ∅: Null percentage
- Σ: Sum, μ: Mean
- D: Distinct count
- [...] : Sample values

USER REQUEST:
"{user_prompt}"

REQUIRED `layout` DICTIONARY STRUCTURE (your script MUST populate this variable):
layout = {{
    "kpis": [
        {{
            "name": "KPI Name (in Vietnamese)",
            "value": 123456,
            # OPTIONAL but strongly preferred when a date column exists: the same
            # measure for the PREVIOUS comparable period, computed with pandas
            # exactly like `value` was. A number with no reference point tells
            # the reader nothing - "25 tỷ" is meaningless until it sits next to
            # last month's figure.
            "compare_value": 118000,
            "compare_label": "tháng trước"   # or "cùng kỳ năm trước"
        }}
    ],
    "charts": [
        {{
            # Single-series chart: use "data" (label/value pairs).
            "title": "Chart Title (in Vietnamese)",
            "type": "bar",  # see CHART TYPE GUIDE below for the full list
            # role + size drive dashboard COMPOSITION (see DASHBOARD LAYOUT below).
            "role": "trend",   # "trend" | "analysis" | "breakdown" | "detail"
            "size": "lg",      # "sm" | "md" | "lg"
            "data": [
                {{"label": "Label A (in Vietnamese)", "value": 100}},
                {{"label": "Label B (in Vietnamese)", "value": 200}}
            ]
        }},
        {{
            # Multi-series chart (stacked-bar / grouped-bar / stacked-area / multi-line / combo / radar):
            # use "series" instead of "data" — one entry per series, each with its own values
            # aligned to the SAME "labels" list.
            "title": "Chart Title (in Vietnamese)",
            "type": "grouped-bar",
            "labels": ["Label A", "Label B"],
            "series": [
                {{"name": "Series 1 (in Vietnamese)", "values": [100, 200]}},
                {{"name": "Series 2 (in Vietnamese)", "values": [80, 150]}}
            ]
        }},
        {{
            # gauge / bullet / progress: a single value against a max (and optional target).
            "title": "Chart Title (in Vietnamese)",
            "type": "gauge",
            "data": [{{"label": "Giá trị", "value": 74}}],
            "target": 90,
            "max": 100
        }},
        {{
            # scatter / bubble: raw (x, y[, size]) points — the ONE case where per-row
            # data (not aggregated) is appropriate, since the relationship IS the point cloud.
            "title": "Chart Title (in Vietnamese)",
            "type": "scatter",
            "points": [{{"x": 1.2, "y": 340.0, "label": "optional", "size": 5}}]
        }},
        {{
            # heatmap: a 2D matrix. "labels" = column headers, "rowLabels" = row headers.
            "title": "Chart Title (in Vietnamese)",
            "type": "heatmap",
            "labels": ["Col A", "Col B"],
            "rowLabels": ["Row 1", "Row 2"],
            "matrix": [[10, 20], [30, 40]]
        }},
        {{
            # For a relationship truly outside the CHART TYPE GUIDE (e.g. a real geo map) —
            # use "vega" instead: a self-contained Vega-Lite spec.
            "title": "Chart Title (in Vietnamese)",
            "type": "vega",
            "vegaLiteSpec": {{
                "data": {{"values": [{{"x": "...", "y": ..., "series": "..."}}]}},
                "mark": "bar",
                "encoding": {{"x": {{"field": "x", "type": "nominal"}}, "y": {{"field": "y", "type": "quantitative"}}, "color": {{"field": "series", "type": "nominal"}}}}
            }}
        }}
    ]
}}

CHART TYPE GUIDE — pick the type that matches what the data MEANS, not just "bar because it's safe".
Do NOT default to "bar" every time; vary the type across a dashboard when the data justifies it.
Single-series (use "data"):
- bar: generic category comparison, few short labels.
- horizontal-bar: rankings or long category names (store names, product names) — labels read better as rows.
- line: trend over an ordered/time axis with >1 series point.
- area: trend over time where cumulative volume/magnitude matters, not just direction.
- lollipop / dot-plot: a ranked comparison with few categories, lighter-weight than bar.
- pie: part-to-whole breakdown, <=6 slices.
- donut: same as pie but you also want the total shown in the center.
- funnel: a sequential process narrowing at each stage (e.g. leads -> qualified -> won).
- pyramid: same shape as funnel but widening (e.g. population by age band, org levels).
- waterfall: a running total built from signed deltas (e.g. starting balance, + revenue, - costs, = ending balance). First value = starting total, rest = signed deltas.
- gauge / bullet / progress: ONE current value against a max and optional target (e.g. "doanh số so với mục tiêu tháng").
- radial-bar: compact side-by-side comparison of a few values as concentric rings.
Multi-series (use "labels" + "series"):
- grouped-bar: compare 2+ series side by side per category.
- stacked-bar: compare 2+ series where the TOTAL per category also matters.
- multi-line: compare 2+ trends over the same time axis.
- stacked-area: compare 2+ trends AND their combined total over time.
- combo: one measure as bars + other measure(s) as lines on the same categories (first series = bars).
- radar: compare 2+ entities across 3+ dimensions/metrics at once.
Point-based (use "points"): scatter (relationship between 2 measures), bubble (same + a 3rd measure as size).
Grid (use "matrix" + "labels" + "rowLabels"): heatmap (intensity across 2 categorical dimensions).

DASHBOARD LAYOUT — every chart MUST carry "role" and "size". A dashboard is read
top-to-bottom from overview to detail, not as a pile of equal boxes:
- role "trend": how the main measure moves over TIME. Usually ONE such chart, and it
  is the anchor of the dashboard → size "lg".
- role "analysis": the charts that explain WHY the trend looks the way it does
  (composition, heatmap, comparison between groups/periods) → size "md", or "lg"
  for the single most important one.
- role "breakdown": supporting rankings and splits (top N stores, share by region)
  → size "sm" or "md".
- role "detail": the most granular view, typically a long ranking or a per-period
  table read last → size "lg".
Aim for a readable composition: at most one or two "lg" charts, the rest "md"/"sm".
EVERY chart MUST include both "role" and "size" — a chart without them cannot be
placed correctly and lands in a fallback zone.

HOW MUCH TO BUILD — judge this from the DATA, not from a quota. There is no
required count; a focused dashboard of 3 well-chosen charts beats 7 padded ones.
Decide by asking what the dataset can actually support:
- A date column worth a trend? A category with enough distinct values worth a
  ranking or a share breakdown? Two measures worth relating to each other?
  Several groups worth comparing? Build the ones the data genuinely answers.
- Skip an angle when the data cannot support it — a share breakdown over two
  categories, or a trend over three days, is noise, not insight.
- Equally, do not stop at the literal question. The user asked for a DASHBOARD,
  so give the rounded picture the data supports, not the single number that
  answers one sentence.
Also add the headline KPIs a reader would look for first (totals, counts,
averages, rates), with `compare_value` wherever a date column makes a
like-for-like comparison possible.

STRICT INSTRUCTIONS:
1. PLAN: Break down your reasoning (e.g., Column mapping, calculation formulas, aggregation, and layout construction).
2. SKILLS: If any of the AVAILABLE SKILLS match the required analysis (e.g. pareto, yoy growth, outliers), call them directly instead of writing code from scratch.
3. DATA ENRICHMENT (MAKE-UP) - BẮT BUỘC: Bạn KHÔNG ĐƯỢC chỉ đơn thuần group by các cột có sẵn. Bạn PHẢI tạo ra ít nhất 1-2 cột phân tích mới (analytical columns) để làm phong phú dữ liệu trước khi vẽ. Ví dụ: Phân loại một cột số thành 'Cao/Trung bình/Thấp', trích xuất 'Thứ trong tuần' / 'Giờ' từ cột Ngày, hoặc chia nhóm tuổi/ngân sách. Dùng chính các cột MỚI này để tạo thêm biểu đồ (như biểu đồ Tròn, Phễu) giúp Dashboard trông sâu sắc và "được trang điểm" xịn xò. KHÔNG ĐƯỢC TẠO DỮ LIỆU GIẢ (fake rows).
4. DATA TYPES: All values in the `layout` dictionary (like KPI values, chart values, labels) MUST be standard native Python types (float, int, str). Cast pandas/numpy types explicitly (e.g., float(row['revenue']), str(row['month']), int(value)).
5. Always assign the final dict to a global variable named `layout`.
6. Handle potential NaN/null values gracefully (e.g., using `.fillna(0)`).
7. All display titles, names, labels, and charts MUST be written in Vietnamese as requested by the user.
7b. KPI COMPARISON — `compare_value` is ONLY valid for a PERIOD-SCOPED KPI:
    - A cumulative / all-time total ("Tổng doanh số", "Tổng số hoá đơn" over the
      whole dataset) has NO previous period. OMIT compare_value entirely. Putting
      last month's figure next to an all-time total produces nonsense like
      "25,3 tỷ ▲389% so với tháng trước" — the two numbers are not comparable.
    - A period-scoped KPI ("Doanh số tháng 01/2026") MAY have compare_value = the
      same measure over the immediately previous period of the SAME length. Say
      the period in the KPI name so the reader knows what is being compared.
    - The two numbers must come from the same aggregation over equal-length
      periods. Never compare a total to a slice, a month to a quarter, or a
      complete period to an incomplete one.
    Compute it in pandas like any other number; never estimate. When in doubt,
    omit it — a KPI with no delta is fine, a KPI with a wrong delta is not.
7c. NO DUPLICATE KPIs: do not emit two KPIs that measure the same thing under
    different names (e.g. "Tổng doanh số" and "Tổng giá trị hoá đơn" computed
    from the same column). Pick one.
7d. PLACEHOLDER CODES: values like "0000", "N/A", "", "-", "UNKNOWN" in an id or
    category column are missing-data markers, NOT real entities. Exclude them
    from rankings and "top N" charts, or the dashboard will report a placeholder
    as its biggest customer/store.

CHARTING RULES (a chart with hundreds of points is unreadable — aggregate, don't dump raw rows):
8. TIME SERIES: if a date/time column is granular (e.g. daily data spanning weeks/months), AGGREGATE to a sensible period so a line/area chart has AT MOST ~24 points. CRITICAL DATE BUG FIX: Parse dates safely `df['date_col'] = pd.to_datetime(df['date_col'], errors='coerce')` and EXPLICITLY filter out invalid/dummy dates like 1970 (`df = df.dropna(subset=['date_col']); df = df[df['date_col'].dt.year > 2000]`) before drawing any line charts. Use pandas resample/period grouping and SUM (for additive measures like revenue/quantity) or MEAN (for rates/ratios). Keep chronological order.
9. CATEGORY CHARTS (bar/horizontal-bar/lollipop/dot-plot/radial-bar): show AT MOST the top 12 categories by value, sorted descending; fold any remainder into a single "Khác" entry. PIE/DONUT: at most 6 slices, otherwise use bar.
10. Keep every label short (a period or category name), never a long sentence. For multi-series charts every series' "values" array MUST be the same length as "labels", aligned by index (use 0 for a missing combination, not a gap).
11. Use the CHART TYPE GUIDE above to vary chart types across the dashboard based on what each relationship actually means — do not make every chart a bar chart. Reach for grouped-bar/stacked-bar/multi-line/combo/radar whenever there are 2+ series to compare, gauge/bullet/progress for a value-vs-target KPI, and scatter/heatmap for genuine 2D relationships. VEGA is the last resort — only when nothing in the guide fits (e.g. a real geo map); its "values" list must still be aggregated/summarized and every value a native Python type (float/int/str), not numpy/pandas.
"""

_LAYOUT_KEYS = ["grid", "kpi-first", "two-column", "storytelling", "overview-detail"]
_PALETTE_KEYS = ["emerald", "ocean", "sunset"]

LAYOUT_PICKER_SCHEMA = {
    "type": "object",
    "required": ["layout", "palette"],
    "properties": {
        "layout": {"type": "string", "enum": _LAYOUT_KEYS},
        "palette": {"type": "string", "enum": _PALETTE_KEYS},
        "reason": {"type": "string"},
    },
}

LAYOUT_PICKER_PROMPT = """You just finished building a dashboard. Based on the KPI/chart TITLES below (their
meaning, not just the count), pick the best-fitting presentation layout and color palette.

KPIs: {kpi_titles}
Charts: {chart_titles}

Layout options:
- grid: uniform grid, no particular emphasis - safe default for a mixed/generic dashboard.
- kpi-first: KPIs get their own prominent row above the charts - use when there are
  several important headline numbers (totals, rates) that should be scanned first.
- two-column: two independent columns - use when the data naturally splits into two
  comparable groups (e.g. "Miền Bắc vs Miền Nam", "before vs after", two categories).
- storytelling: single centered column read top-to-bottom - use when the charts build
  a narrative in sequence (e.g. trend over time, then breakdown, then a conclusion/forecast).
- overview-detail: KPIs on top, then the 2 most important charts shown larger than the rest -
  use when a couple of charts are clearly the main analysis and the rest are supporting detail.

Palette options (choose the mood that fits the business context of the titles):
- emerald: green, growth/positive/financial-health feel.
- ocean: blue, calm/analytical/corporate feel.
- sunset: orange, urgency/alert/marketing-energy feel.

Output a SINGLE JSON: {{"layout": "<one of the layout keys>", "palette": "<one of the palette keys>", "reason": "<short reason in Vietnamese>"}}."""


def suggest_layout_and_palette(kpis: list[dict], charts: list[dict]) -> dict | None:
    """One cheap LLM call, AFTER the dashboard is already built, to pick a
    presentation layout + color palette that fits what the data is ABOUT (not
    just how many KPIs/charts there are). Best-effort: returns None on any
    failure, and the frontend just keeps whatever the user had selected."""
    kpi_titles = [k.get("name") or k.get("title") or "?" for k in kpis]
    chart_titles = [c.get("title") or "?" for c in charts]
    if not kpi_titles and not chart_titles:
        return None
    try:
        prompt = LAYOUT_PICKER_PROMPT.format(kpi_titles=kpi_titles, chart_titles=chart_titles)
        result = call_ai(prompt, LAYOUT_PICKER_SCHEMA, tier="fast")
        if result.get("layout") in _LAYOUT_KEYS and result.get("palette") in _PALETTE_KEYS:
            return result
    except Exception:
        pass
    return None


SKILL_CREATOR_SCHEMA = {
    "type": "object",
    "required": ["is_reusable", "skill_name", "skill_code"],
    "properties": {
        "is_reusable": {
            "type": "boolean",
            "description": "True if the calculation code contains general-purpose reusable logic (like Pareto, YoY growth, outlier detection) that can be parameterized. False if it is too specific to this exact dataset."
        },
        "skill_name": {
            "type": "string",
            "description": "Safe snake_case name for the new skill (e.g., calculate_pareto)"
        },
        "skill_code": {
            "type": "string",
            "description": "Complete reusable Python function code, including docstring, pandas/numpy operations, and typed inputs. It must accept dataframe and column names as parameters. You MUST include required library imports (like 'import pandas as pd', 'import numpy as np') at the top of this code block so the module is fully self-contained when executed."
        }
    }
}

SKILL_CREATOR_PROMPT = """The user wants to save this successful dashboard analysis as a reusable SKILL.
An AI agent wrote this Python code to answer the user query: "{user_prompt}"

AI CODE:
```python
{ai_code}
```

Analyze if the code contains general-purpose calculations (e.g., YoY/MoM growth, Pareto 80/20, outlier detection, rolling average, RFM segmentation, dynamic cohorting) that should be refactored into a reusable, parameter-driven Python function ("Skill") for future reuse.

If it is highly specific (e.g., just computing total of a specific column 'A' or simple group by), mark `is_reusable` as false.
If it is reusable:
1. Choose a clean snake_case name (e.g., `calculate_yoy_growth`).
2. Write the complete Python function. The function must accept the dataframe and target column names as arguments (no hardcoding of dataset-specific columns except as defaults, use parameter arguments).
3. Include a detailed docstring explaining inputs and outputs so other AI models can use it.
4. You MUST write necessary import statements (e.g., `import pandas as pd`, `import numpy as np`) at the top of the code block so it is 100% self-contained and does not throw NameError when imported.
5. DO NOT reference global variables like `df` or `layout`.
"""


def _referenced_skill_names(code: str, candidate_names: list[str]) -> list[str]:
    """Which of the skills OFFERED to the model this run actually got called
    in its code (word-boundary match — good enough, no need to parse the AST
    for a usage-feedback signal)."""
    return [name for name in candidate_names if re.search(rf"\b{re.escape(name)}\b", code)]


def run_code_agent(state: dict, user_prompt: str, user_id: str):
    user_prompt = user_prompt.strip() or "Tạo báo cáo tổng quan"

    # 1. Chuẩn bị Schema & Skills có sẵn
    #
    # BUG (fixed): this used to compress the RAW per-sheet `profiles` (pre-join,
    # e.g. "Fact_Sales" and "Dim_CuaHang" as two separate BabelTele tables) even
    # though the sandbox only ever exposes ONE merged `df` (state["cleaned_df"],
    # already joined by DataAgent before Code Interpreter runs). CODE_PROMPT
    # explicitly says "only `df` is available" while the schema block showed
    # multiple table names — a direct contradiction that led the model to
    # reference a second dataframe (e.g. `Dim_CuaHang`) that was never in scope
    # → NameError. Fix: describe the schema of the ACTUAL merged `df`.
    from app.agent.babeltele import compress_schema_babeltele
    cleaned_schema = state.get("cleaned_schema") or {}
    
    # KIẾN TRÚC 1: COLUMN-RANK (CONTEXT PRUNING) HỌC TỪ AIDER
    # Lọc bỏ các cột rác không liên quan đến câu hỏi trước khi nén BabelTele
    def _prune_columns(prompt: str, profiles: list[dict]) -> list[dict]:
        if len(profiles) <= 12: # Nếu ít cột thì không cần cắt
            return profiles
        names = [c["name"] for c in profiles]
        sys_prompt = f"""User request: '{prompt}'
Available columns: {', '.join(names)}

Which columns are STRICTLY NECESSARY to answer this request?
Return a JSON array of column names. Include at most 12 columns. 
Always include date/time columns if the query implies trends.
"""
        try:
            res = call_ai(sys_prompt, {"type": "object", "properties": {"cols": {"type": "array", "items": {"type": "string"}}}}, tier="fast")
            rel = set(res.get("cols", []))
            if rel:
                return [c for c in profiles if c["name"] in rel]
        except Exception:
            pass
        return profiles
        
    if cleaned_schema.get("column_profiles"):
        pruned_profiles = _prune_columns(user_prompt, cleaned_schema["column_profiles"])
        merged_as_profile = [{
            "source_id": "df",
            "row_count": cleaned_schema.get("row_count", 0),
            "column_profiles": pruned_profiles,
        }]
        schema_text = compress_schema_babeltele(merged_as_profile)
    else:
        # Defensive fallback (shouldn't normally happen — /run_code always runs
        # DataAgent first, which sets cleaned_schema): describe raw profiles.
        schema_text = compress_schema_babeltele(state.get("profiles", []))

    # Same shared understanding of the data the chat pane reasons from: grain
    # (so "đếm dòng" is never mistaken for "số đơn hàng"), unit, and caveats
    # like "cột tồn kho là snapshot, đừng cộng theo thời gian".
    from app.data.semantics import format_semantics_for_prompt

    # The one shared understanding (meaning + observations + caveats), the same
    # block chat receives. Only the column listing above differs between paths,
    # because the dashboard reasons over one merged `df` while chat spans sheets.
    from app.data.context import shared_understanding

    _date_col, _, _ = pick_trend_columns(state.get("cleaned_schema") or {})
    understanding = shared_understanding(state, df=state.get("cleaned_df"), date_col=_date_col)
    if understanding:
        schema_text = schema_text + "\n\n" + understanding

    user_id = state.get("user_id", "")
    
    # Live broadcasts from DataAgent (e.g. warnings about duplicate rows during join)
    local_broadcasts = state.get("join_warnings", [])
    
    # Swarm Memory space completely replaces individual behavior fetching
    swarm_memory = build_swarm_context(user_id, user_prompt, local_broadcasts=local_broadcasts)
        
    prompt = CODE_PROMPT.format(
        schema_info=schema_text, 
        user_prompt=user_prompt,
        available_skills="", # Handled by Swarm Context now
        behaviors_text=swarm_memory
    )
    
    df = state.get("cleaned_df")
    if df is None:
        yield {"type": "error", "message": "Không tìm thấy dữ liệu (cleaned_df). Hãy upload file trước."}
        return
        
    # Skills as loaded callables (local fallback) + as source (container helper).
    skills_env: dict = {}
    load_skills_into_env(skills_env, owner_id=user_id)
    skills_source = get_skills_source(owner_id=user_id)

    max_retries = 2
    attempt = 0
    success = False
    ai_code = ""
    layout = None
    current_prompt = prompt

    while attempt <= max_retries and not success:
        attempt_msg = f" (Lần thử {attempt + 1})" if attempt > 0 else ""
        yield {"type": "step", "message": f"🤖 AI đang lên kế hoạch & viết code Dashboard{attempt_msg}..."}
        
        try:
            response = call_ai(current_prompt, CODE_SCHEMA, tier="strong")
            ai_code = response.get("code", "")
            plan = response.get("plan", [])
            
            # Xuất kế hoạch phân tích ra UI cho người dùng thấy dưới dạng các bước riêng lẻ
            if plan and attempt == 0:
                yield {"type": "step", "message": "📋 Kế hoạch phân tích:"}
                for i, step in enumerate(plan):
                    yield {"type": "step", "message": f"  + Bước {i+1}: {step}"}
                
        except Exception as exc:
            yield {"type": "error", "message": f"AI không lập được kế hoạch/viết code: {exc}"}
            return
            
        yield {"type": "step", "message": f"⚙️ Đang thực thi mã Python để sinh dữ liệu{attempt_msg}..."}

        # Mọi code AI sinh ra đều chạy qua sandbox (container hoặc restricted
        # namespace), không bao giờ exec trực tiếp trong process server.
        run = run_layout_script(
            ai_code, df.copy(), skills_env=skills_env, skills_source=skills_source
        )

        # Feedback loop: did the skills OFFERED this run actually get used, and
        # did the script that used them succeed? This is what turns record_skill's
        # usage_count/success_count from dead counters into a real signal.
        used = _referenced_skill_names(ai_code, list(skills_env.keys()))
        for skill_name in used:
            graph.record_skill_usage(user_id, skill_name, success=run["ok"])

        if run["ok"]:
            layout = run["layout"]
            success = True
        else:
            attempt += 1
            if attempt > max_retries:
                yield {"type": "error", "message": f"Code Python tính toán bị lỗi sau {max_retries} lần tự sửa:\n```python\n{ai_code}\n```\nLỗi: {run['error']}"}
                return

            yield {"type": "step", "message": f"⚠️ Phát hiện lỗi khi thực thi. Đang tự sửa lỗi (Self-Correction)..."}
            current_prompt = (
                prompt
                + f"\n\nYOUR PREVIOUS CODE GENERATED AN ERROR:\n```python\n{ai_code}\n```\n"
                  f"ERROR MESSAGE:\n{run['error']}\n\n"
                  "Please analyze the error (e.g. check column names, types, index resetting, or empty datasets) and write corrected Python code."
            )

    if not isinstance(layout, dict) or not layout:
        yield {"type": "error", "message": "Script chạy thành công nhưng không tạo ra biến layout hợp lệ."}
        return

    # Coerce kpis/charts to lists of dicts HERE, once, so every downstream
    # consumer (condense_layout, generate_insights, save_recipe, the report
    # endpoint) can trust the shape. `layout` comes from executing AI-generated
    # code: a buggy script can assign a raw DataFrame/Series/scalar instead of
    # a list (this is exactly what crashed with "The truth value of a
    # DataFrame is ambiguous" — some downstream code did `layout.get("charts",
    # []) or []`, and `or` evaluates a DataFrame's truthiness). Drop anything
    # malformed with a warning instead of letting it crash the whole build.
    dropped = []
    for key in ("kpis", "charts"):
        value = layout.get(key)
        if not isinstance(value, list):
            dropped.append(key)
            layout[key] = []
        else:
            layout[key] = [item for item in value if isinstance(item, dict)]
    if dropped:
        yield {"type": "step", "message": f"⚠️ Bỏ qua {', '.join(dropped)} vì không đúng định dạng (không phải danh sách)."}

    # Deterministic safety net: cap over-dense charts so a runaway 200-point
    # series can never reach the UI even if the model ignored the prompt rules.
    condense_layout(layout)

    # And drop the trailing point when the last period is unfinished. The prompt
    # already warns the model about this and the model does obey it in prose --
    # but the chart it wrote still plotted five days of a month next to nine full
    # ones, so the caption said "growth" above a cliff. The picture wins that
    # argument with the reader, so the fix has to reach the picture.
    from app.agent.chart_utils import drop_incomplete_period
    from app.data.trends import incomplete_last_period

    _cleaned = state.get("cleaned_df")
    if _cleaned is not None and _date_col:
        _found = incomplete_last_period(_cleaned, _date_col)
        if _found:
            _trimmed = drop_incomplete_period(layout, _found[0])
            if _trimmed:
                yield {"type": "step",
                       "message": f"✂️ Bỏ kỳ {_found[0]} khỏi {_trimmed} biểu đồ "
                                  f"(mới có ~{_found[1] * 100:.0f}% số ngày, vẽ vào sẽ thành sụt giả)."}

    # Deterministic guard on the comparisons: the prompt asks the model not to
    # compare an all-time total against one month, but it has done exactly that
    # ("25,3 tỷ ▲389% so với tháng trước"), so the check cannot live only in
    # the prompt.
    from app.agent.chart_utils import sanitize_kpis

    for note in sanitize_kpis(layout["kpis"]):
        yield {"type": "step", "message": f"⚠️ {note}"}
    layout["kpis"] = [k for k in layout["kpis"] if not k.pop("_duplicate_of", None)]

    for k in layout["kpis"]:
        k["status"] = "ok"
    for c in layout["charts"]:
        c["status"] = "ok"

    yield {"type": "step", "message": "✅ Đã tính toán xong dữ liệu báo cáo!"}

    # 1b. Phân tích xu hướng xác định (pandas/statsmodels, KHÔNG dùng LLM):
    # tăng trưởng, dự báo Holt-Winters, bất thường, biến động mạnh nhất theo
    # nhóm. Đây là các FACT có thật, được đưa vào prompt viết insight bên dưới
    # để nhận xét mang tính dự đoán thay vì chỉ mô tả lại số đã có.
    trend_signals = None
    date_col, value_col, group_col = pick_trend_columns(state.get("cleaned_schema", {}))
    if date_col and value_col:
        trend_signals = analyze_trend(df, date_col, value_col, group_col)
    state["trend_signals"] = trend_signals  # tái dùng cho báo cáo trình sếp (Phase 6)

    # ==========================================
    # BATCHING (ORCHESTRATOR PATTERN)
    # Gộp 3 tác vụ: Nhận xét (Insights), Bố cục (Layout), Lưu Kỹ năng (Skill) vào 1 LLM call
    # ==========================================
    yield {"type": "step", "message": "📝 Đang gộp lệnh phân tích hậu kỳ (Insights, Layout, Skills)..."}
    
    from app.ai.harness import batch_tasks, collect_ground_truth, collect_numbers_from_text, verify_numbers
    from app.agent.number_format import NUMBER_STYLE_RULES, describe
    from app.agent.sub_agents import INSIGHT_SCHEMA
    
    # 1. Chuẩn bị tác vụ Insight
    kpi_lines = [f"- {k.get('title') or k.get('name') or '?'}: {describe(k.get('value'))}{k.get('unit', '')}" for k in layout.get("kpis", [])]
    chart_lines = []
    for c in layout.get("charts", []):
        top = c["data"][:3] if c.get("data") else []
        top_str = ", ".join(f"{d.get('label', '')}={describe(d.get('value', 0))}" for d in top)
        chart_lines.append(f"- {c.get('title', '?')}: top → {top_str}")
        
    trend_context_str = format_trend_for_prompt(trend_signals) if trend_signals else ""
    trend_block = f"\n{trend_context_str}\n" if trend_context_str else ""

    insight_prompt = f"""Dưới đây là KPI và biểu đồ đã tính từ dữ liệu thật:
KPIs:\n{chr(10).join(kpi_lines) or "(không có)"}
Biểu đồ:\n{chr(10).join(chart_lines) or "(không có)"}
{trend_block}
Yêu cầu gốc: "{user_prompt}"

Viết 1-3 đoạn nhận xét/insight kinh doanh bằng tiếng Việt.
- CHỈ nhắc các con số thật ở trên, KHÔNG bịa.
- Nêu rõ điểm nổi bật, xu hướng, bất thường nếu có.
- Nếu có phần "Phân tích xu hướng", hãy lồng dự báo vào.
- Mỗi đoạn 2-4 câu, súc tích.

{NUMBER_STYLE_RULES}
"""

    # 2. Chuẩn bị tác vụ Layout Picker
    kpi_titles = [k.get("title") or k.get("name") or "?" for k in layout.get("kpis", [])]
    chart_titles = [c.get("title") or "?" for c in layout.get("charts", [])]
    presentation_prompt = LAYOUT_PICKER_PROMPT.format(kpi_titles=kpi_titles, chart_titles=chart_titles)

    # 3. Chuẩn bị tác vụ Skill Creator
    skill_prompt = SKILL_CREATOR_PROMPT.format(user_prompt=user_prompt, ai_code=ai_code)

    # GỌI ORCHESTRATOR
    tasks = {
        "insights": insight_prompt,
        "presentation": presentation_prompt,
        "skill": skill_prompt
    }
    schemas = {
        "insights": INSIGHT_SCHEMA,
        "presentation": LAYOUT_PICKER_SCHEMA,
        "skill": SKILL_CREATOR_SCHEMA
    }
    
    batched_results = {}
    try:
        batched_results = batch_tasks(
            global_context=f"Hệ thống vừa tính toán xong dữ liệu Dashboard cho yêu cầu: '{user_prompt}'",
            tasks=tasks,
            schemas=schemas,
            tier="strong"
        )
    except Exception as exc:
        yield {"type": "step", "message": f"⚠️ Gộp lệnh thất bại ({exc}). Bỏ qua bước hậu kỳ."}

    # PHÁT KẾT QUẢ TỪ NHẠC TRƯỞNG VỀ CÁC COMPONENT
    
    # batch_tasks already unwraps each task's {thinking, result} envelope, so
    # these are the results themselves. Reaching for ["result"] a second time
    # returned {} for all three tasks — insights, layout and skill-learning all
    # silently produced nothing, with no error anywhere to show it.
    presentation = batched_results.get("presentation") or {}
    if presentation:
        layout["suggested_layout"] = presentation.get("layout")
        layout["suggested_palette"] = presentation.get("palette")

    # Kết quả Insights (Đi qua cổng Grounding an toàn)
    insights_raw = (batched_results.get("insights") or {}).get("insights", [])
    ground_truth = collect_ground_truth(layout.get("kpis", []), layout.get("charts", []))
    if trend_context_str:
        ground_truth |= collect_numbers_from_text(trend_context_str)
        
    kept_insights = [p for p in insights_raw if not verify_numbers(p, ground_truth)]
    layout["insights"] = kept_insights
    if len(kept_insights) < len(insights_raw):
        yield {"type": "step", "message": f"⚠️ Đã lọc bỏ {len(insights_raw) - len(kept_insights)} nhận xét bị ảo giác số liệu."}

    # Kết quả Skills
    skill_data = batched_results.get("skill") or {}
    if skill_data.get("is_reusable") and skill_data.get("skill_name") and skill_data.get("skill_code"):
        if save_new_skill(user_id, skill_data["skill_name"], skill_data["skill_code"]):
            yield {"type": "step", "message": f"💡 AI đã tự học & lưu kỹ năng mới: `{skill_data['skill_name']}`"}

    state["layout"] = layout
    state["last_user_prompt"] = user_prompt
    state["layout_script"] = ai_code

        
    yield {
        "type": "finished",
        "layout": layout
    }
