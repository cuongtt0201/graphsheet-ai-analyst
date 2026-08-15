"""Quota accounting: what the pool has spent today, and who is allowed to spend
what is left.

The pool already tracks requests per minute per (model, key). That window heals
in sixty seconds, so losing it costs nothing — which is why it lives in memory
and stays there. This module exists for the counters that DON'T heal:

  daily requests   Gemini's free tier meters per model per day; OpenRouter's per
                   ACCOUNT per day (~50). A day's allowance spent by 10am is
                   gone until midnight, so forgetting it on a backend restart is
                   not a small inaccuracy — it is the difference between "we
                   have 8 requests left" and "we have no idea".
  tokens/minute    free tiers cap tokens as well as requests. A handful of
                   data-heavy prompts can exhaust TPM while RPM still reads 3/14,
                   so the pool sees room that is not there and drives into 429s.

Priority is the other half. Every caller currently spends from the same purse at
the same rate, so a nightly memory-distillation job competes on equal terms with
a person waiting on a chat answer. When the purse runs low the background work
should be the thing that stops.

Storage is SQLite because the counters must outlive the process, and because
`sqlite3` is in the standard library — the graph memory already degrades to a
no-op when Neo4j is absent, and quota accounting that silently disappears would
be worse than none at all.
"""

import os
import sqlite3
import threading
import time
from dataclasses import dataclass

from app.config import BASE_DIR

DB_PATH = os.environ.get("ENERGY_DB_PATH", str(BASE_DIR / ".energy.db"))

# Priority classes, cheapest to protect first.
CRITICAL = "critical"      # someone is watching a spinner
NORMAL = "normal"          # ordinary pipeline work
BACKGROUND = "background"  # idle jobs, distillation, warm-up

# Fraction of a provider's daily allowance reserved for higher priorities. With
# the defaults below, BACKGROUND stops at 60% spent and NORMAL at 85%, leaving
# the last 15% for requests a person is actually waiting on.
#
# The floors are per-class rather than a single global cut-off because the
# failure they prevent is specific: on a 50/day account one overnight batch can
# take every request before the first user of the day arrives.
PRIORITY_FLOOR = {BACKGROUND: 0.60, NORMAL: 0.85, CRITICAL: 1.00}

_MINUTE = 60.0


@dataclass
class Budget:
    """One provider's limits. None means "not metered on this axis"."""
    daily_requests: int | None = None
    tokens_per_minute: int | None = None


# Free-tier ceilings. OpenRouter's is an ACCOUNT-wide 50/day (1000 once $10 of
# credits have been bought) — the single tightest limit in the pool. Gemini's
# per-model daily limits vary by model and key tier, so no blanket number is
# claimed here; leaving it None means "count it, don't cap it", which still
# gives visibility without inventing a ceiling we have not verified.
DEFAULT_BUDGETS: dict[str, Budget] = {
    "openrouter": Budget(
        daily_requests=int(os.environ.get("OPENROUTER_DAILY_CAP", "50")),
        tokens_per_minute=int(os.environ.get("OPENROUTER_TPM", "0")) or None,
    ),
    "gemini": Budget(
        daily_requests=int(os.environ.get("GEMINI_DAILY_CAP", "0")) or None,
        tokens_per_minute=int(os.environ.get("GEMINI_TPM", "0")) or None,
    ),
}


def _today() -> str:
    return time.strftime("%Y-%m-%d")


class EnergyLedger:
    """Thread-safe. The pool fans out four ways on three code paths, so every
    counter here is touched concurrently."""

    def __init__(self, db_path: str = DB_PATH, budgets: dict[str, Budget] | None = None):
        self.db_path = db_path
        self.budgets = budgets if budgets is not None else DEFAULT_BUDGETS
        self._lock = threading.Lock()
        # Token windows heal in a minute, so they stay in memory like RPM does.
        self._tokens: dict[str, dict] = {}   # provider -> {"until": ts, "tokens": int}
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS usage (
                day        TEXT NOT NULL,
                provider   TEXT NOT NULL,
                slot_id    TEXT NOT NULL,
                requests   INTEGER NOT NULL DEFAULT 0,
                tokens_in  INTEGER NOT NULL DEFAULT 0,
                tokens_out INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (day, provider, slot_id)
            )
        """)
        self._conn.commit()

    # ── writing ─────────────────────────────────────────────────────────────

    def record_request(self, provider: str, slot_id: str) -> None:
        """Charge one request. Called BEFORE the call goes out, because a failed
        call still consumed the provider's quota."""
        self._bump(provider, slot_id, requests=1)

    def refund_request(self, provider: str, slot_id: str) -> None:
        """Give back a request the gate refused before anything was sent."""
        self._bump(provider, slot_id, requests=-1)

    def record_tokens(self, provider: str, slot_id: str,
                      tokens_in: int | None, tokens_out: int | None) -> None:
        """Record what a completed call actually cost. Providers that report no
        usage contribute nothing rather than a guess — an invented number here
        would be indistinguishable from a measured one later."""
        if not tokens_in and not tokens_out:
            return
        total = (tokens_in or 0) + (tokens_out or 0)
        self._bump(provider, slot_id, tokens_in=tokens_in or 0, tokens_out=tokens_out or 0)
        with self._lock:
            w = self._tokens.setdefault(provider, {"until": 0.0, "tokens": 0})
            now = time.time()
            if now > w["until"]:
                w["until"], w["tokens"] = now + _MINUTE, 0
            w["tokens"] += total

    def _bump(self, provider: str, slot_id: str, requests: int = 0,
              tokens_in: int = 0, tokens_out: int = 0) -> None:
        with self._lock:
            self._conn.execute("""
                INSERT INTO usage (day, provider, slot_id, requests, tokens_in, tokens_out)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(day, provider, slot_id) DO UPDATE SET
                    requests   = MAX(0, requests   + excluded.requests),
                    tokens_in  = MAX(0, tokens_in  + excluded.tokens_in),
                    tokens_out = MAX(0, tokens_out + excluded.tokens_out)
            """, (_today(), provider, slot_id, requests, tokens_in, tokens_out))
            self._conn.commit()

    # ── reading ─────────────────────────────────────────────────────────────

    def requests_today(self, provider: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(requests), 0) FROM usage WHERE day = ? AND provider = ?",
                (_today(), provider),
            ).fetchone()
        return int(row[0])

    def tokens_this_minute(self, provider: str) -> int:
        with self._lock:
            w = self._tokens.get(provider)
            if not w or time.time() > w["until"]:
                return 0
            return w["tokens"]

    def has_headroom(self, provider: str, priority: str = NORMAL) -> bool:
        """May a request of this priority spend on this provider right now?

        Two independent gates: the daily allowance scaled by the caller's
        priority floor, and the per-minute token window. Either one closing is
        enough to stop the request.
        """
        budget = self.budgets.get(provider)
        if budget is None:
            return True

        if budget.daily_requests:
            floor = PRIORITY_FLOOR.get(priority, PRIORITY_FLOOR[NORMAL])
            allowed = budget.daily_requests * floor
            if self.requests_today(provider) >= allowed:
                return False

        if budget.tokens_per_minute:
            if self.tokens_this_minute(provider) >= budget.tokens_per_minute:
                return False

        return True

    def snapshot(self) -> list[dict]:
        """Battery levels, for an admin view or a health endpoint."""
        out = []
        for provider, budget in self.budgets.items():
            used = self.requests_today(provider)
            cap = budget.daily_requests
            out.append({
                "provider": provider,
                "requests_today": used,
                "daily_cap": cap,
                "remaining": (cap - used) if cap else None,
                "percent_used": round(used / cap * 100, 1) if cap else None,
                "tokens_this_minute": self.tokens_this_minute(provider),
                "tpm_cap": budget.tokens_per_minute,
            })
        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# Process-wide ledger, created on FIRST USE rather than at import.
#
# Opening the connection at import time meant that merely importing this module
# — from a test, a script, a linter — created a SQLite file on disk. Deferring it
# keeps import free of side effects; module __getattr__ runs only until someone
# assigns `ledger` directly, which is exactly what tests do when they swap in a
# throwaway ledger.
_ledger: EnergyLedger | None = None


def get_ledger() -> EnergyLedger:
    global _ledger
    if _ledger is None:
        _ledger = EnergyLedger()
    return _ledger


def __getattr__(name: str):
    if name == "ledger":
        return get_ledger()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
