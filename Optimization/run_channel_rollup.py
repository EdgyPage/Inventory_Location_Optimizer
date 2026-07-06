"""run_channel_rollup.py — combine per-channel best plans into a whole-warehouse saving.

The simulation treats store and fulfillment as two INDEPENDENT sections of the warehouse, each
sweeping its OWN set of pick-time regression configs and analyzed on its own by run_analysis.py
(its own `fifo` baseline, its own winner).  This post-analysis step is the ONE place that
combines them: for each inventory it reads every channel's `series.json`, computes each plan's
absolute production-time saving vs that channel's `fifo` baseline, picks the best (config, plan)
per channel across ALL that channel's configs, and SUMS the per-channel best savings into a
cumulative whole-warehouse saving.

Because the channels are independent, absolute savings (sim-unit `ss_prod_hours` deltas) are
ADDITIVE — any (store-plan, fulfillment-plan) pairing is just the sum of the two rows.  So the
per-plan CSV this writes doubles as a mix-and-match table: no cross-product simulation needed.

Run AFTER run_analysis.py (which writes the series.json files).  IMPORTANT: run the
analysis with --preset BY_INITIAL so BOTH uni_* and opt_* arms land in series.json;
the default preset (focus='uni') drops every opt_* arm, so the rollup would pick the
best from only half the suite (it warns when it detects this):
  python run_analysis.py <base_dir> --preset BY_INITIAL
  python run_channel_rollup.py <base_dir>
Outputs (under <base_dir>):
  channel_rollup.csv          — one row per (inventory, config, channel, plan): absolute + % saving
  channel_rollup_summary.csv  — best plan per channel + the cumulative whole-warehouse saving
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))


def _find_series(base_dir: str) -> list[tuple[str, str]]:
    """(sim_meta.json, series.json) pairs under base_dir — one per analyzed channel run."""
    out = []
    for root, _dirs, files in os.walk(base_dir):
        if 'sim_meta.json' in files and 'series.json' in files:
            out.append((os.path.join(root, 'sim_meta.json'),
                        os.path.join(root, 'series.json')))
    return sorted(out)


def _baseline_entry(strategies: list[dict]) -> dict | None:
    """The channel's baseline plan — the fifo arm (uni_fifo), matching run_analysis's
    ctx.base = strategies[0].  Prefer an explicit fifo key; fall back to the first entry."""
    if not strategies:
        return None
    for s in strategies:
        if str(s.get('key', '')).startswith('uni_fifo'):
            return s
    for s in strategies:
        if 'fifo' in str(s.get('key', '')):
            return s
    return strategies[0]


def _channel_rows(meta: dict, series: dict) -> tuple[dict, list[dict]]:
    """Return (channel_info, per-plan rows) for one channel's series.json."""
    strategies = series.get('strategies', [])
    base = _baseline_entry(strategies)
    base_ss = base.get('ss_prod_hours') if base else None
    channel = meta.get('channel') or os.path.basename(os.path.dirname(''))
    # Arms the simulation ran but the analysis dropped from series.json (run_analysis's
    # default focus='uni' filters out every opt_* arm).  If present, the rollup can only
    # see a subset — flag it so the winner isn't silently chosen from half the suite.
    series_keys = {s.get('key') for s in strategies}
    dropped = [s.get('key') for s in meta.get('strategies', [])
               if s.get('key') not in series_keys]
    info = dict(pair=meta.get('inventory', '?'), config=meta.get('name', '?'),
                channel=channel, base_key=(base or {}).get('key'), base_ss=base_ss,
                dropped_arms=dropped)
    rows = []
    for s in strategies:
        ss = s.get('ss_prod_hours')
        if not _is_num(ss) or not _is_num(base_ss):
            saving = pct = float('nan')
        else:
            saving = base_ss - ss
            pct = (saving / base_ss * 100.0) if base_ss else 0.0
        rows.append(dict(
            pair=info['pair'], config=info['config'], channel=channel,
            plan_key=s.get('key'), plan_label=s.get('label', s.get('key')),
            assignment=s.get('assignment', ''),
            ss_prod_hours=ss, saving_abs=saving, saving_pct=pct,
            is_baseline=(s.get('key') == info['base_key']),
        ))
    # mark the best plan (max absolute saving among numeric rows)
    numeric = [r for r in rows if _is_num(r['saving_abs'])]
    best = max(numeric, key=lambda r: r['saving_abs'], default=None)
    for r in rows:
        r['is_best'] = (best is not None and r['plan_key'] == best['plan_key'])
    info['best'] = best
    return info, rows


def rollup(base_dir: str, log=print) -> dict:
    pairs = _find_series(base_dir)
    if not pairs:
        log(f'No analyzed channel runs (sim_meta.json + series.json) found under {base_dir}.')
        log('Run run_analysis.py first.')
        return {}

    all_rows: list[dict] = []
    all_infos: list[dict] = []
    for meta_path, series_path in pairs:
        with open(meta_path) as f:
            meta = json.load(f)
        with open(series_path) as f:
            series = json.load(f)
        info, rows = _channel_rows(meta, series)
        all_rows.extend(rows)
        all_infos.append(info)

    # Store and fulfillment sweep INDEPENDENT config sets, so the whole-warehouse best is the
    # best (config, plan) per channel summed across channels — grouped per PAIR, not per config.
    # (Grouping per config would only combine channels that happened to share a config name; with
    # independent sweeps store and fulfillment configs are named separately and never would.)
    by_pair: dict = defaultdict(lambda: defaultdict(list))   # pair -> channel -> [channel_info]
    for info in all_infos:
        by_pair[info['pair']][info['channel']].append(info)

    # ── per-plan CSV (also the mix-and-match table) ──────────────────────────────
    plan_csv = os.path.join(base_dir, 'channel_rollup.csv')
    with open(plan_csv, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=[
            'pair', 'config', 'channel', 'plan_key', 'plan_label', 'assignment',
            'ss_prod_hours', 'saving_abs', 'saving_pct', 'is_baseline', 'is_best'])
        w.writeheader()
        for r in all_rows:
            w.writerow(r)

    # ── summary CSV + stdout: best (config, plan) per channel, then cumulative whole-warehouse ─
    summary_csv = os.path.join(base_dir, 'channel_rollup_summary.csv')
    summary_rows = []
    for pair, chan_map in sorted(by_pair.items()):
        cum_saving = 0.0
        cum_base = 0.0
        log(f'\n{"="*70}\n  {pair}\n{"="*70}')
        for channel, infos in sorted(chan_map.items()):
            # This channel's winner ACROSS all its configs: the (config, plan) with the biggest
            # absolute saving vs that config's own fifo baseline.
            cand = [i for i in infos if i['best'] is not None and _is_num(i['base_ss'])]
            if not cand:
                log(f'  [{channel:<12}] no numeric series — skipped')
                continue
            win = max(cand, key=lambda i: i['best']['saving_abs'])
            best = win['best']
            base_ss = win['base_ss']
            cum_saving += best['saving_abs']
            cum_base += base_ss
            log(f'  [{channel:<12}] best = {win["config"]}/{best["plan_key"]:<22} '
                f'saving {best["saving_abs"]:>12,.1f}  ({best["saving_pct"]:>5.1f}%)  '
                f'baseline(fifo) {base_ss:,.1f}'
                + (f'  (best of {len(cand)} configs)' if len(cand) > 1 else ''))
            if win.get('dropped_arms'):
                log(f'      !! {len(win["dropped_arms"])} arm(s) ran but are MISSING from '
                    f'analysis (e.g. {win["dropped_arms"][0]}); best chosen from a SUBSET.')
                log(f'      !! Re-run: python run_analysis.py <base> --preset BY_INITIAL '
                    f'(focus=all) to include opt_* arms.')
            summary_rows.append(dict(
                pair=pair, channel=channel, best_config=win['config'],
                best_plan=best['plan_key'], baseline_fifo_ss=base_ss,
                best_ss=best['ss_prod_hours'], saving_abs=best['saving_abs'],
                saving_pct=best['saving_pct']))
        cum_pct = (cum_saving / cum_base * 100.0) if cum_base else 0.0
        log(f'  {"-"*66}')
        log(f'  CUMULATIVE whole-warehouse saving = {cum_saving:,.1f} sim units '
            f'({cum_pct:.1f}% of combined fifo baseline {cum_base:,.1f})')
        summary_rows.append(dict(
            pair=pair, channel='(cumulative)', best_config='(per-channel best)',
            best_plan='sum(best per channel)', baseline_fifo_ss=cum_base,
            best_ss=cum_base - cum_saving, saving_abs=cum_saving, saving_pct=cum_pct))

    with open(summary_csv, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=[
            'pair', 'channel', 'best_config', 'best_plan', 'baseline_fifo_ss',
            'best_ss', 'saving_abs', 'saving_pct'])
        w.writeheader()
        for r in summary_rows:
            w.writerow(r)

    log(f'\nWrote {plan_csv}')
    log(f'Wrote {summary_csv}')
    log('\nMix-and-match: channels are independent, so any (store-plan, fulfillment-plan)')
    log('combined saving is just the sum of their saving_abs rows in channel_rollup.csv.')
    return dict(plan_csv=plan_csv, summary_csv=summary_csv,
                n_channels=len(all_rows and pairs), n_groups=len(by_pair))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('base_dir', help='the comparison_YYYYMMDD_HHMMSS run directory')
    args = ap.parse_args()
    base = os.path.abspath(args.base_dir)
    if not os.path.isdir(base):
        ap.error(f'not a directory: {base}')
    rollup(base)


if __name__ == '__main__':
    main()
