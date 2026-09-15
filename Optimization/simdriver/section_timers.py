"""section_timers.py — the per-section wall accumulator an arm keeps while it runs.

WHAT IT REPLACES.  `strategy_runner._build_leaf` carried this as twenty-three closure
variables: eleven `t_*_ckpt` / `p*_sum_ckpt` window accumulators, the eleven `t_*_run` /
`p*_run` whole-arm totals they fold into, and `t_save_run`, which never had a window
because a checkpoint's DB write is timed inside the checkpoint block itself.  Three sites
had to name every section and stay in step — the declaration block, the checkpoint
fold-and-reset, and `_finish`'s fold of the final unflushed window.

# ── the one design decision ───────────────────────────────────────────────────────

**A TOTAL ALWAYS INCLUDES THE OPEN WINDOW.**  `roll()` closes the window; it does not
move a number anybody is waiting on.  That is the whole point, and it is chosen against a
recorded defect rather than for tidiness: a run-end fold that sits behind a condition
(`if pb:` — true only when an unflushed batch window exists, i.e. NOT when `n_batches`
divides the checkpoint cadence) discards the final window in silence, and the same class
of loss is on record against the `p1`/`p2` split, whose last window went unwritten until
the runtime_metrics column add.  Here there is nothing to forget: the total is right
before the first roll and after the last one.

`roll()` therefore exists for ONE reader — the per-checkpoint log line, which reports the
window rather than the arm — and is an observability operation, not a correctness one.

# ── float discipline ──────────────────────────────────────────────────────────────

The old code was a chain of `+=`, and an archived `runtime_metrics` row has to keep
reproducing, so the association is preserved exactly rather than approximately:

    window  = running sum of this window's deltas          (one add per `add`)
    base    = running sum of the CLOSED windows            (one add per `roll`)
    total   = base + window                                (one add, at read time)

Nothing sums a list, nothing re-associates, and `roll()` on an empty window adds 0.0 —
which is an exact identity for every float a timer can hold.  `Tests/unit/
test_section_timers.py` pins all three against a plain running sum.

# ── the vocabulary is a contract ──────────────────────────────────────────────────

`SECTIONS` names the twelve spans `runtime_metrics` has columns for.  It is NOT the
checkpoint log line's vocabulary and NOT its order: that line prints `smpl=`, `extr=` and
`cons=` for `sample`, `extract` and `inv`, and it puts `kf=` last where this tuple has it
fifth.  Three spellings of the same span therefore coexist on purpose — the accumulator's
key, the log line's token, and the DB column — and only two of the three are contracts.

The DB column is the one that must never move: `COLUMNS` holds the two that are not simply
`t_<name>` (the fast_pick phase split kept its `p1_s` / `p2_s` names when it was promoted
from per-checkpoint to whole-arm), and a renamed column would move the `runtime_metrics`
schema id for a relabelling, breaking archived rows' comparability with themselves.

`Tests/calltree/test_calltree_anchors.py` pins the vocabulary from the other side —
`SECTION_MAP` against this tuple, plus a check that every declared section has a real
`timers.add(...)` site — so renaming a section here without renaming it there fails a gate
rather than silently re-labelling an archived column.
"""
from __future__ import annotations

#: The twelve spans.  The order is this tuple's own and matches NEITHER the checkpoint log
#: line nor `runtime_metrics`' column order — the log line prints reord, build, smpl, task,
#: pre, sim, extr, cons and puts kf last (p1/p2/db come earlier still), so do not read this
#: as a rendering order.  The NAMES are not the log line's words either: `sample`, `extract`
#: and `inv` print as `smpl=`, `extr=` and `cons=`.  These are the accumulator's own keys,
#: and the only contract on them is that `COLUMNS`/`t_<name>` maps each to its
#: `runtime_metrics` column (`Tests/unit/test_section_timers.py` pins the mapping, and
#: `Tests/calltree/test_calltree_anchors.py` pins them against the tracer's vocabulary).
#: `save` is here even though the caller never opens a window on it (see the module
#: docstring), so the result payload is one loop instead of eleven sections plus a case.
SECTIONS: tuple = ('reord', 'build', 'sample', 'task', 'kf', 'pre', 'sim',
                   'extract', 'inv', 'save', 'p1', 'p2')

#: Section -> `runtime_metrics` column.  Everything not named here is `t_<section>`.
COLUMNS: dict = {'p1': 'p1_s', 'p2': 'p2_s'}


class SectionTimers:
    """Two-level accumulator: an open window, and the whole-arm total that contains it.

        st = SectionTimers()
        st.add('sim', dt)          # inside the batch loop
        st.window('sim')           # the checkpoint log line's number
        st.roll()                  # at a checkpoint: close the window
        st.totals()                # at run end: the runtime_metrics payload

    Slotted, so a mistyped attribute raises instead of shadowing a section, and picklable,
    because the leaf that owns one is built inside a spawned worker.
    """

    SECTIONS = SECTIONS
    COLUMNS = COLUMNS

    __slots__ = ('_base', '_win')

    def __init__(self) -> None:
        self._base = {name: 0.0 for name in SECTIONS}
        self._win = {name: 0.0 for name in SECTIONS}

    def __repr__(self) -> str:
        live = ' '.join(f'{n}={self.total(n):.1f}' for n in SECTIONS if self.total(n))
        return f'<SectionTimers {live or "all zero"}>'

    # ── accumulate ────────────────────────────────────────────────────────────────

    def add(self, section: str, seconds: float) -> None:
        """Add one measured span to the open window.  An undeclared section is a KeyError.

        Refused rather than created: a typo that opened a thirteenth section would
        accumulate seconds nothing ever reads and subtract them from nothing.
        """
        self._win[section] += seconds

    # ── read ──────────────────────────────────────────────────────────────────────

    def window(self, section: str) -> float:
        """This checkpoint window's seconds — the number the per-batch log line prints."""
        return self._win[section]

    def total(self, section: str) -> float:
        """Whole-arm seconds, **open window included**.  Correct at every instant."""
        return self._base[section] + self._win[section]

    # ── close a window ────────────────────────────────────────────────────────────

    def roll(self) -> None:
        """Close the open window: fold it into the base and zero it for the next one.

        Nothing depends on this having happened — `total()` already counted the window.
        Rolling an empty window is an exact no-op (`x + 0.0 is x` for every float a timer
        holds), so an extra roll cannot perturb a total.
        """
        base, win = self._base, self._win
        for name in SECTIONS:
            base[name] += win[name]
            win[name] = 0.0

    # ── the result payload ────────────────────────────────────────────────────────

    def totals(self) -> dict:
        """The `runtime_metrics` half of the worker's result dict.

        A plain `dict` of plain `float`s: it crosses the process boundary in the worker
        result, so no views, no defaultdicts, nothing that needs the class to unpickle.
        """
        return {COLUMNS.get(name, f't_{name}'): self.total(name) for name in SECTIONS}
