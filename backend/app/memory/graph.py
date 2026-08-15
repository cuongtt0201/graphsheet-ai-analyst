"""Graph memory: one Neo4j graph centered on :User, holding identity
(email/name as PROPERTIES, never as separate nodes), habits (:Action),
uploaded :File shapes (fingerprinted so two users' structurally-identical
sheets recall each other), :Recipe (dashboards built), and self-learned
:Skill nodes — all connected so recall is a traversal instead of a join.

Design rules (agreed in planning):
  - Identity/scalars (email, name, timestamps) are node PROPERTIES, not nodes.
    Only things worth traversing or sharing across users become nodes.
  - :File and :Column are shared/de-duplicated across users (by fingerprint /
    name+role) so the graph can learn cross-user ("people with a file like
    yours usually build X") — the per-user edge is what's private, not the
    node.
  - :Action nodes are only written for meaningful events (a handful of call
    sites), not every micro-interaction, to avoid turning :User into a
    supernode. Rolling these up into a habit-profile is a later phase.

Every public function degrades to a silent no-op (returns None/[] and logs a
warning) if Neo4j is unreachable or NEO4J_PASSWORD is unset — graph memory
must never be able to break upload/chat/dashboard, the same contract as the
sandbox's Docker-tier fallback.
"""

import hashlib
import logging
import threading
import time
import uuid

from app.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER

logger = logging.getLogger(__name__)

try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import Neo4jError, ServiceUnavailable
    HAS_NEO4J = True
except ImportError:
    HAS_NEO4J = False

ENABLED = HAS_NEO4J and bool(NEO4J_PASSWORD)

_driver = None
_driver_lock = threading.Lock()
_bootstrapped = False


def _get_driver():
    """Lazy singleton driver — the neo4j Python driver is itself a connection
    pool, so one instance should live for the process lifetime."""
    global _driver
    if not ENABLED:
        return None
    if _driver is None:
        with _driver_lock:
            if _driver is None:
                _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver


def _run(query: str, **params):
    """Execute one write/read query in its own session. Returns a list of
    record dicts, or None if the graph is disabled/unreachable (callers must
    treat None the same as "no memory available", never raise)."""
    driver = _get_driver()
    if driver is None:
        return None
    try:
        with driver.session() as session:
            result = session.run(query, **params)
            return [dict(r) for r in result]
    except (ServiceUnavailable, Neo4jError, OSError) as exc:
        logger.warning(f"[graph] query failed, degrading to no-op: {exc}")
        return None


def bootstrap_constraints() -> None:
    """Idempotent — call once at app startup. Best-effort: logs and returns on
    failure instead of blocking app startup on a down Neo4j."""
    global _bootstrapped
    if not ENABLED or _bootstrapped:
        return
    statements = [
        "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
        "CREATE CONSTRAINT file_fingerprint IF NOT EXISTS FOR (f:File) REQUIRE f.fingerprint IS UNIQUE",
        # Replaces an earlier global "skill name is unique across ALL users"
        # constraint - now that skills are personal, two different users must
        # be able to independently learn a skill with the same name.
        "DROP CONSTRAINT skill_name IF EXISTS",
        "CREATE CONSTRAINT skill_owner_name IF NOT EXISTS FOR (s:Skill) REQUIRE (s.owner_id, s.name) IS UNIQUE",
        "CREATE CONSTRAINT recipe_id IF NOT EXISTS FOR (r:Recipe) REQUIRE r.id IS UNIQUE",
        "CREATE CONSTRAINT action_id IF NOT EXISTS FOR (a:Action) REQUIRE a.id IS UNIQUE",
        "CREATE CONSTRAINT behavior_id IF NOT EXISTS FOR (b:Behavior) REQUIRE b.id IS UNIQUE",
        "CREATE INDEX column_name IF NOT EXISTS FOR (c:Column) ON (c.name)",
        "CREATE INDEX action_ts IF NOT EXISTS FOR (a:Action) ON (a.ts)",
    ]
    driver = _get_driver()
    if driver is None:
        return
    try:
        with driver.session() as session:
            for stmt in statements:
                session.run(stmt)
        _bootstrapped = True
        logger.info("[graph] constraints bootstrapped")
    except (ServiceUnavailable, Neo4jError, OSError) as exc:
        logger.warning(f"[graph] bootstrap failed, memory features disabled for now: {exc}")


# ── Identity ─────────────────────────────────────────────────────────────

def merge_user(user_id: str, email: str | None = None, name: str | None = None) -> None:
    """user_id is the stable key: the Google OAuth email when logged in, or an
    anonymous session-derived id otherwise (see state.get_user_id) — chat mode
    works without login, so identity/habit tracking must too."""
    _run(
        """
        MERGE (u:User {id: $user_id})
        ON CREATE SET u.created_at = $now
        SET u.email = coalesce($email, u.email), u.name = coalesce($name, u.name), u.last_seen = $now
        """,
        user_id=user_id, email=email, name=name, now=time.time(),
    )


# ── Files (shared, fingerprinted shape) ────────────────────────────────────

def fingerprint_profile(profile: dict) -> str:
    """A stable hash of a sheet's column SHAPE (name + inferred role),
    order-independent. Two structurally-identical sheets — even from
    different users/filenames — hash the same, so :File nodes are shared and
    "seen a file like this before" recall works across users."""
    cols = profile.get("column_profiles") or []
    sig = sorted(f"{c.get('name', '')}|{c.get('role', '?')}" for c in cols)
    if not sig:
        sig = sorted(profile.get("columns") or [])
    return hashlib.sha256("\n".join(sig).encode("utf-8")).hexdigest()[:32]


def upsert_file(user_id: str, profile: dict) -> str:
    """MERGE the (shared) File node for this sheet's shape, link the columns,
    and record that this user uploaded it. Returns the fingerprint."""
    fp = fingerprint_profile(profile)
    cols = profile.get("column_profiles") or []
    _run(
        """
        MERGE (f:File {fingerprint: $fp})
        ON CREATE SET f.first_seen = $now, f.upload_count = 0
        SET f.last_seen = $now, f.upload_count = coalesce(f.upload_count, 0) + 1,
            f.sample_name = $name, f.row_count = $row_count
        WITH f
        UNWIND $cols AS col
          MERGE (c:Column {name: col.name, role: col.role})
          MERGE (f)-[:HAS_COLUMN]->(c)
        WITH DISTINCT f
        MATCH (u:User {id: $user_id})
        MERGE (u)-[r:UPLOADED]->(f)
        ON CREATE SET r.first_at = $now
        SET r.last_at = $now, r.count = coalesce(r.count, 0) + 1
        """,
        fp=fp, user_id=user_id, now=time.time(),
        name=profile.get("sheet") or profile.get("source_id", ""),
        row_count=profile.get("row_count", 0),
        cols=[{"name": c.get("name", ""), "role": c.get("role", "?")} for c in cols],
    )
    return fp


# ── Habits (Action log — coarse-grained on purpose) ────────────────────────

def log_action(user_id: str, action_type: str, payload: dict | None = None,
                file_fingerprint: str | None = None) -> None:
    """Record one meaningful event (asked a question, built a dashboard,
    exported, clicked a suggestion). Call sparingly — this is what accumulates
    on :User, and an unbounded per-keystroke log would turn it into a
    supernode; a later phase should roll old Actions into an aggregate
    habit-profile instead of keeping them all forever."""
    _run(
        """
        MATCH (u:User {id: $user_id})
        SET u.last_seen = $now
        CREATE (a:Action {id: $id, type: $type, payload: $payload, ts: $now})
        MERGE (u)-[:PERFORMED]->(a)
        WITH a
        OPTIONAL MATCH (f:File {fingerprint: $fp})
        FOREACH (_ IN CASE WHEN f IS NULL THEN [] ELSE [1] END | MERGE (a)-[:ON]->(f))
        """,
        user_id=user_id, id=str(uuid.uuid4()), type=action_type,
        payload=_flatten_payload(payload), now=time.time(), fp=file_fingerprint,
    )


def _flatten_payload(payload: dict | None) -> str:
    """Neo4j properties can't hold nested maps — stash payload as compact JSON."""
    import json
    if not payload:
        return "{}"
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)[:2000]
    except (TypeError, ValueError):
        return "{}"


def recent_actions(user_id: str, action_type: str | None = None, limit: int = 20) -> list[dict]:
    rows = _run(
        """
        MATCH (u:User {id: $user_id})-[:PERFORMED]->(a:Action)
        WHERE $type IS NULL OR a.type = $type
        RETURN a.type AS type, a.payload AS payload, a.ts AS ts
        ORDER BY a.ts DESC
        LIMIT $limit
        """,
        user_id=user_id, type=action_type, limit=limit,
    )
    return rows or []


# ── Recipes (dashboards built, replayable on a similar file later) ────────

def save_recipe(user_id: str, file_fingerprint: str, title: str, layout_summary: dict) -> str | None:
    """layout_summary should be small (KPI/chart TYPES + aggregations, not the
    computed data) — this is a reusable blueprint, not a data snapshot."""
    import json
    recipe_id = str(uuid.uuid4())
    rows = _run(
        """
        MATCH (u:User {id: $user_id})
        MATCH (f:File {fingerprint: $fp})
        CREATE (r:Recipe {id: $id, title: $title, layout: $layout, created_at: $now})
        MERGE (u)-[:BUILT]->(r)
        MERGE (r)-[:FOR]->(f)
        RETURN r.id AS id
        """,
        user_id=user_id, fp=file_fingerprint, id=recipe_id, title=title,
        layout=json.dumps(layout_summary, ensure_ascii=False, default=str)[:4000],
        now=time.time(),
    )
    return recipe_id if rows is not None else None


# ── Skills (personal: each user grows their OWN skill library, mirrors the
# per-user .py files in skills_manager's personal/<owner> dir). Curated skills
# (skills_manager's curated/ dir) are never mirrored here — they're shared,
# hand-tested, and never subject to usage-based retirement. ──────────────────

def record_skill(owner_id: str, name: str, description: str, code: str = "") -> None:
    _run(
        """
        MERGE (u:User {id: $owner_id})
        MERGE (u)-[:HAS_SKILL]->(s:Skill {name: $name, owner_id: $owner_id})
        ON CREATE SET s.created_at = $now, s.usage_count = 0, s.success_count = 0
        SET s.description = $description, s.code = $code
        """,
        owner_id=owner_id, name=name, description=description[:500], code=code, now=time.time(),
    )


def get_personal_skills(owner_id: str) -> list[dict]:
    """Retrieve all personal skills stored in Neo4j for this user, including the python code."""
    if not ENABLED or not owner_id:
        return []
    try:
        rows = _run(
            """
            MATCH (s:Skill {owner_id: $owner_id})
            RETURN s.name AS name, s.description AS description, s.code AS code
            """,
            owner_id=owner_id,
        )
        return rows or []
    except Exception:
        return []


def record_skill_usage(owner_id: str, name: str, success: bool) -> None:
    _run(
        """
        MATCH (s:Skill {name: $name, owner_id: $owner_id})
        SET s.usage_count = coalesce(s.usage_count, 0) + 1,
            s.success_count = coalesce(s.success_count, 0) + CASE WHEN $success THEN 1 ELSE 0 END
        """,
        name=name, owner_id=owner_id, success=success,
    )


def get_retired_skills(owner_id: str) -> set[str]:
    """This user's own skills with low success (usage >= 3, success_rate < 30%)
    - retired from THEIR sandbox only, never affects other users' skills."""
    if not ENABLED or not owner_id:
        return set()
    try:
        rows = _run(
            """
            MATCH (s:Skill {owner_id: $owner_id})
            WHERE s.usage_count >= 3 AND (toFloat(s.success_count) / s.usage_count) < 0.3
            RETURN s.name AS name
            """,
            owner_id=owner_id,
        )
        return {row["name"] for row in rows}
    except Exception:
        return set()


# ── Behaviors (per-user habits distilled from Action logs while idle) ──────
#
# Lifecycle: the idle job (memory/idle_job.py) waits for a user to go quiet,
# hands their raw Action log to an LLM that distills durable habits/preferences
# ("thường xem doanh thu theo tháng", "hay lọc theo Miền Bắc", "đang phân tích
# dở file X"), stores each as a :Behavior, then DELETES the processed Actions —
# so Actions are a short-lived buffer, not an ever-growing log. Behaviors are
# injected into chat prompts; ones the model marks as used-but-wrong repeatedly
# (usage >= 3, success < 30%) are hard-deleted.

MAX_BEHAVIORS_PER_USER = 20


def get_idle_users(idle_seconds: float, min_actions: int = 3) -> list[dict]:
    """Users whose last activity is older than idle_seconds and who have
    unprocessed Actions worth distilling. Uses an atomic lock in Cypher so
    multiple Gunicorn/Uvicorn workers won't step on each other."""
    # lock_cutoff ensures a dead worker doesn't permanently lock a user (5 min timeout)
    rows = _run(
        """
        MATCH (u:User)-[:PERFORMED]->(a:Action)
        WITH u, count(a) AS n_actions
        WHERE u.last_seen < $cutoff AND n_actions >= $min_actions
          AND (u.distill_lock IS NULL OR u.distill_lock < $lock_cutoff)
        SET u.distill_lock = $now
        RETURN u.id AS user_id, n_actions
        """,
        cutoff=time.time() - idle_seconds,
        lock_cutoff=time.time() - 300,
        now=time.time(),
        min_actions=min_actions,
    )
    return rows or []


def get_actions_for_distill(user_id: str, limit: int = 100) -> list[dict]:
    """The user's raw Action buffer, oldest first (chronological reads better
    in the distill prompt), with ids so the caller can delete exactly these."""
    rows = _run(
        """
        MATCH (u:User {id: $user_id})-[:PERFORMED]->(a:Action)
        RETURN a.id AS id, a.type AS type, a.payload AS payload, a.ts AS ts
        ORDER BY a.ts ASC
        LIMIT $limit
        """,
        user_id=user_id, limit=limit,
    )
    return rows or []


def delete_actions(action_ids: list[str]) -> None:
    """Remove processed Actions (post-distillation) so the buffer stays small."""
    if not action_ids:
        return
    _run(
        """
        MATCH (a:Action) WHERE a.id IN $ids
        DETACH DELETE a
        """,
        ids=action_ids,
    )


def get_behaviors(user_id: str) -> list[dict]:
    rows = _run(
        """
        MATCH (u:User {id: $user_id})-[:HAS_BEHAVIOR]->(b:Behavior)
        RETURN b.id AS id, b.description AS description, b.category AS category,
               b.usage_count AS usage_count, b.success_count AS success_count,
               b.updated_at AS updated_at
        ORDER BY b.updated_at DESC
        """,
        user_id=user_id,
    )
    return rows or []


def save_behaviors(user_id: str, behaviors: list[dict]) -> None:
    """Store freshly distilled behaviors. Each: {description, category}.
    Then trim to the newest MAX_BEHAVIORS_PER_USER so a chatty user's profile
    can't grow without bound (oldest, least-recently-updated go first)."""
    if not behaviors:
        return
    now = time.time()
    _run(
        """
        MATCH (u:User {id: $user_id})
        UNWIND $items AS item
          CREATE (b:Behavior {id: item.id, description: item.description,
                              category: item.category, usage_count: 0,
                              success_count: 0, created_at: $now, updated_at: $now})
          MERGE (u)-[:HAS_BEHAVIOR]->(b)
        """,
        user_id=user_id, now=now,
        items=[{"id": str(uuid.uuid4()),
                "description": (b.get("description") or "")[:300],
                "category": (b.get("category") or "habit")[:40]} for b in behaviors],
    )
    _run(
        """
        MATCH (u:User {id: $user_id})-[:HAS_BEHAVIOR]->(b:Behavior)
        WITH b ORDER BY b.updated_at DESC
        SKIP $keep
        DETACH DELETE b
        """,
        user_id=user_id, keep=MAX_BEHAVIORS_PER_USER,
    )


def record_behavior_usage(behavior_ids: list[str], success: bool) -> None:
    """Bump counters for behaviors the chat model said it actually relied on,
    then hard-delete any that have proven wrong (used >= 3 times, < 30% success)
    — bad memories should not linger the way retired skills do."""
    if not behavior_ids:
        return
    _run(
        """
        MATCH (b:Behavior) WHERE b.id IN $ids
        SET b.usage_count = coalesce(b.usage_count, 0) + 1,
            b.success_count = coalesce(b.success_count, 0) + CASE WHEN $success THEN 1 ELSE 0 END,
            b.updated_at = $now
        WITH b
        WHERE b.usage_count >= 3 AND (toFloat(b.success_count) / b.usage_count) < 0.3
        DETACH DELETE b
        """,
        ids=behavior_ids, success=success, now=time.time(),
    )


def delete_all_behaviors(user_id: str) -> int:
    """User-facing "forget me" — wipe the whole distilled profile."""
    rows = _run(
        """
        MATCH (u:User {id: $user_id})-[:HAS_BEHAVIOR]->(b:Behavior)
        DETACH DELETE b
        RETURN count(*) AS deleted
        """,
        user_id=user_id,
    )
    return rows[0]["deleted"] if rows else 0


# ── Diagnostics ─────────────────────────────────────────────────────────────

def user_summary(user_id: str) -> dict | None:
    """One-shot readback used for verification/tests: counts of everything
    hanging off a user's ego graph."""
    rows = _run(
        """
        MATCH (u:User {id: $user_id})
        OPTIONAL MATCH (u)-[:UPLOADED]->(f:File)
        OPTIONAL MATCH (u)-[:PERFORMED]->(a:Action)
        OPTIONAL MATCH (u)-[:BUILT]->(r:Recipe)
        RETURN u.email AS email, u.name AS name,
               count(DISTINCT f) AS files, count(DISTINCT a) AS actions, count(DISTINCT r) AS recipes
        """,
        user_id=user_id,
    )
    return rows[0] if rows else None
