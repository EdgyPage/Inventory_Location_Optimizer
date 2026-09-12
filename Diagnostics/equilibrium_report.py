"""equilibrium_report.py — the calibrated era's equilibrium check, per leaf, off a finished run.

Reads a run tree through its own contract and prints `Optimization/simconfig/equilibrium.py`'s
verdict for every arm of every channel run: the five clauses (labour, released_late,
utilization, supply, rework) over a window of working days, each with its reading, exactly
as the throughput audit logs them -- without re-running the analysis.  Built for "Split the
missed-share clause into supply and labour" (department-calibration 30), whose done-when is a
re-read of two finished runs, and for the re-check that follows it (31).

    python Diagnostics/equilibrium_report.py <run_root>                  # every closed day
    python Diagnostics/equilibrium_report.py <run_root> --window 20-39   # the measured window
    python Diagnostics/equilibrium_report.py <run_root> --arm fifo --json out.json

`<run_root>` is a run directory under the `COMPARISON_OUTPUT_DIR` from `.env` (a path, or a
bare name resolved against it) -- read the key, never paste a machine-local path.

The expectations come off the run's OWN staffing record (the run spec at the run root, the
same object `run_analysis` stamps onto `sim_result`), keyed by the leaf's pair and channel, and re-centred
per arm on its stamped `expected_pick` -- the same three calls the audit makes
(`expectations_for`, `arm_expectations`, `check`), so the two cannot disagree.  A leaf whose
record carries no derived block (a flag-off run) is reported as such and skipped.  A window
outside the ledger's closed days is refused per arm rather than judged over days that never
happened.

Exit status: 0 when every judged arm passed, 1 when any failed, 2 when nothing could be
judged.  The `--json` file carries every verdict's `as_dict()` under `<cell>/<leaf>/<arm>`,
which is what a ticket's answer quotes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# ── path setup: repo root on sys.path so package imports resolve when run as a script ──
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _window_arg(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    lo, _, hi = text.partition('-')
    return int(lo), int(hi)


def _leaf_label(cell: str, cr) -> str:
    return '/'.join(p for p in (cell, cr.pair, cr.config, cr.channel) if p)


def _db_path(rt, cr, strategy: dict) -> str:
    """The arm's sim DB through the run's own contract (`leaf_path`), never the `db_path` the
    leaf's meta recorded: that field belongs to the machine that ran it and goes stale when a
    run is moved (memory `results-drive-location`)."""
    return rt.leaf_path(cr, 'sim_db', strategy=strategy['key'])


def report(root: str, *, window: tuple[int, int] | None = None, arm: str | None = None,
           buckets: bool = True, out=print) -> dict:
    """Every arm's verdict, `{leaf_label/arm_key: Verdict.as_dict()}`; prints one line each,
    followed (unless `buckets=False`) by the rework clause's per-bucket depth table."""
    from Optimization.persistence.Picking_Data import load_shift_days
    from Optimization.runschema import resolve_base_dir, resolver_for
    from Optimization.simconfig import equilibrium as eq

    base = resolve_base_dir(root)
    rt = resolver_for(base)
    with open(rt.run_spec_json(), encoding='utf-8') as f:
        spec = json.load(f)
    staffing = spec.get('staffing') or {}
    # THE COUPLED MARKER COSTS NOTHING HERE: `resolver_for` keeps the parsed layout on the
    # resolver, so this is zero new I/O and -- because it never spells a filename -- zero
    # new budget in the run-tree consumption ratchet.
    coupled = bool((rt.layout or {}).get('coupled'))
    #: On a coupled run the leaf verdict keeps `pick` and DROPS put and recv: those two are
    #: the SITE's crews, and a leaf's seconds over a site crew is a share of a whole the
    #: channel does not own. They are banded once, below, over both leaves' rows.
    leaf_depts = eq.LEAF_DEPARTMENTS if coupled else eq.DEPARTMENTS
    #: `(cell, pair, arm-pair)` -> `{channel: (rows, expectations, days)}`, accumulated as
    #: the leaves are walked and banded once each pair is complete.  The REPORT assembles it
    #: because the report is the half that walks the tree -- `equilibrium.py` states "no
    #: CONFIG, no settings, no run tree" and means it.
    site_rows: dict = {}
    results: dict = {}
    for cell, cr in rt.channel_runs():
        with open(rt.sim_meta(cr), encoding='utf-8') as f:
            meta = json.load(f)
        pair = meta.get('inventory') or cr.pair
        channel = meta.get('channel') or cr.channel
        label = _leaf_label(cell, cr)
        try:
            expectations = eq.expectations_for(staffing, pair=pair, channel=channel)
        except eq.RecordError as exc:
            out(f'{label}: no staffing expectations ({exc}); skipped')
            continue
        for rank, s in enumerate(meta.get('strategies') or []):
            if arm and s['key'] != arm:
                continue
            db = _db_path(rt, cr, s)
            run_id = int(s['run_id'])
            ledger = eq.window_of(load_shift_days(db, run_id))
            if ledger is None:
                out(f'{label} {s["key"]}: no day was ever closed out; skipped')
                continue
            lo, hi = window or ledger
            if lo < ledger[0] or hi > ledger[1]:
                out(f'{label} {s["key"]}: window {lo}-{hi} is outside the ledger\'s closed '
                    f'days {ledger[0]}-{ledger[1]}; refused')
                continue
            arm_exp = eq.arm_expectations(expectations, s.get('expected_pick'))
            if coupled:
                # The rows this leaf contributes to its site's one banded clause.  Loaded
                # here, where the db and the window are already resolved, rather than in a
                # second walk that could disagree about either.
                from Optimization.persistence.Picking_Data import (
                    load_batch_stats, load_work_hours)
                site_rows.setdefault((cell, cr.pair, rank), {})[channel] = (
                    (load_batch_stats(db, run_id), load_work_hours(db, run_id)),
                    arm_exp, list(range(lo, hi + 1)), s['key'])
            try:
                v = eq.check(db, run_id, lo, hi, expectations=arm_exp,
                             departments=leaf_depts)
            except eq.InstrumentError as exc:
                out(f'{label} {s["key"]}: INSTRUMENT ERROR: {exc}')
                results[f'{label}/{s["key"]}'] = {'instrument_error': str(exc)}
                continue
            out(f'{label} {s["key"]}: {eq.summarize(v)}')
            if buckets:
                for line in bucket_table(v):
                    out('    ' + line)
            results[f'{label}/{s["key"]}'] = v.as_dict()
    results.update(_site_verdicts(site_rows, out))
    return results


def _site_verdicts(site_rows: dict, out) -> dict:
    """One SITE verdict per `(cell, pair, arm-pair)`: put and recv banded ONCE.

    Keyed by `(cell, pair, RANK)`, never by an arm key, and that is the whole of it: the
    diagonal is rank against rank (site-dock 06), so the two halves of one pair are named
    differently on any run whose channels sweep different rule subsets -- and keying on the
    key would then put each leaf in its own bucket, leave every bucket one channel short,
    and turn the site clause off for the entire run while reporting it as skipped.  The rank
    is the leaf's position in its OWN ordered `sim_meta['strategies']`, which is the same
    ordering `_prepare_site_run` zips the pair out of.

    A pair with one channel is not banded: a site clause over half a site is a smaller
    denominator, not a smaller site, and it would pass or fail for the wrong reason.
    """
    from Optimization.simconfig import equilibrium as eq
    out_results: dict = {}
    for (cell, pair, rank), by_channel in sorted(site_rows.items()):
        label = '/'.join(p for p in (cell, pair, 'site') if p)
        # The ARM PAIR, spelled the way the site DB's own filename stem spells it: the two
        # halves in declared channel order, store first.  A rank is what IDENTIFIES the pair;
        # this is what a reader recognises it by.
        arm = '__'.join(by_channel[ch][3] for ch in ('store', 'fulfillment')
                        if ch in by_channel)
        if len(by_channel) < 2:
            out(f'{label} {arm}: only {sorted(by_channel)} reached the site clause; '
                f'skipped -- put and recv are the SITE crews and half a site is a wrong '
                f'denominator, not a small one')
            continue
        windows = {ch: (d[0], d[-1]) for ch, (_r, _e, d, _k) in by_channel.items()}
        if len(set(windows.values())) != 1:
            # A leaf that closed fewer days would be banded over days it never ran in:
            # `granted = crew x S x n_days` is then wrong for one channel while the realized
            # sum mixes two windows, and the clause still returns a pass/FAIL. Refused for
            # the same reason half a site is.
            out(f'{label} {arm}: the channels closed different windows ({windows}); '
                f'skipped -- one site crew works one day, and banding two windows as one '
                f'grants hours nobody worked')
            continue
        days = next(iter(by_channel.values()))[2]
        clause = eq.site_utilization_clause(
            {ch: rows for ch, (rows, _e, _d, _k) in by_channel.items()}, days,
            {ch: exp for ch, (_r, exp, _d, _k) in by_channel.items()})
        out(f'{label} {arm}: site_utilization='
            f'{"ok" if clause.passed else "FAIL"} '
            f'({_site_reading(clause)})')
        out_results[f'{label}/{arm}'] = clause.as_dict()
    return out_results


def _site_reading(clause) -> str:
    """The site clause as one line: each department's banded site number, then the
    per-channel SHARES beside it -- never instead of it (memory
    `a-right-site-total-hides-two-wrong-shares`: a right site total hid two bands failing in
    opposite directions)."""
    parts = []
    for dept, r in clause.reading.items():
        if 'absent' in r:
            parts.append(f'{dept} absent ({r["absent"]})')
            continue
        shares = ' '.join(f'{ch[:1]}={v["share_of_site_grant"]:.3f}'
                          for ch, v in r['per_channel'].items())
        parts.append(f'{dept} {r["realized"]:.3f} vs {r["expected"]:.3f} '
                     f'[{shares}]')
    return '; '.join(parts)


def bucket_table(verdict) -> list[str]:
    """The rework clause's per-bucket depth as text rows: one line per bucket of the leaf's
    section -- setup free (the record), the window's first and last readings, its minimum
    and mean, the drawdown, and the batches that read dry.  A header line first; one line
    saying so when the run's vintage never recorded the depth per bucket."""
    b = verdict.clauses['rework'].reading.get('buckets') or {}
    if not b:
        return ['free index per bucket: unrecorded on this vintage']
    w = max(len(k) for k in b)
    lines = [f'{"bucket":<{w}}  {"setup":>10} {"first":>10} {"min":>10} {"mean":>12} '
             f'{"last":>10} {"drawdown":>10}  dry batches']
    for k, v in b.items():
        def _n(x, fmt=',d'):
            return '-' if x is None else format(x, fmt)
        lines.append(f'{k:<{w}}  {_n(v["setup_free"]):>10} {_n(v["first"]):>10} '
                     f'{_n(v["min"]):>10} {_n(v["mean"], ",.1f"):>12} {_n(v["last"]):>10} '
                     f'{_n(v["drawdown"], "+,d"):>10}  '
                     f'{len(v["dry_batches"]) if v["dry_batches"] else "-"}')
    return lines


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('run', help='run root (a path, or a bare name under COMPARISON_OUTPUT_DIR)')
    p.add_argument('--window', help='LO-HI working days to judge (default: every closed day)')
    p.add_argument('--arm', help='one strategy key (default: every arm)')
    p.add_argument('--json', help='write every verdict\'s as_dict() here')
    p.add_argument('--no-buckets', action='store_true',
                   help='omit the per-bucket free-index table under each arm')
    a = p.parse_args(argv)
    try:
        results = report(a.run, window=_window_arg(a.window), arm=a.arm,
                         buckets=not a.no_buckets)
    except Exception as exc:                                   # noqa: BLE001 - reported
        print(f'equilibrium_report: cannot read {a.run!r}: {exc}')
        return 2
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=1, default=str)
        print(f'equilibrium_report: {len(results)} verdict(s) -> {a.json}')
    judged = [r for r in results.values() if 'passed' in r]
    failed = [r for r in judged if not r['passed']]
    print(f'equilibrium_report: {len(judged)} arm(s) judged, {len(failed)} FAILED, '
          f'{len(results) - len(judged)} instrument error(s)')
    if not judged:
        return 2
    return 1 if failed or len(judged) != len(results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
