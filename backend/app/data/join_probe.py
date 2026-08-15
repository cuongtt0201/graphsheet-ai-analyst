"""Measure every merge a generated snippet performs, wherever it runs.

The dashboard path proposes joins explicitly, so it can inspect them before
pandas runs. The chat path cannot: there the model writes `pd.merge(...)` inside
a pandas snippet, and until now that merge went completely unguarded — the same
operation, protected on one route and unprotected on the other.

This closes that gap by measuring at the moment of the merge, when the real
frames and the resolved keys are in hand. It only MEASURES; the thresholds and
the wording stay in join_guard, so the two routes cannot drift into disagreeing
about the same join.

The source of `install()` is also injected into the sandbox container, which has
pandas but none of this application's code — see sandbox.py. That is why this
module imports nothing but pandas and keeps everything inside one function.
"""

import pandas as pd


def install(pd_module, measure, sink: list, names=None):
    """Patch merge so each call appends one measurement to `sink`.

    Returns a callable that undoes the patch. `measure` is passed in rather than
    imported so the injected copy inside the container can hand over its own.

    `names` maps id(frame) -> sheet name, used to say WHICH table was joined.
    It resolves only when the model merges a whole input frame; a slice such as
    `df_cailay[['a','b']]` is a new object with a new id, so those fall back to a
    positional label. Guessing the name from column overlap was considered and
    rejected — a warning that names the wrong sheet is worse than one that names
    none.
    """
    orig_fn = pd_module.merge
    orig_meth = pd_module.DataFrame.merge

    def _keys(kwargs):
        """Resolve the pair of key columns, or None when there is nothing single
        to measure. Multi-column and index joins are skipped rather than guessed
        at — a wrong warning would cost more trust than a missing one."""
        on = kwargs.get("on")
        left_on, right_on = kwargs.get("left_on"), kwargs.get("right_on")
        if on is not None:
            key = on
            if isinstance(key, (list, tuple)):
                if len(key) != 1:
                    return None
                key = key[0]
            return (key, key) if isinstance(key, str) else None
        if isinstance(left_on, str) and isinstance(right_on, str):
            return (left_on, right_on)
        return None

    def _record(left, right, kwargs):
        try:
            if not isinstance(left, pd_module.DataFrame) or not isinstance(right, pd_module.DataFrame):
                return
            keys = _keys(kwargs)
            if keys is None:
                return
            lcol, rcol = keys
            if lcol not in left.columns or rcol not in right.columns:
                return
            m = measure(left, right, lcol, rcol)
            if m:
                m["left_rows"] = int(len(left))
                m["right_rows"] = int(len(right))
                if names:
                    known = names.get(id(right))
                    if known:
                        m["right_name"] = known
                sink.append(m)
        except Exception:
            # A guard that breaks the snippet is worse than the problem it
            # guards against — this is the one rule the whole module obeys.
            pass

    def merge_fn(left, right, *args, **kwargs):
        _record(left, right, kwargs)
        return orig_fn(left, right, *args, **kwargs)

    def merge_meth(self, right, *args, **kwargs):
        _record(self, right, kwargs)
        return orig_meth(self, right, *args, **kwargs)

    pd_module.merge = merge_fn
    pd_module.DataFrame.merge = merge_meth

    def uninstall():
        pd_module.merge = orig_fn
        pd_module.DataFrame.merge = orig_meth

    return uninstall


def judge_all(measurements: list) -> dict:
    """Verdict over every merge a snippet performed.

    Names are deliberately positional. The right-hand frame is usually a slice
    or an intermediate ("df_cailay[['a','b']]"), so there is no reliable name to
    quote — and the warning's value is the structural fact plus the key column,
    not the label.
    """
    from app.data.join_guard import judge_join

    warnings: list[str] = []
    non_additive: list[str] = []
    for i, m in enumerate(measurements or [], start=1):
        # Both message templates already supply the word "bảng", so the label
        # must not repeat it — `Bảng "bảng được ghép vào"` was the first draft.
        label = m.get("right_name") or (f"thứ {i}" if len(measurements) > 1 else "vừa ghép")
        verdict = judge_join(m, label)
        warnings.extend(verdict["warnings"])
        non_additive.extend(verdict["non_additive"])
    # Order-preserving de-duplication: two merges on the same coarse table would
    # otherwise repeat the identical sentence.
    seen: set = set()
    unique = [w for w in warnings if not (w in seen or seen.add(w))]
    return {"warnings": unique, "non_additive": sorted(set(non_additive))}
