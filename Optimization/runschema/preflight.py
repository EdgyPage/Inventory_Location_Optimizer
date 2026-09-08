"""runschema.preflight — prove the run-tree contract BEFORE a simulation, not after.

The problem this solves: every structural change to the output tree used to be discovered
downstream, weeks later, as hand-written throwaway extraction code.  This runs at the FRONT of a
run instead.

Five stages, cheapest first:

  1. DETECT   — hash the shape-defining sources (contract.SHAPE_SOURCES) against the
                `source_fingerprint` in the committed contract.  Unchanged ⇒ return immediately.
                This is the common path: no canary, no cost.
  2. CANARY   — only on a source change.  Two tiny REAL runs into a temp dir:
                  A: mixed catalog + 2-cell sweep  -> `<channel>/` present, `_frozen/`, what-if
                  B: store-only    + 1-cell        -> `<channel>/` ABSENT, no `_frozen/`
                One canary alone would encode "channel always present" — the exact assumption
                behind the store-only bugs this package exists to prevent.
  3. OBSERVE  — generalize every produced path back to a template, consuming levels POSITIONALLY
                against run_layout.json's axis lists (never by directory name: the store config is
                named `store` and so is the store channel, so `<pair>/store/store/` is real).
  4. VALIDATE — compare observed templates against the committed contract.  Produced-but-undeclared
                is the finding that matters; declared-but-never-produced and wrong optionality are
                caught too.
  5. ADOPT    — the schema's identity is the hash of `schema.py`'s declaration, so there is no
                number to pick and no module to scaffold.  When the declaration ALREADY covers the
                observed tree, store the new document, make it the head, and refresh the downstream
                orchestrator files (context/artifacts.yml, Visualization/static/schema.json).  When
                the code moved but `schema.py` didn't, print the exact ARTIFACTS entries to add
                (`--apply` inserts them) and adopt the resulting id.

CLI:
    python -m Optimization.runschema.preflight            # detect; canary + prompt on change
    python -m Optimization.runschema.preflight --check    # stage 1 only; exit 1 on drift, no writes
    python -m Optimization.runschema.preflight --yes      # unattended: adopt without prompting
    python -m Optimization.runschema.preflight --apply    # also write observed entries into schema.py
    python -m Optimization.runschema.preflight --force    # run the canaries even with no change

Environment:
    ILO_SKIP_PREFLIGHT=1   skip entirely (set for the canary subprocesses so they cannot recurse)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from Optimization.runschema import contract

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = contract._REPO_ROOT

SKIP_ENV = 'ILO_SKIP_PREFLIGHT'

# Canary sizing: the smallest run that still produces every artifact class.  Analysis stays ON —
# series.json / _aggregate / channel_rollup / _runtime are the half that keeps breaking, so they
# must be in the fingerprint.
#
# The size levers are the SKU count and the coverage, never a bin/aisle CAP.  A cap that binds
# below what the run's declared levels need refuses the plan outright (department-calibration,
# "Field the floor", decision 3), and it is self-defeating besides: the coverage fixed point
# sizes the warehouse from the levels and reads the levels back off the geometry, so a smaller
# warehouse is a shorter trip, a higher derived lines/day, and BIGGER levels than the cap was
# trying to contain.  `--coverage-days` shrinks the declaration itself, which is the honest
# lever and the one that actually makes a canary small.
CANARY_ARGS = [
    '--n-batches', '2', '--max-skus', '200', '--coverage-days', '1',
    '--keyframe-interval', '1', '--workers', '1',
]

_STRATEGY_RE = re.compile(r'^sim_(?P<strategy>.+?)(?P<kf>\.keyframes)?\.db$')
_CKPT_RE = re.compile(r'^_ckpt_(?P<strategy>.+)\.pkl$')
_BATCHES_RE = re.compile(r'^_batches_[0-9a-f]+\.pkl$')

_EXT_FORMAT = {'.db': 'sqlite', '.json': 'json', '.csv': 'csv', '.png': 'png',
               '.pkl': 'pickle', '.log': 'text', '.txt': 'text'}


# ── stage 1: detect ─────────────────────────────────────────────────────────────

def sources_changed() -> tuple[bool, str, str]:
    """(changed, recorded_fingerprint, current_fingerprint) for the shape-defining sources.

    The recorded value lives in INDEX.json, NOT in a contract document: a document is named by its
    own content hash, so it must never carry a mutable field.  An empty store counts as changed —
    there is nothing to compare against yet.
    """
    index = contract.read_index()
    current = contract.source_fingerprint()
    recorded = index.get('source_fingerprint')
    if not index.get('head') or recorded is None:
        return True, '', current
    return recorded != current, recorded, current


# ── stage 2: canaries ───────────────────────────────────────────────────────────

def _write_canary_catalog(profiles_root: str, profile: str, *, mixed: bool, num_skus: int = 300):
    """Generate one tiny inventory+affinity pair in the profiles-tree layout the driver discovers.

    Mirrors Tests/test_channel_runner_smoke.py's catalog: a real (if small) affinity matrix over
    consecutive SKUs, because affinity-driven arms refuse to run on an empty one.  `mixed=False`
    omits the fulfillment family, which is what makes the run store-only (no `<channel>` level).
    """
    from Warehouse.generation.generate_inventory import (
        Family, fulfillment_family, build_inventory_from_plan, save_inventory_to_db,
    )
    from Warehouse.generation import generate_affinity as ga

    dim = {'dist': 'uniform', 'low': 20, 'high': 44}
    wt = {'dist': 'volume_poisson'}
    if mixed:
        plan = [Family('food', 0.4, (0.5, 0.5), dim, dim, dim, wt),
                Family('clothing', 0.3, (0.5, 0.5), dim, dim, dim, wt),
                fulfillment_family(share=0.3, cube_sizes=(4, 6, 8))]
    else:
        plan = [Family('food', 0.6, (0.5, 0.5), dim, dim, dim, wt),
                Family('clothing', 0.4, (0.5, 0.5), dim, dim, dim, wt)]

    prof_dir = os.path.join(profiles_root, 'profile_00000000_000000', profile)
    inv_dir = os.path.join(prof_dir, 'inventory')
    aff_dir = os.path.join(prof_dir, 'affinity')
    os.makedirs(inv_dir, exist_ok=True)
    os.makedirs(aff_dir, exist_ok=True)

    inv = build_inventory_from_plan(num_skus=num_skus, plan=plan, seed=1)
    inv_db = os.path.join(inv_dir, 'inventory.db')
    save_inventory_to_db(inv, inv_db, {'name': profile, 'num_skus': num_skus})

    aff_db = os.path.join(aff_dir, 'affinity.db')
    conn = ga._init_db(aff_db)
    skus = sorted(c.sku for c in inv.orders)
    rows = []
    for a, b in zip(skus, skus[1:]):
        rows += [(a, b, 1.5), (b, a, 1.5)]
    conn.executemany('INSERT OR REPLACE INTO affinity (sku_i, sku_j, lift) VALUES (?,?,?)', rows)
    conn.commit()
    conn.close()

    # The canary catalogue carries a descriptor too — every preflight run therefore exercises
    # the PROFILES contract for free, exactly as the canaries exercise the run-tree one.  The
    # entries derive from what this function just wrote (a generator may claim its own output;
    # params_digest is None where the canary writes no params.json — honest absence).
    from Schema import profile_tree as _profile_tree
    run_dir = os.path.dirname(prof_dir)
    _profile_tree.write_profile_layout(run_dir, _profile_tree.entries_from_disk(run_dir),
                                       generator='canary')
    return inv_db, aff_db


def _run_canary(workdir: str, name: str, *, spec: str, mixed: bool, echo=print) -> str | None:
    """Run one canary simulation in a SUBPROCESS; return its run-root dir (None on failure).

    Isolation is by environment, not new flags: PROFILE_INPUT_DIR / COMPARISON_OUTPUT_DIR are
    already the driver's input/output roots (sim_config), so the real results drive is never
    touched.  ILO_SKIP_PREFLIGHT stops the child from re-entering this module.
    """
    root = os.path.join(workdir, name)
    profiles = os.path.join(root, 'profiles')
    outputs = os.path.join(root, 'outputs')
    os.makedirs(outputs, exist_ok=True)
    _write_canary_catalog(profiles, 'canary', mixed=mixed)

    env = dict(os.environ)
    env[SKIP_ENV] = '1'
    env['PROFILE_INPUT_DIR'] = profiles
    env['COMPARISON_OUTPUT_DIR'] = outputs
    env['PYTHONIOENCODING'] = 'utf-8'
    env['MPLBACKEND'] = 'Agg'                       # analysis draws PNGs headlessly

    cmd = [sys.executable, '-m', 'Optimization.run_simulation',
           '--spec', spec, '--profiles-dir', profiles] + CANARY_ARGS
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=_REPO_ROOT, env=env, capture_output=True, text=True)
    dt = time.time() - t0
    runs = [d for d in sorted(os.listdir(outputs)) if os.path.isdir(os.path.join(outputs, d))]
    if proc.returncode != 0 or not runs:
        echo(f'  canary {name}: FAILED (exit {proc.returncode}, {dt:.0f}s)')
        tail = (proc.stderr or proc.stdout or '').strip().splitlines()[-15:]
        for line in tail:
            echo(f'    | {line}')
        return None
    echo(f'  canary {name}: ok ({dt:.0f}s) -> {runs[-1]}')
    return os.path.join(outputs, runs[-1])


def run_canaries(workdir: str, echo=print) -> dict[str, str | None]:
    """Both canaries.  A = mixed catalog + 2-cell sweep; B = store-only + single cell."""
    echo('  running canary A (mixed catalog, 2-cell sweep)...')
    a = _run_canary(workdir, 'A', spec='_canary_sweep', mixed=True, echo=echo)
    echo('  running canary B (store-only, single cell)...')
    b = _run_canary(workdir, 'B', spec='_canary_single', mixed=False, echo=echo)
    return {'A': a, 'B': b}


# ── stage 3: observe ────────────────────────────────────────────────────────────

def _axis_values(layout: dict) -> dict[str, set]:
    cfgs = layout.get('configs') or {}
    return {
        'cell': {c['name'] for c in (layout.get('cells') or ())},
        'pair': set(layout.get('pairs') or ()),
        'config': set(cfgs.get('store') or ()) | set(cfgs.get('fulfillment') or ()),
        'channel': set(layout.get('channels') or ()),
    }


def _generalize_file(segments: list[str], axes: dict[str, set]) -> str:
    """Turn a run-root-relative path into a template, consuming levels POSITIONALLY.

    Positional consumption is the whole point: `<pair>/store/store/` is a real path where the
    config and the channel share a name, so any name-based guess is wrong. Each level is taken only
    at its own depth, and only if the segment is a known value for that axis.
    """
    order = ('cell', 'pair', 'config', 'channel')
    out: list[str] = []
    i = 0
    for axis in order:
        if i >= len(segments) - 1:              # keep at least the filename
            break
        if segments[i] in axes[axis]:
            out.append('{%s}' % axis)
            i += 1
        elif axis == 'channel':
            break                                # optional level simply absent
        elif segments[i].startswith('_'):
            break                                # reserved subtree (_frozen, _aggregate, _runtime)
        else:
            break
    # Everything between the consumed levels and the filename is a literal sub-path, except that
    # `_frozen`/`_aggregate` hold an axis value directly beneath them.
    mid = segments[i:-1]
    j = 0
    while j < len(mid):
        seg = mid[j]
        if seg in ('_frozen',) and j + 1 < len(mid) and mid[j + 1] in axes['pair']:
            out.extend([seg, '{pair}'])
            j += 2
            continue
        if seg == '_viz' and j + 1 < len(mid):
            # The viewer's derived subtree MIRRORS the run tree beneath its reserved prefix, so the
            # same four levels follow it — and `<config>/<channel>` is again `store/store` on a
            # store-only-named channel, so they are consumed positionally exactly as above.  Without
            # this branch every arm's sidecar generalizes to a fully LITERAL template, which no
            # declaration can ever match: 272 permanent "undeclared" findings on a full sweep.
            out.append(seg)
            j += 1
            for ax in ('cell', 'pair', 'config', 'channel'):
                if j < len(mid) and mid[j] in axes[ax]:
                    out.append('{%s}' % ax)
                    j += 1
            continue
        if seg == '_aggregate' and j + 1 < len(mid):
            out.append(seg)
            if mid[j + 1] in axes['config']:
                out.append('{config}')
                j += 2
                if j < len(mid) and mid[j] in axes['channel']:
                    out.append('{channel}')
                    j += 1
                continue
            j += 1
            continue
        if seg == 'stats_by_initial' and j + 1 < len(mid):
            # The stats suite fans out one subdir per initial-assignment group (fifo_norsl,
            # rank_labor_norsl, …) — a value axis, not a fixed name.  With the full 34-arm suite
            # that is 17 directories, so it MUST be a placeholder or every run looks like a new tree.
            out.extend([seg, '{initial_group}'])
            j += 2
            continue
        out.append(seg)
        j += 1

    fn = segments[-1]
    m = _STRATEGY_RE.match(fn)
    if fn.endswith('.viz.db') and out and out[0] == '_viz':
        # Checked BEFORE the sim-DB pattern and anchored to the `_viz` subtree: `<arm>.viz.db`
        # carries no `sim_` prefix, so it is the arm axis under a different spelling, and the
        # anchor stops the rule leaking to any other location.
        fn = '{strategy}.viz.db'
    elif m and m.group('strategy'):
        fn = 'sim_{strategy}.keyframes.db' if m.group('kf') else 'sim_{strategy}.db'
    elif _CKPT_RE.match(fn):
        fn = '_ckpt_{strategy}.pkl'
    elif _BATCHES_RE.match(fn):
        fn = '_batches_*.pkl'
    elif fn.endswith('.png') and out:
        # Graph directories hold dozens of PNGs whose names are a plotting detail, so they collapse
        # to one template per directory.  At the run ROOT (no directory above) there are only a
        # handful, all individually named — keep those literal, since precision is free there.
        fn = '*.png'
    out.append(fn)
    return '/'.join(out)


def observe(base_dir: str) -> dict:
    """Derive the on-disk shape of a finished run: the set of generalized path templates.

    Returns {'templates': {template: count}, 'levels_seen': {...}, 'layout': {...}}.
    """
    from Optimization.runschema.sim_manifest import read_run_layout
    layout = read_run_layout(base_dir) or {}
    axes = _axis_values(layout)
    templates: dict[str, int] = {}
    levels_seen: set[str] = set()

    for root, _dirs, files in os.walk(base_dir):
        rel = os.path.relpath(root, base_dir)
        parts = [] if rel == '.' else rel.replace('\\', '/').split('/')
        for axis in ('cell', 'pair', 'config', 'channel'):
            for depth, seg in enumerate(parts):
                if seg in axes[axis] and depth == ('cell', 'pair', 'config', 'channel').index(axis):
                    levels_seen.add(axis)
        for fn in files:
            # SQLite reader litter is not tree shape.  A mode=ro open CREATES `-wal`/`-shm`
            # beside any WAL database and cannot remove them (see the wal-sidecars memory), so
            # whether a canary tree carries one depends on connection-close ORDER during the
            # canary's own analysis — nondeterministic, and never something a consumer resolves.
            # Writers checkpoint-clean on close (183b1e6); declaring these would freeze an
            # accident into the contract, and observing them fails validation spuriously.
            if fn.endswith(('-wal', '-shm', '-journal')):
                continue
            tmpl = _generalize_file(parts + [fn], axes)
            templates[tmpl] = templates.get(tmpl, 0) + 1
    return {'templates': templates, 'levels_seen': sorted(levels_seen), 'layout': layout,
            'base': base_dir}


# ── stage 4: validate ───────────────────────────────────────────────────────────

def _declared_templates(doc: dict) -> dict[str, str]:
    """contract path template -> artifact key, normalized to the observe() vocabulary.

    `{channel?}` expands to BOTH shapes (with and without the segment) because both are legitimate
    — that is precisely the optionality being modelled.

    Skips artifacts that are not FILE templates: aliases (a `resolves_via` entry has no path of its
    own) and directory entries.  Matching observed files against those would be meaningless, and
    would make them permanently show up as "declared but never produced".
    """
    out: dict[str, str] = {}
    for key, spec in doc['artifacts'].items():
        path = spec.get('path')
        if not path or spec.get('format') == 'dir':
            continue
        variants = [path.replace('/{channel?}', '/{channel}'), path.replace('/{channel?}', '')] \
            if '{channel?}' in path else [path]
        for v in variants:
            out.setdefault(v, key)
    return out


def _template_regex(declared: str) -> re.Pattern:
    """Compile a declared path template into a matcher for OBSERVED templates.

    Observed templates carry placeholders as literal text (`{cell}`), so those compare exactly; only
    the glob forms are fuzzy:  `**/` spans any number of segments, `*` stays within one.
    """
    pat = re.escape(declared)
    pat = pat.replace(re.escape('**/'), '(?:[^/]+/)*')
    pat = pat.replace(re.escape('**'), '.*')
    pat = pat.replace(re.escape('*'), '[^/]*')
    return re.compile(f'^{pat}$')


def validate(doc: dict, obs_a: dict, obs_b: dict) -> dict:
    """Compare both canaries against the committed contract.

    Returns {'undeclared': {...}, 'never_produced': [...], 'optionality': [...], 'ok': bool}.
    `undeclared` is the finding that matters most: a file the tree produces that no consumer can
    be told about.
    """
    declared = _declared_templates(doc)
    decl_keys = set(declared.values())

    seen_a = set(obs_a['templates']) if obs_a else set()
    seen_b = set(obs_b['templates']) if obs_b else set()
    seen = seen_a | seen_b

    # Exact declarations win over glob ones, so a specifically-declared file is never swallowed by
    # a broad `**/*.png` bundle that happens to sit above it.
    globbed = [(t, k, _template_regex(t)) for t, k in declared.items() if '*' in t]

    def _match(tmpl: str) -> str | None:
        if tmpl in declared:
            return declared[tmpl]
        for _dt, key, rx in globbed:
            if rx.match(tmpl):
                return key
        return None

    undeclared: dict[str, str] = {}
    # Which canaries produced each declared artifact — resolved through the SAME matcher that
    # classifies paths, so glob-declared bundles (…/**/*.png) are credited correctly.  Comparing
    # declared template strings to observed ones directly would mark every glob artifact absent.
    produced: dict[str, set[str]] = {}
    for tmpl in sorted(seen):
        where = 'A+B' if (tmpl in seen_a and tmpl in seen_b) else ('A' if tmpl in seen_a else 'B')
        key = _match(tmpl)
        if key is None:
            undeclared[tmpl] = where
        else:
            produced.setdefault(key, set()).update({'A', 'B'} if where == 'A+B' else {where})

    never = sorted(decl_keys - set(produced))

    optionality: list[str] = []
    for key in sorted(produced):
        if doc['artifacts'][key].get('optional', False):
            continue
        got = produced[key]
        if got != {'A', 'B'}:
            optionality.append(
                f'{key}: declared REQUIRED but produced by canary {"/".join(sorted(got))} only')

    return {'undeclared': undeclared, 'never_produced': never, 'optionality': optionality,
            'ok': not undeclared and not optionality}


# ── stage 5: bump + orchestrator updates ────────────────────────────────────────

def _format_of(tmpl: str) -> str:
    return _EXT_FORMAT.get(os.path.splitext(tmpl)[1], 'unknown')


def _scope_of(tmpl: str) -> str:
    """Which level a newly-observed template belongs to — the deepest axis it consumes.

    `_aggregate/` is per-CELL even though it carries {config}/{channel} beneath it: those are the
    grouping key of a cross-profile rollup, not the run's own config/channel levels.
    """
    parts = tmpl.split('/')
    if '_aggregate' in parts:
        return 'cell'
    if '{channel}' in parts or '{config}' in parts:
        return 'channel_run' if parts[-1] != 'config.json' else 'config'
    if '{pair}' in parts:
        return 'pair'
    if '{cell}' in parts:
        return 'cell'
    return 'run'


def _artifact_key_for(tmpl: str) -> str:
    """A stable snake_case key for a newly-observed template."""
    stem = os.path.splitext(os.path.basename(tmpl))[0]
    stem = stem.replace('{', '').replace('}', '').replace('*', 'any').replace('.', '_')
    ext = os.path.splitext(tmpl)[1].lstrip('.')
    key = re.sub(r'[^0-9a-zA-Z]+', '_', f'{stem}_{ext}').strip('_').lower()
    return key or 'unnamed_artifact'


def _collapse_channel_variants(undeclared: dict[str, str]) -> dict[str, str]:
    """Fold `…/{channel}/x` + `…/x` into one `…/{channel?}/x` entry.

    The two canaries observe the SAME artifact under both shapes — mixed catalogs put it under a
    channel dir, store-only ones don't.  Emitting two scaffold entries would hard-code exactly the
    "channel is always there" assumption this whole package exists to prevent, so an artifact seen
    in both shapes becomes one required entry with an optional segment.
    """
    out: dict[str, str] = {}
    stripped: dict[str, list[str]] = {}
    for tmpl in undeclared:
        stripped.setdefault(tmpl.replace('/{channel}/', '/'), []).append(tmpl)
    for key, variants in stripped.items():
        withch = next((v for v in variants if '/{channel}/' in v), None)
        if withch and len(variants) > 1:
            out[withch.replace('/{channel}/', '/{channel?}/')] = 'A+B'
            continue
        for v in variants:
            out[v] = undeclared[v]
    return out


def proposed_entries(findings: dict) -> list[tuple[str, str]]:
    """[(artifact_key, source_text), …] — the ARTIFACTS entries the canaries say are missing.

    path/format/scope/optional are derived mechanically from the observed template.  `writer` and
    `condition` stay TODO because no script can infer which function creates a file or why it is
    conditional; those TODOs are deliberately loud.

    Channel variants collapse first, so an artifact seen under BOTH tree shapes becomes one
    `{channel?}` entry rather than two — otherwise the proposal would hard-code the very
    "channel is always present" assumption this package exists to prevent.
    """
    out: list[tuple[str, str]] = []
    for tmpl, where in sorted(_collapse_channel_variants(findings['undeclared']).items()):
        key = _artifact_key_for(tmpl)
        optional = where != 'A+B'
        out.append((key,
                    f"    {key!r}: {{\n"
                    f"        'path': {tmpl!r},\n"
                    f"        'format': {_format_of(tmpl)!r}, 'scope': {_scope_of(tmpl)!r},\n"
                    f"        'optional': {optional},\n"
                    + (f"        'condition': 'TODO — observed only in canary {where}; state WHY.',\n"
                       if optional else '')
                    + f"        'writer': 'TODO@TODO',   # TODO(schema-preflight): who writes this?\n"
                    f"    }},"))
    return out


def apply_entries(entries: list[tuple[str, str]]) -> str:
    """Insert proposed ARTIFACTS entries into runschema/schema.py.  Returns the path.

    Editing the ONE declaration is the whole point of dropping per-version modules: there is no
    `v<N+1>.py` to scaffold, so the new schema id falls out of the edited tables automatically.
    """
    path = os.path.join(_HERE, 'schema.py')
    with open(path, encoding='utf-8') as f:
        src = f.read()
    block = ('\n    # ── added by runschema.preflight (canary-observed; fill the TODOs) ──\n'
             + '\n'.join(text for _k, text in entries) + '\n')
    marker = '\n    # ── transient per-channel-run state'
    if marker in src:
        src = src.replace(marker, block + marker, 1)
    else:
        src = src.rstrip()[:-1].rstrip() + block + '}\n'      # before the ARTIFACTS closing brace
    with open(path, 'w', encoding='utf-8') as f:
        f.write(src)
    return path


def _yaml_pattern(tmpl: str) -> str:
    """contract template -> the angle-bracket pattern style context/artifacts.yml uses."""
    out = tmpl.replace('/{channel?}', '[/<channel>]')
    out = re.sub(r'\{(\w+)\}', r'<\1>', out)
    return f'comparison_<ts>/{out}' if out else 'comparison_<ts>'


def update_artifacts_yml(doc: dict, path: str | None = None) -> list[str]:
    """Rewrite context/artifacts.yml `path_pattern:` lines from the contract.

    Line-level edit (not a YAML round-trip) so the file's comments and ordering survive — they are
    the documentation.  Only ids that exist in BOTH the contract and the file are touched.
    Returns the list of ids whose pattern changed.
    """
    path = path or os.path.join(_REPO_ROOT, 'context', 'artifacts.yml')
    with open(path, encoding='utf-8') as f:
        lines = f.readlines()

    want = {k: _yaml_pattern(v['path']) for k, v in doc['artifacts'].items() if v.get('path')}
    changed: list[str] = []
    current: str | None = None
    for i, line in enumerate(lines):
        m = re.match(r'^  (\w+):\s*$', line)
        if m:
            current = m.group(1)
            continue
        if current and current in want:
            pm = re.match(r'^(\s*path_pattern:\s*)"(.*)"\s*$', line)
            if pm and pm.group(2) != want[current]:
                lines[i] = f'{pm.group(1)}"{want[current]}"\n'
                changed.append(current)
                current = None
    if changed:
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
    return changed


def update_viewer_schema(doc: dict, path: str | None = None) -> str:
    """Emit Visualization/static/schema.json — the slice of the contract the front end reads.

    The viewer builds its cascading run selectors from `axes` and `levels`, so adopting a new schema
    changes the UI's navigation without a single JS edit.
    """
    path = path or os.path.join(_REPO_ROOT, 'Visualization', 'static', 'schema.json')
    payload = {
        'schema_id': doc['schema_id'],
        'schema_short': contract.short_id(doc['schema_id']),
        'generated_by': 'Optimization/runschema/preflight.py',
        'axes': doc['axes'],
        'levels': doc['levels'],
        'artifacts': {k: {'path': v.get('path'), 'format': v['format'], 'scope': v['scope'],
                          'optional': v.get('optional', False)}
                      for k, v in doc['artifacts'].items()},
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.tmp.{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
        f.write('\n')
    os.replace(tmp, path)
    return path


# ── orchestration ───────────────────────────────────────────────────────────────

def _report(findings: dict, echo) -> None:
    if findings['undeclared']:
        echo(f'  {len(findings["undeclared"])} path(s) produced but NOT declared in the contract:')
        for tmpl, where in sorted(findings['undeclared'].items()):
            echo(f'    + {tmpl}   [canary {where}]')
    if findings['optionality']:
        echo('  optionality mismatches:')
        for m in findings['optionality']:
            echo(f'    ! {m}')
    if findings['never_produced']:
        echo('  declared but not produced by either canary (expected for run-condition artifacts):')
        echo(f'    - {", ".join(findings["never_produced"])}')


def _confirm(question: str, assume_yes: bool, echo) -> bool:
    """Interactive gate.  Non-interactive without --yes is a refusal, never a silent write."""
    if assume_yes:
        return True
    if not sys.stdin or not sys.stdin.isatty():
        echo('[preflight] non-interactive and no --yes — refusing to write. Run '
             '`python -m Optimization.runschema.preflight --yes` or pass --no-preflight.')
        return False
    try:
        ans = input(f'[preflight] {question} [y/N] ').strip().lower()
    except EOFError:
        ans = ''
    if ans not in ('y', 'yes'):
        echo('[preflight] declined — no files written.')
        return False
    return True


def ensure(echo=print, *, assume_yes: bool = False, force: bool = False,
           keep_workdir: bool = False, apply_entries_to_schema: bool = False) -> int:
    """The full preflight.  Returns 0 to proceed with the run, non-zero to stop.

    Stage 1 short-circuits when nothing shape-defining changed, which is the common case.
    """
    if os.environ.get(SKIP_ENV) == '1':
        return 0

    short = contract.short_id
    changed, old_fp, new_fp = sources_changed()
    head = contract.head()
    if not changed and not force:
        echo(f'[preflight] run-tree schema {short(head) if head else "(none)"} current '
             f'(no shape-defining source change).')
        return 0

    # The declaration is the source of truth; its hash is the candidate id for this run.
    doc = contract.build()
    if head is None:
        echo('[preflight] schema store is empty; minting from runschema/schema.py.')
    elif doc['schema_id'] != head:
        echo(f'[preflight] schema.py now hashes to {short(doc["schema_id"])} '
             f'(head is {short(head)}) — the declaration was edited.')

    echo(f'[preflight] shape-defining sources changed since the recorded fingerprint.')
    echo(f'            {old_fp or "(none)"} -> {new_fp}')
    echo('[preflight] proving the tree shape with two canary runs (this is the slow path)...')

    workdir = tempfile.mkdtemp(prefix='ilo_preflight_')
    try:
        t0 = time.time()
        runs = run_canaries(workdir, echo=lambda m: echo(f'[preflight] {m}'))
        if not runs['A'] or not runs['B']:
            echo('[preflight] canary run(s) failed — cannot validate the tree shape.')
            echo(f'[preflight] workdir kept for inspection: {workdir}')
            keep_workdir = True
            return 2
        obs_a, obs_b = observe(runs['A']), observe(runs['B'])
        echo(f'[preflight] canaries done in {time.time() - t0:.0f}s  '
             f'(A levels: {obs_a["levels_seen"]}, B levels: {obs_b["levels_seen"]})')

        findings = validate(doc, obs_a, obs_b)
        _report(findings, lambda m: echo(f'[preflight] {m}'))

        # ── the declaration ALREADY covers the observed tree ────────────────────
        if findings['ok']:
            if doc['schema_id'] == head:
                contract.adopt(doc, source_fp=new_fp)      # refresh the trigger only
                update_viewer_schema(doc)
                echo(f'[preflight] tree shape UNCHANGED — schema {short(head)} still valid; '
                     f'refreshed the source fingerprint.')
                return 0

            echo(f'[preflight] the declaration changed and the canaries CONFIRM it: '
                 f'{short(head) if head else "(none)"} -> {short(doc["schema_id"])}')
            for m in (contract.diff_shape(contract.load(head), doc) if head else ['initial schema']):
                echo(f'[preflight]   - {m}')
            if not _confirm(f'adopt schema {short(doc["schema_id"])}?', assume_yes, echo):
                return 3
            contract.adopt(doc, source_fp=new_fp)
            update_artifacts_yml(doc)
            update_viewer_schema(doc)
            echo(f'[preflight] adopted {short(doc["schema_id"])} (parent '
                 f'{short(head) if head else "none"}); artifacts.yml + viewer schema refreshed.')
            return 0

        # ── the CODE moved but schema.py did not ────────────────────────────────
        entries = proposed_entries(findings)
        echo('[preflight] the tree shape CHANGED and the declaration does not cover it.')
        if not entries:
            echo('[preflight] no new paths to declare — the mismatch is optionality only; fix the '
                 '`optional` flags in runschema/schema.py by hand, then re-run.')
            return 4
        echo(f'[preflight] add these {len(entries)} entr(y|ies) to ARTIFACTS in '
             f'Optimization/runschema/schema.py:')
        for _key, text in entries:
            for line in text.splitlines():
                echo(f'[preflight]   {line}')
        if not apply_entries_to_schema:
            echo('[preflight] re-run with --apply to insert them automatically, then fill every '
                 'TODO and re-run the preflight to adopt the resulting schema id.')
            return 4
        if not _confirm('insert them into schema.py?', assume_yes, echo):
            return 3
        path = apply_entries(entries)
        echo(f'[preflight] edited {os.path.relpath(path, _REPO_ROOT)} — fill every TODO, then '
             f're-run the preflight; the new schema id follows from the edited tables.')
        return 4
    finally:
        if keep_workdir:
            echo(f'[preflight] canary workdir: {workdir}')
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='Prove the run-tree contract before a simulation (detect -> canary -> '
                    'validate -> adopt).')
    ap.add_argument('--check', action='store_true',
                    help='stage 1 only: exit 1 if shape-defining sources changed or the schema '
                         'store is stale. Writes nothing. Used by the Stop hook and the tests.')
    ap.add_argument('--yes', action='store_true', help='adopt without prompting (unattended)')
    ap.add_argument('--apply', action='store_true',
                    help='when the canaries find undeclared paths, insert the proposed ARTIFACTS '
                         'entries into runschema/schema.py instead of only printing them')
    ap.add_argument('--force', action='store_true',
                    help='run the canaries even when no source change was detected')
    ap.add_argument('--keep-workdir', action='store_true', help='keep the canary temp dir')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)

    echo = (lambda _m: None) if args.quiet else print

    if args.check:
        stale = contract.main(['--check', '--quiet'])
        if stale:
            print('[schema] the run-tree schema store is stale vs runschema/schema.py — adopt it: '
                  'python -m Optimization.runschema.contract --write')
            return 1
        changed, _old, _new = sources_changed()
        head = contract.head()
        if changed:
            print(f'[schema] shape-defining source changed since the recorded fingerprint for '
                  f'run-tree schema {contract.short_id(head) if head else "(none)"} — the output '
                  f'tree may have moved. Validate before the next run: '
                  f'python -m Optimization.runschema.preflight')
            return 1
        echo(f'run-tree schema {contract.short_id(head)} current.')
        return 0

    return ensure(echo=print, assume_yes=args.yes, force=args.force,
                  keep_workdir=args.keep_workdir, apply_entries_to_schema=args.apply)


if __name__ == '__main__':
    sys.exit(main())
