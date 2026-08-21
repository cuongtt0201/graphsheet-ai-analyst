"""Chat-over-your-file endpoints (the new upload -> ask flow).

Reuses the existing per-session state (dataframes + profiles populated by
POST /api/upload) - no Google login required for this flow.

  GET  /api/tables  -> schema of every uploaded table (for the grid to render)
  POST /api/chat    -> {message, history?} -> grounded answer (+code/table/chart)
"""

import queue
import threading

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.agent.chat_agent import answer_question, build_workspace_context
from app.ai.pool import progress_emit
from app.memory import graph
from app.state import get_state, get_user_id
from app.util_json import ndjson_line

router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/tables")
async def tables(request: Request):
    """Schema + preview rows of every uploaded table, so the frontend grid can
    show the data before the user asks anything."""
    state = get_state(request)
    profiles = state.get("profiles") or []
    return {"tables": profiles}


@router.get("/dashboard/items")
async def get_dashboard_items(request: Request):
    """Whatever is currently pinned to the Dashboard tab, persisted server-side
    so a page refresh doesn't lose KPIs/charts pinned one at a time from chat
    (only the auto-build flow's result used to survive a reload)."""
    state = get_state(request)
    return {"items": state.get("dashboard_items") or []}


@router.put("/dashboard/items")
async def save_dashboard_items(request: Request, body: dict):
    items = body.get("items")
    if not isinstance(items, list):
        raise HTTPException(400, "Thiếu 'items' (danh sách).")
    state = get_state(request)
    state["dashboard_items"] = items  # LazySessionState persists JSON-able values to disk on set
    return {"ok": True, "count": len(items)}


@router.post("/chat")
async def chat(request: Request, body: dict):
    """Streams NDJSON progress events, then a final "done" event carrying the
    reply — same wire shape /api/upload and /api/agent/run_code already use.

    A chat turn is a 3-stage pipeline (decide mode -> run pandas in the sandbox
    -> interpret the result) that can take ~10s. As a plain JSON POST the user
    saw nothing but a spinner for all of it, while the pool was already
    emitting "trying model X" / thought-summary lines that had nowhere to go.

    Validation stays on the request thread and still raises HTTPException:
    nothing has been streamed yet at that point, so the client gets a real
    non-200. Anything discovered later in the worker becomes an "error" event.
    """
    state = get_state(request)
    dataframes = state.get("dataframes")
    profiles = state.get("profiles")
    
    selected_sources = body.get("selected_sources")
    if selected_sources is not None:
        profiles = [p for p in (profiles or []) if p.get("source_id") in selected_sources]
        dataframes = {k: v for k, v in (dataframes or {}).items() if k in selected_sources}

    message = (body.get("message") or "").strip()
    msg_lower = message.lower()

    # Dataset generation with data ALREADY loaded needs explicit phrasing
    # ("tạo dữ liệu...", "sinh bộ dữ liệu..."), so ordinary analysis questions
    # containing "tạo" (e.g. "tạo biểu đồ") don't get hijacked into datagen.
    _EXPLICIT_GEN_PHRASES = (
        "tạo dữ liệu", "sinh dữ liệu", "tạo bộ dữ liệu", "sinh bộ dữ liệu",
        "tạo bảng dữ liệu", "tạo dataset", "tạo thêm dữ liệu", "dữ liệu giả lập",
        "dữ liệu mẫu", "mock data", "generate data", "cào dữ liệu",
        "thu thập dữ liệu", "crawl",
    )

    # Decide upfront (pure string matching, no LLM) so every rejection is a
    # real HTTP error rather than an error event inside a 200 stream.
    needs_datagen = False
    if not dataframes or not profiles:
        if not message:
            raise HTTPException(400, "Chưa có dữ liệu và yêu cầu trống.")
        # Cold start: looser keyword match is fine, there is nothing to hijack.
        if any(x in msg_lower for x in ("tạo", "sinh", "create", "mock", "giả lập", "sample", "dữ liệu mẫu")):
            needs_datagen = True
        else:
            raise HTTPException(
                400,
                "Chưa có dữ liệu - hãy upload file trước hoặc yêu cầu AI tạo dữ liệu giả lập (ví dụ: 'Tạo dữ liệu doanh thu...')."
            )
    elif any(x in msg_lower for x in _EXPLICIT_GEN_PHRASES):
        needs_datagen = True

    if not message:
        raise HTTPException(400, "Câu hỏi trống.")

    history = body.get("history") or []
    user_id = get_user_id(request)

    async def event_stream():
        q = queue.Queue()
        SENTINEL = object()

        def worker():
            nonlocal dataframes, profiles
            # Everything the pool emits during a blocking call_ai (which model
            # it is trying, live thought summaries) lands in this queue.
            token = progress_emit.set(q.put)
            generated_new_data = False
            try:
                if needs_datagen:
                    q.put({"type": "step", "message": "🧪 Đang sinh bộ dữ liệu mới..."})
                    from app.agent.sub_agents import run_datagen_agent
                    for event in run_datagen_agent(state, message):
                        if event["type"] == "error":
                            q.put({"type": "error", "message": f"Lỗi sinh dữ liệu: {event['message']}"})
                            return
                    # Re-read the FULL (unfiltered) session state: new sheets are
                    # not in selected_sources yet, so the filtered view would hide them.
                    dataframes = state.get("dataframes")
                    profiles = state.get("profiles")
                    generated_new_data = True

                # Long-term memory: this user's distilled habits/preferences (built
                # by the idle-time distiller). The model self-selects which notes
                # apply and reports them back in used_memory_ids for the loop below.
                behaviors = graph.get_behaviors(user_id)

                # Chat = control panel: every turn sees the dashboard the user is
                # looking at (auto-built layout with insights, or manually pinned
                # items) so follow-ups land on those exact computed numbers.
                workspace_block = build_workspace_context(
                    state.get("layout"), state.get("dashboard_items")
                )

                reply = answer_question(profiles, dataframes, message, history,
                                        behaviors=behaviors, user_id=user_id,
                                        workspace_block=workspace_block,
                                        semantics=state.get("semantics"),
                                        eda_facts=state.get("eda_facts"))

                # Feedback loop: bump usage/success on the memories the model relied
                # on (an errored reply counts against them; wrong ones get deleted).
                valid_ids = {b["id"] for b in behaviors}
                used_ids = [i for i in (reply.pop("used_memory_ids", None) or []) if i in valid_ids]
                if used_ids:
                    graph.record_behavior_usage(used_ids, success=not reply.get("error"))

                # Habit tracking: what kind of question, and did it land.
                # Personal history only - no cross-user recall.
                fingerprints = state.get("file_fingerprints") or {}
                first_fp = next(iter(fingerprints.values()), None)
                graph.log_action(
                    user_id, "chat_question",
                    {"message": message[:200], "has_chart": bool(reply.get("chart")),
                     "has_table": bool(reply.get("table")), "error": bool(reply.get("error"))},
                    file_fingerprint=first_fp,
                )

                # Autonomous Memory Harvester (Agent Tự Học ngầm vào Neo4j)
                from app.memory.learner import harvest_memory_async
                harvest_memory_async(user_id, message, reply.get("text", ""))

                # follow_up comes from answer_question() - the AI's own read of this
                # file/answer. Just drop ones already asked in this conversation.
                # Exception: on a clarify turn these are ANSWER options, not
                # follow-up questions - filtering them as "already asked" would
                # silently delete valid choices and leave a question with none.
                if not reply.get("clarify"):
                    already_asked = {message.strip().lower()} | {
                        h.get("content", "").strip().lower() for h in history if h.get("role") == "user"
                    }
                    reply["follow_up"] = [
                        qq for qq in (reply.get("follow_up") or [])
                        if qq.strip().lower() not in already_asked
                    ][:3]

                # Tells the frontend to re-fetch /api/tables (new sheets exist).
                reply["generated"] = generated_new_data
                q.put({"type": "done", **reply})
            except Exception as exc:  # noqa: BLE001 - surfaced as an error event
                q.put({"type": "error", "message": str(exc)})
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


@router.get("/diagnostics/memory")
async def diagnostics_memory(request: Request):
    from app.state import get_user_id
    from app.memory.graph import _run, ENABLED
    
    if not ENABLED:
        return {
            "enabled": False,
            "nodes": [],
            "edges": [],
            "skills": [],
            "behaviors": []
        }
        
    user_id = get_user_id(request)

    # Query the user's own identity (set at login) for the "Ta" node label.
    user_rows = _run(
        """
        MATCH (u:User {id: $user_id})
        RETURN u.name AS name, u.email AS email
        """,
        user_id=user_id
    ) or []
    user_row = user_rows[0] if user_rows else {}
    user_label = user_row.get("name") or user_row.get("email") or "Ẩn danh"

    # Query files
    files = _run(
        """
        MATCH (u:User {id: $user_id})-[r:UPLOADED]->(f:File)
        RETURN f.sample_name AS filename, f.fingerprint AS fingerprint
        """,
        user_id=user_id
    ) or []
    
    # Query recipes
    recipes = _run(
        """
        MATCH (u:User {id: $user_id})-[r:BUILT]->(rec:Recipe)
        RETURN rec.id AS id, rec.title AS title
        """,
        user_id=user_id
    ) or []
    
    # Query actions
    actions = _run(
        """
        MATCH (u:User {id: $user_id})-[r:PERFORMED]->(act:Action)
        RETURN act.id AS id, act.type AS type, act.ts AS ts
        ORDER BY act.ts DESC
        LIMIT 10
        """,
        user_id=user_id
    ) or []
    
    # This user's own personal skills only (curated skills are shared app
    # capability, not "memory" - they never appear here).
    skills = _run(
        """
        MATCH (u:User {id: $user_id})-[:HAS_SKILL]->(s:Skill)
        RETURN s.name AS name, s.description AS description,
               s.usage_count AS usage_count, s.success_count AS success_count
        """,
        user_id=user_id
    ) or []
    
    nodes = []
    edges = []
    
    # Add User node
    nodes.append({
        "id": "user",
        "label": user_label,
        "type": "user",
        "properties": {"id": user_id}
    })
    
    for f in files:
        nodes.append({
            "id": f["fingerprint"],
            "label": f["filename"] or "Tệp không tên",
            "type": "file",
            "properties": {"fingerprint": f["fingerprint"]}
        })
        edges.append({
            "source": "user",
            "target": f["fingerprint"],
            "label": "ĐÃ TẢI LÊN"
        })
        
    for r in recipes:
        nodes.append({
            "id": r["id"],
            "label": r["title"] or "Dashboard không tên",
            "type": "recipe",
            "properties": {"id": r["id"]}
        })
        edges.append({
            "source": "user",
            "target": r["id"],
            "label": "ĐÃ DỰNG"
        })
        
    action_labels = {
        "chat_question": "Đặt câu hỏi",
        "build_dashboard": "Dựng dashboard",
        "generate_report": "Tạo báo cáo",
        "upload": "Tải file lên",
    }
    for a in actions:
        nodes.append({
            "id": a["id"],
            "label": action_labels.get(a["type"], a["type"]),
            "type": "action",
            "properties": {"id": a["id"], "ts": a["ts"]}
        })
        edges.append({
            "source": "user",
            "target": a["id"],
            "label": "THỰC HIỆN"
        })

    # Distilled behaviors (long-term memory notes about this user)
    behaviors = graph.get_behaviors(user_id)
    for b in behaviors:
        nodes.append({
            "id": b["id"],
            "label": b["description"] or "",
            "type": "behavior",
            "properties": {"category": b.get("category"), "usage_count": b.get("usage_count")}
        })
        edges.append({
            "source": "user",
            "target": b["id"],
            "label": "GHI NHỚ"
        })


    formatted_skills = []
    for s in skills:
        usage = s.get("usage_count") or 0
        success = s.get("success_count") or 0
        rate = (success / usage) if usage >= graph.RETIRE_MIN_TRIALS else 1.0
        retired = usage >= graph.RETIRE_MIN_TRIALS and rate < graph.RETIRE_SUCCESS_RATE
        formatted_skills.append({
            "name": s["name"],
            "description": s["description"],
            "usage_count": usage,
            "success_count": success,
            "status": "retired" if retired else "active"
        })
        
        nodes.append({
            "id": f"skill-{s['name']}",
            "label": s["name"],
            "type": "skill",
            "properties": {"status": "retired" if retired else "active"}
        })
        
    return {
        "enabled": True,
        "nodes": nodes,
        "edges": edges,
        "skills": formatted_skills,
        "behaviors": behaviors,
    }


@router.delete("/diagnostics/behaviors")
async def delete_behaviors(request: Request):
    """User-facing "forget what you learned about me"."""
    user_id = get_user_id(request)
    deleted = graph.delete_all_behaviors(user_id)
    return {"deleted": deleted}
