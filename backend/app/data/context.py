"""The single shared understanding of the uploaded data.

Everything here is computed ONCE at upload and stored in the session; this
module just renders it into the block every prompt receives. Chat, dashboard,
insights and report all call the same function, so they cannot end up reasoning
from different pictures of the same file.

That failure is not hypothetical. When the incomplete-final-period fix lived in
only one of the two paths, a single dashboard reported "+12.6% tăng trưởng" in
its commentary directly above a KPI card reading "▼83.9%" — same measure, same
data, two answers, because each path had assembled its own context.

The schema description itself is deliberately NOT unified: chat reasons over
several sheets (`dfs[...]`) while the dashboard reasons over one merged frame
(`df`), so they genuinely need different column listings. Everything that
describes what the data MEANS and what it SAYS is shared.
"""

from __future__ import annotations


def shared_understanding(state: dict, df=None, date_col: str | None = None) -> str:
    """The common context block: what the data is, what it says, and what to
    watch out for. Safe to call with a half-populated session — each section is
    skipped when its input is missing."""
    from app.data.eda import format_facts_for_prompt
    from app.data.relations import format_relations_for_prompt
    from app.data.semantics import format_semantics_for_prompt

    parts: list[str] = []

    sem = format_semantics_for_prompt(state.get("semantics"))
    if sem:
        parts.append(sem)

    # Relationships come before observations: knowing "Thành tiền = SL × Đơn
    # giá" changes how every later number should be read, including which
    # columns must never be summed.
    rel = format_relations_for_prompt(state.get("formulas"), state.get("fk_links"))
    if rel:
        parts.append(rel)

    # How the merge itself distorted the frame, if it did. Sits with the
    # relationships because it is the same kind of fact: which columns can no
    # longer be added up. The code already refuses to offer those as measures;
    # this is so the prose does not describe them as totals either.
    from app.data.join_guard import format_join_warnings

    jw = format_join_warnings(state.get("join_warnings") or [],
                              state.get("non_additive_columns") or [])
    if jw:
        parts.append(jw)

    eda = format_facts_for_prompt(state.get("eda_facts"))
    if eda:
        parts.append(eda)

    # Cold statistical signals last: they are the rawest layer and the one the
    # model must translate rather than repeat.
    from app.data.dispatcher import format_signals_for_prompt

    sig = format_signals_for_prompt(state.get("signals"))
    if sig:
        parts.append(sig)

    # Only meaningful where a concrete frame + date column is in play (the
    # dashboard's merged df). Chat spans sheets with different date columns, so
    # a single coverage warning there would be misleading.
    if df is not None and date_col:
        from app.data.trends import period_coverage_note

        note = period_coverage_note(df, date_col)
        if note:
            parts.append(note)

    return "\n\n".join(parts)
