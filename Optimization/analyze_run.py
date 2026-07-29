"""analyze_run.py — one analysis hub for a whole run (every run is a cell matrix).

`run_simulation` calls this in-process after the sim (unless ``--no-analyze``), so ONE command
produces the simulation AND every graph.  It can also be run standalone to (re)analyze a finished
run without re-simulating:

    python -m Optimization.analyze_run <base_dir> [--reference k1_off_rr] [--workers 8]

For each cell under ``base_dir`` it runs the per-cell registry suite (`run_analysis.run_analysis`)
and the whole-warehouse rollup (`run_channel_rollup.rollup`); when the run has >1 cell it also runs
the cross-cell what-if steps (`run_whatif_delta.run`, `run_whatif_labor.run`) at the run root.  Each
step is isolated in try/except so one failure never sinks the rest — the sim's DBs are already safe
on disk, so analysis is best-effort.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from Optimization import (runlayout, run_analysis, run_channel_rollup, run_whatif_delta,
                          run_whatif_labor, run_runtime_graphs)
from Optimization.sim_config import _OUTPUT_DIR, _setup_logging
from Optimization.sim_manifest import read_run_layout


def _step(log, what, fn):
    """Run one analysis step, isolating failures (DBs are already persisted)."""
    try:
        fn()
    except Exception as exc:                                   # noqa: BLE001
        log.error(f'  analysis step failed [{what}]: {exc!r}')


def analyze_run(base_dir, log, *, cells=None, workers=1, preset='BY_INITIAL', reference=None,
                cross_cell=True):
    """Analyze every cell of a run, then (for a multi-cell run) the cross-cell what-if summaries.

    cells: explicit [cell_name, …] from the driver (avoids re-scanning); else discovered from disk.
    reference: the round-robin/baseline cell for the cross-cell delta (from the run's spec).
    """
    # Cell list: the driver's own (avoids a re-scan), else the versioned resolver.  A run whose
    # descriptor is unreadable (pre-v1) falls back to the raw walker so an old tree still analyzes.
    if cells is not None:
        cell_items = [(c, os.path.join(base_dir, c)) for c in cells]
    else:
        try:
            from Optimization.runschema import resolver_for
            cell_items = resolver_for(base_dir).cells()
        except Exception as exc:                                   # noqa: BLE001
            log.warning(f'  analyze_run: no usable run-tree descriptor ({exc!r}); '
                        f'falling back to the structural walk')
            cell_items = list(runlayout.cells(base_dir))
    if not cell_items:
        log.warning(f'  analyze_run: no cells found under {base_dir}')
        return
    log.info(f'\n{"="*64}\n  ANALYSIS — {len(cell_items)} cell(s) under {base_dir}\n{"="*64}')

    for name, cell_dir in cell_items:
        log.info(f'  cell {name}: graphs + rollup')
        _step(log, f'{name}/graphs',
              lambda cd=cell_dir: run_analysis.run_analysis(cd, log, workers=workers, preset=preset))
        _step(log, f'{name}/rollup',
              lambda cd=cell_dir: run_channel_rollup.rollup(cd, log=log.info))

    # Cross-cell what-if summaries only make sense with more than one cell to compare.
    if cross_cell and len(cell_items) > 1:
        cell_names = [n for n, _d in cell_items]
        ref = reference
        if ref is None:
            # Prefer the run's OWN descriptor reference (correct for re-analyzing an old sweep);
            # fall back to the currently-committed whatif spec only when there's no descriptor.
            layout = read_run_layout(base_dir)
            if layout and layout.get('reference'):
                ref = layout['reference']
            else:
                try:
                    from Optimization.whatif_config import WHATIF
                    ref = WHATIF.get('reference')
                except Exception:                              # noqa: BLE001
                    ref = None
        log.info(f'  cross-cell what-if summaries (reference={ref})')
        if ref in cell_names:
            _step(log, 'whatif_delta', lambda: run_whatif_delta.run(base_dir, ref, log=log))
        else:
            log.warning(f'  skip whatif_delta: reference {ref!r} not among cells {cell_names}')
        _step(log, 'whatif_labor', lambda: run_whatif_labor.run(base_dir, reference=ref, log=log))

    # Runtime (compute-cost) graphs from runtime_metrics.db at the run root — always attempted;
    # skips gracefully when the DB is absent (e.g. a re-analysis of an old run without it).
    _step(log, 'runtime_graphs', lambda: run_runtime_graphs.run(base_dir, log=log))


def main(argv=None):
    ap = argparse.ArgumentParser(description='Analyze a finished simulation run (all cells + cross-cell).')
    ap.add_argument('base_dir')
    ap.add_argument('--reference', default=None, help='baseline cell for the cross-cell delta')
    ap.add_argument('--workers', type=int, default=1, help='per-cell analysis pool size')
    ap.add_argument('--preset', default='BY_INITIAL', help='run_analysis preset')
    args = ap.parse_args(argv)
    from Optimization.runschema import resolve_base_dir
    base_dir = resolve_base_dir(args.base_dir)
    if not os.path.isdir(base_dir):
        sys.exit(f'Directory not found: {base_dir}')
    log = _setup_logging(os.path.join(base_dir, 'analysis.log'))
    analyze_run(base_dir, log, workers=args.workers, preset=args.preset, reference=args.reference)


if __name__ == '__main__':
    main()
