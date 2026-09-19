"""test_config_reaches_the_worker.py — the FIFTH SEAM, guarded generically instead of by knob.

# ── the defect this replaces ──────────────────────────────────────────────────────

`settings.py` names four places a new setting must reach: declared here, threaded into
`CONFIG`, given a CLI flag, recorded in the run spec and restored on resume/re-analysis.
There is a FIFTH it does not name — `workunits._shared`, the picklable payload a spawned
worker actually receives.  A worker re-imports nothing of the parent's mutated state, so a
setting missing from that payload is accepted on the command line and then silently ignored
for the whole run.  Every number the run produces is then a number for a configuration
nobody asked for, and nothing anywhere raises.

That seam has been guarded SIX TIMES, once per knob, each time by a fresh
`inspect.getsource(workunits)` substring assertion in a fresh file — `test_run_shaping_params`,
`test_receiving_params`, `test_inbound_params`, `test_couple_channels_param`,
`test_staffing_params`, `test_per_item_charge`.  Those checks are worth keeping: each says
something specific about its own knob.  What none of them says is anything about the knob
that has not been written yet, and a substring check cannot: it can only be added after
someone has already remembered the seam exists.

This file makes the statements that hold for EVERY knob, including the ones not yet written.

# ── the three generic statements ──────────────────────────────────────────────────

1. **A worker cannot read CONFIG at all.**  `strategy_runner` — the worker entry point and
   everything reachable from it — must not import `Optimization.config.sim_config`.  This is
   the invariant that makes the seam safe rather than merely tested: if the worker cannot
   reach CONFIG, every value it uses HAS to have arrived in the payload, and a knob that
   never made it produces a missing key rather than a plausible wrong default.

2. **Every knob BUNDLE is threaded.**  A `*_spec()` accessor in `sim_config` exists to hand
   a group of knobs across the process boundary as one picklable record.  Each one is either
   called when `_shared` is built, or named in `PARENT_ONLY` below with the reason it is not.

3. **No accessor snapshots CONFIG at import.**  The run-shaping values are functions rather
   than module scalars precisely so a CLI override is visible; a snapshot cannot see one.
   This project has shipped that defect (`_INITIAL_FILL`, which made a run misreport its own
   sizing in its warehouse DB) and caught five more before they shipped.  So every accessor
   is CALLED, its CONFIG key mutated, and called again — it must move.
"""
import ast
import importlib
import inspect
import io
import os
import pickle
import re
import subprocess
import sys
import textwrap

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
#: NO sys.path bootstrap here: `Tests/conftest.py` puts the repo root on the path for
#: the whole suite, and CLAUDE.md names it and entry-script bootstraps as the only
#: legal `sys.path.insert` sites.

from Optimization.config import sim_config as sc


#: Accessors that legitimately never reach a worker, and why.  An accessor absent from the
#: payload AND from this table is the defect — so adding a new one forces the choice to be
#: made and written down rather than defaulted into.
PARENT_ONLY = {
    'inbound_lead_law':
        'consumed at SETUP by sim_assets.build_shared_assets to build the coverage record; '
        'the worker receives the derived record inside `staffing`, never the law itself',
    'regime_sizing_from_config':
        'a PARENT-side shape: run_analysis, run_map_precompute and simdriver.scenario size '
        'the warehouse with it before any worker exists',
    'aisle_geometry':
        'a PARENT-side shape, for the same reason as regime_sizing_from_config: the aisle '
        'width/height are consumed by sim_assets.plan_warehouse and run_simulation'
        "'s structural floor check, both of which run before a worker exists. The worker "
        'receives the BUILT warehouse, never the geometry that shaped it',
}

#: Accessors that are STRUCTURALLY OFF under the pristine CONFIG and answer `None` whatever
#: their own knobs say — a feature that has not been switched on.  The freshness check below
#: cannot say anything about an accessor stuck on its `return None` line, so it turns the
#: gate on first.  The gate is the knob whose absence makes the feature not exist at all,
#: and each one is a documented refusal in the accessor's own docstring.
GATE_ON = {
    'inbound_lead_law':  {'inbound_trailer_type': '53'},
    'inbound_spec':      {'inbound_trailer_type': '53', 'recv_crew_size': 2},
    'put_queues_spec':   {'put_queue_split': True},
    'recv_crew_spec':    {'recv_crew_size': 2},
}

#: The worker entry module.  Anything it imports is, by definition, in a spawned worker.
_WORKER_MODULE = 'Optimization.simdriver.strategy_runner'

#: What a worker must never be able to reach.
_FORBIDDEN = 'Optimization.config.sim_config'


def _spec_accessors():
    """Every `*_spec()`-shaped accessor `sim_config` exposes, found in its source.

    Read from the SOURCE rather than from `dir(sc)`, so a name that is imported into the
    module rather than defined by it cannot masquerade as one of its accessors.
    """
    src = inspect.getsource(sc)
    names = re.findall(
        r'^def (\w+_spec|inbound_lead_law|regime_sizing_from_config|aisle_geometry)\(',
        src, re.M)
    return sorted(set(names))


@pytest.fixture()
def pristine_config():
    """`CONFIG` is a module-level dict mutated IN PLACE and shared by the whole session.

    Every test here that bumps a key must put it back however it exits — not only on the
    happy path.  The first version of this file restored the gate keys with a single
    `update` after the assertions, so a failing assertion left `inbound_trailer_type='53'`
    and `recv_crew_size=2` standing for every test that ran afterwards, and because the
    freshness check is parametrized, one failing accessor poisoned the remaining params too.

    That is the exact landmine this effort fixed in `test_inbound_params`'s own `restore`
    fixture two commits earlier — worth stating plainly, because writing the bug back in
    while fixing it elsewhere is how it survives.  The repo's convention is uniform: every
    sibling that touches `CONFIG['global']` unwinds through a fixture or a `try/finally`.

    Restores the CHANNEL blocks too, not just `global`: the freshness check bumps keys in
    `CONFIG['channels'][*]` as well, which is how `regime_sizing_from_config` is reached.
    """
    g_before = dict(sc.CONFIG['global'])
    ch_before = {k: dict(v) for k, v in sc.CONFIG.get('channels', {}).items()
                 if isinstance(v, dict)}
    try:
        yield sc.CONFIG
    finally:
        g = sc.CONFIG['global']
        g.clear()
        g.update(g_before)
        for k, saved in ch_before.items():
            blk = sc.CONFIG['channels'][k]
            blk.clear()
            blk.update(saved)


_SKIP = object()


def _bump(cur):
    """A value of the SAME KIND but a different one, or `_SKIP` if there isn't one.

    `None` is skipped deliberately.  It is a legitimate configured value across this CONFIG
    — "no whistle", "an unbounded floor", "take the pick crew's mode" — and it carries no
    type, so any replacement invents one.  Bumping `put_crew_mode` from None to 1 does not
    test that an accessor reads CONFIG at call time; it tests that it validates its input,
    which it does, by raising.
    """
    if isinstance(cur, bool):
        return not cur
    if isinstance(cur, int):
        return cur + 7
    if isinstance(cur, float):
        return cur + 7.0
    if isinstance(cur, str):
        return cur + '_x'
    return _SKIP


def _config_sections():
    yield sc.CONFIG['global']
    for block in sc.CONFIG.get('channels', {}).values():
        if isinstance(block, dict):
            yield block


def _freshness_evidence(fn, src):
    """Bump each CONFIG key `src` names, one at a time; return (tried, evidence).

    MODULE-LEVEL, and that is the point: the sabotage test calls THIS function, not a
    simplified re-implementation of it.  A sabotage that reimplements the logic proves the
    reimplementation works.

    ONE KEY AT A TIME, because bumping every key an accessor names builds CONTRADICTORY
    configurations — `inbound_standing_yard=True` while `inbound_trailer_type` is None is a
    combination `inbound_spec` correctly refuses — and the check would then be measuring
    validation rather than freshness.

    TWO THINGS COUNT AS EVIDENCE, and a raise is one of them: the output MOVED, or the
    accessor RAISED (it looked at the key and refused the new value).  A snapshot taken at
    import could not have noticed the change at all, so a refusal proves a live read just as
    a different answer does.

    Restores each key it bumps; the CALLER still needs `pristine_config`, because the gate
    keys applied before this runs are not this function's to unwind.
    """
    try:
        before = fn()
    except Exception:                                 # noqa: BLE001
        before = _SKIP                                # a raise on every bump is still evidence

    tried, evidence = [], []
    for block in _config_sections():
        for key in list(block):
            if f"'{key}'" not in src and f'"{key}"' not in src:
                continue
            new = _bump(block[key])
            if new is _SKIP:
                continue
            tried.append(key)
            old = block[key]
            block[key] = new
            try:
                moved = fn() != before
            except Exception:                         # noqa: BLE001 — a refusal IS a live read
                moved = True
            finally:
                block[key] = old
            if moved:
                evidence.append(key)
    return tried, evidence


# ── 1. the worker cannot read CONFIG ──────────────────────────────────────────────

def test_the_worker_module_cannot_reach_sim_config():
    """THE STRUCTURAL GUARANTEE, checked in a FRESH interpreter.

    In-process this would be meaningless: pytest has already imported half the repo, so
    `sim_config` is in `sys.modules` whatever `strategy_runner` does.  A subprocess that
    imports only the worker module answers the real question — can a spawned worker reach
    CONFIG?  If it cannot, no knob can silently fall back to a default, because there is no
    default there to fall back to.
    """
    probe = (
        'import sys;'
        f'import {_WORKER_MODULE};'
        f'hit=[m for m in sys.modules if m=={_FORBIDDEN!r}];'
        'print("LEAK" if hit else "CLEAN")'
    )
    out = subprocess.run([sys.executable, '-c', probe], cwd=_ROOT,
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, f'the probe failed to import the worker:\n{out.stderr}'
    assert out.stdout.strip().endswith('CLEAN'), (
        f'{_WORKER_MODULE} now reaches {_FORBIDDEN} transitively.  A spawned worker that '
        f'can read CONFIG reads PRISTINE DEFAULTS, so any knob taken from there is silently '
        f'wrong for the whole run.  Carry the value in `workunits._shared` instead.\n'
        f'{out.stdout}')


def test_the_probe_would_notice_a_leak():
    """SABOTAGE: the probe above proves nothing unless importing sim_config makes it say LEAK."""
    probe = (
        'import sys;'
        f'import {_FORBIDDEN};'
        f'import {_WORKER_MODULE};'
        f'hit=[m for m in sys.modules if m=={_FORBIDDEN!r}];'
        'print("LEAK" if hit else "CLEAN")'
    )
    out = subprocess.run([sys.executable, '-c', probe], cwd=_ROOT,
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith('LEAK'), \
        'the leak probe cannot detect a leak, so the test above is vacuous'


@pytest.mark.parametrize('pkg', ['Warehouse', 'Inbound'])
def test_the_domain_packages_never_read_config(pkg):
    """The documented import law, checked rather than trusted.

    `Inbound/README.md` says policy/spec selection is the driver's job and this package
    receives constructed objects; `Warehouse/` is the domain engine and the harness is
    `Optimization/`.  A CONFIG read anywhere under either is the same defect from the other
    end — a value resolved where the override cannot be seen.

    THREE SPELLINGS, because a guard that knows only one is a guard with a hole. This repo
    writes all three, and the middle one is the COMMONEST — `Optimization/simconfig/
    constants.py` and several others import the module rather than reaching through it:

        from Optimization.config.sim_config import CONFIG     # dotted-path form
        from Optimization.config import sim_config            # module form  <-- was missed
        import Optimization.config.sim_config                 # plain import

    Parsed as an AST rather than matched as text, so a mention inside a docstring, a comment
    or a string literal is not an offender and a real import inside a function still is —
    several modules in this repo import lazily inside a function body on purpose.
    """
    offenders = []
    for dp, dn, fn in os.walk(os.path.join(_ROOT, pkg)):
        if '__pycache__' in dp:
            continue
        for f in fn:
            if not f.endswith('.py'):
                continue
            p = os.path.join(dp, f)
            body = io.open(p, encoding='utf-8', errors='replace').read()
            try:
                tree = ast.parse(body)
            except SyntaxError:                      # not ours to police here
                continue
            hit = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    hit |= any(a.name == 'Optimization.config.sim_config'
                               or a.name.startswith('Optimization.config.sim_config.')
                               for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    # `from Optimization.config.sim_config import X`
                    hit |= node.module == 'Optimization.config.sim_config'
                    # `from Optimization.config import sim_config`
                    hit |= (node.module == 'Optimization.config'
                            and any(a.name == 'sim_config' for a in node.names))
            if hit:
                offenders.append(os.path.relpath(p, _ROOT).replace(os.sep, '/'))
    assert not offenders, (
        f'{pkg}/ reads sim_config: {offenders}.  The driver constructs from CONFIG and '
        f'injects; the domain receives constructed objects.')


def test_the_domain_guard_catches_all_three_import_spellings(tmp_path):
    """SABOTAGE for the guard above: each spelling must be detected, not just the first.

    The dotted-path form was the only one the first version of this test recognised, and the
    module form — the one this repo actually writes most often — walked straight past it.
    """
    forms = [
        'from Optimization.config.sim_config import CONFIG',
        'from Optimization.config import sim_config',
        'import Optimization.config.sim_config',
    ]
    innocent = [
        '# from Optimization.config import sim_config  (explaining why we do NOT)',
        's = "from Optimization.config import sim_config"',
        'from Optimization.config import settings',
    ]

    def detects(src):
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(a.name == 'Optimization.config.sim_config'
                       or a.name.startswith('Optimization.config.sim_config.')
                       for a in node.names):
                    return True
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == 'Optimization.config.sim_config':
                    return True
                if (node.module == 'Optimization.config'
                        and any(a.name == 'sim_config' for a in node.names)):
                    return True
        return False

    for src in forms:
        assert detects(src), f'the guard does not detect: {src!r}'
    for src in innocent:
        assert not detects(src), f'the guard false-positives on: {src!r}'


# ── 1b. ...and cannot reach it from INSIDE a function either ──────────────────────
# The subprocess probe above imports the worker MODULE, so it sees only module-level
# imports.  `_build_arm` imported `load_run_inventory` from `sim_assets` inside its body,
# and `sim_assets` imports CONFIG at module level -- so every spawned worker DID import
# sim_config at run time, for months, with the probe above green (found 2026-09-19 while
# the flat work pool was built on the invariant this file advertises).  The walk below
# resolves every function-body import of the worker ONE level and refuses one whose module
# imports the forbidden module at ITS module level.

_REPO_PREFIXES = ('Optimization.', 'Warehouse.', 'Inbound.', 'Schema.')


def _function_body_imports(src: str) -> set:
    """Module names imported INSIDE function bodies of `src` (absolute names only)."""
    found = set()
    for fn in ast.walk(ast.parse(src)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                found.add(node.module)
            elif isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
    return found


def _module_level_imports(module_name: str) -> set:
    """Module names a repo module imports at ITS module level, from its source."""
    if not module_name.startswith(_REPO_PREFIXES):
        return set()
    spec = importlib.util.find_spec(module_name)
    if spec is None or not spec.origin or not spec.origin.endswith('.py'):
        return set()
    with open(spec.origin, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    found = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
    return found


def _function_body_leaks(src: str) -> set:
    """The function-body imports of `src` that reach the forbidden module in one hop."""
    return {m for m in _function_body_imports(src)
            if m == _FORBIDDEN or _FORBIDDEN in _module_level_imports(m)}


def test_no_function_body_import_in_the_worker_reaches_sim_config():
    src = inspect.getsource(importlib.import_module(_WORKER_MODULE))
    leaks = _function_body_leaks(src)
    assert not leaks, (
        f'{_WORKER_MODULE} imports {sorted(leaks)} inside a function body, and that module '
        f'imports {_FORBIDDEN} at module level -- so a spawned worker reaches CONFIG at run '
        f'time and the module-level probe cannot see it.  Import the symbol from a module '
        f'that does not import CONFIG (`load_run_inventory` moved to '
        f'`Warehouse.generation.generate_inventory` for exactly this).')


def test_the_function_body_walk_would_notice_a_leak():
    """SABOTAGE: `sim_assets` imports CONFIG at module level, so a function importing from
    it must be flagged -- and one importing from a CONFIG-free module must not."""
    leaking = ('def f():\n'
               '    from Optimization.simdriver.sim_assets import load_run_inventory\n')
    assert _function_body_leaks(leaking) == {'Optimization.simdriver.sim_assets'}
    clean = ('def f():\n'
             '    from Warehouse.generation.generate_inventory import load_run_inventory\n'
             '    import os\n')
    assert _function_body_leaks(clean) == set()


# ── 2. every knob bundle is threaded into the payload ─────────────────────────────
def _accessors_reaching_the_payload(src: str) -> set:
    """Which `*_spec()` accessors reach `workunits._shared`, read off the payload EXPRESSION.

    THE ONE FUNCTION BOTH THE CHECK AND ITS SABOTAGE CALL. Written once, deliberately: a
    sabotage test that reimplements a simplified copy of the logic proves the copy works,
    not the shipped check — the same "recomputes it the way the code does" trap this file
    exists to avoid.

    The first version of the check searched the whole of `inspect.getsource(workunits)` for
    `<name>(`, which is exactly the sin this file's docstring accuses the six per-knob checks
    of. `put_queues_spec` appears on the module's import line as well as in the payload, so
    an accessor that was imported and then NOT threaded would have passed; so would one
    called in a parent-only helper, or merely named in a comment.

    ONE LEVEL OF INDIRECTION is resolved, because the payload legitimately uses locals:
    `staffing` is built as `_staffing_payload = ({'inputs': staffing_spec(), **_st} if _st
    else staffing_spec())` and only then handed to `_shared`. The accessor does reach the
    worker, just not on the same line, and refusing that would demand a style the code has a
    reason not to use. Two levels is NOT resolved: nothing needs it today, and an unbounded
    chase would drift back toward "somewhere in the module".

    Raises if no `_shared = ...` assignment exists, rather than returning an empty set that
    would read as "nothing is threaded" and fail every accessor at once.
    """
    tree = ast.parse(src)

    owner, payload = None, None
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == '_shared' for t in node.targets):
                owner, payload = fn, node.value
                break
        if payload is not None:
            break
    if payload is None:
        raise AssertionError(
            'no `_shared = ...` assignment found — the worker payload was renamed or '
            'restructured, and this check is now checking nothing. Retarget it.')

    def _calls(node):
        return {n.func.id for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}

    local_calls = {}
    for node in ast.walk(owner):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    local_calls.setdefault(t.id, set()).update(_calls(node.value))

    reaching = set(_calls(payload))
    for n in ast.walk(payload):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            reaching |= local_calls.get(n.id, set())
    return reaching


def test_every_spec_accessor_is_either_in_the_payload_or_declared_parent_only():
    """The generic form of six hand-written per-knob substring checks.

    A `*_spec()` exists to move a GROUP of knobs across the process boundary as one record.
    A new one that nobody threads is the fifth-seam defect with a larger blast radius than a
    single setting, because a whole bundle defaults at once.
    """
    from Optimization.simdriver import workunits
    reaching = _accessors_reaching_the_payload(inspect.getsource(workunits))

    missing = [name for name in _spec_accessors()
               if name not in PARENT_ONLY and name not in reaching]
    assert not missing, (
        f'{missing} are spec accessors that never reach `workunits._shared`.  Either thread '
        f'them into the payload, or add them to PARENT_ONLY in this file with the reason a '
        f'worker does not need them.')


def test_the_payload_check_reads_the_payload_and_not_the_whole_module():
    """SABOTAGE, through the SHIPPED function: an accessor merely imported and called
    elsewhere must not count as threaded, while one reached through a local must.

    Both halves matter. Only the first would let the check tighten into uselessness (a check
    that finds nothing threaded passes nothing); only the second would let it loosen back
    into the whole-module match it replaced.
    """
    src = textwrap.dedent('''
        from Optimization.config.sim_config import (
            brand_new_spec, put_crew_spec, staffing_spec)

        def _prepare():
            _unrelated = brand_new_spec()       # called, but never reaches the payload
            _staffing = {"inputs": staffing_spec()}
            _shared = dict(inv_db=1, put_crew=put_crew_spec(), staffing=_staffing)
            return _shared
    ''')
    reaching = _accessors_reaching_the_payload(src)

    assert 'put_crew_spec' in reaching, 'a direct payload call must be seen'
    assert 'staffing_spec' in reaching, 'a call reached through one local must be seen'
    assert 'brand_new_spec' not in reaching, (
        'an accessor imported and called OUTSIDE the payload was counted as threaded — the '
        'check is reading the module again instead of the payload expression')
    assert 'brand_new_spec(' in src, (
        'the sabotage source must contain the substring the OLD check matched on, or this '
        'test does not demonstrate the difference between the two approaches')


def test_the_payload_check_refuses_a_module_with_no_payload():
    """A renamed `_shared` must RAISE, not quietly return an empty set.

    An empty set would make every accessor "missing" and the failure would read as a
    threading bug in sim_config rather than a retarget-this-test bug here.
    """
    with pytest.raises(AssertionError, match='_shared'):
        _accessors_reaching_the_payload(textwrap.dedent('''
            def _prepare():
                payload = dict(a=1)
        '''))



def test_parent_only_names_real_accessors_with_real_reasons():
    """An exemption list rots into a suppression list unless its entries are checked."""
    known = set(_spec_accessors())
    stale = sorted(set(PARENT_ONLY) - known)
    assert not stale, (
        f'PARENT_ONLY exempts {stale}, which sim_config no longer defines — an exemption '
        f'for a deleted accessor silently exempts nothing and hides the next one.')
    for name, why in PARENT_ONLY.items():
        assert len(why) > 40, f'{name} is exempted without a real reason'


def test_the_payload_is_picklable_because_a_worker_is_spawned_not_forked(pristine_config):
    """Every spec a worker receives must survive pickling, or the pool dies at submit.

    Spawn, not fork (CLAUDE.md): the payload crosses a real process boundary.  A spec that
    returned a closure, a logger or a live manager would pass every other test here and then
    fail at the one place that is hardest to debug.

    GATED ON FIRST, because three of the seven accessors this covers — `inbound_spec`,
    `put_queues_spec`, `recv_crew_spec` — answer `None` under the pristine CONFIG, their
    feature being structurally off.  The first version of this test pickled that `None` and
    reported success: `pickle.dumps(None)` proves nothing about the record those accessors
    return when their feature is ON, which is the only time a worker receives one.  Asserted
    non-None per accessor so the test cannot quietly go back to pickling nothing.
    """
    checked = 0
    for name in _spec_accessors():
        if name in PARENT_ONLY:
            continue
        sc.CONFIG['global'].update(GATE_ON.get(name, {}))
        fn = getattr(sc, name)
        value = fn()
        assert value is not None, (
            f'{name}() is None even with GATE_ON={GATE_ON.get(name)} applied — pickling it '
            f'would prove nothing. Either its gate moved, or it needs a GATE_ON entry.')
        try:
            back = pickle.loads(pickle.dumps(value))
        except Exception as exc:                     # noqa: BLE001 — the message is the point
            pytest.fail(f'{name}() returned something unpicklable ({exc}); a spawned worker '
                        f'cannot receive it')
        assert back == value, (
            f'{name}() does not survive a pickle round-trip unchanged; a worker would run '
            f'against a different record than the parent built')
        checked += 1
    assert checked >= 5, f'only {checked} accessors were pickled — the query missed some'


# ── 3. no accessor snapshots CONFIG at import ─────────────────────────────────────

@pytest.mark.parametrize('name', [n for n in _spec_accessors()])
def test_the_accessor_reads_config_at_call_time(name, pristine_config):
    """A value snapshotted at import cannot see a CLI override — the `_INITIAL_FILL` defect.

    ONE KEY AT A TIME, and here is why that matters.  The obvious form — bump every CONFIG
    key the accessor names, then check the output moved — builds CONTRADICTORY
    configurations: flipping `inbound_standing_yard` to True while `inbound_trailer_type` is
    still None is a combination `inbound_spec` correctly refuses ("a standing yard with no
    trailers is a config contradiction").  The test would then be measuring validation, not
    freshness.

    TWO THINGS COUNT AS EVIDENCE that a key is read at call time, and a raise is one of them:

      * the output MOVED — the ordinary case; or
      * the accessor RAISED — it looked at the key and refused the new value.  A snapshot
        taken at import could not have noticed the change at all, so a refusal is proof of a
        live read just as a different answer is.

    A key that produces neither is not evidence either way (it may be named only in a
    comment or a docstring), so the requirement is that AT LEAST ONE key produces evidence.
    """
    fn = getattr(sc, name)
    src = inspect.getsource(fn)

    sc.CONFIG['global'].update(GATE_ON.get(name, {}))
    before = fn()
    assert before is not None, (
        f'{name}() is still None with GATE_ON={GATE_ON.get(name)} applied; the gate moved, '
        f'so this test is measuring a `return None` line')

    tried, evidence = _freshness_evidence(fn, src)

    assert tried, (
        f'{name} names no mutable CONFIG key this test can bump, so it proves nothing about '
        f'{name}.  Point it at the right section rather than letting it pass empty.')
    assert evidence, (
        f'{name}() returned the same record after each of {tried} was changed in CONFIG, and '
        f'refused none of them.  Either it snapshotted at import — in which case a CLI '
        f'override is accepted and silently ignored — or it does not read those keys and '
        f'this test is pointed at the wrong ones.')


def test_the_call_time_check_would_catch_a_snapshot(pristine_config):
    """SABOTAGE — and it RUNS THE CHECK, which the first version of this test did not.

    That version built a frozen stand-in and then asserted only that the stand-in did not
    move.  That is true by construction: a function returning `snapshot[key]` cannot move,
    whatever `_freshness_evidence` does.  It proved the stub was a stub, never that the
    check would catch one — the tautological assertion this repo's own test rules name.

    So this calls the SAME `_freshness_evidence` the real test calls, on a stand-in that
    reads a snapshot taken at import, and requires it to find no evidence.  Both halves are
    asserted: the frozen accessor yields nothing (or the check is blind), and a live one
    reading the same key yields something (or the check is broken in the other direction and
    would pass anything).
    """
    snapshot = dict(sc.CONFIG['global'])            # taken NOW, never re-read

    def frozen_spec():
        """The defect: CONFIG read once at import."""
        return {'put_crew_size': snapshot['put_crew_size']}

    def live_spec():
        """The correct shape: CONFIG read at call time."""
        return {'put_crew_size': sc.CONFIG['global']['put_crew_size']}

    src = "reads 'put_crew_size' at call time"      # the key-name source both share

    frozen_tried, frozen_evidence = _freshness_evidence(frozen_spec, src)
    live_tried, live_evidence = _freshness_evidence(live_spec, src)

    assert frozen_tried and live_tried, (
        'neither stand-in had a bumpable key, so this test compared nothing')
    assert not frozen_evidence, (
        f'_freshness_evidence found {frozen_evidence} for an accessor that reads a snapshot '
        f'taken at import — the check cannot tell a snapshot from a live read, so the real '
        f'test above proves nothing')
    assert live_evidence, (
        '_freshness_evidence found nothing for an accessor that DOES read CONFIG at call '
        'time — the check is blind in both directions and would pass any implementation')
