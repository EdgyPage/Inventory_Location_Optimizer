"""Pull a simulation run's curated files into a docs experiment folder.

Copies, from a completed run's output directory (on the external drive) into
``docs/experiments/<exp>/``, exactly the small files the site needs:

  * per inventory × config:  ``config.json`` + the curated comparison PNGs
  * per inventory:           ``params.json`` + the inventory distribution PNGs

It prefers the run's ``run_manifest.json`` (written by run_simulation.py) to
auto-discover inventories × configs; for older runs without one it falls back to the
experiment's ``experiment.yml``. With ``--gen-manifest`` it also writes a starter
``experiment.yml`` you then edit (titles, short keys, winners).

Usage (run locally — CI has no drive access):
    python docs/experiments/ingest.py --exp experiment-2 \
        --source "D:/.../comparison_20260801_120000" --gen-manifest --dry-run
    python docs/experiments/ingest.py --exp experiment-2 --source "D:/.../comparison_..."

Nothing here runs at site-build time; it just stages committed snapshots.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

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
    ap.add_argument("--source", required=True, help="completed comparison run dir (the drive)")
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

    # Discover inventories × configs — prefer the run manifest, else experiment.yml.
    rm_path = os.path.join(source, "run_manifest.json")
    run = os.path.basename(source.rstrip("/\\"))
    if os.path.isfile(rm_path):
        with open(rm_path, encoding="utf-8") as fh:
            rm = json.load(fh)
        run = rm.get("run", run)
        inventories = list(rm["inventories"])
        configs = [c["name"] for c in rm["configs"]]
    else:
        yml = os.path.join(exp_dir, "experiment.yml")
        if not (yaml and os.path.isfile(yml)):
            sys.exit("no run_manifest.json at source and no experiment.yml to fall back on")
        with open(yml, encoding="utf-8") as fh:
            m = yaml.safe_load(fh)
        run = m.get("run", run)
        inventories = [v["id"] for v in m["inventories"].values()]
        configs = [c["name"] for c in m["configs"]]

    # figure schema: experiment.yml override, else defaults
    top3, full_suite, inv_plots = DEFAULT_TOP3, DEFAULT_FULL_SUITE, DEFAULT_INVENTORY_PLOTS
    yml = os.path.join(exp_dir, "experiment.yml")
    if yaml and os.path.isfile(yml):
        with open(yml, encoding="utf-8") as fh:
            m = yaml.safe_load(fh) or {}
        figs = m.get("figures", {})
        top3 = figs.get("top3", top3)
        full_suite = figs.get("full_suite", full_suite)
        inv_plots = m.get("inventory_plots", inv_plots)
        args.catalogue = m.get("catalogue", args.catalogue)

    log, n = [], 0
    figset = list(dict.fromkeys(top3 + full_suite))     # de-dup, keep order
    for inv in inventories:
        inv_src = os.path.join(source, inv)
        if not os.path.isdir(inv_src):
            log.append(f"  MISSING  inv dir {inv_src}")
            continue
        for cfg in configs:
            cfg_src = os.path.join(inv_src, cfg)
            dst_dir = os.path.join(exp_dir, "images", run, inv, cfg)
            n += _copy(os.path.join(cfg_src, "config.json"),
                       os.path.join(dst_dir, "config.json"), args.dry_run, log)
            for fname in figset:
                n += _copy(_find(cfg_src, fname), os.path.join(dst_dir, fname),
                           args.dry_run, log)
        # params.json + inventory plots via a cfg's sim_meta.json -> inv_db dir
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
        if inv_root:
            n += _copy(os.path.join(inv_root, "params.json"),
                       os.path.join(exp_dir, "data", inv, "params.json"), args.dry_run, log)
            for fname in inv_plots:
                n += _copy(_find(inv_root, fname),
                           os.path.join(exp_dir, "images", args.catalogue, fname),
                           args.dry_run, log)
        else:
            log.append(f"  NOTE     no sim_meta.json under {inv_src}; skipped params/plots")

    if args.gen_manifest and os.path.isfile(rm_path):
        _write_starter_manifest(exp_dir, rm, args.catalogue, top3, full_suite,
                                inv_plots, args.dry_run, log)

    print("\n".join(log))
    print(f"\n{'[dry-run] would copy' if args.dry_run else 'copied'} {n} file(s) "
          f"into {os.path.relpath(exp_dir, _DOCS)}")


def _short_key(inv_id):
    """Derive a short inventory key from a pair label, e.g.
    'mixed_20260624_083549__mixed_realistic_lt0' -> 'lt0'."""
    tail = inv_id.split("__")[-1]
    for marker in ("_realistic_", "_"):
        if marker in tail:
            return tail.rsplit(marker, 1)[-1]
    return tail


def _write_starter_manifest(exp_dir, rm, catalogue, top3, full_suite, inv_plots, dry, log):
    if not yaml:
        log.append("  NOTE     pyyaml missing; cannot write experiment.yml")
        return
    manifest = {
        "title": f"{os.path.basename(exp_dir)} — FIXME title",
        "run": rm["run"],
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
