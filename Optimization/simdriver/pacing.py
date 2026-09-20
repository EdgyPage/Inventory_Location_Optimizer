"""pacing.py — how long a work unit will take, from a reference run's own measurements.

## Why a dispatch order is worth anything at all

Every cell of a matrix feeds ONE pool, so the matrix wall is the worker-hours plus one
unit: whatever is dispatched last still has to finish after everything else has. On a wide,
shallow spec the units differ by more than an order of magnitude — a priced placement arm
against `fifo` measured 39-59x on the campaign — so the tail is decided by WHICH unit goes
last, not by how many there are.

Measured on the ten-cell shape before this existed:

| dispatch order                               | matrix wall |
|---|---|
| submission order (what the pool does bare)   | 5.57 h |
| longest-first jobs                           | 5.37 h |
| ...and heavy cells set up first              | 5.17 h |
| the floor (worker-hours + the longest unit)  | 3.40 h |

So the two levers together are worth ~7%, and ordering the CELLS is the bigger half. That
half is only legal because `cells.cell_scope` made setup order result-neutral: a cell's
CONFIG writes are applied and restored by rebinding, so setting cell 7 up before cell 1
cannot change what either produces.

## Why this is OPT-IN

A weight is a guess about the future taken from a different run. If the reference is
mismatched — a different catalogue size, a different machine, a different era — the guess
is wrong and the order is worse than submission order, which at least follows the spec a
reader is looking at. `--pace-from` makes that a decision with a named source rather than a
default nobody chose. It changes no byte of any result: the pool dispatches the same jobs,
runs the same workers and writes the same files, in a different order.

## What it reads

`runtime_metrics` rows — `(cell, pair, config, channel, arm)` and the spans beside them —
through `load_rows`, the one reader. No path is joined here and no declared artifact is
named, in code or in prose: the run-tree ratchet counts every hand-written copy of a
contract path and a new file has no baseline to be measured against.

## The two things that are easy to get wrong

**`total_s` is the UNIT loop's wall, recorded on BOTH leaves of a coupled unit.** Summing
it double-counts a coupled unit by exactly 2x and would put every coupled unit first on a
matrix that mixes them with single leaves. It is `max` over a unit's rows. `precomp_s` is
measured OUTSIDE that loop and IS per leaf, so it sums.

**A resume's unit has less work left than the row describes.** A row is a FINISHED arm's
wall; a unit resuming at batch 380 of 400 has 5% of it left. The loop term is scaled by the
remaining fraction and the setup term is not, because setup is paid again in full whatever
batch the loop starts at.
"""
from __future__ import annotations

import logging

#: The identity of one measured arm, as both the rows and the payloads spell it.
_KEY = ('cell', 'pair', 'config', 'channel', 'arm')


def load_pace(run_root: str, log: logging.Logger | None = None) -> dict:
    """The pace book for one reference run — `{}` when it has nothing to say.

    Called ONCE per invocation, before the first cell submits: `load_rows` opens a SQLite
    file and a per-unit call would reopen it per unit.

    Best-effort by design. A reference run that cannot be read is a worse dispatch order,
    never a failed run, so every failure here logs and returns `{}` — which the pool reads
    as "no weights", which is submission order, which is what it did before this existed.
    """
    try:
        from Optimization.persistence import runtime_metrics
        rows = runtime_metrics.load_rows(run_root)
    except Exception as exc:                          # noqa: BLE001 - never sink a run
        if log:
            log.warning(f'  [pace] cannot read {run_root}: {exc!r}; dispatching in '
                        f'submission order')
        return {}
    book = _index(rows)
    if log:
        log.info(f'  [pace] {len(rows)} measured arm(s) from {run_root}: '
                 f'{len(book.get("exact") or {})} exact key(s), '
                 f'{len(book.get("leaf") or {})} leaf(s)')
    return book


def _mean(vals) -> float:
    vals = [v for v in vals if v is not None]
    return (sum(vals) / len(vals)) if vals else 0.0


def _index(rows) -> dict:
    """Rows -> the ladder's lookup tables, each a mean over the rows that share its key.

    Built eagerly because every table is small (one entry per arm at the widest) and
    because a lazy fold would re-scan the row list once per unit.
    """
    if not rows:
        return {}
    tables = {'exact': {}, 'arm_any_cell': {}, 'leaf': {}, 'cfg_chan': {},
              'chan_arm': {}, 'arm': {}, 'cell': {}}
    buckets = {name: {} for name in tables}

    def _put(name, key, secs):
        buckets[name].setdefault(key, []).append(secs)

    total = []
    for r in rows:
        cell, pair, cfg = r.get('cell'), r.get('pair'), r.get('config')
        chan, arm = r.get('channel'), r.get('arm')
        secs = float(r.get('total_s') or 0.0) + float(r.get('precomp_s') or 0.0)
        if secs <= 0.0:
            continue
        total.append(secs)
        _put('exact', (cell, pair, cfg, chan, arm), secs)
        _put('arm_any_cell', (pair, cfg, chan, arm), secs)
        _put('leaf', (pair, cfg, chan), secs)
        _put('cfg_chan', (cfg, chan), secs)
        _put('chan_arm', (chan, arm), secs)
        _put('arm', (arm,), secs)
        _put('cell', (cell,), secs)
    for name, bucket in buckets.items():
        tables[name] = {k: _mean(v) for k, v in bucket.items()}
    tables['all'] = _mean(total)
    return tables


#: The ladder, BEST FIRST, as `(table name, how the key is built, what the rung means)`.
#:
#: `exact` first, then the same leaf AND arm in a DIFFERENT CELL, then the leaf with any
#: arm.  That middle rung matters when pacing one spec off another — the cell names differ,
#: the leaves and arms do not — and the leaf rung is what answers a SELF-PACED resume,
#: where the only rows in the book are the arms of this very leaf that already finished.
#:
#: `cell` is LAST of the keyed rungs, deliberately.  An arm's cost varies enormously with
#: the ARM (39-59x priced against `fifo`, measured) and very little with the cell it ran in
#: — that near-invariance is what phase 2's exact-tie ranking was showing.  So the cell is
#: the least informative thing to match on, and a mean over a whole cell is barely better
#: than a mean over everything, which is the rung after it.
_LADDER = (
    ('exact', lambda c, p, cf, ch, a: (c, p, cf, ch, a),
     'this arm, in this cell'),
    ('arm_any_cell', lambda c, p, cf, ch, a: (p, cf, ch, a),
     'this arm on this leaf, in some other cell'),
    ('leaf', lambda c, p, cf, ch, a: (p, cf, ch),
     'the mean arm of this leaf'),
    ('cfg_chan', lambda c, p, cf, ch, a: (cf, ch),
     'the mean arm of this config and channel, over every pair'),
    ('chan_arm', lambda c, p, cf, ch, a: (ch, a),
     'this arm on this channel, over every leaf'),
    ('arm', lambda c, p, cf, ch, a: (a,),
     'this arm anywhere'),
    ('cell', lambda c, p, cf, ch, a: (c,),
     'the mean unit of this cell'),
)


def _leaf_estimate(book: dict, cell, pair, cfg, chan, arm) -> tuple[float, str]:
    """`(seconds, which rung answered)` for ONE leaf — 0.0 and `'none'` when nothing does."""
    for name, keyfn, why in _LADDER:
        table = book.get(name) or {}
        hit = table.get(keyfn(cell, pair, cfg, chan, arm))
        if hit:
            return float(hit), why
    if book.get('all'):
        return float(book['all']), 'the mean unit of the reference run'
    return 0.0, 'none'


def _leaves_of(payload: dict) -> list:
    """`[(pair, config, channel, arm, start_i, n_batches)]` for one unit's rows.

    READ FROM THE PAYLOAD, never by slicing the uid. A uid's four slots mean
    `(pair, config, channel, arm)` on a one-leaf unit and `(pair, 'coupled', arm_store,
    arm_ful)` on a coupled one — same arity, different meanings — so a positional read
    would file a coupled unit under a config named `coupled` with the wrong arm.

    The CHANNEL is `'store'` on a store-only layout, never `''`: that is what the group key
    carries and what `runtime_metrics` records, so a lookup that normalised it to empty
    would miss every row of every store-only run.
    """
    gks = payload.get('group_keys') or []
    arms = payload.get('arm_keys')
    if arms is None:
        one = payload.get('arm_key') or payload.get('strategy')
        arms = [one] * len(gks)
    unit_batches = payload.get('n_batches')
    leaves = payload.get('leaves') or []
    out = []
    for i, gk in enumerate(gks):
        if not (isinstance(gk, (tuple, list)) and len(gk) >= 3):
            continue
        pair, cfg, chan = gk[0], gk[1], gk[2]
        arm = arms[i] if i < len(arms) else None
        leaf = leaves[i] if i < len(leaves) else payload
        start_i = leaf.get('start_i', payload.get('start_i', 0)) or 0
        n_b = leaf.get('n_batches', unit_batches) or unit_batches or 0
        out.append((pair, cfg, chan, arm, int(start_i), int(n_b or 0)))
    return out


def weight_of(cell: str, payload: dict, book: dict) -> float:
    """This unit's estimated seconds — the pool's `Job.weight`, dispatched descending.

    `max` over the unit's leaves for the loop term and `sum` for nothing else, because
    `total_s` is the UNIT loop's wall recorded on BOTH leaves of a coupled unit: summing it
    would rate every coupled unit at twice its cost and float it to the front of a matrix
    that mixes unit kinds. (`precomp_s` really is per leaf and really does sum, but it is
    folded into each leaf's estimate here rather than tracked apart — a setup span is a few
    percent of a unit and splitting the two would buy a decimal place in a guess.)

    0.0 when the book cannot answer, which the pool reads as "no opinion" and breaks by
    submission order — the behaviour without a reference run at all.
    """
    if not book:
        return 0.0
    best = 0.0
    for pair, cfg, chan, arm, start_i, n_b in _leaves_of(payload):
        secs, _why = _leaf_estimate(book, cell, pair, cfg, chan, arm)
        if secs <= 0.0:
            continue
        # WHAT IS LEFT, not what it cost. A resumed unit starting at batch 380 of 400 has
        # 5% of the loop ahead of it; weighting it by the whole thing would dispatch the
        # nearly-finished arms first, which is the exact inverse of the point.
        if n_b > 0 and 0 < start_i < n_b:
            secs *= (n_b - start_i) / float(n_b)
        best = max(best, secs)
    return best


def weigh_jobs(cell: str, jobs, book: dict, log: logging.Logger | None = None) -> list:
    """Stamp `Job.weight` on one cell's jobs, in place, and return them.

    A no-op with an empty book, which is what keeps `--pace-from` off the default path.
    """
    if not book:
        return jobs
    for job in jobs:
        job.weight = weight_of(cell, job.payload, book)
    if log and jobs:
        top = max(jobs, key=lambda j: j.weight)
        log.info(f'  [pace] {cell}: {sum(1 for j in jobs if j.weight > 0)}/{len(jobs)} '
                 f'unit(s) weighted, heaviest {top.weight / 60.0:,.1f} min')
    return jobs


def cell_order(cells, pairs_and_units, book: dict, log=None) -> list:
    """`cells` reordered heaviest-first for SETUP, or unchanged with an empty book.

    Setting the heavy cells up first is the bigger half of the measured gain (5.37 h ->
    5.17 h on the ten-cell shape) and it is only legal because `cells.cell_scope` made setup
    order result-neutral: a cell's CONFIG writes are applied and restored by rebinding, so
    which cell is set up first cannot change what any of them produces.

    THE DESCRIPTOR IS NOT REORDERED. The run's own descriptor keeps spec order, because
    that is what every reader — the analysis, the ranking, a person reading a log — treats
    as the cells' identity. Only the loop that sets them up is reordered, and the log says
    so.

    `pairs_and_units` is `{cell_name: [payloads]}` when the driver already knows a cell's
    units, and `{}` when it does not (the usual case: units are built inside the setup loop,
    which is the thing being ordered). With `{}` the estimate falls to the book's `cell`
    rung — the weakest one, but the only one available before a unit exists, and the spread
    it captures is between cells, which is exactly the axis being sorted.
    """
    if not book or len(cells) < 2:
        return list(cells)

    def _est(cell):
        units = (pairs_and_units or {}).get(cell.name) or []
        if units:
            return sum(weight_of(cell.name, p, book) for p in units)
        return float((book.get('cell') or {}).get((cell.name,)) or book.get('all') or 0.0)

    weighted = sorted(cells, key=lambda c: (-_est(c), c.name))
    if log and [c.name for c in weighted] != [c.name for c in cells]:
        log.info('  [pace] cell SETUP order (heaviest first; run_layout keeps spec order): '
                 + ', '.join(f'{c.name}={_est(c) / 3600.0:,.1f}h' for c in weighted))
    return weighted
