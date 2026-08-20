"""POST /api/agent/run - runs the agent loop and streams NDJSON events.

Event types sent to the client (one JSON object per line):
  {"type": "step", "message": ...}
  {"type": "need_join_confirm", "proposal": [...], "tables": [...]}
  {"type": "done", "url": ..., "kpis": [...], "charts": [...], "insights": [...]}
  {"type": "error", "message": ...}
"""

import json
import os
import queue
import threading
import traceback
from datetime import datetime

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.agent.code_interpreter import run_code_agent
from app.agent.report import generate_executive_report
from app.ai.pool import AllModelsFailedError, progress_emit
from app.data.trends import format_trend_for_prompt
from app.memory import graph
from app.state import get_state, get_user_id
from app.util_json import ndjson_line

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.post("/run_code")
async def run_code(request: Request, body: dict):
    """Endpoint mới sử dụng Code Interpreter để AI tự viết Python."""
    state = get_state(request)
    user_prompt = body.get("prompt", "")
    
    selected_sources = body.get("selected_sources")
    run_state = state
    if selected_sources is not None:
        run_state = dict(state)
        profiles = state.get("profiles") or []
        run_state["profiles"] = [p for p in profiles if p.get("source_id") in selected_sources]
        dataframes = state.get("dataframes") or {}
        run_state["dataframes"] = {k: v for k, v in dataframes.items() if k in selected_sources}

    if not run_state.get("dataframes") and not user_prompt:
        raise HTTPException(400, "Upload files or provide a prompt to generate data")

    user_id = get_user_id(request)
    fingerprints = run_state.get("file_fingerprints") or {}
    first_fp = next(iter(fingerprints.values()), None)

    async def event_stream():
        q: "queue.Queue" = queue.Queue()
        SENTINEL = object()

        def worker():
            token = progress_emit.set(q.put)
            try:
                # Chạy DataAgent (từ orchestrator) trước để có cleaned_df nếu chưa có
                if not run_state.get("cleaned_df"):
                    from app.agent.sub_agents import run_data_agent
                    for event in run_data_agent(run_state, user_prompt):
                        q.put(event)
                        if event["type"] in ("error", "need_join_confirm"):
                            break
                            
                if run_state.get("cleaned_df") is not None:
                    # Sau khi có df, chuyển sang Code Interpreter
                    for event in run_code_agent(run_state, user_prompt, user_id):
                        q.put(event)
                        
            except Exception as exc:  # noqa: BLE001
                err_msg = f"FAILED_CODE_AGENT: Prompt={user_prompt}\n{traceback.format_exc()}"
                print(err_msg)
                try:
                    os.makedirs("logs", exist_ok=True)
                    with open(f"logs/FAILED_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log", "w", encoding="utf-8") as f:
                        f.write(err_msg)
                except Exception:
                    pass
                q.put({"type": "error", "message": str(exc)})
            finally:
                # Ghi ngược state vào global để các request sau (như /filters) nhận được
                if run_state is not state:
                    if "cleaned_df" in run_state:
                        state["cleaned_df"] = run_state["cleaned_df"]
                    if "cleaned_schema" in run_state:
                        state["cleaned_schema"] = run_state["cleaned_schema"]
                    if "layout_script" in run_state:
                        state["layout_script"] = run_state["layout_script"]
                    if "layout" in run_state:
                        state["layout"] = run_state["layout"]
                    
                progress_emit.reset(token)
                q.put(SENTINEL)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            try:
                event = await anyio.to_thread.run_sync(lambda: q.get(timeout=2.5))
            except queue.Empty:
                yield ndjson_line({"type": "ping"})
                continue
            if event is SENTINEL:
                break
                
            if event["type"] == "finished":
                layout = event.get("layout", {})
                kpis = layout.get("kpis", [])
                charts = layout.get("charts", [])

                # Graph memory: record that a dashboard got built, and save a
                # small reusable blueprint (types/aggregations, NOT computed
                # data) so a similar future upload can recall "you usually
                # build this" (Phase 3 consumes this; write path lands now).
                graph.log_action(
                    user_id, "build_dashboard",
                    {"n_kpis": len(kpis), "n_charts": len(charts), "prompt": user_prompt[:200]},
                    file_fingerprint=first_fp,
                )
                if first_fp:
                    recipe_summary = {
                        "kpis": [{"name": k.get("name") or k.get("title"), "aggregation": k.get("aggregation")} for k in kpis],
                        "charts": [{"title": c.get("title"), "type": c.get("type"), "aggregation": c.get("aggregation")} for c in charts],
                    }
                    graph.save_recipe(user_id, first_fp, user_prompt[:100] or "Dashboard", recipe_summary)

                yield ndjson_line({
                    "type": "done",
                    "url": "", # Trống vì đang xem trên FE, lúc nào cần sẽ tải file sau qua /api/export
                    "kpis": kpis,
                    "charts": charts,
                    "insights": layout.get("insights", []),
                    "suggested_layout": layout.get("suggested_layout"),
                    "suggested_palette": layout.get("suggested_palette"),
                    # The join guard already streams these as `step` messages,
                    # but a step scrolls past in a second. A total that is
                    # silently several times too big has to stay on screen next
                    # to the dashboard it affects.
                    "join_warnings": run_state.get("join_warnings") or [],
                    "non_additive": run_state.get("non_additive_columns") or [],
                })
                continue

            yield ndjson_line(event)

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.get("/filters")
def get_filters(request: Request):
    """Which dimensions this dataset can be sliced by. Derived from the data
    itself (date columns -> time range, low-cardinality text -> dropdowns), so
    the filter bar never offers a control that would return nothing."""
    from app.agent.dashboard_filter import available_filters

    state = get_state(request)
    df = state.get("cleaned_df")
    return {"filters": available_filters(df), "can_filter": state.get("layout_script") is not None}


@router.post("/refilter")
def refilter(request: Request, body: dict):
    """Re-run the stored layout script against a filtered `df`.

    This is the whole interactive-dashboard mechanism: no LLM call, just the
    already-verified pandas re-executing on a subset, so every KPI and chart
    moves together and the numbers cannot drift between refreshes.
    Sync (not async): sandbox execution blocks, so FastAPI runs it in the
    threadpool and keeps the event loop free.
    """
    from app.agent.chart_utils import condense_layout
    from app.agent.dashboard_filter import apply_filters
    from app.agent.sandbox import run_layout_script

    state = get_state(request)
    script = state.get("layout_script")
    df = state.get("cleaned_df")
    if script is None or df is None:
        raise HTTPException(400, "Chưa có dashboard nào để lọc — hãy bấm 'Dashboard tự động' trước.")

    filtered, kept = apply_filters(
        df,
        time_column=body.get("time_column"),
        time_range=body.get("time_range"),
        dimensions=body.get("dimensions") or {},
    )
    if kept == 0:
        raise HTTPException(400, "Bộ lọc này không còn dòng dữ liệu nào.")

    try:
        from app.agent.skills_manager import get_skills_source, load_skills_into_env

        user_id = get_user_id(request)
        skills_env: dict = {}
        load_skills_into_env(skills_env, owner_id=user_id)
        skills_source = get_skills_source(owner_id=user_id)
    except Exception:  # noqa: BLE001 - helpers are optional, plain pandas still runs
        skills_env, skills_source = {}, ""

    run = run_layout_script(script, filtered.copy(), skills_env=skills_env, skills_source=skills_source)
    if not run["ok"]:
        raise HTTPException(400, f"Không chạy lại được tính toán với bộ lọc này: {run['error']}")

    layout = run["layout"]
    if not isinstance(layout, dict):
        raise HTTPException(400, "Kết quả lọc không hợp lệ.")

    # Same normalisation the build path applies, so the filtered layout is
    # exactly as trustworthy/shaped as the original.
    for key in ("kpis", "charts"):
        value = layout.get(key)
        layout[key] = [i for i in value if isinstance(i, dict)] if isinstance(value, list) else []
    condense_layout(layout)
    for k in layout["kpis"]:
        k["status"] = "ok"
    for c in layout["charts"]:
        c["status"] = "ok"

    return {"kpis": layout["kpis"], "charts": layout["charts"], "rows": kept}


@router.post("/report")
def generate_report(request: Request, body: dict | None = None):
    """Executive report for whatever is actually pinned in the Dashboard tab
    right now - passed as `items` in the same shape /api/export already takes
    (the Dashboard tab can be assembled either by the /run_code auto-build flow
    OR by pinning individual chat results one at a time; the latter never
    touches state["layout"], so relying on that alone missed it entirely).
    Falls back to state["layout"] (set by /run_code) only if no items are sent,
    which also keeps the richer AI insights/trend context for that flow.
    Sync (not async): call_ai blocks — FastAPI runs a sync def in the
    threadpool, keeping the event loop free (same reasoning as /api/chat)."""
    state = get_state(request)
    items = (body or {}).get("items") or []

    if items:
        kpis, charts = [], []
        for item in items:
            if item.get("type") == "kpi":
                kpis.append({"title": item.get("title", ""), "value": item.get("scalar")})
            elif item.get("type") == "chart":
                chart = item.get("chart") or {}
                labels = chart.get("labels") or []
                values = chart.get("values") or []
                charts.append({
                    "title": chart.get("title") or item.get("title", ""),
                    "type": chart.get("type", "bar"),
                    "data": [{"label": l, "value": v} for l, v in zip(labels, values)],
                })
        insights: list[str] = []
        trend_context = ""
    else:
        layout = state.get("layout") or {}
        kpis, charts = layout.get("kpis", []), layout.get("charts", [])
        insights = layout.get("insights", [])
        trend_context = format_trend_for_prompt(state.get("trend_signals"))

    if not kpis and not charts:
        raise HTTPException(400, "Chưa có dashboard nào được dựng trong phiên này.")

    try:
        report = generate_executive_report(
            kpis, charts, insights,
            state.get("last_user_prompt", ""),
            trend_context=trend_context,
        )
    except AllModelsFailedError as exc:
        raise HTTPException(502, f"AI không tạo được báo cáo: {exc}")

    user_id = get_user_id(request)
    fingerprints = state.get("file_fingerprints") or {}
    graph.log_action(
        user_id, "generate_report", {"n_kpis": len(kpis), "n_charts": len(charts)},
        file_fingerprint=next(iter(fingerprints.values()), None),
    )

    # Persist as a session artifact so "mở lại báo cáo hồi nãy" and file
    # export keep working after the card is closed (or the page reloaded).
    import time as _time
    import uuid as _uuid
    saved = list(state.get("reports") or [])
    entry = {
        "id": str(_uuid.uuid4()),
        "title": (state.get("last_user_prompt") or "").strip()[:80]
                 or f"Báo cáo {_time.strftime('%d/%m %H:%M')}",
        "created_at": _time.time(),
        "report": report,
    }
    saved.append(entry)
    state["reports"] = saved[-20:]  # keep the session artifact list bounded

    return {**report, "id": entry["id"]}


@router.get("/reports")
def list_reports(request: Request):
    """Saved report artifacts of this session, newest first (full content
    included - reports are small text)."""
    state = get_state(request)
    return {"reports": list(reversed(state.get("reports") or []))}


@router.get("/reports/{report_id}/download")
def download_report_docx(request: Request, report_id: str):
    """Render one saved report as a Word (.docx) file with embedded charts."""
    state = get_state(request)
    entry = next((r for r in (state.get("reports") or []) if r["id"] == report_id), None)
    if entry is None:
        raise HTTPException(404, "Báo cáo không tồn tại trong phiên này.")

    from app.agent.exporter import export_report_to_docx

    report = entry["report"]
    # Charts live under the session layout ("layout" -> "charts"), which is
    # where tools.py writes them. Reading a top-level state["charts"] found
    # nothing and silently produced chart-less documents.
    charts = (state.get("layout") or {}).get("charts") or []
    out = export_report_to_docx(
        title=entry.get("title", "Báo Cáo Điều Hành"),
        report=report,
        charts=charts,
        created_at=entry.get("created_at"),
    )
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename=BaoCao_{report_id[:8]}.docx"},
    )


@router.get("/reports/{report_id}/download/pptx")
def download_report_pptx(request: Request, report_id: str):
    """Render one saved report as an Executive PowerPoint (.pptx) Presentation."""
    state = get_state(request)
    entry = next((r for r in (state.get("reports") or []) if r["id"] == report_id), None)
    if entry is None:
        raise HTTPException(404, "Báo cáo không tồn tại trong phiên này.")

    from app.agent.exporter import export_report_to_pptx

    report = entry["report"]
    # Charts live under the session layout ("layout" -> "charts"), which is
    # where tools.py writes them. Reading a top-level state["charts"] found
    # nothing and silently produced chart-less documents.
    charts = (state.get("layout") or {}).get("charts") or []
    out = export_report_to_pptx(
        title=entry.get("title", "Báo Cáo Điều Hành"),
        report=report,
        charts=charts,
        created_at=entry.get("created_at"),
    )
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f"attachment; filename=SlideBaoCao_{report_id[:8]}.pptx"},
    )


@router.get("/reports/{report_id}/download/xlsx")
def download_report_xlsx(request: Request, report_id: str):
    """Render one saved report as an Excel (.xlsx) workbook with charts and formatted data sheets."""
    state = get_state(request)
    entry = next((r for r in (state.get("reports") or []) if r["id"] == report_id), None)
    if entry is None:
        raise HTTPException(404, "Báo cáo không tồn tại trong phiên này.")

    from app.agent.exporter import export_data_and_charts_to_xlsx

    report = entry["report"]
    # Charts live under the session layout ("layout" -> "charts"), which is
    # where tools.py writes them. Reading a top-level state["charts"] found
    # nothing and silently produced chart-less documents.
    charts = (state.get("layout") or {}).get("charts") or []
    dfs = state.get("dataframes") or {}
    summary = report.get("executive_summary", "")

    out = export_data_and_charts_to_xlsx(
        title=entry.get("title", "Báo Cáo Điều Hành"),
        dataframes=dfs,
        charts=charts,
        summary=summary,
    )
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=BaoCaoExcel_{report_id[:8]}.xlsx"},
    )



# ── Memory Management Endpoints ───────────────────────────────────────────

@router.get("/memory/all")
def get_user_memory(request: Request):
    """Retrieve full personal knowledge & memory profile for this user."""
    user_id = get_user_id(request)
    memories = graph.get_all_user_memories(user_id)
    return {"ok": True, "memories": memories}


@router.post("/memory/delete")
def delete_single_memory(request: Request, body: dict):
    """Delete a specific memory node by ID."""
    user_id = get_user_id(request)
    memory_id = body.get("memory_id")
    if not memory_id:
        raise HTTPException(400, "Thiếu memory_id.")
    success = graph.delete_memory_by_id(user_id, memory_id)
    return {"ok": success}


@router.post("/memory/forget")
def forget_memory_text(request: Request, body: dict):
    """Natural language memory erasure matching keywords."""
    user_id = get_user_id(request)
    query = body.get("query", "")
    deleted = graph.forget_memory_by_text(user_id, query)
    return {"ok": True, "deleted_items": deleted}


@router.post("/memory/clear")
def clear_all_memory(request: Request):
    """Wipe all personal memories for this user."""
    user_id = get_user_id(request)
    deleted_count = graph.delete_all_user_memories(user_id)
    return {"ok": True, "deleted_count": deleted_count}


# ── Spreadsheet Copilot Endpoint (Sandbox Verified) ─────────────────────────

@router.post("/sheet/copilot")
def mutate_sheet_via_copilot(request: Request, body: dict):
    """Execute AI Spreadsheet Copilot mutation with pre-flight Sandbox validation."""
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(400, "Thiếu prompt.")
    state = get_state(request)
    dfs = state.get("dataframes") or {}
    profiles = state.get("profiles") or []
    
    from app.agent.chat_agent import _schema_text
    from app.agent.sheet_copilot import apply_sheet_copilot_mutation
    
    schema_context = _schema_text(profiles, dfs)
    result = apply_sheet_copilot_mutation(
        user_prompt=prompt,
        dataframes=dfs,
        schema_context=schema_context,
        sheet_id=body.get("sheet_id"),
    )
    return result

