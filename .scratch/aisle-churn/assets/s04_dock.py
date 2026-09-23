"""S04 -- the site dock as a daily bulk queue, and where it saturates.

The dock unloads with at most doors x door_team workers (4 x 10 on the campaign), so its
daily capacity in unload-seconds is K = doors * team * shift_s.  A day's arriving trailers
carry work W_d; what the doors cannot finish waits, so the carried work is Lindley's

    B_{d+1} = max(0, B_d + W_d - K),

and a trailer's extra yard wait is about B / K shift-days.  For normal daily work (mean mu,
sd sigma) Kingman's heavy-traffic form gives E[B] ~ sigma^2 / (2 (K - mu)): the knee is in
the VARIANCE, not only in rho = mu / K.

Measured: the realised receive seconds per batch (work_events role 'receive', both channels of
one coupled unit), the Lindley recursion run on that series, Kingman on its moments, and the
yard's mean wait (site yard_trailers).

    python .scratch/aisle-churn/assets/s04_dock.py <grid_runs.json> [<tag> ...]
"""
from __future__ import annotations

import glob
import json
import sqlite3
import statistics
import sys

DOORS, TEAM, SHIFT = 4, 10, 28_800.0


def lindley(work, K):
    B, out = 0.0, []
    for w in work:
        B = max(0.0, B + w - K)
        out.append(B)
    return out


def kingman(mu, sd, K):
    return float('inf') if mu >= K else sd * sd / (2.0 * (K - mu))


def main(book, tags=None):
    runs = json.load(open(book, encoding='utf-8'))
    K = DOORS * TEAM * SHIFT
    print(f'{"point":9s} {"rho":>6} {"cv":>5} {"Lindley E[B] h":>15} {"Kingman h":>10} '
          f'{"yard wait h":>12} {"trailers/d":>11}')
    for tag, r in runs.items():
        if tags and tag not in tags:
            continue
        per = {}
        for ch in ('store', 'fulfillment'):
            for db in glob.glob(f"{r['root']}/k1_off_fifo/*/*/{ch}/sim_uni_fifo_norsl.db"):
                con = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
                for b, s in con.execute("select batch_id, sum(duration) from work_events "
                                        "where role = 'receive' group by batch_id"):
                    per[b] = per.get(b, 0.0) + (s or 0.0)
        nb = min(r['n_batches'], 40)
        work = [per.get(b, 0.0) for b in range(nb)]
        steady = work[5:]                       # past the lead's ramp
        mu, sd = statistics.mean(steady), statistics.pstdev(steady)
        B = lindley(work, K)
        eb = statistics.mean(B[5:]) / K * SHIFT / 3600.0
        kg = kingman(mu, sd, K) / K * SHIFT / 3600.0
        site = sorted(glob.glob(f"{r['root']}/k1_off_fifo/*/_site/*.db"))
        wait = n = None
        if site:
            con = sqlite3.connect('file:' + site[0] + '?mode=ro', uri=True)
            n, wait = con.execute('select count(*), avg(staged_s - arrived_s) from '
                                  'yard_trailers').fetchone()
        print(f'{tag:9s} {mu / K:6.3f} {sd / mu:5.2f} {eb:15.2f} {kg:10.2f} '
              f'{(wait or 0) / 3600:12.2f} {(n or 0) / r["n_batches"]:11.1f}')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2:] or None)
