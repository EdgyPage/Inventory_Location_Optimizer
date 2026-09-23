"""S08 -- how much of the picking an inbound decision can touch: the fresh-bin law.

THE MECHANISM (S03 + ADR-0003).  On the floor every line fires a lot of exactly what it took, so
a lot lands as SMALL fresh packs; the simulator drains a SKU's bins smallest-on-hand first, so a
SKU's next line is served from those fresh packs, while the declared bulk stock waits.  An
inbound decision (which bin a pack takes; in which order trailers are unloaded) can therefore
only move the cost of lines that follow an EARLIER line of the same SKU by at least the
order-to-shelf lead l.

THE CLOSED FORM.  Lines of SKU s at Poisson rate lambda over a window of H days: a line at time t
is served from an inbound bin iff some line fell in [0, t - l], so

    E[served lines] = integral_l^H lambda (1 - e^{-lambda (t - l)}) dt
                    = lambda (H - l) - (1 - e^{-lambda (H - l)})

and the section's served share is sum_s E[served]_s / sum_s lambda_s H.  Also computed EXACTLY
from the window's script (each SKU's realised line batches), and measured by
`run_unload_ranking.inbound_repick` (units picked from reorder-placed bins / units picked).

    python .scratch/aisle-churn/assets/s08_churn.py <run_root> <cell> <lo> <hi> [<lead_days>]
"""
from __future__ import annotations

import glob
import math
import os
import sys
from collections import defaultdict

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def served_closed_form(lam: float, H: float, ell: float) -> float:
    """UNCONDITIONAL: lines at Poisson rate `lam` -- for a PREDICTION, where the rate is known
    and the count is not.  Summed over every SKU, those that draw no line included."""
    x = lam * max(0.0, H - ell)
    return x - (1.0 - math.exp(-x))


def served_given_count(n: int, H: float, ell: float) -> float:
    """CONDITIONAL on the window holding exactly `n` lines of the SKU -- for a RETRODICTION.
    Given n, the line times are n uniform points on [0, H]; the j-th (in order) is served from
    an inbound bin iff its gap to the first is at least `ell`, and that gap is H times a
    Beta(j - 1, n - j + 2) variable, so

        E[served | n] = sum_{j=2}^{n} (1 - I_{ell/H}(j - 1, n - j + 2)).

    The unconditional form with rate n / H is NOT this: it credits a SKU drawn once with
    served lines, and the convexity of the rate's effect makes plugging in a realised rate
    biased upward (S08, first read: 36-45% against a measured 9-24%)."""
    from scipy.special import betainc
    if n < 2 or ell >= H:
        return 0.0
    x = ell / H
    return float(sum(1.0 - betainc(j - 1, n - j + 2, x) for j in range(2, n + 1)))


def served_exact(batches: list, ell: float) -> int:
    """Lines with an earlier line of the same SKU at least `ell` batches before."""
    n = 0
    for i, b in enumerate(batches):
        if any(b - a >= ell for a in batches[:i]):
            n += 1
    return n


def main(root, cell, lo, hi, ell=2.77):
    from Optimization.persistence.Picking_Data import (find_run, load_bin_placements,
                                                      load_pick_bins)
    from Optimization.run_unload_ranking import inbound_repick
    from Optimization.simdriver.batch_precompute import read_batches_blob
    H = hi - lo
    for db in sorted(glob.glob(os.path.join(root, cell, '*', '*', '*', 'sim_*.db'))):
        if db.endswith('keyframes.db'):
            continue
        parts = os.path.normpath(db).split(os.sep)
        arm, channel = os.path.basename(db)[4:-3], parts[-2]
        pair_dir = os.path.join(*parts[:-3]) if os.path.isabs(db) is False else os.path.dirname(
            os.path.dirname(os.path.dirname(db)))
        run_id = find_run(db, arm)
        picks = [p for p in load_pick_bins(db, run_id) if lo <= int(p['batch_id']) < hi]
        places = [p for p in load_bin_placements(db, run_id)
                  if p.get('cause') != 'reorder' or lo <= int(p['batch_id']) < hi]
        meas = inbound_repick(picks, places)
        # the window's realised lines per SKU, from the batch script this channel drew
        picked_skus = {p['sku'] for p in picks}
        script = None
        for pkl in glob.glob(os.path.join(pair_dir, '_batches_*.pkl')):
            blob = read_batches_blob(pkl)
            if blob and blob[1] and (set(blob[1][0].items) & picked_skus):
                script = blob[1]
        if script is None:
            continue
        when = defaultdict(list)
        for j, b in enumerate(script[lo:hi]):
            for s in b.items:
                when[s].append(j)
        total = sum(len(v) for v in when.values())
        ex = sum(served_exact(v, ell) for v in when.values())
        cf = sum(served_given_count(len(v), H, ell) for v in when.values())
        print(f'{channel:12s} {arm:28s} lines {total:7,d}  served share: measured(units) '
              f'{meas.get("served_share", float("nan")):6.2%}  exact-from-script(lines) '
              f'{ex / total:6.2%}  closed form {cf / total:6.2%}  |  re-picked '
              f'{meas.get("repicked_share", float("nan")):6.2%}')


if __name__ == '__main__':
    a = sys.argv[1:]
    main(a[0], a[1], int(a[2]), int(a[3]), float(a[4]) if len(a) > 4 else 2.77)
