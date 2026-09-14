"""calltree_inbound_ladder.py — the inbound evaluator's cost across catalogue sizes, under the era.

WHAT THIS REPLACES.  The question phase 2 needs answered is what a gain cell costs against an
unpriced one -- `inbound-optimization` ticket 31 measured that at 1.6-1.9x and flagged its own
limitation: it measured `('fifo','tmin')`, the only two adapters that open NO POOL, while eight of
`PHASE2_PAIRS`' twelve arm-slots are pool adapters.  Re-measuring it properly by running the
campaign costs ~9 h and ~164 GiB.  A ladder over smaller inventories answers it for minutes.

WHAT IT MEASURES, and why each piece is here:

  rho_recv    the receiving crew's utilization.  THE X AXIS, and not catalogue size: T is a
              queueing quantity in rho, and rho(N) is a non-monotone SAWTOOTH because `crew_size`
              takes a `ceil` (staffing.py:652) -- projected 0.446, 0.647, 0.525, 0.619, 0.694,
              0.837, 0.819 at N = 5k..400k.  Fitting anything against N is not a fit.
  T           mean candidates per `plan_order` entry call, inverted from place_loads/entries,
              which is exactly T(T+1).  Reported beside the OBSERVED yard depth: they are
              independent measurements of the same quantity, and if they disagree the ladder is
              measuring something other than the greedy.
  multiplier  the priced drain over the unpriced drain, PAIRED within one rung.  Ticket 31's
              section 5 is why paired: an identical command ran 3.4x apart on two occasions, so no
              absolute wall survives a comparison across runs, and only a within-rung ratio does.

THE ERA IS MANDATORY HERE.  Without `shift_drain_or_cap` there is no receiving day at all
(`strategy_runner.py:1676-1682` takes `_recv_day` from the site shift on that branch and never
consults `RECV_DAY_SECONDS`), the yard empties inside every drain, and every number below would
describe a regime the campaign never runs.

Not collected by pytest (no `test_` prefix).  Hand-run, like the rest of this directory --
`python -m pytest Tests/calltree -q` first, because nothing gates these tiers.

Usage:
    python Tests/calltree/calltree_inbound_ladder.py --dry-run
    python Tests/calltree/calltree_inbound_ladder.py --rungs 5000 10000 20000
    python Tests/calltree/calltree_inbound_ladder.py --rungs 5000 --batches 8 --arm uni_rank_labor_norsl
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics as st
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
for _p in (_REPO, _HERE, os.path.join(os.path.dirname(_HERE), 'bench')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

#: The rungs. Doublings, because a knee needs a mid rung to be distinguishable from machine
#: variance -- `STRESS_TEST_FINDINGS` retracted a knee twice for want of one.
DEFAULT_RUNGS = (5_000, 10_000, 20_000, 40_000)

#: The priced pole and the control. Both run the standing yard and the space timeline; the ONLY
#: difference is whether a gain arm ranks, which is what makes the ratio attributable to pricing
#: rather than to the yard existing.
PRICED, UNPRICED = 'gain_forecast', 'fifo'


def _configure(policy: str, coverage: float, recv_crew: int) -> None:
    from Optimization.config.sim_config import CONFIG
    g = CONFIG['global']
    g.update(coverage_days=coverage,
             recv_crew_size=recv_crew,
             inbound_trailer_type='53',
             inbound_dock_doors=4,
             inbound_standing_yard=True,
             inbound_yard_policy=policy,
             inbound_dock_policy=policy,
             )
    # THE ERA, TAKEN FROM THE CAMPAIGN'S OWN CONSTANT rather than restated.  Without it there
    # is no whistle and the yard cannot stand (see the header) -- and restating it by hand is
    # how this tool first ran: it set `shift_drain_or_cap`, `cut_at_day_end` and
    # `roll_over_unpicked`, missed `releases_per_day`, and the coupled put pool refused with
    # "this run releases None batch(es) per day".  A loud refusal, caught in a 4-batch smoke
    # run -- but the same omission in a knob nothing validates would have run silently.
    # `PHASE2_RUN_DEFAULTS` spreads this same dict, so the ladder and the campaign cannot
    # disagree about what "the era" means.
    from Optimization.config.whatif_config import ERA_RUN_DEFAULTS
    g.update(ERA_RUN_DEFAULTS)


def _one(skus: int, batches: int, arm: str, policy: str,
         coverage: float, recv_crew: int, coupled: bool = False,
         min_catalogue: int | None = None) -> dict:
    """One rung, one pole, IN THIS PROCESS. Returns the drain wall plus the evaluator's counts.

    Callers should prefer `_one_isolated`.  This body mutates process-global `CONFIG` and
    `run_fullfid` mutates it further, so two rungs in one interpreter share whatever the
    previous one left behind.  That is not a hypothetical: a full-suite run this week failed
    on exactly that shape -- `run_analysis._apply_run_shape` writes
    `CONFIG['global']['sampler']` with no restore, and one e2e test then broke a unit test
    twelve minutes later.
    """
    _configure(policy, coverage, recv_crew)

    import Inbound.gain as gain
    import Inbound.receiving as rc
    import calltree_scenarios as cs

    stats = {'entries': 0, 'place_loads': 0, 'pools': 0, 'drain_s': 0.0,
             'depths': [], 'deadline': None}
    _po = gain.plan_order
    _pl = gain._Evaluator.place_load
    _mp = gain._Evaluator._make_pool
    _recv = rc.SiteReceiving.receive

    def po(c, *a, **k):
        stats['entries'] += 1
        stats['depths'].append(len(c))
        return _po(c, *a, **k)

    def pl(self, *a, **k):
        stats['place_loads'] += 1
        return _pl(self, *a, **k)

    def mp(self, *a, **k):
        stats['pools'] += 1
        return _mp(self, *a, **k)

    def recv(self, leaves, deadline=None, *a, **k):
        stats['deadline'] = deadline
        t = time.perf_counter()
        try:
            return _recv(self, leaves, deadline, *a, **k)
        finally:
            stats['drain_s'] += time.perf_counter() - t

    gain.plan_order, gain._Evaluator.place_load, gain._Evaluator._make_pool = po, pl, mp
    rc.SiteReceiving.receive = recv
    try:
        t0 = time.perf_counter()
        res = cs.run_fullfid(n_batches=batches, max_skus=skus, strategy=arm,
                             coupled=coupled, min_catalogue=min_catalogue)
        stats['wall_s'] = time.perf_counter() - t0
    finally:
        gain.plan_order, gain._Evaluator.place_load, gain._Evaluator._make_pool = _po, _pl, _mp
        rc.SiteReceiving.receive = _recv

    # RHO, the x axis. Read off the DERIVED block rather than recomputed: `staffing.derive`
    # already solved `crew_size(load, S, rho_recv)` and `expected_utilization` for this pair,
    # and a second implementation here would drift from the one the run actually sized on.
    try:
        from Warehouse.kernel.timeline import DEFAULT_SHIFT_SECONDS
        _recv = ((res or {}).get('staffing') or {}).get('derived', {}).get('receiving', {})
        _S = float((res or {}).get('shift_seconds') or DEFAULT_SHIFT_SECONDS)
        stats['recv_crew'] = _recv.get('crew')
        stats['recv_load_s'] = _recv.get('load_seconds_per_day')
        stats['rho_recv'] = (stats['recv_load_s'] / (stats['recv_crew'] * _S)
                             if stats['recv_crew'] and _S else None)
    except Exception:
        stats['recv_crew'] = stats['recv_load_s'] = stats['rho_recv'] = None

    e = stats['entries']
    r = stats['place_loads'] / e if e else 0.0
    stats['T'] = (-1 + math.sqrt(1 + 4 * r)) / 2 if r else 0.0
    stats['per_entry'] = r
    stats['max_depth'] = max(stats['depths']) if stats['depths'] else 0
    stats['catalogue'] = (res or {}).get('catalogue')
    stats['catalogue_skus'] = (res or {}).get('catalogue_skus')
    stats['saturated'] = bool((res or {}).get('saturated'))
    stats['mean_depth'] = st.mean(stats['depths']) if stats['depths'] else 0.0
    return stats


def _one_isolated(skus, batches, arm, policy, coverage, recv_crew, coupled,
                  min_catalogue):
    """One rung, one pole, in a FRESH INTERPRETER.

    RUNGS MUST NOT SHARE A PROCESS.  `_configure` writes process-global `CONFIG`, and
    `run_fullfid` writes more of it (`n_batches`, and the era derivation stamps `shared`).
    Two rungs in one interpreter would therefore measure whatever the previous one left
    behind -- and a ladder whose rungs contaminate each other reports a trend that is partly
    its own history.

    This is not a theoretical worry.  A full-suite run this week failed on exactly this
    shape: `run_analysis._apply_run_shape` writes `CONFIG['global']['sampler']` with no
    restore, so one e2e test broke a unit test twelve minutes later, and every tier passed
    when run on its own.  A subprocess is the cheap, total fix -- the OS restores the global
    state for free.
    """
    import subprocess
    payload = json.dumps(dict(skus=skus, batches=batches, arm=arm, policy=policy,
                              coverage=coverage, recv_crew=recv_crew, coupled=coupled,
                              min_catalogue=min_catalogue))
    out = subprocess.run(
        [sys.executable, os.path.abspath(__file__), '--worker', payload],
        capture_output=True, text=True, cwd=_REPO,
        env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    for line in reversed(out.stdout.splitlines()):
        if line.startswith('__RUNG__'):
            return json.loads(line[len('__RUNG__'):])
    raise RuntimeError(
        'rung skus=%s pole=%s produced NO result line -- the subprocess ran and printed '
        'nothing this tool could parse, which is the pool-run-swallows-dead-arms shape: '
        'exit 0 and no data. stdout tail: %s || stderr tail: %s'
        % (skus, policy, out.stdout[-1500:], out.stderr[-1500:]))


def _fmt_rho(v) -> str:
    """rho to 3 decimals, or "-" when the derived block did not carry it.

    A missing rho must READ as missing. Printing 0.000 for "not derived" would put a
    number in the one column the caveats tell the reader to fit against.
    """
    return f'{v:.3f}' if isinstance(v, (int, float)) else '-'


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rungs', type=int, nargs='+', default=list(DEFAULT_RUNGS))
    ap.add_argument('--batches', type=int, default=10)
    ap.add_argument('--arm', default='uni_rank_labor_norsl',
                    help='MUST be a pool adapter, or _make_pool never fires and the '
                         'measurement is empty. fifo is uniform, tmin/tmax are merge.')
    ap.add_argument('--coverage', type=float, default=5.0)
    ap.add_argument('--recv-crew', type=int, default=4)
    ap.add_argument('--coupled', action='store_true',
                    help='run the SITE model (both leaves). The default takes '
                         '_channel_runs[0], always the STORE -- the quieter half of the '
                         'site: it binds 14-17 of 75 drains against fulfillment 46-60.')
    ap.add_argument('--worker', default=None, help=argparse.SUPPRESS)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    if a.worker:                     # the subprocess entry point
        kw = json.loads(a.worker)
        r = _one(kw['skus'], kw['batches'], kw['arm'], kw['policy'],
                 kw['coverage'], kw['recv_crew'], kw['coupled'],
                 kw.get('min_catalogue'))
        r.pop('depths', None)        # keep the handoff small
        print('__RUNG__' + json.dumps(r))
        return

    if a.dry_run:
        print(f'rungs   : {a.rungs}')
        print(f'batches : {a.batches}   arm: {a.arm}   coverage_days: {a.coverage}')
        print(f'poles   : priced={PRICED}  unpriced={UNPRICED}  (2 runs per rung)')
        print(f'total   : {2 * len(a.rungs)} fullfid runs under the era')
        return

    # ONE catalogue for the whole ladder, sized by the TOP rung.
    #
    # Per-rung selection would be worse than the bug it fixes. The catalogues under
    # PROFILE_INPUT_DIR were generated months apart (40,000 on 2026-09-13, 400,000 on
    # 2026-08-16) and the generator moved between them, so a ladder that picked per rung
    # would change GENERATOR VINTAGE partway up and report the difference as growth.
    # Sizing on the top rung binds one catalogue that can serve every rung, and every
    # rung then truncates the SAME catalogue -- which is what makes the rungs comparable.
    floor = max(a.rungs)
    print(f'{"skus":>8} {"pole":>10} {"drain_s":>9} {"wall_s":>8} {"entries":>8} '
          f'{"place_ld":>9} {"pools":>9} {"T":>6} {"maxdep":>7} '
          f'{"crew":>5} {"rho":>6} {"deadline":>9}')
    rows = []
    _announced = False
    for n in a.rungs:
        rung = {}
        for pole, policy in (('unpriced', UNPRICED), ('priced', PRICED)):
            s = _one_isolated(n, a.batches, a.arm, policy, a.coverage,
                              a.recv_crew, a.coupled, floor)
            rung[pole] = s
            if not _announced and s.get('catalogue'):
                print(f'catalogue: {s["catalogue"]} declares '
                      f'{s["catalogue_skus"]:,} SKUs -- every rung truncates THIS one')
                _announced = True
            print(f'{n:>8,} {pole:>10} {s["drain_s"]:>9.3f} {s["wall_s"]:>8.1f} '
                  f'{s["entries"]:>8,} {s["place_loads"]:>9,} {s["pools"]:>9,} '
                  f'{s["T"]:>6.2f} {s["max_depth"]:>7} '
                  f'{str(s.get("recv_crew") or "-"):>5} '
                  f'{_fmt_rho(s.get("rho_recv")):>6} '
                  f'{str(s["deadline"]):>9}')
        rows.append((n, rung))
        if rung['priced'].get('saturated'):
            print(f'{"":>8} {"":>10} SATURATED -- rung {n:,} exceeds the bound '
                  f'catalogue ({rung["priced"].get("catalogue_skus"):,}); this rung '
                  f'RE-RUNS the one at the ceiling and is not a measurement.')

    print()
    print('TWO MULTIPLIERS, AND THEY ARE NOT THE SAME NUMBER — paired within each rung')
    print()
    print('  DRAIN  = priced / unpriced over `SiteReceiving.receive` only. This is the')
    print('           evaluator against no evaluator, and it is the sharp attribution.')
    print('  RUN    = the same ratio over the WHOLE fullfid run. This is the one')
    print("           commensurable with inbound-optimization ticket 31s 1.6-1.9x, which was")
    print('           measured per coupled UNIT and not per drain. Comparing the DRAIN column')
    print('           to that band is the denominator error this repo keeps paying for.')
    print()
    print(f'{"skus":>8} {"drain_u":>9} {"drain_p":>9} {"DRAINx":>8} '
          f'{"run_u":>8} {"run_p":>8} {"RUNx":>7} {"T":>6} {"rho":>6}')
    for n, rung in rows:
        u, p = rung['unpriced']['drain_s'], rung['priced']['drain_s']
        wu, wp = rung['unpriced']['wall_s'], rung['priced']['wall_s']
        dm = (p / u) if u > 0 else float('nan')
        rm = (wp / wu) if wu > 0 else float('nan')
        print(f'{n:>8,} {u:>9.3f} {p:>9.3f} {dm:>8.2f} '
              f'{wu:>8.1f} {wp:>8.1f} {rm:>7.2f} {rung["priced"]["T"]:>6.2f} '
              f'{_fmt_rho(rung["priced"].get("rho_recv")):>6}')

    print()
    print("ticket 31 measured 1.6-1.9x PER COUPLED UNIT on ('fifo','tmin') -- the two adapters")
    print('that open NO pool. Read the RUN column against that band, not the DRAIN column.')
    print('A drain ratio can be large while the run ratio is small: the drain is a minority of')
    print('a unit, and a ratio of two small numbers near timing resolution is unstable.')
    print()
    print('CAVEATS THIS LADDER CANNOT SHED:')
    if a.coupled:
        print('  * COUPLED: both leaves ran, so PutawayPool, the two-leaf')
        print('    compose_site_view and _unload_split door teams ARE exercised.')
    else:
        print('  * UNCOUPLED: run_fullfid took _channel_runs[0], always the STORE leaf,')
        print('    which binds 14-17 of 75 drains against fulfillment 46-60 -- the quieter')
        print('    half. PutawayPool, two-leaf compose_site_view and _unload_split door')
        print('    teams were NOT exercised. Pass --coupled for the site model.')
    print('  * rho is NOT controlled: crew_size takes a ceil, so small rungs float the')
    print('    crew under target and rho(N) is a SAWTOOTH. Read the multiplier against')
    print('    the measured rho and T, never against the rung SKU count.')
    print('  * Scale: the campaign is 400,000 SKUs (MAX_SKUS = None) at 40 site days.')


if __name__ == '__main__':
    main()
