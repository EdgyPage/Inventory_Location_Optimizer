"""sim_manifest.py — run bookkeeping artifacts.

Resume state (resume.pkl) + the top-level run_manifest.json schema index that the
docs ingest (docs/experiments/ingest.py) auto-discovers a run from.
"""
import json
import os
import pickle


# ── resume helpers ─────────────────────────────────────────────────────────────

def _resume_path(run_dir: str) -> str:
    return os.path.join(run_dir, 'resume.pkl')


def _save_resume(run_dir: str, run_ids: dict, starts: dict) -> None:
    """Persist per-strategy batch counters and run IDs for crash recovery.

    run_ids and starts are keyed by strategy key (e.g. 'uniform', 'trip_min').
    Written atomically (tmp + os.replace) so a crash mid-write can't corrupt resume state.
    """
    state = {'run_ids': run_ids, 'next_batch': dict(starts)}
    path = _resume_path(run_dir)
    tmp = f'{path}.tmp.{os.getpid()}'
    with open(tmp, 'wb') as f:
        pickle.dump(state, f)
    os.replace(tmp, path)


def _load_resume(run_dir: str):
    path = _resume_path(run_dir)
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        return pickle.load(f)


# ── run spec (run_spec.json) — the resolved run invocation, for zero-param --resume ─────

def _run_spec_path(base_dir: str) -> str:
    return os.path.join(base_dir, 'run_spec.json')


def _write_run_spec(base_dir: str, spec: dict) -> None:
    """Persist the fully-resolved run spec (argv + resolved run-shaping params + the resolved
    inventory pairs) to <base>/run_spec.json, atomically.  On --resume this is read back so a
    bare `python run_simulation.py --resume DIR` reconstructs the run with zero retyped flags
    and no find_latest_db_pairs drift."""
    path = _run_spec_path(base_dir)
    tmp = f'{path}.tmp.{os.getpid()}'
    with open(tmp, 'w') as f:
        json.dump(spec, f, indent=2)
    os.replace(tmp, path)


def _load_run_spec(base_dir: str):
    path = _run_spec_path(base_dir)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


# ── run layout (run_layout.json) — the unified cell-tree descriptor ─────────────

def _run_layout_path(base_dir: str) -> str:
    return os.path.join(base_dir, 'run_layout.json')


def write_run_layout(base_dir, *, spec, reference, cells, pairs, store_cfgs, ff_cfgs,
                     channels, arms, created) -> None:
    """Write <base>/run_layout.json — the descriptor of a run's unified cell tree
    ``<base>/<cell>/<pair>/<config>[/<channel>]/sim_*.db``.  Lets tools INFER the tree (cells,
    reference, configs, pairs) instead of directory-guessing, and lets analysis of a partial/crashed
    or OLD run enumerate + label cells without re-importing the (possibly since-changed) whatif spec.
    Atomic (tmp + os.replace), same pattern as _write_run_spec.

    cells = the _build_cells() tuples [(name, split, zoning, scheduler), …]; split is None or
    {'k','capacity_loss'}.  `created` (ISO-8601) is passed in so the caller owns the clock.

    `schema_version` stamps which RUN-TREE CONTRACT this run's directory layout follows
    (Optimization/schemas/run_tree.v<N>.json).  Downstream tools select their resolver from it
    (runschema.resolver_for), so an OLD run keeps analyzing correctly after the tree shape moves on.
    It is distinct from `version`, which versions THIS descriptor file's own field set.
    """
    from Optimization.runschema import RUN_TREE_VERSION
    layout = {
        'version'       : 1,
        'schema_version': RUN_TREE_VERSION,
        'kind'         : 'single' if len(cells) <= 1 else 'sweep',
        'spec'         : spec,
        'base'         : os.path.basename(base_dir.rstrip('/\\')),
        'created'      : created,
        'reference'    : reference,
        # FULL template from the run root — the cell level is part of the tree, not implied.
        'tree_template': '<cell>/<pair>/<config>[/<channel>]/sim_<strategy>.db',
        'channels'     : list(channels),
        'cells'        : [{'name': name, 'split': split, 'zoning': zoning, 'scheduler': sched}
                          for (name, split, zoning, sched) in cells],
        'pairs'        : [label for label, _inv, _aff in pairs],
        'configs'      : {'store'      : [c['name'] for c in store_cfgs],
                          'fulfillment': [c['name'] for c in ff_cfgs]},
        'arms'         : list(arms) if arms is not None else None,
    }
    path = _run_layout_path(base_dir)
    tmp = f'{path}.tmp.{os.getpid()}'
    with open(tmp, 'w') as f:
        json.dump(layout, f, indent=2)
    os.replace(tmp, path)


def read_run_layout(base_dir: str):
    """Load <base>/run_layout.json, or None if absent/malformed (legacy runs have no descriptor)."""
    path = _run_layout_path(base_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def write_run_manifest(base_dir, pairs, store_cfgs, ff_cfgs, strategies) -> None:
    """Write <base>/run_manifest.json — a schema index of this run (inventories ×
    configs × strategies) so the docs ingest can auto-discover what to pull.
    See docs/experiments/ingest.py."""
    def _brackets_json(hb):
        return [[(None if thr == float('inf') else thr), mult] for thr, mult in (hb or ())]
    def _cfg_json(c, channel):
        return {'name'           : c['name'],
                'channel'        : channel,
                'pick_weight_fn' : c.get('pick_weight_fn'),
                'pick_volume_fn' : c.get('pick_volume_fn'),
                'height_brackets': _brackets_json(c.get('height_brackets'))}
    _store_cfgs = [_cfg_json(c, 'store') for c in store_cfgs]
    _ff_cfgs    = [_cfg_json(c, 'fulfillment') for c in ff_cfgs]
    run_manifest = {
        'run'                : os.path.basename(base_dir.rstrip('/\\')),
        'inventories'        : [label for label, _inv, _aff in pairs],
        # Merged flat list (store + fulfillment) — kept for docs ingest compatibility,
        # which reads a single `configs` list.  The per-channel lists are the source of truth.
        'configs'            : _store_cfgs + _ff_cfgs,
        'store_configs'      : _store_cfgs,
        'fulfillment_configs': _ff_cfgs,
        'strategies'         : [{'key': s.key, 'label': s.label} for s in strategies],
        'baseline'           : strategies[0].key if strategies else None,
    }
    with open(os.path.join(base_dir, 'run_manifest.json'), 'w') as f:
        json.dump(run_manifest, f, indent=2)
