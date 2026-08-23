import os
import ast
import glob
import importlib.util
import logging
import re
from typing import List, Dict

from app.agent.sandbox import UnsafeCodeError, scan_code
from app.memory import graph

logger = logging.getLogger(__name__)

# Two tiers, kept in separate directories:
#   curated/  - hand-written, pre-tested helpers shipped with the app, shared
#               by EVERY user (top_n_by, pareto, ...). Never personal, never
#               retired automatically.
#   personal/<owner>/ - skills the AI teaches ITSELF while working for one
#               specific user (save_new_skill). Only that user's sandbox ever
#               sees them - this is what makes the AI "grow with the user"
#               instead of every user sharing one global skill pool.
_BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
CURATED_DIR = os.path.join(_BASE_DIR, "curated")
PERSONAL_ROOT = os.path.join(_BASE_DIR, "personal")
os.makedirs(CURATED_DIR, exist_ok=True)
os.makedirs(PERSONAL_ROOT, exist_ok=True)


def _safe_owner_dir(owner_id: str) -> str:
    """user_id (an email, or 'anon:<session id>') -> a filesystem-safe personal
    skills directory, created on first use."""
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", owner_id)[:120] or "unknown"
    d = os.path.join(PERSONAL_ROOT, safe)
    os.makedirs(d, exist_ok=True)
    return d


def _skill_dirs(owner_id: str | None) -> List[str]:
    """Curated skills are always in scope; personal skills only for their owner."""
    dirs = [CURATED_DIR]
    if owner_id:
        dirs.append(_safe_owner_dir(owner_id))
    return dirs


def _is_safe_skill(code: str, origin: str) -> bool:
    """Skills are AI-written code persisted to disk and executed on every run —
    the same AST gate as the sandbox (whitelisted imports only, no dunder/
    forbidden names) must pass both at save time and again at load time, so a
    file edited on disk can't sneak past."""
    try:
        scan_code(code, allow_imports=True)
        return True
    except UnsafeCodeError as exc:
        logger.warning(f"Rejected unsafe skill ({origin}): {exc}")
        return False


def get_available_skills(owner_id: str | None = None) -> List[Dict[str, str]]:
    """Curated skills from disk + personal skills from Neo4j.
    Never another user's personal skills."""
    skills = []
    try:
        retired = graph.get_retired_skills(owner_id) if owner_id else set()
    except Exception:
        retired = set()

    # Load curated from disk
    if os.path.exists(CURATED_DIR):
        for filename in os.listdir(CURATED_DIR):
            if not (filename.endswith(".py") and not filename.startswith("__")):
                continue
            filepath = os.path.join(CURATED_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    code = f.read()
                    tree = ast.parse(code, filename=filepath)
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                        docstring = ast.get_docstring(node) or "No description."
                        skills.append({
                            "name": node.name,
                            "description": docstring.strip(),
                            "code": code,
                            "type": "curated"
                        })
            except Exception:
                pass

    # Load personal from Neo4j
    if owner_id:
        try:
            personal = graph.get_personal_skills(owner_id)
            for p in personal:
                if p["name"] not in retired and p.get("code"):
                    skills.append({
                        "name": p["name"],
                        "description": p.get("description", ""),
                        "code": p["code"],
                        "type": "personal"
                    })
        except Exception as e:
            logger.warning(f"Could not load personal skills: {e}")

    return skills


def _top_level_descriptions(code: str) -> List[tuple]:
    """[(function_name, docstring), ...] for every public top-level function —
    used to mirror the skill into graph memory (usage/success counters live
    there; the .py file on disk stays the source of truth for the code itself)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            out.append((node.name, (ast.get_docstring(node) or "").strip()))
    return out


def _normalize_skill_name(name: str) -> str:
    """Strip version-y suffixes so calculate_pareto / calculate_pareto_v2 /
    calculate_pareto_new are recognized as the same skill."""
    return re.sub(r"(_v\d+|_new|_updated|_final|_\d+)$", "", name.lower())


_DUPLICATE_JACCARD_THRESHOLD = 0.55
# A weaker docstring-overlap bar, used ONLY when corroborated by the code
# actually doing similar pandas operations (see _operation_fingerprint) — this
# is what catches "same logic, different wording" (e.g. an English docstring
# vs. a Vietnamese one for the identical computation) that pure word-overlap
# on prose would miss entirely.
_CORROBORATING_JACCARD_THRESHOLD = 0.20
_OPERATION_JACCARD_THRESHOLD = 0.6


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _operation_fingerprint(code: str, func_name: str) -> set[str]:
    """The set of method/attribute names (`.groupby`, `.sum`, `.pct_change`,
    `.rolling`, ...) called inside one function's body — a much more robust
    "does this do the same thing" signal than docstring wording. Two skills
    that both `groupby().sum().pct_change()` are doing the same computation
    regardless of whether their docstrings are in English or Vietnamese, or
    phrased completely differently. Not full semantic (no embeddings), but a
    real step toward "same logic" rather than "similar-sounding prose"."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return {sub.attr for sub in ast.walk(node) if isinstance(sub, ast.Attribute)}
    return set()


def _find_duplicate_skill(owner_id: str, name: str, docstring: str, code: str) -> Dict[str, str] | None:
    """Heuristic dedup (no embeddings API — keeps this proportionate, local,
    and free): checked against curated skills + THIS user's own personal
    skills only (never another user's). An existing skill counts as a
    duplicate if
      (a) its normalized name matches, or
      (b) its docstring shares more than half its words with the new one
          (Jaccard) — catches the common "renamed but same prose" case, or
      (c) its CODE's operation fingerprint overlaps ≥60% with the new code's
          AND there's at least weak docstring corroboration (≥20% word
          overlap) — catches "same computation, different/translated wording"
          that (b) alone would miss, while the corroboration guard avoids
          false positives from two unrelated skills that both happen to call
          common ops like .sum()/.mean()."""
    norm_new = _normalize_skill_name(name)
    new_words = set(re.findall(r"\w+", docstring.lower())) if docstring else set()
    new_ops = _operation_fingerprint(code, name)

    for s in get_available_skills(owner_id):
        if s["name"] == name:
            continue  # re-saving under the exact same name is an intentional update
        if _normalize_skill_name(s["name"]) == norm_new:
            return s

        existing_words = set(re.findall(r"\w+", s["description"].lower()))
        doc_jaccard = _jaccard(new_words, existing_words)
        if doc_jaccard > _DUPLICATE_JACCARD_THRESHOLD:
            return s

        if new_ops and doc_jaccard > _CORROBORATING_JACCARD_THRESHOLD:
            # get_available_skills already carries the source in "code", for both
            # tiers. Reading s["file"] raised KeyError -- no skill dict has ever
            # had that key -- and KeyError is not OSError, so it escaped the
            # guard below and killed the whole request that triggered it.
            existing_code = s.get("code") or ""
            if not existing_code:
                continue
            existing_ops = _operation_fingerprint(existing_code, s["name"])
            if _jaccard(new_ops, existing_ops) >= _OPERATION_JACCARD_THRESHOLD:
                return s
    return None


def save_new_skill(owner_id: str, skill_name: str, code: str) -> bool:
    """Persist a self-learned skill to THIS user's personal skill directory —
    never shared with other users (that's what distinguishes it from the
    curated library)."""
    if not _is_safe_skill(code, f"save:{skill_name}"):
        return False

    descriptions = _top_level_descriptions(code)
    own_doc = next((doc for n, doc in descriptions if n == skill_name), "")
    if not own_doc and descriptions:
        own_doc = descriptions[0][1]

    dup = _find_duplicate_skill(owner_id, skill_name, own_doc, code)
    if dup is not None:
        logger.info(f"Skipping save of '{skill_name}' for {owner_id} — duplicate of existing skill '{dup['name']}'")
        # Reinforce the existing skill instead of forking a near-identical one.
        graph.record_skill_usage(owner_id, dup["name"], success=True)
        return False

    try:
        # Save directly to Neo4j (code included), solving the multi-pod disk state issue!
        for name, docstring in descriptions:
            graph.record_skill(owner_id, name, docstring, code=code)
        return True
    except Exception as e:
        logger.error(f"Failed to save skill to graph: {e}")
        return False


def get_skills_source(owner_id: str | None = None) -> str:
    """Concatenated source of all safe skills (curated + this owner's personal)
    — shipped into the container sandbox as helper code so scripts can call
    them there too."""
    parts = []
    for s in get_available_skills(owner_id):
        if _is_safe_skill(s["code"], s["name"]):
            parts.append(f"# --- {s['name']} ({s['type']}) ---\n" + s["code"])
    return "\n\n".join(parts)


def load_skills_into_env(env: dict, owner_id: str | None = None):
    for s in get_available_skills(owner_id):
        if _is_safe_skill(s["code"], s["name"]):
            try:
                # Execute the code directly in the provided dict, avoiding local file importlib mess.
                # Safe because _is_safe_skill already runs AST verification.
                exec(s["code"], env)
            except Exception as e:
                logger.warning(f"Error loading skill {s['name']} into env: {e}")


def get_relevant_skills(query: str, owner_id: str | None = None, top_k: int = 5) -> List[Dict[str, str]]:
    """Retrieves relevant skills (curated + this owner's personal) using BM25
    or keyword matching."""
    all_skills = get_available_skills(owner_id)
    if not all_skills:
        return []

    try:
        from rank_bm25 import BM25Okapi
        import re

        def tokenize(text: str) -> List[str]:
            return [w.lower() for w in re.findall(r"\w+", text)]

        corpus = [tokenize(f"{s['name']} {s['description']}") for s in all_skills]
        bm25 = BM25Okapi(corpus)

        query_tokens = tokenize(query)
        scores = bm25.get_scores(query_tokens)

        scored_skills = list(zip(all_skills, scores))
        scored_skills.sort(key=lambda x: x[1], reverse=True)

        # Filter out zero/negative scores, return top_k
        return [item[0] for item in scored_skills[:top_k] if item[1] > 0]
    except Exception:
        # Fallback keyword matching
        words = [w.lower() for w in query.split() if len(w) > 2]
        if not words:
            return all_skills[:top_k]

        scored = []
        for s in all_skills:
            text = f"{s['name']} {s['description']}".lower()
            score = sum(1 for w in words if w in text)
            if score > 0:
                scored.append((s, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [item[0] for item in scored[:top_k]]
