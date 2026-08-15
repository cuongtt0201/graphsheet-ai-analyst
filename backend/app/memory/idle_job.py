"""Idle-time behavior distillation.

A daemon thread wakes every CHECK_INTERVAL seconds and looks for users who have
gone quiet (no activity for IDLE_THRESHOLD seconds) but still have raw :Action
events buffered in the graph. For each, one cheap LLM call distills those
events into durable :Behavior notes ("thường xem doanh thu theo tháng", "đang
phân tích dở file bán hàng"...), then the processed Actions are deleted — the
Action log is a short-lived buffer feeding this pipeline, not a permanent
history, so the graph cannot grow without bound.

Single-process safe only: this app runs one uvicorn worker (see Dockerfile).
If it ever scales to multiple workers/replicas, this loop needs a distributed
guard (e.g. a last_distilled_at timestamp checked in the same Cypher write).
"""

import json
import logging
import threading
import time

from app.ai import energy
from app.ai.pool import AllModelsFailedError, call_ai
from app.memory import graph

logger = logging.getLogger(__name__)

IDLE_THRESHOLD = 30 * 60      # user must be quiet this long (seconds)
CHECK_INTERVAL = 5 * 60       # how often the loop scans for idle users
MIN_ACTIONS = 3               # don't distill fewer events than this

_DISTILL_SCHEMA = {
    "type": "object",
    "required": ["behaviors"],
    "properties": {
        "behaviors": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["description", "category"],
                "properties": {
                    "description": {"type": "string"},
                    "category": {"type": "string", "enum": ["habit", "preference", "unfinished_work"]},
                },
            },
        },
    },
}

_DISTILL_PROMPT = """Bạn là bộ nhớ dài hạn của một trợ lý phân tích dữ liệu. Dưới đây là nhật ký hành động THÔ của MỘT người dùng trong (các) phiên làm việc gần nhất, theo thứ tự thời gian:

{action_log}

Người dùng này đã có sẵn các ghi nhớ sau (KHÔNG lặp lại chúng):
{existing}

Chắt lọc ra 0-4 ghi nhớ MỚI, bền vững và đáng nhớ về người dùng này. Chỉ ghi những gì có bằng chứng lặp lại hoặc rõ ràng trong nhật ký:
- "habit": thói quen thao tác lặp lại (ví dụ: hay tổng hợp theo tháng, hay hỏi top N, hay build dashboard ngay sau khi upload)
- "preference": sở thích nội dung (ví dụ: quan tâm chủ yếu đến doanh thu vùng Miền Bắc, thích xem biểu đồ tròn)
- "unfinished_work": việc đang làm dở (ví dụ: đang phân tích file X về khuyến mãi, đã hỏi 3 câu nhưng chưa build dashboard)

Quy tắc:
- Mỗi ghi nhớ 1 câu tiếng Việt ngắn gọn, cụ thể (nêu tên cột/file/chủ đề thật từ nhật ký).
- KHÔNG ghi nhớ điều chỉ xảy ra 1 lần trừ khi rất rõ ràng (như việc dở dang).
- Nếu nhật ký không có gì đáng nhớ, trả về mảng rỗng.

Xuất JSON: {{"behaviors": [{{"description": "...", "category": "habit|preference|unfinished_work"}}]}}"""


def _format_actions(actions: list[dict]) -> str:
    lines = []
    for a in actions:
        ts = time.strftime("%d/%m %H:%M", time.localtime(a.get("ts") or 0))
        payload = a.get("payload") or "{}"
        try:
            payload_dict = json.loads(payload)
        except (TypeError, ValueError):
            payload_dict = {}
        if a.get("type") == "chat_question":
            desc = f'hỏi: "{payload_dict.get("message", "?")}"'
            if payload_dict.get("error"):
                desc += " (bị lỗi)"
        elif a.get("type") == "build_dashboard":
            desc = f'build dashboard: "{payload_dict.get("prompt") or "(tổng quan)"}" ({payload_dict.get("n_kpis", 0)} KPI, {payload_dict.get("n_charts", 0)} chart)'
        elif a.get("type") == "upload":
            desc = f'upload file: {payload_dict.get("filenames", "?")} ({payload_dict.get("n_sheets", 0)} sheet)'
        elif a.get("type") == "generate_report":
            desc = "tạo báo cáo điều hành từ dashboard"
        else:
            desc = f'{a.get("type")}: {payload[:120]}'
        lines.append(f"[{ts}] {desc}")
    return "\n".join(lines)


def distill_user(user_id: str) -> int:
    """Distill one idle user's Action buffer into Behaviors. Returns how many
    new behaviors were saved. Deletes the processed Actions on success AND on
    'nothing worth remembering' — either way the buffer's job is done."""
    actions = graph.get_actions_for_distill(user_id)
    if len(actions) < MIN_ACTIONS:
        return 0

    existing = graph.get_behaviors(user_id)
    existing_text = "\n".join(f"- {b['description']}" for b in existing) or "(chưa có)"

    prompt = _DISTILL_PROMPT.format(
        action_log=_format_actions(actions), existing=existing_text
    )
    try:
        # BACKGROUND: nobody is waiting on this. It stops once the day's
        # allowance is 60% spent, so the tail of the budget stays available for
        # requests with a person behind them. The buffer is kept either way and
        # retried next cycle, so yielding costs nothing but a delay.
        result = call_ai(prompt, _DISTILL_SCHEMA, tier="fast", priority=energy.BACKGROUND)
    except AllModelsFailedError as exc:
        # Keep the Actions — retry next cycle when a model is available.
        logger.warning(f"[idle_job] distill failed for {user_id}, keeping buffer: {exc}")
        return 0

    behaviors = result.get("behaviors") or []
    if behaviors:
        graph.save_behaviors(user_id, behaviors)
    graph.delete_actions([a["id"] for a in actions])
    logger.info(f"[idle_job] {user_id}: {len(actions)} actions -> {len(behaviors)} behaviors")
    return len(behaviors)


def _loop():
    while True:
        time.sleep(CHECK_INTERVAL)
        try:
            if not graph.ENABLED:
                continue
            for row in graph.get_idle_users(IDLE_THRESHOLD, MIN_ACTIONS):
                distill_user(row["user_id"])
        except Exception:  # noqa: BLE001 — the loop must never die
            logger.exception("[idle_job] cycle failed")


def start() -> None:
    """Called once from app startup. Daemon thread: dies with the process."""
    threading.Thread(target=_loop, daemon=True, name="behavior-distill").start()
    logger.info(f"[idle_job] started (idle>{IDLE_THRESHOLD}s, every {CHECK_INTERVAL}s)")
