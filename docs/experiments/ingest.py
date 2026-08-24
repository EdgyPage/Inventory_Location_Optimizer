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
# Both lists below are DERIVED from the figure registry (figures.yml, beside this script),
# in registry order.  They were literals here once, and the same names lived independently
# in macros' caption tables and every experiment.yml — the param_frequency ->
# param_relative_frequency rename silently staged nothing for months because only the
# MISSING log ever noticed the drift.  Tests/architecture/test_figure_registry.py pins the
# derived lists and ties every registry name back to its writer.
#
# They are two lists because the flag serves two masters that parted ways when the chart
# families replaced the old suite:
#   default:  what a NEW experiment's scaffolded manifest starts from — the current figures.
#   legacy:   what a no-manifest experiment renders.  Experiment 1 is the only one, its
#             sweep predates every figure since, and its set is frozen.
# Conflating them would hand each new experiment a manifest full of retired names whose
# only symptom is a MISSING line in the ingest log — the exact failure this registry exists
# to end.

def _registry_lists(flag: str):
    """(top3, full_suite, inventory_plots) for entries carrying `flag`, or None without
    pyyaml."""
    if yaml is None:
        return None
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'figures.yml')
    with open(path, encoding='utf-8') as fh:
        figs = (yaml.safe_load(fh) or {}).get('figures', [])
    by = {'top3': [], 'full_suite': [], 'inventory': []}
    for f in figs:
        if f.get(flag):
            by[f['section']].append(f['name'])
    return by['top3'], by['full_suite'], by['inventory']


#: A staged PNG matching this is a what-if artifact, not a registry figure — it has a
#: declared producer (a runschema writer) that figures.yml never names.  The SAME pattern
#: `context/guards/experiment_guard.py` exempts; see `_declared_png_names`.
WHATIF_PNG_GLOB = 'whatif_*.png'


def _declared_png_names() -> set:
    """Every PNG basename figures.yml names, RETIRED ENTRIES INCLUDED.

    Deliberately the same set `context/guards/experiment_guard.py._registry_png_names`
    builds, because the prune pass below and that guard must not be able to disagree: one
    deletes and the other reports, and a file one considers an orphan while the other does
    not is a build that fails after the cleanup already ran.

    A `retired:` entry keeps protecting its staged copies on purpose — retired means the
    writer is gone, not that the evidence a published page cites should vanish.
    """
    if yaml is None:
        return set()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'figures.yml')
    with open(path, encoding='utf-8') as fh:
        figs = (yaml.safe_load(fh) or {}).get('figures', [])
    return {f['name'] for f in figs if isinstance(f, dict) and 'name' in f}


_DEFAULTS = _registry_lists('default')
DEFAULT_TOP3, DEFAULT_FULL_SUITE, DEFAULT_INVENTORY_PLOTS = _DEFAULTS or (None, None, None)
_LEGACY = _registry_lists('legacy')
LEGACY_TOP3, LEGACY_FULL_SUITE, LEGACY_INVENTORY_PLOTS = _LEGACY or (None, None, None)
# Cross-cell what-if artifacts at the RUN ROOT.  The resolver route stages EVERY .json in
# the contract's `whatif` group (so the volume/labor numbers the pages quote always have a
# committed source, and a new group member auto-stages with no edit here — the ratchet in
# Tests/architecture/test_runtree_consumption.py is why no filename is spelled).  The
# literal below serves only the pre-descriptor legacy route, which predates the group tag.
DEFAULT_WHATIF_DATA = ["whatif_delta.json"]
DEFAULT_WHATIF_PNG_GLOB = "whatif_*.png"
#: Data extensions staged into data/.  CSV joined JSON when the run dossier arrived: the
#: JSON-only rule dated from when the only run-root data files were the what-if summaries,
#: and it silently skipped the sibling CSVs those same writers emit — so a page wanting the
#: per-arm rows behind a quoted median had nothing committed to link to.
_RUN_DATA_EXT = ('.json', '.csv')
#: Contract group tags whose members are staged from the run root.  A group, not a file
#: list, so the next document its writers emit stages itself.
_RUN_GROUPS = ('whatif', 'dossier')
#: One artifact per group, used only to ask WHICH CONTRACT declares that group — a group
#: tag has no name of its own to resolve, and a run's own document may not know the group
#: exists.  Any member does; these are the stage roots, which never move.
_GROUP_PROBE = {'whatif': ('whatif_delta_json',), 'dossier': ('dossier_dir',)}

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
# luck with reviewable data.  Post-redesign the leaf tree is one folder per chart family
# plus the tidy-CSV folder, and every figure basename carries its view prefix, so basenames
# are unique BY CONSTRUCTION (a golden test asserts it) — the ordering here only decides
# walk priority, and stays lexicographic to match it.  A figure in none of these dirs
# falls to the `_find` walk unchanged.  Cross-checked against the registry's declared
# out_subdirs by an architecture test — kept a literal here so ingest does not import the
# matplotlib-heavy analysis package.
_FIGURE_DIR_PREFERENCE = (
    'figures/diagnostics', 'figures/headline', 'figures/labor', 'figures/layout',
    'figures/significance', 'figures/task_time', 'figures/throughput',
    'figures/trajectories', 'tables',
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

    # Figure schema: the manifest names them when there is one.  Without a manifest this is
    # a legacy experiment, and it renders the frozen legacy set — NOT the starter defaults,
    # which are the current figures its sweep never produced.
    top3, full_suite, inv_plots = LEGACY_TOP3, LEGACY_FULL_SUITE, LEGACY_INVENTORY_PLOTS
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
    n += _stage_rollups(exp_dir, args.dry_run, log, rt=rt, cells=[c for c, _d in cells])
    n += _stage_leaf_tables(exp_dir, args.dry_run, log, rt=rt,
                            cells=[c for c, _d in cells])

    if args.gen_manifest and last_rm is not None:
        # A STARTER manifest names the current figures, never whatever this run happened to
        # render with (`top3` above is the legacy set when no manifest existed yet).
        _write_starter_manifest(exp_dir, last_rm, args.catalogue,
                                DEFAULT_TOP3, DEFAULT_FULL_SUITE,
                                DEFAULT_INVENTORY_PLOTS, args.dry_run, log,
                                cells=[c for c, _d in cells], schema_id=schema_id,
                                commit=commit, dirty=dirty)

    dropped = _prune_orphan_pngs(exp_dir, args.dry_run, log)

    print("\n".join(log))
    print(f"\n{'[dry-run] would copy' if args.dry_run else 'copied'} {n} file(s) "
          f"into {os.path.relpath(exp_dir, _DOCS)}"
          + (f"; removed {dropped} orphan PNG(s)" if dropped else ""))


def _prune_orphan_pngs(exp_dir, dry, log):
    """Delete staged PNGs whose basename no live registry entry declares.

    Staging COPIES; it has never removed anything.  So when a figure is renamed — and
    `delta_travel_vs_baseline.png` became `percent_travel_per_arm.png` the day figure views
    stopped being editorial — the new file lands beside the old one, and the old one stays
    committed, staged, and reachable, describing a chart the suite no longer draws.

    Nothing caught that except `context/guards/experiment_guard.py --scan`, which runs at
    the END of the publish loop and reports it for a human to chase.  The guard's rule is
    "a PNG with no declared producer", so applying exactly that rule HERE means ingest and
    the guard cannot disagree: the orphan is gone before the guard looks.

    Conservative by construction — only under `images/`, only `.png`, and only a basename
    the registry does not name at all.  What-if PNGs are exempt: their producer is a
    runschema writer, which figures.yml never lists.
    """
    declared = _declared_png_names()
    if not declared:
        return 0                    # no registry (no pyyaml) -> no opinion, delete nothing
    # Through the declaration, never a hand-join: `run_png` renders `images/{fname}`, so
    # its dirname IS the images root and moving that template moves this sweep with it.
    root = os.path.dirname(site_tree.path('run_png', exp_dir, fname='x.png'))
    dropped = 0
    for dirpath, _dirs, files in os.walk(root):
        for base in sorted(files):
            if (not base.endswith('.png') or base in declared
                    or fnmatch.fnmatch(base, WHATIF_PNG_GLOB)):
                continue
            path = os.path.join(dirpath, base)
            log.append(f"  {'would drop' if dry else 'drop'}     "
                       f'{os.path.relpath(path, _DOCS)}  (no live registry entry)')
            if not dry:
                os.remove(path)
            dropped += 1
    return dropped


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
    """Run-root outputs — the cross-cell what-if summaries AND the run dossier — into
    data/ + images/.

    whatif_delta.json is the file docs/macros.py:whatif_matrix() reads; before this it had no
    producer and no ingest path, so it was hand-copied from whatif_labor.json under a new name.
    Absent on a single-cell run, which is not an error.

    With a resolver the candidates come from the contract's GROUP TAGS (`_RUN_GROUPS`), so a
    new document its writers emit stages itself with no edit here; what is STAGED is
    shape-based, never name-based: every group data file into data/ (the committed sources
    the prose quotes) and every group PNG into images/.  That is why this function can serve
    two producers where `_stage_rollups` and `_stage_leaf_tables` name their artifacts one at
    a time and cost an edit each.  The literal joins below remain the pre-descriptor route.
    """
    n = 0
    if rt is not None:
        # Two passes — data files first, then PNGs — so the staged (src -> dst) sequence
        # is grouped rather than interleaved (one mixed sorted() pass puts pngs before the
        # json of a later group).
        cands = []
        for group in _RUN_GROUPS:
            # HEAD's contract per group, not the run's.  A finished run's own document
            # predates the groups today's analysis writes into — the dossier does not exist
            # in it at all — so resolving through it would stage nothing and say nothing.
            # `_reader_tree` is the same rule the leaf tables use, applied per group.
            rd = _reader_tree(rt, next(iter(_GROUP_PROBE[group]), None)) or rt
            cands.extend(sorted(rd.group_outputs(group)))
        for src in cands:
            base = os.path.basename(src)
            if base.endswith(_RUN_DATA_EXT):
                n += _copy(src, site_tree.path('run_data', exp_dir, fname=base), dry, log)
        for src in cands:
            base = os.path.basename(src)
            if not base.endswith(_RUN_DATA_EXT) and base.endswith('.png'):
                n += _copy(src, site_tree.path('run_png', exp_dir, fname=base), dry, log)
        return n
    for fname in DEFAULT_WHATIF_DATA:
        src = os.path.join(source, fname)
        if os.path.isfile(src):
            n += _copy(src, site_tree.path('run_data', exp_dir, fname=fname), dry, log)
    for src in sorted(glob.glob(os.path.join(source, DEFAULT_WHATIF_PNG_GLOB))):
        n += _copy(src, site_tree.path('run_png', exp_dir,
                                       fname=os.path.basename(src)), dry, log)
    return n


def _stage_rollups(exp_dir, dry, log, rt=None, cells=()):
    """Per-cell channel-rollup CSVs into data/<cell>/ — the per-arm labor rows and the
    per-channel best/saving summary the labor page's headline quotes.

    Added for Experiment 8 after a reviewer traced the labor headline to the rollup
    summary and found the site had never staged it: every quoted number must have a
    committed, diffable source.  Resolver-only (the artifacts are
    cell-scope contract members reached by NAME via `rt.rollup_csv` / `rt.path` — no
    filename literals here, per the run-tree consumption ratchet); a pre-descriptor
    tree logs a NOTE and stages nothing, matching the other resolver-gated stages.
    """
    if rt is None:
        log.append('  NOTE     no run-tree descriptor: channel rollups not staged '
                   '(labor-headline citations will dangle)')
        return 0
    n = 0
    for cell in cells:
        for src in (rt.rollup_csv(cell), rt.path('channel_rollup_summary_csv', cell=cell)):
            if os.path.isfile(src):
                n += _copy(src, site_tree.path('cell_data', exp_dir, run=cell,
                                               fname=os.path.basename(src)), dry, log)
            else:
                log.append(f'  MISSING  {src}  (run Optimization/run_channel_rollup.py '
                           f'on the cell first)')
    return n


def _reader_tree(rt, artifact):
    """A HEAD-contract resolver for an ANALYSIS artifact, falling back to `rt`.

    Thin alias for `runschema.reader_for`, which now owns the rule — contract SELECTION
    belongs in the resolver layer, and the analysis suite's run-scope evaluations hit the
    identical problem from the other side.  The argument (and the silent failure it
    prevents) lives in that docstring.
    """
    from Optimization.runschema import reader_for
    return reader_for(rt, artifact)


def _stage_leaf_tables(exp_dir, dry, log, rt=None, cells=()):
    """The per-leaf arm-vs-baseline table into data/<cell>/<pair>/<config>/.

    The headline figure prints an effect size, an interval and a corrected p for each
    arm; before this stage those numbers existed only as pixels, and a reader who wanted
    to check one — or to ask why an arm that is NOT on the podium missed it — had nothing
    to open.  Resolver-only and reached by NAME, like the rollup stage beside it.
    """
    if rt is None:
        return 0
    reader = _reader_tree(rt, 'vs_baseline_csv')
    if reader is None:
        log.append('  NOTE     no contract declares the arm-vs-baseline table: not staged')
        return 0
    n = 0
    per_run = _reader_tree(rt, 'per_run_summary_csv')
    for cell in cells:
        for _c, cr in rt.channel_runs(cell):
            wanted = [reader.leaf_path(cr, 'vs_baseline_csv')]
            if per_run is not None:
                # small per-arm aggregate; carries the put-away volume the published
                # exposure argument divides by, so that argument is checkable on the site
                wanted.append(per_run.leaf_path(cr, 'per_run_summary_csv'))
            for src in wanted:
                if os.path.isfile(src):
                    n += _copy(src, site_tree.path('leaf_data', exp_dir, run=cell,
                                                   inv=cr.pair, cfg=cr.config,
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
