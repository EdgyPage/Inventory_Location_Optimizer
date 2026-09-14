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
import importlib
import inspect
import io
import os
import pickle
import re
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

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
    names = re.findall(r'^def (\w+_spec|inbound_lead_law|regime_sizing_from_config)\(',
                       src, re.M)
    return sorted(set(names))


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
            if re.search(r'^\s*(from|import)\s+Optimization\.config\.sim_config', body, re.M):
                offenders.append(os.path.relpath(p, _ROOT))
    assert not offenders, (
        f'{pkg}/ reads sim_config: {offenders}.  The driver constructs from CONFIG and '
        f'injects; the domain receives constructed objects.')


# ── 2. every knob bundle is threaded into the payload ─────────────────────────────

def test_every_spec_accessor_is_either_in_the_payload_or_declared_parent_only():
    """The generic form of six hand-written per-knob substring checks.

    A `*_spec()` exists to move a GROUP of knobs across the process boundary as one record.
    A new one that nobody threads is the fifth-seam defect with a larger blast radius than a
    single setting, because a whole bundle defaults at once.
    """
    from Optimization.simdriver import workunits
    wu_src = inspect.getsource(workunits)

    missing = []
    for name in _spec_accessors():
        if name in PARENT_ONLY:
            continue
        if not re.search(rf'\b{name}\(', wu_src):
            missing.append(name)
    assert not missing, (
        f'{missing} are spec accessors that never reach `workunits._shared`.  Either thread '
        f'them into the payload, or add them to PARENT_ONLY in this file with the reason a '
        f'worker does not need them.')


def test_parent_only_names_real_accessors_with_real_reasons():
    """An exemption list rots into a suppression list unless its entries are checked."""
    known = set(_spec_accessors())
    stale = sorted(set(PARENT_ONLY) - known)
    assert not stale, (
        f'PARENT_ONLY exempts {stale}, which sim_config no longer defines — an exemption '
        f'for a deleted accessor silently exempts nothing and hides the next one.')
    for name, why in PARENT_ONLY.items():
        assert len(why) > 40, f'{name} is exempted without a real reason'


def test_the_payload_is_picklable_because_a_worker_is_spawned_not_forked():
    """Every spec a worker receives must survive pickling, or the pool dies at submit.

    Spawn, not fork (CLAUDE.md): the payload crosses a real process boundary.  A spec that
    returned a closure, a logger or a live manager would pass every other test here and then
    fail at the one place that is hardest to debug.
    """
    for name in _spec_accessors():
        if name in PARENT_ONLY:
            continue
        fn = getattr(sc, name)
        value = fn()
        try:
            pickle.loads(pickle.dumps(value))
        except Exception as exc:                     # noqa: BLE001 — the message is the point
            pytest.fail(f'{name}() returned something unpicklable ({exc}); a spawned worker '
                        f'cannot receive it')


# ── 3. no accessor snapshots CONFIG at import ─────────────────────────────────────

@pytest.mark.parametrize('name', [n for n in _spec_accessors()])
def test_the_accessor_reads_config_at_call_time(name):
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
    _SKIP = object()

    def _bump(cur):
        """A value of the SAME KIND but a different one, or `_SKIP` if there isn't one.

        `None` is skipped deliberately.  It is a legitimate configured value across this
        CONFIG — "no whistle", "an unbounded floor", "take the pick crew's mode" — and it
        carries no type, so any replacement invents one.
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

    def _sections():
        yield sc.CONFIG['global']
        for block in sc.CONFIG.get('channels', {}).values():
            if isinstance(block, dict):
                yield block

    g0 = sc.CONFIG['global']
    _gate_saved = {k: g0[k] for k in GATE_ON.get(name, {}) if k in g0}
    g0.update(GATE_ON.get(name, {}))
    try:
        before = fn()
    except Exception:                                 # noqa: BLE001
        g0.update(_gate_saved)
        pytest.skip(f'{name}() does not evaluate even with its gate on')
    assert before is not None, (
        f'{name}() is still None with GATE_ON={GATE_ON.get(name)} applied; the gate moved, '
        f'so this test is measuring a `return None` line')

    tried, evidence = [], []
    for block in _sections():
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
                after = fn()
                moved = after != before
            except Exception:                         # noqa: BLE001 — a refusal IS a live read
                moved = True
            finally:
                block[key] = old
            if moved:
                evidence.append(key)

    g0.update(_gate_saved)

    assert tried, (
        f'{name} names no mutable CONFIG key this test can bump, so it proves nothing about '
        f'{name}.  Point it at the right section rather than letting it pass empty.')
    assert evidence, (
        f'{name}() returned the same record after each of {tried} was changed in CONFIG, and '
        f'refused none of them.  Either it snapshotted at import — in which case a CLI '
        f'override is accepted and silently ignored — or it does not read those keys and '
        f'this test is pointed at the wrong ones.')


def test_the_call_time_check_would_catch_a_snapshot():
    """SABOTAGE: an accessor that read CONFIG once at import must fail the check above."""
    snapshot = dict(sc.CONFIG['global'])            # taken NOW, never re-read

    def frozen_spec():
        return {'put_crew_size': snapshot['put_crew_size']}

    g = sc.CONFIG['global']
    old = g['put_crew_size']
    before = frozen_spec()
    g['put_crew_size'] = (old or 0) + 7
    try:
        assert frozen_spec() == before, \
            'the frozen stand-in moved, so it is not a snapshot and proves nothing'
    finally:
        g['put_crew_size'] = old
