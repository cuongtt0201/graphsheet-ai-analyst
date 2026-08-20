"""Conversational Q&A over the uploaded tables.

Same guiding split as the dashboard agent: the LLM plans, deterministic code
computes. For each question the model chooses ONE of two modes:

  answer - qualitative/meta questions ("what is this file about?", "which
           columns have missing values?") answered straight from the schema.
  code   - anything needing real numbers: the model writes a pandas snippet
           (assigning to `result`), we run it in the sandbox, then a second
           LLM call turns the computed result into a grounded Vietnamese answer
           (+ an optional chart spec). The model NEVER makes numbers up.

On a code error we feed the traceback back to the model once so it can fix its
own snippet before giving up.
"""

import json
import logging

import pandas as pd

from app.agent.chart_utils import condense_chat_chart
from app.agent.sandbox import run_pandas
from app.ai.pool import AllModelsFailedError, call_ai, progress_emit
from app.agent.babeltele import compress_schema_babeltele

logger = logging.getLogger(__name__)


def _emit(event: dict) -> None:
    """Push a live progress event to whoever is streaming this request (set by
    the /api/chat worker). No-op when nothing is listening, so answer_question
    stays usable as a plain function."""
    fn = progress_emit.get()
    if fn is not None:
        fn(event)


def _step(message: str) -> None:
    _emit({"type": "step", "message": message})

# Char budgets for the data context we hand the model. The pool routes big
# prompts to large-context models (Gemini ~1M), so we can afford a genuinely
# rich view of the data instead of 2 sample rows.
#
# Two separate budgets, because the two blocks have different guarantees. The
# preview is disposable — cutting it loses illustration only. The column
# listing is not: the generated pandas must match those names exactly, and a
# truncated list is how the model ends up inventing a plausible column and
# dying on KeyError. So column NAMES are never cut; when a workbook is wide
# enough to blow the budget, the per-column STATISTICS are dropped instead
# (see _schema_text), which costs precision and not correctness.
PREVIEW_CHAR_BUDGET = 60000
STATS_CHAR_BUDGET = 120000
PREVIEW_MAX_ROWS = 20
PREVIEW_MAX_COLS = 40
CELL_CAP = 40

# Free-tier keys rotate across 30 pool slots, so a second self-correction
# attempt costs latency, not money - and a wrong column name is exactly the
# kind of error one more grounded attempt fixes.
CODE_RETRIES = 2

# --- Stage 1: decide answer vs code -----------------------------------------

_DECISION_SCHEMA = {
    "type": "object",
    "required": ["mode", "reason"],
    "properties": {
        "mode": {"type": "string", "enum": ["answer", "code", "clarify"]},
        "reason": {"type": "string"},
        "answer": {"type": "string"},
        "code": {"type": "string"},
        # clarify: the one question that must be settled before any number is
        # computed, plus concrete choices the user can click instead of typing.
        "clarify_question": {"type": "string"},
        "clarify_options": {"type": "array", "items": {"type": "string"}},
        "follow_up": {"type": "array", "items": {"type": "string"}},
        "used_memory_ids": {"type": "array", "items": {"type": "string"}},
    },
}

_DECISION_PROMPT = """The user is asking questions about the uploaded dataset.

{schema}

BabelTele notation guide:
- T: {{Table Name}} (R: {{Row Count}})
- #: Numeric measure column (can be aggregated like sum, mean)
- $: Category / dimension column (usually for group-by)
- @: Datetime column
- ∅: Null percentage
- Σ: Sum, μ: Mean

Lịch sử hội thoại (nén: U=người dùng hỏi, A[tag]=bạn đã trả lời — tag: B=đã có bảng, G=đã có biểu đồ, K=đã có số cụ thể, !=lỗi, ?=chỉ trả lời chữ):
{history}
{workspace_block}{memory_block}{skills_block}
Câu hỏi mới của người dùng: "{question}"

Choose ONE of the three modes, and output a SINGLE JSON matching the schema:
- If the question is qualitative / about the file structure (description, missing columns, meaning...) and can be answered directly from the schema:
  {{"mode": "answer", "answer": "<Vietnamese answer explaining the schema/structure>", "reason": "<1-sentence explanation of your choice>", "follow_up": ["<2-3 natural next questions in Vietnamese that build on this answer, using exact column/sheet names from the schema>"]}}
- If it requires calculation of exact numbers (sum, mean, group by, filter, rank...):
  {{"mode": "code", "code": "<pandas python code snippet>", "reason": "<1-sentence explanation of your choice>"}}
- If the question is genuinely AMBIGUOUS and guessing wrong would produce a confidently WRONG number:
  {{"mode": "clarify", "clarify_question": "<một câu hỏi ngắn bằng tiếng Việt để làm rõ>", "clarify_options": ["<2-4 lựa chọn cụ thể, dùng đúng tên cột/sheet có thật>"], "reason": "<1-sentence>"}}

- When to use mode=answer or mode=clarify for BLINDSPOTS:
- If the question asks for a measure that is listed in the BLINDSPOTS block (e.g. asking for profit/margin when cost is missing, or asking for millisecond jitter when logs lack high-res timestamps), DO NOT invent code or guess: use mode="answer" to explain clearly what is missing and propose the closest computable alternative.

Rules for writing code (mode=code):
- The first table is available as `df`. ALL sheets/tables are in the `dfs` dictionary, key = source_id (e.g. "filename::sheet_name"). If the question refers to a specific sheet, use it: e.g., `dfs["report.xlsx::Revenue"]`.
- `pd` and `np` are pre-imported. DO NOT write imports, DO NOT read/write files, DO NOT use os/sys/subprocess.
- Assign the final computed result (DataFrame, Series, or scalar) to the variable named `result`.
- If a HELPER FUNCTIONS block is present above, PREFER calling a listed helper when it fits the question (they are already defined and tested) — e.g. `result = top_n_by(df, 'Cửa hàng', 'Doanh thu', 10)`. Do NOT redefine or import them; just call them. Write plain pandas only when no helper fits.
- Use only valid column names present in the schema above.
- DOMAIN ARCHETYPE COMPUTATION:
  * For Telemetry/DevOps/Logs: Use percentiles (`.quantile(0.95)`, `.quantile(0.99)`) for latency/request_time, calculate error rates (`(df['status'] >= 500).mean() * 100`), or count unique IPs/sessions.
  * For Academic/Grades: Compute mean/median, pass/fail rates, or score histograms.
  * For Scientific/Lab: Compute mean ± std, interquartile ranges, correlations (`.corr()`).
  * For Transactional/Sales: Aggregate sums, average order value, growth rates.
- TIME SERIES: for a "trend over time" question on granular dates (daily data spanning many weeks/months), aggregate to a sensible period so the result stays readable — prefer monthly (`df.groupby(df['col'].dt.to_period('M'))`) or weekly. Parse dates with `pd.to_datetime(..., errors='coerce')`.

- MEMORY SYNC (CRITICAL): If a MEMORY block is present below, those are durable notes about THIS user's habits and preferences from past sessions. You MUST explicitly review them. If a memory dictates a preference (e.g., "prefers pie charts", "aggregates by week", "focuses on region X"), you MUST incorporate it into your pandas code or chart selection. When you apply one or more notes, list their ids in "used_memory_ids". Do NOT ignore user habits.
"""

_MEMORY_BLOCK_TEMPLATE = """
[MEMORY BLOCK] - HỒ SƠ THÓI QUEN CỦA NGƯỜI DÙNG NÀY:
(Phải áp dụng các thói quen này nếu chúng liên quan đến câu hỏi)
{notes}
"""

_WORKSPACE_BLOCK_TEMPLATE = """
NGỮ CẢNH DASHBOARD HIỆN TẠI - người dùng đang nhìn thấy dashboard này ngay cạnh khung chat.
Đây là các con số ĐÃ ĐƯỢC TÍNH THẬT từ dữ liệu (không phải ước lượng). Khi câu hỏi liên quan
tới chúng ("làm sao để tăng...", "vì sao giảm...", "cửa hàng nào tốt nhất..."), hãy trả lời
BÁM VÀO các số/nhận xét này thay vì tính lại từ đầu hay trả lời chung chung né tránh:
{content}
"""


def build_workspace_context(layout: dict | None, dashboard_items: list | None) -> str:
    """One synchronized view of what the user is LOOKING AT right now, injected
    into every chat turn - the chat pane is the control panel for the whole
    workspace, so it must never answer blind to the dashboard next to it.

    Prefers the auto-build layout (has insights); falls back to manually
    pinned dashboard_items (no insights, but titles + KPI values exist)."""
    lines: list[str] = []
    if isinstance(layout, dict) and (layout.get("kpis") or layout.get("charts")):
        for k in layout.get("kpis", []):
            if isinstance(k, dict):
                name = k.get("name") or k.get("title") or "?"
                lines.append(f"- KPI: {name} = {k.get('value')}")
        for c in layout.get("charts", []):
            if not isinstance(c, dict):
                continue
            data = c.get("data")
            if isinstance(data, list) and data:
                pts = ", ".join(
                    f"{d.get('label')}={d.get('value')}" for d in data[:12] if isinstance(d, dict)
                )
                lines.append(f"- Biểu đồ \"{c.get('title')}\": {pts}")
            else:
                lines.append(f"- Biểu đồ \"{c.get('title')}\"")
        for ins in layout.get("insights", []):
            if isinstance(ins, str) and ins.strip():
                lines.append(f"- Nhận xét: {ins.strip()}")
    elif isinstance(dashboard_items, list) and dashboard_items:
        for item in dashboard_items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "kpi":
                lines.append(f"- KPI: {item.get('title')} = {item.get('scalar')}")
            elif item.get("type") == "chart":
                chart = item.get("chart") or {}
                labels = chart.get("labels") or []
                values = chart.get("values") or []
                if labels and values:
                    pts = ", ".join(f"{l}={v}" for l, v in list(zip(labels, values))[:12])
                    lines.append(f"- Biểu đồ \"{item.get('title')}\": {pts}")
                else:
                    lines.append(f"- Biểu đồ \"{item.get('title')}\"")

    if not lines:
        return ""
    return _WORKSPACE_BLOCK_TEMPLATE.format(content="\n".join(lines))

_SKILLS_BLOCK_TEMPLATE = """
HELPER FUNCTIONS - hàm phân tích đã có sẵn (gọi trực tiếp trong code, đừng định nghĩa lại):
{skills}
"""


def _memory_block(behaviors: list[dict], user_id: str | None = None) -> str:
    from app.memory import graph
    lines = []
    if behaviors:
        lines.append("📌 Thói quen & Sở thích đã củng cố (Bubble Weight):")
        for b in behaviors:
            w = b.get("weight", 1)
            bubble_tag = f"[Trọng số: {w}]" if w > 1 else ""
            lines.append(f"  - [{b['id']}] ({b.get('category', 'habit')}) {b['description']} {bubble_tag}")

    if user_id and graph.ENABLED:
        try:
            rules = graph.get_business_rules(user_id)
            if rules:
                lines.append("\n📖 Luật nghiệp vụ riêng của người dùng này (BẮT BUỘC tuân thủ):")
                for r in rules:
                    lines.append(f"  - [{r.get('concept_name')}]: {r.get('formula_desc')} (Trọng số: {r.get('weight', 1)})")
        except Exception:
            pass

    if not lines:
        return ""
    return _MEMORY_BLOCK_TEMPLATE.format(notes="\n".join(lines))


def _load_chat_skills(question: str, user_id: str | None, top_k: int = 5):
    """Retrieve the skill functions most relevant to this question — curated
    (shared) + this user's own personal skills, never another user's — and
    return (prompt_block, skills_source, skills_env) so the chat sandbox can
    call them. Fully best-effort: any failure yields empty context (plain
    pandas still works)."""
    try:
        from app.agent.skills_manager import get_relevant_skills, get_skills_source, load_skills_into_env
    except Exception:
        return "", "", {}
    try:
        relevant = get_relevant_skills(question, owner_id=user_id, top_k=top_k)
        if not relevant:
            return "", "", {}
        lines = [f"- {s['name']}: {s['description'].splitlines()[0] if s['description'] else ''}" for s in relevant]
        block = _SKILLS_BLOCK_TEMPLATE.format(skills="\n".join(lines))
        env: dict = {}
        load_skills_into_env(env, owner_id=user_id)
        return block, get_skills_source(owner_id=user_id), env
    except Exception:
        return "", "", {}


def _col_stat(cp: dict) -> str:
    """One line describing a column: type/role + the real stats the profiler
    already computed (min/max/mean/sum for numbers, distinct + sample values for
    text). This grounds both qualitative answers and generated pandas."""
    bits = [f"{cp['name']} [{cp['dtype']}, {cp.get('role', '?')}]"]
    if cp.get("null_pct"):
        bits.append(f"trống {int(cp['null_pct'] * 100)}%")
    if "sum" in cp:  # numeric column
        bits.append(f"min {cp.get('min')}, max {cp.get('max')}, TB {cp.get('mean')}, tổng {cp.get('sum')}")
    elif cp.get("sample"):
        vals = ", ".join(str(s) for s in cp["sample"][:5])
        bits.append(f"{cp.get('distinct', '?')} giá trị (vd: {vals})")
    return "    • " + " — ".join(bits)


def _preview(df: pd.DataFrame, max_rows: int) -> str:
    """A compact pipe-delimited preview of the real (cleaned) rows the pandas
    code will run against - column names here match what the model must use."""
    d = df.iloc[:max_rows, :PREVIEW_MAX_COLS]
    header = " | ".join(str(c) for c in d.columns)
    rows = []
    for tup in d.itertuples(index=False, name=None):
        cells = []
        for v in tup:
            s = "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)
            cells.append(s[:CELL_CAP])
        rows.append(" | ".join(cells))
    return header + "\n" + "\n".join(rows)


def columns_reference(dataframes: dict) -> str:
    """The exact, complete column list of every dataframe the code will run
    against. Read straight off the live DataFrames (not the profiles) so it can
    never drift from what pandas actually holds."""
    lines = []
    for sid, df in dataframes.items():
        if df is None:
            continue
        cols = ", ".join(f"'{c}'" for c in df.columns)
        lines.append(f"  dfs[\"{sid}\"] → [{cols}]")
    return "\n".join(lines)


def _schema_text(profiles: list[dict], dataframes: dict, semantics: dict | None = None,
                 eda_facts: dict | None = None) -> str:
    """Rich data context: compressed via BabelTele, an exact column index, and
    sample rows for raw preview.

    Truncation applies ONLY to the preview tail. Column names are what the
    generated pandas code must match exactly — cutting them mid-way is how the
    model ends up inventing a plausible-sounding column (e.g. naming one after
    the sheet title) and the snippet dies on KeyError."""
    head = ["Các bảng dữ liệu đã upload (được nén bằng BabelTele):"]
    head.append(_schema_stats(profiles))
    # The one shared understanding block - literally the same text the
    # dashboard builder receives, so the two can never disagree about what the
    # data means or what it shows. Comes before the column list because grain
    # is what stops "đếm số dòng" being mistaken for "số đơn hàng".
    from app.data.context import shared_understanding

    understanding = shared_understanding({"semantics": semantics, "eda_facts": eda_facts})
    if understanding:
        head.append("\n" + understanding)
    head.append("\nDANH SÁCH CỘT CHÍNH XÁC (BẮT BUỘC dùng đúng tên này, KHÔNG được tự đặt tên cột khác):")
    head.append(columns_reference(dataframes))
    head_text = "\n".join(head)

    body = ["\nXem trước dữ liệu thực tế (5 dòng đầu):"]
    for p in profiles:
        sid = p["source_id"]
        df = dataframes.get(sid)
        if df is not None and len(df):
            n = min(len(df), 5)
            body.append(f"  Bảng `{sid}`:")
            body.append(_preview(df, n))
    body_text = "\n".join(body)

    if len(body_text) > PREVIEW_CHAR_BUDGET:
        body_text = body_text[:PREVIEW_CHAR_BUDGET] + "\n…(đã cắt bớt phần xem trước)"
    return head_text + body_text


def _schema_stats(profiles: list[dict]) -> str:
    """Per-column statistics, bounded at sheet boundaries.

    A 60-sheet accounting workbook produced a statistics block larger than the
    budget that was nominally enforcing it — the cap only ever applied to the
    preview tail, so this block grew without limit. Sheets past the budget keep
    their name and row count here and their full column list further down; only
    the statistics go, which the model can live without.
    """
    kept, spent = [], 0
    for i, p in enumerate(profiles):
        block = compress_schema_babeltele([p])
        if spent + len(block) > STATS_CHAR_BUDGET and kept:
            remaining = profiles[i:]
            kept.append("\n".join(f"T:{q['source_id']}(R:{q['row_count']})" for q in remaining))
            kept.append(
                f"…(đã lược thống kê chi tiết của {len(remaining)} bảng cuối vì file quá rộng — "
                f"tên cột đầy đủ vẫn ở DANH SÁCH CỘT bên dưới)"
            )
            break
        kept.append(block)
        spent += len(block)
    return "\n".join(kept)


_KIND_TAG = {"table": "B", "chart": "G", "scalar": "K", "error": "!", "text": "?"}
# Assistant answers are ALREADY grounded in real computed numbers, so a fresh
# question can still reference "cái vừa tính" accurately even from a trimmed
# history line — the model recomputes from the live dataframe, it never
# relies on remembering the old prose verbatim. Trimming just stops long
# paragraphs from being replayed in full on every subsequent turn.
_HISTORY_ANSWER_CHARS = 200


def _history_text(history: list[dict]) -> str:
    """Compact, BabelTele-tagged conversation history: `U:` for the user's
    question, `A[tag]:` for the assistant's answer where tag says what KIND of
    answer it was (B=table, G=chart, K=scalar, !=error, ?=plain text — chosen
    to not collide with the schema notation's own T/# meanings) — the model
    can skip re-parsing prose to know "was this already answered with a
    number/chart" when deciding the next turn's mode."""
    if not history:
        return "(chưa có)"
    lines = []
    for h in history[-6:]:
        content = h["content"] or ""
        if h["role"] == "user":
            lines.append(f"U: {content}")
        else:
            tag = _KIND_TAG.get(h.get("kind", "text"), "?")
            if len(content) > _HISTORY_ANSWER_CHARS:
                content = content[:_HISTORY_ANSWER_CHARS].rstrip() + "…"
            lines.append(f"A[{tag}]: {content}")
    return "\n".join(lines)


# --- Upload-time comprehension: summary + suggested questions ---------------

_SUMMARY_SCHEMA = {
    "type": "object",
    "required": ["summary", "suggestions"],
    "properties": {
        "summary": {"type": "string"},
        "suggestions": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 3,
        },
    },
}

_SUMMARY_PROMPT = """The user just uploaded the following dataset:

{schema}

Your task: Understand the dataset's context, the structural grain (mức độ chi tiết), and anticipate the user's business objective. Output a SINGLE JSON:
{{"summary": "<A welcoming Data Persona greeting in Vietnamese. Example: 'Chào bạn. Tôi nhận thấy đây là [Loại Dữ Liệu], với độ chi tiết đến từng [Grain]. Có vẻ mục tiêu của bạn là [Mục tiêu]. Tuy nhiên, tôi thấy dữ liệu này không có [Cột quan trọng bị thiếu, vd: Chi phí/Khách hàng], nên tôi không thể phân tích [Lợi nhuận/Tệp khách], mà sẽ tập trung vào [Hướng thay thế] nhé. Bạn muốn bắt đầu từ đâu?'>",
  "suggestions": ["<exactly 3 short, concise analytical questions in Vietnamese (under 10 words each, using exact column names)>"]}}

Rules for the Data Persona (summary):
- Be proactive and honest. If obvious business metrics are missing (e.g., Sales data without Cost, HR data without Salary), STATE IT CLEARLY so the user knows the blindspots upfront.
- Identify the likely business objective (e.g., Sales Performance, Inventory Management).

Rules for suggestions:
- Exactly 3 short, concise questions. Keep each question short and under 10 words.
- Use EXACT sheet/column names present in the data, do not invent them.
- Prioritize quantitative questions: sum, average, comparison, top N, trends over time.
"""


def summarize_upload(profiles: list[dict], dataframes: dict) -> dict | None:
    """One fast LLM pass right after upload: reads the rich data card and
    returns {"summary", "suggestions"} - the 'AI already understood your file'
    moment. Returns None on LLM failure; upload must never break because of it."""
    prompt = _SUMMARY_PROMPT.format(schema=_schema_text(profiles, dataframes))
    try:
        return call_ai(prompt, _SUMMARY_SCHEMA, tier="strong")
    except AllModelsFailedError:
        return None


# --- Stage 2: interpret the computed result into a grounded answer ----------

_INTERPRET_SCHEMA = {
    "type": "object",
    "required": ["answer"],
    "properties": {
        "answer": {"type": "string"},
        "chart": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["bar", "line", "pie", "vega"]},
                "title": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "values": {"type": "array", "items": {"type": "number"}},
                "vegaLiteSpec": {"type": "object"},
            },
        },
        "follow_up": {"type": "array", "items": {"type": "string"}},
    },
}

_INTERPRET_PROMPT = """The user asked: "{question}"

You ran this pandas code:
{code}

And got the following computed result (DO NOT alter these numbers, they are facts):
{result}

Full schema of the dataset (for grounding follow-up suggestions in real column/sheet names):
{schema}

Output a SINGLE JSON matching the schema:
{{"answer": "<interpret the results in Vietnamese, be concise, and state the exact numbers computed above>",
  "chart": {{"type": "bar|line|pie|vega", "title": "<Chart Title in Vietnamese>", "labels": [<list of labels in Vietnamese>], "values": [<list of numbers>], "vegaLiteSpec": <Vega-Lite JSON spec if type is vega>}},
  "follow_up": ["<2-3 natural next questions in Vietnamese that dig deeper into THIS specific finding (e.g. break it down further, compare a subset, look at a related column) - use exact column/sheet names from the schema, never invent one>"]}}

- Only include the "chart" key if the result is a grouped dataframe suitable for plotting (omit it if the result is a single number/scalar).
- If the required visualization is a complex/interactive chart (e.g. grouped bar, stacked area, scatter, heatmap, bubble), set type to "vega" and provide a valid, self-contained Vega-Lite JSON specification inside "vegaLiteSpec".
- For "vegaLiteSpec", always include the "data": {{"values": [...]}} mapping the records directly from the computed result. Do not reference external URLs.
"""


def answer_question(profiles: list[dict], dataframes: dict, question: str, history: list[dict],
                    behaviors: list[dict] | None = None, user_id: str | None = None,
                    workspace_block: str = "", semantics: dict | None = None,
                    eda_facts: dict | None = None) -> dict:
    """Returns a JSON-serializable reply:
      {"answer": str, "code": str|None, "table": {...}|None, "chart": {...}|None,
       "error": str|None, "follow_up": [str], "used_memory_ids": [str]}
    "follow_up" is the AI's own next-question suggestions, grounded in this
    file's real schema/result - not other users' history (no cross-user recall).
    "behaviors" are this user's distilled memory notes (memory/idle_job); the
    model self-selects which apply and reports them in used_memory_ids.
    "user_id" scopes which skill library is used - curated (shared) + this
    user's own personal skills, never another user's.
    "workspace_block" is build_workspace_context()'s view of the dashboard the
    user is looking at - the chat must never answer blind to it."""
    # Natural Language Memory Erasure Handler
    q_lower = question.lower().strip()
    from app.memory import graph
    if user_id and graph.ENABLED:
        if any(kw in q_lower for kw in ["xóa tất cả ký ức", "xóa hết bộ nhớ", "quên hết về tôi", "clear all memory", "forget everything"]):
            deleted = graph.delete_all_user_memories(user_id)
            return {"answer": f"🧹 **Đã xóa toàn bộ {deleted} ký ức và thói quen** của bạn khỏi đồ thị Neo4j. AI giờ đây không còn lưu bất kỳ thông tin cá nhân nào.",
                    "code": None, "table": None, "chart": None, "scalar": None, "error": None,
                    "follow_up": ["Bắt đầu phân tích dữ liệu mới?"], "used_memory_ids": []}

        if any(kw in q_lower for kw in ["xóa ký ức", "quên thói quen", "đừng nhớ", "xóa luật", "quên đi", "xóa công thức"]):
            deleted_items = graph.forget_memory_by_text(user_id, question)
            if deleted_items:
                items_str = "\n".join(f"- {it}" for it in deleted_items)
                return {"answer": f"🧹 **Đã xóa các ký ức sau khỏi bộ não AI**:\n{items_str}",
                        "code": None, "table": None, "chart": None, "scalar": None, "error": None,
                        "follow_up": ["Tiếp tục phân tích?"], "used_memory_ids": []}

    schema = _schema_text(profiles, dataframes, semantics, eda_facts)
    skills_block, skills_source, skills_env = _load_chat_skills(question, user_id)
    decision_prompt = _DECISION_PROMPT.format(
        schema=schema, history=_history_text(history), question=question,
        workspace_block=workspace_block,
        memory_block=_memory_block(behaviors or [], user_id=user_id), skills_block=skills_block,
    )

    _step("🧠 Đang đọc câu hỏi và chọn hướng trả lời...")
    try:
        decision = call_ai(decision_prompt, _DECISION_SCHEMA, tier="strong")
    except AllModelsFailedError as exc:
        return {"answer": None, "code": None, "table": None, "chart": None,
                "scalar": None, "error": f"AI không phản hồi được: {exc}",
                "follow_up": [], "used_memory_ids": []}

    used_memory_ids = decision.get("used_memory_ids") or []

    # The model already explains its plan in "reason" — it used to be computed
    # and thrown away. Surfacing it early is the cheapest possible progress
    # signal: the user sees whether the question was understood correctly
    # seconds before the actual numbers arrive.
    reason = (decision.get("reason") or "").strip()
    if reason:
        _emit({"type": "reason", "message": reason})

    if decision["mode"] == "clarify":
        # Ask instead of guessing. Returned as a normal answer plus clickable
        # options, so the next turn arrives as an ordinary question with the
        # ambiguity already resolved - no extra conversational state to keep.
        q = (decision.get("clarify_question") or "").strip()
        opts = [o for o in (decision.get("clarify_options") or []) if isinstance(o, str) and o.strip()][:4]
        if not q:
            # Model picked clarify but gave nothing to ask - fall through to a
            # plain answer rather than showing the user an empty prompt.
            return {"answer": decision.get("answer") or "Bạn có thể nói rõ hơn ý câu hỏi không?",
                    "code": None, "table": None, "chart": None, "scalar": None, "error": None,
                    "follow_up": [], "reason": reason, "used_memory_ids": used_memory_ids}
        return {"answer": q, "code": None, "table": None, "chart": None,
                "scalar": None, "error": None, "clarify": True,
                "follow_up": opts, "reason": reason,
                "used_memory_ids": used_memory_ids}

    if decision["mode"] == "answer":
        return {"answer": decision.get("answer", ""), "code": None,
                "table": None, "chart": None, "scalar": None, "error": None,
                "follow_up": decision.get("follow_up") or [],
                "reason": reason, "used_memory_ids": used_memory_ids}

    # mode == "code": Dispatch to the Alpha Meta-Cognitive Orchestrator (Bầy Agent Trồi Sinh)
    from app.agent.alpha import run_alpha_cognition
    alpha_res = run_alpha_cognition(
        question=question,
        dataframes=dataframes,
        schema_context=schema,
        user_id=user_id,
        initial_code=decision.get("code"),
        initial_hypothesis=reason,
        skills_env=skills_env,
        skills_source=skills_source,
        call_ai_fn=call_ai,
        run_pandas_fn=run_pandas,
    )
    alpha_res["used_memory_ids"] = used_memory_ids
    return alpha_res

