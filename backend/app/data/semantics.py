"""Semantic understanding of an uploaded sheet — what the data actually MEANS.

The deterministic profiler (data/profiler.py) answers "what type is this
column". This module answers the questions a human analyst settles first, and
which no amount of dtype inspection can reveal:

  - GRAIN: what does ONE ROW represent? A whole invoice, one line item inside
    an invoice, one product, one daily snapshot? This is the single most
    consequential fact about a table. Counting rows to get "số đơn hàng" is
    wrong on a line-item table; summing a stock level across a snapshot table
    is meaningless. It also decides whether identical rows are a data error or
    legitimate data (see dedup_safe).
  - UNIT: VNĐ, nghìn VNĐ, USD, cái, kg. A number without its unit is unreadable
    and gets narrated wrongly downstream.
  - DOMAIN / sheet role: retail POS vs inventory vs HR; fact table vs lookup
    dimension - which drives how sheets should be joined.

Sheets are analysed a few per call (see analyze_all), best-effort: any failure
falls back to the per-sheet path and then to None, so the pipeline continues
exactly as before whatever goes wrong. The result is persisted in
session state and injected into every downstream prompt, so chat, dashboard and
report all reason from the same understanding of the data.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Enum kept small and concrete: a free-text grain is useful for humans, but
# downstream CODE (the dedup decision) needs a value it can branch on.
GRAIN_TYPES = [
    "transaction_line",  # 1 dòng = 1 mặt hàng trong một giao dịch/hoá đơn
    "transaction",       # 1 dòng = 1 giao dịch/hoá đơn trọn vẹn
    "entity",            # 1 dòng = 1 thực thể (sản phẩm, khách hàng, cửa hàng)
    "snapshot",          # 1 dòng = trạng thái của 1 thực thể tại 1 thời điểm
    "aggregate",         # 1 dòng = số liệu đã tổng hợp sẵn (theo tháng, theo vùng)
    "unknown",
]

SEMANTIC_SCHEMA = {
    "type": "object",
    "required": ["grain_type", "grain_description", "dedup_safe", "sheet_role"],
    "properties": {
        "grain_type": {"type": "string", "enum": GRAIN_TYPES},
        "grain_description": {
            "type": "string",
            "description": "Một câu tiếng Việt: một dòng trong bảng này đại diện cho cái gì.",
        },
        "dedup_safe": {
            "type": "boolean",
            "description": "true nếu hai dòng giống hệt nhau CHẮC CHẮN là lỗi trùng lặp và xoá bớt được an toàn.",
        },
        "domain": {"type": "string", "description": "Lĩnh vực nghiệp vụ, ví dụ 'bán lẻ POS', 'tồn kho', 'nhân sự'."},
        "sheet_role": {"type": "string", "enum": ["fact", "dimension", "unknown"]},
        "primary_measure": {"type": "string", "description": "Tên cột số quan trọng nhất về mặt kinh doanh."},
        "measure_unit": {"type": "string", "description": "Đơn vị của cột đó: VNĐ, nghìn VNĐ, USD, cái, kg..."},
        "entity_key": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Cột (hoặc tổ hợp cột) định danh một dòng.",
        },
        "caveats": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Cảnh báo khi dùng dữ liệu này, ví dụ 'không được cộng cột Tồn kho vì là snapshot'.",
        },
    },
}

_PROMPT = """Bạn là Data Analyst kỳ cựu đang tiếp nhận một bảng dữ liệu mới và phải xác lập
cách hiểu đúng về nó TRƯỚC KHI bất kỳ ai phân tích.

TÊN BẢNG: {source_id}
SỐ DÒNG: {row_count}

CÁC CỘT (tên, kiểu, vai trò suy ra tự động, %rỗng, số giá trị phân biệt, mẫu):
{columns}

VÀI DÒNG DỮ LIỆU THẬT:
{preview}

SỐ DÒNG TRÙNG HOÀN TOÀN: {dup_count}

Hãy trả lời các câu hỏi sau, DỰA TRÊN dữ liệu thật ở trên, không suy diễn quá xa:

1. grain_type + grain_description — MỘT DÒNG đại diện cho cái gì? Đây là câu hỏi quan trọng nhất.
   Gợi ý phân biệt: nếu cùng một mã hoá đơn/đơn hàng xuất hiện ở NHIỀU dòng khác nhau
   → đó là "transaction_line" (mỗi dòng là một mặt hàng trong hoá đơn). Nếu mỗi dòng một
   mã riêng biệt → "transaction". Nếu là danh mục sản phẩm/cửa hàng/nhân viên → "entity".
   Nếu là tồn kho/số dư tại một thời điểm → "snapshot". Nếu đã cộng sẵn theo tháng/vùng → "aggregate".

2. dedup_safe — hai dòng GIỐNG HỆT NHAU trong bảng này là lỗi hay là dữ liệu hợp lệ?
   Đặt false khi dòng trùng có thể hợp lệ (ví dụ bảng chi tiết bán hàng: khách mua 2 lần
   cùng một sản phẩm cùng đơn giá trong một hoá đơn sẽ tạo ra 2 dòng y hệt).
   Chỉ đặt true khi chắc chắn mỗi dòng phải là duy nhất (bảng danh mục, bảng khoá chính).

3. sheet_role — bảng dữ liệu giao dịch (fact) hay bảng tra cứu/danh mục (dimension)?

4. primary_measure + measure_unit — cột số quan trọng nhất về mặt kinh doanh và ĐƠN VỊ của nó.
   Suy đơn vị từ tên cột và độ lớn giá trị (ví dụ "Thành tiền" hàng triệu → VNĐ).
   Nếu không xác định được thì để trống, TUYỆT ĐỐI không đoán bừa.

5. caveats — cảnh báo quan trọng khi dùng bảng này (ví dụ "cột Tồn cuối là snapshot,
   cộng lại theo thời gian là vô nghĩa", "thiếu dữ liệu tháng 3").

Trả lời DUY NHẤT một JSON đúng schema."""


def _format_columns(column_profiles: list[dict], limit: int = 40) -> str:
    lines = []
    for c in column_profiles[:limit]:
        bits = [f"{c.get('name')} ({c.get('dtype')}, vai trò={c.get('role')})"]
        if c.get("null_pct"):
            bits.append(f"rỗng {int(c['null_pct'] * 100)}%")
        bits.append(f"{c.get('distinct')} giá trị phân biệt")
        sample = c.get("sample") or []
        if sample:
            bits.append("vd: " + ", ".join(str(s) for s in sample[:3]))
        lines.append("  - " + " | ".join(bits))
    return "\n".join(lines)


def _format_preview(sample_rows: list[dict], limit: int = 5) -> str:
    if not sample_rows:
        return "(không có)"
    cols = list(sample_rows[0].keys())[:15]
    out = [" | ".join(cols)]
    for row in sample_rows[:limit]:
        out.append(" | ".join(str(row.get(c, ""))[:30] for c in cols))
    return "\n".join(out)


def analyze_sheet_semantics(profile: dict, dup_count: int = 0) -> dict | None:
    """One sheet -> its semantic profile, or None on any failure (the caller
    must keep working exactly as before when this is unavailable)."""
    try:
        from app.ai.pool import call_ai
    except Exception:
        return None

    prompt = _PROMPT.format(
        source_id=profile.get("source_id", "?"),
        row_count=profile.get("row_count", "?"),
        columns=_format_columns(profile.get("column_profiles") or []),
        preview=_format_preview(profile.get("sample_rows") or []),
        dup_count=dup_count,
    )
    try:
        result = call_ai(prompt, SEMANTIC_SCHEMA, tier="strong")
    except Exception as exc:  # noqa: BLE001 - never break upload over this
        logger.warning(f"[semantics] skipped for {profile.get('source_id')}: {exc}")
        return None

    return _verify(result, profile)


def _key_column(result: dict, profile: dict) -> str | None:
    """Which column the grain check should measure uniqueness on.

    `entity_key` is not a required field, so a model that simply omitted it used
    to disable the whole check silently — the one case where the guard was most
    needed (an answer given carelessly enough to skip a field) was the case it
    stopped running.

    The fallback is deliberately narrow. With two id columns ("Mã HĐ" and
    "Mã KH") picking the wrong one flips the grain the wrong way, and a wrong
    override is worse than none: it decides whether duplicate rows get deleted.
    So the fallback applies only when the profiler found exactly ONE id column
    and there is nothing to choose between.
    """
    names = {c.get("name") for c in (profile.get("column_profiles") or [])}
    keys = result.get("entity_key") or []
    if len(keys) == 1 and keys[0] in names:
        return keys[0]
    # A composite key cannot be checked from per-column counts at all.
    if len(keys) > 1:
        return None

    ids = [c.get("name") for c in (profile.get("column_profiles") or []) if c.get("role") == "id"]
    return ids[0] if len(ids) == 1 else None


def _verify(result: dict | None, profile: dict) -> dict | None:
    """Deterministic check of the model's answer against the profiler's counts.

    Shared by the batched and per-sheet paths: whichever route produced the
    answer, pandas gets the last word.
    """
    if not isinstance(result, dict):
        return None
    if result.get("grain_type") not in GRAIN_TYPES:
        result["grain_type"] = "unknown"

    # --- DETERMINISTIC SEMANTIC VERIFICATION ---
    # Trust but Verify: LLM đoán ngữ nghĩa, còn Pandas (thông qua profile) làm quan tòa.
    row_count = profile.get("row_count", 0)
    key_col = _key_column(result, profile)

    if row_count > 0 and key_col:
        col_prof = next((c for c in (profile.get("column_profiles") or []) if c.get("name") == key_col), None)

        if col_prof and col_prof.get("distinct") is not None:
            distinct_count = col_prof.get("distinct")
            
            # TÌNH HUỐNG 1: LLM bảo đây là bảng "Chi tiết hóa đơn" (nhiều dòng chung 1 mã HĐ)
            # NHƯNG số mã HĐ phân biệt lại BẰNG đúng số dòng -> Mỗi dòng là 1 HĐ riêng biệt.
            # -> Bác bỏ LLM, bẻ lái về "transaction" (Hóa đơn nguyên vẹn).
            if result["grain_type"] == "transaction_line" and distinct_count == row_count:
                logger.info(f"Override LLM: {profile.get('source_id')} claims transaction_line but {key_col} is unique. Fixing to 'transaction'.")
                result["grain_type"] = "transaction"
                result["dedup_safe"] = True  # Mã đã unique thì dòng trùng giống hệt nhau 100% là lỗi.
                
            # TÌNH HUỐNG 2: LLM bảo đây là bảng "Thực thể / Giao dịch gốc" (Mỗi dòng 1 mã duy nhất)
            # NHƯNG số mã phân biệt lại ít hơn rất nhiều so với số dòng (có lặp lại mã).
            # -> Bác bỏ LLM, bẻ lái về "transaction_line".
            elif result["grain_type"] in ("transaction", "entity") and distinct_count < row_count * 0.95:
                logger.info(f"Override LLM: {profile.get('source_id')} claims {result['grain_type']} but {key_col} has duplicates. Fixing to 'transaction_line'.")
                result["grain_type"] = "transaction_line"
                result["dedup_safe"] = False # Chấp nhận trùng lặp vì một hóa đơn có thể mua 2 món hàng y hệt nhau.

    return result


# How many sheets ride in one call. Lower than the header batcher's 8 because
# each sheet here contributes a full column listing plus preview rows rather
# than a handful of short lines — and because a lost batch costs more when the
# answer is this consequential.
BATCH_MAX_SHEETS = 4

_BATCH_CONTEXT = """Bạn là Data Analyst kỳ cựu đang tiếp nhận NHIỀU bảng dữ liệu từ cùng một
file và phải xác lập cách hiểu đúng về từng bảng TRƯỚC KHI bất kỳ ai phân tích.

Với MỖI bảng dưới đây, hãy trả lời:

1. grain_type + grain_description — MỘT DÒNG đại diện cho cái gì? Đây là câu hỏi quan trọng nhất.
   Nếu cùng một mã hoá đơn/đơn hàng xuất hiện ở NHIỀU dòng → "transaction_line". Nếu mỗi dòng
   một mã riêng biệt → "transaction". Nếu là danh mục sản phẩm/cửa hàng/nhân viên → "entity".
   Nếu là tồn kho/số dư tại một thời điểm → "snapshot". Nếu đã cộng sẵn theo tháng/vùng → "aggregate".

2. dedup_safe — hai dòng GIỐNG HỆT NHAU là lỗi hay dữ liệu hợp lệ? Đặt false khi dòng trùng
   có thể hợp lệ (bảng chi tiết bán hàng). Chỉ true khi mỗi dòng bắt buộc phải duy nhất.

3. sheet_role — bảng giao dịch (fact) hay bảng tra cứu/danh mục (dimension)?

4. primary_measure + measure_unit — cột số quan trọng nhất và ĐƠN VỊ của nó, suy từ tên cột và
   độ lớn giá trị. Không xác định được thì để trống, TUYỆT ĐỐI không đoán bừa.

5. caveats — cảnh báo khi dùng bảng này.

QUAN TRỌNG: các bảng trong cùng một file thường liên quan nhau (bảng chi tiết + bảng tổng hợp
đã cộng sẵn + bảng danh mục). Hãy tận dụng điều đó để phân biệt bảng nào là dữ liệu gốc và
bảng nào đã được tổng hợp — nhưng phán đoán về mỗi bảng phải dựa trên dữ liệu CỦA CHÍNH NÓ."""


def _task_text(profile: dict, dup_count: int) -> str:
    return (
        f'TÊN BẢNG: {profile.get("source_id", "?")}\n'
        f'SỐ DÒNG: {profile.get("row_count", "?")}\n\n'
        f'CÁC CỘT (tên, kiểu, vai trò suy ra tự động, %rỗng, số giá trị phân biệt, mẫu):\n'
        f'{_format_columns(profile.get("column_profiles") or [])}\n\n'
        f'VÀI DÒNG DỮ LIỆU THẬT:\n{_format_preview(profile.get("sample_rows") or [])}\n\n'
        f'SỐ DÒNG TRÙNG HOÀN TOÀN: {dup_count}'
    )


def analyze_all(profiles: list[dict], dup_counts: dict[str, int] | None = None,
                max_workers: int = 4) -> dict[str, dict]:
    """{source_id: semantic_profile} for every sheet. Sheets whose call failed
    are simply absent from the result.

    Batched, because this was the last per-sheet call left at upload: a 30-sheet
    accounting workbook spent 30 round-trips and 30 rate-limit slots on it,
    which is the single largest cost of opening such a file. Grouping sheets
    also lets the model see that one table is the summed version of another —
    a distinction it cannot make when each sheet arrives alone.
    """
    if not profiles:
        return {}
    dup_counts = dup_counts or {}

    out: dict[str, dict] = {}
    for start in range(0, len(profiles), BATCH_MAX_SHEETS):
        out.update(_analyze_chunk(profiles[start:start + BATCH_MAX_SHEETS], dup_counts, max_workers))
    return out


def _analyze_chunk(chunk: list[dict], dup_counts: dict[str, int], max_workers: int) -> dict[str, dict]:
    try:
        from app.ai.harness import batch_tasks
    except Exception:  # noqa: BLE001
        return _analyze_individually(chunk, dup_counts, max_workers)

    # Synthetic task keys: a source_id carries "::", dots and Vietnamese text,
    # none of which are safe as JSON-schema property names.
    keys = {f"sheet_{i}": p for i, p in enumerate(chunk)}
    tasks = {k: _task_text(p, dup_counts.get(p.get("source_id"), 0)) for k, p in keys.items()}

    try:
        results = batch_tasks(_BATCH_CONTEXT, tasks, tier="strong",
                              schemas={k: SEMANTIC_SCHEMA for k in keys})
    except Exception as exc:  # noqa: BLE001 - never break upload over this
        logger.warning(f"[semantics] batch failed, falling back per-sheet: {exc}")
        return _analyze_individually(chunk, dup_counts, max_workers)

    out: dict[str, dict] = {}
    missing: list[dict] = []
    for k, p in keys.items():
        sem = _verify(results.get(k), p)
        sid = p.get("source_id")
        if sid and sem:
            out[sid] = sem
        else:
            missing.append(p)

    # Grain is consequential enough that a sheet the batch dropped is worth a
    # second, individual attempt rather than being left with no semantics.
    if missing:
        out.update(_analyze_individually(missing, dup_counts, max_workers))
    return out


def _analyze_individually(chunk: list[dict], dup_counts: dict[str, int],
                          max_workers: int) -> dict[str, dict]:
    from concurrent.futures import ThreadPoolExecutor

    if not chunk:
        return {}

    def _one(p: dict):
        return p.get("source_id"), analyze_sheet_semantics(p, dup_counts.get(p.get("source_id"), 0))

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(chunk))) as pool:
        for sid, sem in pool.map(_one, chunk):
            if sid and sem:
                out[sid] = sem
    return out


def apply_grain_dedup(df, semantic: dict | None):
    """Delete exact-duplicate rows ONLY when the grain says they cannot be
    legitimate. Returns (df, removed_count).

    Deliberately conservative: no semantic profile, an unknown grain, or any
    transaction-shaped grain all mean "keep the rows". Wrongly keeping a
    duplicate is a visible flag the user can act on; wrongly deleting one
    silently understates every total forever."""
    if df is None or semantic is None:
        return df, 0
    if not semantic.get("dedup_safe"):
        return df, 0
    # dedup_safe is the model's judgement; the grain is the structural fact.
    # Require BOTH to agree before an irreversible delete.
    if semantic.get("grain_type") not in ("entity", "dimension", "aggregate"):
        return df, 0

    before = len(df)
    out = df.drop_duplicates().reset_index(drop=True)
    return out, before - len(out)


_GRAIN_LABEL = {
    "transaction_line": "mỗi dòng = 1 mặt hàng trong giao dịch",
    "transaction": "mỗi dòng = 1 giao dịch trọn vẹn",
    "entity": "mỗi dòng = 1 thực thể (danh mục)",
    "snapshot": "mỗi dòng = trạng thái tại 1 thời điểm",
    "aggregate": "mỗi dòng = số liệu đã tổng hợp sẵn",
    "unknown": "chưa xác định",
}


def format_semantics_for_prompt(semantics: dict[str, dict] | None) -> str:
    """Compact block injected into every downstream prompt, so chat, dashboard
    and report all reason from the SAME understanding of what the data is."""
    if not semantics:
        return ""
    lines = ["BẢN CHẤT DỮ LIỆU (đã phân tích lúc nạp file — dùng làm căn cứ, không suy diễn lại):"]
    for sid, s in semantics.items():
        parts = [f'- Bảng "{sid}": {s.get("grain_description") or _GRAIN_LABEL.get(s.get("grain_type"), "?")}']
        if s.get("domain"):
            parts.append(f"lĩnh vực {s['domain']}")
        if s.get("sheet_role") and s["sheet_role"] != "unknown":
            parts.append("bảng giao dịch" if s["sheet_role"] == "fact" else "bảng danh mục")
        if s.get("primary_measure"):
            unit = f" ({s['measure_unit']})" if s.get("measure_unit") else ""
            parts.append(f"chỉ số chính: {s['primary_measure']}{unit}")
        lines.append(" · ".join(parts))
        for c in (s.get("caveats") or [])[:3]:
            lines.append(f"    ⚠ {c}")
    lines.append(
        "LƯU Ý: nếu grain là 'mỗi dòng = 1 mặt hàng trong giao dịch' thì ĐẾM SỐ DÒNG "
        "KHÔNG phải số đơn hàng — phải đếm số mã giao dịch phân biệt."
    )
    return "\n".join(lines)
