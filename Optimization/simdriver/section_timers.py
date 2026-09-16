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

`SECTIONS` names the twelve spans an arm measures.  **FOUR spellings of each span coexist**,
and it is worth writing them out because three of the four look like each other:

    span        accumulator key   log-line token   result-dict key   runtime_metrics column
    sampling    'sample'          smpl=            't_sample'        smpl_s
    extraction  'extract'         extr=            't_extract'       extract_s
    bin ledger  'inv'             cons=            't_inv'           inv_s
    reorder     'reord'           reord=           't_reord'         reord_s
    fast_pick   'p1' / 'p2'       p1= / p2=        'p1_s' / 'p2_s'   p1_s / p2_s

`totals()` emits the THIRD column — the worker RESULT-DICT keys — not the DB column names.
The result dict is mapped onto columns positionally by `runtime_metrics.record_arm`
(runtime_metrics.py:186-194: `res.get('t_sample')` lands in `smpl_s`), so the two are
related only by that hand-written call.  `p1_s` / `p2_s` are the single case where the key
and the column coincide, which is exactly why calling `t_<name>` "the column" reads as
plausible and is wrong.

The tuple's ORDER is its own: it is neither the log line's (which puts `kf=` last where this
has it fifth, and prints p1/p2/db earlier still) nor the DDL's.

WHAT IS AND IS NOT A CONTRACT.  The DB column must never move — a rename would shift the
`runtime_metrics` schema id for a relabelling and break archived rows' comparability with
themselves — but this module does not name it, so that contract is enforced at
`record_arm`, not here.  What IS pinned here is the ACCUMULATOR KEY:
`Tests/calltree/test_calltree_anchors.py` checks `SECTION_MAP` against this tuple and that
every declared section has a real `timers.add(...)` site.  `COLUMNS` (badly named for
history) holds the two result-dict keys that are not simply `t_<name>`.
"""
from __future__ import annotations

import time

#: The twelve spans.  The order is this tuple's own and matches NEITHER the checkpoint log
#: line nor `runtime_metrics`' column order — the log line prints reord, build, smpl, task,
#: pre, sim, extr, cons and puts kf last (p1/p2/db come earlier still), so do not read this
#: as a rendering order.  The NAMES are not the log line's words either: `sample`, `extract`
#: and `inv` print as `smpl=`, `extr=` and `cons=`.  These are the accumulator's own keys,
#: and the only contract on them is that `COLUMNS`/`t_<name>` maps each to its
#: result-dict key (`Tests/unit/test_section_timers.py` pins the mapping, and
#: `Tests/calltree/test_calltree_anchors.py` pins them against the tracer's vocabulary).
#: `save` is here even though the caller never opens a window on it (see the module
#: docstring), so the result payload is one loop instead of eleven sections plus a case.
SECTIONS: tuple = ('reord', 'build', 'sample', 'task', 'kf', 'pre', 'sim',
                   'extract', 'inv', 'save', 'p1', 'p2')

#: Section -> its RESULT-DICT key, for the two that are not simply `t_<section>`.  NOT the
#: DB column: `runtime_metrics.record_arm` maps result-dict keys onto columns positionally
#: (`t_sample` -> `smpl_s`), and `p1_s`/`p2_s` are the one case where the two coincide.
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

    __slots__ = ('_base', '_win', '_cursor')

    def __init__(self) -> None:
        self._base = {name: 0.0 for name in SECTIONS}
        self._win = {name: 0.0 for name in SECTIONS}
        self._cursor = 0.0

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

    # ── the lap cursor ────────────────────────────────────────────────────────────

    def start(self, now: float = None) -> None:
        """Open a lap.  Called once at the top of each batch, before any `split`.

        The cursor is the TIMERS' state, not the caller's.  `_build_leaf` used to keep it as
        a seventh closure variable `_t`, and every one of the eight timing sites repeated
        `_now = time.perf_counter(); timers.add(s, _now - _t); _t = _now` -- eight chances to
        forget the advance, which silently charges the next section its own time PLUS
        everything before it.

        `now` is injectable so a test need not sleep; production passes nothing.
        """
        self._cursor = time.perf_counter() if now is None else now

    def split(self, *sections: str, now: float = None) -> float:
        """Close the lap: charge the elapsed time to EVERY named section, reopen at the same
        instant, and return the delta.

        SEVERAL SECTIONS, ONE CLOCK READ.  `build` is the sum of `sample` and `task`, so both
        of those sites charged two sections from a single `_dt` -- never two reads, which
        would have made `build` disagree with its own parts by a few microseconds per batch.
        """
        t = time.perf_counter() if now is None else now
        dt = t - self._cursor
        win = self._win
        for s in sections:
            win[s] += dt
        self._cursor = t
        return dt

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


#: The five counters the checkpoint log line prints beside the section walls.  `dur_sum`
#: accumulates batch durations and `dur_count` the batches; the other three are the
#: reorder triple (distinct SKUs, units ordered, units placed) for the window.
COUNTERS: tuple = ('reorders', 'units_ordered', 'placed', 'dur_sum', 'dur_count')


class CheckpointWindow:
    """The COUNTER half of the checkpoint window, beside `SectionTimers`' timer half.

        win = CheckpointWindow(opened_at=time.perf_counter())
        win.add('placed', batch_rp)          # inside the batch loop
        ckpt_wall = win.wall(now)            # at a checkpoint: the log line's three numbers
        ckpt_rate = win.rate(ckpt_wall)
        avg_dur   = win.avg_dur()
        win.roll(now)                        # close the window

    # -- why this one is free, and `SectionTimers` was not ---------------------------

    EVERY VALUE HERE DIES AT THE LOG LINE.  The five counters, the open instant, and the
    three numbers derived from them are consumed by exactly one reader -- the checkpoint
    `log.info(...)` f-string -- and reach no database, no result dict and no
    `runtime_metrics` column.  They are not simulation numbers, so no arrangement of them
    can move one.  `SectionTimers` could not say that: its totals are persisted.

    `Tests/unit/test_checkpoint_window.py` asserts the guarantee rather than trusting it --
    the old loose names must stay gone and the window must not reach any writer -- because
    the moment one of these values is persisted, the argument above stops holding silently.

    # -- why it is a second object and not folded into `SectionTimers` ----------------

    The two always roll together, which normally argues for one object.  They are kept
    apart because their contracts differ: a timer carries a WHOLE-ARM total that outlives
    every window and lands in a DB column; a counter here has no run total at all.  Folding
    them would give half the members a `total()` that means nothing, and would reopen the
    `SectionTimers` interface that `Tests/calltree/test_calltree_anchors.py` pins.

    # -- the arithmetic is preserved exactly -----------------------------------------

    `dur_sum` is a running sum in batch order (the original was a chain of `+=`); `avg_dur`
    keeps the original's `if dur_count else 0.0` guard; and `rate` takes the ALREADY-COMPUTED
    wall rather than re-reading the clock, because the original reused its `ckpt_wall` local
    -- a `rate()` that read the clock itself would divide by a slightly later wall than the
    one printed beside it.  `rate` deliberately does NOT guard a zero wall: the original had
    no guard, and adding one would hide a clock that failed to advance.

    Slotted and picklable, for the same reasons as `SectionTimers`.
    """

    COUNTERS = COUNTERS

    __slots__ = ('_c', '_opened')

    def __init__(self, opened_at: float) -> None:
        # `dur_sum` seeded as a float so the first `+=` cannot promote an int accumulator.
        self._c = {name: (0.0 if name == 'dur_sum' else 0) for name in COUNTERS}
        self._opened = opened_at

    def __repr__(self) -> str:
        live = ' '.join(f'{n}={self._c[n]}' for n in COUNTERS if self._c[n])
        return f'<CheckpointWindow {live or "empty"}>'

    # -- accumulate / read ------------------------------------------------------------

    def add(self, counter: str, n) -> None:
        """Add to one counter.  An undeclared name is a KeyError, never a sixth counter."""
        self._c[counter] += n

    def get(self, counter: str):
        return self._c[counter]

    # -- the log line's three derived numbers -----------------------------------------

    def wall(self, now: float) -> float:
        """Seconds since this window opened."""
        return now - self._opened

    def rate(self, wall: float) -> float:
        """Batches per second over `wall` -- the caller's already-computed wall."""
        return self._c['dur_count'] / wall

    def avg_dur(self) -> float:
        """Mean batch duration this window, or 0.0 when it timed no batch."""
        n = self._c['dur_count']
        return self._c['dur_sum'] / n if n else 0.0

    # -- close a window ---------------------------------------------------------------

    def roll(self, now: float) -> None:
        """Zero every counter and reopen the window at `now`.

        All five together, as the original's one reset block did: a partial reset would
        carry one counter into the next line and read as a spike.
        """
        c = self._c
        for name in COUNTERS:
            c[name] = 0.0 if name == 'dur_sum' else 0
        self._opened = now
