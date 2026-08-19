"""Universal Semantic Dossier of an uploaded dataset — what the data actually MEANS.

The deterministic profiler (data/profiler.py) answers "what type is this column".
This module answers the questions a senior human analyst settles first across ANY
data domain (DevOps/Logs/IoT, Scientific/Lab, Education, Survey, Logistics, Finance, etc.):

  - ARCHETYPE: Is this server telemetry/logs, a scientific lab experiment, student exam grades,
    a customer satisfaction survey, logistics route tracking, or sales transactions?
  - PRIMARY PURPOSE: What business or operational objective does this table serve?
  - GRAIN: What does ONE ROW represent? An event/log entry, an invoice line, a single student,
    a daily snapshot, or pre-aggregated summary?
  - TARGET MEASURES & INDICATORS: What are the core measures, their concepts, units, and
    appropriate aggregations (sum, mean, p95, nunique)?
  - PREDICTED INTENT: What 3-4 key questions does the user/stakeholder need answered?
  - DATA BLINDSPOTS: What critical columns/information are missing, preventing certain metrics
    from being computed (e.g., revenue without cost -> cannot compute profit; logs without millisecond
    timestamps -> cannot measure micro-bursts)?
  - COLUMN GLOSSARY: Semantic translation of cryptic column names into human concepts.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DATA_ARCHETYPES = [
    "time_series_telemetry",   # Logs, IoT, Server metrics, Giám sát hệ thống
    "scientific_experimental", # Thí nghiệm phòng lab, Y tế, Thử nghiệm A/B, Khí hậu
    "academic_educational",    # Giáo dục, Điểm thi, Chuyên cần, Đánh giá học tập
    "survey_feedback",         # Khảo sát ý kiến, Đánh giá CSAT/NPS, Thăm dò
    "logistics_operations",    # Vận tải, Lộ trình xe, Kho bãi, Chuỗi cung ứng
    "master_registry",         # Danh mục thực thể (Nhân sự, Khách hàng, Thiết bị, Tài sản)
    "transactional_financial", # Giao dịch đơn hàng, Bán hàng, Tài chính, Kế toán
    "general_tabular",         # Dữ liệu bảng tổng quát khác
]

GRAIN_TYPES = [
    "event",             # 1 dòng = 1 sự kiện / 1 log entry xảy ra tại thời điểm t
    "transaction_line",  # 1 dòng = 1 mặt hàng trong một giao dịch/hoá đơn
    "transaction",       # 1 dòng = 1 giao dịch/hoá đơn trọn vẹn
    "entity",            # 1 dòng = 1 thực thể độc lập (sản phẩm, học sinh, máy chủ, khách hàng)
    "snapshot",          # 1 dòng = trạng thái của 1 thực thể tại 1 thời điểm định kỳ
    "aggregate",         # 1 dòng = số liệu đã tổng hợp sẵn (theo tháng, theo vùng, theo lớp)
    "sample",            # 1 dòng = 1 mẫu đo lường thí nghiệm / 1 phiếu khảo sát
    "unknown",
]

SEMANTIC_SCHEMA = {
    "type": "object",
    "required": ["archetype", "primary_purpose", "grain_type", "grain_description", "dedup_safe", "sheet_role"],
    "properties": {
        "archetype": {
            "type": "string",
            "enum": DATA_ARCHETYPES,
            "description": "Hình thái dữ liệu chuẩn mực phù hợp nhất.",
        },
        "primary_purpose": {
            "type": "string",
            "description": "Một câu tiếng Việt cô đọng về mục đích và bản chất của bảng dữ liệu này.",
        },
        "grain_type": {"type": "string", "enum": GRAIN_TYPES},
        "grain_description": {
            "type": "string",
            "description": "Một câu tiếng Việt mô tả chính xác một dòng trong bảng này đại diện cho cái gì.",
        },
        "dedup_safe": {
            "type": "boolean",
            "description": "true nếu hai dòng giống hệt nhau CHẮC CHẮN là lỗi trùng lặp và xoá bớt được an toàn.",
        },
        "domain": {"type": "string", "description": "Lĩnh vực cụ thể (ví dụ: 'DevOps / Nginx Logs', 'Y sinh / Thử nghiệm', 'Giáo dục ĐH', 'Bán lẻ POS')."},
        "sheet_role": {"type": "string", "enum": ["fact", "dimension", "telemetry", "registry", "unknown"]},
        "time_axis": {
            "type": "string",
            "description": "Tên cột mốc thời gian/timestamp chính (nếu có, ví dụ: 'timestamp', 'Ngay_Giao_Dich', 'created_at').",
        },
        "group_axis": {
            "type": "string",
            "description": "Tên cột phân loại/phân nhóm chính (ví dụ: 'service_name', 'Chi_Nhanh', 'Lop_Hoc', 'Treatment_Group').",
        },
        "target_measures": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "column": {"type": "string"},
                    "concept": {"type": "string", "description": "Khái niệm đo lường (vd: 'Độ trễ xử lý', 'Điểm trung bình', 'Tổng tiền', 'Lượng tồn kho')"},
                    "unit": {"type": "string", "description": "ms, giây, %, điểm, °C, cái, VNĐ, USD..."},
                    "aggregation_type": {"type": "string", "enum": ["sum", "mean", "median", "p95", "p99", "count", "nunique", "none"]},
                },
            },
            "description": "Danh sách các cột đo lường chính kèm đơn vị và phép tổng hợp phù hợp.",
        },
        "predicted_intent": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-4 bài toán / câu hỏi phân tích trọng tâm mà người dùng/sếp sẽ muốn giải quyết với tập dữ liệu này.",
        },
        "recommended_indicators": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Tên chỉ số (vd: 'Peak P95 Latency', 'Tỷ lệ Lỗi 5xx', 'Điểm Trung Bình Khối', 'Tỷ lệ Đỗ', 'AOV')"},
                    "formula_desc": {"type": "string", "description": "Mô tả công thức tính"},
                    "unit": {"type": "string"},
                },
            },
            "description": "Các chỉ số đo lường hoặc KPI tiêu biểu phù hợp nhất với lĩnh vực dữ liệu này.",
        },
        "blindspots": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Cảnh báo rõ ràng các chỉ số KHÔNG THỂ TÍNH ĐƯỢC do thiếu cột/thiếu thông tin trong file.",
        },
        "column_glossary": {
            "type": "object",
            "description": "Từ điển ánh xạ: Tên_cột_thô -> Ý nghĩa nghiệp vụ dễ hiểu.",
        },
        "caveats": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Cảnh báo kỹ thuật khi dùng dữ liệu (vd: 'Cột tồn kho là snapshot không được cộng dồn theo thời gian').",
        },
        "suggestions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-4 câu hỏi phân tích gợi ý thông minh, sát sườn với đúng lĩnh vực của file.",
        },
    },
}

_PROMPT = """Bạn là Senior Data Analyst & Domain Specialist kỳ cựu tiếp nhận một bảng dữ liệu mới.
Dữ liệu có thể thuộc BẤT KỲ LĨNH VỰC NÀO (Hạ tầng DevOps/Server logs, Thí nghiệm khoa học, Y tế, Giáo dục, Khảo sát, Logistics, Nhân sự, Bán hàng, Tài chính...).
Hãy thiết lập một HỒ SƠ TRI THỨC DỮ LIỆU (Universal Semantic Dossier) toàn diện TRƯỚC KHI bất kỳ ai phân tích.

TÊN BẢNG: {source_id}
SỐ DÒNG: {row_count}

CÁC CỘT (tên, kiểu, vai trò suy ra tự động, %rỗng, số giá trị phân biệt, mẫu):
{columns}

VÀI DÒNG DỮ LIỆU THẬT:
{preview}

SỐ DÒNG TRÙNG HOÀN TOÀN: {dup_count}

HÃY PHÂN TÍCH CHUYÊN SÂU THEO CÁC NGUYÊN TẮC SAU:

1. ARCHETYPE & DOMAIN:
   - Xác định chính xác hình thái dữ liệu:
     * `time_series_telemetry`: Logs, metrics server (latency, request_time, status code, 5xx/4xx, cpu, rps, sensor IoT).
     * `scientific_experimental`: Thí nghiệm lab, dữ liệu y sinh, đo lường hóa sinh, thử nghiệm A/B.
     * `academic_educational`: Điểm thi, bảng điểm học sinh/sinh viên, đánh giá môn học.
     * `survey_feedback`: Khảo sát ý kiến, thang điểm Likert 1-5, phản hồi khách hàng CSAT.
     * `logistics_operations`: Vận tải, kho bãi, tuyến đường, thời gian giao hàng (lead time).
     * `master_registry`: Danh mục nhân sự, danh mục khách hàng, danh sách tài sản/thiết bị.
     * `transactional_financial`: Hóa đơn bán hàng, giao dịch tiền, chi phí, doanh thu.
     * `general_tabular`: Bảng tổng hợp khác.

2. PRIMARY PURPOSE & GRAIN:
   - `primary_purpose`: Một câu tiếng Việt nêu rõ bảng này dùng để làm gì (vd: "Giám sát độ trễ và tỷ lệ lỗi của dịch vụ Core API", "Đánh giá kết quả thi học kỳ I khối 10").
   - `grain_type` & `grain_description`: MỘT DÒNG đại diện cho cái gì cụ thể (1 request log, 1 học sinh, 1 mặt hàng trong đơn, 1 mẫu đo).

3. DEDUP SAFE:
   - Đặt false nếu các dòng trùng hoàn toàn có thể là hợp lệ (vd: 2 request giống nhau, khách mua 2 món giống nhau).
   - Chỉ đặt true khi chắc chắn mỗi dòng là 1 thực thể độc nhất (bảng danh mục/registry).

4. MEASURES, TIME & GROUP AXIS:
   - Tìm cột thời gian chính (`time_axis`), cột phân nhóm chính (`group_axis`).
   - `target_measures`: Liệt kê các cột đo lường chính kèm đơn vị và cách gom nhóm chuẩn (vd: latency -> p95/mean; tiền -> sum; error_code -> count).

5. PREDICTED INTENT & RECOMMENDED INDICATORS:
   - `predicted_intent`: 3-4 bài toán cốt lõi tương ứng với ngành đó (vd Logs -> "Tìm khung giờ có tỷ lệ lỗi 5xx cao nhất", "Đánh giá p95/p99 latency theo endpoint").
   - `recommended_indicators`: Các chỉ số tiêu biểu (vd Logs -> "Peak P99 Latency (s)", "Tỷ lệ Lỗi 5xx (%)", "Total Requests").

6. BLINDSPOTS (ĐIỂM MÙ DỮ LIỆU):
   - Chỉ rõ những gì dữ liệu KHÔNG THỂ phân tích được do thiếu cột (vd: "Có doanh thu nhưng thiếu giá vốn -> không thể tính lợi nhuận ròng", "Có log response nhưng thiếu request payload -> không biết tham số đầu vào cụ thể").

7. SUGGESTIONS:
   - 3-4 câu hỏi phân tích ngắn gọn, dùng đúng tên cột thật, chạm đúng nghiệp vụ của ngành đó.

Trích xuất DUY NHẤT một JSON đúng schema."""


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
    """One sheet -> its universal semantic dossier, or None on failure."""
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
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[semantics] skipped for {profile.get('source_id')}: {exc}")
        return None

    return _verify(result, profile)


def _key_column(result: dict, profile: dict) -> str | None:
    names = {c.get("name") for c in (profile.get("column_profiles") or [])}
    keys = result.get("entity_key") or []
    if len(keys) == 1 and keys[0] in names:
        return keys[0]
    if len(keys) > 1:
        return None

    ids = [c.get("name") for c in (profile.get("column_profiles") or []) if c.get("role") == "id"]
    return ids[0] if len(ids) == 1 else None


def _verify(result: dict | None, profile: dict) -> dict | None:
    """Deterministic check of the model's answer against profiler counts."""
    if not isinstance(result, dict):
        return None
    if result.get("archetype") not in DATA_ARCHETYPES:
        result["archetype"] = "general_tabular"
    if result.get("grain_type") not in GRAIN_TYPES:
        result["grain_type"] = "unknown"

    row_count = profile.get("row_count", 0)
    key_col = _key_column(result, profile)

    if row_count > 0 and key_col:
        col_prof = next((c for c in (profile.get("column_profiles") or []) if c.get("name") == key_col), None)

        if col_prof and col_prof.get("distinct") is not None:
            distinct_count = col_prof.get("distinct")
            if result["grain_type"] == "transaction_line" and distinct_count == row_count:
                result["grain_type"] = "transaction"
                result["dedup_safe"] = True
            elif result["grain_type"] in ("transaction", "entity") and distinct_count < row_count * 0.95:
                result["grain_type"] = "transaction_line"
                result["dedup_safe"] = False

    return result


BATCH_MAX_SHEETS = 3

_BATCH_CONTEXT = """Bạn là Senior Data Analyst & Domain Specialist kỳ cựu tiếp nhận NHIỀU bảng dữ liệu từ cùng một file.
Dữ liệu có thể thuộc BẤT KỲ LĨNH VỰC NÀO (Hạ tầng DevOps/Logs, Thí nghiệm khoa học, Y tế, Giáo dục, Khảo sát, Logistics, Nhân sự, Bán hàng, Tài chính...).

Với MỖI bảng dưới đây, hãy xác lập một HỒ SƠ TRI THỨC DỮ LIỆU (Universal Semantic Dossier) hoàn chỉnh:
1. `archetype` & `domain`: Hình thái dữ liệu chuẩn (time_series_telemetry, scientific_experimental, academic_educational, survey_feedback, logistics_operations, master_registry, transactional_financial, general_tabular).
2. `primary_purpose`: Một câu tiếng Việt nêu rõ mục đích của bảng này.
3. `grain_type` & `grain_description`: MỘT DÒNG đại diện cho cái gì.
4. `dedup_safe`: true nếu hai dòng giống hệt nhau CHẮC CHẮN là lỗi trùng lặp.
5. `target_measures`, `time_axis`, `group_axis`: Các cột đo lường chính, trục thời gian, trục phân nhóm.
6. `predicted_intent` & `recommended_indicators`: 3-4 bài toán cốt lõi và các chỉ số đo lường đặc thù của lĩnh vực đó.
7. `blindspots`: Cảnh báo các chỉ số KHÔNG THỂ TÍNH ĐƯỢC do thiếu cột.
8. `suggestions`: 3-4 câu hỏi phân tích gợi ý thông minh, sát sườn nghiệp vụ.

QUAN TRỌNG: Hãy phân biệt bảng nào là dữ liệu giao dịch/log gốc và bảng nào là danh mục/tổng hợp."""


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
    """{source_id: universal_semantic_dossier} for every sheet."""
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

    keys = {f"sheet_{i}": p for i, p in enumerate(chunk)}
    tasks = {k: _task_text(p, dup_counts.get(p.get("source_id"), 0)) for k, p in keys.items()}

    try:
        results = batch_tasks(_BATCH_CONTEXT, tasks, tier="fast",
                              schemas={k: SEMANTIC_SCHEMA for k in keys})
    except Exception as exc:  # noqa: BLE001
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
    """Delete exact-duplicate rows ONLY when the grain confirms it's safe."""
    if df is None or semantic is None:
        return df, 0
    if not semantic.get("dedup_safe"):
        return df, 0
    if semantic.get("grain_type") not in ("entity", "dimension", "aggregate", "registry"):
        return df, 0

    before = len(df)
    out = df.drop_duplicates().reset_index(drop=True)
    return out, before - len(out)


_ARCHETYPE_ICONS = {
    "time_series_telemetry": "📡 Hạ tầng / Logs / IoT Telemetry",
    "scientific_experimental": "🔬 Khoa học / Y sinh / Thí nghiệm",
    "academic_educational": "🎓 Giáo dục / Bảng điểm / Đào tạo",
    "survey_feedback": "📋 Khảo sát / Đánh giá / Phản hồi",
    "logistics_operations": "🚚 Vận tải / Logistics / Kho bãi",
    "master_registry": "👥 Danh mục Thực thể / Nhân sự / Thiết bị",
    "transactional_financial": "💳 Giao dịch / Bán hàng / Tài chính",
    "general_tabular": "📊 Bảng dữ liệu tổng hợp",
}


def format_semantics_for_prompt(semantics: dict[str, dict] | None) -> str:
    """Format the Universal Data Semantic Dossier for downstream LLM prompts (Chat & Dashboard)."""
    if not semantics:
        return ""
    lines = ["═════════════════════════════════════════════════════════════════════════",
             "🗂️ UNIVERSAL DATA SEMANTIC DOSSIER (HỒ SƠ TRI THỨC DỮ LIỆU - ĐÃ PHÂN TÍCH TOÀN DIỆN):",
             "═════════════════════════════════════════════════════════════════════════"]

    for sid, s in semantics.items():
        archetype_title = _ARCHETYPE_ICONS.get(s.get("archetype"), "📊 Dữ liệu")
        purpose = s.get("primary_purpose") or "Phân tích dữ liệu"
        grain = s.get("grain_description") or s.get("grain_type", "mỗi dòng dữ liệu")

        lines.append(f"\n📁 BẢNG: \"{sid}\" | Hình thái: {archetype_title}")
        lines.append(f"  • Mục đích: {purpose}")
        lines.append(f"  • Hạt dữ liệu (Grain): {grain}")

        if s.get("time_axis"):
            lines.append(f"  • Trục thời gian chính: `{s['time_axis']}`")
        if s.get("group_axis"):
            lines.append(f"  • Trục phân nhóm chính: `{s['group_axis']}`")

        # Target measures
        measures = s.get("target_measures") or []
        if measures:
            m_strs = [f"`{m.get('column')}` ({m.get('concept')}, {m.get('unit', '')} -> {m.get('aggregation_type', 'sum')})" for m in measures[:4]]
            lines.append(f"  • Chỉ số đo lường chính: {', '.join(m_strs)}")
        elif s.get("primary_measure"):
            unit = f" ({s['measure_unit']})" if s.get("measure_unit") else ""
            lines.append(f"  • Chỉ số chính: `{s['primary_measure']}`{unit}")

        # Recommended indicators
        indicators = s.get("recommended_indicators") or []
        if indicators:
            ind_strs = [f"{ind.get('name')} [{ind.get('formula_desc', '')}]" for ind in indicators[:3]]
            lines.append(f"  • Bộ chỉ số khuyến nghị: {'; '.join(ind_strs)}")

        # Blindspots
        blindspots = s.get("blindspots") or []
        if blindspots:
            lines.append("  • ⚠️ ĐIỂM MÙ DỮ LIỆU (TUYỆT ĐỐI KHÔNG TỰ BỊA SỐ NẾU BỊ HỎI CÁC CHỈ SỐ NÀY):")
            for b in blindspots[:3]:
                lines.append(f"    - {b}")

        # Caveats
        for c in (s.get("caveats") or [])[:2]:
            lines.append(f"    - Cảnh báo: {c}")

    lines.append("\nQuy tắc phân tích: Bám sát đúng Hạt dữ liệu (Grain) và Hình thái (Archetype) ở trên. "
                 "LƯU Ý: nếu grain là 'mỗi dòng = 1 mặt hàng trong giao dịch' thì ĐẾM SỐ DÒNG "
                 "KHÔNG phải số đơn hàng — phải đếm số mã giao dịch phân biệt. "
                 "Nếu là bảng log/telemetry thì đo lường theo p95/p99/error rate; nếu là điểm số thì tính trung bình/phổ điểm; "
                 "nếu là giao dịch thì tính tổng/AOV.")
    return "\n".join(lines)
