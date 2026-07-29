"""Pull a simulation run's curated files into a docs experiment folder.

Copies, from a completed run's output directory (on the external drive) into
``docs/experiments/<exp>/``, exactly the small files the site needs:

  * per inventory × config:  ``config.json`` + the curated comparison PNGs
  * per inventory:           ``params.json`` + the inventory distribution PNGs

``--source`` is the RUN ROOT.  Every run is a cell matrix, so the run root holds one
``<cell>/`` subtree per what-if cell and ONE invocation now stages them all; previously this
script assumed the pre-cell flat tree (``<source>/<inv>/<cfg>``) and only worked if you pointed
it at a single cell directory by hand, once per cell.

The docs-side layout is unchanged — ``images/<cell>/<inv>/<config>/`` — because that is what
``docs/macros.py`` indexes and what every committed snapshot already uses.  The ``<channel>``
level is flattened away by the recursive figure lookup (a store config only ever runs on the
store channel, and likewise for fulfillment), so no site path moves.

Inventories × configs come from each cell's ``run_manifest.json``; for older runs without one it
falls back to the experiment's ``experiment.yml``.  With ``--gen-manifest`` it also writes a
starter ``experiment.yml`` you then edit (titles, short keys, winners).

Cross-cell what-if outputs at the run root (``whatif_delta.json`` + the ``whatif_*.png`` set) are
staged too — those used to be hand-copied and hand-renamed.

Usage (run locally — CI has no drive access):
    python docs/experiments/ingest.py --exp experiment-2 \
        --source "D:/.../comparison_20260801_120000" --gen-manifest --dry-run
    python docs/experiments/ingest.py --exp experiment-5 --source "D:/.../comparison_whatif_..."
    python docs/experiments/ingest.py --exp experiment-5 --source "D:/..." --cell k1_off_lpt

Nothing here runs at site-build time; it just stages committed snapshots.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys

# Repo root on sys.path: this is an entry script, invoked as `python docs/experiments/ingest.py`.
_REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    import yaml
except ImportError:                       # pragma: no cover
    yaml = None

# Curated figure schema — the filenames the docs macros render. Kept here (not in the
# run output) because which plots are "curated" is a docs decision. Override per
# experiment via experiment.yml `figures:` / `inventory_plots:`.
DEFAULT_TOP3 = [
    "top3_by_initial_prodtime_cum_improvement.png",
    "top3_by_initial_production_time_over_time.png",
    "top3_by_initial_prodtime_delta_trend.png",
    "top_vs_baseline_table.png",
    "top_vs_baseline.png",
]
DEFAULT_FULL_SUITE = [
    "task_duration_by_strategy.png",
    "production_time_over_time.png",
]
DEFAULT_INVENTORY_PLOTS = [
    "group_sizes.png", "demand.png", "param_frequency.png",
    "param_quantity.png", "equilibrium_qty.png",
]
# Cross-cell what-if artifacts at the RUN ROOT. whatif_delta.json is what docs/macros.py's
# whatif_matrix() renders; the PNGs are the scatter/bar set.  Both used to be copied by hand.
DEFAULT_WHATIF_DATA = ["whatif_delta.json"]
DEFAULT_WHATIF_PNG_GLOB = "whatif_*.png"

_DOCS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # …/docs


def _find(root, name):
    """First path matching `name` anywhere under `root` (run_analysis nests PNGs in
    compare/top, compare/breakdown, compare/overlay, …)."""
    for dirpath, _dirs, files in os.walk(root):
        if name in files:
            return os.path.join(dirpath, name)
    return None


def _copy(src, dst, dry, log):
    if not src or not os.path.isfile(src):
        log.append(f"  MISSING  {src}")
        return 0
    log.append(f"  copy     {src}  ->  {os.path.relpath(dst, _DOCS)}")
    if not dry:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exp", required=True, help="experiment folder name, e.g. experiment-2")
    ap.add_argument("--source", required=True,
                    help="the completed RUN ROOT (comparison_* / comparison_whatif_*). Every cell "
                         "under it is staged in one pass.")
    ap.add_argument("--cell", action="append", default=None,
                    help="stage only this cell (repeatable); default = every cell in the run")
    ap.add_argument("--catalogue", default="catalogue",
                    help="folder under images/ for the inventory distribution plots")
    ap.add_argument("--gen-manifest", action="store_true",
                    help="also write a starter experiment.yml from run_manifest.json")
    ap.add_argument("--dry-run", action="store_true", help="print actions, copy nothing")
    args = ap.parse_args(argv)

    source = os.path.abspath(args.source)
    exp_dir = os.path.join(_DOCS, "experiments", args.exp)
    if not os.path.isdir(source):
        sys.exit(f"source run dir not found: {source}")

    log, n = [], 0
    cells, schema_id = _cells_of(source, log)
    if args.cell:
        wanted = set(args.cell)
        missing = wanted - {c for c, _d in cells}
        if missing:
            sys.exit(f"--cell {sorted(missing)} not in this run: {[c for c, _d in cells]}")
        cells = [(c, d) for c, d in cells if c in wanted]

    # figure schema: experiment.yml override, else defaults
    top3, full_suite, inv_plots = DEFAULT_TOP3, DEFAULT_FULL_SUITE, DEFAULT_INVENTORY_PLOTS
    yml = os.path.join(exp_dir, "experiment.yml")
    ymldoc = {}
    if yaml and os.path.isfile(yml):
        with open(yml, encoding="utf-8") as fh:
            ymldoc = yaml.safe_load(fh) or {}
        figs = ymldoc.get("figures", {})
        top3 = figs.get("top3", top3)
        full_suite = figs.get("full_suite", full_suite)
        inv_plots = ymldoc.get("inventory_plots", inv_plots)
        args.catalogue = ymldoc.get("catalogue", args.catalogue)

    figset = list(dict.fromkeys(top3 + full_suite))     # de-dup, keep order
    last_rm = None
    for cell_name, cell_dir in cells:
        # The docs "run" id IS the cell name — that is what macros.py indexes and what every
        # committed snapshot uses, so staging more cells never moves an existing site path.
        rm, inventories, configs = _cell_inventory_configs(cell_dir, exp_dir, ymldoc, log)
        if rm is not None:
            last_rm = rm
        if not inventories:
            log.append(f"  NOTE     cell {cell_name}: nothing to stage")
            continue
        log.append(f"  cell     {cell_name}: {len(inventories)} inventory × {len(configs)} config")
        for inv in inventories:
            inv_src = os.path.join(cell_dir, inv)
            if not os.path.isdir(inv_src):
                log.append(f"  MISSING  inv dir {inv_src}")
                continue
            for cfg in configs:
                cfg_src = os.path.join(inv_src, cfg)
                if not os.path.isdir(cfg_src):
                    continue          # a config that this channel/cell didn't run — not an error
                dst_dir = os.path.join(exp_dir, "images", cell_name, inv, cfg)
                # config.json sits at <config>/; the figures may be one <channel>/ deeper, which
                # the recursive _find absorbs — so both tree shapes stage identically.
                n += _copy(_find(cfg_src, "config.json"),
                           os.path.join(dst_dir, "config.json"), args.dry_run, log)
                for fname in figset:
                    n += _copy(_find(cfg_src, fname), os.path.join(dst_dir, fname),
                               args.dry_run, log)
            n += _stage_inventory_assets(inv_src, inv, exp_dir, args, inv_plots, log)

    n += _stage_whatif(source, exp_dir, args.dry_run, log)

    if args.gen_manifest and last_rm is not None:
        _write_starter_manifest(exp_dir, last_rm, args.catalogue, top3, full_suite,
                                inv_plots, args.dry_run, log,
                                cells=[c for c, _d in cells], schema_id=schema_id)

    print("\n".join(log))
    print(f"\n{'[dry-run] would copy' if args.dry_run else 'copied'} {n} file(s) "
          f"into {os.path.relpath(exp_dir, _DOCS)}")


def _cells_of(source, log):
    """[(cell_name, cell_dir), …] for a run root, plus the run's tree schema id.

    Uses the versioned run-tree resolver.  When the descriptor is missing (a pre-v1 run, or a
    single CELL directory passed the old way), fall back to treating `source` itself as one cell
    so an existing workflow still stages — but say so, because the cell name then comes from the
    directory name rather than the run.
    """
    try:
        from Optimization.runschema import resolver_for
        rt = resolver_for(source)
        return rt.cells(), rt.schema_id
    except Exception as exc:                                    # noqa: BLE001
        log.append(f"  NOTE     no run-tree descriptor at {source} ({exc.__class__.__name__}); "
                   f"treating it as a single cell dir. Point --source at the RUN ROOT to stage "
                   f"every cell in one pass.")
        return [(os.path.basename(source.rstrip("/\\")), source)], None


def _cell_inventory_configs(cell_dir, exp_dir, ymldoc, log):
    """(run_manifest|None, inventories, configs) for one cell — manifest first, experiment.yml next."""
    rm_path = os.path.join(cell_dir, "run_manifest.json")
    if os.path.isfile(rm_path):
        with open(rm_path, encoding="utf-8") as fh:
            rm = json.load(fh)
        return rm, list(rm["inventories"]), [c["name"] for c in rm["configs"]]
    if ymldoc.get("inventories") and ymldoc.get("configs"):
        return (None,
                [v["id"] for v in ymldoc["inventories"].values()],
                [c["name"] for c in ymldoc["configs"]])
    log.append(f"  NOTE     no run_manifest.json in {cell_dir} and no experiment.yml fallback")
    return None, [], []


def _stage_inventory_assets(inv_src, inv, exp_dir, args, inv_plots, log):
    """params.json + the inventory distribution plots, located via a sim_meta.json's inv_db path."""
    meta = _find(inv_src, "sim_meta.json")
    inv_root = None
    if meta:
        try:
            with open(meta, encoding="utf-8") as fh:
                inv_db = json.load(fh).get("inv_db")
            if inv_db:
                inv_root = os.path.dirname(inv_db)      # …/inventory
        except Exception:                               # noqa: BLE001
            inv_root = None
    if not inv_root:
        log.append(f"  NOTE     no sim_meta.json under {inv_src}; skipped params/plots")
        return 0
    n = _copy(os.path.join(inv_root, "params.json"),
              os.path.join(exp_dir, "data", inv, "params.json"), args.dry_run, log)
    for fname in inv_plots:
        n += _copy(_find(inv_root, fname),
                   os.path.join(exp_dir, "images", args.catalogue, fname), args.dry_run, log)
    return n


def _stage_whatif(source, exp_dir, dry, log):
    """Cross-cell what-if outputs from the RUN ROOT into data/ + images/.

    whatif_delta.json is the file docs/macros.py:whatif_matrix() reads; before this it had no
    producer and no ingest path, so it was hand-copied from whatif_labor.json under a new name.
    Absent on a single-cell run, which is not an error.
    """
    n = 0
    for fname in DEFAULT_WHATIF_DATA:
        src = os.path.join(source, fname)
        if os.path.isfile(src):
            n += _copy(src, os.path.join(exp_dir, "data", fname), dry, log)
    for src in sorted(glob.glob(os.path.join(source, DEFAULT_WHATIF_PNG_GLOB))):
        n += _copy(src, os.path.join(exp_dir, "images", os.path.basename(src)), dry, log)
    return n


def _short_key(inv_id):
    """Derive a short inventory key from a pair label, e.g.
    'mixed_20260624_083549__mixed_realistic_lt0' -> 'lt0'."""
    tail = inv_id.split("__")[-1]
    for marker in ("_realistic_", "_"):
        if marker in tail:
            return tail.rsplit(marker, 1)[-1]
    return tail


def _write_starter_manifest(exp_dir, rm, catalogue, top3, full_suite, inv_plots, dry, log,
                            cells=None, schema_id=None):
    if not yaml:
        log.append("  NOTE     pyyaml missing; cannot write experiment.yml")
        return
    manifest = {
        "title": f"{os.path.basename(exp_dir)} — FIXME title",
        "run": rm["run"],
        # Which run-tree contract the source run followed — the content address of that
        # contract (Optimization/schemas/run_tree/<short>.json), so a re-ingest years later knows
        # exactly which layout the staged snapshot came from and can fetch that document.
        "schema_id": schema_id,
        "cells": list(cells or []),
        "catalogue": catalogue,
        "baseline": rm.get("baseline", "fifo"),
        "winners": ["FIXME", "FIXME", "FIXME"],
        "inventories": {_short_key(i): {"id": i, "label": f"{_short_key(i)} — FIXME"}
                        for i in rm["inventories"]},
        "configs": [{"name": c["name"], "label": "FIXME",
                     "height_brackets": c.get("height_brackets")} for c in rm["configs"]],
        "figures": {"top3": top3, "full_suite": full_suite},
        "inventory_plots": inv_plots,
    }
    dst = os.path.join(exp_dir, "experiment.yml")
    log.append(f"  {'would write' if dry else 'write'}  {os.path.relpath(dst, _DOCS)} (edit the FIXMEs)")
    if not dry:
        os.makedirs(exp_dir, exist_ok=True)
        with open(dst, "w", encoding="utf-8") as fh:
            yaml.safe_dump(manifest, fh, sort_keys=False, allow_unicode=True)


if __name__ == "__main__":
    main()
