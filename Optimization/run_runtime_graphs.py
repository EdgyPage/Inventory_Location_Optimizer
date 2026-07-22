"""run_runtime_graphs.py — RUNTIME (compute-cost) graphs from runtime_metrics.db.

Cross-cell analysis step (part of the analysis hub): reads ``<run_root>/runtime_metrics.db`` (one row
per arm, written parent-side by the supervisor) and emits PNGs to ``<run_root>/_runtime/`` ranking the
slowest arms / assignment-fns / warehouses / cells and — critically — a per-SECTION stacked breakdown
showing WHERE the wall time goes, so recurring hot-paths (e.g. a reorder/reslot-dominated arm — the
valid-aisle recompute suspicion) are visible at a glance.

This is RUNTIME (how long an arm took to COMPUTE), orthogonal to the sim-modeled batch_stats times.

    python -m Optimization.run_runtime_graphs <run_root>
"""
from __future__ import annotations

import argparse
import os
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from Optimization.runtime_metrics import load_rows, SECTIONS, RUNTIME_DB, runtime_db_path
from Optimization.sim_config import _OUTPUT_DIR

_SECTION_COLORS = plt.cm.tab10.colors


def _save(fig, path):
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def _channels(rows):
    return sorted(set(r['channel'] for r in rows))


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return statistics.mean(vals) if vals else 0.0


def _slowest_arms(rows, out, top=25):
    rows = sorted(rows, key=lambda r: r['total_s'], reverse=True)[:top]
    rows = rows[::-1]                                   # slowest at the TOP of a barh
    labels = [f"{r['cell']}/{r['channel']}/{r['arm']}" for r in rows]
    fig, ax = plt.subplots(figsize=(10, max(4, 0.32 * len(rows) + 1)))
    ax.barh(range(len(rows)), [r['total_s'] for r in rows], color='#4c78a8')
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_xlabel('wall time to compute the arm (s)')
    ax.set_title(f'Slowest arms by compute time (top {len(rows)})')
    ax.grid(alpha=0.3, axis='x')
    _save(fig, out)


def _by_group(rows, key, title, out):
    """Mean compute time per group (assignment / warehouse / cell), per channel subplots."""
    chans = _channels(rows)
    fig, axes = plt.subplots(1, len(chans), figsize=(6.2 * len(chans), 6.0), squeeze=False)
    for ax, ch in zip(axes[0], chans):
        sub = [r for r in rows if r['channel'] == ch]
        groups = {}
        for r in sub:
            groups.setdefault(key(r), []).append(r['total_s'])
        items = sorted(((g, _mean(v)) for g, v in groups.items()), key=lambda kv: kv[1])
        ax.barh(range(len(items)), [v for _g, v in items], color='#f58518')
        ax.set_yticks(range(len(items)))
        ax.set_yticklabels([g for g, _v in items], fontsize=7)
        ax.set_xlabel('mean compute time (s)')
        ax.set_title(f'{ch}: {title}')
        ax.grid(alpha=0.3, axis='x')
    fig.tight_layout()
    _save(fig, out)


def _section_breakdown(rows, out):
    """Stacked per-SECTION mean time per assignment fn, per channel — WHERE the time goes.
    A tall `reorder` (or `pre-snapshot`) stack flags the recurring aisle-recompute hot-path."""
    chans = _channels(rows)
    fig, axes = plt.subplots(1, len(chans), figsize=(7.0 * len(chans), 6.4), squeeze=False)
    for ax, ch in zip(axes[0], chans):
        sub = [r for r in rows if r['channel'] == ch]
        assigns = sorted(set(r['assignment'] for r in sub),
                         key=lambda a: _mean([r['total_s'] for r in sub if r['assignment'] == a]))
        y = range(len(assigns))
        left = [0.0] * len(assigns)
        for si, (col, label) in enumerate(SECTIONS):
            widths = [_mean([r[col] for r in sub if r['assignment'] == a]) for a in assigns]
            ax.barh(list(y), widths, left=left, label=label,
                    color=_SECTION_COLORS[si % len(_SECTION_COLORS)])
            left = [l + w for l, w in zip(left, widths)]
        ax.set_yticks(list(y))
        ax.set_yticklabels(assigns, fontsize=7)
        ax.set_xlabel('mean compute time by section (s)')
        ax.set_title(f'{ch}: where the compute time goes')
        ax.grid(alpha=0.3, axis='x')
        ax.legend(fontsize=7, ncol=2)
    fig.suptitle('Per-section runtime breakdown — a tall reorder/pre stack = an aisle-recompute hot-path',
                 fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    _save(fig, out)


def run(run_root, log=None):
    """Emit the runtime graphs for a finished run to <run_root>/runtime/.  Returns the out dir
    (or None if there's no runtime_metrics.db).  Importable so the analysis hub calls it in-process."""
    say = log.info if log is not None else print
    rows = load_rows(run_root)
    if not rows:
        say(f'  runtime graphs: no {RUNTIME_DB} at {run_root} (skipped)')
        return None
    # '_' prefix so every tree walker (cells / iter_channel_runs / the cross-cell whatif scans)
    # skips it — it's a run-root output dir, not a cell (same convention as _frozen / _aggregate).
    outdir = os.path.join(run_root, '_runtime')
    os.makedirs(outdir, exist_ok=True)
    _slowest_arms(rows, os.path.join(outdir, 'runtime_slowest_arms.png'))
    _by_group(rows, lambda r: r['assignment'], 'mean compute time by assignment fn',
              os.path.join(outdir, 'runtime_by_assignment.png'))
    _by_group(rows, lambda r: f"{r['pair'].split('__')[-1]}/{r['config']}",
              'mean compute time by warehouse (inventory/config)',
              os.path.join(outdir, 'runtime_by_warehouse.png'))
    _section_breakdown(rows, os.path.join(outdir, 'runtime_section_breakdown.png'))
    if len(set(r['cell'] for r in rows)) > 1:
        _by_group(rows, lambda r: r['cell'], 'mean compute time by cell',
                  os.path.join(outdir, 'runtime_by_cell.png'))
    say(f'  runtime graphs -> {outdir}  ({len(rows)} arm rows)')
    return outdir


def main(argv=None):
    ap = argparse.ArgumentParser(description='Runtime (compute-cost) graphs from runtime_metrics.db.')
    ap.add_argument('run_root')
    args = ap.parse_args(argv)
    root = args.run_root if os.path.isabs(args.run_root) else os.path.join(_OUTPUT_DIR, args.run_root)
    if not os.path.exists(runtime_db_path(root)):
        raise SystemExit(f'no {RUNTIME_DB} in {root}')
    run(root)


if __name__ == '__main__':
    main()
