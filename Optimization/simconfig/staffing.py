"""staffing.py — the calibrated era's staffing DERIVATION, as a pure module.

Pickers per channel are the ONE declared staffing input of the calibrated era
(.scratch/department-calibration, "Define the calibrated era"); everything else a run
fields is DERIVED from them, live at setup, with a declared scalar at every step:

    pick capacity      K × S × ρ_pick                        (seconds of picking per day)
    daily demand       capacity ÷ s_pick                     (units per day, per channel)
    batch content      one batch = one day's demand          (a BatchConfig mean fraction)
    put load           units × f_put                         (units put per unit picked)
    put crew           ceil(put load × s_put ÷ (S × ρ_put))  (ONE site crew, both channels)
    receive load       packs the script implies × f_recv
    receiving crew     ceil(receive seconds ÷ (S × ρ_recv))  (ONE site crew)

`s_pick` (per channel) and `s_put` (one site value) are seconds per unit and are HYBRID by
nature: pick and put-away TRAVEL is arm-dependent and needs a measured reference run, so they
come from the committed calibration record (`Optimization/simconfig/calibration.py`), or --
until a reference run exists -- from the script's own analytic prediction scaled by a
declared TRAVEL SHARE (the record's pass-0 seed).  `s_recv` is EXACT from the script: an
unload has no travel term, so every pack's seconds are computable from the catalogue and
the cost model alone.

THIS MODULE IS PURE.  Inputs, the loaded calibration record and the catalogue / script
totals go in; the derived dict comes out.  It imports no CONFIG, reads no settings and
touches no file, so the arithmetic is testable against a hand computation without a run
(`Tests/unit/test_staffing_derivation.py`).  The harness seam that feeds it is
`Optimization/simdriver/workunits._derive_staffing_for_pair`, which runs AFTER batch
precompute because the receiving crew needs the packs the script implies -- and the script
is itself derived from the pickers, which is why the derivation has two stages:

    stage A  (catalogue only)  s_pick, daily demand, batch content -- BEFORE precompute
    stage B  (the script)      put load, packs, the two site crews -- AFTER precompute

Every scalar default below is a declared ASSUMPTION of the era, never a measurement, and the
record says so (`PROVENANCE`, "Design the staffing record", decision 5).  The derived block
carries `derived`; a copied constant keeps the provenance it arrived with.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from Inbound.pack import receive as _receive
from Inbound.unload import UnloadCost, unload_cost
from Warehouse.kernel.cost_model import handle_var, per_pick
from Warehouse.kernel.regime import regime_of
from Warehouse.operations.putaway import PutawayCost
from Optimization.simconfig.constants import PROVENANCE

#: The channel names the derivation knows, in the order the harness runs them.
CHANNELS: tuple[str, ...] = ('store', 'fulfillment')

#: The three departments the utilization bands are drawn for ("Declare the equilibrium
#: bands", decision 1): one ρ each, all 0.85 by decision.
DEPARTMENTS: tuple[str, ...] = ('pick', 'put', 'recv')


# ── the pricing config: the coefficients a channel's labour is predicted with ─────────

@dataclass(frozen=True)
class PricingConfig:
    """The handling coefficients one channel's analytic prediction is priced with.

    The subset of a `PickConfig` the script-only prediction needs -- no speeds, no height
    brackets, because the prediction is at GROUND height with no travel term (the travel
    share is what a reference run adds).  `from_pick_config` lifts them off the channel's
    canonical pick config; `put()` / `recv()` derive the other two crews' prices by the
    same chain the runner uses (`PutawayCost.from_pick`, `UnloadCost.from_putaway`).
    """
    name: str
    intercept: float
    per_item: float
    weight_coef: float
    volume_coef: float
    weight_fn: str
    volume_fn: str

    @classmethod
    def from_pick_config(cls, cfg, name: str = '') -> 'PricingConfig':
        return cls(name=name or getattr(cfg, 'name', '') or 'pick',
                   intercept=float(cfg.pick_intercept), per_item=float(cfg.pick_per_item),
                   weight_coef=float(cfg.pick_weight_coef),
                   volume_coef=float(cfg.pick_volume_coef),
                   weight_fn=str(cfg.pick_weight_fn), volume_fn=str(cfg.pick_volume_fn))

    def var(self, order) -> float:
        """The per-unit weight/volume handling term for `order` under these coefficients."""
        return handle_var(order.weight, order.volume(), self.weight_coef, self.volume_coef,
                          self.weight_fn, self.volume_fn)

    def put(self, *, intercept_scale: float, item_ratio: float) -> PutawayCost:
        return PutawayCost(intercept=self.intercept * intercept_scale,
                           per_item=self.per_item * item_ratio,
                           weight_coef=self.weight_coef, volume_coef=self.volume_coef,
                           weight_fn=self.weight_fn, volume_fn=self.volume_fn)

    def recv(self, *, put_intercept_scale: float, put_item_ratio: float,
             recv_intercept_scale: float) -> UnloadCost:
        return UnloadCost.from_putaway(
            self.put(intercept_scale=put_intercept_scale, item_ratio=put_item_ratio),
            intercept_scale=recv_intercept_scale)


# ── stage A: the catalogue alone ────────────────────────────────────────────────────────

def analytic_pick(orders, pricing: PricingConfig) -> dict:
    """The script-only prediction of a channel's picking labour per UNIT, from the catalogue.

    Weighted the way the sampler weights: a SKU enters a batch in proportion to its
    `relative_frequency` (affinity lift ignored -- it redistributes lines, it does not add
    them), and a line demands `quantity_rate` units in expectation.  One intercept per LINE
    (one bin visit for one SKU) is the line-count floor ("Choose the calibration
    procedure", decision 7); the per-item charge and `qty × handle_var` are per unit; the
    height multiplier is M(0) = 1 (ground) and there is no travel term.  Returns

        {'seconds_per_unit', 'units_per_line', 'n_skus', 'pricing_config'}

    `seconds_per_unit` is what a travel share scales into a seeded `s_pick`.  Empty
    orders yield zeros rather than raising: a store-only catalogue has no fulfillment
    section, and the caller records that as an absent channel.
    """
    w_sum = 0.0
    wq_sum = 0.0
    sec_sum = 0.0
    n = 0
    for c in orders:
        w = float(c.demand.relative_frequency)
        q = max(1.0, float(c.demand.quantity_rate))     # `max(1, sample)` in Batch
        w_sum += w
        wq_sum += w * q
        sec_sum += w * per_pick(1.0, pricing.intercept, pricing.var(c), q, pricing.per_item)
        n += 1
    if wq_sum <= 0.0:
        return {'seconds_per_unit': 0.0, 'units_per_line': 0.0, 'n_skus': n,
                'pricing_config': pricing.name}
    return {'seconds_per_unit': sec_sum / wq_sum,
            'units_per_line': wq_sum / w_sum,
            'n_skus': n,
            'pricing_config': pricing.name}


def pick_capacity(pickers: int, day_seconds: float, rho_pick: float) -> float:
    """Seconds of picking one channel's crew is expected to deliver per day: K × S × ρ."""
    return float(pickers) * float(day_seconds) * float(rho_pick)


def daily_demand(capacity_s: float, s_pick: float) -> float:
    """Units per day the channel's capacity buys at `s_pick` seconds per unit."""
    if s_pick <= 0.0:
        raise ValueError(f's_pick must be positive seconds per unit; got {s_pick!r}')
    return capacity_s / float(s_pick)


def batch_content(demand_units: float, units_per_line: float, n_skus: int,
                  declared_mean: float, declared_std: float) -> dict:
    """One batch = one day's demand, as the sampler's (mean_fraction, std_fraction).

    The sampler draws a LINE count ~ N(mean·N, std·N) and each line demands
    `quantity_rate` units, so the mean fraction that delivers `demand_units` is
    `(demand ÷ units_per_line) ÷ N`.  The spread keeps the DECLARED coefficient of
    variation (the flag-off `std ÷ mean`, 1/3 for the store, 1/4 for fulfillment) so a
    derived script has the same relative day-to-day variability the archive's did.  A
    mean above 1.0 -- every SKU every day, and then some -- is CLAMPED and flagged: the
    sampler cannot draw more lines than SKUs, and the declared crew is then oversized for
    the catalogue (the verifier will show the idle capacity; `saturated` says why).
    """
    if n_skus <= 0 or units_per_line <= 0.0:
        return {'mean_lines': 0.0, 'mean_fraction': 0.0, 'std_fraction': 0.0,
                'saturated': False}
    mean_lines = demand_units / units_per_line
    mean_fraction = mean_lines / n_skus
    cv = (declared_std / declared_mean) if declared_mean > 0 else 0.0
    saturated = mean_fraction > 1.0
    if saturated:
        mean_fraction = 1.0
    return {'mean_lines': mean_lines,
            'mean_fraction': mean_fraction,
            'std_fraction': mean_fraction * cv,
            'saturated': saturated}


# ── stage B: the script ─────────────────────────────────────────────────────────────────

@dataclass
class ScriptTotals:
    """What one channel's precomputed batch script demands, summed over its batches.

    `units` / `lines` are the demand itself; `analytic_pick_s` is the same script priced
    with `analytic_pick`'s formula line by line (so the record can hold the script-only
    prediction NEXT to the measured value, and their ratio is the travel share).  The
    put and receiving totals come from `implied_reorders`: `put_units` are merchandise
    units put away (= units received), `put_s` their analytic put-away seconds at ground,
    `packs` the storage units the reorders pack into, `recv_s` their EXACT unload seconds.
    `unknown_skus` counts script lines whose SKU the catalogue map did not hold -- zero on
    a healthy run, and reported rather than silently skipped.
    """
    batches: int = 0
    units: float = 0.0
    lines: float = 0.0
    analytic_pick_s: float = 0.0
    put_units: float = 0.0
    put_s: float = 0.0
    packs: float = 0.0
    recv_s: float = 0.0
    unknown_skus: int = 0
    per_sku_units: dict = field(default_factory=dict)

    def per_day(self, value: float) -> float:
        """A total as a per-DAY quantity under the era's one batch per day."""
        return value / self.batches if self.batches else 0.0


def script_totals(batches, orders_by_sku: dict, pricing: PricingConfig) -> ScriptTotals:
    """Stage B's demand side: units, lines and the analytic pick seconds of a batch list.

    `batches` are `Workload_Builder.Batch` objects (or anything with `.items: {sku: qty}`);
    `orders_by_sku` maps a SKU to its `Order` for the handling term.  Per-SKU unit totals
    are kept for `implied_reorders`.
    """
    t = ScriptTotals(batches=len(batches))
    for b in batches:
        for sku, qty in b.items.items():
            c = orders_by_sku.get(sku)
            if c is None:
                t.unknown_skus += 1
                continue
            q = float(qty)
            t.units += q
            t.lines += 1.0
            t.analytic_pick_s += per_pick(1.0, pricing.intercept, pricing.var(c), q,
                                          pricing.per_item)
            t.per_sku_units[sku] = t.per_sku_units.get(sku, 0.0) + q
    return t


def reorder_lot(order) -> int:
    """The quantity one reorder of `order` brings in, in expectation.

    The manager's order-up-to rule (`inventory_reorder.check_reorders`): when the
    inventory position falls to the reorder point it orders `equilibrium + pipeline -
    position`, where the pipeline allowance is `round(rp × lead ÷ (lead + 1))`.  Taking
    the position AT the reorder point gives the expected lot; the supply-cv jitter is
    zero-mean and drops out.  Floors at 1: a SKU whose reorder point sits at its
    equilibrium still reorders something.
    """
    eq = int(getattr(order, 'equilibrium_qty', getattr(order, 'stock_qty', 1)))
    rp = int(getattr(order, 'reorder_point', 0))
    lead = max(0.0, float(getattr(order, 'lead_time_mean', 0.0)))
    pipeline = round(rp * lead / (lead + 1.0)) if lead > 0.0 else 0
    return max(1, eq + pipeline - rp)


def implied_reorders(totals: ScriptTotals, orders_by_sku: dict, pricing: PricingConfig, *,
                     f_put: float, f_recv: float, put_intercept_scale: float,
                     put_item_ratio: float, recv_intercept_scale: float) -> ScriptTotals:
    """Stage B's supply side: the put-away and receiving work the script implies.

    Steady state (f = 1.0) replenishes every unit picked, so each SKU's expected reorders
    are `units demanded ÷ lot`, each lot packs into the storage units the sim's own packer
    would produce (`Inbound.pack.receive` -> `viable_storage_units`, honouring a
    `stock_plan`), and each pack costs

        put-away   M(0) · (put_intercept + qty · put_per_item + qty · var)   (no travel)
        receiving  recv_intercept + recv_per_item + qty · var                (EXACT)

    Fractional reorders are expectations and are kept fractional.  `f_put` scales the put
    load and `f_recv` the receiving load ("units put per unit picked" / packs received per
    pack demanded); at 1.0 both are the steady state, any other value models a growing or
    shrinking warehouse, which is exactly why they are knobs.  Mutates and returns `totals`.
    """
    put_cost = pricing.put(intercept_scale=put_intercept_scale, item_ratio=put_item_ratio)
    recv_cost = pricing.recv(put_intercept_scale=put_intercept_scale,
                             put_item_ratio=put_item_ratio,
                             recv_intercept_scale=recv_intercept_scale)
    for sku, units in totals.per_sku_units.items():
        c = orders_by_sku[sku]
        lot = reorder_lot(c)
        reorders = units / lot
        plan = _receive(c, lot)
        var = pricing.var(c)
        put_s = sum(per_pick(1.0, put_cost.intercept, var, u.quantity, put_cost.per_item)
                    for u in plan.units)
        recv_s = sum(unload_cost(c.weight, c.volume(), u.quantity, recv_cost)
                     for u in plan.units)
        totals.put_units += reorders * plan.packed_qty * f_put
        totals.put_s += reorders * put_s * f_put
        totals.packs += reorders * plan.unit_count * f_recv
        totals.recv_s += reorders * recv_s * f_recv
    return totals


# ── the crews and the bands ─────────────────────────────────────────────────────────────

def crew_size(load_seconds_per_day: float, day_seconds: float, rho: float) -> int:
    """ceil(load ÷ (S × ρ)), floored at 1 when there is load and 0 when there is none.

    A department with work to do fields at least one person even when the arithmetic says
    0.3; a department with NO work (a store-only catalogue's absent channel contributes
    nothing, but the site crew still forms from the other) fields nobody only when the
    whole site has nothing for it.
    """
    if load_seconds_per_day <= 0.0:
        return 0
    return max(1, math.ceil(load_seconds_per_day / (float(day_seconds) * float(rho))))


def expected_utilization(load_seconds_per_day: float, crew: int, day_seconds: float) -> float:
    """worked ÷ granted for one department on one leaf: load × s ÷ (crew × S).

    AFTER `ceil` and AFTER the leaf's share of the site crew ("Declare the equilibrium
    bands", decision 3): integer crews and single-channel leaves undercut ρ by
    construction, so the band is drawn around THIS number, never around ρ.
    """
    if crew <= 0 or day_seconds <= 0:
        return 0.0
    return load_seconds_per_day / (float(crew) * float(day_seconds))


# ── the record ─────────────────────────────────────────────────────────────────────────

def constant(value: float, provenance: str, **extra) -> dict:
    """One recorded constant: `{value, provenance, ...}`.  Provenance must be one of the
    five agreed values; a copied constant keeps the one it arrived with."""
    if provenance not in PROVENANCE:
        raise ValueError(f'provenance must be one of {PROVENANCE}; got {provenance!r}')
    return {'value': value, 'provenance': provenance, **extra}


def derive(*, inputs: dict, constants: dict, day_seconds: float, channels: dict,
           scripts: dict, pricing_names: dict) -> dict:
    """The derived block, from declared inputs + resolved constants + the script.

    `inputs` is the staffing record's INPUTS (`sim_config.staffing_spec()` shape: the two
    picker counts, `rho_pick/rho_put/rho_recv`, `f_put/f_recv`, `band_tol`,
    `put_crew_mode`, plus the three crew-cost scales the harness copies in).
    `constants` is `{'s_pick': {channel: constant}, 's_put': constant, 'k_max':
    {channel: int|None}}` as `calibration.resolve_constants` returns it.  `channels` maps
    a channel name to its stage-A dict (`analytic`, `batch`, `pickers`, `n_skus`);
    `scripts` maps it to its `ScriptTotals`.  Channels absent from `channels` (a
    store-only catalogue) are recorded as absent and contribute nothing.

    The put and receiving crews are SITE totals: one crew each, summing both channels'
    per-day loads.  Each channel leaf's expected utilization for those two departments is
    its own load against the WHOLE site crew, which is why it sits well below ρ.
    """
    S = float(day_seconds)
    s_put = float(constants['s_put']['value'])
    out: dict = {
        'provenance': 'derived',
        'day_seconds': S,
        'channels': {},
        'put': {}, 'receiving': {},
        'k_max_exceeded': {},
    }
    put_load_s = 0.0
    recv_load_s = 0.0
    per_channel_put_s: dict = {}
    per_channel_recv_s: dict = {}
    for name in CHANNELS:
        ch = channels.get(name)
        if ch is None:
            out['channels'][name] = None
            continue
        t: ScriptTotals = scripts[name]
        s_pick = float(constants['s_pick'][name]['value'])
        K = int(ch['pickers'])
        cap = pick_capacity(K, S, float(inputs['rho_pick']))
        units_day = t.per_day(t.units)
        rec = {
            'pickers': K,
            'pricing_config': pricing_names[name],
            's_pick': constants['s_pick'][name],
            'pick_capacity_s': cap,
            'daily_demand_units': ch['daily_demand_units'],
            'analytic': ch['analytic'],
            'batch': ch['batch'],
            'script': {
                'batches': t.batches,
                'units': t.units, 'lines': t.lines,
                'units_per_day': units_day,
                'analytic_pick_s': t.analytic_pick_s,
                # The script's own seconds per unit at ground with no travel -- what a
                # measured s_pick divides by to give the travel share.
                'analytic_s_pick': (t.analytic_pick_s / t.units) if t.units else 0.0,
                'put_units': t.put_units, 'put_s': t.put_s,
                'packs': t.packs, 'recv_s': t.recv_s,
                'unknown_skus': t.unknown_skus,
            },
            'expected_utilization': {
                'pick': expected_utilization(units_day * s_pick, K, S),
            },
        }
        # K_max bounds, it does not set ("Choose the calibration procedure", decision 6):
        # a declared crew above the recorded window minimum is warned about and stamped.
        k_max = (constants.get('k_max') or {}).get(name)
        if k_max is not None and K > int(k_max):
            out['k_max_exceeded'][name] = {'declared': K, 'k_max': int(k_max)}
        # Site loads, per day.  Put-away is priced at s_put (one site value, measured or
        # seeded); receiving is EXACT -- the script's unload seconds need no constant.
        ch_put_s = t.per_day(t.put_units) * s_put
        ch_recv_s = t.per_day(t.recv_s)
        per_channel_put_s[name] = ch_put_s
        per_channel_recv_s[name] = ch_recv_s
        put_load_s += ch_put_s
        recv_load_s += ch_recv_s
        out['channels'][name] = rec

    put_crew = crew_size(put_load_s, S, float(inputs['rho_put']))
    recv_crew = crew_size(recv_load_s, S, float(inputs['rho_recv']))
    out['put'] = {
        'crew': put_crew,
        'mode': inputs.get('put_crew_mode'),
        's_put': constants['s_put'],
        'load_seconds_per_day': put_load_s,
        'load_units_per_day': sum(scripts[n].per_day(scripts[n].put_units)
                                  for n in channels),
        'f_put': float(inputs['f_put']),
        'expected_utilization': {n: expected_utilization(per_channel_put_s[n], put_crew, S)
                                 for n in channels},
    }
    total_packs = sum(scripts[n].per_day(scripts[n].packs) for n in channels)
    out['receiving'] = {
        'crew': recv_crew,
        'load_seconds_per_day': recv_load_s,
        'load_packs_per_day': total_packs,
        # Exact seconds per pack, derived from the script: the self-check value the
        # reference run compares its measured receiving labour against.
        's_recv': constant((recv_load_s / total_packs) if total_packs else 0.0, 'derived',
                           note='exact from the script: an unload has no travel term'),
        'f_recv': float(inputs['f_recv']),
        'expected_utilization': {n: expected_utilization(per_channel_recv_s[n], recv_crew, S)
                                 for n in channels},
    }
    return out


def derived_differs(a: dict | None, b: dict | None, *, rel_tol: float = 1e-9) -> list[str]:
    """The paths at which two derived blocks disagree, for the restore drift check.

    Floats compare with a tolerance, never `==` (the repo rule); everything else exactly.
    An empty list means "the same derivation".  Used on resume (raise) and on re-analysis
    (warn and stamp) -- "Design the staffing record", decision 3.
    """
    diffs: list[str] = []

    def _walk(x, y, path):
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y)):
                if k not in x or k not in y:
                    diffs.append(f'{path}/{k}')
                else:
                    _walk(x[k], y[k], f'{path}/{k}')
        elif isinstance(x, (int, float)) and isinstance(y, (int, float)) \
                and not isinstance(x, bool) and not isinstance(y, bool):
            if not math.isclose(float(x), float(y), rel_tol=rel_tol, abs_tol=1e-9):
                diffs.append(path)
        elif x != y:
            diffs.append(path)

    _walk(a or {}, b or {}, '')
    return diffs


def regime_orders(orders, regime: str | None) -> list:
    """The catalogue section one channel picks from: `regime_of(order) == regime`, or the
    whole catalogue when `regime` is None (the store-only path)."""
    if regime is None:
        return list(orders)
    return [c for c in orders if regime_of(c) == regime]
