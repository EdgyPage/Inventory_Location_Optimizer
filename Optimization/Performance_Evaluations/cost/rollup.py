"""cost.rollup — the per-rule compute-cost table, and the two spans it must keep apart.

WHAT IS BEING MEASURED.  Two disjoint wall-clock spans per arm:

  * the SETUP span (`precomp_s`) — `strat.build()`, where the map family solves its
    offline address map.  Once per arm, before the batch loop's clock starts.
  * the LOOP sections (`reord_s`, `sim_s`, …) — a strict partition of `total_s`.

`reord_s` is the one that answers "what does this rule cost to run": it is the per-arrival
placement scoring, and FIFO's value is the floor every other rule is measured against,
because FIFO does the same reorder bookkeeping and then picks a slot at random.  The
DIFFERENCE over FIFO is the scoring itself.

WHY NOT RAW SECONDS.  They are contended (the sweep ran a worker pool), machine-specific,
and dominated by scale — the store and fulfillment channels differ by 3x on the same rule
for reasons that have nothing to do with the rule.  Two normalisations travel with every
row instead: the multiple of the FIFO floor, and milliseconds per unit placed, which is
the number a WMS engineer can compare against their own dock throughput.
"""
import csv
import json
import os
import statistics as st

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io
from Optimization.config.objectives import OBJECTIVES

#: The do-nothing rule every cost is quoted against.
BASE_RULE = 'fifo'


def _f(row, key):
    v = row.get(key)
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _med(vals):
    vals = [v for v in vals if v is not None]
    return st.median(vals) if vals else None


def _ratio(x, base):
    return (x / base) if (x is not None and base) else None


def units_placed(ctx) -> dict:
    """{(cell, pair, config, channel): units put away per wave} from the per-run tables.

    The denominator that turns seconds into an engineering number.  Read from the leaf
    summaries rather than assumed, and averaged across arms within a leaf: every arm faces
    the same demand, so they agree to rounding, and taking the mean makes a missing arm a
    small error rather than a wrong one.
    """
    out: dict = {}
    for row in ctx.leaf_rows('per_run_summary_csv'):
        key = (row.get('_cell'), row.get('_pair'), row.get('_config'), row.get('_channel'))
        v = _f(row, 'mean_reorder_placements')
        if v:
            out.setdefault(key, []).append(v)
    return {k: st.mean(v) for k, v in out.items()}


def cost_rows(ctx) -> list:
    """One row per (channel, rule), with both spans and both normalisations.

    Grouped by CHANNEL, not by leaf: a rule's compute cost is a property of the rule and
    the catalogue it scores against, and the two channels have genuinely different
    catalogues.  Cells and pairs collapse into the median, which is what makes `n_arms`
    worth printing beside it.
    """
    rows = ctx.runtime_rows()
    if not rows:
        return []
    units = units_placed(ctx)
    batches = _med([_f(r, 'batches') for r in rows]) or 0

    by: dict = {}
    for r in rows:
        by.setdefault((r.get('channel'), r.get('assignment')), []).append(r)

    # Per-unit cost needs the units for THIS channel, summed over the leaves the arms came
    # from; a channel's leaves all place the same amount, so the mean is the per-wave rate.
    per_channel_units: dict = {}
    for (cell, pair, cfg, ch), v in units.items():
        per_channel_units.setdefault(ch, []).append(v)
    per_channel_units = {k: st.mean(v) for k, v in per_channel_units.items()}

    out = []
    for ch in sorted({c for c, _a in by}):
        base = _med([_f(r, 'reord_s') for r in by.get((ch, BASE_RULE), [])])
        base_total = _med([_f(r, 'total_s') for r in by.get((ch, BASE_RULE), [])])
        placed_per_wave = per_channel_units.get(ch)
        placed_total = (placed_per_wave * batches) if placed_per_wave and batches else None
        for rule in sorted({a for c, a in by if c == ch}):
            arms = by[(ch, rule)]
            reord = _med([_f(r, 'reord_s') for r in arms])
            precomp = _med([_f(r, 'precomp_s') for r in arms])
            srcs = {r.get('precomp_src') for r in arms if r.get('precomp_src')}
            obj = OBJECTIVES.get(rule)
            scoring = (reord - base) if (reord is not None and base is not None) else None
            out.append(dict(
                channel=ch, rule=rule,
                label=obj.label if obj else rule,
                family=obj.family if obj else '',
                control=bool(obj.control) if obj else False,
                n_arms=len(arms),
                # setup span — NOT part of total_s; see runtime_metrics.OUTSIDE_TOTAL
                precomp_s=precomp,
                precomp_src=('/'.join(sorted(srcs)) if srcs else None),
                has_precompute=bool(obj and obj.precompute),
                map_lap_pct=_med([_f(r, 'map_lap_pct') for r in arms]),
                # Scope, so nobody reads the repeated value as an independent confirmation:
                # the map is solved over the whole catalogue, once per inventory pair, and
                # its exact/greedy split depends on bin geometry rather than on the channel's
                # pick constants.  Every channel row therefore carries the SAME measurement.
                map_lap_scope=('per catalogue (one solve per inventory pair; the same value '
                               'appears on every channel row)' if obj and obj.precompute
                               else None),
                precomp_scope=('once per arm, per inventory pair — a two-channel site does '
                               'not pay it twice' if obj and obj.precompute else None),
                # loop sections
                reord_s=reord,
                sim_s=_med([_f(r, 'sim_s') for r in arms]),
                total_s=_med([_f(r, 'total_s') for r in arms]),
                # normalisations: the two numbers that survive leaving this machine
                x_reord_vs_fifo=_ratio(reord, base),
                x_total_vs_fifo=_ratio(_med([_f(r, 'total_s') for r in arms]), base_total),
                scoring_s=scoring,
                # TWO per-unit numbers, because they answer different questions and mixing
                # them in one figure is what an SME review caught: the ABSOLUTE cost is what
                # a WMS engineer sizes against their dock, and the DELTA over the floor is
                # what the rule itself adds.  FIFO has a real absolute cost and a zero delta;
                # one column cannot be both.
                reord_ms_per_unit=((reord * 1000.0 / placed_total)
                                   if (reord is not None and placed_total) else None),
                scoring_ms_per_unit=((scoring * 1000.0 / placed_total)
                                     if (scoring is not None and placed_total) else None),
                reord_s_per_wave=(reord / batches) if (reord is not None and batches) else None,
                units_per_wave=placed_per_wave,
                n_batches=batches,
                # provenance: these are CONTENDED wall times.  A reader comparing them to
                # a dedicated benchmark needs to know that from the data, not a caption.
                workers=ctx.run_workers(),
                contended=ctx.run_workers() not in (None, 1),
            ))
    return out


@evaluation(key='cost.rollup', label='Per-rule compute cost (real wall seconds)',
            scope='run', needs=('runtime',), out_subdir='tables')
def render(ctx, params):
    rows = cost_rows(ctx)
    if not rows:
        return
    out = io.out_dir(ctx)
    path = os.path.join(out, 'compute_cost.csv')
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    ctx.log.info(f'  wrote {os.path.basename(path)} ({len(rows)} rows)')

    # The dossier index: one document a page can load to reach every part of this layer,
    # so no macro has to know the layout of the tree it is reading from.
    idx_dir = os.path.dirname(out)
    with open(os.path.join(idx_dir, 'dossier.json'), 'w', encoding='utf-8') as fh:
        json.dump({
            'run': os.path.basename(os.path.abspath(ctx.run_root)),
            'workers': ctx.run_workers(),
            'n_batches': rows[0]['n_batches'],
            'baseline_rule': BASE_RULE,
            'note': ('Wall-clock compute cost of the SIMULATOR, not modeled warehouse '
                     'labor. Setup and loop spans are disjoint: precomp_s is measured '
                     'before the batch loop starts and is not part of total_s.'),
            'cost': rows,
        }, fh, indent=2)
