"""era_coverage.py — the calibrated era's stage-A expectation per channel, and the
pair-level FIXED POINT that sizes the warehouse from stock levels denominated in days.

Two things live here because they are one loop:

  * `stage_a` -- for each channel, the class-uniform placement distribution over the
    section's packs and the expected day over it.  UNDER THE ERA (ADR-0004) the day is
    DECLARED: `n = demand × N_skus` lines, the expected seconds per unit is read AT that
    day (`expected_travel.expected_pick`), and the picking crew is SOLVED from the
    first-time confidence (`staffing.solve_pickers`: the smallest integer whose expected
    cut share of the demanded units is under `1 - sqrt(c)`).  FLAG-OFF the crew is declared
    and the day is the fixed point at which it fills its capacity
    (`expected_travel.solve_n`), or the demand a declared `--s-pick-*` override buys.  The
    regime is read off `inputs` (`staffing_spec()` records the other regime's keys as
    None), never off a flag.  This is the first half of
    `workunits._derive_staffing_for_pair` ("Derive the expected-travel closed form"), factored
    out so the coverage loop and the derivation compute it with ONE function -- two copies
    would be two answers.

  * `fixed_point` -- "Rescale stock coverage at setup" (.scratch/department-calibration,
    decision 2 of the closed form): a stock level is a RUN's declaration in declared days.
    Every SKU's `equilibrium_qty` / `reorder_point` follow from its daily demand `d_s`, which
    needs `n`; the warehouse is sized from those quantities (`plan_warehouse`); `n` depends on
    the built geometry.  So

        Q(n) -> plan_warehouse -> geometry -> expected_travel.solve_n -> n

    is iterated at PAIR level until every channel's `n` moves by less than `tol`, or
    `max_rounds` is spent and the residual is recorded.  ROUND 0 IS A SEED, NOT A PLAN
    (`seed_lines`): the catalogue carries no level to plan (ADR-0002), so the loop opens with
    the line count the crew fills at the section's ANALYTIC seconds per unit -- catalogue
    only, no geometry, no travel term.  Each later round rescales the WHOLE catalogue at the
    previous round's `n` and re-plans.  The last plan stands: its sampled orders, its geometry
    and its stage-A expectation are what the run fields, so the derivation reuses them rather
    than pricing the section twice.

    UNDER THE ERA THE LOOP COLLAPSES TO ONE PASS: the line count is declared, so the seed
    IS the fixed point, round 1 declares at it, plans once, and stage A answers with the
    same `n` (converged by construction; a `--max-skus` sample that shrinks a section costs
    one more round).  The levels no longer depend on the crew -- only the crew reads the
    geometry.  Before round 1 the LINE FLOOR is solved per channel (`coverage
    .solve_floor_lines`: the smallest floor whose stamped first-pass fill clears
    `sqrt(c)`), the shelf side of the same guarantee; a typed `--floor-lines` is accepted
    at or above it and refused below.  Flag-off the floor is one line and the loop
    iterates as before.

  * THE DERIVED FILL ("Derive the fill headroom from the fragmentation").  UNDER THE ERA
    (`inputs['min_headroom']` is not None) the planner's fill is not typed: each round,
    after the levels are declared and before the warehouse is planned, every SKU's plan is
    stamped and the stationary fragmentation chain (`simconfig/fragmentation.py`) prices
    the extra bins each bucket grows into under base stock; the bucket is then sized to
    HOLD `max(requirement + E[extra], requirement / (1 - min_headroom))` bins
    (`derive_fill`, handed to the planner as `bucket_hold`).  The record's
    `fielded.buckets[]` carries `fill` and `hold` beside `expected_extra`, and a rebuild
    reads the holds back (`holds_at`) rather than re-deriving them.  Flag-off the typed
    `store_fill` / `ff_fill` size the warehouse exactly as before and the record says so
    (`fielded.fill.provenance = 'assumed'`, `derived = None`).

The loop is driven from `sim_assets.build_shared_assets` WHEREVER IT SAMPLES -- in every mode,
not only under the era, because a level is now a run's declaration in every mode (ADR-0002,
decision 6); the era flag decides only whether the clock cuts and caps, and flag-off the day
the loop declares against is the reporting frame.  `plan_fn` is injected (a closure over the
planner's arguments) so the loop is testable with a fake planner and knows nothing about sizing.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

from Optimization.config.sim_config import (CONFIG, CALIBRATION_KEYS, channel_pickers,
                                            _DEMAND_KEY)
from Optimization.simconfig import coverage as _cov
from Optimization.simconfig import expected_travel as _et
from Optimization.simconfig import fragmentation as _frag
from Optimization.simconfig import staffing as _staffing
from Warehouse.kernel.regime import FULFILLMENT as _FULFILLMENT
from Warehouse.layout.Storage_Primitive import viable_storage_units

#: Which staffing input overrides each channel's expected s_pick.
PICK_OVERRIDE_KEY: dict = {'store': 's_pick_store', 'fulfillment': 's_pick_ff'}


def declared_lines(inputs: dict, name: str, section) -> float | None:
    """The DECLARED lines per day of channel `name` over `section`, or None flag-off.

    The declaration is in the sampler's unit -- the fraction of the section's SKUs drawn
    per day -- so the line count is `fraction × N` over exactly the orders handed in: the
    whole catalogue at the seed, the sampled set at stage A.  The two agree whenever the
    planner samples nothing away, which is every run without `--max-skus`.
    """
    fraction = inputs.get(_DEMAND_KEY[name])
    if fraction is None:
        return None
    return float(fraction) * float(len(section))


def _guarantee_inputs(inputs: dict) -> tuple[float, float] | None:
    """`(fill_min, cut_share_max)` from the declared first-time confidence -- both sides at
    `sqrt(c)` -- or None flag-off."""
    c = inputs.get('first_time_confidence')
    if c is None:
        return None
    side = _staffing.first_time_split(float(c))
    return side, 1.0 - side


@dataclass(frozen=True)
class ChannelSpec:
    """What stage A needs to know about one channel: its name, the regime its section is
    filtered by (None on a store-only catalogue: the whole catalogue), the pick config it is
    priced with and the name of that config (the channel's FIRST config)."""
    name: str
    regime: str | None
    pick_cfg: object
    cfg_name: str


def channel_specs(inventory) -> tuple[bool, list[ChannelSpec]]:
    """`(mixed, [ChannelSpec, ...])` for a catalogue, one spec per channel, from the same
    channel plan the run's work units are built from (`workunits._channel_runs_for`)."""
    from Optimization.simdriver.workunits import _channel_runs_for, _config_name   # noqa: E402
    mixed, runs = _channel_runs_for(inventory)
    seen: dict = {}
    for ch, cfg in runs:
        if ch.name not in seen:
            seen[ch.name] = ChannelSpec(ch.name, ch.regime if mixed else None,
                                        ch.picker.cost, _config_name(cfg))
    return mixed, list(seen.values())


def stage_a(orders, geometry: _et.Geometry, specs: list, *, inputs: dict,
            day_seconds: float, log: logging.Logger) -> dict:
    """Stage A of the staffing derivation for every channel: `{name: {...}}`.

    Per channel the dict carries `orders` (the section), `pricing`, `dist` (class-uniform),
    `pick_cfg`, `rates`, `analytic`, `expected` (the expected day at the section's line
    count), `pickers` (SOLVED under the era, declared flag-off), `pickers_provenance`,
    `daily_demand_units`, `batch`, `s_pick` (the recorded constant), and under the era
    `demand` (the declaration and the day law's two cvs) and `guarantee` (the crew solve's
    stamp) -- exactly what `_derive_staffing_for_pair` reads.  `n` is the section's lines
    per day.  Warnings, never failures: an empty section prices nothing; a saturated batch
    is flagged.

    UNDER THE ERA (`inputs` carries a demand for the channel, ADR-0004) the line count is
    the declaration, the expected day is priced AT it, and the crew is the smallest integer
    whose expected cut share of the DEMANDED units -- `n × E[q]` at the expected seconds per
    unit, under the day's unit cv -- is within `1 - sqrt(c)`.  A declared `--s-pick-*`
    replaces the expected price in that load and is recorded `declared`.
    """
    overrides = {k: inputs.get(k) for k in CALIBRATION_KEYS}
    guarantee = _guarantee_inputs(inputs)
    out: dict = {}
    for spec in specs:
        name = spec.name
        pick_cfg = spec.pick_cfg
        section = _staffing.regime_orders(orders, spec.regime)
        pricing = _staffing.PricingConfig.from_pick_config(pick_cfg, name=spec.cfg_name)
        analytic = _staffing.analytic_pick(section, pricing)
        t0 = time.perf_counter()
        dist = _et.PlacementDist.uniform(
            {c.sku: viable_storage_units(c, c.equilibrium_qty) for c in section})
        rates = _et.accumulate(section, pick_cfg, dist, geometry)
        declared = CONFIG['channels'][name]['batch']
        cv = (float(declared['std']) / float(declared['mean'])) if float(declared['mean']) > 0 else 0.0
        override = overrides.get(PICK_OVERRIDE_KEY[name])
        n_declared = declared_lines(inputs, name, section)
        demand = None
        solved = None
        if rates.units_per_line <= 0.0:
            log.warning(f"  [staffing] {name}: s_pick could not be priced (empty section); "
                        f"the channel derives no demand")
            expected = _et.expected_pick(rates, geometry, pick_cfg, 0.0, cv)
            s_pick = {'value': 0.0, 'provenance': 'derived', 'source': 'expected_travel',
                      'unpriced': True}
            D = 0.0
            # Nothing to solve a crew from; the era fields one picker so the channel still
            # runs (a crew of none is not a configuration), flag-off the declared count.
            K = 1 if n_declared is not None else channel_pickers(name)
            provenance = 'derived' if n_declared is not None else 'declared'
        elif n_declared is not None:
            # THE ERA: the day is declared, the price is read at it, the crew is solved.
            if guarantee is None:
                raise ValueError(f'{name}: a demand is declared but no first_time_confidence '
                                 f'is; the crew cannot be solved without the guarantee')
            expected = _et.expected_pick(rates, geometry, pick_cfg, n_declared, cv)
            if override is not None:
                s_pick = _staffing.constant(float(override), 'declared', source='override',
                                            expected=float(expected['s_pick']))
            else:
                s_pick = _staffing.constant(float(expected['s_pick']), 'derived',
                                            source='expected_travel', placement=dist.kind)
            moments = _staffing.line_moments(section)
            D = n_declared * float(analytic['units_per_line'])      # DEMANDED units/day
            cv_units = _staffing.units_cv(n_declared, cv, moments)
            solved = _staffing.solve_pickers(D * float(s_pick['value']), cv_units, day_seconds,
                                             guarantee[1])
            K = int(solved['pickers'])
            provenance = 'derived'
            demand = {'declared': float(inputs[_DEMAND_KEY[name]]), 'n_skus': len(section),
                      'lines_per_day': float(n_declared), 'units_per_day': float(D),
                      'served_units_per_day': float(expected['units']),
                      'units_per_line': float(analytic['units_per_line']),
                      'day_law': {'family': 'normal', 'cv_lines': float(cv),
                                  'cv_units': float(cv_units),
                                  'line_var': float(moments['var'])}}
        else:
            # FLAG-OFF: the crew is declared and the day is the fixed point it fills.
            K = channel_pickers(name)
            provenance = 'declared'
            cap = _staffing.pick_capacity(K, day_seconds, float(inputs['rho_pick']))
            if override is not None:
                D = _staffing.daily_demand(cap, float(override))
                expected = _et.expected_pick(rates, geometry, pick_cfg,
                                             D / rates.units_per_line, cv)
                s_pick = _staffing.constant(float(override), 'declared', source='override',
                                            expected=float(expected['s_pick']))
            else:
                expected = _et.solve_n(rates, geometry, pick_cfg, cv, capacity_s=cap,
                                       n_max=len(section))
                D = float(expected['units'])
                s_pick = _staffing.constant(float(expected['s_pick']), 'derived',
                                            source='expected_travel', placement=dist.kind)
        if rates.unplaced_skus:
            log.warning(f"  [staffing] {name}: {rates.unplaced_skus} SKU(s) pack into no storage "
                        f"unit and carry no visit rate")
        # The batch content: under the era the declared fraction back (D ÷ E[q] = n lines
        # over N SKUs), flag-off the served units over the expectation's units per line.
        upl = float(analytic['units_per_line']) if n_declared is not None else rates.units_per_line
        batch = _staffing.batch_content(D, upl, len(section),
                                        float(declared['mean']), float(declared['std']))
        if batch['saturated']:
            log.warning(f"  [staffing] {name}: {K} pickers at {s_pick['value']:.2f} s/unit "
                        f"ask for {batch['mean_lines']:,.0f} lines/day but the section has "
                        f"{len(section)} SKUs -- batch content clamped to every SKU every "
                        f"day; the declared crew is oversized for this catalogue")
        out[name] = {'orders': section, 'pricing': pricing, 'dist': dist, 'pick_cfg': pick_cfg,
                     'rates': rates, 'analytic': analytic,
                     'expected': expected, 'pickers': K, 'pickers_provenance': provenance,
                     'daily_demand_units': D, 'demand': demand,
                     'guarantee': ({'first_time_confidence': float(inputs['first_time_confidence']),
                                    'side': float(guarantee[0]), 'crew': solved}
                                   if solved is not None else None),
                     'batch': batch, 's_pick': s_pick, 'n': float(expected['lines'])}
        if solved is not None:
            log.info(f"  [staffing] {name}: demand declared {demand['declared']:.6f} of "
                     f"{len(section):,} SKUs = {n_declared:,.1f} lines/day, {D:,.0f} demanded "
                     f"units/day (cv {cv:.3f} lines / {demand['day_law']['cv_units']:.4f} "
                     f"units); s_pick={s_pick['value']:.3f} s/unit ({s_pick['provenance']}, "
                     f"expected {expected['s_pick']:.3f}: {expected['tasks']:,.0f} tasks, "
                     f"{expected['swaps']:,.0f} swaps) -> E[W]={solved['load_s']:,.0f} s/day; "
                     f"crew SOLVED K={K} for cut share <= {solved['cut_share_max']:.4f} "
                     f"(expected {solved['cut_share']:.4f}, utilization "
                     f"{solved['expected_utilization']:.3f})  [{time.perf_counter()-t0:.0f}s]")
        else:
            log.info(f"  [staffing] {name}: K={K}  s_pick={s_pick['value']:.3f} s/unit "
                     f"({s_pick['provenance']}, expected {expected['s_pick']:.3f} at "
                     f"{expected['lines']:,.0f} lines/day: {expected['tasks']:,.0f} tasks, "
                     f"{expected['swaps']:,.0f} swaps)  demand={D:,.0f} units/day  "
                     f"batch mean_fraction={batch['mean_fraction']:.4f} "
                     f"(~{batch['mean_lines']:,.0f} lines)  [{time.perf_counter()-t0:.0f}s]")
    return out


def seed_lines(orders_all: list, specs: list, *, inputs: dict, day_seconds: float,
               log: logging.Logger) -> dict:
    """Round 0's `{channel: lines per day}`, from the CATALOGUE ALONE.

    The loop needs a line count before it can declare a level, and it cannot get one the way
    every later round does: `stage_a` prices a placement distribution over each SKU's storage
    units at its order-up-to, and at round 0 no SKU has one (ADR-0002 -- the catalogue carries
    no level, so there is nothing to plan and no geometry to price against).

    So the seed is the ANALYTIC prediction (`staffing.analytic_pick`): one intercept per line,
    the per-item charge and the handling term per unit, at ground height with NO travel term.
    That is a strict UNDER-estimate of seconds per unit, so `n` starts too HIGH and the first
    rescaling asks for too much stock.  The DIRECTION is not load-bearing -- `next_guess` needs
    the root bracketed, one point each side, and the plain iterate delivers that after one round
    from either side -- but a seed of the right MAGNITUDE is what the loop is owed, and pricing
    the real catalogue is the only honest way to get one without geometry.  A declared
    `--s-pick-*` override replaces the analytic value, because a run that names its constant
    means it from the first round.

    Zero for a channel whose section is empty or unpriceable; the loop then declares every SKU
    at its line floor, which is what an empty demand implies.

    UNDER THE ERA there is nothing to seed: the line count is DECLARED (`declared_lines`),
    so the seed is the declaration itself and the loop converges on it in one round.
    """
    out: dict = {}
    for spec in specs:
        section = _staffing.regime_orders(orders_all, spec.regime)
        n_declared = declared_lines(inputs, spec.name, section)
        if n_declared is not None:
            out[spec.name] = float(n_declared)
            log.info(f'  [coverage] {spec.name}: {out[spec.name]:,.1f} lines/day DECLARED '
                     f'({inputs[_DEMAND_KEY[spec.name]]:.6f} of {len(section):,} SKUs); the '
                     f'fixed point is the declaration')
            continue
        pricing = _staffing.PricingConfig.from_pick_config(spec.pick_cfg, name=spec.cfg_name)
        a = _staffing.analytic_pick(section, pricing)
        override = inputs.get(PICK_OVERRIDE_KEY[spec.name])
        s_pick = float(override) if override is not None else float(a['seconds_per_unit'])
        upl = float(a['units_per_line'])
        if s_pick <= 0.0 or upl <= 0.0:
            out[spec.name] = 0.0
            log.warning(f'  [coverage] {spec.name}: the section prices no picking work '
                        f'({len(section)} SKU(s)); the seed line count is zero and every SKU '
                        f'is declared at its line floor')
            continue
        cap = _staffing.pick_capacity(channel_pickers(spec.name), day_seconds,
                                      float(inputs['rho_pick']))
        out[spec.name] = _staffing.daily_demand(cap, s_pick) / upl
        log.info(f'  [coverage] seed {spec.name}: {out[spec.name]:,.0f} lines/day at the '
                 f'analytic {s_pick:.3f} s/unit over {len(section):,} SKUs '
                 f'({upl:.2f} units/line, no travel term)')
    return out


def declared_at(record: dict) -> dict:
    """`{channel: the line count this run DECLARED its levels at}` from a coverage record.

    NOT `record['lines_per_day']`, which is the fixed point's OUTPUT: the `n` stage A answered
    with AFTER the last declaration was made and the warehouse sized from it.  Declaring at the
    output re-derives levels the run never fielded -- see `declare_from_record`.

    `final[<channel>]` IS the stats dict `coverage.rescale_section` returned, so its
    `lines_per_day` is by construction the value the declaration was made at.
    `rounds[-1]['rescaled_at']` is the same number recorded from the loop's side, and serves as
    the fallback so a record written by either shape reads correctly.
    """
    out = {ch: st['lines_per_day']
           for ch, st in (record.get('final') or {}).items()
           if isinstance(st, dict) and st.get('lines_per_day') is not None}
    if out:
        return out
    rounds = record.get('rounds') or []
    return dict((rounds[-1].get('rescaled_at') or {}) if rounds else {})


class MissingCoverageRecord(RuntimeError):
    """A rebuild asked to re-declare a run's levels, and the run recorded no coverage."""


def declare_from_record(orders_all: list, specs: list, record: dict | None, *,
                        log: logging.Logger) -> dict:
    """Re-declare a catalogue's stock levels EXACTLY as one finished run declared them, from
    that run's own `staffing.calibration[<pair>].coverage` record.  Returns the per-channel
    rescale stats.

    This is the REBUILD path, and it exists because a rebuild has a different job from a run.
    `run_analysis` and `run_map_precompute` re-plan a finished run's warehouse from its
    original catalogue to recover the shape it ran on -- and since ADR-0002 that catalogue
    carries no level, while the warehouse is sized from levels on every path (`sample=False`
    skips only the SKU sampling, never `bucket_requirements`).  So a rebuild must declare
    before it plans, and it must declare THE SAME THING, or it silently rebuilds a different
    warehouse -- the exact failure `run_map_precompute._identity_matches` refuses on, and the
    one `run_analysis` would swallow into "Config stage: 0 job(s)".

    Re-running the fixed point would be both expensive (~8 minutes a pair) and WRONG: it would
    re-derive levels from THIS checkout's geometry and closed form, not the ones the run
    fielded.  The record already holds the only thing that varies -- the line count the run
    DECLARED at -- and the levels are a pure function of it and the three declared scalars, so
    ONE rescaling pass reproduces the run's declaration exactly, in a second.

    WHICH line count, and it is not the obvious one.  A round declares at `prev` and sizes the
    warehouse from that declaration; the stage A that follows ANSWERS with a different `n`, and
    that answer is what `record['lines_per_day']` holds.  On a converged run the two differ by
    up to `tol` (1%) and on one that spent `max_rounds` by whatever the residual says -- enough
    to move `bucket_requirements` by tens of buckets and flip a `_demand_replicas` ceil, which
    is a rebuilt warehouse the run never built.  `run_map_precompute` would refuse every row;
    `run_analysis` has no such gate and would ship the wrong `total_bins` into every evaluation
    in silence.  So the INPUT is the one we want -- `declared_at`.

    Raises `MissingCoverageRecord` rather than guessing when the record is absent or does not
    name a channel the rebuild needs: a fabricated level here is a fabricated warehouse.
    """
    if not record or not (record.get('final') or record.get('rounds')):
        raise MissingCoverageRecord(
            'this run recorded no coverage block, so its stock declaration cannot be '
            'reproduced. Runs written before ADR-0002 carried their levels on the catalogue; '
            'that catalogue can still be read, but a rebuild of one must load the run\'s own '
            'planned inventory instead of re-declaring.')
    n = declared_at(record)
    missing = [s.name for s in specs if n.get(s.name) is None]
    if missing:
        raise MissingCoverageRecord(
            f'the recorded coverage names a declared line count for {sorted(n)} but this '
            f'rebuild needs {missing} -- the run and the rebuild disagree about which channels '
            f'the catalogue has, so re-declaring would field a section the run never did.')
    floors = floors_at(record)
    stats = {s.name: _cov.rescale_section(
        _staffing.regime_orders(orders_all, s.regime), float(n[s.name]),
        coverage_days=float(record['coverage_days']),
        safety_days=float(record['safety_days']),
        floor_lines=float(floors[s.name])) for s in specs}
    for name, st in stats.items():
        log.info(f"  [coverage] re-declared {name} from the run's record: "
                 f"{n[name]:,.0f} lines/day -> sum Q {st['sum_q']:,} over {st['n_skus']:,} SKUs "
                 f"({record['coverage_days']:g}/{record['safety_days']:g}/"
                 f"{floors[name]:g} days/days/lines)")
    return stats


def floors_at(record: dict) -> dict:
    """`{channel: the line floor this run DECLARED its levels at}` from a coverage record.

    Per channel since ADR-0004 (each section's floor is solved from its own fill), read off
    `final[<channel>]['floor_lines']` -- the rescale stats, so by construction the value the
    declaration was made at -- and falling back to the record's scalar for a run that
    recorded one floor for both sections (every run before the flip), then to the flag-off
    default.  A channel absent from `final` takes the same fallback."""
    scalar = record.get('floor_lines')
    default = float(_cov.DEFAULT_FLOOR_LINES if scalar is None else scalar)
    out: dict = {}
    for ch, st in (record.get('final') or {}).items():
        v = st.get('floor_lines') if isinstance(st, dict) else None
        out[ch] = float(default if v is None else v)
    return _Floors(out, default)


class _Floors(dict):
    """A per-channel floor map whose missing channels read the record's fallback."""
    def __init__(self, values: dict, default: float) -> None:
        super().__init__(values)
        self.default = float(default)

    def __missing__(self, key):
        return self.default


def _lines(sa: dict) -> dict:
    return {name: float(a['n']) for name, a in sa.items()}


def next_guess(points: list) -> float:
    """The line count to rescale at next, from this channel's `(n_in, n_out)` history.

    `n_out = g(n_in)` is monotone DECREASING: more lines a day means more stock, a bigger
    warehouse, more travel per pick and so fewer lines the crew can fill -- and its gain is
    above one on a real catalogue (the reference pair's fulfillment section went 1,818 ->
    14,832 -> 4,048 -> 10,797 under plain iteration).  So the plain iterate is used only
    until the root of `f(n) = log g(n) - log n` is BRACKETED (one point with `f > 0`, one
    with `f < 0`); from then on the next guess is the secant of `f` in log-space between
    the two bracket ends, pulled to the log-midpoint when it lands within 5% of either end
    (the one-sided stagnation regula falsi is known for).  Every later evaluation shrinks
    the bracket, so the loop converges on any monotone map.
    """
    if not points:
        raise ValueError('no evaluations yet')
    lo = max((p for p in points if p[1] > p[0] and p[0] > 0.0), key=lambda p: p[0],
             default=None)      # the largest n_in the crew still OUT-filled
    hi = min((p for p in points if p[1] < p[0] and p[0] > 0.0), key=lambda p: p[0],
             default=None)      # the smallest n_in the crew fell SHORT of
    if lo is None or hi is None or lo[0] >= hi[0]:
        return float(points[-1][1])                     # not bracketed: the plain iterate
    la, lb = math.log(lo[0]), math.log(hi[0])
    fa, fb = math.log(lo[1]) - la, math.log(hi[1]) - lb  # f > 0 at la, f < 0 at lb
    x = la - fa * (lb - la) / (fb - fa)
    if not (la + 0.05 * (lb - la) < x < lb - 0.05 * (lb - la)):
        x = 0.5 * (la + lb)
    return float(math.exp(x))


def fielded_block(section: list, plan, regime: str | None, floor_lines: float) -> dict:
    """What the plan actually FIELDED for one channel section, as the record's proof that the
    line floor's promise was kept ("Field the floor", decision 11).

        {'n_skus', 'fielded_sum_q', 'declared_sum_q',
         'below_floor_skus',        # fielded below the SKU's own line floor: the promise BROKEN
         'above_declaration_skus',  # fielded above its declaration: the retired phase-2 growth
         'buckets': [{handling, category, size, unit, requirement, capacity, free}, ...]}

    The growth counter is `above_DECLARATION_skus`, not "above floor" as decision 11 sketched
    it.  On the pair that produced this map the two coincided -- 100% of both sections sat ON
    the floor, so a level above the floor WAS a grown one -- but on any catalogue where
    coverage exceeds the floor, "SKUs above their floor" is nearly all of them and reads as an
    alarm.  What is worth zero is the count of levels the planner moved.

    Both counts are read off each order's recorded `stock_plan` -- the packing the warehouse
    holds -- not off the level it was asked for, so they can disagree with the declaration and
    say so.  Under the one planner contract they are zero by construction; the point of
    stamping them is that a reader can see the promise was TESTED rather than asserted, and
    the pair that produced this map (30.3% of the fulfillment section below floor, 64,989 SKUs
    grown above it) is exactly what a nonzero reading would look like.
    """
    below = above = 0
    fielded_q = declared_q = 0
    for c in section:
        plan_slots = getattr(c, 'stock_plan', None) or []
        q_fielded  = sum(int(per) * int(count) for _flag, per, count in plan_slots)
        q_declared = int(c.equilibrium_qty)
        fielded_q  += q_fielded
        declared_q += q_declared
        if q_fielded < _cov.line_floor(c.demand.line, floor_lines):
            below += 1
        if q_fielded > q_declared:
            above += 1
    is_ff = (regime == _FULFILLMENT)
    # `fill` and `hold` are what the planner SIZED the bucket at -- the typed fill and
    # `requirement / fill` flag-off, the derived fill and the hold under the era -- so a
    # reader can see the number without re-deriving it ("Derive the fill headroom").
    rows = [{'handling': b[0], 'category': b[1], 'size': b[2], 'unit': b[3],
             'requirement': t['requirement'], 'capacity': t['capacity'], 'free': t['free'],
             'fill': float(t['fill']), 'hold': float(t['hold'])}
            for b, t in sorted((plan.fielding or {}).items(), key=lambda kv: repr(kv[0]))
            if regime is None or (b[3] == _FULFILLMENT) == is_ff]
    return {'n_skus': len(section), 'fielded_sum_q': int(fielded_q),
            'declared_sum_q': int(declared_q), 'below_floor_skus': int(below),
            'above_declaration_skus': int(above), 'buckets': rows}


def derive_fill(req: dict, extra: dict, min_headroom: float) -> dict:
    """The bins each bucket must HOLD under the era, and the fill that implies:

        hold_b = max(requirement_b + E[extra_b],  requirement_b / (1 - min_headroom))
        fill_b = requirement_b / hold_b                    (1 - min_headroom on an empty bucket)

    -- "Band the own-bin share and the free-index depth", decision 7: the warehouse holds
    its declaration AND the stationary fragmentation base stock creates
    (`fragmentation.section_fragmentation`, the `expected_extra` per bucket), floored at a
    declared minimum headroom for what the chain does not model (settings `MIN_HEADROOM`).
    `expected_extra` can be NEGATIVE (a remainder unit migrating down a tier), and a bucket
    can carry extra with no requirement at all (the tier it migrates INTO); both read
    correctly here, the first taking the headroom floor, the second sized to its extra.

    Pure: `{bucket: {'requirement', 'expected_extra', 'hold', 'fill', 'headroom_floored'}}`
    over the union of both maps' buckets, `headroom_floored` saying the minimum bound (the
    fragmentation alone would have left less free).  Refuses a headroom outside [0, 1).
    """
    h = float(min_headroom)
    if not (0.0 <= h < 1.0):
        raise ValueError(f'min_headroom must be a free share in [0, 1); got {min_headroom!r}')
    out: dict = {}
    for b in sorted(set(req) | set(extra), key=repr):
        r = int(req.get(b, 0))
        e = float(extra.get(b, 0.0))
        frag_hold = r + e
        floor_hold = r / (1.0 - h)
        hold = max(frag_hold, floor_hold, 0.0)
        floored = bool(r > 0 and frag_hold < floor_hold - 1e-9)
        fill = (r / hold) if hold > 0.0 else (1.0 - h)
        out[tuple(b)] = {'requirement': r, 'expected_extra': e, 'hold': float(hold),
                         'fill': float(fill), 'headroom_floored': floored}
    return out


def derived_holds(orders_all: list, specs: list, *, min_headroom: float,
                  log: logging.Logger) -> tuple[dict, dict, dict]:
    """The era's per-bucket hold map for a DECLARED catalogue, before the warehouse is
    planned: `(holds, fills, frags)` -- `{bucket: bins}` for the planner, `derive_fill`'s
    table, and each channel's `section_fragmentation` result (keyed by channel name).

    The chain packs a lot by the SKU's `stock_plan`, which the planner writes when it
    fields -- AFTER sizing.  So every SKU's plan is stamped here first (one packing pass,
    `declared_packing` + `field_requirement` with its slots); the planner's own packing
    then honours the stamped plan (`viable_storage_units`' plan branch) and fields the same
    thing, which the loop's fielded-equals-declared check states rather than assumes.  The
    chain computed here is the one the record stamps (`stamp_fragmentation(frag=)`): same
    declaration, same plan, one derivation.

    COST: one packing pass plus the chain per channel, EVERY round of the loop (the chain
    used to run once, after convergence).  Under the era the loop is one round, so setup
    grows by one packing and the chain (about a minute on the 400k-SKU reference pair);
    the `[coverage] fill DERIVED ... [Ns]` line below is where that time shows.
    """
    from Warehouse.inventory.Inventory_Management import Inventory_Manager   # noqa: E402
    t0 = time.perf_counter()
    req, slots = Inventory_Manager.declared_packing(orders_all)
    Inventory_Manager.field_requirement(orders_all, slots)
    extra: dict = {}
    frags: dict = {}
    for s in specs:
        section = _staffing.regime_orders(orders_all, s.regime)
        fr = _frag.section_fragmentation(section)
        frags[s.name] = fr
        for b, row in fr['buckets'].items():
            extra[tuple(b)] = extra.get(tuple(b), 0.0) + float(row['expected_extra'])
    fills = derive_fill(req, extra, min_headroom)
    # A bucket that must hold NOTHING (no requirement, no positive extra) is not named: the
    # planner's fill rule gives it its one structural replica, and `holds_at` reads the
    # record back on the same rule, so a run's map and its rebuild's are the same map.
    holds = {b: v['hold'] for b, v in fills.items() if v['hold'] > 0.0}
    n_floored = sum(1 for v in fills.values() if v['headroom_floored'])
    r_sum = sum(v['requirement'] for v in fills.values())
    h_sum = sum(v['hold'] for v in fills.values())
    per_channel = ', '.join(f"{n} {fr['expected_extra']:+,.0f}" for n, fr in frags.items())
    log.info(f"  [coverage] fill DERIVED per bucket from the stationary fragmentation "
             f"({per_channel} extra bins): {r_sum:,} bins declared -> {h_sum:,.0f} to hold "
             f"({(r_sum / h_sum) if h_sum else 0.0:.3f} overall), {n_floored} of "
             f"{len(fills)} bucket(s) at the {min_headroom:.0%} minimum headroom  "
             f"[{time.perf_counter() - t0:.0f}s]")
    return holds, fills, frags


def stamp_fill(fielded: dict, fills: dict | None, min_headroom: float | None, *,
               name: str = '') -> dict:
    """Stamp how one channel's `fielded` block was SIZED (in place) and return the block.

    Every `buckets[]` row (already carrying `fill` and `hold` from the plan) gains
    `headroom_floored`, and the block gains

        'fill': {'provenance': 'derived' | 'assumed',
                 'min_headroom': h | None,           # the era's declared floor
                 'typed': f | None,                  # the flag-off fill the rows share
                 'derived': {'section_fill',         # sum requirement / sum hold
                             'expected_extra',       # the section's stationary extra
                             'headroom_floored_buckets'} | None}

    `fills` is `derive_fill`'s table under the era and None flag-off, where the derived
    block is None and the typed fill is what the rows say ("flag-off keeps the typed fill
    and records the derived one as None").
    """
    rows = fielded['buckets']
    if fills is None:
        for r in rows:
            r['headroom_floored'] = False
        typed = sorted({float(r['fill']) for r in rows})
        fielded['fill'] = {'provenance': 'assumed', 'min_headroom': None,
                           'typed': (typed[0] if len(typed) == 1 else typed) if typed else None,
                           'derived': None}
        return fielded['fill']
    n_floored = 0
    req_sum = 0
    hold_sum = 0.0
    extra_sum = 0.0
    for r in rows:
        b = (r['handling'], r['category'], r['size'], r['unit'])
        v = fills.get(b)
        r['headroom_floored'] = bool(v['headroom_floored']) if v else False
        if v is None:
            # An empty structural bucket the map never named: the planner gave it its one
            # replica under the fill rule, so its row would read the typed fill beside a
            # DERIVED block -- a number nobody derived.  Say what `derive_fill` says of an
            # empty bucket instead.
            r['fill'] = 1.0 - float(min_headroom)
        if v and abs(float(r['hold']) - float(v['hold'])) > 1e-6:
            raise ValueError(
                f'{name}: bucket {b} was sized to hold {r["hold"]:,.1f} bins but the derived '
                f'fill asks {v["hold"]:,.1f}; the planner and the derivation have drifted apart')
        n_floored += int(r['headroom_floored'])
        req_sum += int(r['requirement'])
        hold_sum += float(r['hold'])
        extra_sum += float(v['expected_extra']) if v else 0.0
    fielded['fill'] = {'provenance': 'derived', 'min_headroom': float(min_headroom),
                       'typed': None,
                       'derived': {'section_fill': (req_sum / hold_sum) if hold_sum else 0.0,
                                   'expected_extra': float(extra_sum),
                                   'headroom_floored_buckets': int(n_floored)}}
    return fielded['fill']


def holds_at(record: dict | None) -> dict | None:
    """`{bucket: bins to hold}` a run DERIVED its warehouse from, read off its coverage
    record's `final[<ch>].fielded.buckets[]` -- or None when the run sized at a typed fill
    (flag-off, or a record written before the derived fill existed).

    The REBUILD path's counterpart of `declare_from_record`: a rebuild re-plans the run's
    warehouse from its catalogue and must size every bucket exactly as the run did.
    Re-running the chain would be both slow and a re-derivation from THIS checkout's code;
    the record already holds the only thing the planner needs.  A row sized to hold
    nothing (an empty structural bucket the fill rule gave its one replica) is not a hold
    the run named, and is skipped as `derived_holds` skips it.
    """
    out: dict = {}
    derived = False
    for _ch, st in ((record or {}).get('final') or {}).items():
        fb = (st.get('fielded') or {}) if isinstance(st, dict) else {}
        blk = fb.get('fill') or {}
        if blk.get('provenance') != 'derived':
            continue
        derived = True
        for r in fb.get('buckets') or []:
            if float(r['hold']) > 0.0:
                out[(r['handling'], r['category'], r['size'], r['unit'])] = float(r['hold'])
    return out if derived else None


def stamp_fragmentation(fielded: dict, section: list, log: logging.Logger, *,
                        name: str = '', frag: dict | None = None) -> dict:
    """Stamp the stationary fragmentation onto one channel's `fielded` block (in place) and
    return the closed form's summary.  `frag` is a `section_fragmentation` result already
    computed for exactly this section and declaration (the era's `derived_holds` prices it
    before the plan); None prices it here.

    Every `buckets[]` row gains `expected_extra` (the expected units of that bucket in
    steady state beyond its `requirement`; 0.0 on a bucket no SKU's plan can reach), and
    the block gains

        'fragmentation': {'expected_extra',          # the section sum, provenance `derived`
                          'provenance', 'method', 'n_classes', 'capped_classes',
                          'positive_lead_skus', 'fielded_bins_per_sku',
                          'stationary_bins_per_sku', 'seconds'}

    A kind that maps to a bucket the plan never built RAISES: the planner sizes a bucket
    for every tier the packer can produce, so a miss is the planner and the packer having
    drifted apart -- the same contract the fielded-equals-declared check above states.
    """
    t0 = time.perf_counter()
    if frag is None:
        frag = _frag.section_fragmentation(section)
    rows = {(r['handling'], r['category'], r['size'], r['unit']): r for r in fielded['buckets']}
    for r in fielded['buckets']:
        r['expected_extra'] = 0.0
    for b, fr_row in frag['buckets'].items():
        row = rows.get(tuple(b))
        if row is None:
            raise ValueError(
                f'{name}: the fragmentation chain packs a lot into bucket {tuple(b)}, which the '
                f'plan never built -- the planner sizes a bucket for every tier the packer can '
                f'produce, so this is the planner and the packer having drifted apart')
        row['expected_extra'] = float(fr_row['expected_extra'])
    seconds = time.perf_counter() - t0
    fielded['fragmentation'] = {
        'expected_extra': float(frag['expected_extra']), 'provenance': 'derived',
        'method': 'stationary_chain', 'n_classes': int(frag['n_classes']),
        'capped_classes': int(frag['capped_classes']),
        'positive_lead_skus': int(frag['positive_lead_skus']),
        'fielded_bins_per_sku': float(frag['fielded_bins_per_sku']),
        'stationary_bins_per_sku': float(frag['stationary_bins_per_sku']),
        'seconds': float(seconds)}
    req = sum(int(r['requirement']) for r in fielded['buckets'])
    log.info(f"  [coverage] {name}: stationary fragmentation {frag['expected_extra']:+,.0f} bins "
             f"over a requirement of {req:,} ({frag['fielded_bins_per_sku']:.3f} -> "
             f"{frag['stationary_bins_per_sku']:.3f} bins/SKU, {frag['n_classes']} SKU classes"
             + (f", {frag['positive_lead_skus']:,} SKU(s) with a lead priced at lead 0"
                if frag['positive_lead_skus'] else '')
             + (f", {frag['capped_classes']} class(es) at the iteration cap"
                if frag['capped_classes'] else '')
             + f")  [{seconds:.0f}s]")
    return frag


def resolve_floors(orders_all: list, specs: list, n: dict, *, coverage_days: float,
                   safety_days: float, floor_lines: float | None, inputs: dict,
                   log: logging.Logger) -> tuple[dict, dict]:
    """The line floor each channel declares at, and the record's `floor` block.

    FLAG-OFF (no first-time confidence in `inputs`): the typed `floor_lines`, else the
    one-line default; nothing is solved.  UNDER THE ERA (ADR-0004, the shelf side): each
    section's floor is SOLVED at its declared line count so the stamped first-pass fill
    clears `sqrt(c)` (`coverage.solve_floor_lines`), and a typed `--floor-lines` is
    accepted at or above every channel's solved value (stamped `declared`, the solved one
    recorded beside it) and REFUSED below it -- a floor under the solved one is a smaller
    promise than the confidence makes, and raising it silently would be the second authored
    knob that moves the crew, the pattern that hid the fill-rate defect.

    Returns `({channel: floor}, {channel: {'floor_lines', 'provenance', 'solved': {...}}})`.
    """
    guarantee = _guarantee_inputs(inputs)
    typed = None if floor_lines is None else float(floor_lines)
    floors: dict = {}
    block: dict = {}
    for s in specs:
        name = s.name
        if guarantee is None:
            v = _cov.DEFAULT_FLOOR_LINES if typed is None else typed
            floors[name] = float(v)
            block[name] = {'floor_lines': float(v),
                           'provenance': 'assumed' if typed is None else 'declared',
                           'solved': None}
            continue
        section = _staffing.regime_orders(orders_all, s.regime)
        t0 = time.perf_counter()
        solved = _cov.solve_floor_lines(section, float(n.get(name) or 0.0),
                                        coverage_days=coverage_days, safety_days=safety_days,
                                        fill_min=guarantee[0])
        log.info(f"  [coverage] {name}: line floor SOLVED at {solved['floor_lines']:.4f} "
                 f"lines for a first-pass fill >= {solved['fill_min']:.4f} (stamped "
                 f"{solved['fill_rate']:.4f}; {solved['evaluations']} evaluations"
                 f"{', the one-line floor already clears it' if solved['at_lower_bound'] else ''}"
                 f")  [{time.perf_counter()-t0:.0f}s]")
        if typed is not None and typed < solved['floor_lines'] - 1e-12:
            raise ValueError(
                f"--floor-lines {typed:g} is below the {solved['floor_lines']:.4f} lines the "
                f"{name} section needs for a first-pass fill of {solved['fill_min']:.4f} "
                f"(the shelf side of first_time_confidence "
                f"{inputs['first_time_confidence']:g}); a smaller floor is a smaller promise "
                f"than the confidence makes. Drop the flag to take the solved floor, or "
                f"type one at or above it.")
        v = solved['floor_lines'] if typed is None else typed
        floors[name] = float(v)
        block[name] = {'floor_lines': float(v),
                       'provenance': 'derived' if typed is None else 'declared',
                       'solved': {k: (list(val) if isinstance(val, tuple) else val)
                                  for k, val in solved.items()}}
    return floors, block


def fixed_point(orders_all: list, plan_fn, specs: list, *, coverage_days: float,
                safety_days: float, floor_lines: float | None, inputs: dict,
                day_seconds: float, log: logging.Logger, tol: float = _cov.DEFAULT_TOL,
                max_rounds: int = _cov.DEFAULT_MAX_ROUNDS) -> tuple:
    """Iterate Q(n) -> plan -> geometry -> n at pair level.  Returns
    `(plan, warehouse_meta, stage_a_result, record)` for the LAST round.

    `plan_fn()` plans `orders_all` (which this loop rescales in place before each call) and
    builds the warehouse: `(plan, warehouse_meta)`.  UNDER THE ERA it is called as
    `plan_fn(bucket_hold=holds)` with the derived hold map (`derived_holds`); flag-off with
    no argument at all, so a planner closure that never learned the keyword is still the
    flag-off path.  `orders_all` is the whole catalogue; the
    plan's `sampled` subset (or the whole list when the planner samples nothing) is what
    stage A prices.  `floor_lines` is the INPUT as declared: None means the one-line default
    flag-off and "solve it" under the era (`resolve_floors`).  The derived fill is on when
    `inputs['min_headroom']` is not None (`staffing_spec()` records it so under the era and
    None flag-off).  The record is JSON-shaped and
    rides `staffing.calibration[<pair>]['coverage']`:

        {'coverage_days', 'safety_days', 'floor_lines',   # the INPUT (None = solved)
         'floor': {channel: {'floor_lines', 'provenance', 'solved'}},   # what each declares at
         'tol', 'max_rounds', 'rounds': [...],
         'seed': {'method': 'analytic_pick' | 'declared', 'lines_per_day': {channel: n}},
         'final': {channel: rescale stats + 'fill' + 'fielded'},   # the declared levels, the
                                                        #   fill rate at them, and the proof
                                                        #   the plan fielded exactly them --
                                                        #   with, per bucket, what it was
                                                        #   sized to hold (`stamp_fill`)
         'planned_sum_q', 'lines_per_day': {channel: n}, 'residual': {channel: n/n_prev - 1},
         'converged': bool}

    `final[<channel>]['fill']` is `coverage.fill_rate` over the orders the last plan fields:
    the expected first-pass fill rate the audit reads `missed_share` against, and the
    base-stock share after planning.  Priced post-plan for a reason that no longer bites --
    the planner used to grow a level into leftover capacity, so the shelf that served a line
    was not the one declared -- and it now equals the pre-plan price by construction, which
    is what `final[<channel>]['fielded']` (`fielded_block`) states and this loop RAISES on.
    """
    if int(max_rounds) < 1:
        raise ValueError(f'the coverage loop needs at least one rescaling round; got '
                         f'max_rounds={max_rounds!r}')
    record: dict = {'coverage_days': float(coverage_days), 'safety_days': float(safety_days),
                    'floor_lines': (None if floor_lines is None else float(floor_lines)),
                    'tol': float(tol), 'max_rounds': int(max_rounds), 'rounds': []}
    t_all = time.perf_counter()
    era = _guarantee_inputs(inputs) is not None
    log.info(f'  [coverage] declaring stock coverage at setup: {coverage_days:g} days of each '
             f"SKU's own demand, {safety_days:g} safety days, floored at "
             + ("a line floor SOLVED per section from the first-time confidence" if era
                and floor_lines is None else f'{floor_lines or _cov.DEFAULT_FLOOR_LINES:g} '
                f"line(s) of the SKU's own mean line")
             + (' (the line count is DECLARED: one round)' if era else
                ' (round 0 is the analytic seed -- the catalogue carries no level to plan)'))
    # Round 0 does NOT plan: there is nothing to plan (ADR-0002).  The seed is the catalogue's
    # own analytic price -- or, under the era, the declaration -- and the FIRST declaration
    # follows from it.
    n = seed_lines(orders_all, specs, inputs=inputs, day_seconds=day_seconds, log=log)
    plan = meta = sa = None
    sampled = orders_all
    seed_method = 'declared' if era else 'analytic_pick'
    record['seed'] = {'method': seed_method, 'lines_per_day': dict(n)}
    record['rounds'].append({'round': 0, 'seed': seed_method, 'lines_per_day': n})
    # THE SHELF SIDE of the guarantee, before any level is declared: the floor each section
    # declares at (solved under the era at the declared line count; one line or the typed
    # value flag-off).
    floors, record['floor'] = resolve_floors(orders_all, specs, n, coverage_days=coverage_days,
                                             safety_days=safety_days, floor_lines=floor_lines,
                                             inputs=inputs, log=log)
    stats: dict = {}
    converged = False
    history: dict = {s.name: [] for s in specs}      # per channel: [(n_in, n_out), ...]
    prev = n
    # THE DERIVED FILL: on when the era declares a minimum headroom.  `fills` / `frags` are
    # the LAST round's derivation, which the stamps below reuse (same declaration, same
    # plan); flag-off both stay None and `plan_fn` is called exactly as it always was.
    min_headroom = inputs.get('min_headroom')
    fills: dict | None = None
    frags: dict = {}
    for r in range(1, int(max_rounds) + 1):
        # Round 1 declares at the SEED's line count (nothing to bracket yet, and nothing
        # planned yet); every later round at `next_guess` -- the plain iterate until the root
        # is bracketed, then the log-space secant between the bracket ends.  Every round
        # declares before it plans, because the planner sizes from the declaration.
        prev = n if r == 1 else {name: next_guess(history[name]) for name in n}
        stats = {s.name: _cov.rescale_section(_staffing.regime_orders(orders_all, s.regime),
                                              prev[s.name], coverage_days=coverage_days,
                                              safety_days=safety_days,
                                              floor_lines=floors[s.name])
                 for s in specs}
        if min_headroom is not None:
            holds, fills, frags = derived_holds(orders_all, specs,
                                                min_headroom=float(min_headroom), log=log)
            plan, meta = plan_fn(bucket_hold=holds)
        else:
            plan, meta = plan_fn()
        sampled = plan.sampled or orders_all
        sa = stage_a(sampled, _et.Geometry.from_warehouse(meta), specs, inputs=inputs,
                     day_seconds=day_seconds, log=log)
        n = _lines(sa)
        for name in n:
            history[name].append((prev[name], n[name]))
        converged = _cov.converged(prev, n, tol)
        record['rounds'].append({'round': r, 'lines_per_day': n, 'aisles': plan.total_aisles,
                                 'bins': plan.total_bins, 'sampled': len(sampled),
                                 'planned_sum_q': int(sum(c.equilibrium_qty for c in sampled)),
                                 'rescaled_at': prev, 'stats': stats})
        for name, st in stats.items():
            log.info(f"  [coverage] round {r} {name}: Q from {prev[name]:,.0f} lines/day -> "
                     f"sum Q {st['sum_q']:,} ({st['units_per_day']:,.0f} units/day); "
                     f"{st['floor_line_share']:.1%} of SKUs sit on the LINE floor, carrying "
                     f"{st['floor_line_demand_share']:.1%} of the demand "
                     f"({st['base_stock_share']:.1%} run base stock); SKUs above the floor "
                     f"carry {st['realized_coverage_days']:.1f} days; n now {n[name]:,.0f} "
                     f"({(n[name] / prev[name] - 1.0) if prev[name] else 0.0:+.2%})")
        log.info(f'  [coverage] round {r}: {plan.total_aisles} aisles / {plan.total_bins:,} bins'
                 + ('  -- converged' if converged else ''))
        if converged:
            break
    # The expected first-pass fill rate at the levels the run FIELDS: the last plan's orders
    # (grown into leftover capacity where the planner found some), priced once per channel at
    # the fixed point's line count -- `missed_share`'s expectation, and the base-stock share
    # after planning.
    for s in specs:
        section = _staffing.regime_orders(sampled, s.regime)
        fill = _cov.fill_rate(section, n[s.name])
        stats[s.name]['fill'] = fill
        fielded = fielded_block(section, plan, s.regime, floors[s.name])
        stats[s.name]['fielded'] = fielded
        log.info(f"  [coverage] {s.name}: expected first-pass fill rate {fill['fill_rate']:.3f} "
                 f"(expected missed share {fill['expected_missed_share']:.3f}) over the planned "
                 f"levels; {fill['base_stock_share']:.1%} of {fill['n_skus']:,} planned SKUs run "
                 f"base stock")
        log.info(f"  [coverage] {s.name}: fielded {fielded['fielded_sum_q']:,} units against a "
                 f"declared {fielded['declared_sum_q']:,} -- {fielded['below_floor_skus']:,} SKUs "
                 f"below their line floor, {fielded['above_declaration_skus']:,} above their "
                 f"declaration, {sum(r['free'] for r in fielded['buckets']):,} free bins over "
                 f"{len(fielded['buckets'])} bucket(s)")
        # The promise, stated as an equality rather than a hope.  `plan_warehouse` refuses a
        # warehouse that cannot hold the declaration, and `field_requirement` fields exactly
        # it -- so a section that fields anything else means the two halves of the planner's
        # one contract have drifted apart, and every number derived from this record (the
        # fill rate, the audit's expected missed share, the derivation's put-away load) is
        # priced on levels the run does not hold.
        if fielded['fielded_sum_q'] != fielded['declared_sum_q'] or fielded['below_floor_skus']:
            raise ValueError(
                f'{s.name}: the plan fielded {fielded["fielded_sum_q"]:,} units against a '
                f'declared {fielded["declared_sum_q"]:,}, with '
                f'{fielded["below_floor_skus"]:,} SKU(s) below their line floor and '
                f'{fielded["above_declaration_skus"]:,} above their declaration. The planner fields '
                f'the requirement exactly (ADR-0002, "Field the requirement"); a difference '
                f'here is a packing that disagrees with the one the warehouse was sized from.')
        # THE STATIONARY FRAGMENTATION ("Derive the stationary fragmentation closed form"):
        # the bins the fielded shelf grows into under base stock, per bucket, derived from
        # each SKU's line law and plan -- stamped in EVERY mode, like the fill rate, so the
        # record says what the declaration needs beyond itself whether or not the era is on.
        # `expected_extra` can be negative on a bucket whose remainder unit migrates down a
        # tier; the section sum is the number the fill headroom derives from.  Under the
        # era the chain was priced before the plan (`derived_holds`) and is reused here.
        stamp_fragmentation(fielded, section, log, name=s.name, frag=frags.get(s.name))
        # ...and how the bucket was SIZED: the derived hold and fill under the era, the
        # typed fill flag-off ("Derive the fill headroom from the fragmentation").
        blk = stamp_fill(fielded, fills, None if min_headroom is None else float(min_headroom),
                         name=s.name)
        if blk['derived'] is not None:
            log.info(f"  [coverage] {s.name}: sized at a DERIVED fill of "
                     f"{blk['derived']['section_fill']:.3f} over the section "
                     f"({blk['derived']['expected_extra']:+,.0f} expected extra bins; "
                     f"{blk['derived']['headroom_floored_buckets']} bucket(s) at the "
                     f"{blk['min_headroom']:.0%} minimum headroom)")
    record['final'] = stats
    record['lines_per_day'] = n
    record['residual'] = {k: (n[k] / prev[k] - 1.0) if prev.get(k) else 0.0 for k in n}
    record['planned_sum_q'] = int(sum(c.equilibrium_qty for c in sampled))
    record['converged'] = bool(converged)
    if not converged:
        log.warning(f'  [coverage] the fixed point did not converge in {max_rounds} round(s); '
                    f"the last rescaling stands with residual "
                    + ', '.join(f'{k} {v:+.2%}' for k, v in record['residual'].items()))
    log.info(f'  [coverage] done in {len(record["rounds"]) - 1} round(s) '
             f'[{time.perf_counter() - t_all:.0f}s]')
    return plan, meta, sa, record
