"""The missing pipeline stage: proactive analysis at upload time.

Everything else in the system is reactive — it answers what the user asked.
This stage runs before any question exists, so the user opens a fresh file and
already sees what is in it.

Two things separate this from LIDA's goal explorer, which only *suggests*:

  it runs what it proposes   LIDA says "you should look at profit by quarter".
                             Here the pandas actually executes, so the output is
                             "profit fell 23% in Q3, driven by 3 stores" — a
                             finding, not a chore assigned back to the user.
  it starts from evidence    data/dispatcher.py has already measured where the
                             structure is. The model translates those cold
                             signals into business questions instead of guessing
                             what might be interesting, so it cannot propose a
                             goal the data has no support for.

Call shape is deliberately 2 LLM calls for N findings, not 2N: one batched call
plans every goal, the sandbox runs them in PARALLEL (independent work, which is
where fan-out belongs), one batched call writes them up. Parallelism is applied
to the executing, not to the thinking.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from app.ai.pool import AllModelsFailedError, call_ai

logger = logging.getLogger(__name__)

MAX_GOALS = 5
MAX_WORKERS = 4
RESULT_PREVIEW_CHARS = 1200

PLAN_SCHEMA = {
    "type": "object",
    "required": ["goals"],
    "properties": {
        "goals": {
            "type": "array",
            "maxItems": MAX_GOALS,
            "items": {
                "type": "object",
                "required": ["question", "code"],
                "properties": {
                    "question": {"type": "string", "description": "Câu hỏi nghiệp vụ, tiếng Việt."},
                    "why": {"type": "string", "description": "Dựa trên tín hiệu/quan sát nào."},
                    "code": {"type": "string", "description": "pandas, gán kết quả vào `result`."},
                },
            },
        }
    },
}

PLAN_PROMPT = """Người dùng vừa nạp dữ liệu và CHƯA hỏi gì. Nhiệm vụ của bạn: tự tìm ra
những điều đáng chú ý nhất trong dữ liệu này để trình bày ngay cho họ.

{understanding}

DANH SÁCH CỘT CHÍNH XÁC:
{columns}

Hãy đề xuất tối đa {max_goals} câu hỏi phân tích, MỖI CÂU KÈM ĐOẠN PANDAS để trả lời.

Nguyên tắc chọn câu hỏi:
- BÁM VÀO phần "TÍN HIỆU THỐNG KÊ" và "QUAN SÁT THỰC TẾ" ở trên — đó là những chỗ dữ liệu
  đã cho thấy có cấu trúc thật. Đừng hỏi những thứ dữ liệu không có bằng chứng gì.
- Mỗi câu hỏi phải trả lời được bằng SỐ, và phải là thứ người kinh doanh quan tâm
  (ai đóng góp nhiều nhất, cái gì đang giảm, nhóm nào khác biệt, xu hướng ra sao).
- Đừng lặp lại cùng một góc nhìn với cách chia khác.
- Nếu một tín hiệu thống kê chỉ là quan hệ số học hiển nhiên, BỎ QUA.

Quy tắc viết code:
- Bảng đầu tiên là `df`; tất cả bảng nằm trong dict `dfs` theo source_id.
- `pd`, `np` đã có sẵn. KHÔNG import, KHÔNG đọc/ghi file.
- Gán kết quả cuối vào biến `result`. Kết quả phải NHỎ (tối đa ~10 dòng), đã tổng hợp và sắp xếp.
- Chỉ dùng đúng tên cột có thật ở trên.
- Loại bỏ các giá trị rỗng/giả đã được cảnh báo ở trên khỏi bảng xếp hạng.

Trả lời DUY NHẤT JSON đúng schema."""


WRITEUP_SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "detail"],
                "properties": {
                    "title": {"type": "string", "description": "Một câu ngắn, có số."},
                    "detail": {"type": "string", "description": "1-2 câu giải thích."},
                },
            },
        }
    },
}

WRITEUP_PROMPT = """Bạn vừa chạy một loạt phân tích trên dữ liệu người dùng mới nạp.
Dưới đây là câu hỏi và KẾT QUẢ THẬT do pandas tính.

{results}

Viết lại thành các phát hiện ngắn gọn bằng tiếng Việt cho người kinh doanh đọc.
- Mỗi phát hiện: `title` một câu có SỐ CỤ THỂ, `detail` 1-2 câu nói ý nghĩa.
- CHỈ dùng những con số xuất hiện trong kết quả trên. Không ước lượng, không suy diễn thêm.
- Nếu một kết quả không có gì đáng nói, BỎ QUA nó thay vì cố nặn ra nhận xét.
- KHÔNG khẳng định nguyên nhân ("do khuyến mãi", "vì mùa vụ") — dữ liệu không chứng minh được.
{number_rules}

Trả lời DUY NHẤT JSON đúng schema."""


def _columns_block(dataframes: dict) -> str:
    lines = []
    for sid, df in dataframes.items():
        if df is None:
            continue
        lines.append(f'  dfs["{sid}"] → [' + ", ".join(f"'{c}'" for c in df.columns) + "]")
    return "\n".join(lines)


def explore(state: dict, dataframes: dict, emit=None) -> list[dict]:
    """Verified findings for a freshly uploaded dataset, or [] if nothing
    survived. Never raises — an upload must not fail because the proactive
    analysis did."""
    from app.agent.number_format import NUMBER_STYLE_RULES
    from app.agent.sandbox import run_pandas
    from app.ai.harness import collect_ground_truth, verify_numbers
    from app.data.context import shared_understanding

    def _say(msg: str) -> None:
        if emit:
            emit({"type": "step", "message": msg})

    if not dataframes:
        return []

    understanding = shared_understanding(state)
    if not understanding:
        # Without the measured layer this would be pure guessing, which is
        # exactly what this stage exists to avoid.
        return []

    try:
        plan = call_ai(
            PLAN_PROMPT.format(
                understanding=understanding,
                columns=_columns_block(dataframes),
                max_goals=MAX_GOALS,
            ),
            PLAN_SCHEMA, tier="strong",
        )
    except AllModelsFailedError as exc:
        logger.info(f"[goal_explorer] planning skipped: {exc}")
        return []

    goals = [g for g in (plan.get("goals") or []) if g.get("code")][:MAX_GOALS]
    if not goals:
        return []

    _say(f"🎯 Đang tự kiểm chứng {len(goals)} hướng phân tích (song song)...")

    # Independent work -> run concurrently. The sandbox is the slow part and
    # the goals do not depend on each other, so N goals cost roughly one.
    def _run(goal):
        try:
            return goal, run_pandas(goal["code"], dataframes)
        except Exception as exc:  # noqa: BLE001 - one bad goal must not sink the batch
            return goal, {"ok": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(goals))) as pool:
        outcomes = list(pool.map(_run, goals))

    truths: set = set()
    blocks = []
    for goal, run in outcomes:
        if not run.get("ok"):
            continue
        preview = json.dumps(run["result"], ensure_ascii=False, default=str)[:RESULT_PREVIEW_CHARS]
        truths |= collect_ground_truth(run["result"])
        blocks.append(f'- Câu hỏi: {goal["question"]}\n  Kết quả: {preview}')

    if not blocks:
        return []

    try:
        writeup = call_ai(
            WRITEUP_PROMPT.format(results="\n".join(blocks), number_rules=NUMBER_STYLE_RULES),
            WRITEUP_SCHEMA, tier="strong",
        )
    except AllModelsFailedError:
        return []

    # Same gate as insights and reports: a finding citing a number the analysis
    # never produced is dropped, not shown.
    findings = []
    for f in writeup.get("findings") or []:
        title, detail = (f.get("title") or "").strip(), (f.get("detail") or "").strip()
        if not title:
            continue
        if verify_numbers(f"{title} {detail}", truths):
            logger.info(f"[goal_explorer] dropped unverifiable finding: {title[:60]}")
            continue
        findings.append({"title": title, "detail": detail})

    if findings:
        _say(f"💡 Tìm thấy {len(findings)} phát hiện đáng chú ý.")
    return findings
