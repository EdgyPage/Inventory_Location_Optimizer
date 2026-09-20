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
from datetime import datetime
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


# ── code provenance — WHICH simulator produced a run ────────────────────────────
# Repo root from this file: Optimization/runschema/sim_manifest.py -> ../../ .  Depth-sensitive in
# the same way Optimization/config/sim_config.py's _REPO_ROOT is, but harmless if wrong: a bad path
# yields `unknown` rather than a misdirected write.
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

#: `git status` is a subprocess at the front of a run.  Bounded so a wedged git (a stale index.lock,


# _git_dir / _head_commit / repo_provenance MOVED to Schema/provenance.py: the profiles-tree
# descriptor is stamped by Warehouse/generation, which must not import the run harness, and
# provenance is a Schema-layer question.  Re-exported here so every existing caller (and the
# context/ anchors) keeps working unchanged.
from Schema.provenance import _git_dir, _head_commit, repo_provenance  # noqa: F401


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


# ── run history (run_history.json) — every LAUNCH, and how each one ended ────────
#
# `run_spec.json` beside it is the run's INVOCATION, written once and never rewritten: it
# has no timestamp, no status, and no trace of the three attempts a long campaign takes.
# So nothing durable said a run had finished, and nothing said how it was put together --
# which phase it belongs to, which cells it declared, how many units each planned.
#
# APPEND-ONLY, one record per launch INCLUDING EVERY RESUME, which is the rule neither
# existing writer has. Each record is opened at the top of a launch and closed at the tail,
# on BOTH paths -- including the refusal path, which today raises without recording
# anything at all.

def _run_history_path(base_dir: str) -> str:
    return os.path.join(base_dir, 'run_history.json')


def read_run_history(base_dir: str) -> list:
    """The launch records for one run root, oldest first.  `[]` when there are none.

    Tolerant of a truncated or hand-edited file: this is a LEDGER, and a reader that raised
    on a malformed one would take a whole campaign's history with it. A file that cannot be
    parsed reads as no history, and the next append starts a fresh list beside it.
    """
    path = _run_history_path(base_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding='utf-8') as f:
            doc = json.load(f)
    except Exception:                                  # noqa: BLE001 - a ledger never raises
        return []
    return doc if isinstance(doc, list) else []


def open_run_record(base_dir: str, record: dict) -> int:
    """Append a launch record and return its ATTEMPT NUMBER (1 for the first).

    Called after the preflight gate, at the one point where the whole construction is in
    scope: before it, a failed preflight's cleanup could tear a record that describes a run
    that never began.

    `resume_of` and `attempt` are derived HERE from what is already on disk rather than
    passed in, so a caller cannot get the chain wrong: the attempt is the count of records
    already present plus one, and `resume_of` is the previous record's `started`.
    """
    hist = read_run_history(base_dir)
    rec = {
        **record,
        # AFTER the caller's fields, not before: spread first, these three would be
        # overridable, and a caller that passed its own `attempt` could re-use a number and
        # leave two records claiming to be attempt 1 -- which is how a torn campaign comes
        # to read as a clean one. They are derived from what is on disk and nothing else.
        'attempt': len(hist) + 1,
        'resume_of': hist[-1].get('started') if hist else None,
        'started': datetime.now().isoformat(timespec='seconds'),
        **repo_provenance(),
        # Closed by `close_run_record`. Present and null from the start so a record that was
        # never closed -- a kill, a crash, a machine that lost power -- is DISTINGUISHABLE
        # from one that finished: `status: null` means "this launch never reported back",
        # which is a different fact from "it failed".
        'status': None, 'ended': None, 'unfinished': None, 'analysis_ran': None,
    }
    _write_history(base_dir, hist + [rec])
    return rec['attempt']


def close_run_record(base_dir: str, attempt: int, *, status: str,
                     unfinished=None, analysis_ran=None) -> None:
    """Stamp the outcome onto the record `open_run_record` returned.

    BEST-EFFORT AND SILENT. A ledger write that sank a finished twelve-hour run would be
    worse than the gap it closes; an unclosed record already reads as "never reported back".
    """
    try:
        hist = read_run_history(base_dir)
        for rec in hist:
            if rec.get('attempt') == attempt:
                rec['status'] = status
                rec['ended'] = datetime.now().isoformat(timespec='seconds')
                rec['unfinished'] = unfinished
                rec['analysis_ran'] = analysis_ran
                break
        else:
            return
        _write_history(base_dir, hist)
    except Exception:                                  # noqa: BLE001
        pass


def _write_history(base_dir: str, hist: list) -> None:
    path = _run_history_path(base_dir)
    tmp = f'{path}.tmp.{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(hist, f, indent=2)
    os.replace(tmp, path)


# ── the cross-run ledger ──────────────────────────────────────────────
#
# OUTSIDE every run root, and therefore outside the run-tree contract: no schema id moves,
# no canary runs are needed to add it, and a run root that is archived or deleted leaves its
# lines behind. One line per launch and one per completion, appended.
#
# This is what turns "which runs were phase 2, and did they finish?" from N file opens into
# one read. The per-run history above is the authority; this is the index.

RUN_INDEX_NAME = 'run_index.jsonl'


def _run_index_path(base_dir: str) -> str:
    """The ledger beside the run roots — the PARENT of this run's directory.

    Derived from the run root rather than read from the environment, so a run written
    somewhere other than the configured output directory indexes itself where it actually
    landed instead of into a ledger that describes a different tree.
    """
    return os.path.join(os.path.dirname(os.path.abspath(base_dir.rstrip(os.sep))),
                        RUN_INDEX_NAME)


def append_run_index(base_dir: str, event: str, payload: dict) -> None:
    """Append one line to the cross-run ledger.  BEST-EFFORT: never raises, never blocks.

    A failure to write an index entry must not sink a run -- the run's own history file is
    the authority and this is a convenience over it.
    """
    try:
        line = {'event': event,
                'at': datetime.now().isoformat(timespec='seconds'),
                'run': os.path.basename(os.path.abspath(base_dir.rstrip(os.sep))),
                'root': os.path.abspath(base_dir.rstrip(os.sep)),
                **payload}
        with open(_run_index_path(base_dir), 'a', encoding='utf-8') as f:
            f.write(json.dumps(line) + chr(10))
    except Exception:                                  # noqa: BLE001
        pass


def read_run_index(out_dir: str) -> list:
    """Every ledger line under one output directory, oldest first.

    A malformed line is SKIPPED rather than fatal, for the reason the history reader gives:
    a ledger that raises takes a campaign's worth of records with it.
    """
    path = os.path.join(os.path.abspath(out_dir), RUN_INDEX_NAME)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding='utf-8') as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except Exception:                          # noqa: BLE001
                continue
    return out


# ── run layout (run_layout.json) — the unified cell-tree descriptor ─────────────

def _run_layout_path(base_dir: str) -> str:
    return os.path.join(base_dir, 'run_layout.json')


def _pair_bindings(pairs) -> dict:
    """{label: binding | None} — WHICH catalogue version each pair label resolves to.

    The binding comes from the pair's profile run's own `profile_layout.json` (via
    `ProfileTree.binding_of`); `None` records that discovery used the pre-contract legacy walk —
    with descriptors forward-only, that is every catalogue generated before the profiles
    contract, and recording the absence honestly beats inventing provenance.  A regenerated-in-
    place catalogue mints new params digests, so a recorded binding is what makes silent
    re-pointing DETECTABLE after the fact.
    """
    from Schema.profile_resolver import ProfileTree
    out: dict = {}
    for label, inv_db, _aff in pairs:
        # profiles_root = two levels above inventory.db's side dir: <root>/<run>/<profile>/inventory/
        profile_dir = os.path.dirname(os.path.dirname(os.path.abspath(inv_db)))
        run_dir = os.path.dirname(profile_dir)
        try:
            out[label] = ProfileTree(os.path.dirname(run_dir)).binding_of(label)
        except Exception:                    # noqa: BLE001 - a binding must never block a run
            out[label] = None
    return out


def write_run_layout(base_dir, *, spec, reference, cells, pairs, store_cfgs, ff_cfgs,
                     channels, arms, created, coupled=False) -> None:
    """Write <base>/run_layout.json — the descriptor of a run's unified cell tree
    ``<base>/<cell>/<pair>/<config>[/<channel>]/sim_*.db``.  Lets tools INFER the tree (cells,
    reference, configs, pairs) instead of directory-guessing, and lets analysis of a partial/crashed
    or OLD run enumerate + label cells without re-importing the (possibly since-changed) whatif spec.
    Atomic (tmp + os.replace), same pattern as _write_run_spec.

    cells = the _build_cells() `Cell` records (name, split, zoning, scheduler); split is None or
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
        # version 3: + coupled (additive, and FALSE on every run that exists — the site-dock
        # marker below).  version 2: + pair_bindings (additive — `pairs` stays labels, so
        # every pre-v2 reader including resolver.axes() is untouched; absence of the key on
        # old files is normal).
        'version'       : 3,
        'schema_id'     : _contract.head() or _contract.build()['schema_id'],
        'kind'         : 'single' if len(cells) <= 1 else 'sweep',
        'spec'         : spec,
        'base'         : os.path.basename(base_dir.rstrip('/\\')),
        'created'      : created,
        'reference'    : reference,
        # FULL template from the run root — the cell level is part of the tree, not implied.
        'tree_template': '<cell>/<pair>/<config>[/<channel>]/sim_<strategy>.db',
        'channels'     : list(channels),
        # `Cell` is a NamedTuple whose field names ARE these keys, so `_asdict()` keeps the
        # descriptor and the producer in step by construction.  A plain tuple is still
        # accepted: a legacy resumed run reaches here with whatever it recorded.
        # `zip` truncates to the shorter side, so a legacy FOUR-tuple still yields exactly the
        # four keys it recorded — an absent `inbound` reads as "this run predates the axis".
        'cells'        : [c._asdict() if hasattr(c, '_asdict')
                          else dict(zip(('name', 'split', 'zoning', 'scheduler', 'inbound'), c))
                          for c in cells],
        'pairs'        : [label for label, _inv, _aff in pairs],
        'pair_bindings': _pair_bindings(pairs),
        'configs'      : {'store'      : [c['name'] for c in store_cfgs],
                          'fulfillment': [c['name'] for c in ff_cfgs]},
        'arms'         : list(arms) if arms is not None else None,
        # THE SITE-DOCK MARKER (site-dock 03).  True when a work unit drove BOTH channel
        # leaves -- one dock, one receiving crew, one pool of putters -- so the two leaves of
        # a pair are not independent warehouses and their savings are not additive.  It moves
        # no path, which is why it is a field here rather than a tree level: the leaves keep
        # their own `<config>/<channel>/` subtrees and every resolver reads them unchanged.
        # `run_restock_selection.select` refuses a root where this is true (its validity
        # argument IS channel independence); `run_channel_rollup` has the same premise.
        # Absent on every archived run, which reads as False -- correctly, they all are.
        'coupled'      : bool(coupled),
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
