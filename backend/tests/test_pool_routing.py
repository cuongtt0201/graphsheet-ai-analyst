"""Slot routing: headroom accounting, load balancing, and session affinity.

Routing decides which (model, key) serves each request, so a mistake here does
not show up as a crash — it shows up as 429s on one key while five sit idle, or
as silently using a fraction of the quota the pool was built to unlock. That
makes it worth pinning down.
"""

import time

import pytest

from app.ai import energy, pool


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Routing state is process-global; tests must not leak into each other.

    The energy ledger is swapped for one backed by a throwaway file: it persists
    to SQLite by design, so a shared ledger would carry one test's spending into
    the next — and into the developer's real .energy.db.
    """
    pool._slot_states.clear()
    monkeypatch.setattr(
        energy, "ledger",
        energy.EnergyLedger(db_path=str(tmp_path / "energy.db"),
                            budgets={"openrouter": energy.Budget(daily_requests=50)}),
    )
    yield
    energy.ledger.close()
    pool._slot_states.clear()


def _slots(n_keys=3, models=("m1", "m2")):
    return [
        {"provider": "gemini", "model": m, "api_key": f"key{k}", "id": f"gemini:{m}:#{k}"}
        for k in range(n_keys)
        for m in models
    ]


def test_headroom_is_tracked_per_model_and_key_not_per_key():
    """REGRESSION: counting per API key capped six keys at ~84 req/min when the
    same keys serve ~450 across five models — Gemini's free-tier limits are per
    model per project. Saturating one model must not disqualify the others on
    the same key."""
    slots = _slots(n_keys=1, models=("m1", "m2"))
    for _ in range(pool.RPM_HEADROOM):
        pool._record_attempt("gemini:m1:#0", None)

    ranked = [s["id"] for s in pool._rank_slots(slots, None)]
    assert "gemini:m1:#0" not in ranked      # exhausted
    assert "gemini:m2:#0" in ranked          # same key, untouched model


def test_busy_slots_rank_below_idle_ones():
    slots = _slots(n_keys=3, models=("m1",))
    for _ in range(5):
        pool._record_attempt("gemini:m1:#0", None)

    ranked = [s["id"] for s in pool._rank_slots(slots, None)]
    assert ranked[-1] == "gemini:m1:#0"


def test_affinity_breaks_ties_but_never_beats_real_load():
    """REGRESSION: affinity was +1000 against a load term capped near -140, so
    it always won. Every thread of a parallel fan-out then chose the same slot
    and 429'd one key while the rest idled."""
    slots = _slots(n_keys=2, models=("m1",))
    pool._record_attempt("gemini:m1:#0", "S")     # slot 0 now has affinity AND load

    # Equal load -> affinity decides.
    pool._record_attempt("gemini:m1:#1", None)
    assert pool._rank_slots(slots, "S")[0]["id"] == "gemini:m1:#0"

    # Give the affine slot clearly more load -> the idle slot must win.
    for _ in range(4):
        pool._record_attempt("gemini:m1:#0", "S")
    assert pool._rank_slots(slots, "S")[0]["id"] == "gemini:m1:#1"


def test_parallel_fanout_spreads_across_keys():
    """The concrete failure mode: four concurrent calls in one session must not
    all land on the same key."""
    slots = _slots(n_keys=4, models=("m1",))
    chosen = []
    for _ in range(4):
        top = pool._rank_slots(slots, "S")[0]
        chosen.append(top["id"])
        pool._record_attempt(top["id"], "S")
    assert len(set(chosen)) == 4


def test_rpm_window_resets_after_a_minute():
    slots = _slots(n_keys=1, models=("m1",))
    for _ in range(pool.RPM_HEADROOM):
        pool._record_attempt("gemini:m1:#0", None)
    assert pool._rank_slots(slots, None) == []

    pool._slot_states["gemini:m1:#0"]["reset_at"] = time.time() - 1
    assert len(pool._rank_slots(slots, None)) == 1


def test_ranking_preserves_incoming_order_on_a_tie():
    """Tier and rotation are already encoded in the order handed in; the score
    must only reorder when load or affinity actually differ."""
    slots = _slots(n_keys=3, models=("m1",))
    assert [s["id"] for s in pool._rank_slots(slots, None)] == [s["id"] for s in slots]


def test_empty_pool_is_not_an_error():
    assert pool._rank_slots([], None) == []


def test_exhausted_pool_explains_itself(monkeypatch):
    """REGRESSION: with every slot rate-limited, nothing was attempted, so the
    error read "Every slot failed:" with an empty list — the one situation where
    the message carried no information at all. A rate-limit wall must not look
    like a model failure."""
    slots = _slots(n_keys=2, models=("m1",))
    monkeypatch.setattr(pool, "_build_slots", lambda: slots)
    for s in slots:
        for _ in range(pool.RPM_HEADROOM):
            pool._record_attempt(s["id"], None)

    with pytest.raises(pool.AllModelsFailedError) as exc:
        pool.call_ai("x", {"type": "object"}, tier="fast")

    msg = str(exc.value)
    assert "hết hạn mức" in msg
    assert "Every slot failed" not in msg


def _mixed_slots():
    """Primary (gemini) + fallback (openrouter) slots, fallback listed FIRST so
    a passing test proves the demotion, not the incoming order."""
    return [
        {"provider": "openrouter", "model": "f1", "api_key": "ok", "id": "openrouter:f1:#0"},
        {"provider": "gemini", "model": "m1", "api_key": "gk", "id": "gemini:m1:#0"},
    ]


def test_fallback_provider_never_outranks_a_primary_slot_with_headroom():
    """OpenRouter's free tier is ~50 requests per DAY against the Gemini pool's
    ~420 per MINUTE. Ranked as a peer it would win the moment a Gemini slot took
    a single request (score 0 vs -10) and burn the day's allowance on traffic
    Gemini could have served."""
    slots = _mixed_slots()
    assert [s["id"] for s in pool._rank_slots(slots, None)][0] == "gemini:m1:#0"

    # Load the Gemini slot right up to its last request: still must win.
    for _ in range(pool.RPM_HEADROOM - 1):
        pool._record_attempt("gemini:m1:#0", None, "gemini")
    assert [s["id"] for s in pool._rank_slots(slots, None)][0] == "gemini:m1:#0"


def test_fallback_is_used_once_the_primary_pool_is_spent():
    """The demotion must be an ordering penalty, not an exclusion — the whole
    point of the fallback is to answer when the primary pool cannot."""
    slots = _mixed_slots()
    for _ in range(pool.RPM_HEADROOM):
        pool._record_attempt("gemini:m1:#0", None, "gemini")

    assert [s["id"] for s in pool._rank_slots(slots, None)] == ["openrouter:f1:#0"]


def test_daily_cap_is_per_provider_not_per_slot():
    """OpenRouter meters per ACCOUNT across every model, unlike Gemini's per
    (model, key) limits. Counting per slot would let N models each spend the
    full allowance and 429 the account N-fold."""
    slots = _mixed_slots()
    # Spend the DAY's allowance without touching the per-minute window, so the
    # slot can only be dropped for the reason under test. (Going through
    # _record_attempt would blow RPM_HEADROOM first and pass for the wrong one.)
    for _ in range(50):
        energy.ledger.record_request("openrouter", "openrouter:f1:#0")

    ranked = [s["id"] for s in pool._rank_slots(slots, None, energy.CRITICAL)]
    assert "openrouter:f1:#0" not in ranked
    assert "gemini:m1:#0" in ranked          # primary untouched by the cap


def test_background_work_yields_the_last_of_the_budget_to_users():
    """A nightly distillation job and a person waiting on a chat answer used to
    spend from the same purse at the same rate. On a 50/day account one overnight
    batch could take every request before the first user of the day arrived."""
    slots = _mixed_slots()
    for _ in range(31):                       # 62% spent — past BACKGROUND's floor
        energy.ledger.record_request("openrouter", "openrouter:f1:#0")

    background = [s["id"] for s in pool._rank_slots(slots, None, energy.BACKGROUND)]
    critical = [s["id"] for s in pool._rank_slots(slots, None, energy.CRITICAL)]

    assert "openrouter:f1:#0" not in background
    assert "openrouter:f1:#0" in critical


def test_a_refused_request_is_refunded_not_charged():
    """The pool charges before calling, because a failed call still burns quota.
    Refusals the gate makes without sending anything must be given back, or the
    pool rate-limits itself against requests the provider never saw."""
    pool._record_attempt("openrouter:f1:#0", None, "openrouter")
    assert energy.ledger.requests_today("openrouter") == 1

    pool._refund_attempt("openrouter:f1:#0", "openrouter")
    assert energy.ledger.requests_today("openrouter") == 0


def test_openrouter_slots_are_absent_without_env_config(monkeypatch):
    """No key or no model list must leave the pool byte-for-byte as it was
    before this provider existed — an unconfigured fallback is not a failure."""
    monkeypatch.delenv("OPENROUTER_MODELS", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEYS", raising=False)
    assert pool._openrouter_entries() == []
    assert [s for s in pool._build_slots() if s["provider"] == "openrouter"] == []


def test_no_escalation_when_strong_slots_have_no_headroom(monkeypatch, capsys):
    """Escalating to a tier whose slots are all rate-limited just replays the
    same wall and burns another pass."""
    slots = _slots(n_keys=1, models=("gemini-2.5-flash",))
    monkeypatch.setattr(pool, "_build_slots", lambda: slots)
    for _ in range(pool.RPM_HEADROOM):
        pool._record_attempt(slots[0]["id"], None)

    with pytest.raises(pool.AllModelsFailedError):
        pool.call_ai("x", {"type": "object"}, tier="fast")

    assert "Escalating" not in capsys.readouterr().out
