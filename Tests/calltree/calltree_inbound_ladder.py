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


def _configure(policy: str, coverage: float, recv_crew: int,
               doors: int | None = None, door_team: int | None = None) -> None:
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
    # THE ARRIVAL REGIME, from the campaign's own constant for exactly the reason the era is.
    #
    # Without a lead every trailer arrives the instant it is dispatched, takes one of the four
    # doors at once, and THE YARD NEVER STANDS.  Measured 2026-09-20, after the `TierSlice`
    # repair made this tool run again:
    #
    #     this ladder, 40,000 SKUs   rho = 0.803   T =  1.14   max yard depth  2
    #     the campaign, 400,000      rho = 0.819   T = 16.55   max yard depth 24
    #
    # The same utilization and a FOURTEEN-FOLD difference in T -- the quantity every number
    # below is a function of, since `plan_order` costs T(T+1) `place_load` calls.  So the
    # header's premise, that T is a queueing quantity in rho and rho is therefore the honest
    # x axis, is only half true: rho does not determine depth.  Depth is arrivals against door
    # throughput, and the LEAD LAW is what spreads the arrivals -- probed the same day, the
    # lead moved a fixed catalogue's yard from 32 trailers to 4 while rho sat still.
    #
    # Taken from `PHASE2_RUN_DEFAULTS` rather than restated, so the ladder and the campaign
    # cannot disagree about what the arrival regime is.  The two policy knobs are excluded
    # because choosing them per pole is this tool's whole job.
    from Optimization.config.whatif_config import PHASE2_RUN_DEFAULTS
    g.update({k: v for k, v in PHASE2_RUN_DEFAULTS.items()
              if k.startswith('inbound_')
              and k not in ('inbound_yard_policy', 'inbound_dock_policy')})
    # THE DEPTH LEVERS, applied AFTER the campaign regime so they deliberately override it.
    #
    # Setting the regime above is necessary and not sufficient: the top rung still reaches
    # T = 1.30 against the campaign's 16.55, because depth needs the receiving crew
    # saturated ACROSS drains and this ladder's catalogue caps at 40,000 SKUs where it
    # never is.  Growing the catalogue to 400k would reach it and would cost what the
    # campaign costs, which is the thing this tool exists not to pay.
    #
    # So reach the depth from the other side: throttle door throughput.  What the gain
    # evaluator costs is a function of (T, tier size, live aisles) -- `plan_order` does
    # T(T+1) `place_load` calls whatever made the yard deep -- so a T reached by starving
    # the doors prices the same work as a T reached by flooding the arrivals.  It is NOT
    # the campaign's mechanism and a reader must not read these rungs as the campaign's
    # OPERATING POINT; they are a cost measurement at a matched T.
    if doors is not None:
        g['inbound_dock_doors'] = int(doors)
    if door_team is not None:
        g['inbound_door_team'] = int(door_team)


def _live_candidates(cands):
    """`(count, list)` of the bins one pool open actually chooses from.

    A pool used to be handed a LIST, and `_make_pool` did `pool_factory(list(cands), ...)`,
    so `len(cands)` was both the candidate count and a fair proxy for what the open cost.
    Since the per-drain overlay (`Warehouse/placement/frozen_tier.py`) the gain evaluator
    hands a `TierSlice` -- the frozen tier plus one exclusion set -- and a sliced open builds
    cursors instead of copying every element.  `TierSlice` has no `__len__`, so this tool
    died with `object of type 'TierSlice' has no len()` the moment that landed, and nothing
    noticed because no tier of `Tests/calltree/` is in a gate (memory
    `hand-run-test-tiers-rot-silently`).

    TWO THINGS CHANGED, not one.  The count is now the SURVIVING candidates -- the eager
    path's `len(live)` -- and it is no longer a proxy for the open's COST, because a sliced
    open is O(aisles) plus the skips rather than O(candidates).  So `cnd/open` still answers
    "how much tier is this pool choosing from", which is what it is read for, and no longer
    answers "what did the open cost".  Anything fitted against that column has to say which
    of the two it means.
    """
    if hasattr(cands, '__len__'):
        return len(cands), cands
    tier, excl = cands.tier, cands.excluded
    live = [b for b, i in zip(tier.bins, tier.ids) if i not in excl]
    return len(live), live


def _one(skus: int, batches: int, arm: str, policy: str,
         coverage: float, recv_crew: int, coupled: bool = False,
         min_catalogue: int | None = None, slice_probe: bool = False,
         doors: int | None = None, door_team: int | None = None,
         recv_deadline: float | None = None) -> dict:
    """One rung, one pole, IN THIS PROCESS. Returns the drain wall plus the evaluator's counts.

    Callers should prefer `_one_isolated`.  This body mutates process-global `CONFIG` and
    `run_fullfid` mutates it further, so two rungs in one interpreter share whatever the
    previous one left behind.  That is not a hypothetical: a full-suite run this week failed
    on exactly that shape -- `run_analysis._apply_run_shape` writes
    `CONFIG['global']['sampler']` with no restore, and one e2e test then broke a unit test
    twelve minutes later.
    """
    _configure(policy, coverage, recv_crew, doors, door_team)

    import Inbound.gain as gain
    import Inbound.receiving as rc
    import calltree_scenarios as cs

    stats = {'entries': 0, 'place_loads': 0, 'pools': 0, 'drain_s': 0.0,
             'cands': 0, 'pool_init_s': 0.0, 'buckets': 0, 'takes': 0,
             'keep_k3': 0, 'keep_k5': 0, 'keep_at_k': 0, 'opens_scored': 0,
             'run_bounds': 0, 'aisles': 0,
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

    wp_of = []
    _take_patched: dict = {}      # {pool class: its original take}, restored below
    # (bucket sizes, take count at that open start) for the open still in flight. Scored
    # when the NEXT open begins, because k is only known once the open has finished.
    _pending: list = []

    def _score_pending():
        """Score the last open against the number of units it actually seated.

        The slice keeps at most k per bucket where k is what the pool pops, so its
        reduction is sum(min(k, bucket)) / sum(bucket) -- and k must be the OBSERVED
        take count for that open, not a constant. Using a k smaller than the real one
        would not just overstate the win, it would describe an UNSOUND slice: the
        (k+1)-th entry of a bucket is reachable as soon as the pool pops k+1 times.
        """
        if not _pending:
            return
        sizes, at_start = _pending[0], _pending[1]
        kk = stats['takes'] - at_start
        if kk <= 0:
            kk = 1                 # an open that seated nothing still needs one head
        stats['keep_at_k'] += sum(kk if n > kk else n for n in sizes)
        stats['opens_scored'] += 1

    def mp(self, cands, *a, **k):
        if not wp_of and a:
            wp_of.append(a[0])            # _make_pool(self, cands, wp)
        stats['pools'] += 1
        # HOW MUCH TIER this pool is choosing from -- and since the overlay, NOT what the
        # open costs.  `_live_candidates` says what changed and why the distinction matters
        # to anything fitted against this column.
        _n_cands, _live = _live_candidates(cands)
        stats['cands'] += _n_cands
        # THE SLICE PROBE, and it is OFF by default for a reason: bucketing every candidate
        # here is the same work the pool's own `__init__` does, so it roughly DOUBLES pool
        # construction and inflates the drain. Leaving it always-on would hand the next
        # reader a timing column that is wrong for a reason nothing in the output explains
        # -- the contamination this tool warns about everywhere else.
        #
        # Bucketed EXACTLY as _TravelBalancedPool buckets -- (aisle, height_mult) -- so the
        # distribution is the real one and not a proxy for it.
        if slice_probe:
            try:
                from Warehouse.kernel.cost_model import height_multiplier as _hm
                _br = getattr(wp_of[0], 'height_brackets', ()) if wp_of else ()
                _bk = {}
                for _b in _live:
                    _key = (_b.location[0], _hm(_br, _b.y_phys))
                    _bk[_key] = _bk.get(_key, 0) + 1
                stats['buckets'] += len(_bk)
                stats['keep_k3'] += sum(3 if _n > 3 else _n for _n in _bk.values())
                stats['keep_k5'] += sum(5 if _n > 5 else _n for _n in _bk.values())
                _score_pending()
                _pending[:] = [tuple(_bk.values()), stats['takes']]
            except Exception:
                pass
        # The pool CONSTRUCTION wall, against the drain it sits in. This is the number
        # that decides whether the candidate slice is worth building: the slice makes
        # `__init__` cheaper and nothing else, so `pool_init_s / drain_s` is its ceiling.
        t = time.perf_counter()
        try:
            _p = _mp(self, cands, *a, **k)
        finally:
            stats['pool_init_s'] += time.perf_counter() - t
        # Patch the CLASS, not the instance: every pool here defines __slots__, so an
        # instance assignment raises AttributeError -- and swallowing that gave a counter
        # that read 0.00 and looked like "no takes happened" rather than "not measured".
        # AISLES, not buckets. `take` iterates `for aid in by_aisle`, so the SCAN WIDTH is
        # the aisle count; the slice probe counts (aisle, mult) buckets, which is larger.
        # Reporting buckets as the scan width overstates it -- and did, in ticket 14.
        try:
            stats['aisles'] += len(getattr(_p, '_by_aisle', ()) or ())
        except Exception:
            pass
        _cls = type(_p)
        if _cls not in _take_patched:
            _orig_take = _cls.take

            def _counting_take(pself, u, _o=_orig_take):
                stats['takes'] += 1
                # A run boundary is the pool rebuilding EVERY aisle cache. The heap does
                # not touch that path, so R bounds what the heap can ever save.
                try:
                    if getattr(pself, '_run_sku', None) != u.order.sku:
                        stats['run_bounds'] += 1
                except Exception:
                    pass
                return _o(pself, u)
            _cls.take = _counting_take
            _take_patched[_cls] = _orig_take
        return _p

    def recv(self, leaves, deadline=None, *a, **k):
        # A DEPTH LEVER THAT HELPS AND DOES NOT SUFFICE.  Measured 2026-09-20 at 40,000 SKUs:
        #
        #     doors 4 team 10 -> doors 1 team 1 ...... T 1.25 -> 1.12   (no effect)
        #     recv budget uncapped -> 600 s .......... T 1.25 -> 2.34
        #     600 s -> 200 s -> 80 s ................. T 2.34, 2.34, 2.34 (saturated)
        #
        # against the campaign's T = 16.55.  NO THROUGHPUT LEVER CAN REACH IT, and the reason
        # is not tuning: yard depth cannot exceed the trailers that have ARRIVED, arrivals
        # scale with reorder volume, and volume scales with the catalogue.  Starving the doors
        # or the receiving budget makes the few trailers present wait longer; it cannot
        # conjure a sixteen-deep yard out of four.
        #
        # So this caps a real quantity and is worth having -- it doubles T and is the only
        # lever that moves it -- but the ladder reaches T ~ 2.3 and the campaign runs at 16.55,
        # which is 7.8 against 290 `place_load` calls per entry.  Anything read here is a
        # measurement of a regime the campaign never enters, and the honest instrument for the
        # campaign's regime is this ladder bound to the 400k reference catalogue.
        #
        # It is also an INTERVENTION rather than the campaign's mechanism: trailers stand
        # because receiving stopped, not because the crew was saturated.
        if recv_deadline is not None:
            deadline = (float(recv_deadline) if deadline is None
                        else min(float(deadline), float(recv_deadline)))
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
        for _c, _t in _take_patched.items():
            _c.take = _t          # a patched CLASS outlives the rung if left alone

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
    stats['cands_per_open'] = (stats['cands'] / stats['pools']) if stats['pools'] else 0.0
    stats['init_share'] = (stats['pool_init_s'] / stats['drain_s']
                           if stats['drain_s'] > 0 else 0.0)
    _score_pending()               # the last open has no successor to trigger it
    _po = stats['pools'] or 1
    stats['buckets_per_open'] = stats['buckets'] / _po
    stats['takes_per_open'] = stats['takes'] / _po
    stats['aisles_per_open'] = stats['aisles'] / _po
    stats['bounds_per_open'] = stats['run_bounds'] / _po
    # The heap removes the per-placement scan (K x A) and leaves the run-boundary rebuild
    # (R x A) untouched, so K / (K + R) is its ceiling as a share of the selection cost.
    _k, _r = stats['takes'], stats['run_bounds']
    stats['heap_ceiling'] = _k / (_k + _r) if (_k + _r) else 0.0
    stats['slice_x_k3'] = (stats['cands'] / stats['keep_k3']) if stats['keep_k3'] else 0.0
    stats['slice_x_k5'] = (stats['cands'] / stats['keep_k5']) if stats['keep_k5'] else 0.0
    stats['slice_x_real'] = ((stats['cands'] / stats['keep_at_k'])
                             if stats['keep_at_k'] else 0.0)
    stats['catalogue'] = (res or {}).get('catalogue')
    stats['catalogue_skus'] = (res or {}).get('catalogue_skus')
    stats['saturated'] = bool((res or {}).get('saturated'))
    stats['mean_depth'] = st.mean(stats['depths']) if stats['depths'] else 0.0
    return stats


def _one_isolated(skus, batches, arm, policy, coverage, recv_crew, coupled,
                  min_catalogue, slice_probe=False, doors=None, door_team=None,
                  recv_deadline=None):
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
                              doors=doors, door_team=door_team,
                              recv_deadline=recv_deadline,
                              min_catalogue=min_catalogue, slice_probe=slice_probe))
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
    ap.add_argument('--slice-probe', action='store_true',
                    help='count what a per-bucket candidate slice WOULD keep. Buckets '
                         'every candidate in-process, roughly DOUBLING pool construction, '
                         'so the timing columns are NOT readable on a run carrying it. The '
                         'counts are exact regardless, which is the point. Off by default.')
    ap.add_argument('--coupled', action='store_true',
                    help='run the SITE model (both leaves). The default takes '
                         '_channel_runs[0], always the STORE -- the quieter half of the '
                         'site: it binds 14-17 of 75 drains against fulfillment 46-60.')
    ap.add_argument('--doors', type=int, default=None,
                    help='override the dock door count. With --door-team, the lever that '
                         'reaches campaign yard depth on a small catalogue: T is what the '
                         'evaluator costs, and starving the doors reaches a given T without '
                         'the 400k catalogue that reaching it by arrivals would need.')
    ap.add_argument('--door-team', type=int, default=None,
                    help='override the per-door unload team. Smaller team, slower door, '
                         'deeper yard at a fixed catalogue.')
    ap.add_argument('--recv-deadline', type=float, default=None,
                    help='cap the receiving budget per drain, in seconds. The only lever '
                         'that moves T on a small catalogue (doors and team do not), and '
                         'it saturates at T~2.3 against the campaign 16.55. See the '
                         'clamp in `_one` for why no throughput lever can close that.')
    ap.add_argument('--worker', default=None, help=argparse.SUPPRESS)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    if a.worker:                     # the subprocess entry point
        kw = json.loads(a.worker)
        r = _one(kw['skus'], kw['batches'], kw['arm'], kw['policy'],
                 kw['coverage'], kw['recv_crew'], kw['coupled'],
                 kw.get('min_catalogue'), kw.get('slice_probe', False),
                 doors=kw.get('doors'), door_team=kw.get('door_team'),
                 recv_deadline=kw.get('recv_deadline'))
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
          f'{"place_ld":>9} {"pools":>9} {"cnd/open":>9} {"init_s":>8} {"init%":>6} '
          f'{"T":>6} {"maxdep":>7} {"crew":>5} {"rho":>6} {"deadline":>9}')
    rows = []
    _announced = False
    for n in a.rungs:
        rung = {}
        for pole, policy in (('unpriced', UNPRICED), ('priced', PRICED)):
            s = _one_isolated(n, a.batches, a.arm, policy, a.coverage,
                              a.recv_crew, a.coupled, floor, a.slice_probe,
                              a.doors, a.door_team, a.recv_deadline)
            rung[pole] = s
            if not _announced and s.get('catalogue'):
                print(f'catalogue: {s["catalogue"]} declares '
                      f'{s["catalogue_skus"]:,} SKUs -- every rung truncates THIS one')
                _announced = True
            print(f'{n:>8,} {pole:>10} {s["drain_s"]:>9.3f} {s["wall_s"]:>8.1f} '
                  f'{s["entries"]:>8,} {s["place_loads"]:>9,} {s["pools"]:>9,} '
                  f'{s.get("cands_per_open", 0.0):>9,.0f} '
                  f'{s.get("pool_init_s", 0.0):>8.2f} '
                  f'{s.get("init_share", 0.0) * 100:>5.1f}% '
                  f'{s["T"]:>6.2f} {s["max_depth"]:>7} '
                  f'{str(s.get("recv_crew") or "-"):>5} '
                  f'{_fmt_rho(s.get("rho_recv")):>6} '
                  f'{str(s["deadline"]):>9}')
        rows.append((n, rung))
        if rung['priced'].get('saturated'):
            print(f'{"":>8} {"":>10} SATURATED -- rung {n:,} exceeds the bound '
                  f'catalogue ({rung["priced"].get("catalogue_skus"):,}); this rung '
                  f'RE-RUNS the one at the ceiling and is not a measurement.')

    # Printed ONLY when the probe ran. Without it every column below is zero, and a table of
    # zeros reads as "the slice would keep nothing" -- the most flattering possible answer, and
    # the exact "reads zero, means not measured" failure this effort found three times.
    if a.slice_probe:
        print()
        print('WHAT THE CANDIDATE SLICE WOULD KEEP -- COUNTS ONLY.')
        print('The bucketing runs inside the wrapper and roughly doubles pool construction,')
        print('so the TIMING columns above are NOT readable on this run. The counts are exact')
        print('regardless -- that is why this is counted and not timed.')
        print()
        print(f'{"skus":>8} {"cnd/open":>9} {"bkts/open":>10} {"aisl/open":>10} '
              f'{"bnds/open":>10} {"heap ceil":>10} {"takes/open":>11} '
              f'{"slice x @k":>11} {"(@3 unsound)":>13}')
        for n, rung in rows:
            s = rung['priced']
            if not s.get('pools'):
                continue
            print(f'{n:>8,} {s.get("cands_per_open", 0):>9,.0f} '
                  f'{s.get("buckets_per_open", 0):>10,.0f} '
                  f'{s.get("aisles_per_open", 0):>10,.0f} '
                  f'{s.get("bounds_per_open", 0):>10,.1f} '
                  f'{s.get("heap_ceiling", 0) * 100:>9,.0f}% '
                  f'{s.get("takes_per_open", 0):>11,.2f} '
                  f'{s.get("slice_x_real", 0):>11,.1f} '
                  f'{s.get("slice_x_k3", 0):>13,.1f}')
        print()
        print('  slice x @k uses the OBSERVED takes per open. The @3 column is what ticket 10s')
        print('  "median k = 3" would have predicted -- that 3 is the median GROUP size, not the')
        print('  pops, and a slice built on it would be byte-DIFFERENT, not merely optimistic.')
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
          f'{"run_u":>8} {"run_p":>8} {"RUNx":>7} {"T":>6} {"rho":>6} '
          f'{"aisl/opn":>9} {"bnds/opn":>9} {"heapceil":>9}')
    for n, rung in rows:
        u, p = rung['unpriced']['drain_s'], rung['priced']['drain_s']
        wu, wp = rung['unpriced']['wall_s'], rung['priced']['wall_s']
        dm = (p / u) if u > 0 else float('nan')
        rm = (wp / wu) if wu > 0 else float('nan')
        pr = rung['priced']
        print(f'{n:>8,} {u:>9.3f} {p:>9.3f} {dm:>8.2f} '
              f'{wu:>8.1f} {wp:>8.1f} {rm:>7.2f} {pr["T"]:>6.2f} '
              f'{_fmt_rho(pr.get("rho_recv")):>6} '
              f'{pr.get("aisles_per_open", 0):>9,.0f} '
              f'{pr.get("bounds_per_open", 0):>9,.1f} '
              f'{pr.get("heap_ceiling", 0) * 100:>8,.0f}%')

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
