"""bench_pool_open.py — price one gain-evaluator pool open in seconds, at the campaign shape.

WHY THIS EXISTS (`.scratch/inbound-throughput/issues/02`, and `phase-2-campaign` 04 before it).
The instruments this repo had for the gain evaluator's pool path were the 90 s toy digest
(blind to cost by design), the meso calltree ladder (minutes, and it gave the WRONG SIGN on
the round-shared prologue: its tier carries 74 buckets against the campaign's 4,200, so it
rebuilt a per-round template almost as often as it read one -- memory
`meso-ladder-cannot-size-the-pool-prologue`), and a three-hour campaign-scale probe.  Every
attempt at the next evaluator target (`_TravelBalancedPool._aisle_best`, 72% of an open --
memory `aisle-best-is-what-a-pool-open-now-costs`) would have cost three hours to score.

WHAT IT BUILDS.  A synthetic frozen tier at the measured campaign shape -- 1,400 live aisles
x 3 height brackets x 6 bins = 25,200 bins -- through the production `freeze_tier`, with the
`test_frozen_tier.py` idiom that makes the tie-breaks real: a small set of shared x columns,
so D ties across aisles exist, and heights straddling the 96/240/inf brackets.  A load is
~12 units of mostly distinct SKUs (the campaign's mean), so nearly every unit opens a SKU run
-- the regime `_aisle_best` pays for.

WHAT IT TIMES, per pool family (travel-balanced: `rank_cartlabor`; min-labour: `rank_minlabor`,
the two the phase-2 winner pair runs):

  1. `TierSlice.aisle_buckets()` alone, split into the first-live walk + sort and the
     `_Cursor` constructions (the split re-runs the walk without the cursors -- a replica of
     `_aisle_buckets_eager`'s loop, kept beside the real one by the smoke test's assertion
     that both find the same aisles in the same order);
  2. open-and-seat through a real pool over an EAGER slice (no template store);
  3. the same over a shared TEMPLATE store (`tier.slice(excl, store)`), which is what one
     round of the gain greedy does.

and asserts that (2) and (3) emit the IDENTICAL `(aisle, x_phys, y_phys, score)` sequence, so
the bench is also a correctness check and cannot silently time two different computations.

It also reports the REPLICA WEIGHT: the traced bytes a process pays to hold the bins, the
frozen tier and the pool inputs -- what a helper (ticket 07) would hold instead of a whole
~4 GB sim worker.

USAGE
    python Tests/bench/bench_pool_open.py                  # campaign shape, 20 repeats
    python Tests/bench/bench_pool_open.py --repeats 50
    python Tests/bench/bench_pool_open.py --small          # the smoke shape, seconds

The campaign-shape timing is a JUDGEMENT instrument: machine-dependent, never a pass/fail.
The pass/fail part is `Tests/unit/test_bench_pool_open.py`, in the CLAUDE.md gate, which
asserts only non-vacuity at the small shape: same sequence both ways, at least one unit
seated, the walk replica agreeing with the real build.
"""
from __future__ import annotations

import argparse
import copy
import os
import random
import statistics
import sys
import time
import tracemalloc
from collections import defaultdict
from dataclasses import dataclass

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:                        # entry-script bootstrap (CLAUDE.md section 2)
    sys.path.insert(0, _REPO)

from Optimization.metrics.Workload import WorkloadParams          # noqa: E402
from Warehouse.picking.Pick import PickConfig                     # noqa: E402
from Warehouse.placement import Assignment_Functions as af        # noqa: E402


# ── fixtures: the minimal objects the pools read (the `test_frozen_tier.py` idiom) ────

class _Bin:
    __slots__ = ('location', 'x_phys', 'y_phys')

    def __init__(self, aid, x, y):
        self.location, self.x_phys, self.y_phys = (aid,), float(x), float(y)


class _Demand:
    __slots__ = ('relative_frequency', 'quantity_rate')

    def __init__(self, f, q):
        self.relative_frequency, self.quantity_rate = f, q


class _Order:
    __slots__ = ('sku', 'handle_var', 'demand', 'expected_labor', 'labor_cost',
                 'expected_popularity')

    def __init__(self, sku, handle_var, f, q, labor, labor_cost):
        self.sku, self.handle_var = sku, handle_var
        self.demand, self.expected_labor = _Demand(f, q), labor
        self.labor_cost, self.expected_popularity = labor_cost, f * q


class _Unit:
    __slots__ = ('order',)

    def __init__(self, order):
        self.order = order


class _Affinity:
    """The two attributes the pools read off an `AffinityStore`."""

    def __init__(self, skus, pairs):
        import numpy as np
        from scipy.sparse import csr_matrix
        self._sku_to_idx = {s: i for i, s in enumerate(sorted(skus))}
        n = len(self._sku_to_idx)
        rows, cols, vals = [], [], []
        for a, b, lift in pairs:
            ia, ib = self._sku_to_idx[a], self._sku_to_idx[b]
            rows += [ia, ib]
            cols += [ib, ia]
            vals += [lift, lift]
        self._matrix = csr_matrix((np.asarray(vals, dtype=np.float32), (rows, cols)),
                                  shape=(n, n))


def _wp():
    return WorkloadParams.from_pick_config(PickConfig(
        num_pickers=2, x_speed=3.0, y_speed=2.0, pick_intercept=15.0,
        pick_weight_coef=1.1, pick_volume_coef=1e-3, cart_swap_coef=300.0))


#: Six x positions per bracket, shared by every aisle: D ties across aisles are real.
_COLS = (0.0, 48.0, 96.0, 144.0, 192.0, 240.0)
#: One height per bracket, straddling the default 96/240/inf brackets.
_HEIGHTS = (40.0, 150.0, 300.0)


@dataclass(frozen=True)
class Shape:
    aisles: int
    bins_per_bracket: int
    units: int
    catalogue: int            # SKUs the aisle state and affinity know about

    @property
    def bins(self) -> int:
        return self.aisles * len(_HEIGHTS) * self.bins_per_bracket


#: The measured campaign shape (`phase-2-campaign` 04): 1,400 live aisles x 3 brackets x 6.
CAMPAIGN = Shape(aisles=1400, bins_per_bracket=6, units=12, catalogue=4000)
#: The gated smoke shape: every code path, milliseconds.
SMALL = Shape(aisles=12, bins_per_bracket=2, units=6, catalogue=60)


@dataclass
class Scene:
    shape: Shape
    wp: object
    bins: list
    tier: object
    excluded: set
    units: list
    affinity: object
    freq_by_sku: dict
    qty_by_sku: dict
    freq_by_idx: dict
    pick_load: dict
    vol: dict
    state_travel: dict
    state_minlabor: dict


def build_scene(shape: Shape = CAMPAIGN, seed: int = 7) -> Scene:
    """The whole scene, deterministically from `seed`."""
    rng = random.Random(seed)
    wp = _wp()
    bins = [_Bin(aid, x, y)
            for aid in range(1, shape.aisles + 1)
            for y in _HEIGHTS
            for x in rng.sample(_COLS, shape.bins_per_bracket)]
    rng.shuffle(bins)                           # appearance order is a tie-break
    tier = af.freeze_tier(bins, wp)
    # The round's exclusion: ~10% of the tier already taken, always including the first
    # appearing bin of some aisle so the filtered first-appearance order moves.
    excluded = {id(b) for b in bins if rng.random() < 0.10}
    excluded.add(id(bins[0]))

    skus = list(range(1, shape.catalogue + 1))
    load_skus = rng.sample(skus, shape.units)   # a load: mostly distinct SKUs
    orders = {s: _Order(s, 0.5 + (s % 7) * 0.2, 1.0 / (1 + s % 50), 1.0 + s % 9,
                        10.0 - (s % 5), 1.0 + 0.25 * (s % 4)) for s in skus}
    units = [_Unit(orders[s]) for s in load_skus]
    pairs = [(a, b, 1.5 + rng.random() * 3.0)
             for a, b in (rng.sample(skus, 2) for _ in range(shape.catalogue))]
    for s in load_skus:                         # each load SKU has a partner somewhere
        p = rng.choice(skus)
        if p != s:
            pairs.append((s, p, 4.0))
    affinity = _Affinity(skus, pairs)
    idx = affinity._sku_to_idx
    fbs = {s: o.demand.relative_frequency for s, o in orders.items()}
    qbs = {s: o.demand.quantity_rate for s, o in orders.items()}
    fbi = {idx[s]: fbs[s] for s in skus}
    plp = {s: 0.7 * (1 + s % 11) for s in skus}
    vol = {s: 300.0 * (1 + s % 13) for s in skus}

    aids = range(1, shape.aisles + 1)
    # Each aisle already holds a few SKUs, so co-occurrence and the ledgers are live.
    held = {a: rng.sample(skus, 3) for a in aids}
    state_travel = {
        'aisle_sku_sets': defaultdict(set, {a: set(held[a]) for a in aids}),
        'aisle_idx_sets': defaultdict(set, {a: {idx[s] for s in held[a]} for a in aids}),
        'aisle_demand_sum': defaultdict(float, {a: sum(fbs[s] for s in held[a]) for a in aids}),
        'aisle_pick_load_sum': defaultdict(float, {a: 0.5 * (a % 17) for a in aids}),
        'aisle_vol_sum': defaultdict(float, {a: 100.0 * (a % 23) for a in aids}),
    }
    mp = defaultdict(lambda: defaultdict(list))
    for a in aids:
        for s in held[a]:
            mp[a][idx[s]].append(float(rng.choice(_COLS)))
    state_minlabor = {
        'ss': defaultdict(set, {a: set(held[a]) for a in aids}),
        'ii': defaultdict(set, {a: {idx[s] for s in held[a]} for a in aids}),
        'dd': defaultdict(float, {a: sum(fbs[s] for s in held[a]) for a in aids}),
        'mp': mp,
    }
    return Scene(shape, wp, bins, tier, excluded, units, affinity, fbs, qbs, fbi, plp, vol,
                 state_travel, state_minlabor)


FAMILIES = ('travel', 'minlabor')


def open_pool(scene: Scene, family: str, cands, cart: bool = True):
    """One pool of `family` over `cands`, on a FRESH deep copy of the aisle state -- a
    virtual placement never advances the live books, and neither does a bench repeat."""
    if family == 'travel':
        st = copy.deepcopy(scene.state_travel)
        cart_arg = ((st['aisle_vol_sum'], scene.vol, 125000.0, sum(scene.freq_by_sku.values()))
                    if cart else None)
        return af._TravelBalancedPool(
            cands, scene.affinity, scene.wp, st['aisle_sku_sets'], st['aisle_idx_sets'],
            st['aisle_demand_sum'], st['aisle_pick_load_sum'], scene.pick_load,
            scene.freq_by_sku, scene.qty_by_sku, cart=cart_arg)
    if family == 'minlabor':
        st = copy.deepcopy(scene.state_minlabor)
        return af._MinLaborPool(cands, scene.affinity, scene.wp, st['ss'], st['ii'], st['dd'],
                                st['mp'], scene.freq_by_idx, scene.freq_by_sku,
                                scene.qty_by_sku, 0.5)
    raise ValueError(f'unknown pool family {family!r}; one of {FAMILIES}')


def seat(pool, units) -> list:
    """The pool's own order, one take per unit; the emitted `(aisle, x, y, score)` sequence."""
    out = []
    for u in pool.order(list(units)):
        b, score = pool.take(u)
        out.append(None if b is None else (b.location[0], b.x_phys, b.y_phys, score))
    return out


def walk_only(sl) -> list:
    """`TierSlice._aisle_buckets_eager`'s first-live walk and sort WITHOUT the `_Cursor`
    constructions -- the (a) half of the prologue split.  Returns the aisle order, which
    the smoke test checks against the real build so this replica cannot drift silently."""
    tier = sl.tier
    ids, excl = tier.ids, sl.excluded
    per_aisle: dict = {}
    firsts: dict = {}
    for (aid, m), appear in tier._bucket_appear.items():
        first = None
        for i in appear:
            if ids[i] not in excl:
                first = i
                break
        if first is not None:
            per_aisle.setdefault(aid, []).append((first, m))
            f0 = firsts.get(aid)
            if f0 is None or first < f0:
                firsts[aid] = first
    order = [aid for _f, aid in sorted((f, aid) for aid, f in firsts.items())]
    for aid in order:
        per_aisle[aid].sort()
    return order


def _time(fn, repeats: int) -> float:
    """Median seconds of `fn()` over `repeats` calls, after one warm-up call."""
    fn()
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - t0)
    return statistics.median(samples)


def measure(scene: Scene, repeats: int = 20) -> dict:
    """Every timing and the equality check, per family.  Raises if the eager and the
    template opens emit different sequences -- a bench that times two different
    computations is worse than no bench."""
    out = {'shape': scene.shape, 'bins': len(scene.bins), 'units': len(scene.units),
           'families': {}}
    sl_eager = scene.tier.slice(scene.excluded)
    prologue_s = _time(lambda: sl_eager.aisle_buckets(), repeats)
    walk_s = _time(lambda: walk_only(sl_eager), repeats)
    out['prologue_s'] = prologue_s
    out['prologue_walk_s'] = walk_s
    out['prologue_cursor_s'] = max(0.0, prologue_s - walk_s)
    out['live_aisles'] = len(sl_eager.aisle_buckets())
    for family in FAMILIES:
        eager_seq = seat(open_pool(scene, family, scene.tier.slice(scene.excluded)),
                         scene.units)
        store: dict = {}
        tmpl_seq = seat(open_pool(scene, family, scene.tier.slice(scene.excluded, store)),
                        scene.units)
        if eager_seq != tmpl_seq:
            raise AssertionError(f'{family}: the template open emitted a different sequence '
                                 f'from the eager open -- the bench would time two '
                                 f'different computations')
        # the state deep-copy is paid by the bench, not by the evaluator (which reads a
        # copy-on-write view): time it alone and subtract it from both opens.
        copy_s = _time(lambda f=family: copy.deepcopy(
            scene.state_travel if f == 'travel' else scene.state_minlabor), repeats)
        eager_s = _time(lambda f=family: seat(
            open_pool(scene, f, scene.tier.slice(scene.excluded)), scene.units), repeats)
        tmpl_s = _time(lambda f=family, st=store: seat(
            open_pool(scene, f, scene.tier.slice(scene.excluded, st)), scene.units), repeats)
        out['families'][family] = {
            'seated': sum(1 for x in eager_seq if x is not None),
            'eager_open_s': max(0.0, eager_s - copy_s),
            'template_open_s': max(0.0, tmpl_s - copy_s),
            'state_copy_s': copy_s,
            'sequence': eager_seq,
        }
    return out


def replica_bytes(shape: Shape = CAMPAIGN, seed: int = 7) -> dict:
    """Traced Python allocations to hold one scene: bins, frozen tier, pool inputs.  What a
    helper process would carry beside its interpreter, against the ~4 GB a sim worker
    peaks at (`flat-work-pool-era`)."""
    tracemalloc.start()
    try:
        scene = build_scene(shape, seed)
        cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {'bins': len(scene.bins), 'bytes': cur, 'peak_bytes': peak,
            'bytes_per_bin': cur / max(1, len(scene.bins))}


def _fmt_ms(s: float) -> str:
    return f'{s * 1e3:9.3f} ms'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--small', action='store_true', help='the smoke shape instead of the '
                                                         'campaign shape')
    ap.add_argument('--repeats', type=int, default=20)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--replica-bins', type=int, default=None, metavar='N',
                    help='also scale the replica weight to a tier of N bins (the campaign '
                         'run\'s own empties count, if you know it)')
    a = ap.parse_args(argv)
    shape = SMALL if a.small else CAMPAIGN
    scene = build_scene(shape, a.seed)
    m = measure(scene, a.repeats)
    print(f'pool open at {"small" if a.small else "campaign"} shape: {m["bins"]:,} bins, '
          f'{m["live_aisles"]:,} live aisles, {m["units"]} units a load, '
          f'median of {a.repeats}')
    print(f'  prologue  aisle_buckets() {_fmt_ms(m["prologue_s"])}'
          f'   walk+sort {_fmt_ms(m["prologue_walk_s"])}'
          f'   cursors {_fmt_ms(m["prologue_cursor_s"])}')
    for fam, r in m['families'].items():
        ratio = (r['eager_open_s'] / r['template_open_s']) if r['template_open_s'] else float('nan')
        print(f'  {fam:<9} open+seat eager {_fmt_ms(r["eager_open_s"])}'
              f'   template {_fmt_ms(r["template_open_s"])}   x{ratio:5.2f}'
              f'   seated {r["seated"]}/{m["units"]}   (state copy {_fmt_ms(r["state_copy_s"])}'
              f' subtracted)   sequence IDENTICAL')
    rep = replica_bytes(shape, a.seed)
    print(f'  replica   {rep["bytes"] / 2**20:8.1f} MiB traced for {rep["bins"]:,} bins '
          f'({rep["bytes_per_bin"]:.0f} B/bin incl. pool inputs), peak '
          f'{rep["peak_bytes"] / 2**20:.1f} MiB')
    if a.replica_bins:
        print(f'  replica   scaled to {a.replica_bins:,} bins: '
              f'~{rep["bytes_per_bin"] * a.replica_bins / 2**20:,.0f} MiB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
