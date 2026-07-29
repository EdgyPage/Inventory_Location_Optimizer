"""smoketest.py — one-command pipeline smoketest: simulate, analyse, then verify the finished run
tree against the run-tree contract.

The point is the VERIFICATION. A simulation that exits 0 proves very little: `analyze_run` wraps
every step in a handler that logs and swallows, so a half-finished analysis still exits 0, and the
only way to know an artifact landed where the contract says it should is to go and look. Stage 3
does that by reusing the contract machinery itself (`runschema.preflight.observe` / `validate` and
the resolver) rather than by hand-rolling paths — so it stays correct when the tree shape moves.

Three profiles:
    tiny    the smallest run that still produces every structural feature — for shaking out the
            chain end to end, not for measuring anything
    smoke   capped SKUs, the fast default for routine reuse
    full    production scale, 10 batches — what you run when the tree shape has changed

All three use the multi-cell what-if spec, because `_frozen/<pair>/` and the six what-if artifacts
exist ONLY on a multi-cell run; a single-cell run can never exercise those levels.

The last stage archives a completed cell to bulk storage and then re-runs the whole contract
verification. That is the only meaningful test of the archiver: the point of leaving a junction is
that nothing downstream can tell the difference, so "did the copy work" is the wrong question and
"does the same verification still pass" is the right one.

Nothing here writes a machine-local path into a tracked file: results go to stdout or to a path you
name, and every location is referred to by its .env key.

Run:
    python Tests/bench/smoketest.py                       # smoke profile, all stages
    python Tests/bench/smoketest.py --profile full
    python Tests/bench/smoketest.py --stages preconditions            # ~2s dry gate
    python Tests/bench/smoketest.py --reuse-run DIR --stages verify_tree
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, '..', '..'))
if _ROOT not in sys.path:                      # entry-script bootstrap (see Tests/conftest.py)
    sys.path.insert(0, _ROOT)

WORKER_CAP = 20                                # operator constraint: never exceed this
STAGES = ('preconditions', 'simulate', 'analyze', 'verify_tree', 'website', 'mkdocs', 'archive')

# Both profiles run the multi-cell spec. keyframe-interval 5 with 10 batches fires at i=0 and i=5,
# so `keyframes_db` is guaranteed to exist — a 0 interval would silently drop a declared artifact.
_COMMON = ('--spec', 'scheduler_ab', '--n-batches', '10', '--keyframe-interval', '5')


@dataclass(frozen=True)
class Profile:
    name: str
    sim_args: tuple[str, ...]
    timeout_s: int
    free_gb: int
    note: str


PROFILES = {
    # The pipeline-shakeout size: every structural feature of a real run (2 cells, both channels,
    # all 34 arms, the _frozen level, the what-if outputs) at the smallest scale that still produces
    # them. For troubleshooting the chain end to end, not for measuring anything.
    'tiny': Profile('tiny', ('--spec', 'scheduler_ab', '--n-batches', '6',
                             '--keyframe-interval', '3',
                             '--max-skus', '8000',
                             '--s-max-bins', '9000', '--ff-max-bins', '12000',
                             '--max-tasks-per-child', '6'),
                    timeout_s=3600, free_gb=15, note='pipeline shakeout; target < 20 min'),

    # ~13% scale. The first 20k SKUs are ~40% fulfillment (SKU ids interleave the families), so the
    # catalogue stays MIXED and the optional <channel> level still appears. A cap that produced a
    # store-only catalogue would silently stop testing the thing this exists to test.
    'smoke': Profile('smoke', _COMMON + (
        '--max-skus', '20000',
        '--s-max-bins', '20000', '--ff-max-bins', '27000',
        '--max-tasks-per-child', '4',
    ), timeout_s=3600, free_gb=40, note='capped; routine reuse'),

    # Sizing verbatim from the last real production run's run_spec.json.
    'full': Profile('full', _COMMON + (
        '--s-max-bins', '150000', '--ff-max-bins', '200000',
        '--max-tasks-per-child', '1',
    ), timeout_s=14400, free_gb=250, note='production scale, 10 batches'),
}


@dataclass
class StageResult:
    name: str
    status: str = 'skip'                       # pass | warn | fail | skip
    seconds: float = 0.0
    messages: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {'status': self.status, 'seconds': round(self.seconds, 1),
                'messages': self.messages, 'evidence': self.evidence}


@dataclass
class SmokeResult:
    profile: str
    workers: int
    ok: bool = False
    seconds: float = 0.0
    run_dir: str | None = None
    schema_short: str | None = None
    stages: dict = field(default_factory=dict)

    def failed(self) -> list:
        return [n for n, s in self.stages.items() if s.status == 'fail']

    def as_dict(self) -> dict:
        return {'profile': self.profile, 'workers': self.workers, 'ok': self.ok,
                'seconds': round(self.seconds, 1), 'schema_short': self.schema_short,
                'stages': {n: s.as_dict() for n, s in self.stages.items()}}


class _Ctx:
    """Mutable state threaded between stages."""

    def __init__(self, profile: Profile, workers: int, echo, exp: str = '_smoketest',
                 keep_experiment: bool = False):
        self.profile = profile
        self.workers = workers
        self.echo = echo
        self.exp = exp
        self.keep_experiment = keep_experiment
        self.run_dir: str | None = None
        self.mixed: bool | None = None         # measured from the catalogue, NOT from the run tree
        self.pairs: list = []
        self.git_baseline: set = set()


def _py(*args: str) -> list:
    return [sys.executable, *args]


def _sh(cmd: list, timeout: int = 600, env: dict | None = None) -> tuple[int, str]:
    e = dict(os.environ)
    e.setdefault('PYTHONIOENCODING', 'utf-8')
    e.setdefault('MPLBACKEND', 'Agg')
    if env:
        e.update(env)
    p = subprocess.run(cmd, cwd=_ROOT, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=timeout, env=e)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


# ── stage 0: preconditions ──────────────────────────────────────────────────────────────────
def _stage_preconditions(ctx: _Ctx) -> StageResult:
    r = StageResult('preconditions')
    ev, msgs = r.evidence, r.messages

    # The gate that matters most. On a source change preflight fires two canary simulations and,
    # run non-interactively without --yes, refuses and returns 3 — which kills run_simulation AFTER
    # it has already created the run directory. Catching it here costs 2s instead of a wasted run.
    rc, out = _sh(_py('-m', 'Optimization.runschema.preflight', '--check'), timeout=300)
    ev['preflight_check_rc'] = rc
    if rc != 0:
        msgs.append('preflight --check failed: the run-tree schema sources changed since the last '
                    'adopt. The next simulation would fire two canary runs and then abort. '
                    'Resolve first: python -m Optimization.runschema.preflight --yes')
        msgs.append(out.strip()[:400])
        r.status = 'fail'
        return r

    from Optimization import runschema
    from Optimization.config import sim_config as sc
    from Optimization.runschema import runlayout

    head = runschema.head()
    ev['schema_head'] = head[:19] if head else None
    if not head:
        msgs.append('no head schema in the store — run: python -m Optimization.runschema.contract --write')
        r.status = 'fail'
        return r

    # Output root must be configured AND outside the repo. A wrong _REPO_ROOT depth in sim_config
    # silently falls back to the source tree and writes a 150-200 GB run into git with no error.
    out_dir = sc._OUTPUT_DIR
    ev['output_dir_configured'] = bool(os.environ.get('COMPARISON_OUTPUT_DIR'))
    inside_repo = os.path.normcase(os.path.abspath(out_dir)).startswith(os.path.normcase(_ROOT))
    if not os.path.isdir(out_dir):
        msgs.append('COMPARISON_OUTPUT_DIR does not resolve to a directory — check .env')
        r.status = 'fail'
        return r
    if inside_repo:
        msgs.append('COMPARISON_OUTPUT_DIR resolves INSIDE the repo. That is the sim_config '
                    '_REPO_ROOT fallback and it would write the whole run into git. Fix .env.')
        r.status = 'fail'
        return r

    free_gb = shutil.disk_usage(out_dir).free / (1024 ** 3)
    ev['free_gb'] = round(free_gb, 1)
    if free_gb < ctx.profile.free_gb:
        msgs.append(f'only {free_gb:.0f} GB free on the output drive; profile '
                    f'{ctx.profile.name} wants >= {ctx.profile.free_gb} GB')
        r.status = 'fail'
        return r

    pairs = runlayout.find_latest_db_pairs(sc._DEFAULT_PROFILES_DIR)
    ctx.pairs = [lbl for lbl, _i, _a in pairs]
    ev['pairs'] = ctx.pairs
    if not pairs:
        msgs.append('no inventory+affinity DB pairs under PROFILE_INPUT_DIR — nothing to simulate')
        r.status = 'fail'
        return r

    # Measure mixedness from the CATALOGUE, never from the run tree. Stage 3's expectations are
    # derived from this; reading it back out of the tree it is checking would be circular.
    cap = _arg_value(ctx.profile.sim_args, '--max-skus')
    shares = {}
    for label, inv_db, _aff in pairs:
        shares[label] = _fulfillment_share(inv_db, int(cap) if cap else None)
    ctx.mixed = any(s > 0 for s in shares.values())
    ev['fulfillment_share'] = {k: round(v, 4) for k, v in shares.items()}
    if not ctx.mixed:
        msgs.append('catalogue is store-only at this SKU cap — the optional <channel> level will '
                    'not appear, so this run cannot exercise it')
        r.status = 'warn'

    missing = [m for m in ('yaml', 'matplotlib') if not _importable(m)]
    ev['missing_imports'] = missing
    if missing:
        msgs.append(f'missing optional imports: {missing}')
        r.status = 'warn' if r.status != 'fail' else r.status

    ctx.git_baseline = _git_dirty()
    ev['git_dirty_at_start'] = len(ctx.git_baseline)

    if r.status == 'skip':
        r.status = 'pass'
    msgs.insert(0, f'schema {head[7:19]}, {len(pairs)} pair(s), '
                   f'{free_gb:.0f} GB free, mixed={ctx.mixed}')
    return r


def _arg_value(args: tuple, flag: str) -> str | None:
    args = list(args)
    return args[args.index(flag) + 1] if flag in args else None


def _importable(mod: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def _fulfillment_share(inv_db: str, limit: int | None) -> float:
    """Fraction of the SKUs this run will actually load that are fulfillment-family.

    Mirrors load_inventory_from_db's `ORDER BY sku LIMIT n`, because a cap that happened to select
    only store SKUs would silently remove the <channel> level from the tree.
    """
    import pathlib
    uri = pathlib.Path(inv_db).as_uri() + '?mode=ro'
    con = sqlite3.connect(uri, uri=True)
    try:
        sub = 'SELECT handling FROM cartons ORDER BY sku' + (f' LIMIT {limit}' if limit else '')
        rows = con.execute(f'SELECT handling, COUNT(*) FROM ({sub}) GROUP BY handling').fetchall()
    finally:
        con.close()
    total = sum(n for _h, n in rows) or 1
    ff = sum(n for h, n in rows if h == 'fulfillment')
    return ff / total


def _git_dirty() -> set:
    rc, out = _sh(['git', 'status', '--porcelain', '-uall'], timeout=120)
    return {ln[3:].strip().strip('"') for ln in out.splitlines() if ln[3:].strip()} if rc == 0 else set()


# ── stage 1: simulate ───────────────────────────────────────────────────────────────────────
def _stage_simulate(ctx: _Ctx) -> StageResult:
    r = StageResult('simulate')
    from Optimization.config import sim_config as sc

    before = set(os.listdir(sc._OUTPUT_DIR))
    cmd = _py('-m', 'Optimization.run_simulation',
              '--workers', str(ctx.workers), '--analysis-workers', str(ctx.workers),
              *ctx.profile.sim_args)
    r.evidence['argv'] = cmd[1:]
    ctx.echo(f'  running: {" ".join(cmd[1:])}')

    t0 = time.time()
    rc, out = _sh(cmd, timeout=ctx.profile.timeout_s)
    r.seconds = time.time() - t0
    r.evidence['returncode'] = rc
    r.evidence['tail'] = out.strip().splitlines()[-25:]

    # Two independent ways to name the run dir; disagreement means something is wrong with either
    # the output root or our understanding of it.
    new = sorted(set(os.listdir(sc._OUTPUT_DIR)) - before)
    stated = [ln.split(':', 1)[1].strip() for ln in out.splitlines()
              if ln.strip().startswith('Output directory')]
    if new:
        ctx.run_dir = os.path.join(sc._OUTPUT_DIR, new[-1])
    elif stated:
        ctx.run_dir = stated[-1]
    r.evidence['run_basename'] = os.path.basename(ctx.run_dir) if ctx.run_dir else None

    if rc != 0:
        r.status = 'fail'
        r.messages.append(f'run_simulation exited {rc}')
        return r
    if not ctx.run_dir:
        r.status = 'fail'
        r.messages.append('could not identify the run directory')
        return r
    r.status = 'pass'
    r.messages.append(f'{os.path.basename(ctx.run_dir)} in {r.seconds / 60:.1f} min')
    return r


# ── stage 2: analysis health ────────────────────────────────────────────────────────────────
def _stage_analyze(ctx: _Ctx) -> StageResult:
    """Verify the in-process analysis did not half-fail.

    analyze_run wraps every step in a handler that logs and returns, so the process exits 0 whether
    analysis succeeded or every single step raised. The log is the only signal.
    """
    r = StageResult('analyze')
    log_path = os.path.join(ctx.run_dir or '', 'run.log')
    if not os.path.isfile(log_path):
        r.status = 'fail'
        r.messages.append('run.log missing — cannot confirm analysis ran at all')
        return r
    with open(log_path, encoding='utf-8', errors='replace') as fh:
        lines = fh.read().splitlines()
    bad = [ln for ln in lines if 'analysis step failed [' in ln or 'Analysis failed for ' in ln]
    banner = [ln for ln in lines if 'ANALYSIS' in ln]
    r.evidence['failed_steps'] = bad[:20]
    r.evidence['saw_banner'] = bool(banner)
    if bad:
        r.status = 'fail'
        r.messages.append(f'{len(bad)} analysis step(s) failed silently (exit code was still 0)')
        return r
    if not banner:
        r.status = 'warn'
        r.messages.append('no ANALYSIS banner in run.log — was --no-analyze passed?')
        return r
    r.status = 'pass'
    r.messages.append('no swallowed analysis failures')
    return r


# ── stage 3: verify the tree against the contract ───────────────────────────────────────────
# Artifacts that exist only on a multi-cell run.
_SWEEP_ONLY = ('frozen_inventory_db', 'frozen_warehouse_db', 'whatif_delta_csv',
               'whatif_delta_json', 'whatif_delta_png', 'whatif_labor_csv',
               'whatif_labor_json', 'whatif_labor_pngs')
# Written by the flat stats suite; the default BY_INITIAL preset writes stats_by_initial/ instead.
_FLAT_STATS_ONLY = ('stats_pngs', 'stats_summary_csv', 'stats_tests_json')
# Present only while an arm/config is in flight; removed on finalize.
_IN_FLIGHT_ONLY = ('resume_pkl', 'checkpoint_pkl')
# Not file templates: a directory entry and a resolves_via alias. Checked via the resolver instead.
_NOT_TEMPLATES = ('aggregate_dir', 'planned_inventory')


def _stage_verify_tree(ctx: _Ctx) -> StageResult:
    r = StageResult('verify_tree')
    ev, msgs = r.evidence, r.messages
    from Optimization import runschema
    from Optimization.runschema import contract, preflight

    rt = runschema.resolver_for(ctx.run_dir)
    # Judge the run against the contract it was WRITTEN with, not against whatever is current.
    doc = contract.load(rt.schema_id)
    obs = preflight.observe(ctx.run_dir)
    find = preflight.validate(doc, obs, obs)

    ev['schema_short'] = rt.schema_short
    ev['head_matches'] = (rt.schema_id == contract.head())
    ev['is_sweep'] = rt.is_sweep
    ev['levels_seen'] = obs['levels_seen']
    ev['n_templates'] = len(obs['templates'])

    # Passing one observation twice makes every template 'A+B', so validate's optionality branch is
    # unreachable. Assert that rather than letting its emptiness read as a pass.
    assert not find['optionality'], 'optionality is unreachable with a single observation'

    arts = doc['artifacts']
    kf_on = int((rt.layout.get('keyframe_interval') or 5)) > 0
    required = {k for k, v in arts.items() if not v.get('optional') and k not in _NOT_TEMPLATES}
    cond_req = set()
    if rt.is_sweep:
        cond_req |= set(_SWEEP_ONLY)
    if kf_on:
        cond_req.add('keyframes_db')
    must_absent = set(_FLAT_STATS_ONLY) | set(_IN_FLIGHT_ONLY)
    if rt.is_sweep:
        must_absent.add('planned_inventory_db')     # the frozen copy is shared instead

    # Force a new artifact in schema.py to be classified here rather than silently ignored.
    classified = required | cond_req | must_absent | set(_NOT_TEMPLATES) | {'batches_cache'}
    unclassified = sorted(set(arts) - classified)
    ev['unclassified_artifacts'] = unclassified

    never = set(find['never_produced'])
    produced = set(preflight._declared_templates(doc).values()) - never

    required_missing = sorted((required | cond_req) - produced - set(_NOT_TEMPLATES))
    present_but_forbidden = sorted(must_absent & produced)
    undeclared = dict(find['undeclared'])

    ev['required_missing'] = required_missing
    ev['expected_absent_but_present'] = present_but_forbidden
    ev['undeclared'] = undeclared

    counts, problems = _leaf_checks(rt, ctx, kf_on)
    ev['counts'] = counts
    ev['leaf_problems'] = problems[:30]
    ev['non_vacuity'] = _negative_control(doc, obs, preflight)

    if unclassified:
        msgs.append(f'{len(unclassified)} artifact(s) in schema.py are not classified by this '
                    f'stage — classify them: {unclassified[:6]}')
    if required_missing:
        msgs.append(f'{len(required_missing)} required artifact(s) never produced: {required_missing[:8]}')
    if present_but_forbidden:
        msgs.append(f'{len(present_but_forbidden)} artifact(s) present that this run shape must NOT '
                    f'have: {present_but_forbidden} — a freeze or a finalize did not happen')
    if undeclared:
        msgs.append(f'{len(undeclared)} undeclared path template(s) — files no consumer knows about:')
        for tmpl, _where in sorted(undeclared.items())[:10]:
            msgs.append(f'    {tmpl}  (x{obs["templates"].get(tmpl, 0)})')
        try:
            ev['proposed_entries'] = [t for _k, t in preflight.proposed_entries({'undeclared': undeclared})]
        except Exception as exc:                        # advisory only
            ev['proposed_entries_error'] = repr(exc)
    if problems:
        msgs.append(f'{len(problems)} leaf problem(s): {problems[:5]}')

    if not ev['non_vacuity']['ok']:
        msgs.append('NEGATIVE CONTROL FAILED — the checker did not detect planted damage, so a '
                    'pass here means nothing')
        r.status = 'fail'
        return r

    r.status = 'fail' if (required_missing or present_but_forbidden or undeclared
                          or problems or unclassified) else 'pass'
    if r.status == 'pass':
        msgs.insert(0, f'{counts["sim_dbs"]["found"]} sim DBs across {counts["channel_runs"]["found"]} '
                       f'leaves; every declared artifact accounted for')
    return r


def _leaf_checks(rt, ctx: _Ctx, kf_on: bool) -> tuple[dict, list]:
    """Per-leaf existence + count checks, driven entirely through the resolver."""
    from collections import defaultdict
    problems: list = []
    layout = rt.layout
    cells = [n for n, _d in rt.cells()]
    pairs = list(layout.get('pairs') or [])
    cfgs = layout.get('configs') or {}
    # Prefer the catalogue measurement from stage 0; fall back to the run's own descriptor when
    # verifying a run we did not simulate (--reuse-run). Defaulting to False would silently HALVE
    # the expected counts on a mixed run and report a passing tree as broken.
    mixed = ctx.mixed
    if mixed is None:
        mixed = len(layout.get('channels') or []) > 1
    n_cfg = len(cfgs.get('store') or []) + (len(cfgs.get('fulfillment') or []) if mixed else 0)

    arms = []
    if cells:
        try:
            with open(rt.run_manifest(cells[0]), encoding='utf-8') as fh:
                arms = [s['key'] if isinstance(s, dict) else s
                        for s in (json.load(fh).get('strategies') or [])]
        except (OSError, ValueError, KeyError, TypeError):
            problems.append('could not read strategies from run_manifest.json')

    exp_leaves = len(cells) * len(pairs) * n_cfg
    exp_dbs = exp_leaves * len(arms)

    leaves = list(rt.channel_runs())
    for cell, cr in leaves:
        if not os.path.isfile(rt.sim_meta(cr)):
            problems.append(f'{cell}/{cr.pair}/{cr.group_key}: sim_meta.json missing')
        if not os.path.isfile(rt.series_json(cr)):
            problems.append(f'{cell}/{cr.pair}/{cr.group_key}: series.json missing')
        if mixed and cr.channel is None:
            problems.append(f'{cell}/{cr.pair}/{cr.config}: mixed catalogue but no <channel> level')

    dbs = list(rt.sim_dbs())
    by_leaf = defaultdict(set)
    kf_missing = 0
    for cell, cr, db in dbs:
        by_leaf[(cell, cr.pair, cr.group_key)].add(rt.strategy_of(db))
        if kf_on and not os.path.isfile(rt.keyframe_db(db)):
            kf_missing += 1
    if arms:
        for leaf, got in sorted(by_leaf.items()):
            if got != set(arms):
                problems.append(f'{"/".join(leaf)}: arms differ (missing {sorted(set(arms) - got)[:4]})')

    agg_dirs = {(cell, cr.group_key) for cell, cr in leaves}
    agg_found = sum(1 for cell, gk in agg_dirs if os.path.isdir(rt.aggregate_dir(cell, gk)))
    if agg_found != len(agg_dirs):
        problems.append(f'_aggregate dirs: {agg_found}/{len(agg_dirs)} present')

    # The resolves_via check with real teeth: RunTree.path returns the LAST candidate when none
    # exists, so os.path.isfile alone would pass even with resolution broken. Assert WHICH candidate
    # won — frozen on a sweep, per-cell otherwise.
    pi_via, pi_checked = set(), 0
    for cell in cells:
        for pair in pairs:
            p = rt.planned_inventory_db(cell, pair)
            pi_checked += 1
            if not os.path.isfile(p):
                problems.append(f'{cell}/{pair}: planned inventory did not resolve to a real file')
                continue
            rel = os.path.relpath(p, rt.base).replace('\\', '/')
            pi_via.add('_frozen' if rel.startswith('_frozen/') else 'per-cell')
    want_via = '_frozen' if rt.is_sweep else 'per-cell'
    if pi_via and pi_via != {want_via}:
        problems.append(f'planned_inventory resolved via {sorted(pi_via)}, expected {want_via}')

    wf = rt.whatif_outputs()
    if rt.is_sweep and len(wf) < 6:
        problems.append(f'sweep run but only {len(wf)} what-if output(s) exist')
    if not rt.is_sweep and wf:
        problems.append(f'single-cell run but {len(wf)} what-if output(s) exist')

    counts = {
        'cells': len(cells), 'pairs': len(pairs), 'configs': n_cfg, 'arms': len(arms),
        'mixed': mixed, 'mixed_source': 'catalogue' if ctx.mixed is not None else 'run_layout',
        'channel_runs': {'expected': exp_leaves, 'found': len(leaves)},
        'sim_dbs': {'expected': exp_dbs, 'found': len(dbs)},
        'keyframes_missing': kf_missing,
        'aggregate_dirs': {'expected': len(agg_dirs), 'found': agg_found},
        'whatif_outputs': len(wf),
        'planned_inventory': {'checked': pi_checked, 'via': sorted(pi_via)},
    }
    if exp_leaves and len(leaves) != exp_leaves:
        problems.insert(0, f'channel-run count {len(leaves)} != expected {exp_leaves}')
    if exp_dbs and len(dbs) != exp_dbs:
        problems.insert(0, f'sim DB count {len(dbs)} != expected {exp_dbs}')
    if kf_missing:
        problems.append(f'{kf_missing} keyframe DB(s) missing')
    return counts, problems


def _negative_control(doc: dict, obs: dict, preflight) -> dict:
    """Prove the classifier is live on THIS run's contract, every invocation.

    Without this, a stage that found nothing wrong is indistinguishable from a stage whose matcher
    silently matches everything.
    """
    out = {'ok': False, 'detected_missing': [], 'detected_undeclared': False}
    declared = preflight._declared_templates(doc)
    for victim in ('sim_meta', 'series_json', 'channel_rollup_csv'):
        tmpl = next((t for t, k in declared.items() if k == victim), None)
        if tmpl is None or tmpl not in obs['templates']:
            continue
        cut = {**obs, 'templates': {k: v for k, v in obs['templates'].items() if k != tmpl}}
        if victim in preflight.validate(doc, cut, cut)['never_produced']:
            out['detected_missing'].append(victim)
    bogus = '{cell}/{pair}/__not_a_real_artifact__.json'
    inj = {**obs, 'templates': {**obs['templates'], bogus: 1}}
    out['detected_undeclared'] = bogus in preflight.validate(doc, inj, inj)['undeclared']
    out['ok'] = bool(out['detected_missing']) and out['detected_undeclared']
    return out


# ── stage 4: website import into a disposable experiment ────────────────────────────────────
def _stage_website(ctx: _Ctx) -> StageResult:
    """Stage the run into a throwaway docs experiment, assert what landed, then remove it.

    Ingest is deliberately non-fatal: a missing curated figure logs MISSING and returns 0. So the
    only way a stale figure list gets noticed is to read its stdout, which is what this does.
    """
    r = StageResult('website')
    ev, msgs = r.evidence, r.messages
    from Optimization import runschema

    exp_dir = os.path.join(_ROOT, 'docs', 'experiments', ctx.exp)
    if os.path.exists(exp_dir):
        r.status = 'fail'
        msgs.append(f'docs/experiments/{ctx.exp}/ already exists — refusing to overwrite')
        return r

    rt = runschema.resolver_for(ctx.run_dir)
    cells = [n for n, _d in rt.cells()]
    pairs = list(rt.layout.get('pairs') or [])
    cfgs = sorted({cr.config for _c, cr in rt.channel_runs()})
    exp_leaf_dirs = len(cells) * len(pairs) * len(cfgs)

    try:
        rc, out = _sh(_py(os.path.join('docs', 'experiments', 'ingest.py'),
                          '--exp', ctx.exp, '--source', ctx.run_dir, '--gen-manifest'),
                      timeout=1800)
        ev['returncode'] = rc
        missing = [ln.strip() for ln in out.splitlines() if ln.strip().startswith('MISSING')]
        ev['missing_lines'] = missing[:20]
        ev['copied'] = next((ln for ln in out.splitlines() if 'copied' in ln), '')
        if rc != 0:
            r.status = 'fail'
            msgs.append(f'ingest exited {rc}')
            msgs.append(out.strip()[-400:])
            return r

        got = {'config_json': 0, 'figures': 0, 'params': 0, 'whatif_png': 0}
        for root, _dirs, files in os.walk(exp_dir):
            rel = os.path.relpath(root, exp_dir).replace('\\', '/')
            for fn in files:
                if fn == 'config.json':
                    got['config_json'] += 1
                elif fn == 'params.json':
                    got['params'] += 1
                elif fn.endswith('.png') and fn.startswith('whatif_') and rel == 'images':
                    got['whatif_png'] += 1
                elif fn.endswith('.png') and rel.startswith('images/'):
                    got['figures'] += 1
        ev['staged'] = got
        ev['expected_config_json'] = exp_leaf_dirs

        if got['config_json'] != exp_leaf_dirs:
            msgs.append(f'config.json staged {got["config_json"]}, expected {exp_leaf_dirs}')
        if got['params'] != len(pairs):
            msgs.append(f'params.json staged {got["params"]}, expected {len(pairs)} (one per pair)')
        if not got['figures']:
            msgs.append('no curated figures staged at all')
        if rt.is_sweep and got['whatif_png'] < 4:
            msgs.append(f'sweep run but only {got["whatif_png"]} whatif_*.png staged')
        if missing:
            msgs.append(f'{len(missing)} MISSING line(s) from ingest — a curated name is stale:')
            msgs.extend('    ' + m for m in missing[:6])

        wd = os.path.join(exp_dir, 'data', 'whatif_delta.json')
        if rt.is_sweep:
            if os.path.isfile(wd):
                with open(wd, encoding='utf-8') as fh:
                    ev['whatif_delta_keys'] = len(json.load(fh) or {})
                if not ev['whatif_delta_keys']:
                    msgs.append('whatif_delta.json staged but empty')
            else:
                msgs.append('whatif_delta.json not staged (sweep run)')

        # --gen-manifest stamps the run's schema id into the docs side; that is the docs half of
        # the very inference schema under test, so check it round-tripped.
        ym = os.path.join(exp_dir, 'experiment.yml')
        if os.path.isfile(ym) and _importable('yaml'):
            import yaml
            with open(ym, encoding='utf-8') as fh:
                man = yaml.safe_load(fh) or {}
            ev['manifest_schema_id'] = str(man.get('schema_id', ''))[:19]
            if man.get('schema_id') != rt.schema_id:
                msgs.append('experiment.yml schema_id does not match the run')
            if sorted(man.get('cells') or []) != sorted(cells):
                msgs.append('experiment.yml cells do not match the run')
        elif not os.path.isfile(ym):
            msgs.append('--gen-manifest produced no experiment.yml')

        r.status = 'fail' if msgs else 'pass'
        if r.status == 'pass':
            msgs.append(f'{got["config_json"]} config.json, {got["figures"]} figures, '
                        f'{got["params"]} params.json, {got["whatif_png"]} whatif png')
    finally:
        # Must always run: a leftover experiment is neither gitignored nor tracked, so it would
        # show up as untracked forever and the Stop guard would scan every staged file.
        if not ctx.keep_experiment:
            shutil.rmtree(exp_dir, ignore_errors=True)
        after = _git_dirty()
        leaked = sorted(p for p in (after - ctx.git_baseline) if '/experiments/' in p.replace('\\', '/'))
        ev['leaked_paths'] = leaked[:10]
        if leaked and not ctx.keep_experiment:
            r.status = 'fail'
            msgs.append(f'{len(leaked)} staged path(s) survived cleanup: {leaked[:3]}')
    return r


# ── stage 5: the site still builds ──────────────────────────────────────────────────────────
def _stage_mkdocs(ctx: _Ctx) -> StageResult:
    """Same command the deploy workflow runs. Built into a temp dir so a local site/ is untouched."""
    r = StageResult('mkdocs')
    if not _importable('mkdocs'):
        r.status = 'skip'
        r.messages.append('mkdocs not installed (pip install -r requirements-docs.txt)')
        return r
    import tempfile
    out_dir = tempfile.mkdtemp(prefix='smoke_site_')
    try:
        rc, out = _sh(_py('-m', 'mkdocs', 'build', '--strict', '--site-dir', out_dir), timeout=1800)
        r.evidence['returncode'] = rc
        if rc != 0:
            r.status = 'fail'
            r.messages.append('mkdocs build --strict failed')
            r.messages.append(out.strip()[-500:])
            return r
        r.status = 'pass'
        r.messages.append('site builds strictly')
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
    return r


# ── stage 6: archive a completed cell and prove the tree survives it ────────────────────────
def _stage_archive(ctx: _Ctx) -> StageResult:
    """Move one completed cell to bulk storage, then re-verify the whole run through the junction.

    This is the only check that matters for the archiver: the point of leaving a junction is that
    NOTHING downstream can tell the difference. So the test is not "did the copy succeed" — it is
    "does the same contract verification that passed before still pass after", with the bytes on a
    different physical drive.
    """
    r = StageResult('archive')
    ev, msgs = r.evidence, r.messages
    sys.path.insert(0, os.path.join(_ROOT, 'scripts'))
    import archive_cells as ac
    from Optimization import runschema

    cold = ac.cold_root()
    ev['cold_configured'] = bool(cold)
    if not cold or not os.path.isdir(cold):
        r.status = 'skip'
        msgs.append('COLD_DRIVE not set or not connected')
        return r

    can_link = ac.supports_reparse(ctx.run_dir)
    ev['hot_supports_junction'] = can_link
    mode = 'cell' if can_link else 'keyframes'
    ev['mode'] = mode
    msgs.append(f'hot volume {"supports" if can_link else "does NOT support"} junctions -> mode {mode}')

    rt = runschema.resolver_for(ctx.run_dir)
    before_dbs = len(list(rt.sim_dbs()))
    # Free space on the VOLUME, not a walk of the tree: after the swap the junction is transparent,
    # so walking the run directory still sees every file and would report nothing was moved.
    before_free = shutil.disk_usage(ctx.run_dir).free
    # First cell not already relocated. Re-running the stage on a run whose cells are all archived
    # is a no-op, not a failure — otherwise a second invocation reports a problem that isn't one.
    cells = [n for n, _d in rt.cells()]
    target = next((c for c in cells if not ac.is_archived(rt.cell_dir(c))), None)
    ev['already_archived'] = [c for c in cells if ac.is_archived(rt.cell_dir(c))]
    if target is None:
        r.status = 'skip'
        msgs.append(f'every cell is already archived ({len(cells)}) — nothing to do')
        return r

    t0 = time.time()
    done = ac.sweep(ctx.run_dir, cold=cold, workers=ctx.workers, analyse=False,
                    dry_run=False, include_last=True, only=[target], mode=mode,
                    echo=lambda m: None)
    ev['archive_result'] = done
    if not done or done[0].get('status') != 'archived':
        r.status = 'fail'
        msgs.append(f'archive did not complete: {done}')
        return r

    after_free = shutil.disk_usage(ctx.run_dir).free
    ev['freed_gb'] = round((after_free - before_free) / (1024 ** 3), 2)
    ev['seconds'] = round(time.time() - t0, 1)
    msgs.append(f'{target}: {ev["freed_gb"]:.2f} GB moved off the hot drive in {ev["seconds"]:.0f}s')

    # The transparency proof. Re-resolve from scratch — a cached resolver would hide a broken path.
    rt2 = runschema.resolver_for(ctx.run_dir)
    after_dbs = len(list(rt2.sim_dbs()))
    ev['sim_dbs_before'] = before_dbs
    ev['sim_dbs_after'] = after_dbs
    if mode == 'cell':
        if after_dbs != before_dbs:
            r.status = 'fail'
            msgs.append(f'sim DBs visible dropped {before_dbs} -> {after_dbs}: the junction is not '
                        f'transparent, and a resume would re-simulate this cell')
            return r
        readable = 0
        for _c, _cr, db in rt2.sim_dbs(target):
            if os.path.isfile(db):
                readable += 1
        ev['readable_through_junction'] = readable
        if readable != before_dbs // max(len(cells), 1):
            msgs.append(f'only {readable} DBs readable through the junction')
    else:
        ev['note'] = 'keyframes mode: sim DBs stay in place by design'

    # Re-run the full contract verification against the now-relocated tree.
    sub = _stage_verify_tree(ctx)
    ev['verify_after_archive'] = sub.status
    ev['verify_messages'] = sub.messages[:4]
    if sub.status != 'pass':
        r.status = 'fail'
        msgs.append('contract verification FAILED after archiving — ' + '; '.join(sub.messages[:2]))
        return r

    msgs.append('contract still verifies with the cell on bulk storage')
    r.status = 'pass'
    return r


def _tree_gb(root: str) -> float:
    tot = 0
    for dirpath, _d, files in os.walk(root):
        for fn in files:
            try:
                tot += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return tot / (1024 ** 3)


# ── driver ──────────────────────────────────────────────────────────────────────────────────
_STAGE_FNS = {
    'preconditions': _stage_preconditions,
    'simulate': _stage_simulate,
    'analyze': _stage_analyze,
    'verify_tree': _stage_verify_tree,
    'website': _stage_website,
    'mkdocs': _stage_mkdocs,
    'archive': _stage_archive,
}


def run(profile: str = 'smoke', *, workers: int = WORKER_CAP, stages=None,
        reuse_run: str | None = None, exp: str = '_smoketest',
        keep_experiment: bool = False, echo=print) -> SmokeResult:
    if profile not in PROFILES:
        raise ValueError(f'unknown profile {profile!r}; choices: {sorted(PROFILES)}')
    prof = PROFILES[profile]
    workers = max(1, min(int(workers), WORKER_CAP, os.cpu_count() or 1))
    wanted = list(stages) if stages else list(STAGES)
    bad = [s for s in wanted if s not in _STAGE_FNS]
    if bad:
        raise ValueError(f'unknown stage(s) {bad}; choices: {list(STAGES)}')

    ctx = _Ctx(prof, workers, echo, exp=exp, keep_experiment=keep_experiment)
    ctx.run_dir = reuse_run
    if reuse_run and 'preconditions' not in wanted:
        ctx.git_baseline = _git_dirty()        # stage 4 diffs against this
    res = SmokeResult(profile=profile, workers=workers)
    t0 = time.time()

    for name in wanted:
        if name in ('analyze', 'verify_tree', 'website', 'archive') and not ctx.run_dir:
            sr = StageResult(name, status='skip')
            sr.messages.append('no run directory (pass --reuse-run or include the simulate stage)')
            res.stages[name] = sr
            echo(f'  [skip] {name}: no run directory')
            continue
        echo(f'== {name} ==')
        s0 = time.time()
        try:
            sr = _STAGE_FNS[name](ctx)
        except Exception as exc:                        # a stage crash is a stage failure
            sr = StageResult(name, status='fail')
            sr.messages.append(f'stage raised: {exc!r}')
        if not sr.seconds:
            sr.seconds = time.time() - s0
        res.stages[name] = sr
        for m in sr.messages:
            echo(f'  {m}')
        echo(f'  -> {sr.status.upper()} ({sr.seconds:.1f}s)')
        if sr.status == 'fail' and name in ('preconditions', 'simulate'):
            break                                       # later stages cannot mean anything

    res.seconds = time.time() - t0
    res.run_dir = ctx.run_dir
    vt = res.stages.get('verify_tree')
    res.schema_short = (vt.evidence.get('schema_short') if vt else None)
    res.ok = all(s.status in ('pass', 'warn', 'skip') for s in res.stages.values())
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='Pipeline smoketest: simulate, analyse, verify the run tree against the contract.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument('--profile', default='smoke', choices=sorted(PROFILES))
    ap.add_argument('--workers', type=int, default=WORKER_CAP,
                    help=f'clamped to {WORKER_CAP} and to the CPU count')
    ap.add_argument('--stages', default=None, help='comma-separated subset of: ' + ','.join(STAGES))
    ap.add_argument('--reuse-run', default=None, metavar='DIR',
                    help='verify an existing run instead of simulating')
    ap.add_argument('--exp', default='_smoketest', metavar='NAME',
                    help='disposable docs experiment name used by the website stage')
    ap.add_argument('--keep-experiment', action='store_true',
                    help='do not delete the staged docs experiment (leaves untracked files)')
    ap.add_argument('--json', default=None, metavar='PATH',
                    help="write the result as JSON ('-' for stdout)")
    a = ap.parse_args(argv)

    res = run(a.profile, workers=a.workers,
              stages=[s.strip() for s in a.stages.split(',')] if a.stages else None,
              reuse_run=a.reuse_run, exp=a.exp, keep_experiment=a.keep_experiment)

    print(f'\n{"OK" if res.ok else "FAILED"}  profile={res.profile} workers={res.workers} '
          f'{res.seconds / 60:.1f} min')
    if res.failed():
        print('  failed stages: ' + ', '.join(res.failed()))
    if a.json:
        blob = json.dumps(res.as_dict(), indent=2)
        if a.json == '-':
            print(blob)
        else:
            with open(a.json, 'w', encoding='utf-8') as fh:
                fh.write(blob + '\n')
    return 0 if res.ok else 1


if __name__ == '__main__':
    sys.exit(main())
