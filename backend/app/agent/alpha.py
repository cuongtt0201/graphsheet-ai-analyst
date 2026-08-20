"""Alpha Meta-Cognitive Orchestrator (Ý thức Nội tâm & Con Đầu Đàn Trồi Sinh).

Maintains an evolving Inner Monologue, orchestrates specialized Worker Sub-Agents
(Python Sandbox, Data Critic, Visualizer), and autonomously adapts its beliefs (Mind Shifts)
when computational evidence contradicts initial hypotheses.
"""

from __future__ import annotations

import datetime
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import pandas as pd

from app.agent.critic import CriticVerdict, critique_execution
from app.agent.sandbox import SmartDataframeDict, run_pandas
from app.ai.pool import call_ai, progress_emit
from app.agent.swarm import build_swarm_context

logger = logging.getLogger(__name__)


@dataclass
class MindShift:
    """A recorded pivot in the Alpha Agent's reasoning trajectory."""
    previous_hypothesis: str
    triggering_signal: str
    adapted_hypothesis: str
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().strftime("%H:%M:%S"))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InnerMonologueState:
    """The evolving working memory and belief state of the Alpha Orchestrator."""
    user_intent: str
    current_hypothesis: str
    confidence: float = 0.8
    mind_shifts: list[MindShift] = field(default_factory=list)
    collected_facts: list[str] = field(default_factory=list)
    execution_history: list[dict] = field(default_factory=list)

    def record_mind_shift(self, trigger: str, new_hypothesis: str) -> MindShift:
        shift = MindShift(
            previous_hypothesis=self.current_hypothesis,
            triggering_signal=trigger,
            adapted_hypothesis=new_hypothesis,
        )
        self.mind_shifts.append(shift)
        self.current_hypothesis = new_hypothesis
        return shift

    def to_summary(self) -> str:
        lines = [
            f"🧠 Ý THỨC NỘI TÂM (ALPHA MONOLOGUE):",
            f"- Ý định phân tích: {self.user_intent}",
            f"- Giả thuyết hiện tại: {self.current_hypothesis}",
        ]
        if self.mind_shifts:
            lines.append("🔄 LỊCH SỬ BẺ LÁI (MIND SHIFTS):")
            for s in self.mind_shifts:
                lines.append(f"  ⚡ [{s.timestamp}] Tín hiệu: '{s.triggering_signal}' ➔ Đổi hướng: '{s.adapted_hypothesis}'")
        if self.collected_facts:
            lines.append("📌 SỰ THỰC ĐÃ KIỂM CHỨNG TỪ PYTHON:")
            for f in self.collected_facts:
                lines.append(f"  - {f}")
        return "\n".join(lines)


def _columns_reference(dataframes: dict[str, pd.DataFrame]) -> str:
    """The exact column list of every dataframe for sandbox code repair."""
    lines = []
    for sid, df in dataframes.items():
        if df is None:
            continue
        cols = ", ".join(f"'{c}'" for c in df.columns)
        lines.append(f"  dfs[\"{sid}\"] → [{cols}]")
    return "\n".join(lines)


CODE_RETRIES = 2


def _emit(event: dict) -> None:
    fn = progress_emit.get()
    if fn is not None:
        fn(event)


def run_alpha_cognition(
    question: str,
    dataframes: dict[str, pd.DataFrame],
    schema_context: str,
    user_id: str | None = None,
    initial_code: str | None = None,
    initial_hypothesis: str | None = None,
    skills_env: dict | None = None,
    skills_source: str = "",
    max_reflexion_turns: int = 1,
    call_ai_fn: Callable = call_ai,
    run_pandas_fn: Callable = run_pandas,
) -> dict:
    """Executes the Emergent Meta-Cognitive loop:
    1. Formulates initial hypothesis (or uses provided initial_code).
    2. Generates & runs Python in Sandbox (with self-healing Code Retries).
    3. Receives Critic feedback on valid execution.
    4. Evaluates if a Mind Shift is warranted (Reflexion on true statistical anomalies).
    5. Returns unified response with chart/kpi and thought stream.
    """
    _emit({"type": "step", "message": "🧠 Alpha Orchestrator đang thiết lập dòng suy nghĩ nội tâm..."})

    # Step 1: Initial formulation
    swarm_mem = build_swarm_context(user_id or "", question)
    curr_hypo = initial_hypothesis or f"Giải quyết câu hỏi '{question}' bằng phân tích số liệu trực tiếp"
    state = InnerMonologueState(
        user_intent=question,
        current_hypothesis=curr_hypo,
    )

    code_to_run = initial_code
    executed_code = code_to_run  # track the code that actually ran
    if not code_to_run:
        # Initial plan schema
        plan_prompt = f"""Bạn là Alpha Orchestrator — Con đầu đàn điều phối phân tích dữ liệu.
Nhiệm vụ: Lập giả thuyết ban đầu và viết 1 đoạn mã Python/Pandas ngắn để trích xuất số liệu giải quyết câu hỏi.

{swarm_mem}

DỮ LIỆU CÓ SẴN:
{schema_context}

CÂU HỎI CỦA NGƯỜI DÙNG:
"{question}"

YÊU CẦU:
1. Nêu giả thuyết hoặc hướng tiếp cận ban đầu (1-2 câu).
2. Viết mã Python gán kết quả vào biến `result` (DataFrame, Series hoặc Scalar).
3. Sử dụng `df` (nếu 1 bảng) hoặc `dfs['tên_sheet']`.

Trả về JSON:
{{
  "hypothesis": "<giả thuyết / hướng tiếp cận>",
  "code": "<mã python gán vào result>",
  "expected_signals": "<dự kiến số liệu sẽ phản ánh điều gì>"
}}
"""
        plan_schema = {
            "type": "object",
            "required": ["hypothesis", "code"],
            "properties": {
                "hypothesis": {"type": "string"},
                "code": {"type": "string"},
                "expected_signals": {"type": "string"},
            }
        }

        try:
            plan_res = call_ai_fn(plan_prompt, plan_schema, tier="strong")
            state.current_hypothesis = plan_res.get("hypothesis", state.current_hypothesis)
            code_to_run = plan_res.get("code", "")
        except Exception as exc:
            logger.warning(f"[alpha] Planning fallback: {exc}")
            code_to_run = "result = df.head(10) if df is not None else None"

    _emit({"type": "step", "message": f"💡 Giả thuyết: {state.current_hypothesis}"})
    _emit({"type": "step", "message": "⚡ Đang giao việc cho Python Sandbox Worker..."})
    executed_code = code_to_run  # sync after planning may have updated code_to_run

    # Step 2: Run in Sandbox with Code Retry Loop
    run_res = run_pandas_fn(code_to_run, dataframes, skills_env=skills_env, skills_source=skills_source)
    retries = 0
    while not run_res.get("ok") and retries < CODE_RETRIES:
        retries += 1
        error_msg = run_res.get("error", "Lỗi thực thi không xác định")
        _emit({"type": "step", "message": f"⚙️ Đang tự động sửa lỗi mã Python (lần {retries}/{CODE_RETRIES})..."})

        cols_ref = _columns_reference(dataframes)
        fix_prompt = f"""Mã Python trước đó thực thi thất bại trong Sandbox. Hãy sửa lại code để chạy thành công.

CÂU HỎI: "{question}"
MÃ BỊ LỖI:
```python
{executed_code}
```
LỖI CHI TIẾT TỪ SANDBOX:
{error_msg}

DANH SÁCH CỘT THỰC TẾ TRONG DATAFRAME:
{cols_ref}

YÊU CẦU:
1. Sửa lỗi chính xác theo thông báo lỗi (đặc biệt chú ý tên cột trong dfs['...']).
2. Gán kết quả cuối cùng vào biến `result`.
3. Chỉ trả về JSON với mã code mới.

Trả về JSON:
{{
  "code": "<mã python đã sửa>",
  "fix_explanation": "<giải thích ngắn gọn cách sửa>"
}}
"""
        fix_schema = {
            "type": "object",
            "required": ["code"],
            "properties": {
                "code": {"type": "string"},
                "fix_explanation": {"type": "string"},
            }
        }
        try:
            fix_res = call_ai_fn(fix_prompt, fix_schema, tier="strong")
            new_code = fix_res.get("code", "")
            if new_code:
                executed_code = new_code
                run_res = run_pandas_fn(new_code, dataframes, skills_env=skills_env, skills_source=skills_source)
            else:
                break
        except Exception as exc:
            logger.warning(f"[alpha] Code fix fallback: {exc}")
            break

    # Step 3: Critic evaluation (ONLY on successful execution)
    verdict = CriticVerdict()
    final_run_res = run_res
    if run_res.get("ok"):
        verdict = critique_execution(run_res.get("kind", "text"), run_res.get("result"))
        
        # Step 4: Reflexion & Mind Shift Loop (ONLY on true statistical anomalies)
        if verdict.has_anomalies and max_reflexion_turns > 0:
            trigger_msg = "; ".join(verdict.anomaly_signals or verdict.statistical_insights)
            adapted_target = verdict.suggested_drill_down[0] if verdict.suggested_drill_down else "Đào sâu vào điểm bất thường được Critic cảnh báo"
            
            shift = state.record_mind_shift(trigger_msg, adapted_target)
            
            # Broadcast Mind Shift event to UI
            _emit({
                "type": "step",
                "message": f"🔄 Alpha đổi hướng: Phát hiện bất thường ({trigger_msg}) ➔ {adapted_target}",
            })
            _emit({
                "type": "mind_shift",
                "from": shift.previous_hypothesis,
                "to": shift.adapted_hypothesis,
                "signal": shift.triggering_signal,
            })

            # Generate refined drill-down code
            refine_prompt = f"""Alpha đang thực hiện BẺ LÁI SUY NGHĨ (Mind Shift) dựa trên số liệu thực tế.
Giả thuyết cũ: {shift.previous_hypothesis}
Tín hiệu bất thường từ Critic: {shift.triggering_signal}
Mục tiêu mới: {shift.adapted_hypothesis}

{schema_context}

Hãy viết lại mã Python/Pandas để đào sâu vào vấn đề bất thường này, gán kết quả vào `result`.
Trả về JSON:
{{
  "code": "<mã python đào sâu>",
  "reason": "<lý do đổi mã>"
}}
"""
            refine_schema = {
                "type": "object",
                "required": ["code"],
                "properties": {
                    "code": {"type": "string"},
                    "reason": {"type": "string"},
                }
            }
            try:
                refine_res = call_ai_fn(refine_prompt, refine_schema, tier="strong")
                new_code = refine_res.get("code", "")
                if new_code:
                    _emit({"type": "step", "message": "⚙️ Đang chạy phân tích đào sâu (Drill-down)..."})
                    second_run = run_pandas_fn(new_code, dataframes, skills_env=skills_env, skills_source=skills_source)
                    if second_run.get("ok"):
                        final_run_res = second_run
                        executed_code = new_code
                        state.collected_facts.append(f"Đã đào sâu thành công theo hướng: {shift.adapted_hypothesis}")
            except Exception as exc:
                logger.warning(f"[alpha] Reflexion run fallback: {exc}")
    else:
        verdict.is_valid = False
        verdict.anomaly_signals.append(f"Thực thi Python thất bại: {run_res.get('error')}")

    # Step 5: Synthesis & Storyteller
    _emit({"type": "step", "message": "📝 Alpha đang đúc kết phân tích và trực quan hóa..."})
    from app.agent.chart_utils import condense_chat_chart
    from app.ai.harness import collect_ground_truth, collect_numbers_from_text, verify_numbers

    result_preview = json.dumps(final_run_res.get("result"), ensure_ascii=False, default=str)[:4000]
    guard_feedback = verdict.format_for_monologue() if verdict.has_anomalies or verdict.statistical_insights else ""

    # Restore Join warnings and non-additive metric guards
    join_guards = (final_run_res.get("join_warnings") or []) + (final_run_res.get("non_additive") or [])
    if join_guards:
        guard_feedback += "\n\n⚠️ CẢNH BÁO TOÁN HỌC / GHÉP BẢNG:\n" + "\n".join(f"- {w}" for w in join_guards)

    interp_prompt = f"""Bạn là Alpha Storyteller — tổng hợp kết quả phân tích dữ liệu cho người dùng.
CÂU HỎI: "{question}"
{state.to_summary()}

KẾT QUẢ ĐÃ TÍNH TỪ PYTHON:
{result_preview}

{guard_feedback}

YÊU CẦU:
1. Trả lời trực tiếp, rõ ràng, gãy gọn bằng tiếng Việt. Nếu có Mind Shift (bẻ lái do phát hiện bất thường), hãy chỉ rõ phát hiện bất ngờ đó cho người dùng.
2. CHỈ DÙNG các con số có thật trong kết quả ở trên. Tuyệt đối không bịa số.
3. Nếu dữ liệu phù hợp vẽ biểu đồ:
   - Các biểu đồ cơ bản (so sánh, xu hướng, tỷ trọng): xuất type là "bar", "line" hoặc "pie".
   - Các biểu đồ phức tạp (nhiều chiều, scatter, heatmap, grouped bar, stacked area): xuất type là "vega" và cung cấp đầy đủ "vegaLiteSpec" tự chứa dữ liệu values.
   - Nếu không phù hợp vẽ biểu đồ, xuất chart là null.

Trả về JSON:
{{
  "answer": "<câu trả lời chi tiết, chuyên nghiệp>",
  "chart": {{
    "type": "bar" | "line" | "pie" | "vega",
    "title": "<tiêu đề ngắn>",
    "x_axis": "<tên cột x>",
    "y_axis": "<tên cột y>",
    "labels": ["nhãn 1", "nhãn 2"],
    "values": [10, 20],
    "vegaLiteSpec": {{}}
  }} hoặc null,
  "follow_up": ["3 câu hỏi đào sâu tiếp theo"]
}}
"""
    interp_schema = {
        "type": "object",
        "required": ["answer"],
        "properties": {
            "answer": {"type": "string"},
            "chart": {
                "type": ["object", "null"],
                "properties": {
                    "type": {"type": "string", "enum": ["bar", "line", "pie", "vega"]},
                    "title": {"type": "string"},
                    "x_axis": {"type": "string"},
                    "y_axis": {"type": "string"},
                    "labels": {"type": "array", "items": {"type": "string"}},
                    "values": {"type": "array", "items": {"type": ["number", "string", "null"]}},
                    "vegaLiteSpec": {"type": "object"},
                },
            },
            "follow_up": {"type": "array", "items": {"type": "string"}},
        }
    }

    try:
        interp = call_ai_fn(interp_prompt, interp_schema, tier="strong")
        answer = interp.get("answer", "Đã phân tích xong dữ liệu.")
        chart = condense_chat_chart(interp.get("chart"))
        follow_up = interp.get("follow_up") or []
    except Exception as exc:
        logger.warning(f"[alpha] Storyteller fallback: {exc}")
        answer = "Đã tính toán xong dữ liệu (xem bảng kết quả bên dưới)."
        chart = None
        follow_up = []

    answer_truths = collect_ground_truth(final_run_res.get("result")) | collect_numbers_from_text(schema_context)
    violations = verify_numbers(answer, answer_truths)
    if violations:
        bad = ", ".join(v["token"] for v in violations[:5])
        _emit({"type": "step", "message": "🔍 Phát hiện số chưa khớp kết quả tính — đang viết lại..."})
        try:
            interp = call_ai_fn(
                interp_prompt
                + f"\n\nCÂU TRẢ LỜI TRƯỚC chứa số KHÔNG có trong kết quả đã tính: {bad}. "
                  "Viết lại, CHỈ dùng đúng những con số xuất hiện trong KẾT QUẢ ở trên.",
                interp_schema, tier="strong",
            )
            answer = interp.get("answer", "")
            chart = condense_chat_chart(interp.get("chart"))
            follow_up = interp.get("follow_up") or []
        except Exception:
            pass
        if verify_numbers(answer, answer_truths):
            logger.warning(f"[alpha] ungrounded answer dropped: {answer[:80]}")
            answer = "Đã tính xong (xem bảng kết quả bên dưới)."
            follow_up = []

    table = final_run_res["result"] if final_run_res.get("kind") == "table" else None
    scalar = final_run_res["result"] if final_run_res.get("kind") == "scalar" else None

    # Step 6: Bounded Root-Cause Investigation (when warranted by analytical query)
    investigation = None
    if final_run_res.get("ok"):
        try:
            from app.agent.investigator import should_investigate, run_investigation
            warrants_inv, inv_hypo = should_investigate(question, result_preview)
            if warrants_inv:
                investigation = run_investigation(
                    question=question,
                    hypothesis=inv_hypo or state.current_hypothesis,
                    schema_text=schema_context,
                    dataframes=dataframes,
                    skills_env=skills_env,
                    skills_source=skills_source,
                    ground_truth=answer_truths,
                )
        except Exception as exc:
            logger.warning(f"[alpha] Investigation step fallback: {exc}")

    return {
        "ok": final_run_res.get("ok", False),
        "answer": answer,
        "code": executed_code or code_to_run,
        "table": table,
        "chart": chart,
        "scalar": scalar,
        "error": final_run_res.get("error"),
        "follow_up": follow_up,
        "reason": state.current_hypothesis,
        "verdict": verdict.to_dict(),
        "monologue": state.to_summary(),
        "mind_shifts": [s.to_dict() for s in state.mind_shifts],
        "investigation": investigation,
    }


