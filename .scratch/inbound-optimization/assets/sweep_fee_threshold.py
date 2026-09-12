"""Re-take the yard reading and the fee-threshold sweep on a finished coupled run.

Written for "Re-run the gate and fix the fee threshold"
(`.scratch/inbound-optimization/issues/29-rerun-the-gate-and-fix-the-threshold.md`).
Everything it needs is already on disk: no new run, no re-simulation.  That is not a
convenience, it is the design -- `yard_trailers` holds raw arrival/staging/emptying STAMPS
and the fee is derived at read time, so a finished run is re-reportable under any threshold
(`Optimization/Performance_Evaluations/yard/__init__.py`).

Two sections, both restricted to the measured window:

  1. THE YARD READING, in the shape "Re-verify the gate under the lead-aware record" (26)
     built it: strict contention, binding cuts, yard depth, free doors at freeze, detention
     p50/max, standing at end, trailers cleared, `recv_depth` max.

  2. THE THRESHOLD SWEEP over the window's trailer population -- for each candidate free-day
     threshold, the share of trailers accruing any overage and the total overage
     trailer-days per arm.  The knee is where a meaningful minority pays, the arms separate,
     and neither pole is saturated; `PHASE2_THRESHOLD_DAYS` is then set from the table rather
     than from a fresh search.

WHY IT IMPORTS TWO PRIVATE FRAME BUILDERS.  `frames._ydf` and `frames._ddf` are the
production derivations of `detention_days`, `overage_days` and `binding_cut`.  Re-deriving
them here would be a second definition of the fee that could drift from the one the campaign
reports, so this reads through the same functions the renderers do -- including the two
decisions that are easy to get wrong by hand: a right-CENSORED trailer (still on site at run
end) is kept at its lower bound rather than dropped, and the per-drain levels are never
summed (`binding_cut` is a boolean per drain, and its statistic is a COUNT of drains).

THE CENSORING BOUND IS THE SITE'S.  `requests._arm_end_s` closes a censored row at
`max(batch_start_time + duration)` over the arm's batch record, and at site scope that record
is BOTH leaves concatenated.  This script reproduces that union explicitly: a bound taken
from one leaf would censor the shared yard on one channel's clock.

THE WINDOW IS AN ARRIVAL WINDOW.  `yard_trailers` carries no batch column, so the population
is trailers whose `arrived_s` falls inside the measured days, whose boundaries come from
`shift_days.end_s`.  The site DB's own `shift_days` is EMPTY (it holds only the three site
tables), so the boundaries are read from a leaf.

EVERY PRINTED LINE IS ASCII.  A Windows console is cp1252 and a box-drawing character in an
output line kills the run half-way through the table (memory `windows-console-is-cp1252`).

    python .scratch/inbound-optimization/assets/sweep_fee_threshold.py <run> [--window 20-39]
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from Optimization.Performance_Evaluations.common.frames import _ddf, _ydf   # noqa: E402
from Optimization.persistence.Picking_Data import (load_yard_drains,        # noqa: E402
                                                   load_yard_trailers)
from Schema import connect as _connect                                      # noqa: E402

#: 26's grid, kept verbatim so the two runs' tables are read side by side.
DEFAULT_GRID = (0.75, 1.00, 1.10, 1.20, 1.25, 1.30, 1.50, 1.75, 2.00)


# ── locating the run ─────────────────────────────────────────────────────────────────────

def _resolve_run(arg):
    """A path, or a bare run name under COMPARISON_OUTPUT_DIR (the diagnostics convention)."""
    if os.path.isdir(arg):
        return os.path.abspath(arg)
    base = os.environ.get('COMPARISON_OUTPUT_DIR')
    if base:
        cand = os.path.join(base, arg)
        if os.path.isdir(cand):
            return cand
    raise SystemExit('no such run: %r (and not found under COMPARISON_OUTPUT_DIR)' % (arg,))


def _site_dbs(run_root):
    """[(arm_label, site_db_path, pair_dir)] -- one per coupled arm, sorted by label."""
    out = []
    for dirpath, _dirs, files in os.walk(run_root):
        if os.path.basename(dirpath) != '_site':
            continue
        for fn in sorted(files):
            if fn.startswith('inbound_') and fn.endswith('.db'):
                out.append((fn[len('inbound_'):-len('.db')],
                            os.path.join(dirpath, fn),
                            os.path.dirname(dirpath)))
    if not out:
        raise SystemExit('no <pair>/_site/inbound_*.db under %s -- not a coupled run, '
                         'or inbound was off' % (run_root,))
    return sorted(out)


def _leaf_dbs(pair_dir, arm_label):
    """The leaf `sim_<arm>.db` files behind one coupled arm.

    A coupled arm is labelled `<leafA>__<leafB>`; both halves carry the same leaf arm name
    on this map's runs, but the split is honoured rather than assumed.
    """
    names = set(part for part in arm_label.split('__') if part)
    found = []
    for dirpath, _dirs, files in os.walk(pair_dir):
        if os.path.basename(dirpath) == '_site':
            continue
        for fn in files:
            if fn.startswith('sim_') and fn.endswith('.db') and fn[4:-3] in names:
                found.append(os.path.join(dirpath, fn))
    return sorted(found)


def _ro(path):
    """Read-only open, through the repo's own connector.

    `immutable=True` for the same reason `_query_rows` uses it: a plain `mode=ro` open
    CREATES `-wal`/`-shm` sidecars beside an archived database and cannot remove them
    (memory `wal-sidecars-come-from-readers`), and the preflight canaries see that litter as
    an undeclared tree path.  The promise it requires -- nothing is writing -- holds for a
    finished run, which is the only kind this script reads.
    """
    return _connect.read_only(path, immutable=True)


def _run_id(path):
    with _ro(path) as con:
        rows = [r[0] for r in con.execute(
            'SELECT run_id FROM simulation_runs ORDER BY run_id')]
    if len(rows) != 1:
        raise SystemExit('%s: expected one run row, found %d'
                         % (os.path.basename(path), len(rows)))
    return int(rows[0])


# ── the two bounds a censored row and a window need ──────────────────────────────────────

def _site_end_s(leaf_paths):
    """`max(batch_start_time + duration)` across BOTH leaves -- the site's censoring bound."""
    end = 0.0
    for p in leaf_paths:
        with _ro(p) as con:
            row = con.execute(
                'SELECT MAX(batch_start_time + duration) FROM batch_stats').fetchone()
        if row and row[0] is not None:
            end = max(end, float(row[0]))
    return end


def _window_bounds(leaf_paths, lo_day, hi_day):
    """[start of `lo_day`, end of `hi_day`) in absolute seconds, off `shift_days.end_s`.

    One site day boundary serves every crew under the era, so the leaves must agree; a
    disagreement is REPORTED rather than averaged away, because it would mean the two
    channels closed their days apart and the window is not one window.
    """
    per_leaf = []
    for p in leaf_paths:
        with _ro(p) as con:
            rows = dict((int(r['day']), float(r['end_s'])) for r in
                        con.execute('SELECT day, end_s FROM shift_days ORDER BY day'))
        if rows:
            per_leaf.append((p, rows))
    if not per_leaf:
        raise SystemExit('no shift_days rows on either leaf -- not an era run?')

    def _at(day):
        vals = [r[day] for _p, r in per_leaf if day in r]
        if not vals:
            raise SystemExit('shift_days has no day %d on any leaf' % (day,))
        if max(vals) - min(vals) > 1.0:
            print('  !! leaves disagree on the end of day %d: %s'
                  % (day, ', '.join(format(v, ',.1f') for v in vals)))
        return max(vals)

    start = 0.0 if lo_day == 0 else _at(lo_day - 1)
    return start, _at(hi_day)


# ── section 1: the yard reading ──────────────────────────────────────────────────────────

def _recv_depth_max(site_db, run_id, lo_day, hi_day):
    with _ro(site_db) as con:
        row = con.execute(
            'SELECT MAX(recv_depth) FROM site_receiving '
            'WHERE run_id = ? AND batch BETWEEN ? AND ?',
            (run_id, lo_day, hi_day)).fetchone()
    return None if not row or row[0] is None else int(row[0])


def yard_reading(arms, lo_day, hi_day):
    print('')
    print('== the yard, window days %d-%d %s' % (lo_day, hi_day, '=' * 40))
    hdr = ('%-28s %9s %9s %11s %7s %8s %8s %6s %6s %6s'
           % ('arm', 'contend', 'binding', 'depth', 'doors',
              'det p50', 'det max', 'stand', 'done', 'recvd'))
    print(hdr)
    print('-' * len(hdr))
    for a in arms:
        d = a['drains']
        d = d[(d['batch'] >= lo_day) & (d['batch'] <= hi_day)]
        n_drains = len(d)
        contend = int(((d['yard_start'] > 0) & (d['free_doors_start'] <= 0)).sum())
        binding = int(d['binding_cut'].sum())
        det = a['window']['detention_days'].to_numpy(dtype=float)
        p50 = float(np.median(det)) if det.size else float('nan')
        dmax = float(det.max()) if det.size else float('nan')
        allrows = a['trailers']
        print('%-28s %4d/%-4d %4d/%-4d %6.1f/%-4d %7.2f %8.3f %8.3f %6d %6d %6s'
              % (a['label'][:28], contend, n_drains, binding, n_drains,
                 d['yard_start'].mean(), int(d['yard_start'].max()),
                 d['free_doors_start'].mean(), p50, dmax,
                 int((allrows['status'] == 'standing').sum()),
                 int((allrows['status'] == 'done').sum()),
                 str(a['recv_depth_max'])))
    print('')
    print('  contend = drains with trailers standing AND no free door (yard.binding).')
    print('  binding = drains leaving yard_end or staged_remainder_end above zero; a COUNT')
    print('            of drains, never a sum of the levels.')
    print('  stand/done are whole-run trailer statuses; detention is the window population.')


# ── section 2: the sweep ─────────────────────────────────────────────────────────────────

def sweep(arms, grid, lo_day, hi_day):
    print('')
    print('== the fee-threshold sweep, window days %d-%d %s' % (lo_day, hi_day, '=' * 26))
    n = [len(a['window']) for a in arms]
    print('  population: %s-%s trailers per arm, %d arm(s); censored rows kept at the '
          'site bound.' % (format(min(n), ','), format(max(n), ','), len(arms)))
    print('')
    hdr = '%10s %14s %26s %8s' % ('threshold', 'over %', 'overage trailer-days', 'spread')
    print(hdr)
    print('-' * len(hdr))
    rows = []
    for t in grid:
        shares, totals = [], []
        for a in arms:
            keep = set(int(s) for s in a['window']['seq'])
            df = _ydf(a['rows'], a['end_s'], t)
            df = df[df['seq'].isin(keep)]
            shares.append(100.0 * float(df['over_threshold'].mean()) if len(df) else 0.0)
            totals.append(float(df['overage_days'].sum()))
        lo_t, hi_t = min(totals), max(totals)
        spread = (hi_t / lo_t) if lo_t > 0 else float('inf')
        rows.append((t, min(shares), max(shares), lo_t, hi_t, spread))
        print('%9.2fd %6.1f-%-6.1f %11s - %-11s %s'
              % (t, min(shares), max(shares),
                 format(lo_t, ',.2f'), format(hi_t, ',.2f'),
                 (format(spread, '7.2f') + 'x') if np.isfinite(spread) else '%8s' % '-'))
    print('')
    print('  A candidate is a KNEE when a meaningful minority pays (neither pole saturated')
    print('  at 0% or 100%) and the arms separate -- the fee axis has to be able to rank')
    print('  them.  Set PHASE2_THRESHOLD_DAYS from this table, not from a fresh search.')
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('run', help='run root (a path, or a bare name under COMPARISON_OUTPUT_DIR)')
    ap.add_argument('--window', default='20-39', help='LO-HI working days (default: 20-39)')
    ap.add_argument('--grid', default=None,
                    help="comma-separated candidate thresholds in days (default: 26's grid)")
    args = ap.parse_args(argv)

    lo_day, hi_day = (int(x) for x in args.window.split('-'))
    grid = (DEFAULT_GRID if args.grid is None
            else tuple(float(x) for x in args.grid.split(',')))

    run_root = _resolve_run(args.run)
    print('run: %s' % run_root)

    arms = []
    for label, site_db, pair_dir in _site_dbs(run_root):
        leaves = _leaf_dbs(pair_dir, label)
        if not leaves:
            raise SystemExit('%s: found no leaf sim_*.db under %s' % (label, pair_dir))
        rid = _run_id(site_db)
        end_s = _site_end_s(leaves)
        lo_s, hi_s = _window_bounds(leaves, lo_day, hi_day)
        rows = load_yard_trailers(site_db, rid)
        trailers = _ydf(rows, end_s, 0.0)
        in_window = set(int(r['seq']) for r in rows
                        if lo_s <= float(r['arrived_s']) < hi_s)
        window = trailers[trailers['seq'].isin(in_window)]
        arms.append({
            'label': label, 'rows': rows, 'end_s': end_s,
            'trailers': trailers, 'window': window,
            'drains': _ddf(load_yard_drains(site_db, rid)),
            'recv_depth_max': _recv_depth_max(site_db, rid, lo_day, hi_day),
        })
        print('  %s: %s trailer(s), %s in window, %d leaf DB(s), site end %ss'
              % (label, format(len(rows), ','), format(len(window), ','),
                 len(leaves), format(end_s, ',.0f')))

    yard_reading(arms, lo_day, hi_day)
    sweep(arms, grid, lo_day, hi_day)
    return 0


if __name__ == '__main__':
    sys.exit(main())
