"""Agnostic analysis dispatcher — the backend reasons about SHAPE, never about
business meaning.

Business domains are unbounded (retail, HR, logistics, sensors, accounting) so
any rule written in their vocabulary is wrong for some file we have not seen.
Statistical shape is bounded: a column is temporal, continuous, or categorical,
and the useful tests follow mechanically from which shapes are present.

    [Cat] × [C]   -> one-way ANOVA        -> eta²        "groups differ"
    [C]   × [C]   -> Pearson + Spearman   -> |r|         "moves together"
    [Cat] × [Cat] -> chi-square           -> Cramér's V  "depends on"
    [T]   × [C]   -> handled by trends.py (not duplicated here)

The backend emits COLD SIGNALS: which columns, which statistic, how big the
effect. It never says "Marketing takes more leave than IT" — translating a
signal into a business sentence needs world knowledge, which is the LLM's job.

Three things stop this becoming a noise machine, and all three are mandatory:

  multiplicity  Twenty columns give ~190 pairs. At 268k rows essentially every
                test reaches p < 0.05, so unfiltered output is ~190 confident-
                looking findings that are mostly chance. Benjamini-Hochberg
                controls the false-discovery rate, and effect size — not p —
                decides what is worth reporting.
  validity      Each test has preconditions (expected cell counts, group sizes,
                non-constant inputs). Failing them returns nothing rather than
                a number that looks real.
  tautology     If `Thành tiền = SL × Đơn giá`, then Pearson(Thành tiền, SL) is
                high by arithmetic, not by insight. Pairs already explained by
                data/relations.py are excluded.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Statistical power saturates long before 268k rows; sampling keeps every test
# fast while leaving effect-size estimates essentially unchanged. (More rows
# would only shrink p-values further, and p is not what we gate on.)
MAX_TEST_ROWS = 50_000

MAX_COLS_PER_KIND = 12      # pair count grows quadratically
MIN_ROWS = 30

# --- test validity preconditions (statistical, not arbitrary) ---
ANOVA_MIN_GROUPS = 2
ANOVA_MAX_GROUPS = 30
ANOVA_MIN_PER_GROUP = 5
CHI2_MAX_LEVELS = 20        # keeps the contingency table interpretable
CHI2_MIN_EXPECTED = 5.0     # standard requirement for the chi-square approximation

# --- effect-size floors (Cohen's conventions, "small" boundary) ---
MIN_ETA_SQUARED = 0.06      # medium; small (0.01) is invisible in practice
MIN_ABS_R = 0.30
MIN_CRAMERS_V = 0.20

FDR_ALPHA = 0.05
MAX_SIGNALS = 12


def classify_columns(df: pd.DataFrame, profile: dict | None = None) -> dict[str, list[str]]:
    """Split columns into temporal / continuous / categorical.

    Identifier columns are dropped: a transaction id is technically categorical
    with 268k levels, and testing it against anything produces noise. The
    profiler already tags them, so that judgement is reused rather than redone.
    """
    roles = {}
    for c in (profile or {}).get("column_profiles", []) or []:
        roles[c.get("name")] = c.get("role")

    out: dict[str, list[str]] = {"T": [], "C": [], "Cat": []}
    for col in df.columns:
        role = roles.get(col)
        if role in ("id", "mostly_empty", "text"):
            continue
        s = df[col]
        if pd.api.types.is_datetime64_any_dtype(s):
            out["T"].append(col)
        elif pd.api.types.is_numeric_dtype(s):
            if col in ("year", "month", "quarter"):
                continue
            if s.dropna().nunique() < 3:
                continue          # a flag, not a measure
            out["C"].append(col)
        else:
            n = s.dropna().nunique()
            if 1 < n <= CHI2_MAX_LEVELS * 5:
                out["Cat"].append(col)

    for k in out:
        out[k] = out[k][:MAX_COLS_PER_KIND]
    return out


def _signal(kind, cols, stat, effect, effect_name, p, n, detail) -> dict:
    return {
        "kind": kind, "columns": cols, "statistic": round(float(stat), 4),
        "effect": round(float(effect), 4), "effect_name": effect_name,
        "p": float(p), "n": int(n), "detail": detail,
    }


def _formula_pairs(formulas: list[dict] | None) -> set[frozenset]:
    """Column pairs whose correlation is arithmetic rather than empirical."""
    out = set()
    for f in formulas or []:
        for a in (f.get("left"), f.get("right")):
            if a and f.get("target"):
                out.add(frozenset((f["target"], a)))
        if f.get("left") and f.get("right"):
            out.add(frozenset((f["left"], f["right"])))
    return out


# ── [Cat] × [C] : do the groups differ? ─────────────────────────────────────

def _anova(df: pd.DataFrame, cat: str, num: str) -> dict | None:
    from scipy import stats

    sub = df[[cat, num]].dropna()
    if len(sub) < MIN_ROWS:
        return None
    groups = [g[num].to_numpy(dtype="float64") for _, g in sub.groupby(cat, observed=True)]
    groups = [g for g in groups if len(g) >= ANOVA_MIN_PER_GROUP and np.ptp(g) > 0]
    if not (ANOVA_MIN_GROUPS <= len(groups) <= ANOVA_MAX_GROUPS):
        return None
    try:
        f_stat, p = stats.f_oneway(*groups)
    except Exception:  # noqa: BLE001
        return None
    if not np.isfinite(f_stat) or not np.isfinite(p):
        return None

    # eta² = between-group variance / total variance: the share of the measure
    # explained by which group a row belongs to.
    all_vals = np.concatenate(groups)
    grand = all_vals.mean()
    ss_between = sum(len(g) * (g.mean() - grand) ** 2 for g in groups)
    ss_total = ((all_vals - grand) ** 2).sum()
    if ss_total <= 0:
        return None
    eta2 = ss_between / ss_total

    means = sorted(((float(g.mean()), len(g)) for g in groups), reverse=True)
    spread = means[0][0] / means[-1][0] if means[-1][0] else float("inf")
    return _signal(
        "cat_vs_num", [cat, num], f_stat, eta2, "eta²", p, len(all_vals),
        f"nhóm cao nhất gấp {spread:.1f}× nhóm thấp nhất" if np.isfinite(spread) else "chênh lệch lớn",
    )


# ── [C] × [C] : do they move together? ──────────────────────────────────────

def _correlation(df: pd.DataFrame, a: str, b: str) -> dict | None:
    from scipy import stats

    sub = df[[a, b]].dropna()
    if len(sub) < MIN_ROWS:
        return None
    x = sub[a].to_numpy(dtype="float64")
    y = sub[b].to_numpy(dtype="float64")
    if np.ptp(x) == 0 or np.ptp(y) == 0:
        return None
    try:
        r, p = stats.pearsonr(x, y)
        rho, p_s = stats.spearmanr(x, y)
    except Exception:  # noqa: BLE001
        return None
    if not np.isfinite(r):
        return None

    # Spearman catches monotonic relationships Pearson understates (curved but
    # consistently rising). Report whichever is stronger, and say which.
    use_spearman = np.isfinite(rho) and abs(rho) > abs(r) + 0.1
    eff, pv = (abs(rho), p_s) if use_spearman else (abs(r), p)
    direction = "đồng biến" if (rho if use_spearman else r) > 0 else "nghịch biến"
    detail = f"{direction}" + (" (phi tuyến nhưng đơn điệu)" if use_spearman else "")
    return _signal("num_vs_num", [a, b], rho if use_spearman else r, eff,
                   "|rho|" if use_spearman else "|r|", pv, len(sub), detail)


# ── [Cat] × [Cat] : are they dependent? ─────────────────────────────────────

def _chi_square(df: pd.DataFrame, a: str, b: str) -> dict | None:
    from scipy import stats

    sub = df[[a, b]].dropna()
    if len(sub) < MIN_ROWS:
        return None
    table = pd.crosstab(sub[a], sub[b])
    if table.shape[0] < 2 or table.shape[1] < 2:
        return None
    if table.shape[0] > CHI2_MAX_LEVELS or table.shape[1] > CHI2_MAX_LEVELS:
        return None
    # The chi-square approximation is invalid when expected counts are tiny;
    # reporting it anyway would dress up an unreliable number as a finding.
    if len(sub) / (table.shape[0] * table.shape[1]) < CHI2_MIN_EXPECTED:
        return None
    try:
        chi2, p, _, _ = stats.chi2_contingency(table)
    except Exception:  # noqa: BLE001
        return None
    if not np.isfinite(chi2):
        return None

    n = table.to_numpy().sum()
    k = min(table.shape) - 1
    if n <= 0 or k <= 0:
        return None
    cramers_v = np.sqrt(chi2 / (n * k))
    return _signal("cat_vs_cat", [a, b], chi2, cramers_v, "Cramér's V", p, int(n),
                   f"bảng {table.shape[0]}×{table.shape[1]}")


# ── multiplicity control ────────────────────────────────────────────────────

def _benjamini_hochberg(signals: list[dict], alpha: float = FDR_ALPHA) -> list[dict]:
    """Control the false-discovery rate across ALL tests run.

    Running ~190 tests at alpha=0.05 yields ~10 'significant' results from pure
    chance. BH keeps the expected share of false positives among reported
    findings at alpha instead of applying alpha to each test independently."""
    if not signals:
        return []
    ordered = sorted(signals, key=lambda s: s["p"])
    m = len(ordered)
    cutoff = 0
    for i, s in enumerate(ordered, start=1):
        if s["p"] <= (i / m) * alpha:
            cutoff = i
    return ordered[:cutoff]


_MIN_EFFECT = {"eta²": MIN_ETA_SQUARED, "|r|": MIN_ABS_R, "|rho|": MIN_ABS_R,
               "Cramér's V": MIN_CRAMERS_V}


def analyze(df: pd.DataFrame, profile: dict | None = None,
            formulas: list[dict] | None = None) -> list[dict]:
    """Every shape-appropriate test, filtered down to the signals worth a human
    reading. Returns [] rather than raising on any degenerate input."""
    if df is None or len(df) < MIN_ROWS:
        return []
    try:
        kinds = classify_columns(df, profile)
        sample = df.sample(MAX_TEST_ROWS, random_state=0) if len(df) > MAX_TEST_ROWS else df
        skip = _formula_pairs(formulas)

        raw: list[dict] = []
        for cat in kinds["Cat"]:
            for num in kinds["C"]:
                s = _anova(sample, cat, num)
                if s:
                    raw.append(s)

        for i, a in enumerate(kinds["C"]):
            for b in kinds["C"][i + 1:]:
                if frozenset((a, b)) in skip:
                    continue      # arithmetic, not evidence
                s = _correlation(sample, a, b)
                if s:
                    raw.append(s)

        for i, a in enumerate(kinds["Cat"]):
            for b in kinds["Cat"][i + 1:]:
                s = _chi_square(sample, a, b)
                if s:
                    raw.append(s)

        survivors = _benjamini_hochberg(raw)
        strong = [s for s in survivors if s["effect"] >= _MIN_EFFECT.get(s["effect_name"], 0.2)]
        strong.sort(key=lambda s: s["effect"], reverse=True)
        return strong[:MAX_SIGNALS]
    except Exception as exc:  # noqa: BLE001 - never break an upload over analysis
        logger.warning(f"[dispatcher] skipped: {exc}")
        return []


_KIND_TEXT = {
    "cat_vs_num": 'giá trị "{1}" khác biệt rõ giữa các nhóm của "{0}"',
    "num_vs_num": '"{0}" và "{1}" biến động cùng nhau',
    "cat_vs_cat": '"{0}" và "{1}" phụ thuộc lẫn nhau',
}


def format_signals_for_prompt(signals_by_sheet: dict[str, list[dict]] | None) -> str:
    """Cold signals for the LLM to translate. Deliberately short: handing over
    every test result invites the model to cherry-pick a story, so the backend
    ranks and cuts first and passes only what survived."""
    if not signals_by_sheet:
        return ""
    lines = ["TÍN HIỆU THỐNG KÊ (kiểm định trên dữ liệu thật, đã lọc nhiễu và so sánh bội —"
             " KHÔNG phải AI phỏng đoán):"]
    any_line = False
    for sid, signals in signals_by_sheet.items():
        if not signals:
            continue
        any_line = True
        lines.append(f'- Bảng "{sid}":')
        for s in signals:
            cols = s["columns"]
            text = _KIND_TEXT.get(s["kind"], "{0} ~ {1}").format(*cols)
            lines.append(
                f'    • {text} — {s["effect_name"]}={s["effect"]}, {s["detail"]} (n={s["n"]:,})'
            )
    if not any_line:
        return ""
    lines.append(
        "Đây là tín hiệu THÔ về hình thái dữ liệu, chưa mang nghĩa nghiệp vụ. Hãy DỊCH sang "
        "ngôn ngữ kinh doanh dựa vào tên cột, và ưu tiên đưa vào phân tích những tín hiệu có "
        "độ lớn hiệu ứng cao nhất. Nếu một tín hiệu không có ý nghĩa thực tế, được phép bỏ qua."
    )
    return "\n".join(lines)
