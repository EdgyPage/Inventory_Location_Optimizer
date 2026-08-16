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
        --source "$COMPARISON_OUTPUT_DIR/comparison_20260801_120000" --gen-manifest --dry-run
    python docs/experiments/ingest.py --exp experiment-5 --source "$COMPARISON_OUTPUT_DIR/comparison_whatif_..."
    python docs/experiments/ingest.py --exp experiment-5 --source "$COMPARISON_OUTPUT_DIR/<run>" --cell k1_off_lpt

Nothing here runs at site-build time; it just stages committed snapshots.
"""
from __future__ import annotations

import argparse
import fnmatch
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


def _load_env(path: str) -> None:
    """Inject KEY=VALUE pairs from *path* into os.environ (shell vars take priority).

    Byte-for-byte the entry-script idiom in `Warehouse/generation/generate_mixed_profile.py`
    and `generate_profile_suite.py`; `Optimization/config/sim_config.py` carries its own.

    WITHOUT this, `--profiles-root`'s documented `$PROFILE_INPUT_DIR` default could never
    populate: the key lives in `.env`, nothing here read it, so `os.getenv` returned None,
    `_inv_root_from_bindings` returned at its first guard, and BY-NAME catalogue resolution
    silently fell through to the per-leaf meta file's recorded ABSOLUTE `inv_db` — the one
    route pair_bindings exists to replace, and the one that does not survive a moved drive.
    No warning was emitted, because the fallback is a legitimate path for pre-v2 runs.
    """
    if not os.path.isfile(path):
        return
    with open(path, encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith('#') or '=' not in _line:
                continue
            _key, _, _val = _line.partition('=')
            _key = _key.strip();  _val = _val.strip()
            if _val.startswith(('r"', "r'")):
                _val = _val[2:].rstrip('"').rstrip("'")
            else:
                _val = _val.strip('"').strip("'")
            if _key and _key not in os.environ:
                os.environ[_key] = _val


_load_env(os.path.join(_REPO_ROOT, '.env'))

try:
    import yaml
except ImportError:                       # pragma: no cover
    yaml = None

# The staged-tree declaration (docs/experiments/site_tree.py): every destination this script
# writes renders from its five templates, so a site path can only move by editing the contract.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import site_tree                          # noqa: E402

# Curated figure schema — the filenames the docs macros render. Kept here (not in the
# run output) because which plots are "curated" is a docs decision. Override per
# experiment via experiment.yml `figures:` / `inventory_plots:`.
#
# The default lists are DERIVED from the figure registry (figures.yml, beside this
# script): section membership + default: true, in registry order.  They were literals
# here once, and the same names lived independently in macros'
# caption tables and every experiment.yml — the param_frequency -> param_relative_frequency
# rename silently staged nothing for months because only the MISSING log ever noticed the
# drift.  Tests/architecture/test_figure_registry.py pins the derived defaults to those
# historical literals byte-for-byte and ties every registry name back to its writer.

def _registry_defaults():
    """(top3, full_suite, inventory_plots) from figures.yml, or None if pyyaml is absent."""
    if yaml is None:
        return None
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'figures.yml')
    with open(path, encoding='utf-8') as fh:
        figs = (yaml.safe_load(fh) or {}).get('figures', [])
    by = {'top3': [], 'full_suite': [], 'inventory': []}
    for f in figs:
        if f.get('default'):
            by[f['section']].append(f['name'])
    return by['top3'], by['full_suite'], by['inventory']


_DEFAULTS = _registry_defaults()
DEFAULT_TOP3, DEFAULT_FULL_SUITE, DEFAULT_INVENTORY_PLOTS = _DEFAULTS or (None, None, None)
# Cross-cell what-if artifacts at the RUN ROOT. whatif_delta.json is what docs/macros.py's
# whatif_matrix() renders; the PNGs are the scatter/bar set.  Both used to be copied by hand.
DEFAULT_WHATIF_DATA = ["whatif_delta.json"]
DEFAULT_WHATIF_PNG_GLOB = "whatif_*.png"

_DOCS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # …/docs


def _find(root, name):
    """First path matching `name` anywhere under `root` (run_analysis nests PNGs in
    compare/top, compare/breakdown, compare/overlay, …).

    The NO-CONTRACT lookup: pre-descriptor runs and the profiles tree (which has no run-tree
    contract at all) resolve through this walk.  Runs with a descriptor resolve through
    `_leaf_file` instead.
    """
    for dirpath, _dirs, files in os.walk(root):
        if name in files:
            return os.path.join(dirpath, name)
    return None


# Which analysis subdir wins when the same figure basename exists in more than one — the
# `@evaluation` out_subdir vocabulary in EXPLICIT preference order, replacing alphabetical
# luck with reviewable data.  production_time_over_time.png exists under BOTH compare/faceted/
# (eval compare.faceted) and compare/overlay/ (compare.overlay); every committed snapshot
# staged the faceted copy because f < o in the walk, so faceted-first encodes exactly the
# historical pick.  A figure in none of these dirs falls to the `_find` walk unchanged.
# Cross-checked against the registry's declared out_subdirs by an architecture test — kept a
# literal here so ingest does not import the matplotlib-heavy analysis package.
_FIGURE_DIR_PREFERENCE = (
    'compare', 'compare/breakdown', 'compare/faceted', 'compare/overlay', 'compare/top',
    'per_strategy', 'stats', 'stats_by_initial',
)


def _leaf_file(rt, cell, inv, cfg, name, cfg_src):
    """One curated file at a (cell, inventory, config) leaf, or None if absent.

    With a resolver, `config.json` is the contract's `config_json` artifact, and a figure is
    looked up inside the CONTRACT-RESOLVED channel-run leaf (`rt.channel_runs`) — the resolver
    consumes the `<channel>` level, so this function never has to guess whether one exists.
    Within a leaf the pick is by the EXPLICIT dir preference above (which analysis subtree owns
    a duplicated basename is now data, not walk order); `_find` remains for figures outside
    that vocabulary, and — without a resolver (pre-descriptor run, `--source` at a single cell
    dir) or for a leaf not yet finalized — over the config dir, the unchanged legacy route.
    """
    if rt is not None:
        if name == "config.json":
            p = rt.config_json(cell, inv, cfg)
            return p if os.path.isfile(p) else None
        leaves = [cr.path for _c, cr in rt.channel_runs(cell)
                  if cr.pair == inv and cr.config == cfg]
        if leaves:
            for leaf in leaves:
                for sub in _FIGURE_DIR_PREFERENCE:
                    p = os.path.join(leaf, *sub.split('/'), name)
                    if os.path.isfile(p):
                        return p
                hit = _find(leaf, name)
                if hit:
                    return hit
            return None
    return _find(cfg_src, name)


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
    if _DEFAULTS is None:
        sys.exit('ingest needs pyyaml to read the figure registry (docs/experiments/'
                 'figures.yml): pip install pyyaml.  (This is a local staging tool; CI '
                 'never runs it.)')
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
    ap.add_argument("--profiles-root", default=os.getenv("PROFILE_INPUT_DIR") or None,
                    help="profiles root for BY-NAME catalogue resolution via the run's recorded "
                         "pair_bindings (default: $PROFILE_INPUT_DIR). Pre-v2 runs and "
                         "pre-contract catalogues fall back to the sim_meta absolute path.")
    args = ap.parse_args(argv)

    source = os.path.abspath(args.source)
    exp_dir = os.path.join(_DOCS, "experiments", args.exp)
    if not os.path.isdir(source):
        sys.exit(f"source run dir not found: {source}")

    log, n = [], 0
    rt, cells, schema_id = _cells_of(source, log)
    commit, dirty = _repo_provenance_of(source, log, rt)
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
        # schema_id is stamped only by a REAL re-ingest (--gen-manifest); a plain ingest never
        # edits the curated YAML.  But a legacy manifest without it keeps the macros verifier
        # dormant for this experiment, so print the exact paste-lines — a human decision with
        # zero transcription effort.
        if schema_id and not ymldoc.get("schema_id"):
            log.append(f"  NOTE     {os.path.relpath(yml, _DOCS)} carries no schema_id, so the")
            log.append(f"           macros run-tree check stays dormant for this experiment.")
            log.append(f"           If these figures come from THIS run, paste into it:")
            log.append(f"             schema_id: {schema_id}")
            if commit:
                log.append(f"             repo_commit: {commit}")

    figset = list(dict.fromkeys(top3 + full_suite))     # de-dup, keep order
    last_rm = None
    for cell_name, cell_dir in cells:
        # The docs "run" id IS the cell name — that is what macros.py indexes and what every
        # committed snapshot uses, so staging more cells never moves an existing site path.
        rm, inventories, configs = _cell_inventory_configs(cell_dir, exp_dir, ymldoc, log,
                                                           rt=rt, cell=cell_name)
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
                # config.json sits at <config>/; the figures may be one <channel>/ deeper, which
                # both routes of _leaf_file absorb — so both tree shapes stage identically.
                n += _copy(_leaf_file(rt, cell_name, inv, cfg, "config.json", cfg_src),
                           site_tree.path('config_json', exp_dir, run=cell_name,
                                          inv=inv, cfg=cfg), args.dry_run, log)
                for fname in figset:
                    n += _copy(_leaf_file(rt, cell_name, inv, cfg, fname, cfg_src),
                               site_tree.path('figure_png', exp_dir, run=cell_name,
                                              inv=inv, cfg=cfg, figure=fname),
                               args.dry_run, log)
            n += _stage_inventory_assets(inv_src, inv, exp_dir, args, inv_plots, log,
                                         rt=rt, cell=cell_name)

    n += _stage_whatif(source, exp_dir, args.dry_run, log, rt=rt)

    if args.gen_manifest and last_rm is not None:
        _write_starter_manifest(exp_dir, last_rm, args.catalogue, top3, full_suite,
                                inv_plots, args.dry_run, log,
                                cells=[c for c, _d in cells], schema_id=schema_id,
                                commit=commit, dirty=dirty)

    print("\n".join(log))
    print(f"\n{'[dry-run] would copy' if args.dry_run else 'copied'} {n} file(s) "
          f"into {os.path.relpath(exp_dir, _DOCS)}")


def _cells_of(source, log):
    """(resolver|None, [(cell_name, cell_dir), …], schema_id|None) for a run root.

    Uses the versioned run-tree resolver, and hands it back so every later lookup (run_spec,
    run_manifest, figures, what-if outputs) resolves through the run's OWN contract.  When the
    descriptor is missing (a pre-v1 run, or a single CELL directory passed the old way), fall
    back to treating `source` itself as one cell so an existing workflow still stages — but say
    so, because the cell name then comes from the directory name rather than the run.
    """
    try:
        from Optimization.runschema import resolver_for
        rt = resolver_for(source)
        return rt, rt.cells(), rt.schema_id
    except Exception as exc:                                    # noqa: BLE001
        log.append(f"  NOTE     no run-tree descriptor at {source} ({exc.__class__.__name__}); "
                   f"treating it as a single cell dir. Point --source at the RUN ROOT to stage "
                   f"every cell in one pass.")
        return None, [(os.path.basename(source.rstrip("/\\")), source)], None


def _repo_provenance_of(source, log, rt=None):
    """(commit, dirty) for a run root — the simulator CODE that produced it.

    Read from the run root's ``run_spec.json`` (``_write_run_spec`` stamps it there), which is the
    only file on a run that distinguishes two runs of the same tree and the same tables from
    different code.  Runs made before the stamp existed have no such keys and yield
    ``('unknown', None)``; that is a fact about the run, so it is recorded and rendered rather than
    quietly dropped.  With a resolver the location comes from the contract (`run_spec`); the
    literal join stays for pre-descriptor sources.
    """
    path = rt.run_spec_json() if rt is not None else os.path.join(source, "run_spec.json")
    try:
        with open(path, encoding="utf-8") as fh:
            spec = json.load(fh)
    except (OSError, json.JSONDecodeError):
        log.append(f"  NOTE     no readable run_spec.json at {source}; recording commit 'unknown'")
        return "unknown", None
    commit = spec.get("repo_commit") or "unknown"
    if commit == "unknown":
        log.append("  NOTE     run_spec.json carries no repo_commit (run predates the stamp)")
    return commit, spec.get("repo_dirty")


def _cell_inventory_configs(cell_dir, exp_dir, ymldoc, log, rt=None, cell=None):
    """(run_manifest|None, inventories, configs) for one cell — manifest first, experiment.yml next.

    The manifest's location comes from the contract (`run_manifest`) when the run has one; the
    literal join stays for pre-descriptor sources and single-cell invocations."""
    rm_path = (rt.run_manifest(cell) if rt is not None
               else os.path.join(cell_dir, "run_manifest.json"))
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


def _inv_root_from_bindings(rt, inv, profiles_root, log):
    """Resolve the catalogue's inventory dir BY NAME from the run's recorded pair binding.

    The binding (the run-layout descriptor's `pair_bindings`, v2) names the profile_run +
    profile, so the catalogue resolves under ANY profiles root — including after the tree moved
    drives, which is exactly when the sim_meta absolute path (the fallback below) goes dead.
    None when the run predates v2, the binding is null (pre-contract catalogue), or the resolved
    dir does not exist under this root.
    """
    binding = ((rt.layout.get('pair_bindings') or {}).get(inv)) if rt is not None else None
    if not binding or not profiles_root:
        return None
    from Schema.profile_resolver import ProfileTree
    pt = ProfileTree(profiles_root)
    inv_db = pt.path('inventory_db', binding['profile_run'], profile=binding['profile'])
    if os.path.exists(inv_db):
        log.append(f"  resolve  {inv}: catalogue by NAME via pair_bindings "
                   f"({binding['profile_run']}/{binding['profile']})")
        return os.path.dirname(inv_db)
    log.append(f"  NOTE     pair_bindings names {binding['profile_run']}/{binding['profile']} "
               f"but it is not under {profiles_root}; falling back to sim_meta")
    return None


def _stage_inventory_assets(inv_src, inv, exp_dir, args, inv_plots, log, rt=None, cell=None):
    """params.json + the inventory distribution plots.

    Located, in preference order: (1) BY NAME via the run's recorded `pair_bindings` under
    --profiles-root (v2 run_layout + a descriptor-bearing catalogue — survives drive moves);
    (2) via a sim_meta.json's recorded inv_db ABSOLUTE path — the frozen legacy route, load-
    bearing for every pre-v2 run.  The `_find` walks on the resolved root remain: the profile
    plots live in generator-owned subtrees the docs deliberately flatten."""
    inv_root = _inv_root_from_bindings(rt, inv, getattr(args, 'profiles_root', None), log)
    if not inv_root:
        if rt is not None:
            metas = rt.glob('sim_meta', cell=cell, pair=inv)
            meta = metas[0] if metas else None
        else:
            meta = _find(inv_src, "sim_meta.json")
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
              site_tree.path('pair_params', exp_dir, inv=inv), args.dry_run, log)
    for fname in inv_plots:
        n += _copy(_find(inv_root, fname),
                   site_tree.path('catalogue_png', exp_dir, catalogue=args.catalogue,
                                  plot=fname), args.dry_run, log)
    return n


def _stage_whatif(source, exp_dir, dry, log, rt=None):
    """Cross-cell what-if outputs from the RUN ROOT into data/ + images/.

    whatif_delta.json is the file docs/macros.py:whatif_matrix() reads; before this it had no
    producer and no ingest path, so it was hand-copied from whatif_labor.json under a new name.
    Absent on a single-cell run, which is not an error.

    With a resolver the candidates come from the contract's `whatif` group tag
    (`rt.whatif_outputs()` — a new group member surfaces here with no edit); what is STAGED stays
    the docs' own selection (DEFAULT_WHATIF_DATA + the PNG set).  The literal joins below remain
    the pre-descriptor route.
    """
    n = 0
    if rt is not None:
        outs = rt.whatif_outputs()                   # existing whatif artifacts at the run root
        by_name = {os.path.basename(p): p for p in outs}
        for fname in DEFAULT_WHATIF_DATA:
            src = by_name.get(fname)
            if src:
                n += _copy(src, site_tree.path('whatif_data', exp_dir, fname=fname), dry, log)
        for src in sorted(p for p in outs
                          if fnmatch.fnmatch(os.path.basename(p), DEFAULT_WHATIF_PNG_GLOB)):
            n += _copy(src, site_tree.path('whatif_delta', exp_dir,
                                           fname=os.path.basename(src)), dry, log)
        return n
    for fname in DEFAULT_WHATIF_DATA:
        src = os.path.join(source, fname)
        if os.path.isfile(src):
            n += _copy(src, site_tree.path('whatif_data', exp_dir, fname=fname), dry, log)
    for src in sorted(glob.glob(os.path.join(source, DEFAULT_WHATIF_PNG_GLOB))):
        n += _copy(src, site_tree.path('whatif_delta', exp_dir,
                                       fname=os.path.basename(src)), dry, log)
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
                            cells=None, schema_id=None, commit=None, dirty=None):
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
        # Which simulator CODE produced it.  schema_id answers "what shape", this answers "what
        # behaviour" — and only the second moves when a fix changes a number without changing a
        # path or a column.  docs/macros.py:run_commit() renders it beside the run id.
        # A WHAT-IF experiment's `run:` is a cell, so its commit belongs beside whatif.source_run;
        # move this key there by hand when you add that block.
        "commit": commit,
        "dirty": dirty,
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
