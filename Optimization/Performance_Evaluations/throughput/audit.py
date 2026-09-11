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

A DRAINED day is a LABOUR verdict (`equilibrium.is_drained`, amending decision 4 by "Choose
the coverage floor", decision 7): the supply carry -- demand no bin could serve -- is stock
not delivered, the `supply` clause's quantity, and never keeps a day from draining.  The
per-day frame carries both halves (`standing_carry_labour` / `standing_carry_supply`) and
the labour clause's `drained` reading lists the days that closed with supply carry standing.

Since "Split the missed-share clause into supply and labour" (2026-09-09) the check judges
the two causes apart, both as `carryover` FLOWS over FRESH demand (`equilibrium.demand_flows`,
fed from `ctx.carry_df`): the `labour` clause reads the realized cut share against the
stamped expected cut share (ADR-0004) with the standing labour carry bounded and not
trending, and the `supply` clause reads the first-attempt supply share against the stamped
`1 - fill`.  "Every day drained" is a reading inside the labour clause, never a verdict.
The two shares ride the inspection table as two more rows per arm beside the departments.

Since "Band the own-bin share and the free-index depth" (2026-09-10) the `rework` clause
judges the tier spill, the own-bin top-up and the repack at exactly zero and reads the
free-index depth PER BUCKET off the `free_index` table (`ctx.free_index_df`) against the
record's per-bucket setup free; the table carries the three events and the depth (the
section and its driest bucket) as five more rows per arm.

Overtime caps a day ("Overtime behind a drained day raises the instrument", 2026-09-07):
a day whose last task finished past its cap is labour that did not fit the day, and the
loader serves a pre-amendment ledger's `drained` with that term folded in -- which is what
lets the store leaf of the line-floor check render at all (its day 3, stamped drained with
138 s of overtime, raised the released-late clause behind it).

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

_COLS = ('arm', 'days', 'drained', 'capped', 'overtime', 'reading', 'crew', 'expected',
         'realized', 'band', 'read')

#: relative column widths, so the capped and read cells have room for their words
_COL_W = (1.4, 0.5, 0.6, 1.2, 0.7, 0.9, 0.5, 0.8, 0.8, 0.6, 1.5)

_DEPT_LABEL = {'pick': 'picking', 'put': 'put-away', 'recv': 'receiving'}

#: The two flow clauses' rows in the inspection table: (clause, label, decimals).
_SHARE_ROWS = (('supply', 'supply share', 3), ('labour', 'cut share', 4))

#: The rework clause's three events in the inspection table: (reading key, label).  Each is
#: judged at exactly zero (the spill and the top-up with no knob; the repack against the
#: record's stamped `f_repack`), so the "expected" cell is 0 and the read names the dry
#: bucket(s) -- department-calibration 32.  The free-index depth follows as two REPORTED
#: rows: the section's setup free against its window-end reading with the drawdown in the
#: read cell, then the driest bucket by its window minimum against its own setup free.
_REWORK_ROWS = (('spills', 'tier spills'), ('topups', 'own-bin top-ups'),
                ('repacked_packs', 'repacked packs'))


def _verdict_for(ctx, key, sdf, expectations):
    """The check over every day this arm's ledger closed, or None without expectations."""
    if expectations is None or sdf.empty:
        return None
    lo, hi = int(sdf['day'].min()), int(sdf['day'].max())
    bdf, wdf, cdf = ctx.batch_df(key), ctx.work_df(key), ctx.carry_df(key)
    fdf = ctx.free_index_df(key)
    shift_rows = sdf.to_dict('records')
    batch_rows = bdf.to_dict('records') if not bdf.empty else []
    carry_rows = cdf.to_dict('records') if not cdf.empty else []
    free_rows = fdf.to_dict('records') if not fdf.empty else []
    work_rows = []
    if not wdf.empty:
        for r in wdf.to_dict('records'):
            work_rows.append({'batch_id': r['batch_id'], 'role': 'put',
                              'seconds': r['put_seconds']})
            work_rows.append({'batch_id': r['batch_id'], 'role': 'receive',
                              'seconds': r['unload_seconds']})
    return _eq.check_rows(shift_rows=shift_rows, batch_rows=batch_rows, work_rows=work_rows,
                          carry_rows=carry_rows, day_lo=lo, day_hi=hi,
                          expectations=expectations, free_rows=free_rows)


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


def _read_share(reading):
    """The one-word reading of a flow clause's level, plus its trend and (labour) its carry.

    `in_band` is None when the record carries no expectation for the level -- the reading
    says so rather than pretending; a trend past its tolerance or a carry past a day's
    capacity is named even when the level sits in band, because each fails the clause on
    its own."""
    if not reading or reading.get('level') is None:
        return 'n/a'
    ok = reading.get('in_band')
    word = ('n/a (no expectation)' if ok is None
            else 'in band' if ok
            else ('below band' if reading['delta'] < 0 else 'ABOVE band'))
    extra = []
    trend = reading.get('trend')
    if trend is not None and abs(trend) > reading.get('trend_tol', _eq.TREND_TOL):
        extra.append('trending')
    if reading.get('carry_bounded') is False:
        extra.append('carry past a day')
    return word + (f' · {", ".join(extra)}' if extra else '')


def _share_rows(head, verdict):
    """The supply and cut-share rows of the inspection table for one arm, after the
    departments: expected / realized / band / read, the crew cell blank."""
    out = []
    for clause, label, nd in _SHARE_ROWS:
        r = verdict.clauses[clause].reading if verdict is not None else {}
        row = [''] * len(head)
        lvl, exp, tol = r.get('level'), r.get('expected'), r.get('tol')
        if lvl is None:
            row += [label, '-', '-', '-', '-', 'n/a']
        else:
            row += [label, '-',
                    (f'{exp:.{nd}f}' if exp is not None else '-'),
                    f'{lvl:.{nd}f}',
                    (f'±{tol:.{nd}f}' if tol is not None else '-'),
                    _read_share(r)]
        out.append(row)
        ld = r.get('lead') or {}
        if clause == 'supply' and (ld.get('stamped_days') is not None
                                   or ld.get('realized_days') is not None):
            # Only where there is a lead to report: a pre-lead archive's table is unchanged.
            out.append(_lead_row(head, r))
    return out


def _lead_row(head, reading):
    """The order-to-shelf lead row under the supply share: the STAMPED lead as the expected
    cell, the REALIZED lead (Little's law over the window, `equilibrium.realized_lead`) as
    the realized cell, no band, and the read naming the EXPLAINED supply level
    `1 - fill(realized)` off the record's curve ("Declare the coverage against the inbound
    lead", decision 10) -- reported, never judged."""
    ld = (reading or {}).get('lead') or {}
    row = [''] * len(head)
    st, rz, ex = ld.get('stamped_days'), ld.get('realized_days'), ld.get('explained')
    if st is None and rz is None:
        row += ['order-to-shelf lead', '-', '-', '-', '-', 'n/a (unstamped, unmeasured)']
        return row
    read = 'reported, not judged'
    if ex is not None:
        read += f' · explains supply {ex:.3f}'
    elif st is None:
        read += ' · lead unstamped on this record'
    elif rz is None:
        read += ' · no orders in the window'
    row += ['order-to-shelf lead', '-',
            (f'{st:.3f} d' if st is not None else '-'),
            (f'{rz:.3f} d' if rz is not None else '-'), '-', read]
    return row


def _rework_rows(head, verdict):
    """The rework clause's rows of the inspection table for one arm, after the shares: the
    three events judged at zero, then the per-bucket depth as a report."""
    out = []
    r = verdict.clauses['rework'].reading if verdict is not None else {}
    dry = r.get('dry_buckets') or []
    where = (f' · dry: {", ".join(dry)}' if dry
             else ('' if r.get('buckets_recorded') else ' · depth unrecorded per bucket'))
    for key, label in _REWORK_ROWS:
        row = [''] * len(head)
        n = r.get(key)
        exp = (r.get('expected_repacked_packs') if key == 'repacked_packs' else 0)
        if not r:
            row += [label, '-', '-', '-', '-', 'n/a']
        elif n is None:
            row += [label, '-', '0', 'unrecorded', '-', 'n/a (unrecorded on this vintage)']
        else:
            exp_txt = ('-' if exp is None else f'{exp:g}')
            read = ('none' if n == 0 else f'ABOVE zero{where}')
            if key == 'repacked_packs' and exp is None:
                read = 'reported, not judged (no f_repack)'
            row += [label, '-', exp_txt, f'{int(n):,}', '0', read]
        out.append(row)
    # The depth, two rows: the section (the record's buckets) setup vs window end with the
    # drawdown in the read cell, then the DRIEST bucket by its window minimum -- its own
    # setup free against its minimum, the label in the read cell.  Two rows because one
    # cell cannot hold a bucket label beside the numbers at this font (measured: the single
    # row overflowed two columns on the 60-bucket store leaf).
    b = {k: v for k, v in (r.get('buckets') or {}).items() if v.get('last') is not None}
    row = [''] * len(head)
    if not b:
        row += ['free index', '-', '-', '-', '-',
                'n/a (per-bucket depth unrecorded)' if r else 'n/a']
        out.append(row)
        row = [''] * len(head)
        row += ['driest bucket', '-', '-', '-', '-', 'n/a']
        out.append(row)
        return out
    setup = [v['setup_free'] for v in b.values() if v.get('setup_free') is not None]
    last = sum(v['last'] for v in b.values())
    dd = sum(v['drawdown'] for v in b.values())
    row += ['free index', '-', (f'{sum(setup):,} at setup' if setup else '-'),
            f'{last:,}', '-', f'drawdown {dd:+,} over {len(b)} bucket(s)']
    out.append(row)
    driest = min(b, key=lambda k: b[k]['min'])
    d = b[driest]
    row = [''] * len(head)
    row += ['driest bucket', '-',
            (f'{d["setup_free"]:,} at setup' if d.get('setup_free') is not None else '-'),
            f'{d["min"]:,}', '-', _short_bucket(driest)]
    out.append(row)
    return out


def _short_bucket(label: str) -> str:
    """`handling/category/size` for the table: the `unit` segment is implied by the size
    class in this repo (`ff_*` is fulfillment, `singleton` is singleton, the rest pallet)
    and the full label does not fit the cell.  Display only -- the reading keeps the key."""
    parts = label.split('/')
    return '/'.join(parts[:3]) if len(parts) == 4 else label


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
            reading = _read(dept, r, is_base, tol)
            if dept == 'recv':
                # THE DOCK'S PARALLELISM CEILING beside the receiving row, reported and
                # never banded ("Decide the contention regime under the derived crew", 4).
                # The crew is derived as a site total and the dock is capped at `cap` per
                # door, so when `cap x doors` seats less than the whole crew a receiving
                # utilization below its band is the DOCK, not a staffing error — and the
                # reader should not have to infer that from a door count elsewhere.
                # Printed whether or not it binds: "the dock could seat everyone" is the
                # fact that makes a below-band reading damning rather than explained.
                ceil_v = ctx.dock_ceiling()
                if ceil_v is not None:
                    reading += f' · dock seats {ceil_v * 100.0:.0f}%'
            row += [_DEPT_LABEL[dept], str(r['crew']), f'{r["expected"]:.3f}',
                    f'{r["realized"]:.3f}', f'±{tol:.2f}', reading]
        out.append(row)
    out.extend(_share_rows(head, verdict))
    out.extend(_rework_rows(head, verdict))
    return out


def _flags(expectations):
    if not expectations:
        return 'no staffing record on this run: expected values unavailable'
    f = expectations['flags']
    bits = []
    if f.get('overridden'):
        bits.append('a declared override replaced the expected seconds per unit')
    if f.get('saturated'):
        bits.append('batch content saturated (every SKU every day)')
    exp_miss = expectations.get('expected_missed_share')
    if exp_miss is not None:
        # The record's stamped first-pass fill rate ("Choose the coverage floor", decision 5):
        # the level the `supply` clause is read against, printed per arm by `summarize`.
        bits.append(f'expected supply share {exp_miss:.3f} (1 - the stamped fill rate)')
    exp_cut = expectations.get('expected_cut_share')
    if exp_cut is not None:
        # ADR-0004's stamped guarantee: the level the `labour` clause is read against.
        bits.append(f'expected cut share {exp_cut:.4f} (the stamped guarantee)')
    return (' · '.join(bits) if bits
            else "expectations from the closed form on this run's geometry")


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
        # The arm's own band: the pair's expectations re-centred on the expected seconds per
        # unit its initial placement gives (stamped by the worker; absent flag-off).
        arm_exp = _eq.arm_expectations(expectations, s.get('expected_pick'))
        verdict = _verdict_for(ctx, s['key'], sdf, arm_exp)
        if verdict is not None:
            ctx.log.info(f'  [audit] {_stitle(s)}: {_eq.summarize(verdict)}')
        rows.extend(_rows(ctx, s, sdf, verdict, arm_exp))
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
