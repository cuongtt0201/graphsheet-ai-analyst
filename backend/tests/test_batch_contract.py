"""The contract between batch_tasks and its callers.

batch_tasks strips each task's {thinking, result} envelope before returning, so
what the caller receives IS the result. A caller that reaches for ["result"]
again gets {} — and because every consumer here treats an empty result as
"nothing to do", the failure is completely silent: no exception, no log, just a
dashboard with no insights, no chosen layout, and no skill ever learned.

That is exactly what shipped, so the shape is pinned down here.
"""

from unittest.mock import patch

from app.ai import harness


MODEL_RESPONSE = {
    "insights": {"thinking": "...", "result": {"insights": ["Doanh thu tăng 12%"]}},
    "presentation": {"thinking": "...", "result": {"layout": "kpi-first", "palette": "ocean"}},
    "skill": {"thinking": "...", "result": {"is_reusable": True, "skill_name": "calc_x"}},
}

TASKS = {"insights": "a", "presentation": "b", "skill": "c"}


def _run():
    with patch("app.ai.pool.call_ai", return_value=MODEL_RESPONSE):
        return harness.batch_tasks("ctx", TASKS)


def test_envelope_is_stripped_exactly_once():
    """REGRESSION: the caller unwrapped a second time and silently lost all
    three post-processing features."""
    out = _run()
    assert out["presentation"] == {"layout": "kpi-first", "palette": "ocean"}
    assert out["insights"] == {"insights": ["Doanh thu tăng 12%"]}
    assert out["skill"]["is_reusable"] is True
    # The envelope must be gone, not nested one level deeper.
    assert "result" not in out["presentation"]
    assert "thinking" not in out["presentation"]


def test_caller_access_pattern_yields_real_values():
    """The exact expressions code_interpreter uses, so a future refactor that
    re-introduces double unwrapping fails here instead of in production."""
    out = _run()
    presentation = out.get("presentation") or {}
    insights = (out.get("insights") or {}).get("insights", [])
    skill = out.get("skill") or {}

    assert presentation.get("layout") == "kpi-first"
    assert insights == ["Doanh thu tăng 12%"]
    assert skill.get("skill_name") == "calc_x"


def test_missing_task_degrades_to_empty_not_crash():
    """One task the model omitted must not take the other two down."""
    partial = {"insights": {"thinking": "x", "result": {"insights": ["ok"]}}}
    with patch("app.ai.pool.call_ai", return_value=partial):
        out = harness.batch_tasks("ctx", TASKS)
    assert out.get("insights") == {"insights": ["ok"]}
    assert (out.get("presentation") or {}).get("layout") is None


def test_per_task_schemas_reach_the_model():
    """Without them each result is an unconstrained object, which throws away
    the structure guarantee the rest of the pipeline relies on."""
    seen = {}

    def capture(prompt, schema, **kw):
        seen["schema"] = schema
        return MODEL_RESPONSE

    with patch("app.ai.pool.call_ai", side_effect=capture):
        harness.batch_tasks("ctx", TASKS, schemas={"insights": {"type": "object", "x": 1}})

    result_schema = seen["schema"]["properties"]["insights"]["properties"]["result"]
    assert result_schema == {"type": "object", "x": 1}
