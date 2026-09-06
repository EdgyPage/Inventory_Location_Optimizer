"""throughput.audit — declared throughput vs delivered: the calibrated era's audit.

The second caller of the equilibrium check ("Declare the equilibrium bands", decision 6):
`Optimization/simconfig/equilibrium.check_rows` judged over EVERY day the ledger closed,
per arm, and rendered as realized-vs-expected utilization per department plus the
drained / capped day count.  The first caller, the reference-run driver, uses the same
function as a PRECONDITION; this one REPORTS (decision 7):

  - a CAPPED day is flagged "declared throughput not delivered";
  - a picking utilization BELOW the band on a campaign arm is the arm's travel saving --
    the effect being measured -- and is labelled as such, never as a failure;
  - a nonzero `released_late` on a drained day still RAISES (`InstrumentError`): that is
    the instrument contradicting itself, and the driver records it as an error for this
    evaluation rather than a figure quietly not appearing.

Nothing here fails a run.  The sim never judges itself, and neither does the audit.

## Two marks, one quantity

`days_capped` is the ONE quantity (ranked, absolute + percent vs baseline): a capped day
has an unambiguous direction.  Realized vs expected utilization has none -- the same
below-band read is a saving on one arm and a broken derivation on another -- so it lives
in an INSPECTION table, the family grammar's slot for a read-out with no baseline in it,
exactly as `yard.scorecard` holds door utilization.  Expected values, `band_tol`, the
crews and the calibration stamps come off the `staffing` record stamped onto
`sim_result` (03's sixth seam) through `ctx.staffing_expectations()`; the audit needs no
seam work and reads nothing from CONFIG.

## Gated by the ledger

The quantity names the `shift_days` capability, so the era gate refuses the render on
every run without a drain-or-cap ledger -- the whole archive, and every flag-off run since
-- with a reason in the log.  A run that HAS a ledger but no staffing expectations (its
record carries no derived block) still renders the day verdicts; the utilization rows
read "n/a" rather than zero.
"""
import os

import numpy as np

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import chartkit, io, marks
from Optimization.Performance_Evaluations.common.style import _stitle
from Optimization.Performance_Evaluations.core import quantities as _q
from Optimization.simconfig import equilibrium as _eq

_COLS = ('arm', 'days', 'drained', 'capped', 'overtime', 'dept', 'crew', 'expected',
         'realized', 'band', 'read')

#: relative column widths, so the capped and read cells have room for their words
_COL_W = (1.4, 0.5, 0.6, 1.2, 0.7, 0.9, 0.5, 0.8, 0.8, 0.6, 1.5)

_DEPT_LABEL = {'pick': 'picking', 'put': 'put-away', 'recv': 'receiving'}


def _verdict_for(ctx, key, sdf, expectations):
    """The check over every day this arm's ledger closed, or None without expectations."""
    if expectations is None or sdf.empty:
        return None
    lo, hi = int(sdf['day'].min()), int(sdf['day'].max())
    bdf, wdf = ctx.batch_df(key), ctx.work_df(key)
    shift_rows = sdf.to_dict('records')
    batch_rows = bdf.to_dict('records') if not bdf.empty else []
    work_rows = []
    if not wdf.empty:
        for r in wdf.to_dict('records'):
            work_rows.append({'batch_id': r['batch_id'], 'role': 'put',
                              'seconds': r['put_seconds']})
            work_rows.append({'batch_id': r['batch_id'], 'role': 'receive',
                              'seconds': r['unload_seconds']})
    return _eq.check_rows(shift_rows=shift_rows, batch_rows=batch_rows, work_rows=work_rows,
                          day_lo=lo, day_hi=hi, expectations=expectations)


def _read(dept, reading, is_base, tol):
    """The one-word reading of a department's band position, in the audit's vocabulary."""
    if 'realized' not in reading:
        return 'n/a'
    delta = reading['delta']
    if abs(delta) <= tol:
        return 'in band'
    if dept == 'pick' and delta < 0 and not is_base:
        return 'below: travel saving'
    return 'below band' if delta < 0 else 'ABOVE band'


def _rows(ctx, s, sdf, verdict, expectations):
    n = int(len(sdf))
    capped = int(sdf['capped'].sum())
    overtime = int((sdf['overtime_s'] > 0).sum())
    # "not delivered" = declared throughput not delivered, spelled out in the subtitle.
    head = [_stitle(s), str(n), str(n - capped),
            (f'{capped} · not delivered' if capped else '0'), str(overtime)]
    tol = float(expectations['band_tol']) if expectations else float('nan')
    is_base = s['key'] == ctx.base['key']
    out = []
    util = verdict.clauses['utilization'].reading if verdict is not None else {}
    for i, dept in enumerate(_eq.DEPARTMENTS):
        r = util.get(dept, {})
        spec = ((expectations or {}).get('departments') or {}).get(dept)
        row = list(head) if i == 0 else [''] * len(head)
        if spec is None or 'realized' not in r:
            why = ((expectations or {}).get('absent') or {}).get(dept, 'no record')
            row += [_DEPT_LABEL[dept], '-', '-', '-', '-', f'n/a ({why})']
        else:
            row += [_DEPT_LABEL[dept], str(r['crew']), f'{r["expected"]:.3f}',
                    f'{r["realized"]:.3f}', f'±{tol:.2f}', _read(dept, r, is_base, tol)]
        out.append(row)
    return out


def _flags(expectations):
    if not expectations:
        return 'no staffing record on this run: expected values unavailable'
    f = expectations['flags']
    bits = []
    if f.get('calibration_stale'):
        bits.append('calibration STALE (record measured on another catalogue)')
    elif not f.get('calibration_measured'):
        bits.append('calibration is the pass-0 SEED (no reference run yet)')
    if f.get('k_max_exceeded'):
        bits.append('declared pickers exceed K_max')
    if f.get('saturated'):
        bits.append('batch content saturated (every SKU every day)')
    return ' · '.join(bits) if bits else 'calibration measured on this catalogue'


@evaluation(key='throughput.audit', label='Throughput audit: declared vs delivered',
            scope='config', needs=('shift', 'batch'),
            family='throughput', shape=('ranked', 'inspection'),
            quantities=('days_capped',))
def render(ctx, params):
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
    expectations = ctx.staffing_expectations()
    frames = {s['key']: ctx.shift_df(s['key']) for s in ctx.strategies}
    out = io.out_dir(ctx)
    n = 0

    # ── the quantity: days capped per arm, ranked ────────────────────────────────
    q = _q.BY_KEY['days_capped']
    entries = [(s, float(frames[s['key']]['capped'].sum()))
               for s in ctx.strategies if not frames[s['key']].empty]
    for view in EVAL_BY_KEY['throughput.audit'].views:
        if view not in _q.derive_views(q, _q.SHAPE_BY_NAME['ranked']) or not entries:
            continue
        ch = chartkit.make(panels=1, legend='none', panel_w=7.0,
                           panel_h=chartkit.height_for_categories(len(entries), per=0.3,
                                                                  base=2.0))
        if not marks.ranked(ch, entries, quantity=q, view=view, baseline=ctx.base,
                            strategies=ctx.strategies):
            ch.abandon()
            continue
        ch.title(f'{q.label} per arm',
                 'a capped day is declared throughput not delivered · a report, never a '
                 'failure')
        n += bool(ch.save(os.path.join(out, f'{view}_days_capped.png'), view=view))

    # ── the inspection table: realized vs expected per department ────────────────
    rows = []
    for s in ctx.strategies:
        sdf = frames[s['key']]
        if sdf.empty:
            continue
        verdict = _verdict_for(ctx, s['key'], sdf, expectations)
        if verdict is not None:
            ctx.log.info(f'  [audit] {_stitle(s)}: {_eq.summarize(verdict)}')
        rows.extend(_rows(ctx, s, sdf, verdict, expectations))
    if rows:
        ch = chartkit.make(panels=1, panel_w=13.0, legend='none',
                           panel_h=chartkit.height_for_categories(len(rows), per=0.36,
                                                                  base=1.4))
        ax = ch.ax
        ax.axis('off')
        ax.grid(False)
        tbl = ax.table(cellText=rows, colLabels=list(_COLS), cellLoc='center',
                       colWidths=_COL_W, bbox=[0.0, 0.0, 1.0, 1.0])
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(7.5)
        cells = tbl.get_celld()
        for c in range(len(_COLS)):
            hc = cells[(0, c)]
            hc.set_facecolor('#34495e')
            hc.set_text_props(color='white', fontweight='bold', fontsize=7)
        band = (f'band ±{expectations["band_tol"]:.2f} around the derivation\'s expected '
                f'utilization' if expectations else 'no band: no staffing record')
        ch.title('Throughput audit: realized vs expected utilization',
                 f'inspection only · {band} · worked ÷ granted over every closed day, a '
                 f'ratio of sums · "not delivered" = declared throughput not delivered · '
                 f'{_flags(expectations)}')
        n += bool(ch.save(os.path.join(out, 'absolute_throughput_audit.png'),
                          view='absolute'))
    ctx.log.info(f'  throughput audit: {n} figures -> {out}')
