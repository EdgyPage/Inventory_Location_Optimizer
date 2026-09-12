"""Measure the fill-law gap's two unmeasured assumptions on a finished run.

Written for "Measure the repeat structure and the realized lead distribution"
(`.scratch/department-calibration/issues/39-measure-the-repeat-structure-and-lead-distribution.md`).
Everything it needs is already on disk: no new run, no re-simulation.  Four measurements,
each carrying the control its predecessor paid for.

  1. REPEAT STRUCTURE.  The lag-j line recurrence of the SAMPLER's own draws, read off the
     precomputed `_batches_*.pkl` -- the demand before any pick, miss or re-offer touches
     it, so the rollover contamination that made the realized line count endogenous to the
     miss rate cannot enter at all.  Scored against a within-SKU day permutation that keeps
     every realized count exactly and destroys temporal alignment only.

  2. LEAD DISTRIBUTION.  `TrailerTransit.lead_for` is a pure function of (seed, tag, seq),
     so every trailer's drawn transit is RECONSTRUCTIBLE from `yard_trailers.seq` --
     `dispatched_s = arrived_s - lead_for(seq)`, no inference from a level.  The realized
     order-to-shelf lead is then whole grid days from dispatch to `emptied_s`.

  3. THE EVENT THE FILL LAW PRICES.  On a wholly floored section the loss is carried by
     "the SKU's own previous line is still being replenished", so measure
     `P(>= 1 prior line within K days | a line)`, K ~ the stamped pmf, against three
     baselines: the record's own Poisson at the declared share; the permutation control
     (which isolates temporal dependence); and counts drawn from the share law being
     EXACTLY true pushed through the identical estimator -- the finite-window control,
     worth two thirds of the apparent movement on ticket 38.

  4. THE SCORE, in missed-share currency.  NOT a per-SKU rate substitution: 38 measured
     that and it moves the wrong way, because it zeroes the rate of every SKU a finite
     window never touched.  Instead the single multiplier on the declared rate that makes
     the record's OWN weighted prior-line probability equal the realized one, then
     `coverage.fill_rate` re-priced at that rate with levels, weights, line laws and the
     lead pmf all held at the stamp.

The rebuild gate runs first and must reproduce the record's `fill_rate` to ~0 before any
substitution is reported, exactly as 38 gated its own scorer.

Paths come from the environment, never from the source (`COMPARISON_OUTPUT_DIR` names the
runs directory; `--inventory` names the pair's `inventory.db` under the generated catalogue):

    python .scratch/department-calibration/assets/measure_repeat_and_lead.py \
        --run comparison_YYYYMMDD_HHMMSS --pair <pair> --inventory <path to inventory.db>
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import pickle
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from Optimization.simconfig import coverage as _cov                # noqa: E402
from Optimization.simconfig import staffing as _stf                # noqa: E402
from Optimization.simdriver import era_coverage as _ec             # noqa: E402
from Optimization.simdriver.sim_assets import load_run_inventory    # noqa: E402

#: The window the reference run is measured over -- days 0..19 are the ramp.
WINDOW = (20, 39)
#: Permutation / synthetic-control replicates.  The spread is reported beside every point
#: estimate, so a reader can see whether a difference is inside it.
REPS = 8


def lead_for(seq: int, seed: int, tag: int, lead_s: float, sigma: float) -> float:
    """`TrailerTransit.lead_for`, re-derived here so a finished run needs no re-simulation.

    Kept a literal transcription of `Inbound/transit.py`: the whole value of measurement 2
    is that the trailer's transit is RECONSTRUCTED rather than inferred from a level, and
    that only holds while the two expressions agree.
    """
    if sigma <= 0.0:
        return lead_s
    rng = np.random.default_rng(np.random.SeedSequence([int(seed), int(tag), int(seq)]))
    return lead_s * float(np.exp(sigma * rng.standard_normal()))


def batch_days(batch_dir: str, leaf_skus: set, lo: int, hi: int) -> list:
    """The sampler's line sets for days `lo..hi`, from a batch cache directory.

    A mixed catalogue writes one file per channel; the one whose SKUs are a subset of this
    leaf's is this leaf's.  Membership, not the filename -- the fingerprint in the name
    carries no channel.
    """
    for p in sorted(glob.glob(os.path.join(batch_dir, '_batches_*.pkl'))):
        with open(p, 'rb') as f:
            batches = pickle.load(f)['batches']
        if set().union(*[set(b.items) for b in batches]) <= leaf_skus:
            return [set(batches[d].items) for d in range(lo, hi + 1)]
    raise SystemExit('no batch cache in %s belongs to the leaf' % batch_dir)


def matrix(days: list, index: dict) -> np.ndarray:
    """`M[sku, day]` -- True where the sampler drew a line for that SKU that day."""
    m = np.zeros((len(index), len(days)), dtype=bool)
    for d, ss in enumerate(days):
        for sk in ss:
            if sk in index:
                m[index[sk], d] = True
    return m


def permute(M: np.ndarray, counts: np.ndarray, rng) -> np.ndarray:
    """Each SKU's days redrawn uniformly, its realized count kept EXACTLY.

    Conditioning on the count is what makes this the right independence baseline: it keeps
    all the per-SKU heterogeneity (so the size-bias that makes a plain marginal wrong
    survives) and removes temporal alignment alone.
    """
    P = np.zeros_like(M)
    D = M.shape[1]
    for r in range(M.shape[0]):
        P[r, rng.choice(D, size=int(counts[r]), replace=False)] = True
    return P


def lag_lift(M: np.ndarray, j: int) -> tuple:
    """`(P(line at d+j | line at d), numerator, denominator)`, pooled over the window."""
    a, b = M[:, :M.shape[1] - j], M[:, j:]
    num, den = int((a & b).sum()), int(a.sum())
    return (num / den if den else float('nan')), num, den


def prior_prob(M: np.ndarray, ks: list, pv: np.ndarray) -> float:
    """`P(>= 1 line for the same SKU in the preceding K days | a line)`, K ~ `pv`.

    A line counts only where its whole K-window lies inside the measured window: days
    before it are unobserved, and treating them as empty would bias the answer DOWN by
    exactly the busy SKUs the record gets most wrong.
    """
    D = M.shape[1]
    num = den = 0.0
    for k, w in zip(ks, pv):
        a = M[:, k:]
        prior = np.zeros_like(a)
        for j in range(1, k + 1):
            prior |= M[:, k - j:D - j]
        num += w * float((a & prior).sum())
        den += w * float(a.sum())
    return (num / den) if den else float('nan')


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', required=True,
                    help='run directory name under COMPARISON_OUTPUT_DIR')
    ap.add_argument('--pair', required=True)
    ap.add_argument('--cell', default='k1_off')
    ap.add_argument('--inventory', required=True, help="the pair's inventory.db")
    ap.add_argument('--batch-dir', default=None,
                    help='directory holding the `_batches_*.pkl` to measure. Defaults\n'
                         'to the pair directory (the run\'s own script). Point it at a\n'
                         'script drawn under a DIFFERENT sampler to re-measure the\n'
                         'repeat structure without touching the archived run.')
    ap.add_argument('--realized-missed', nargs=2, type=float, metavar=('STORE', 'FUL'),
                    default=(0.0302, 0.1044),
                    help="the run's own realized missed share per leaf, for the score")
    args = ap.parse_args()

    out = os.environ.get('COMPARISON_OUTPUT_DIR')
    if not out:
        raise SystemExit('COMPARISON_OUTPUT_DIR is not set -- see the README (.env)')
    run = os.path.join(out, args.run)
    pair_dir = os.path.join(run, args.cell, args.pair)
    batch_dir = args.batch_dir or pair_dir
    lo, hi = WINDOW
    D = hi - lo + 1

    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger('measure')

    with open(os.path.join(run, 'run_spec.json')) as f:
        spec = json.load(f)
    record = spec['staffing']['calibration'][args.pair]['coverage']
    lead = record['lead']
    stamped = _cov.transit_day_law(float(lead['lead_s']), float(lead['lead_sigma']),
                                   float(lead['day_seconds']))
    unit = float(lead['lead_unit_days'])
    pk = {int(k): float(v) for k, v in stamped['pmf'].items() if float(v) > 1e-9}
    ks = [k for k in sorted(pk) if 1 <= k < D]
    pv = np.array([pk[k] for k in ks], dtype=float)
    pv /= pv.sum()

    inv = load_run_inventory(args.inventory, limit=None)
    _mixed, specs = _ec.channel_specs(inv)
    _ec.declare_from_record(inv.orders, specs, record, log=log)
    n_decl = _ec.declared_at(record)
    realized = (dict(zip([s.name for s in specs], args.realized_missed))
                if len(specs) == 2 else {})

    # ── 2. the lead, per arm, off the SITE's own trailer table ──────────────────
    print()
    print('=== 2. the realized order-to-shelf lead ===')
    print('stamped transit_days %.4f   E[K^2] %.4f'
          % (stamped['transit_days'], sum(k * k * v for k, v in stamped['pmf'].items())))
    day_s = float(lead['day_seconds'])
    realized_pmfs: dict = {}
    for p in sorted(glob.glob(os.path.join(pair_dir, '_site', 'inbound_*.db'))):
        arm = os.path.basename(p)[len('inbound_'):-len('.db')].split('__')[0]
        con = sqlite3.connect('file:' + p + '?mode=ro', uri=True)
        rows = list(con.execute('SELECT seq, arrived_s, emptied_s FROM yard_trailers '
                                'WHERE emptied_s IS NOT NULL ORDER BY seq'))
        if not rows:
            continue
        seq = np.array([r[0] for r in rows])
        arr = np.array([r[1] for r in rows], dtype=float)
        emp = np.array([r[2] for r in rows], dtype=float)
        ld = np.array([lead_for(s, spec['seed_world'], spec['inbound_lead_tag'],
                                float(lead['lead_s']), float(lead['lead_sigma']))
                       for s in seq])
        d_disp = np.floor((arr - ld) / day_s)
        w = (d_disp >= lo) & (d_disp <= hi)
        k_draw = np.ceil(ld / day_s)[w]
        k_real = (np.floor(emp / day_s) - d_disp)[w]
        v, c = np.unique(k_real.astype(int), return_counts=True)
        realized_pmfs[arm] = {int(a): float(b) / c.sum() for a, b in zip(v, c)}
        det = ((emp - arr) / day_s)[w]
        print('  %-16s trailers %3d   drawn K %.4f (E[K^2] %.4f)   '
              'realized K %.4f (E[K^2] %.4f)   detention %.3f d (p90 %.3f)'
              % (arm, int(w.sum()), k_draw.mean(), (k_draw ** 2).mean(),
                 k_real.mean(), (k_real ** 2).mean(), det.mean(),
                 float(np.percentile(det, 90))))

    for s in specs:
        orders = _stf.regime_orders(inv.orders, s.regime)
        n = float(n_decl[s.name])
        base = _cov.fill_rate(orders, n, transit=stamped, lead_unit_days=unit)
        rec = record['final'][s.name]['fill']['fill_rate']
        print()
        print('=== %s ===' % s.name)
        print('  gate: rebuilt fill_rate %.10f   record %.10f   delta %.3e'
              % (base['fill_rate'], rec, base['fill_rate'] - rec))

        for arm, rp in sorted(realized_pmfs.items()):
            tr = {'pmf': rp, 'transit_days': sum(k * v for k, v in rp.items())}
            f = _cov.fill_rate(orders, n, transit=tr, lead_unit_days=unit)
            print('  lead re-price %-16s missed %.4f -> %.4f  (%+.4f)'
                  % (arm, base['expected_missed_share'], f['expected_missed_share'],
                     f['expected_missed_share'] - base['expected_missed_share']))

        # ── 1 + 3: the sampler's own draws ──────────────────────────────────────
        leaf = {int(c.sku) for c in orders}
        days = batch_days(batch_dir, leaf, lo, hi)
        touched = sorted(set().union(*days))
        M = matrix(days, {sk: i for i, sk in enumerate(touched)})
        counts = M.sum(axis=1)
        rng = np.random.default_rng(20260912)
        print('  lines %d over %d days   SKUs touched %d'
              % (int(M.sum()), D, len(touched)))
        for j in (1, 2, 3):
            emp_l = lag_lift(M, j)[0]
            ctl = [lag_lift(permute(M, counts, rng), j)[0] for _ in range(REPS)]
            print('    lag %d  empirical %.5f   permutation %.5f (sd %.5f)   lift %.4fx'
                  % (j, emp_l, float(np.mean(ctl)), float(np.std(ctl)),
                     emp_l / float(np.mean(ctl))))

        dd = _cov.daily_demand(orders, n)
        rate_all = np.array([(dd[c.sku] / float(c.demand.line.mean()))
                             if dd[c.sku] > 0 else 0.0 for c in orders])
        m_all = matrix(days, {int(c.sku): i for i, c in enumerate(orders)})
        emp_pp = prior_prob(m_all, ks, pv)
        perm = [prior_prob(permute(M, counts, rng), ks, pv) for _ in range(REPS)]
        p1 = 1.0 - np.exp(-rate_all)
        shr_M = [np.random.default_rng(4200 + sd).random((len(orders), D)) < p1[:, None]
                 for sd in range(3)]
        shr = [prior_prob(m, ks, pv) for m in shr_M]
        # The CONCENTRATION signature: how many distinct SKUs the window touches,
        # empirically vs under the share law being exactly true.  38 read the
        # shortfall here as affinity clustering; measuring both counts in one place
        # is what lets a later sampler version tell clustering from a draw defect.
        shr_touched = float(np.mean([int((m.sum(axis=1) > 0).sum()) for m in shr_M]))
        print('    SKUs touched: empirical %d   share-law-true control %.0f   (%+.1f%%)'
              % (len(touched), shr_touched,
                 100.0 * (len(touched) - shr_touched) / shr_touched))
        print('    P(prior line within K | a line):')
        print('      permutation control (counts kept)  %.5f  -> temporal alone %+.5f'
              % (float(np.mean(perm)), emp_pp - float(np.mean(perm))))
        print('      share-law-TRUE control             %.5f (sd %.5f)'
              % (float(np.mean(shr)), float(np.std(shr))))
        print('      EMPIRICAL                          %.5f  (x%.2f over that control)'
              % (emp_pp, emp_pp / float(np.mean(shr))))

        # ── 4. the score ────────────────────────────────────────────────────────
        def pp(mult: float) -> float:
            p = 1.0 - np.exp(-np.outer(np.array(ks, dtype=float), mult * rate_all))
            return float((pv[:, None] * p * rate_all[None, :]).sum() / rate_all.sum())

        klo, khi = 1.0, 400.0
        for _ in range(80):
            mid = 0.5 * (klo + khi)
            if pp(mid) < emp_pp:
                klo = mid
            else:
                khi = mid
        mult = 0.5 * (klo + khi)
        # A uniform scale on `lines_per_day` moves `rate_s` inside `_served_under_lead` and
        # nothing else: the weights and the units denominator scale together and cancel.
        scaled = _cov.fill_rate(orders, n * mult, transit=stamped, lead_unit_days=unit)
        print('    score: the record prices the event at %.5f, reality %.5f  (m = %.3f)'
              % (pp(1.0), emp_pp, mult))
        print('           missed share %.4f -> %.4f   realized %.4f'
              % (base['expected_missed_share'], scaled['expected_missed_share'],
                 realized.get(s.name, float('nan'))))
        if s.name in realized:
            gap = realized[s.name] - base['expected_missed_share']
            got = scaled['expected_missed_share'] - base['expected_missed_share']
            print('           explains %+.4f of %+.4f  =  %.0f%%'
                  % (got, gap, 100 * got / gap))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
