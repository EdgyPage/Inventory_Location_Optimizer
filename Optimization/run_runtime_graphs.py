"""run_runtime_graphs.py — RUNTIME (compute-cost) graphs from the runtime metrics DB.

Cross-cell analysis step (part of the analysis hub): reads the run root's runtime metrics DB (one row
per arm, written parent-side by the supervisor) and emits PNGs to the run root's runtime dir — the
contract's ``runtime_pngs`` artifact, whose glob star sits in the filename segment, so the template's
directory IS the output dir — ranking the slowest arms / assignment-fns / warehouses / cells and —
critically — a per-SECTION stacked breakdown showing WHERE the wall time goes, so recurring
hot-paths (e.g. a reorder/reslot-dominated arm — the valid-aisle recompute suspicion) are visible at
a glance.

This is RUNTIME (how long an arm took to COMPUTE), orthogonal to the sim-modeled batch_stats times.

    python -m Optimization.run_runtime_graphs <run_root>
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys

# ── path setup: repo root on sys.path so package imports resolve when run as a
#    script (python Optimization/run_runtime_graphs.py <dir>); `-m` form needs none.
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from Optimization.persistence.runtime_metrics import load_rows, SECTIONS, RUNTIME_DB, runtime_db_path
from Optimization.config.sim_config import _OUTPUT_DIR

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
            # .get, not [col]: rows from an OLD-vintage runtime_metrics.db lack columns
            # added later (e.g. the 2026-08-19 observability set) — the graphs must keep
            # rendering archived runs, so a missing section reads as 0, never a KeyError.
            widths = [_mean([r.get(col, 0.0) for r in sub if r['assignment'] == a])
                      for a in assigns]
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


def _runtime_dir(run_root: str) -> str:
    """The run-root runtime-PNG directory, from the contract's `runtime_pngs` template.

    The glob star sits in the FILENAME segment, so the template's dirname is the output dir —
    the underscore prefix it carries is what makes every tree walker (cells / iter_channel_runs /
    the cross-cell whatif scans) skip it, the same reserved-dir convention as the frozen assets
    and the aggregate subtree.  A root with NO descriptor (ad-hoc trees) falls back to the HEAD
    contract, which renders exactly the string the old literal join produced.
    """
    from Optimization import runschema
    from Optimization.runschema import contract as _contract
    from Optimization.runschema.resolver import RunTree
    try:
        rt = runschema.resolver_for(run_root)
    except runschema.UnsupportedRunTree:
        head = _contract.head()
        doc = _contract.load(head) if head else None
        if doc is None:
            raise
        rt = RunTree(run_root, doc, layout={})
    return os.path.dirname(rt.path('runtime_pngs'))


def run(run_root, log=None):
    """Emit the runtime graphs for a finished run to the contract's runtime PNG dir.  Returns the
    out dir (or None if there's no runtime metrics DB).  Importable so the analysis hub calls it
    in-process."""
    say = log.info if log is not None else print
    rows = load_rows(run_root)
    if not rows:
        say(f'  runtime graphs: no {RUNTIME_DB} at {run_root} (skipped)')
        return None
    outdir = _runtime_dir(run_root)
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
    _memory_graph(rows, os.path.join(outdir, 'runtime_memory.png'))
    say(f'  runtime graphs -> {outdir}  ({len(rows)} arm rows)')
    return outdir


def _memory_graph(rows, out):
    """Per-arm peak RSS bars + GC pause overlay — the memory observability view.

    Skips silently when the run predates the 2026-08-19 columns (all values absent):
    old vintages keep rendering their other graphs untouched."""
    rss = [(r, r.get('peak_rss_mib')) for r in rows]
    rss = [(r, v) for r, v in rss if v]
    if not rss:
        return
    rss.sort(key=lambda t: -t[1])
    top = rss[:30]
    labels = [f"{r['cell']}/{r['arm']} [{r['channel']}]" for r, _v in top]
    vals   = [v for _r, v in top]
    gc_s   = [(_r.get('gc_pause_s') or 0.0) for _r, _v in top]
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13, 0.28 * len(top) + 2.2))
    ax.barh(labels, vals, color=_SECTION_COLORS[0])
    ax.invert_yaxis()
    ax.set_xlabel('peak worker RSS (MiB)')
    ax.set_title('per-arm peak memory (top 30)')
    ax.tick_params(axis='y', labelsize=6)
    ax2.barh(labels, gc_s, color=_SECTION_COLORS[3])
    ax2.invert_yaxis()
    ax2.set_xlabel('GC pause total (s) — overlaps every section')
    ax2.set_title('per-arm GC pause')
    ax2.set_yticklabels([])
    ax2.grid(alpha=0.3, axis='x')
    fig.tight_layout()
    _save(fig, out)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Runtime (compute-cost) graphs from the runtime metrics DB.')
    ap.add_argument('run_root')
    args = ap.parse_args(argv)
    from Optimization.runschema import resolve_base_dir
    root = resolve_base_dir(args.run_root)
    if not os.path.exists(runtime_db_path(root)):
        raise SystemExit(f'no {RUNTIME_DB} in {root}')
    run(root)


if __name__ == '__main__':
    main()
