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
           out=print) -> dict:
    """Every arm's verdict, `{leaf_label/arm_key: Verdict.as_dict()}`; prints one line each."""
    from Optimization.persistence.Picking_Data import load_shift_days
    from Optimization.runschema import resolve_base_dir, resolver_for
    from Optimization.simconfig import equilibrium as eq

    base = resolve_base_dir(root)
    rt = resolver_for(base)
    with open(rt.run_spec_json(), encoding='utf-8') as f:
        spec = json.load(f)
    staffing = spec.get('staffing') or {}
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
        for s in meta.get('strategies') or []:
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
            try:
                v = eq.check(db, run_id, lo, hi, expectations=arm_exp)
            except eq.InstrumentError as exc:
                out(f'{label} {s["key"]}: INSTRUMENT ERROR: {exc}')
                results[f'{label}/{s["key"]}'] = {'instrument_error': str(exc)}
                continue
            out(f'{label} {s["key"]}: {eq.summarize(v)}')
            results[f'{label}/{s["key"]}'] = v.as_dict()
    return results


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('run', help='run root (a path, or a bare name under COMPARISON_OUTPUT_DIR)')
    p.add_argument('--window', help='LO-HI working days to judge (default: every closed day)')
    p.add_argument('--arm', help='one strategy key (default: every arm)')
    p.add_argument('--json', help='write every verdict\'s as_dict() here')
    a = p.parse_args(argv)
    try:
        results = report(a.run, window=_window_arg(a.window), arm=a.arm)
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
