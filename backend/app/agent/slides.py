"""Deck spec: what a slide deck contains, decided by the model.

The model chooses CONTENT and which of a closed set of layouts holds it. It
never decides where anything sits on screen -- CSS does that in the browser,
which is the only part of this pipeline that actually knows how wide a
Vietnamese sentence renders. This split is the whole reason the deck cannot end
up with boxes overlapping each other.

The character budgets below are the other half. Most "ugly slide" is really
"too much text for the space", and that is cheaper to prevent at generation
time than to fix at render time. Every budget is enforced twice: stated in the
schema so the model aims for it, and clamped in code so a model that ignores it
still cannot produce a broken slide.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.ai.pool import call_ai, progress_emit

logger = logging.getLogger(__name__)


# ── Content budgets ────────────────────────────────────────────────────────
# Tuned for Vietnamese, which runs roughly 20-30% longer than English for the
# same meaning; budgets set from English samples overflow here.
MAX_SLIDES = 12
LIMITS = {
    "deck_title": 70,
    "deck_subtitle": 110,
    "heading": 68,
    "kicker": 40,
    "bullet": 105,
    "takeaway": 150,
    "kpi_label": 34,
    "kpi_value": 14,
    "kpi_note": 40,
    "big_number": 12,
    "big_caption": 90,
    "body": 240,
}
MAX_BULLETS = 5
MAX_KPIS = 4

LAYOUTS = ("title", "section", "kpi", "chart", "chart_split", "bullets", "big_number", "closing")


DECK_SCHEMA = {
    "type": "object",
    "required": ["title", "slides"],
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "slides": {
            "type": "array",
            "minItems": 3,
            "maxItems": MAX_SLIDES,
            "items": {
                "type": "object",
                "required": ["layout"],
                "properties": {
                    # Descriptions stay terse here on purpose. Gemini rejects a
                    # request outright when the schema "produces a constraint
                    # that has too many states for serving", and long
                    # descriptions are the stated cause -- the weaker models in
                    # the pool refused this schema until the explanations moved
                    # into the prompt, where they cost nothing structural.
                    "layout": {"type": "string", "enum": list(LAYOUTS)},
                    "kicker": {"type": "string"},
                    "heading": {"type": "string"},
                    "takeaway": {"type": "string"},
                    "bullets": {
                        "type": "array",
                        "maxItems": MAX_BULLETS,
                        "items": {"type": "string"},
                    },
                    "kpis": {
                        "type": "array",
                        "maxItems": MAX_KPIS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "string"},
                                "note": {"type": "string"},
                            },
                        },
                    },
                    "big_value": {"type": "string"},
                    "big_caption": {"type": "string"},
                    "chart_index": {"type": "integer"},
                },
            },
        },
    },
}


# ── Clamping ───────────────────────────────────────────────────────────────

def _clip(text: Any, limit: int) -> str:
    """Trim to `limit`, cutting at a word boundary and marking the cut.

    Cutting mid-word reads as corruption; an ellipsis reads as an edit. Either
    way the user sees that something was removed, which is the point -- silent
    overflow is what puts one sentence on top of another.
    """
    s = " ".join(str(text or "").split())
    if len(s) <= limit:
        return s
    cut = s[: limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…"


def _clamp_slide(raw: dict, n_charts: int) -> dict | None:
    """One validated slide, or None when it cannot be rendered at all."""
    layout = str(raw.get("layout") or "").strip()
    if layout not in LAYOUTS:
        return None

    slide: dict[str, Any] = {"layout": layout}

    for field, key in (("kicker", "kicker"), ("heading", "heading"), ("takeaway", "takeaway")):
        if raw.get(field):
            slide[key] = _clip(raw[field], LIMITS[field])

    # Read bullets for every layout, not just the ones that display them: the
    # chart downgrade below needs to know whether there are words worth keeping,
    # and it cannot ask a field that was never parsed.
    bullets = [_clip(b, LIMITS["bullet"]) for b in (raw.get("bullets") or []) if str(b).strip()][:MAX_BULLETS]
    if layout in ("bullets", "chart_split", "closing"):
        slide["bullets"] = bullets

    if layout == "kpi":
        kpis = []
        for k in (raw.get("kpis") or [])[:MAX_KPIS]:
            if not isinstance(k, dict) or not str(k.get("value") or "").strip():
                continue
            entry = {
                "label": _clip(k.get("label"), LIMITS["kpi_label"]),
                "value": _clip(k.get("value"), LIMITS["kpi_value"]),
            }
            if k.get("note"):
                entry["note"] = _clip(k["note"], LIMITS["kpi_note"])
            kpis.append(entry)
        if not kpis:
            return None
        slide["kpis"] = kpis

    if layout == "big_number":
        value = _clip(raw.get("big_value"), LIMITS["big_number"])
        if not value:
            return None
        slide["big_value"] = value
        slide["big_caption"] = _clip(raw.get("big_caption"), LIMITS["big_caption"])

    if layout in ("chart", "chart_split"):
        idx = raw.get("chart_index")
        if not isinstance(idx, int) or not (0 <= idx < n_charts):
            # A chart slide with no chart is an empty box. Downgrade rather than
            # ship a blank: the words the model wrote are still worth showing.
            if bullets:
                slide["layout"] = "bullets"
                slide["bullets"] = bullets
            elif slide.get("takeaway"):
                slide["layout"] = "section"
                slide["heading"] = slide.get("heading") or slide["takeaway"]
            else:
                return None
        else:
            slide["chart_index"] = idx

    # Every layout except the chart ones needs words to be worth a slide.
    if slide["layout"] not in ("chart", "chart_split"):
        has_content = any(slide.get(k) for k in ("heading", "takeaway", "bullets", "kpis", "big_value"))
        if not has_content:
            return None
    return slide


def clamp_deck(raw: dict, n_charts: int) -> dict:
    """Enforce every budget on a model-authored deck."""
    slides = []
    for item in (raw.get("slides") or []):
        if not isinstance(item, dict):
            continue
        slide = _clamp_slide(item, n_charts)
        if slide is not None:
            slides.append(slide)
        if len(slides) >= MAX_SLIDES:
            break

    return {
        "title": _clip(raw.get("title"), LIMITS["deck_title"]) or "Báo cáo phân tích",
        "subtitle": _clip(raw.get("subtitle"), LIMITS["deck_subtitle"]),
        "slides": slides,
    }


# ── Generation ─────────────────────────────────────────────────────────────

DECK_PROMPT = """Bạn đang dựng một bài thuyết trình từ dashboard đã phân tích xong.

TẤT CẢ số liệu dưới đây là số THẬT đã tính bằng pandas. TUYỆT ĐỐI không bịa thêm
con số nào không có ở đây.

CÁC CHỈ SỐ:
{kpi_lines}

DANH SÁCH BIỂU ĐỒ (dùng `chart_index` để chèn vào slide):
{chart_lines}

NHẬN XÉT ĐÃ CÓ:
{insight_lines}

YÊU CẦU CỦA NGƯỜI DÙNG: "{user_prompt}"

CÁC LOẠI SLIDE (`layout`):
- title: trang bìa.
- section: trang phân mục, chỉ một dòng chữ lớn.
- kpi: 2-4 con số lớn (dùng `kpis`, mỗi mục có `label` + `value` đã định dạng sẵn như "2,02 tỷ", "68%").
- chart: một biểu đồ chiếm trọn trang + một câu `takeaway`. Bắt buộc có `chart_index`.
- chart_split: biểu đồ bên trái, `bullets` bên phải. Bắt buộc có `chart_index`.
- bullets: `heading` + tối đa {max_bullets} gạch đầu dòng.
- big_number: một con số duy nhất gây ấn tượng (`big_value` + `big_caption`).
- closing: đề xuất hành động, trang cuối.

`chart_index` là số thứ tự trong DANH SÁCH BIỂU ĐỒ ở trên, đếm từ 0.

CÁCH DỰNG:
1. Mở bằng slide `title`, kết bằng slide `closing` chứa đề xuất hành động.
2. Ở giữa: mỗi biểu đồ đáng nói cho một slide `chart` hoặc `chart_split`. Dùng
   `kpi` cho nhóm chỉ số, `big_number` khi có MỘT con số đáng để cả trang.
3. Mỗi slide nói ĐÚNG MỘT ý. Thà 8 slide rõ ràng còn hơn 4 slide nhồi nhét.
4. `takeaway` là điều người xem phải nhớ, KHÔNG phải mô tả lại biểu đồ.
   Sai: "Biểu đồ thể hiện doanh thu theo miền."
   Đúng: "Miền Bắc dẫn đầu nhưng khoảng cách ba miền chỉ 15%."

GIỚI HẠN CHỮ — viết vượt là bị cắt cụt, nên hãy viết trong giới hạn:
- heading ≤ {heading} ký tự
- mỗi gạch đầu dòng ≤ {bullet} ký tự, tối đa {max_bullets} gạch một slide
- takeaway ≤ {takeaway} ký tự
- nhãn chỉ số ≤ {kpi_label} ký tự, giá trị ≤ {kpi_value} ký tự (định dạng sẵn: "2,02 tỷ", "68%")

Viết tiếng Việt, giọng trình bày cho lãnh đạo. Trả về DUY NHẤT JSON đúng schema."""


def _emit(event: dict) -> None:
    fn = progress_emit.get()
    if fn is not None:
        fn(event)


def _fmt_kpis(kpis: list[dict]) -> str:
    if not kpis:
        return "(không có)"
    return "\n".join(
        f"- {k.get('title') or k.get('name') or 'Chỉ số'}: {k.get('value') if k.get('value') is not None else k.get('scalar')}"
        for k in kpis
    )


def _fmt_charts(charts: list[dict]) -> str:
    if not charts:
        return "(không có biểu đồ nào)"
    lines = []
    for i, c in enumerate(charts):
        labels = c.get("labels") or []
        values = c.get("values") or []
        pairs = ", ".join(f"{l}={v}" for l, v in list(zip(labels, values))[:8])
        more = " ..." if len(labels) > 8 else ""
        lines.append(f"[{i}] {c.get('title') or 'Biểu đồ'} ({c.get('type', 'bar')}): {pairs}{more}")
    return "\n".join(lines)


def build_deck(
    user_prompt: str,
    kpis: list[dict],
    charts: list[dict],
    insights: list[str] | None = None,
    call_ai_fn: Callable | None = None,
) -> dict:
    """Plan a deck over already-computed numbers. Never computes anything itself."""
    call_ai_fn = call_ai_fn or call_ai
    if not kpis and not charts:
        return {"ok": False, "error": "Chưa có dashboard hoặc biểu đồ nào để dựng slide."}

    _emit({"type": "step", "message": "🎞️ Đang dựng bố cục bài thuyết trình..."})

    prompt = DECK_PROMPT.format(
        kpi_lines=_fmt_kpis(kpis),
        chart_lines=_fmt_charts(charts),
        insight_lines="\n".join(f"- {i}" for i in (insights or [])) or "(không có)",
        user_prompt=user_prompt,
        heading=LIMITS["heading"],
        bullet=LIMITS["bullet"],
        max_bullets=MAX_BULLETS,
        takeaway=LIMITS["takeaway"],
        kpi_label=LIMITS["kpi_label"],
        kpi_value=LIMITS["kpi_value"],
    )

    try:
        raw = call_ai_fn(prompt, DECK_SCHEMA, tier="strong")
    except Exception as exc:
        logger.warning(f"[slides] deck planning failed: {exc}")
        return {"ok": False, "error": f"Không dựng được bài thuyết trình: {exc}"}

    deck = clamp_deck(raw, n_charts=len(charts))
    if not deck["slides"]:
        return {"ok": False, "error": "Mô hình không trả về slide nào dùng được."}

    # Charts ride along so the browser renders them live rather than as images.
    deck["charts"] = charts
    return {"ok": True, "deck": deck}
