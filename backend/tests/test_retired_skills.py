"""A skill that keeps failing must stop being handed to the sandbox.

graph.get_retired_skills did not exist, so skills_manager's call raised into a
bare except and the retirement rule -- computed and displayed on the memory
screen -- was enforced nowhere.
"""

import app.memory.graph as graph
from app.agent import skills_manager


def test_retirement_thresholds_are_shared_not_duplicated():
    """The diagnostics screen and the sandbox filter must agree on "retired"."""
    assert graph.RETIRE_MIN_TRIALS == 3
    assert graph.RETIRE_SUCCESS_RATE == 0.3


def test_get_retired_skills_exists_and_is_safe_without_a_graph():
    assert callable(graph.get_retired_skills)
    assert graph.get_retired_skills("") == set()


def test_retired_personal_skills_are_withheld_from_the_pool(monkeypatch):
    monkeypatch.setattr(graph, "get_retired_skills", lambda owner: {"hay_hong"})
    monkeypatch.setattr(graph, "get_personal_skills", lambda owner: [
        {"name": "hay_hong", "description": "thất bại liên tục", "code": "def hay_hong():\n    pass"},
        {"name": "chay_tot", "description": "ổn định", "code": "def chay_tot():\n    pass"},
    ])

    names = {s["name"] for s in skills_manager.get_available_skills("u1") if s.get("type") == "personal"}
    assert "chay_tot" in names
    assert "hay_hong" not in names


def test_a_graph_failure_still_yields_every_skill(monkeypatch):
    """Neo4j being down must not silently empty the skill pool."""
    def boom(owner):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(graph, "get_retired_skills", boom)
    monkeypatch.setattr(graph, "get_personal_skills", lambda owner: [
        {"name": "chay_tot", "description": "ổn định", "code": "def chay_tot():\n    pass"},
    ])

    names = {s["name"] for s in skills_manager.get_available_skills("u1") if s.get("type") == "personal"}
    assert names == {"chay_tot"}
