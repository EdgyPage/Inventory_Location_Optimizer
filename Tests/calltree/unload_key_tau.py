"""unload_key_tau.py -- does a per-trailer O(T) KEY rank the yard the way `gain` does?

W8 stage 3 asked for an "unload metric that measures the value of trailers" cheaply enough to
avoid the cubic greedy (`Inbound.gain.plan_order` makes T(T+1) `place_load` calls per drain,
956 s of a 1,373 s run at campaign scale). `Inbound/priorities.py` already accepts a pure KEY
entry -- `(candidate, ctx) -> float` -- so the question was whether a sum over the load, with no
pool, no SpaceView and no bundle, reproduces the plan's order. The plan said: validate by
Kendall tau against `gain` before persisting anything. This is that validation, kept because
the last width instrument that was not kept had to be rebuilt.

MEASURED 2026-09-18 (`--skus 200000 --min-catalogue 400000`, the campaign catalogue, ten
batches, `gain_forecast`, receiving crew 4, uncoupled), 18 drains at yard depths 3-7:

    key      exact  top-1   tau(n>=3) mean / median
    labor    0/18   9/18    +0.03 / +0.20
    pop      0/18   4/18    -0.04 /  0.00
    units    0/18   4/18    -0.05 / -0.07
    fifo     0/18   5/18    +0.01 /  0.00

REFUTED. Labour mass aboard is the best of the four and it is a weak prior, not a substitute:
half the top picks at depths where chance is a quarter, and no drain reproduced. `gain`'s order
is dominated by the SPACE each load takes and leaves for the others -- the contention term --
which no per-trailer sum can see; ticket 04's fidelity ladder already found that dropping
contention (its L1 rung) moves tau to 0.58-0.94, and a key drops space entirely. The persisted
table the plan sketched is therefore NOT built: it would record a number that does not rank
trailers the way the evaluator does. If the cubic greedy must go, the plan's own fallback
stands -- an L2-style pool-free, contention-aware rung -- and that is an evaluator, not a key.

At 5k and 40k SKUs the yard never holds more than two trailers (18 plan_order calls per run,
depth 1-2), so the question cannot be asked there; contention is a property of the catalogue's
arrival rate against four doors.

The wrapper returns `gain`'s order untouched: observation only.

Usage:  python Tests/calltree/unload_key_tau.py --skus 200000 --min-catalogue 400000
        python Tests/calltree/unload_key_tau.py --skus 5000            # depth 1: nothing to rank
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, '..', '..'))
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

KEYS = ('labor', 'pop', 'units', 'fifo')


def _units(trailer) -> list:
    pend = trailer.pending
    return [it.unit for it in pend[trailer.taken:]] if pend is not None else []


def keys_of(trailer) -> dict:
    """The four candidate keys for one trailer.  Higher = unload first.

    labor  Σ order.expected_labor over units aboard (freq·qty·labor_cost, the labour mass)
    pop    Σ order.expected_popularity (freq·qty, the demand mass)
    units  units aboard
    fifo   -arrived_s, the order-blind control the campaign already carries
    """
    us = _units(trailer)
    return {'labor': sum(u.order.expected_labor for u in us),
            'pop': sum(u.order.expected_popularity for u in us),
            'units': sum(u.quantity for u in us),
            'fifo': -(trailer.arrived_s if trailer.arrived_s is not None else 0.0)}


def key_order(drain: dict, name: str) -> list:
    """The key's ranking of one drain's candidates: descending value, then seq (FIFO)."""
    return sorted(drain['seqs'], key=lambda s: (-drain['keys'][s][name], s))


def score(drains: list, name: str) -> dict:
    """{'n', 'exact', 'top1', 'taus'} for one key over every drain with >= 2 candidates."""
    from scipy.stats import kendalltau
    n = exact = top1 = 0
    taus: list = []
    for d in drains:
        seqs = d['seqs']
        if len(seqs) < 2:
            continue
        n += 1
        order = key_order(d, name)
        gain_order = sorted(seqs, key=lambda s: d['gain_pos'][s])
        exact += order == gain_order
        top1 += order[0] == gain_order[0]
        if len(seqs) >= 3:
            pos = {s: i for i, s in enumerate(order)}
            tau = kendalltau([pos[s] for s in seqs], [d['gain_pos'][s] for s in seqs]).statistic
            if tau == tau:                      # nan when a key is constant across the drain
                taus.append(tau)
    return {'n': n, 'exact': exact, 'top1': top1, 'taus': taus}


def capture(*, skus: int, batches: int, coupled: bool, recv_crew: int,
            min_catalogue: int | None, policy: str = 'gain_forecast') -> tuple[list, list]:
    """Run the inbound ladder's own workload with `plan_order` wrapped; return
    `(drains, depths)` -- one drain dict per call with >= 2 candidates, every call's depth."""
    import calltree_inbound_ladder as lad
    import calltree_scenarios as cs
    import Inbound.gain as gain
    lad._configure(policy, 5.0, recv_crew)
    drains: list = []
    depths: list = []
    _po = gain.plan_order

    def po(candidates, *a, **k):
        out = _po(candidates, *a, **k)
        depths.append(len(candidates))
        if len(candidates) >= 2:
            drains.append({'seqs': [t.seq for t in candidates],
                           'gain_pos': {t.seq: i for i, t in enumerate(out)},
                           'keys': {t.seq: keys_of(t) for t in candidates}})
        return out
    gain.plan_order = po
    try:
        cs.run_fullfid(n_batches=batches, max_skus=skus, coupled=coupled,
                       strategy='uni_rank_labor_norsl', min_catalogue=min_catalogue)
    finally:
        gain.plan_order = _po
    return drains, depths


def report(drains: list, depths: list, *, out=print) -> None:
    hist = {d: depths.count(d) for d in sorted(set(depths))}
    out(f'plan_order calls: {len(depths)}  depth histogram: {hist}')
    out(f'drains with >= 2 candidates: {len(drains)}')
    for name in KEYS:
        r = score(drains, name)
        if not r['n']:
            out(f'  {name:6s}  nothing to rank')
            continue
        taus = r['taus']
        tau_s = (f"tau(n>=3) mean {statistics.mean(taus):+.3f} median {statistics.median(taus):+.3f} "
                 f"min {min(taus):+.3f} over {len(taus)}" if taus else 'tau: no drain with >= 3')
        out(f"  {name:6s}  drains={r['n']:3d}  exact {r['exact']}/{r['n']}  "
            f"top-1 {r['top1']}/{r['n']}  {tau_s}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--skus', type=int, default=200_000)
    ap.add_argument('--batches', type=int, default=10)
    ap.add_argument('--coupled', action='store_true')
    ap.add_argument('--recv-crew', type=int, default=4)
    ap.add_argument('--min-catalogue', type=int, default=None,
                    help='bind a profile run holding at least this many SKUs (the campaign '
                         'catalogue is 400,000); without it `--skus` above the newest catalogue '
                         'silently takes everything it has')
    a = ap.parse_args(argv)
    drains, depths = capture(skus=a.skus, batches=a.batches, coupled=a.coupled,
                             recv_crew=a.recv_crew, min_catalogue=a.min_catalogue)
    report(drains, depths)
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
