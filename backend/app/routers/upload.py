from fastapi import APIRouter, HTTPException, Request, UploadFile

from app.agent.chat_agent import summarize_upload
from app.data.profiling import profile_file, read_raw_grids, read_single_sheet_raw, profile_dataframe
from app.data.profiler import clean_and_profile
from app.data.smart_read import read_grid_with_header
from app.data.llm_header import llm_detect_header, llm_detect_headers
from app.data.semantics import analyze_all, apply_grain_dedup, _ARCHETYPE_ICONS
from app.memory import graph
from app.sheets.xlsx_export import build_dashboard_xlsx
from app.state import get_state, get_user_id
from app.util_json import ndjson_line

import json
import queue
import threading
import anyio
from fastapi.responses import StreamingResponse
from app.ai.pool import progress_emit

router = APIRouter(prefix="/api", tags=["upload"])


@router.post("/upload")
async def upload(request: Request, files: list[UploadFile]):
    if not files:
        raise HTTPException(400, "No files uploaded")

    is_sample = (
        request.query_params.get("sample") in ("1", "true", "True")
        or any("Doanh_Thu_Ban_Hang_Mau" in (f.filename or "") or "sample" in (f.filename or "").lower() for f in files)
    )

    state = get_state(request) # Lấy state ở luồng chính để set cookie kịp thời!
    # Resolve identity on the request thread (chat mode has no login, so this
    # falls back to a session-derived anonymous id — graph memory works either way).
    user_id = get_user_id(request)
    graph.merge_user(user_id, email=request.session.get("email"))

    async def event_stream():
        q = queue.Queue()
        SENTINEL = object()

        # The four phases the UI shows as a progress rail. Named here rather than
        # inferred in the frontend: guessing the phase by pattern-matching the
        # message text would break the moment someone rewords a message.
        stage = {"i": 0}

        def emit(event: dict) -> None:
            """Stamp the current phase on everything, including the events the
            AI pool raises from deep inside a call."""
            if event.get("type") == "step":
                event.setdefault("stage", stage["i"])
            q.put(event)

        def step(message: str, **extra) -> None:
            emit({"type": "step", "message": message, **extra})

        def enter(i: int) -> None:
            stage["i"] = i

        def worker():
            token = progress_emit.set(emit)
            try:
                dataframes = {}
                profiles = []
                raw_grids = {}
                raw_files = {}

                filenames = ", ".join(f.filename for f in files)
                step(f"📄 Đang đọc file {filenames}...")

                for f in files:
                    content = f.file.read()
                    raw_files[f.filename] = content
                    file_profiles, file_dataframes = profile_file(f.filename, content)
                    profiles.extend(file_profiles)
                    dataframes.update(file_dataframes)
                    # Faithful raw view for display (unmodified cells), separate from the
                    # cleaned dataframes used for analysis.
                    file_grids = read_raw_grids(f.filename, content)
                    raw_grids.update(file_grids)

                    # Hybrid step v2: EVERY sheet gets an LLM structure check —
                    # batched into ONE call, because each sheet only needs a
                    # one-line answer and N separate calls spent N round-trips
                    # (and N rate-limit slots) to move very little information.
                    # Reconciliation:
                    #   - LLM agrees            -> keep, mark llm_confirmed
                    #   - LLM differs, heuristic was unsure -> re-parse with LLM's row
                    #   - LLM differs, heuristic was confident -> KEEP heuristic
                    #     (never degrade an already-correct parse) but flag
                    #     low_confidence so the review banner invites the user to
                    #     check, with the LLM's suggestion attached.
                    # LLM header confirmation ONLY for low-confidence sheets (weird layouts).
                    # Clean/standard sheets skip this entirely for instant 0ms parsing.
                    checkable = [
                        p for p in file_profiles
                        if (p.get("detection") or {}).get("low_confidence")
                        and (file_grids.get(p["source_id"]) or {}).get("grid")
                    ]
                    if is_sample:
                        for p in checkable:
                            det = p.get("detection") or {}
                            det["llm_confirmed"] = True
                            det["low_confidence"] = False
                            p["detection"] = det
                    else:
                        llm_rows = []
                        if checkable:
                            step(f"🤖 AI đang xác nhận cấu trúc {len(checkable)} sheet (gộp 1 lượt)...")
                            header_map = llm_detect_headers({
                                p["source_id"]: (file_grids.get(p["source_id"]) or {}).get("grid") or []
                                for p in checkable
                            })
                            llm_rows = [header_map.get(p["source_id"]) for p in checkable]

                        for p, llm_row in zip(checkable, llm_rows):
                            det = p.get("detection") or {}
                            if llm_row is None:
                                continue
                            if llm_row == det.get("header_row"):
                                det["llm_confirmed"] = True
                                det["low_confidence"] = False
                                p["detection"] = det
                                continue
                            if det.get("low_confidence"):
                                try:
                                    raw_df = read_single_sheet_raw(f.filename, content, p["sheet"])
                                    new_df = read_grid_with_header(raw_df, llm_row)
                                    cleaned, prof = clean_and_profile(new_df)
                                    prof["detection"] = {"header_row": llm_row, "confidence": 0.9,
                                                         "totals_dropped": 0, "low_confidence": False, "llm": True}
                                    new_p = profile_dataframe(f.filename, p["sheet"], cleaned, prof)
                                    # Swap into the batch collections in place.
                                    profiles[profiles.index(p)] = new_p
                                    dataframes[p["source_id"]] = cleaned
                                except Exception:
                                    pass  # keep the heuristic result
                            else:
                                det["low_confidence"] = True
                                det["llm_suggested_row"] = llm_row
                                p["detection"] = det
                            step(f"⚠️ Sheet '{p['sheet']}': heuristic chọn dòng {det.get('header_row', 0) + 1}, AI đề xuất dòng {llm_row + 1} — hãy kiểm tra banner tiêu đề.")

                # Phase 2: the file is parsed; from here on we are working out
                # what it MEANS — headers, entities, grain.
                enter(1)

                # Entity resolution FIRST — before semantics, before observations,
                # before anything that groups. While "Miền Bắc"/"MIỀN BẮC"/"miền
                # bắc" are three values, every share, ranking and total computed
                # afterwards is wrong; fixing it later corrects the labels but
                # not the numbers that were already derived from them.
                from app.data.entities import find_placeholders, resolve_entities

                entity_notes: dict[str, list[str]] = {}
                for sid, sheet_df in list(dataframes.items()):
                    fixed, notes = resolve_entities(sheet_df)
                    if notes:
                        dataframes[sid] = fixed
                    notes = notes + find_placeholders(dataframes[sid])
                    if notes:
                        entity_notes[sid] = notes
                        for p in profiles:
                            if p["source_id"] == sid:
                                p["flags"] = (p.get("flags") or []) + notes

                if is_sample:
                    semantics = {
                        p["source_id"]: {
                            "grain": "transaction",
                            "subject": "Doanh thu bán hàng chuỗi cửa hàng",
                            "currency": "VND",
                        }
                        for p in profiles
                    }
                else:
                    step(f"🔬 AI đang tìm hiểu bản chất {len(profiles)} bảng dữ liệu...")
                    dup_counts = {p["source_id"]: p.get("duplicate_rows_found", 0) for p in profiles}
                    semantics = analyze_all(profiles, dup_counts)

                # Now that the grain is known, exact-duplicate rows can be
                # removed where they cannot possibly be legitimate - and only
                # there. clean_and_profile deliberately kept them all.
                for p in profiles:
                    sid = p["source_id"]
                    sem = semantics.get(sid)
                    if not sem or sid not in dataframes:
                        continue
                    p["semantics"] = sem
                    deduped, removed = apply_grain_dedup(dataframes[sid], sem)
                    if removed:
                        dataframes[sid] = deduped
                        p["row_count"] = len(deduped)
                        p["flags"] = [f for f in (p.get("flags") or []) if "dòng trùng hoàn toàn" not in f]
                        p["flags"].append(
                            f"Đã bỏ {removed:,} dòng trùng hoàn toàn (bảng danh mục — mỗi dòng phải là duy nhất)."
                        )
                        step(f"🧹 Sheet '{p['sheet']}': bỏ {removed:,} dòng trùng (bảng danh mục).")

                # Append to the session instead of replacing it: uploading more
                # files later must not wipe what's already loaded. Re-uploading
                # a file with the SAME name replaces just that file's sheets.
                new_filenames = set(raw_files.keys())
                prev_profiles = [p for p in (state.get("profiles") or []) if p.get("filename") not in new_filenames]
                prev_dfs = {k: v for k, v in (state.get("dataframes") or {}).items() if k.split("::", 1)[0] not in new_filenames}
                prev_grids = {k: v for k, v in (state.get("raw_grids") or {}).items() if k.split("::", 1)[0] not in new_filenames}
                prev_raw = {k: v for k, v in (state.get("raw_files") or {}).items() if k not in new_filenames}
                prev_fps = {k: v for k, v in (state.get("file_fingerprints") or {}).items() if k.split("::", 1)[0] not in new_filenames}

                # Observed facts (concentration, unusual periods, gaps, data
                # quality) computed in pandas right after the grain is known.
                # No LLM: free, instant, and impossible to hallucinate - so it
                # can be fed to every later prompt as ground truth.
                from app.data.eda import profile_facts

                eda_facts = {
                    sid: profile_facts(dataframes.get(sid), semantics.get(sid))
                    for sid in dataframes
                }
                eda_facts = {k: v for k, v in eda_facts.items() if v}

                # Relationships between columns and between tables. Fully
                # deterministic, and the only part of the layer that looks at
                # more than one column at a time — which is where "Thành tiền =
                # SL × Đơn giá" and the fact→dimension links live.
                from app.data.relations import detect_formulas, detect_keys

                formulas = {sid: detect_formulas(d) for sid, d in dataframes.items()}
                formulas = {k: v for k, v in formulas.items() if v}
                fk_links = detect_keys(dataframes)

                # Shape-driven analysis: whichever tests the column topology
                # supports, with multiplicity control and the formula pairs
                # excluded so arithmetic is never reported as a discovery.
                from app.data.dispatcher import analyze as analyze_shape

                prof_by_sid = {p["source_id"]: p for p in profiles}
                signals = {
                    sid: analyze_shape(d, prof_by_sid.get(sid), formulas.get(sid))
                    for sid, d in dataframes.items()
                }
                signals = {k: v for k, v in signals.items() if v}
                if signals:
                    step(f"📊 {sum(len(v) for v in signals.values())} tín hiệu thống kê đáng chú ý.")

                if formulas or fk_links:
                    step(f"🔗 Tìm thấy {sum(len(v) for v in formulas.values())} công thức giữa cột "
                                      f"và {len(fk_links)} liên kết giữa bảng.")

                prev_sem = {k: v for k, v in (state.get("semantics") or {}).items() if k.split("::", 1)[0] not in new_filenames}
                prev_eda = {k: v for k, v in (state.get("eda_facts") or {}).items() if k.split("::", 1)[0] not in new_filenames}

                state["dataframes"] = {**prev_dfs, **dataframes}
                state["profiles"] = prev_profiles + profiles
                state["raw_grids"] = {**prev_grids, **raw_grids}
                state["raw_files"] = {**prev_raw, **raw_files}
                # Every downstream prompt reads this one shared understanding:
                # what the data IS (semantics) and what it SAYS (eda_facts).
                state["semantics"] = {**prev_sem, **semantics}
                state["eda_facts"] = {**prev_eda, **eda_facts}
                state["formulas"] = {**{k: v for k, v in (state.get("formulas") or {}).items()
                                        if k.split("::", 1)[0] not in new_filenames}, **formulas}
                # Keys span sheets, so they are recomputed wholesale rather than merged.
                state["fk_links"] = fk_links
                state["signals"] = {**{k: v for k, v in (state.get("signals") or {}).items()
                                       if k.split("::", 1)[0] not in new_filenames}, **signals}
                # A previously merged/joined view is stale once new data lands.
                state.pop("cleaned_df", None)

                # Graph memory: fingerprint each sheet's column shape so a
                # structurally-identical upload (same columns+roles, any
                # filename/user) is recognized later — the basis for recipe
                # recall (Phase 3). No-ops silently if Neo4j is unavailable.
                state["file_fingerprints"] = {
                    **prev_fps,
                    **{p["source_id"]: graph.upsert_file(user_id, p) for p in profiles},
                }

                # Lazy per-sheet loading: with many sheets (and/or big sheets) we must NOT
                # ship every grid at once. Send the grid only for the initial sheet (the one
                # with the most rows = most likely the real data, not a cover/guide sheet);
                # every other sheet's grid is fetched on demand via POST /api/sheet when its
                # tab is opened. `has_data` lets the UI de-emphasize near-empty sheets.
                initial_sid = max(raw_grids, key=lambda s: len(raw_grids[s]["grid"]), default=None)
                for p in profiles:
                    raw = raw_grids.get(p["source_id"])
                    raw_len = len(raw["grid"]) if raw else 0
                    # Initial payload gets a compact preview (500 rows); full grid fetched on demand via /api/sheet
                    p["grid"] = (raw["grid"][:500] if raw and raw.get("grid") else None) if (raw and p["source_id"] == initial_sid) else None
                    # Use actual row_count from profiling for accurate count display
                    p["grid_rows"] = p.get("row_count", raw_len)
                    p["has_data"] = p["grid_rows"] > 0

                # Phase 3: structure settled, now measuring and interpreting.
                enter(2)

                n_sheets = len(profiles)
                n_rows = sum(p.get("row_count", 0) for p in profiles)
                step(f"📊 Đã nạp {n_sheets} sheet · {n_rows:,} dòng dữ liệu.")

                if is_sample:
                    insights = {
                        "summary": f"Đã nạp thành công bộ dữ liệu mẫu ({n_sheets} sheet, {n_rows:,} dòng). Bạn có thể đặt câu hỏi phân tích hoặc bấm nút 'Tạo Dashboard'.",
                        "suggestions": [
                            "Tổng quan các chỉ số đo lường chính?",
                            "Xu hướng biến động theo thời gian?",
                            "Top các nhóm/thực thể hàng đầu?",
                        ],
                    }
                    discoveries = []
                    enter(3)
                else:
                    step("🧠 Đang tổng hợp Hồ sơ Tri thức Dữ liệu (Semantic Dossier)...")
                    # Build rich persona insights directly from the Universal Semantic Dossier
                    main_sem = semantics.get(initial_sid) or (next(iter(semantics.values()), None) if semantics else {})
                    if main_sem:
                        archetype_name = _ARCHETYPE_ICONS.get(main_sem.get("archetype"), "📊 Bảng dữ liệu")
                        purpose = main_sem.get("primary_purpose") or "Phân tích và trực quan hóa dữ liệu"
                        grain = main_sem.get("grain_description") or main_sem.get("grain_type", "dữ liệu chi tiết")
                        blindspots = main_sem.get("blindspots") or []
                        suggestions = main_sem.get("suggestions") or []

                        summary_lines = [
                            f"👋 **Tôi đã đọc và hiểu toàn bộ dữ liệu** ({n_sheets} sheet · {n_rows:,} dòng).",
                            f"📁 **Hình thái dữ liệu**: {archetype_name}",
                            f"🎯 **Mục đích**: {purpose}.",
                            f"🔍 **Hạt dữ liệu (Grain)**: {grain}."
                        ]
                        if blindspots:
                            summary_lines.append(f"⚠️ **Điểm mù dữ liệu**: {blindspots[0]}")
                        summary_lines.append("\n👉 Bạn có thể đặt câu hỏi bất kỳ ở khung chat bên dưới, hoặc bấm nút **Tạo Dashboard** khi cần nhé!")

                        insights = {
                            "summary": "\n".join(summary_lines),
                            "suggestions": suggestions[:4] if suggestions else [
                                "Thống kê tổng quan các chỉ số đo lường?",
                                "Xu hướng biến động theo thời gian?",
                                "Xếp hạng các thực thể/phân nhóm hàng đầu?",
                            ],
                            "dossier": main_sem,
                        }
                    else:
                        insights = summarize_upload(profiles, dataframes) or {
                            "summary": f"Đã nạp thành công {n_sheets} sheet ({n_rows:,} dòng).",
                            "suggestions": ["Thống kê tổng quan dữ liệu?", "Phân tích theo nhóm?"]
                        }

                    # Phase 4: assembly complete
                    enter(3)

                    # Proactive stage: goal explorer
                    from app.agent.goal_explorer import explore

                    try:
                        discoveries = explore(state, dataframes, emit=q.put)
                    except Exception as exc:  # noqa: BLE001 - upload must still succeed
                        print(f"[upload] goal explorer skipped: {exc}")
                        discoveries = []
                    state["discoveries"] = discoveries

                # The "active" sheet's fingerprint is what the chat mostly
                # operates on, so memory recall keys off it specifically
                # (falls back to the first sheet's fingerprint if unset).
                active_fp = state["file_fingerprints"].get(initial_sid) or next(
                    iter(state["file_fingerprints"].values()), None
                )
                
                # Proactive Recipe Recall: Check if user built a dashboard on this exact structure before
                if not is_sample and active_fp:
                    try:
                        # graph.query_recipes_by_fingerprint never existed, so every
                        # upload raised AttributeError into the except below and the
                        # recall message was never once shown. The real lookup is
                        # find_matching_recipe, which returns one recipe whose counts
                        # live under the parsed layout, not at the top level.
                        recipe = graph.find_matching_recipe(user_id, active_fp)
                        if recipe:
                            layout = recipe.get("layout_obj") or {}
                            kpi_count = len(layout.get("kpis") or [])
                            chart_count = len(layout.get("charts") or [])
                            step(f"💡 Nhận diện cấu trúc quen thuộc! Bạn từng xây Dashboard ({kpi_count} KPI, {chart_count} biểu đồ) với cấu trúc dữ liệu này.")
                    except Exception as exc:
                        print(f"[upload] recipe recall failed: {exc}")

                graph.log_action(
                    user_id, "upload",
                    {"n_sheets": n_sheets, "n_rows": n_rows, "filenames": filenames},
                    file_fingerprint=active_fp,
                )

                q.put({
                    "type": "done",
                    # Full merged session view (old + new files), not just this batch.
                    "files": state.get("profiles") or profiles,
                    "active": initial_sid,
                    "insights": insights,
                    "discoveries": discoveries,
                })
            except Exception as exc:
                q.put({"type": "error", "message": f"Lỗi upload: {exc}"})
            finally:
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
            yield ndjson_line(event)

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.post("/sheet")
async def sheet(request: Request, body: dict):
    """Return one sheet's grid on demand (lazy per-sheet loading).

    A sheet the copilot has edited is served from derived_grids instead of
    raw_grids, so the computed column is still there after a refresh. The
    `derived` flag tells the frontend which it got: a derived grid is built from
    the cleaned dataframe and always has its header on row 0, while a raw grid
    keeps whatever preamble the file had above its header.
    """
    state = get_state(request)
    sid = body.get("source_id")

    derived = (state.get("derived_grids") or {}).get(sid)
    if derived is not None:
        return {"source_id": sid, "grid": derived["grid"], "derived": True}

    grid = (state.get("raw_grids") or {}).get(sid)
    if grid is None:
        raise HTTPException(404, "Sheet không tồn tại - hãy upload lại file.")
    return {"source_id": sid, "grid": grid["grid"], "derived": False}


def _to_native(value):
    return value.item() if hasattr(value, "item") else value


@router.post("/export")
def export_dashboard(request: Request, body: dict):
    """Export the Dashboard as a native .xlsx (KPI cards + real Excel chart
    objects, mapped per-type and palette-matched — see app/sheets/xlsx_export.py).

    Only the pinned dashboard items are written, never the uploaded workbook —
    earlier versions copied it wholesale and a 250k-row sheet took minutes,
    blocked the event loop, and blew past the Cloudflare tunnel's ~100s timeout
    ("Failed to fetch"). Sync (not async) so the openpyxl work runs in
    FastAPI's threadpool."""
    items = body.get("items") or []
    palette = body.get("palette") or "emerald"

    out = build_dashboard_xlsx(items, palette=palette)

    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=Dashboard_Analytic.xlsx"}
    )


# Session keys holding one entry per sheet, keyed by source_id ("file.xlsx::Sheet1").
# Every one of these is rendered into the shared understanding block that goes
# into EVERY prompt, so an entry left behind after a delete does not sit idle —
# it actively tells the model about grain, formulas and statistical signals for
# a sheet the user can no longer see.
_SHEET_KEYED_STATE = ("semantics", "eda_facts", "formulas", "signals",
                      "raw_grids", "derived_grids", "raw_files", "file_fingerprints")

# Derived from ALL sheets together, so removing any one of them invalidates the
# whole thing rather than part of it.
_CROSS_SHEET_STATE = ("fk_links", "cleaned_df", "cleaned_schema", "confirmed_joins",
                      "join_warnings", "non_additive_columns", "dashboard_items",
                      "discoveries")


@router.delete("/files/{filename:path}")
def delete_file(request: Request, filename: str):
    """Xóa một file đã upload khỏi phiên làm việc hiện tại.

    Removing the dataframes is not enough. The upload pipeline derives a whole
    layer of understanding from each sheet — what one row means, which columns
    are computed from which, which statistical signals are real — and all of it
    is injected into every prompt. Left behind, it describes data that is gone:
    the model keeps citing a deleted sheet's totals and no code path contradicts
    it, because nothing is technically broken.

    The cross-sheet derivations (the merged frame, the foreign keys, the join
    warnings) are dropped outright rather than filtered: each was computed from
    the full set of sheets, so with one removed they are not partially valid,
    they are simply stale. The next dashboard build recomputes them.
    """
    state = get_state(request)

    def _belongs(key: str) -> bool:
        return key.split("::")[0] == filename

    profiles = state.get("profiles") or []
    new_profiles = [p for p in profiles
                    if p.get("filename") != filename
                    and p.get("source_id", "").split("::")[0] != filename]
    state["profiles"] = new_profiles

    dataframes = state.get("dataframes") or {}
    state["dataframes"] = {k: v for k, v in dataframes.items() if not _belongs(k)}

    for key in _SHEET_KEYED_STATE:
        current = state.get(key)
        if isinstance(current, dict):
            state[key] = {k: v for k, v in current.items() if not _belongs(k)}

    for key in _CROSS_SHEET_STATE:
        state.pop(key, None)

    remaining = len(set(p.get("filename") or p.get("source_id", "").split("::")[0]
                        for p in new_profiles))
    return {"status": "ok", "remaining_files": remaining}
