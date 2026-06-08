"""
run_analysis.py — standalone graph generator for completed simulation runs.

Reads sim_meta.json files written by run_simulation.py, rebuilds warehouse
aisle maps via build_shared_assets, and calls run_config_analysis to produce
all plots.

Usage:
  python run_analysis.py <base_dir>
  python run_analysis.py C:\\...\\comparison_20260605_120000
"""

import argparse
import json
import logging
import os
import sys

# ── path setup ─────────────────────────────────────────────────────────────────
_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'Warehouse'))
sys.path.insert(0, _HERE)

from run_simulation import (
    build_shared_assets,
    discover_db_pairs,
    find_latest_db_pairs,
    _setup_logging,
    _clean_path,
    _OUTPUT_DIR,
)
from Comparison_Plots import run_config_analysis as _run_config_analysis


def run_analysis(base_dir: str, log: logging.Logger) -> None:
    """Re-run analysis (plots + CSV summaries) on all completed sims under *base_dir*.

    Scans the two-level structure base_dir/pair_label/config_name/ for
    sim_meta.json files written by run_simulation.py.  Calls build_shared_assets
    once per pair (to get aisle maps) then run_config_analysis per config.
    """
    for pair_name in sorted(os.listdir(base_dir)):
        pair_dir = os.path.join(base_dir, pair_name)
        if not os.path.isdir(pair_dir):
            continue

        config_metas: list[dict] = []
        inv_db = aff_db = None
        for cfg_name in sorted(os.listdir(pair_dir)):
            meta_path = os.path.join(pair_dir, cfg_name, 'sim_meta.json')
            if not os.path.exists(meta_path):
                continue
            with open(meta_path) as f:
                meta = json.load(f)
            config_metas.append(meta)
            if inv_db is None:
                inv_db = meta.get('inv_db')
                aff_db = meta.get('aff_db')

        if not config_metas or inv_db is None or aff_db is None:
            continue

        log.info(f'  Pair: {pair_name}  ({len(config_metas)} config(s))')
        try:
            shared = build_shared_assets(inv_db, aff_db, log)
        except Exception as exc:
            log.error(f'  build_shared_assets failed for {pair_name}: {exc}',
                      exc_info=True)
            continue

        for meta in config_metas:
            cfg_name = meta.get('name', '?')
            try:
                sim_result = {k: meta[k] for k in ('name', 'run_dir', 'strategies')}
                sim_result['optimal_sigma_fd'] = meta.get('optimal_sigma_fd', 0.0)
                _run_config_analysis(sim_result, shared, log)
            except Exception as exc:
                log.error(f'  Analysis failed for {cfg_name}: {exc}', exc_info=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Generate plots and CSV summaries from completed simulation runs.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        'base_dir',
        nargs='?',
        default=None,
        help='Comparison output directory (e.g. comparison_20260605_120000). '
             'Relative paths are resolved under COMPARISON_OUTPUT_DIR.',
    )
    args = parser.parse_args()

    if args.base_dir is None:
        parser.print_help()
        sys.exit(1)

    base_dir = (args.base_dir if os.path.isabs(args.base_dir)
                else os.path.join(_OUTPUT_DIR, args.base_dir))
    if not os.path.isdir(base_dir):
        sys.exit(f'Directory not found: {base_dir}')

    log = _setup_logging(os.path.join(base_dir, 'analysis.log'))
    log.info(f'run_analysis  dir: {base_dir}')
    run_analysis(base_dir, log)
    log.info('Done.')


if __name__ == '__main__':
    main()
