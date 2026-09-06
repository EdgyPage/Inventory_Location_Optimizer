"""era_coverage.py — the calibrated era's stage-A expectation per channel, and the
pair-level FIXED POINT that sizes the warehouse from stock levels denominated in days.

Two things live here because they are one loop:

  * `stage_a` -- for each channel, the class-uniform placement distribution over the
    section's packs, the expected day at every line count and the fixed point `n` at which
    that day fills the declared crew's capacity (`expected_travel.solve_n`), or the demand a
    declared `--s-pick-*` override buys.  This is the first half of
    `workunits._derive_staffing_for_pair` ("Derive the expected-travel closed form"), factored
    out so the coverage loop and the derivation compute it with ONE function -- two copies
    would be two answers.

  * `fixed_point` -- "Rescale stock coverage at setup" (.scratch/department-calibration,
    decision 2 of the closed form): coverage is a RUNTIME rescaling under declared days.
    Every SKU's `equilibrium_qty` / `reorder_point` follow from its daily demand `d_s`, which
    needs `n`; the warehouse is sized from those quantities (`plan_warehouse`); `n` depends on
    the built geometry.  So

        Q(n) -> plan_warehouse -> geometry -> expected_travel.solve_n -> n

    is iterated at PAIR level until every channel's `n` moves by less than `tol`, or
    `max_rounds` is spent and the residual is recorded.  Round 0 plans the catalogue's own
    levels (what the generation-batch denomination amounted to on this pair is recorded as
    `catalogue`); each later round rescales the WHOLE catalogue at the previous round's `n`
    and re-plans.  The last plan stands: its sampled orders, its geometry and its stage-A
    expectation are what the run fields, so the derivation reuses them rather than pricing
    the section twice.

The loop is driven from `sim_assets.build_shared_assets` behind `era_on()`; flag-off it is
never entered and the planner is called exactly once, as before.  `plan_fn` is injected (a
closure over the planner's arguments) so the loop is testable with a fake planner and knows
nothing about sizing.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

from Optimization.config.sim_config import (CONFIG, CALIBRATION_KEYS, channel_pickers)
from Optimization.simconfig import coverage as _cov
from Optimization.simconfig import expected_travel as _et
from Optimization.simconfig import staffing as _staffing
from Warehouse.layout.Storage_Primitive import viable_storage_units

#: Which staffing input overrides each channel's expected s_pick.
PICK_OVERRIDE_KEY: dict = {'store': 's_pick_store', 'fulfillment': 's_pick_ff'}


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
    `pick_cfg`, `rates`, `analytic`, `expected` (the expected day at the fixed point),
    `pickers`, `daily_demand_units`, `batch` and `s_pick` (the recorded constant) -- exactly
    what `_derive_staffing_for_pair` reads.  `n` is the section's lines per day.
    Warnings, never failures: an empty section prices nothing; a saturated batch is flagged.
    """
    overrides = {k: inputs.get(k) for k in CALIBRATION_KEYS}
    out: dict = {}
    for spec in specs:
        name = spec.name
        pick_cfg = spec.pick_cfg
        section = _staffing.regime_orders(orders, spec.regime)
        pricing = _staffing.PricingConfig.from_pick_config(pick_cfg, name=spec.cfg_name)
        t0 = time.perf_counter()
        dist = _et.PlacementDist.uniform(
            {c.sku: viable_storage_units(c, c.equilibrium_qty) for c in section})
        rates = _et.accumulate(section, pick_cfg, dist, geometry)
        declared = CONFIG['channels'][name]['batch']
        cv = (float(declared['std']) / float(declared['mean'])) if float(declared['mean']) > 0 else 0.0
        K = channel_pickers(name)
        cap = _staffing.pick_capacity(K, day_seconds, float(inputs['rho_pick']))
        override = overrides.get(PICK_OVERRIDE_KEY[name])
        if rates.units_per_line <= 0.0:
            log.warning(f"  [staffing] {name}: s_pick could not be priced (empty section); "
                        f"the channel derives no demand")
            expected = _et.expected_pick(rates, geometry, pick_cfg, 0.0, cv)
            s_pick = {'value': 0.0, 'provenance': 'derived', 'source': 'expected_travel',
                      'unpriced': True}
            D = 0.0
        elif override is not None:
            D = _staffing.daily_demand(cap, float(override))
            expected = _et.expected_pick(rates, geometry, pick_cfg, D / rates.units_per_line, cv)
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
        batch = _staffing.batch_content(D, rates.units_per_line, len(section),
                                        float(declared['mean']), float(declared['std']))
        if batch['saturated']:
            log.warning(f"  [staffing] {name}: {K} pickers at {s_pick['value']:.2f} s/unit "
                        f"ask for {batch['mean_lines']:,.0f} lines/day but the section has "
                        f"{len(section)} SKUs -- batch content clamped to every SKU every "
                        f"day; the declared crew is oversized for this catalogue")
        out[name] = {'orders': section, 'pricing': pricing, 'dist': dist, 'pick_cfg': pick_cfg,
                     'rates': rates,
                     'analytic': _staffing.analytic_pick(section, pricing),
                     'expected': expected, 'pickers': K, 'daily_demand_units': D,
                     'batch': batch, 's_pick': s_pick, 'n': float(expected['lines'])}
        log.info(f"  [staffing] {name}: K={K}  s_pick={s_pick['value']:.3f} s/unit "
                 f"({s_pick['provenance']}, expected {expected['s_pick']:.3f} at "
                 f"{expected['lines']:,.0f} lines/day: {expected['tasks']:,.0f} tasks, "
                 f"{expected['swaps']:,.0f} swaps)  demand={D:,.0f} units/day  "
                 f"batch mean_fraction={batch['mean_fraction']:.4f} "
                 f"(~{batch['mean_lines']:,.0f} lines)  [{time.perf_counter()-t0:.0f}s]")
    return out


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


def fixed_point(orders_all: list, plan_fn, specs: list, *, coverage_days: float,
                safety_days: float, inputs: dict, day_seconds: float,
                log: logging.Logger, tol: float = _cov.DEFAULT_TOL,
                max_rounds: int = _cov.DEFAULT_MAX_ROUNDS) -> tuple:
    """Iterate Q(n) -> plan -> geometry -> n at pair level.  Returns
    `(plan, warehouse_meta, stage_a_result, record)` for the LAST round.

    `plan_fn()` plans `orders_all` (which this loop rescales in place before each call) and
    builds the warehouse: `(plan, warehouse_meta)`.  `orders_all` is the whole catalogue; the
    plan's `sampled` subset (or the whole list when the planner samples nothing) is what
    stage A prices.  The record is JSON-shaped and rides `staffing.calibration[<pair>]
    ['coverage']`:

        {'coverage_days', 'safety_days', 'tol', 'max_rounds', 'rounds': [...],
         'catalogue': {channel: implied_coverage},      # the levels the catalogue authored
         'final': {channel: rescale stats},             # the levels the run fields, pre-plan
         'planned_sum_q', 'lines_per_day': {channel: n}, 'residual': {channel: n/n_prev - 1},
         'converged': bool}
    """
    record: dict = {'coverage_days': float(coverage_days), 'safety_days': float(safety_days),
                    'tol': float(tol), 'max_rounds': int(max_rounds), 'rounds': []}
    t_all = time.perf_counter()
    log.info(f'  [coverage] rescaling stock coverage at setup: {coverage_days:g} days of each '
             f"SKU's own demand, {safety_days:g} safety days (round 0 plans the catalogue's "
             f'own levels)')
    plan, meta = plan_fn()
    sampled = plan.sampled or orders_all
    sa = stage_a(sampled, _et.Geometry.from_warehouse(meta), specs, inputs=inputs,
                 day_seconds=day_seconds, log=log)
    n = _lines(sa)
    record['catalogue'] = {
        s.name: _cov.implied_coverage(_staffing.regime_orders(sampled, s.regime), n[s.name])
        for s in specs}
    record['rounds'].append({'round': 0, 'lines_per_day': n, 'aisles': plan.total_aisles,
                             'bins': plan.total_bins, 'sampled': len(sampled),
                             'planned_sum_q': int(sum(c.equilibrium_qty for c in sampled))})
    for name, cat in record['catalogue'].items():
        log.info(f"  [coverage] round 0 {name}: the catalogue's levels are worth "
                 f"{cat['demand_weighted_days']:,.0f} days (median {cat['median_days']:,.0f}) "
                 f"at {n[name]:,.0f} lines/day; sum Q = {cat['sum_q']:,}")
    stats: dict = {}
    converged = False
    history: dict = {s.name: [] for s in specs}      # per channel: [(n_in, n_out), ...]
    prev = n
    for r in range(1, int(max_rounds) + 1):
        # Round 1 rescales at round 0's line count (nothing to bracket yet); every later
        # round at `next_guess` -- the plain iterate until the root is bracketed, then the
        # log-space secant between the bracket ends.
        prev = n if r == 1 else {name: next_guess(history[name]) for name in n}
        stats = {s.name: _cov.rescale_section(_staffing.regime_orders(orders_all, s.regime),
                                              prev[s.name], coverage_days=coverage_days,
                                              safety_days=safety_days)
                 for s in specs}
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
                     f"{st['floor_q_share']:.1%} of SKUs hold ONE unit, carrying "
                     f"{st['floor_q_demand_share']:.1%} of the demand; SKUs above the floor "
                     f"carry {st['realized_coverage_days']:.1f} days; n now {n[name]:,.0f} "
                     f"({(n[name] / prev[name] - 1.0) if prev[name] else 0.0:+.2%})")
        log.info(f'  [coverage] round {r}: {plan.total_aisles} aisles / {plan.total_bins:,} bins'
                 + ('  -- converged' if converged else ''))
        if converged:
            break
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
