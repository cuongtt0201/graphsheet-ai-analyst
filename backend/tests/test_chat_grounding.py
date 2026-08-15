"""Grounding on the chat answer — the most-read output in the product.

Insights, reports, investigations and discoveries all passed their prose
through the number gate; the chat answer did not, even though it is what users
see on every single turn.

It also needs a different failure action from the others. Dropping a paragraph
from a report still leaves a report; dropping the answer leaves the user with
nothing for the question they just asked. So a violation buys one rewrite and
then degrades to the same minimal reply already used when the prose call fails
outright — the computed table with no narration, rather than narration with an
invented number.
"""

import pandas as pd
import pytest

from app.agent import chat_agent


@pytest.fixture
def dfs():
    return {"f::S1": pd.DataFrame({"kv": ["Bắc", "Nam"], "tien": [11_000_000, 9_000_000]})}


def _stub_pipeline(monkeypatch, answers, result):
    """Drive answer_question past planning and execution so only the
    interpretation step is under test."""
    calls = {"n": 0}

    def fake_call_ai(prompt, schema, tier="fast", **kw):
        # Stage 1 decides the mode; later calls are the interpretation.
        if "mode" in str(schema.get("properties", {})):
            return {"mode": "code", "code": "result = 1", "reason": "test"}
        i = min(calls["n"], len(answers) - 1)
        calls["n"] += 1
        return {"answer": answers[i], "follow_up": []}

    monkeypatch.setattr(chat_agent, "call_ai", fake_call_ai)
    monkeypatch.setattr(chat_agent, "run_pandas",
                        lambda *a, **k: {"ok": True, "kind": "table", "result": result})
    monkeypatch.setattr(chat_agent, "_load_chat_skills", lambda *a, **k: ("", "", {}))
    # Keep the investigation loop out of this test.
    monkeypatch.setattr("app.agent.investigator.should_investigate", lambda *a, **k: (False, ""))
    return calls


def test_grounded_answer_passes_through(monkeypatch, dfs):
    result = {"columns": ["kv", "tien"], "rows": [["Bắc", 11000000], ["Nam", 9000000]]}
    _stub_pipeline(monkeypatch, ["Miền Bắc đạt 11.000.000 VNĐ, Miền Nam 9.000.000 VNĐ."], result)

    reply = chat_agent.answer_question([], dfs, "doanh thu theo miền?", [])
    assert "11.000.000" in reply["answer"]


def test_invented_number_triggers_a_rewrite(monkeypatch, dfs):
    """First answer cites a number nowhere in the computed result; the retry
    fixes it and that corrected answer is what ships."""
    result = {"columns": ["kv", "tien"], "rows": [["Bắc", 11000000], ["Nam", 9000000]]}
    calls = _stub_pipeline(monkeypatch, [
        "Miền Bắc đạt 88.888.888 VNĐ.",       # invented
        "Miền Bắc đạt 11.000.000 VNĐ.",       # corrected
    ], result)

    reply = chat_agent.answer_question([], dfs, "doanh thu?", [])
    assert "11.000.000" in reply["answer"]
    assert "88.888.888" not in reply["answer"]
    assert calls["n"] >= 2                     # a rewrite really was attempted


def test_persistently_ungrounded_answer_degrades_instead_of_shipping(monkeypatch, dfs):
    """When the rewrite invents too, the answer is replaced — the user gets the
    real table and no narration, never a confident wrong number."""
    result = {"columns": ["kv", "tien"], "rows": [["Bắc", 11000000]]}
    _stub_pipeline(monkeypatch, ["Doanh thu 77.777.777 VNĐ."] * 3, result)

    reply = chat_agent.answer_question([], dfs, "doanh thu?", [])
    assert "77.777.777" not in reply["answer"]
    assert "xem bảng kết quả" in reply["answer"]
    assert reply["table"] is not None           # the computed data still ships


def test_numbers_from_the_schema_block_count_as_grounded(monkeypatch, dfs):
    """The schema already carries profiler-computed sums and ranges; citing one
    is legitimate context, not invention, and must not be flagged."""
    result = {"columns": ["kv"], "rows": [["Bắc"]]}
    _stub_pipeline(monkeypatch, ["Bảng này có tổng cộng 20.000.000 VNĐ."], result)

    profiles = [{
        "source_id": "f::S1", "row_count": 2,
        "column_profiles": [{"name": "tien", "dtype": "float64", "role": "measure",
                             "distinct": 2, "sum": 20000000}],
    }]
    reply = chat_agent.answer_question(profiles, dfs, "tổng?", [])
    assert "20.000.000" in reply["answer"]
