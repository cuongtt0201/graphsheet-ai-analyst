"""Quota accounting that outlives the process.

The counter this module exists for is the DAILY one. A per-minute window healing
in sixty seconds can live in memory and be lost on restart at no cost; a day's
allowance spent by 10am cannot. Most of what follows is therefore about the
numbers still being there after the process that wrote them is gone.
"""

import pytest

from app.ai import energy


@pytest.fixture
def ledger(tmp_path):
    led = energy.EnergyLedger(
        db_path=str(tmp_path / "e.db"),
        budgets={"openrouter": energy.Budget(daily_requests=50, tokens_per_minute=1000),
                 "gemini": energy.Budget()},
    )
    yield led
    led.close()


# ── persistence ─────────────────────────────────────────────────────────────

def test_the_days_spending_survives_a_restart(tmp_path):
    """THE reason this module is not a dict. The previous in-memory counter reset
    on every backend restart, so a 50/day account could be spent several times
    over in one day and the pool would never know."""
    path = str(tmp_path / "e.db")
    budgets = {"openrouter": energy.Budget(daily_requests=50)}

    first = energy.EnergyLedger(db_path=path, budgets=budgets)
    for _ in range(12):
        first.record_request("openrouter", "slot-a")
    first.close()

    reborn = energy.EnergyLedger(db_path=path, budgets=budgets)
    assert reborn.requests_today("openrouter") == 12
    reborn.close()


def test_a_fresh_database_starts_at_zero_rather_than_erroring(tmp_path):
    led = energy.EnergyLedger(db_path=str(tmp_path / "new.db"), budgets={})
    assert led.requests_today("openrouter") == 0
    led.close()


def test_yesterdays_spending_does_not_count_against_today(ledger, monkeypatch):
    monkeypatch.setattr(energy, "_today", lambda: "2026-08-08")
    for _ in range(50):
        ledger.record_request("openrouter", "slot-a")
    assert not ledger.has_headroom("openrouter", energy.CRITICAL)

    monkeypatch.setattr(energy, "_today", lambda: "2026-08-09")
    assert ledger.requests_today("openrouter") == 0
    assert ledger.has_headroom("openrouter", energy.CRITICAL)


# ── daily allowance ─────────────────────────────────────────────────────────

def test_requests_are_summed_across_every_slot_of_a_provider(ledger):
    """OpenRouter meters per ACCOUNT, so two models on one key draw from one
    purse. Counting per slot would let each model spend the full allowance."""
    ledger.record_request("openrouter", "slot-a")
    ledger.record_request("openrouter", "slot-b")
    assert ledger.requests_today("openrouter") == 2


def test_a_provider_with_no_budget_is_counted_but_never_capped(ledger):
    """Gemini's per-model daily limits vary by model and key tier. Rather than
    invent a ceiling, record the spend and let it through — visibility without a
    number we have not verified."""
    for _ in range(10_000):
        ledger.record_request("gemini", "g1")
    assert ledger.requests_today("gemini") == 10_000
    assert ledger.has_headroom("gemini", energy.BACKGROUND)


def test_an_unknown_provider_is_never_blocked(ledger):
    assert ledger.has_headroom("brand-new", energy.BACKGROUND)


def test_refund_returns_a_request_to_the_budget(ledger):
    ledger.record_request("openrouter", "slot-a")
    ledger.refund_request("openrouter", "slot-a")
    assert ledger.requests_today("openrouter") == 0


def test_a_counter_cannot_be_driven_negative(ledger):
    """Refunds are issued by routing on a path that could, in principle, run
    twice. A negative counter would read as free quota."""
    ledger.refund_request("openrouter", "slot-a")
    ledger.refund_request("openrouter", "slot-a")
    assert ledger.requests_today("openrouter") == 0


# ── priority ────────────────────────────────────────────────────────────────

def test_background_stops_first_then_normal_then_critical(ledger):
    """The whole point of priority: the last of the budget belongs to requests
    with a person waiting on them."""
    for _ in range(31):                       # 62%
        ledger.record_request("openrouter", "s")
    assert not ledger.has_headroom("openrouter", energy.BACKGROUND)
    assert ledger.has_headroom("openrouter", energy.NORMAL)
    assert ledger.has_headroom("openrouter", energy.CRITICAL)

    for _ in range(12):                       # 86%
        ledger.record_request("openrouter", "s")
    assert not ledger.has_headroom("openrouter", energy.NORMAL)
    assert ledger.has_headroom("openrouter", energy.CRITICAL)

    for _ in range(7):                        # 100%
        ledger.record_request("openrouter", "s")
    assert not ledger.has_headroom("openrouter", energy.CRITICAL)


def test_priority_floors_are_ordered_and_critical_gets_the_whole_budget():
    assert (energy.PRIORITY_FLOOR[energy.BACKGROUND]
            < energy.PRIORITY_FLOOR[energy.NORMAL]
            < energy.PRIORITY_FLOOR[energy.CRITICAL] == 1.00)


def test_an_unrecognised_priority_is_treated_as_normal(ledger):
    for _ in range(43):                       # 86% — past NORMAL, under CRITICAL
        ledger.record_request("openrouter", "s")
    assert not ledger.has_headroom("openrouter", "vip-platinum")


# ── tokens per minute ───────────────────────────────────────────────────────

def test_tokens_close_the_gate_even_while_request_count_looks_healthy(ledger):
    """The failure this catches: a few data-heavy prompts exhaust TPM while RPM
    still reads 3/14, so the pool sees room that is not there."""
    ledger.record_tokens("openrouter", "s", 900, 200)
    assert ledger.requests_today("openrouter") == 0      # no request charged
    assert not ledger.has_headroom("openrouter", energy.CRITICAL)


def test_the_token_window_reopens_after_a_minute(ledger, monkeypatch):
    import time as _t
    ledger.record_tokens("openrouter", "s", 1200, 0)
    assert not ledger.has_headroom("openrouter", energy.CRITICAL)

    later = _t.time() + 120                   # must be AFTER the window closes
    monkeypatch.setattr(energy.time, "time", lambda: later)
    assert ledger.tokens_this_minute("openrouter") == 0
    assert ledger.has_headroom("openrouter", energy.CRITICAL)


def test_providers_that_report_no_usage_contribute_nothing(ledger):
    """An estimate written here would be indistinguishable from a measured
    number later. Better a gap than a fiction."""
    ledger.record_tokens("openrouter", "s", None, None)
    assert ledger.tokens_this_minute("openrouter") == 0


def test_token_totals_are_persisted_per_slot(tmp_path):
    path = str(tmp_path / "e.db")
    led = energy.EnergyLedger(db_path=path, budgets={"gemini": energy.Budget()})
    led.record_tokens("gemini", "g:flash:#0", 100, 50)
    led.close()

    reborn = energy.EnergyLedger(db_path=path, budgets={"gemini": energy.Budget()})
    row = reborn._conn.execute(
        "SELECT tokens_in, tokens_out FROM usage WHERE slot_id = ?", ("g:flash:#0",)
    ).fetchone()
    assert row == (100, 50)
    reborn.close()


# ── snapshot ────────────────────────────────────────────────────────────────

def test_snapshot_reports_a_usable_battery_level(ledger):
    for _ in range(25):
        ledger.record_request("openrouter", "s")
    row = next(r for r in ledger.snapshot() if r["provider"] == "openrouter")

    assert row["requests_today"] == 25
    assert row["remaining"] == 25
    assert row["percent_used"] == 50.0


def test_snapshot_leaves_uncapped_providers_blank_rather_than_guessing(ledger):
    row = next(r for r in ledger.snapshot() if r["provider"] == "gemini")
    assert row["daily_cap"] is None
    assert row["remaining"] is None
    assert row["percent_used"] is None
