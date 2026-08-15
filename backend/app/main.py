import logging
import secrets
import threading
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.auth.routes import router as auth_router
from app.config import FRONTEND_URL, SESSION_SECRET
from app.memory import graph, idle_job
from app.routers.agent import router as agent_router
from app.routers.chat import router as chat_router
from app.routers.upload import router as upload_router
from app.util_json import SafeJSONResponse

# NaN/Inf-safe default response: messy sheets produce non-finite floats
# (all-null column mean, divide-by-zero KPI) that Starlette's stock JSON
# renderer rejects with "Out of range float values are not JSON compliant".
app = FastAPI(title="AI Dashboard Tool API", default_response_class=SafeJSONResponse)

# A hardcoded fallback secret is worse than a random one: anyone who reads the
# source can forge session cookies for every deployment that didn't set the env
# var. Generate a per-process random secret instead (sessions won't survive a
# restart, which is the correct failure mode until SESSION_SECRET is configured).
_session_secret = SESSION_SECRET
if not _session_secret:
    logging.getLogger(__name__).warning(
        "SESSION_SECRET is not set - using a random per-process secret; "
        "sessions will not persist across restarts. Set SESSION_SECRET in .env."
    )
    _session_secret = secrets.token_urlsafe(64)

app.add_middleware(SessionMiddleware, secret_key=_session_secret)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(upload_router)
app.include_router(chat_router)
app.include_router(agent_router)


@app.on_event("startup")
def _bootstrap_graph_memory():
    """Neo4j may still be booting when this container starts (compose only
    gates on it when the full stack comes up together). Retry a few times in
    a background thread so a slow/absent graph never delays API readiness —
    graph.py's own per-query fallback keeps the app working either way."""
    if not graph.ENABLED:
        return

    def _retry():
        for attempt in range(5):
            graph.bootstrap_constraints()
            if graph._bootstrapped:
                return
            time.sleep(5 * (attempt + 1))
        logging.getLogger(__name__).warning(
            "[graph] could not bootstrap constraints after retries; "
            "memory features will keep degrading to no-ops."
        )

    threading.Thread(target=_retry, daemon=True).start()


@app.on_event("startup")
def _start_idle_distiller():
    """Background self-learning: while a user is idle, distill their Action
    buffer into :Behavior memories and clear the buffer (see memory/idle_job)."""
    if graph.ENABLED:
        idle_job.start()


@app.get("/health")
def health():
    return {"ok": True}
