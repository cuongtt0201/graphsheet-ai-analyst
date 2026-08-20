"""Autonomous Memory & Preference Learner (Agent Tự Học & Cá Nhân Hóa Ngầm).

Asynchronously listens to conversational turns, extracting:
1. Explicit Business Rules (e.g. "Doanh thu thuần phải trừ chiết khấu").
2. User Habits & Preferences (e.g. "Luôn thích biểu đồ đường cho chuỗi thời gian").
3. Persists directly into Neo4j Knowledge Graph to evolve the user's permanent persona.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.memory import graph

logger = logging.getLogger(__name__)

_HARVEST_PROMPT = """Bạn là Memory Extractor của hệ thống AI Data Analyst.
Nhiệm vụ: Phân tích lượt trò chuyện vừa qua xem NGƯỜI DÙNG có dạy hoặc thể hiện LUẬT NGHIỆP VỤ / THÓI QUEN CÁ NHÂN nào cần ghi nhớ lâu dài không.

CÂU NÓI CỦA NGƯỜI DÙNG:
"{user_prompt}"

CÂU TRẢ LỜI CỦA AI:
"{assistant_reply}"

TIÊU CHÍ TRÍCH XUẤT:
1. `business_rules`: Định nghĩa công thức, cách tính chỉ số riêng, hoặc điều kiện lọc bắt buộc (VD: "Doanh thu thuần = Doanh số - Chiết khấu", "Luôn bỏ đơn CANCEL").
2. `behaviors`: Thói quen, sở thích hiển thị, phong cách phân tích (VD: "Thích biểu đồ cột", "Ưu tiên phân tích theo tuần").

QUAN TRỌNG: Chỉ trích xuất khi người dùng THỰC SỰ nêu luật/thói quen có giá trị lâu dài. Nếu chỉ là câu hỏi tính toán thông thường (VD: "Tính tổng doanh thu tháng 8"), hãy trả về mảng rỗng.

Trả về JSON:
{{
  "has_learned_knowledge": true/false,
  "business_rules": [
    {{
      "concept_name": "<tên khái niệm, vd: Doanh thu thuần>",
      "formula_desc": "<mô tả công thức hoặc quy tắc>",
      "target_columns": ["<cột liên quan>"]
    }}
  ],
  "behaviors": [
    {{
      "description": "<mô tả thói quen súc tích>",
      "category": "habit" hoặc "preference" hoặc "domain"
    }}
  ]
}}
"""

_HARVEST_SCHEMA = {
    "type": "object",
    "required": ["has_learned_knowledge", "business_rules", "behaviors"],
    "properties": {
        "has_learned_knowledge": {"type": "boolean"},
        "business_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["concept_name", "formula_desc"],
                "properties": {
                    "concept_name": {"type": "string"},
                    "formula_desc": {"type": "string"},
                    "target_columns": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "behaviors": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["description", "category"],
                "properties": {
                    "description": {"type": "string"},
                    "category": {"type": "string"},
                },
            },
        },
    },
}


def harvest_memory_sync(user_id: str, user_prompt: str, assistant_reply: str) -> dict:
    """Synchronous memory extraction and Neo4j persistence."""
    if not user_id or not user_prompt:
        return {"learned": False}

    # Heuristic fast pre-filter: skip if prompt doesn't look like an instruction/definition
    prompt_lower = user_prompt.lower()
    learning_triggers = ["phải", "luôn", "bên tôi", "bên anh", "bên em", "quy ước", "công thức", "định nghĩa", "nhé", "lưu ý", "nhớ", "đừng", "không tính", "trừ"]
    if not any(t in prompt_lower for t in learning_triggers):
        return {"learned": False}

    try:
        from app.ai.pool import call_ai
        prompt = _HARVEST_PROMPT.format(user_prompt=user_prompt[:500], assistant_reply=assistant_reply[:500])
        res = call_ai(prompt, _HARVEST_SCHEMA, tier="fast")
        
        if not res.get("has_learned_knowledge"):
            return {"learned": False}

        saved_rules = []
        for r in res.get("business_rules", []):
            c_name = r.get("concept_name")
            f_desc = r.get("formula_desc")
            if c_name and f_desc:
                graph.record_business_rule(
                    user_id=user_id,
                    concept_name=c_name,
                    formula_desc=f_desc,
                    target_columns=r.get("target_columns") or [],
                )
                saved_rules.append(c_name)

        saved_behaviors = []
        for b in res.get("behaviors", []):
            desc = b.get("description")
            if desc:
                graph.record_behavior(
                    user_id=user_id,
                    description=desc,
                    category=b.get("category", "habit"),
                )
                saved_behaviors.append(desc)

        logger.info(f"[learner] User {user_id} auto-learned: rules={saved_rules}, behaviors={saved_behaviors}")
        return {
            "learned": True,
            "rules": saved_rules,
            "behaviors": saved_behaviors,
        }
    except Exception as exc:
        logger.warning(f"[learner] Harvest failed gracefully: {exc}")
        return {"learned": False, "error": str(exc)}


def harvest_memory_async(user_id: str, user_prompt: str, assistant_reply: str) -> None:
    """Non-blocking background memory harvesting."""
    if not user_id:
        return
    thread = threading.Thread(
        target=harvest_memory_sync,
        args=(user_id, user_prompt, assistant_reply),
        daemon=True,
    )
    thread.start()
