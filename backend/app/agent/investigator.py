"""Bounded investigation loop — what turns "tra cứu" into "phân tích".

A lookup engine answers the question asked. An analyst pulls the thread: sees
revenue down 20%, then asks which region, which stores, price or volume, one
bad day or a trend. That behaviour is a LOOP, and no amount of prompt wording
produces it from a single pass.

The loop here is deliberately NOT an open-ended agent:

  Activation gate  - most questions ("tổng doanh thu bao nhiêu") deserve one
                     query, not a five-round investigation. Digging into them
                     only burns quota and time.
  Fixed move set   - each round picks from a small menu of analytical moves
                     (break down by dimension, compare periods, check outliers,
                     split price vs volume...). Free-form agents wander; a menu
                     keeps every step explainable.
  Verify per round - each round's numbers pass the deterministic grounding gate
                     BEFORE they become input to the next. This is the whole
                     reason a loop is safe here: without it, round 3 reasons on
                     round 2's hallucination and the final story is confidently
                     wrong.
  Hard budget      - max rounds and a wall-clock cap, because free-tier quota
                     is a real limit and a user waiting is a real cost.

Every step is streamed, so the user watches the reasoning instead of receiving
a verdict from a black box.
"""

from __future__ import annotations

import json
import logging
import time

from app.ai.pool import AllModelsFailedError, call_ai, progress_emit

logger = logging.getLogger(__name__)

MAX_ROUNDS = 4
TIME_BUDGET_S = 75.0
RESULT_PREVIEW_CHARS = 1500


def _emit(event: dict) -> None:
    fn = progress_emit.get()
    if fn is not None:
        fn(event)


def _step(message: str) -> None:
    _emit({"type": "step", "message": message})


# ── Activation gate ──────────────────────────────────────────────────────────

GATE_SCHEMA = {
    "type": "object",
    "required": ["investigate", "reason"],
    "properties": {
        "investigate": {"type": "boolean"},
        "reason": {"type": "string"},
        "hypothesis": {"type": "string", "description": "Điều đáng đào sâu, nếu có."},
    },
}

GATE_PROMPT = """Bạn vừa trả lời một câu hỏi phân tích dữ liệu. Hãy quyết định có ĐÁNG đào sâu thêm không.

CÂU HỎI: "{question}"
KẾT QUẢ VỪA TÍNH: {result}

Đặt investigate=true CHỈ KHI có lý do phân tích thật sự, ví dụ:
- Câu hỏi mang tính "vì sao / do đâu / nguyên nhân / làm sao cải thiện".
- Kết quả lộ ra điều bất thường đáng truy (một nhóm lệch hẳn, sụt/tăng mạnh, phân bố quá lệch).
- Câu hỏi so sánh mà mới chỉ trả lời được một nửa.

Đặt investigate=false khi:
- Câu hỏi chỉ hỏi một con số/danh sách và đã trả lời xong ("tổng doanh thu bao nhiêu", "liệt kê cửa hàng").
- Kết quả không có gì bất thường.
- Dữ liệu quá ít để đào sâu có ý nghĩa.

Đào sâu tốn thời gian của người dùng — chỉ làm khi thật sự đáng.
Trả lời DUY NHẤT JSON đúng schema."""


def should_investigate(question: str, result_preview: str) -> tuple[bool, str]:
    """(có nên đào sâu, giả thuyết). Never raises — on any failure the caller
    simply skips the investigation and keeps the plain answer."""
    try:
        res = call_ai(
            GATE_PROMPT.format(question=question, result=result_preview[:RESULT_PREVIEW_CHARS]),
            GATE_SCHEMA, tier="fast",
        )
    except Exception as exc:  # noqa: BLE001
        logger.info(f"[investigator] gate skipped: {exc}")
        return False, ""
    return bool(res.get("investigate")), (res.get("hypothesis") or "").strip()


# ── Planning one round ───────────────────────────────────────────────────────

MOVES = [
    "breakdown",     # tách theo một chiều (khu vực/sản phẩm/kênh) để tìm nguồn gốc
    "compare",       # so sánh hai kỳ / hai nhóm
    "outlier",       # tìm điểm bất thường trong chuỗi
    "composition",   # cơ cấu / tỷ trọng, mức độ tập trung
    "decompose",     # tách một chỉ số thành thừa số (giá × lượng)
    "done",          # đủ rồi
]

ROUND_SCHEMA = {
    "type": "object",
    "required": ["move", "rationale"],
    "properties": {
        "move": {"type": "string", "enum": MOVES},
        "rationale": {"type": "string", "description": "Một câu tiếng Việt: đang muốn biết gì."},
        "code": {"type": "string", "description": "Đoạn pandas gán kết quả vào biến `result`."},
    },
}

ROUND_PROMPT = """Hãy ĐIỀU TRA sâu một câu hỏi, theo từng bước một.

{schema}

CÂU HỎI GỐC: "{question}"
GIẢ THUYẾT BAN ĐẦU: {hypothesis}

NHỮNG GÌ ĐÃ BIẾT (kết quả các bước trước):
{findings}

Chọn NƯỚC ĐI tiếp theo (mỗi lượt một nước, đừng làm nhiều việc trong một đoạn code):
- breakdown: tách theo một chiều để tìm phần nào gây ra hiện tượng (ví dụ mức giảm đến từ khu vực nào).
- compare: so sánh hai kỳ hoặc hai nhóm với nhau.
- outlier: tìm kỳ/điểm bất thường trong chuỗi thời gian.
- composition: xem cơ cấu, tỷ trọng, mức độ tập trung (vài nhóm chiếm bao nhiêu %).
- decompose: tách một chỉ số thành thừa số cấu thành (ví dụ doanh thu = số lượng × đơn giá) để biết do giá hay do lượng.
- done: đã đủ dữ kiện để kết luận, KHÔNG cần đào thêm.

Nếu chọn nước đi khác "done", viết `code` là một đoạn pandas NGẮN:
- Dữ liệu ở `df` (bảng đầu tiên) và `dfs` (dict theo source_id). `pd`, `np` đã có sẵn.
- KHÔNG import, KHÔNG đọc/ghi file.
- Gán kết quả cuối vào biến `result` (DataFrame/Series/số).
- Kết quả phải NHỎ (tối đa ~15 dòng): đã tổng hợp, đã sắp xếp, chỉ lấy phần đáng chú ý.
- Chỉ dùng đúng tên cột có thật trong schema trên.

Trả lời DUY NHẤT JSON đúng schema."""


SUMMARY_SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
            "description": "Chuỗi phát hiện theo thứ tự suy luận, mỗi ý một câu, có số cụ thể.",
        },
        "conclusion": {"type": "string", "description": "Kết luận ngắn gọn cho câu hỏi gốc."},
    },
}

SUMMARY_PROMPT = """Bạn vừa điều tra xong một câu hỏi. Hãy tổng hợp thành chuỗi phát hiện.

CÂU HỎI GỐC: "{question}"

CÁC BƯỚC ĐÃ CHẠY VÀ KẾT QUẢ THẬT:
{findings}

Viết:
- findings: 2-5 ý theo đúng mạch suy luận (từ hiện tượng → nguyên nhân cụ thể), MỖI Ý KÈM SỐ THẬT lấy từ kết quả trên.
- conclusion: 1-2 câu trả lời thẳng câu hỏi gốc.

TUYỆT ĐỐI chỉ dùng những con số xuất hiện trong kết quả ở trên. Không ước lượng, không bịa.
{number_rules}

Trả lời DUY NHẤT JSON đúng schema."""


def run_investigation(question: str, hypothesis: str, schema_text: str, dataframes: dict,
                      skills_env: dict | None = None, skills_source: str = "",
                      ground_truth: set | None = None) -> dict | None:
    """Runs the bounded loop. Returns {"findings": [...], "conclusion": str,
    "rounds": [...]} or None if nothing useful came out.

    `ground_truth` is seeded from the first answer's numbers and GROWS with each
    verified round, so the final write-up can cite anything the investigation
    actually computed - and nothing else.
    """
    from app.agent.sandbox import run_pandas
    from app.ai.harness import collect_ground_truth, verify_numbers
    from app.agent.number_format import NUMBER_STYLE_RULES

    truths = set(ground_truth or set())
    rounds: list[dict] = []
    deadline = time.monotonic() + TIME_BUDGET_S

    for i in range(MAX_ROUNDS):
        if time.monotonic() > deadline:
            _step("⏱️ Hết thời gian điều tra, dừng và tổng hợp lại.")
            break

        findings_text = "\n".join(
            f"- [{r['move']}] {r['rationale']}\n  Kết quả: {r['result_preview']}" for r in rounds
        ) or "(chưa có, đây là bước đầu tiên)"

        try:
            plan = call_ai(
                ROUND_PROMPT.format(
                    schema=schema_text, question=question,
                    hypothesis=hypothesis or "(chưa có)", findings=findings_text,
                ),
                ROUND_SCHEMA, tier="strong",
            )
        except AllModelsFailedError:
            break

        move = plan.get("move")
        rationale = (plan.get("rationale") or "").strip()
        if move == "done":
            _step(f"✅ Đã đủ dữ kiện: {rationale}")
            break

        code = plan.get("code") or ""
        if not code.strip():
            break

        _step(f"🔎 Bước {i + 1}: {rationale or move}")
        run = run_pandas(code, dataframes, skills_env=skills_env, skills_source=skills_source)
        if not run["ok"]:
            # A dead end is information, not a failure: record it so the next
            # round picks a different angle instead of repeating this one.
            _step(f"↩️ Hướng này không chạy được, đổi cách tiếp cận.")
            rounds.append({
                "move": move, "rationale": rationale,
                "result_preview": f"(không chạy được: {str(run['error'])[:120]})",
                "failed": True,
            })
            continue

        preview = json.dumps(run["result"], ensure_ascii=False, default=str)[:RESULT_PREVIEW_CHARS]

        # Every number this round produced becomes citable ground truth for the
        # write-up - and the write-up is checked against exactly this set.
        truths |= collect_ground_truth(run["result"])

        rounds.append({
            "move": move, "rationale": rationale, "code": code,
            "result_preview": preview, "failed": False,
        })

    real_rounds = [r for r in rounds if not r.get("failed")]
    if not real_rounds:
        return None

    findings_text = "\n".join(
        f"- [{r['move']}] {r['rationale']}\n  Kết quả: {r['result_preview']}" for r in real_rounds
    )
    _step("🧩 Đang tổng hợp chuỗi phát hiện...")
    try:
        summary = call_ai(
            SUMMARY_PROMPT.format(question=question, findings=findings_text,
                                  number_rules=NUMBER_STYLE_RULES),
            SUMMARY_SCHEMA, tier="strong",
        )
    except AllModelsFailedError:
        return None

    findings = [f for f in (summary.get("findings") or []) if isinstance(f, str) and f.strip()]
    conclusion = (summary.get("conclusion") or "").strip()

    # Same rule as insights/report: a finding citing a number the investigation
    # never computed is dropped rather than shown. A shorter honest chain beats
    # a longer invented one.
    kept = [f for f in findings if not verify_numbers(f, truths)]
    dropped = len(findings) - len(kept)
    if dropped:
        logger.info(f"[investigator] dropped {dropped} finding(s) with unverifiable numbers")
    if conclusion and verify_numbers(conclusion, truths):
        conclusion = ""

    if not kept and not conclusion:
        return None
    return {"findings": kept, "conclusion": conclusion, "rounds": len(real_rounds)}
