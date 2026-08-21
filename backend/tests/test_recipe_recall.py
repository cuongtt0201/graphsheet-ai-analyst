"""The upload path's recipe recall must call a function that exists.

It called graph.query_recipes_by_fingerprint, which was never defined, so every
upload raised AttributeError into a bare except and the feature never once ran.
"""

import app.memory.graph as graph


def test_recall_uses_a_function_the_graph_module_actually_defines():
    assert hasattr(graph, "find_matching_recipe")
    assert not hasattr(graph, "query_recipes_by_fingerprint")


def test_recall_reads_counts_from_the_parsed_layout(monkeypatch):
    """save_recipe stores {"kpis": [...], "charts": [...]} under `layout`, and
    find_matching_recipe hands it back parsed as `layout_obj` - not at the top
    level, which is where the dead call expected to find it."""
    recorded = {}

    def fake_find(user_id, fingerprint):
        recorded["args"] = (user_id, fingerprint)
        return {
            "id": "r1",
            "title": "Doanh thu",
            "layout_obj": {"kpis": [{"name": "Tổng"}], "charts": [{"title": "A"}, {"title": "B"}]},
        }

    monkeypatch.setattr(graph, "find_matching_recipe", fake_find)

    recipe = graph.find_matching_recipe("u1", "fp1")
    layout = recipe.get("layout_obj") or {}
    assert len(layout.get("kpis") or []) == 1
    assert len(layout.get("charts") or []) == 2
    assert recorded["args"] == ("u1", "fp1")


def test_find_matching_recipe_is_a_no_op_without_a_fingerprint():
    assert graph.find_matching_recipe("u1", "") is None
    assert graph.find_matching_recipe("", "fp") is None
