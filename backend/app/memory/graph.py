"""Graph memory: one Neo4j graph centered on :User, holding identity,
habits (:Behavior with Bubble Merge), personal business rules (:BusinessRule),
uploaded :File shapes (fingerprinted), :Recipe (dashboards built), and self-learned
:Skill nodes — all connected so recall is a traversal instead of a join.

Key Innovations:
  - BUBBLE MERGE: Similar memory nodes merge into a single larger, higher-weight node
    (weight increases: 1 -> 2 -> 3) instead of proliferating duplicate nodes.
  - HARD QUOTAS & LRU EVICTION: Strict caps per user (15 Behaviors, 10 Rules, 10 Recipes)
    preventing supernode explosion. Stale, low-weight nodes are evicted automatically.
  - EXPLICIT & NATURAL ERASURE: Fast deletion by ID, keyword query, or full reset.
  - PROACTIVE RECIPE RECALL: Auto-detects previously built dashboards on matching file shapes.
"""

import hashlib
import json
import logging
import re
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

# Hard Quotas per User to prevent supernode bloat
MAX_BEHAVIORS_PER_USER = 15
MAX_RULES_PER_USER = 10
MAX_RECIPES_PER_USER = 10


def _get_driver():
    """Lazy singleton driver."""
    global _driver
    if not ENABLED:
        return None
    if _driver is None:
        with _driver_lock:
            if _driver is None:
                _driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver


def _run(query: str, **params):
    """Execute one write/read query in its own session."""
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
    """Idempotent constraints bootstrap."""
    global _bootstrapped
    if not ENABLED or _bootstrapped:
        return
    statements = [
        "CREATE CONSTRAINT user_id IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
        "CREATE CONSTRAINT file_fingerprint IF NOT EXISTS FOR (f:File) REQUIRE f.fingerprint IS UNIQUE",
        "DROP CONSTRAINT skill_name IF EXISTS",
        "CREATE CONSTRAINT skill_owner_name IF NOT EXISTS FOR (s:Skill) REQUIRE (s.owner_id, s.name) IS UNIQUE",
        "CREATE CONSTRAINT recipe_id IF NOT EXISTS FOR (r:Recipe) REQUIRE r.id IS UNIQUE",
        "CREATE CONSTRAINT action_id IF NOT EXISTS FOR (a:Action) REQUIRE a.id IS UNIQUE",
        "CREATE CONSTRAINT behavior_id IF NOT EXISTS FOR (b:Behavior) REQUIRE b.id IS UNIQUE",
        "CREATE CONSTRAINT business_rule_id IF NOT EXISTS FOR (r:BusinessRule) REQUIRE r.id IS UNIQUE",
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
        logger.info("[graph] constraints bootstrapped successfully")
    except (ServiceUnavailable, Neo4jError, OSError) as exc:
        logger.warning(f"[graph] bootstrap failed, memory features disabled for now: {exc}")


# ── Identity ─────────────────────────────────────────────────────────────

def merge_user(user_id: str, email: str | None = None, name: str | None = None) -> None:
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
    cols = profile.get("column_profiles") or []
    sig = sorted(f"{c.get('name', '')}|{c.get('role', '?')}" for c in cols)
    if not sig:
        sig = sorted(profile.get("columns") or [])
    return hashlib.sha256("\n".join(sig).encode("utf-8")).hexdigest()[:32]


def upsert_file(user_id: str, profile: dict) -> str:
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


# ── Habits (Action log — short-lived buffer) ──────────────────────────────

def log_action(user_id: str, action_type: str, payload: dict | None = None,
                file_fingerprint: str | None = None) -> None:
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
    recipe_id = str(uuid.uuid4())
    now = time.time()
    rows = _run(
        """
        MATCH (u:User {id: $user_id})
        MATCH (f:File {fingerprint: $fp})
        CREATE (r:Recipe {id: $id, title: $title, layout: $layout, created_at: $now, updated_at: $now, usage_count: 1})
        MERGE (u)-[:BUILT]->(r)
        MERGE (r)-[:FOR]->(f)
        RETURN r.id AS id
        """,
        user_id=user_id, fp=file_fingerprint, id=recipe_id, title=title,
        layout=json.dumps(layout_summary, ensure_ascii=False, default=str)[:4000],
        now=now,
    )
    # Evict oldest recipes if exceeding quota
    _run(
        """
        MATCH (u:User {id: $user_id})-[:BUILT]->(r:Recipe)
        WITH r ORDER BY r.updated_at DESC
        SKIP $keep
        DETACH DELETE r
        """,
        user_id=user_id, keep=MAX_RECIPES_PER_USER,
    )
    return recipe_id if rows is not None else None


def find_matching_recipe(user_id: str, file_fingerprint: str) -> dict | None:
    """Proactive Recipe Recall: Detects if the user previously built a dashboard
    for a file with the EXACT same column shape."""
    if not ENABLED or not user_id or not file_fingerprint:
        return None
    rows = _run(
        """
        MATCH (u:User {id: $user_id})-[:BUILT]->(r:Recipe)-[:FOR]->(f:File {fingerprint: $fp})
        RETURN r.id AS id, r.title AS title, r.layout AS layout, r.created_at AS created_at, f.sample_name AS sample_name
        ORDER BY r.updated_at DESC LIMIT 1
        """,
        user_id=user_id, fp=file_fingerprint,
    )
    if rows and rows[0]:
        rec = rows[0]
        try:
            rec["layout_obj"] = json.loads(rec.get("layout") or "{}")
        except Exception:
            rec["layout_obj"] = {}
        return rec
    return None


# ── Skills (Muscle Memory) ─────────────────────────────────────────────────

def record_skill(owner_id: str, name: str, description: str, code: str = "") -> None:
    _run(
        """
        MERGE (u:User {id: $owner_id})
        MERGE (u)-[:HAS_SKILL]->(s:Skill {name: $name, owner_id: $owner_id})
        ON CREATE SET s.created_at = $now, s.usage_count = 0, s.success_count = 0
        SET s.description = $description, s.code = $code, s.updated_at = $now
        """,
        owner_id=owner_id, name=name, description=description[:500], code=code, now=time.time(),
    )


def get_personal_skills(owner_id: str) -> list[dict]:
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
            s.success_count = coalesce(s.success_count, 0) + CASE WHEN $success THEN 1 ELSE 0 END,
            s.updated_at = $now
        """,
        name=name, owner_id=owner_id, success=success, now=time.time(),
    )


# ── Bubble Merge & Behaviors (Consolidated Habits & Preferences) ───────────

def _token_similarity(text1: str, text2: str) -> float:
    """Calculate token Jaccard similarity to detect overlapping concepts."""
    tokens1 = set(re.findall(r"\w+", text1.lower()))
    tokens2 = set(re.findall(r"\w+", text2.lower()))
    if not tokens1 or not tokens2:
        return 0.0
    return len(tokens1 & tokens2) / len(tokens1 | tokens2)


def get_behaviors(user_id: str) -> list[dict]:
    rows = _run(
        """
        MATCH (u:User {id: $user_id})-[:HAS_BEHAVIOR]->(b:Behavior)
        RETURN b.id AS id, b.description AS description, b.category AS category,
               coalesce(b.weight, 1) AS weight, b.usage_count AS usage_count,
               b.success_count AS success_count, b.updated_at AS updated_at
        ORDER BY b.weight DESC, b.updated_at DESC
        """,
        user_id=user_id,
    )
    return rows or []


def save_or_merge_behavior(user_id: str, description: str, category: str = "habit") -> dict:
    """Bubble Merge: Merges with an existing similar behavior (increasing its weight)
    or creates a new node, enforcing the MAX_BEHAVIORS_PER_USER quota."""
    now = time.time()
    existing = get_behaviors(user_id)
    
    # Check if there is a similar bubble to merge with (similarity >= 0.50)
    for b in existing:
        sim = _token_similarity(description, b.get("description", ""))
        if sim >= 0.50 or (b.get("category") == category and sim >= 0.35):
            new_weight = int(b.get("weight", 1)) + 1
            merged_desc = description if len(description) >= len(b.get("description", "")) else b.get("description")
            _run(
                """
                MATCH (b:Behavior {id: $id})
                SET b.description = $desc, b.weight = $weight, b.updated_at = $now
                """,
                id=b["id"], desc=merged_desc, weight=new_weight, now=now,
            )
            return {"action": "merged", "id": b["id"], "weight": new_weight, "description": merged_desc}

    # No similar bubble -> Create new node
    new_id = str(uuid.uuid4())
    _run(
        """
        MATCH (u:User {id: $user_id})
        CREATE (b:Behavior {id: $id, description: $desc, category: $category,
                            weight: 1, usage_count: 0, success_count: 0,
                            created_at: $now, updated_at: $now})
        MERGE (u)-[:HAS_BEHAVIOR]->(b)
        """,
        user_id=user_id, id=new_id, desc=description[:300], category=category[:40], now=now,
    )

    # Evict lowest-weight & oldest if quota exceeded
    _run(
        """
        MATCH (u:User {id: $user_id})-[:HAS_BEHAVIOR]->(b:Behavior)
        WITH b ORDER BY b.weight ASC, b.updated_at ASC
        WITH collect(b) AS all_b
        WHERE size(all_b) > $quota
        UNWIND all_b[0..(size(all_b) - $quota)] AS to_delete
        DETACH DELETE to_delete
        """,
        user_id=user_id, quota=MAX_BEHAVIORS_PER_USER,
    )
    return {"action": "created", "id": new_id, "weight": 1, "description": description}


def save_behaviors(user_id: str, behaviors: list[dict]) -> None:
    """Batch entry for distiller: uses bubble merge on each behavior."""
    for b in behaviors:
        desc = b.get("description")
        cat = b.get("category") or "habit"
        if desc:
            save_or_merge_behavior(user_id, desc, cat)


# ── Business Rules (Custom Formulas & Definitions per User) ────────────────

def get_business_rules(user_id: str) -> list[dict]:
    rows = _run(
        """
        MATCH (u:User {id: $user_id})-[:DEFINES]->(r:BusinessRule)
        RETURN r.id AS id, r.concept_name AS concept_name, r.formula_desc AS formula_desc,
               r.target_columns AS target_columns, coalesce(r.weight, 1) AS weight,
               r.updated_at AS updated_at
        ORDER BY r.weight DESC, r.updated_at DESC
        """,
        user_id=user_id,
    )
    return rows or []


def save_or_merge_business_rule(user_id: str, concept_name: str, formula_desc: str, target_columns: list[str] | None = None) -> dict:
    """Bubble Merge for Business Rules."""
    now = time.time()
    cols = json.dumps(target_columns or [], ensure_ascii=False)
    existing = get_business_rules(user_id)

    # Check for matching concept
    for r in existing:
        if r.get("concept_name", "").lower() == concept_name.lower() or _token_similarity(concept_name, r.get("concept_name", "")) >= 0.7:
            new_weight = int(r.get("weight", 1)) + 1
            _run(
                """
                MATCH (r:BusinessRule {id: $id})
                SET r.formula_desc = $formula, r.target_columns = $cols,
                    r.weight = $weight, r.updated_at = $now
                """,
                id=r["id"], formula=formula_desc, cols=cols, weight=new_weight, now=now,
            )
            return {"action": "merged", "id": r["id"], "weight": new_weight}

    new_id = str(uuid.uuid4())
    _run(
        """
        MATCH (u:User {id: $user_id})
        CREATE (r:BusinessRule {id: $id, concept_name: $name, formula_desc: $formula,
                                target_columns: $cols, weight: 1, created_at: $now, updated_at: $now})
        MERGE (u)-[:DEFINES]->(r)
        """,
        user_id=user_id, id=new_id, name=concept_name, formula=formula_desc, cols=cols, now=now,
    )

    # Evict if exceeding quota
    _run(
        """
        MATCH (u:User {id: $user_id})-[:DEFINES]->(r:BusinessRule)
        WITH r ORDER BY r.weight ASC, r.updated_at ASC
        WITH collect(r) AS all_r
        WHERE size(all_r) > $quota
        UNWIND all_r[0..(size(all_r) - $quota)] AS to_delete
        DETACH DELETE to_delete
        """,
        user_id=user_id, quota=MAX_RULES_PER_USER,
    )
    return {"action": "created", "id": new_id, "weight": 1}


# ── Explicit & Natural Memory Erasure ───────────────────────────────────────

def delete_memory_by_id(user_id: str, memory_id: str) -> bool:
    """Delete a specific memory node by ID."""
    rows = _run(
        """
        MATCH (u:User {id: $user_id})
        MATCH (n {id: $id})
        WHERE (u)-[:HAS_BEHAVIOR]->(n) OR (u)-[:DEFINES]->(n) OR (u)-[:BUILT]->(n)
        DETACH DELETE n
        RETURN count(n) AS deleted
        """,
        user_id=user_id, id=memory_id,
    )
    return bool(rows and rows[0].get("deleted", 0) > 0)


_ERASE_STOPWORDS = {
    "quên", "xóa", "thói", "quen", "bộ", "nhớ", "ký", "ức", "đi", "đừng",
    "hết", "tất", "cả", "luật", "công", "thức", "tôi", "cho", "về", "của",
    "các", "những", "nào", "gì", "memory", "forget", "delete", "clear", "all", "the", "my"
}


def forget_memory_by_text(user_id: str, query_text: str) -> list[str]:
    """Natural Language Erasure: Search and delete memories matching keywords."""
    if not query_text or not user_id:
        return []
    keywords = [w.lower() for w in re.findall(r"\w+", query_text) if len(w) >= 2]
    if not keywords:
        return []

    search_terms = [w for w in keywords if w not in _ERASE_STOPWORDS]
    if not search_terms:
        search_terms = keywords

    deleted_items = []
    # Search behaviors
    behaviors = get_behaviors(user_id)
    for b in behaviors:
        desc = b.get("description", "").lower()
        if any(term in desc for term in search_terms) or _token_similarity(query_text, desc) >= 0.35:
            delete_memory_by_id(user_id, b["id"])
            deleted_items.append(f"Thói quen: {b.get('description')}")

    # Search rules
    rules = get_business_rules(user_id)
    for r in rules:
        c_name = r.get("concept_name", "").lower()
        if any(term in c_name for term in search_terms) or _token_similarity(query_text, c_name) >= 0.35:
            delete_memory_by_id(user_id, r["id"])
            deleted_items.append(f"Luật nghiệp vụ: {r.get('concept_name')}")

    return deleted_items


def delete_all_user_memories(user_id: str) -> int:
    """Wipe the entire personal knowledge profile for this user."""
    rows = _run(
        """
        MATCH (u:User {id: $user_id})
        OPTIONAL MATCH (u)-[:HAS_BEHAVIOR]->(b:Behavior)
        OPTIONAL MATCH (u)-[:DEFINES]->(r:BusinessRule)
        OPTIONAL MATCH (u)-[:BUILT]->(rc:Recipe)
        DETACH DELETE b, r, rc
        RETURN count(b) + count(r) + count(rc) AS deleted
        """,
        user_id=user_id,
    )
    return rows[0]["deleted"] if rows else 0


def get_all_user_memories(user_id: str) -> dict:
    """Retrieve full memory profile for UI management."""
    return {
        "behaviors": get_behaviors(user_id),
        "business_rules": get_business_rules(user_id),
        "recipes": _run(
            """
            MATCH (u:User {id: $user_id})-[:BUILT]->(r:Recipe)
            RETURN r.id AS id, r.title AS title, r.updated_at AS updated_at
            ORDER BY r.updated_at DESC
            """,
            user_id=user_id,
        ) or [],
    }


# ── Distiller Helpers ──────────────────────────────────────────────────────

def get_idle_users(idle_seconds: float, min_actions: int = 3) -> list[dict]:
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
    if not action_ids:
        return
    _run(
        """
        MATCH (a:Action) WHERE a.id IN $ids
        DETACH DELETE a
        """,
        ids=action_ids,
    )


def record_behavior_usage(behavior_ids: list[str], success: bool) -> None:
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


def user_summary(user_id: str) -> dict | None:
    rows = _run(
        """
        MATCH (u:User {id: $user_id})
        OPTIONAL MATCH (u)-[:UPLOADED]->(f:File)
        OPTIONAL MATCH (u)-[:PERFORMED]->(a:Action)
        OPTIONAL MATCH (u)-[:BUILT]->(r:Recipe)
        OPTIONAL MATCH (u)-[:HAS_BEHAVIOR]->(b:Behavior)
        OPTIONAL MATCH (u)-[:DEFINES]->(br:BusinessRule)
        RETURN u.email AS email, u.name AS name,
               count(DISTINCT f) AS files, count(DISTINCT a) AS actions,
               count(DISTINCT r) AS recipes, count(DISTINCT b) AS behaviors,
               count(DISTINCT br) AS business_rules
        """,
        user_id=user_id,
    )
    return rows[0] if rows else None
