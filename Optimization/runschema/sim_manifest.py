"""sim_manifest.py — run bookkeeping artifacts.

Resume state (resume.pkl) + the top-level run_manifest.json schema index that the
docs ingest (docs/experiments/ingest.py) auto-discovers a run from.

It also stamps CODE provenance into run_spec.json (`repo_commit` / `repo_dirty`).  That is a
different question from the two ids already on a run and neither of them answers it:
``run_layout.json``'s ``schema_id`` names the run-TREE contract and ``sim_meta.json``'s
``sim_schema_id`` names the DB TABLE contract, so a change to the simulator that moves no path and
no column — `753d01e` moved absolute throughput ~1.4 % without touching either — leaves both
byte-identical and the two runs indistinguishable.
"""
import json
import os
import pickle
import subprocess


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


# ── code provenance — WHICH simulator produced a run ────────────────────────────
# Repo root from this file: Optimization/runschema/sim_manifest.py -> ../../ .  Depth-sensitive in
# the same way Optimization/config/sim_config.py's _REPO_ROOT is, but harmless if wrong: a bad path
# yields `unknown` rather than a misdirected write.
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

#: `git status` is a subprocess at the front of a run.  Bounded so a wedged git (a stale index.lock,
#: a network-backed worktree) costs seconds, never the run.
_GIT_TIMEOUT = 10.0


def _git_dir(repo_root: str) -> str | None:
    """The `.git` directory for `repo_root`, or None when there is no git metadata at all.

    `.git` is a DIRECTORY in a normal clone and a FILE holding `gitdir: <path>` in a linked
    worktree or a submodule, which is why this is not a bare isdir() check.
    """
    p = os.path.join(repo_root, '.git')
    if os.path.isdir(p):
        return p
    if os.path.isfile(p):
        try:
            with open(p, encoding='utf-8') as f:
                head = f.read().strip()
        except OSError:
            return None
        if head.startswith('gitdir:'):
            target = head.split(':', 1)[1].strip()
            target = target if os.path.isabs(target) else os.path.join(repo_root, target)
            return os.path.normpath(target)
    return None


def _head_commit(git_dir: str) -> str | None:
    """HEAD's full sha by reading files only — no subprocess, and no git binary required.

    A source export (a zip, a docker COPY) has no `.git` and returns None from _git_dir before we
    get here; a SHALLOW clone does have one and resolves normally, which is the case the naive
    `git describe` approach gets wrong.  Three shapes are handled: a detached HEAD (the sha
    inline), a loose ref, and a ref that only exists in `packed-refs` (a fresh clone's usual state).
    """
    try:
        with open(os.path.join(git_dir, 'HEAD'), encoding='utf-8') as f:
            head = f.read().strip()
    except OSError:
        return None
    if not head.startswith('ref:'):
        return head or None                                  # detached: HEAD is the sha itself
    ref = head.split(':', 1)[1].strip()
    try:
        with open(os.path.join(git_dir, *ref.split('/')), encoding='utf-8') as f:
            return f.read().strip() or None
    except OSError:
        pass
    try:
        with open(os.path.join(git_dir, 'packed-refs'), encoding='utf-8') as f:
            for line in f:
                if line.startswith(('#', '^')):
                    continue
                sha, _, name = line.strip().partition(' ')
                if name == ref:
                    return sha or None
    except OSError:
        pass
    return None


def repo_provenance(repo_root: str = _REPO_ROOT) -> dict:
    """{'repo_commit': <short sha> | 'unknown', 'repo_dirty': True | False | None}.

    WHY THIS MUST NOT RAISE: it runs at the front of every simulation, and a run that dies because
    provenance could not be derived is strictly worse than a run that records `unknown`.  A shallow
    clone, a source export with no `.git`, a machine with no git on PATH and a wedged index lock all
    resolve to a recorded value rather than an exception.

    `repo_dirty` is deliberately THREE-valued: True/False are answers, `None` means "not
    established" — which is what an export or a missing git binary honestly is, and reading it as
    "clean" would be the one wrong inference.
    """
    git_dir = _git_dir(repo_root)
    if git_dir is None:
        return {'repo_commit': 'unknown', 'repo_dirty': None}
    sha = _head_commit(git_dir)
    dirty: bool | None = None
    try:
        r = subprocess.run(['git', 'status', '--porcelain'], cwd=repo_root,
                           capture_output=True, text=True, timeout=_GIT_TIMEOUT)
        if r.returncode == 0:
            dirty = bool(r.stdout.strip())
    except Exception:                        # noqa: BLE001 - provenance is best-effort, always
        dirty = None
    return {'repo_commit': (sha[:12] if sha else 'unknown'), 'repo_dirty': dirty}


# ── run spec (run_spec.json) — the resolved run invocation, for zero-param --resume ─────

def _run_spec_path(base_dir: str) -> str:
    return os.path.join(base_dir, 'run_spec.json')


def _write_run_spec(base_dir: str, spec: dict) -> None:
    """Persist the fully-resolved run spec (argv + resolved run-shaping params + the resolved
    inventory pairs) to <base>/run_spec.json, atomically.  On --resume this is read back so a
    bare `python run_simulation.py --resume DIR` reconstructs the run with zero retyped flags
    and no find_latest_db_pairs drift.

    `repo_commit` / `repo_dirty` are added HERE rather than by the caller so that every producer of
    a run_spec gets them without opting in — the point of the stamp is that it cannot be forgotten.
    They are provenance, not run shape: `_apply_run_spec` overlays an explicit whitelist of fields
    onto argv, so a resume ignores them and keeps the ORIGINAL run's commit on record."""
    spec = {**spec, **repo_provenance()}
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

    `schema_id` stamps which RUN-TREE CONTRACT this run's directory layout follows — the sha256 of
    that contract's own shape, stored at Optimization/schemas/run_tree/<short>.json.  Downstream
    tools select their resolver from it (runschema.resolver_for), so an OLD run keeps analyzing
    correctly after the tree shape moves on.  It is a content address, not a sequence number: there
    is nothing to increment, and anyone can re-derive it from the declaration to check it.

    Distinct from `version`, which versions THIS descriptor file's own field set.
    """
    from Optimization.runschema import contract as _contract
    layout = {
        'version'       : 1,
        'schema_id'     : _contract.head() or _contract.build()['schema_id'],
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
