"""test_registry_discovery.py

Locks the two `pkgutil.walk_packages` auto-discovery registries against SILENT loss.

WHY THIS EXISTS: `Performance_Evaluations/core/discovery.py` and `simconfig/core/discovery.py` import
every submodule so `@evaluation` / `@pick_config` decorators fire.  `walk_packages` only descends
into directories that are importable packages, so a module that lands outside `pkg.__path__` — or in
a new subdirectory that is missing `__init__.py` — is simply NEVER IMPORTED.  Its decorator never
runs, the registry is quietly short one entry, and:

  * a missing @evaluation  -> the preset silently renders fewer plots;
  * a missing @pick_config -> a config vanishes from the sweep, which CHANGES THE OUTPUT DIRECTORY
    NAMES and therefore breaks the run-tree contract downstream.

Nothing raised, nothing failed — which is exactly why these assertions exist.  Both registries are
also repopulated in every spawned worker process, so a discovery breakage that a single-process run
tolerates still bites in production.

The check is structural, not a hardcoded list: it walks the package directory on disk, counts the
modules that declare a decorator, and asserts the registry holds exactly that many.  Adding a graph
or a pick-config therefore needs no edit here; LOSING one fails.

Run:  python -m pytest Tests/test_registry_discovery.py -q
"""
from __future__ import annotations

import ast
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))


def _decorated_keys(pkg_relpath: str, decorator: str) -> set[str]:
    """Every `key=`/`name=` passed to `@decorator(...)` across a package's modules, read from the
    SOURCE with ast — deliberately independent of importing anything, so it can't share a bug with
    the discovery machinery it is checking."""
    found: set[str] = set()
    root = os.path.join(_ROOT, pkg_relpath)
    for dirpath, _dirs, files in os.walk(root):
        if '__pycache__' in dirpath:
            continue
        for fn in sorted(files):
            if not fn.endswith('.py'):
                continue
            with open(os.path.join(dirpath, fn), encoding='utf-8') as fh:
                tree = ast.parse(fh.read(), filename=fn)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                for dec in node.decorator_list:
                    if not isinstance(dec, ast.Call):
                        continue
                    name = dec.func.id if isinstance(dec.func, ast.Name) else getattr(
                        dec.func, 'attr', '')
                    if name != decorator:
                        continue
                    for kw in dec.keywords:
                        if kw.arg in ('key', 'name') and isinstance(kw.value, ast.Constant):
                            found.add(kw.value.value)
    return found


def _package_dirs_missing_init(pkg_relpath: str) -> list[str]:
    """Directories under a discovery package that hold .py files but no __init__.py.

    walk_packages cannot descend into these, so anything inside is invisible to the registry.
    """
    bad: list[str] = []
    root = os.path.join(_ROOT, pkg_relpath)
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        if dirpath == root:
            continue
        if any(f.endswith('.py') and f != '__init__.py' for f in files) \
                and '__init__.py' not in files:
            bad.append(os.path.relpath(dirpath, _ROOT).replace('\\', '/'))
    return bad


# ── evaluations (the graph suite) ───────────────────────────────────────────────

def test_every_declared_evaluation_is_registered():
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
    declared = _decorated_keys('Optimization/Performance_Evaluations', 'evaluation')
    assert declared, 'ast scan found no @evaluation declarations — the scan itself is broken'
    missing = declared - set(EVAL_BY_KEY)
    assert not missing, (
        f'{len(missing)} @evaluation module(s) never registered — discovery did not import them: '
        f'{sorted(missing)}')


def test_evaluation_package_dirs_are_importable():
    bad = _package_dirs_missing_init('Optimization/Performance_Evaluations')
    assert not bad, f'directories with .py but no __init__.py (walk_packages skips them): {bad}'


# ── pick-configs (the sweep) ────────────────────────────────────────────────────

def test_every_declared_pick_config_is_registered():
    from Optimization.simconfig import PICK_CONFIG_BY_KEY
    declared = _decorated_keys('Optimization/simconfig', 'pick_config')
    assert declared, 'ast scan found no @pick_config declarations — the scan itself is broken'
    missing = declared - set(PICK_CONFIG_BY_KEY)
    assert not missing, (
        f'{len(missing)} @pick_config module(s) never registered — a config missing from the sweep '
        f'silently changes output directory names: {sorted(missing)}')


def test_pick_config_package_dirs_are_importable():
    bad = _package_dirs_missing_init('Optimization/simconfig')
    assert not bad, f'directories with .py but no __init__.py (walk_packages skips them): {bad}'


def test_figure_evaluation_keys_carry_their_family():
    """A config-stage figure eval's key prefix names its chart family, so the registry
    reads as the folder tree it produces.  'sig.' is the one blessed shorthand (for
    'significance'); aggregate-scope evals live in the 'agg.' namespace instead."""
    from Optimization import Performance_Evaluations  # noqa: F401 — populate the registry
    from Optimization.Performance_Evaluations.core.registry import EVALUATIONS
    alias = {'significance': 'sig'}
    for ev in EVALUATIONS:
        if ev.scope == 'aggregate':
            assert ev.key.startswith('agg.'), ev.key
            continue
        if ev.family:
            prefix = ev.key.split('.')[0]
            assert prefix in (ev.family, alias.get(ev.family)), (
                f'{ev.key} declares family {ev.family!r} — key prefix must match it')


def test_registries_are_not_empty():
    """Non-vacuity: every assertion above passes trivially against an empty registry."""
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
    from Optimization.simconfig import PICK_CONFIG_BY_KEY
    assert len(EVAL_BY_KEY) >= 20, f'only {len(EVAL_BY_KEY)} evaluations registered'
    assert len(PICK_CONFIG_BY_KEY) >= 4, f'only {len(PICK_CONFIG_BY_KEY)} pick-configs registered'
