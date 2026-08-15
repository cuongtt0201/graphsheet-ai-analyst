"""The chat path's merge guard.

The dashboard path proposes joins explicitly and has always inspected them. The
chat path lets the model write `pd.merge(...)` inside a snippet, and that merge
ran completely unguarded — the same operation, protected on one route and not on
the other. These tests pin the newer half.

Neither failure mode raises anything in pandas. That is the whole problem: the
totals come back looking perfectly ordinary.
"""

import pandas as pd
import pytest

from app.agent import sandbox
from app.data.join_guard import measure_join
from app.data.join_probe import install, judge_all


@pytest.fixture(autouse=True)
def clean_probe():
    sandbox.join_measurements.set(None)
    yield
    sandbox.join_measurements.set(None)


# One order table (many rows per product) and the two shapes it gets joined to.
ORDERS = pd.DataFrame({"sp": ["A", "A", "A", "B", "B"], "tien": [10, 20, 30, 40, 50]})
PRICES = pd.DataFrame({"sp": ["A", "B"], "don_gia": [100, 200]})       # coarser
LOTS = pd.DataFrame({"sp": ["A", "A", "B"], "lo": ["L1", "L2", "L3"]})  # repeating key
CUSTOMERS = pd.DataFrame({"sp": ["A", "B"], "ten": ["X", "Y"]})         # no measures


def _run(code, dfs):
    return sandbox.run_pandas(code, dfs)


# ── the two failure modes, end to end through run_pandas ────────────────────

def test_broadcast_is_reported_and_the_columns_are_named():
    """A price table joined onto orders repeats don_gia across every order line.
    Nothing multiplies, no row looks wrong, and SUM(don_gia) is now nonsense."""
    r = _run('result = pd.merge(don, gia, on="sp")', {"don": ORDERS, "gia": PRICES})

    assert r["ok"]
    assert r["non_additive"] == ["don_gia"]
    assert any("LẶP LẠI" in w for w in r["join_warnings"])


def test_fanout_is_reported():
    """A repeating right key duplicates the LEFT table's own rows — the measures
    the user trusts most are the ones that inflate."""
    r = _run('result = pd.merge(don, lo, on="sp")', {"don": ORDERS, "lo": LOTS})

    assert r["ok"]
    assert any("BỊ LẶP" in w for w in r["join_warnings"])


def test_a_clean_lookup_join_says_nothing():
    """A dimension table with no numeric columns cannot inflate any total, so
    warning about it would only teach the user to ignore warnings."""
    r = _run('result = pd.merge(don, kh, on="sp")', {"don": ORDERS, "kh": CUSTOMERS})

    assert r["ok"]
    assert not r.get("join_warnings")


def test_a_snippet_that_never_merges_reports_nothing():
    r = _run('result = float(don["tien"].sum())', {"don": ORDERS})
    assert r["ok"] and not r.get("join_warnings")


def test_warnings_do_not_leak_into_the_next_run():
    """REGRESSION risk: measurements live in a ContextVar shared by the process.
    A later question that merges nothing must not inherit the earlier verdict."""
    first = _run('result = pd.merge(don, gia, on="sp")', {"don": ORDERS, "gia": PRICES})
    assert first["join_warnings"]

    second = _run('result = float(don["tien"].sum())', {"don": ORDERS})
    assert not second.get("join_warnings")


def test_the_method_form_of_merge_is_caught_too():
    """`left.merge(right)` and `pd.merge(left, right)` are the same operation;
    guarding only the function form would leave the shorter spelling open."""
    r = _run('result = don.merge(gia, on="sp")', {"don": ORDERS, "gia": PRICES})
    assert r["non_additive"] == ["don_gia"]


def test_the_joined_table_is_named_when_it_is_a_known_sheet():
    r = _run('result = pd.merge(don, gia, on="sp")', {"don": ORDERS, "gia": PRICES})
    assert 'Bảng "gia"' in r["join_warnings"][0]


# ── the probe itself ────────────────────────────────────────────────────────

def test_merge_still_returns_the_right_answer_while_patched():
    """A guard that changes the result is worse than no guard."""
    sink = []
    uninstall = install(pd, measure_join, sink)
    try:
        got = pd.merge(ORDERS, PRICES, on="sp")
    finally:
        uninstall()

    assert list(got.columns) == ["sp", "tien", "don_gia"]
    assert len(got) == 5


def test_uninstall_restores_the_original_functions():
    """The patch is process-global. Leaving it installed would follow every
    other request in the backend for the rest of its life."""
    before_fn, before_meth = pd.merge, pd.DataFrame.merge
    install(pd, measure_join, [])()
    assert pd.merge is before_fn
    assert pd.DataFrame.merge is before_meth


def test_multi_column_keys_are_skipped_rather_than_guessed():
    """Measuring a composite key properly needs tuple keys; a wrong warning
    costs more trust than a missing one."""
    left = pd.DataFrame({"a": [1, 1], "b": [1, 2], "v": [10, 20]})
    right = pd.DataFrame({"a": [1], "b": [1], "w": [5]})
    sink = []
    uninstall = install(pd, measure_join, sink)
    try:
        pd.merge(left, right, on=["a", "b"])
    finally:
        uninstall()
    assert sink == []


def test_a_broken_measure_never_breaks_the_merge():
    """The one rule the guard obeys: it may fail, the user's question may not."""
    def exploding(*a, **k):
        raise RuntimeError("boom")

    sink = []
    uninstall = install(pd, exploding, sink)
    try:
        got = pd.merge(ORDERS, PRICES, on="sp")
    finally:
        uninstall()
    assert len(got) == 5


def test_repeated_identical_warnings_are_collapsed():
    m = {"right_col": "sp", "r_valid": 2, "r_distinct": 2, "matched_valid": 5,
         "matched_distinct": 2, "right_measures": ["don_gia"], "right_name": "gia"}
    out = judge_all([m, dict(m)])
    assert len(out["warnings"]) == 1


# ── the two execution tiers must measure the same thing ─────────────────────

def test_the_container_gets_the_real_functions_not_a_copy():
    """The sandbox image has pandas but none of this app's code, so the probe
    travels with the job as source. Shipping the ACTUAL source — rather than a
    hand-written twin — is what stops the two tiers from drifting apart."""
    src = sandbox._probe_source()

    assert "def measure_join" in src
    assert "def _numeric_columns" in src
    assert "def install" in src
    # It must stand alone in an image that only has pandas.
    assert "from app." not in src
    assert "import app" not in src


def test_the_injected_source_actually_runs_and_agrees_with_the_host():
    """Executes the shipped source in a bare namespace — what the container
    does — and checks it measures the same join identically."""
    ns: dict = {}
    exec(compile(sandbox._probe_source(), "<probe>", "exec"), ns)  # noqa: S102

    sink: list = []
    uninstall = ns["install"](pd, ns["measure_join"], sink)
    try:
        pd.merge(ORDERS, PRICES, on="sp")
    finally:
        uninstall()

    assert len(sink) == 1
    host = measure_join(ORDERS, PRICES, "sp", "sp")
    for key, value in host.items():
        assert sink[0][key] == value


def test_annotations_are_evaluated_before_pandas_is_imported():
    """REGRESSION: the injected functions annotate parameters as pd.DataFrame,
    and an annotation runs when the `def` runs. Without pandas imported FIRST in
    the shipped source, the container failed at definition time."""
    assert sandbox._probe_source().lstrip().startswith("import pandas as pd")
