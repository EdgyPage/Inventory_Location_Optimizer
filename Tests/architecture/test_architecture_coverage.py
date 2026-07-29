"""test_architecture_coverage.py

EMPIRICAL half of the architecture contract: the `hotpaths` declared in
context/architecture.yml must actually EXECUTE end-to-end, not merely exist in the source.

Drives Tests/bench/coverage_e2e.py :: main() (the in-process, ProcessPool-bypassing
pipeline exerciser) under the coverage.py API and asserts every hotpath ran — with the
first hotpath as a SENTINEL (called directly by the driver) so a no-op driver fails loudly
rather than passing vacuously.

Skips cleanly when `coverage` is not installed or no generated profile data is present, so
it never reds the ordinary suite; it bites only once coverage + data are available.

Run:  pip install coverage && python -m pytest Tests/test_architecture_coverage.py -q
"""
from __future__ import annotations

import ast
import importlib.util
import os

import pytest

pytest.importorskip('yaml', reason='needs pyyaml (requirements-docs.txt)')
import yaml  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _def_span(abs_path: str, name: str) -> tuple[int, int] | None:
    """(body_start_line, end_line) of the first def/class named `name`, via ast.

    Deliberately starts at the first BODY statement (skipping the signature and any
    docstring), because the `def` line itself executes at import/def time — including it
    would falsely report a never-called function as "executed" under coverage.
    """
    with open(abs_path, encoding='utf-8') as fh:
        tree = ast.parse(fh.read(), filename=abs_path)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and node.name == name:
            body = node.body
            idx = 0
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    getattr(body[0], 'value', None), ast.Constant) \
                    and isinstance(body[0].value.value, str) and len(body) > 1:
                idx = 1                       # skip a leading docstring
            start = body[idx].lineno if body else node.lineno
            return (start, getattr(node, 'end_lineno', start))
    return None


def _hotpaths() -> list[dict]:
    with open(os.path.join(_ROOT, 'context', 'architecture.yml'), encoding='utf-8') as fh:
        return (yaml.safe_load(fh) or {}).get('hotpaths') or []


def _load_driver():
    path = os.path.join(_ROOT, 'Tests', 'bench', 'coverage_e2e.py')
    spec = importlib.util.spec_from_file_location('coverage_e2e', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- unit: the custom line-span resolution logic (no coverage / data needed) --------------

def test_def_span_resolution():
    # every declared hotpath resolves to a real, non-empty def span
    hps = _hotpaths()
    assert len(hps) >= 5, 'architecture.yml has too few hotpaths'
    for h in hps:
        span = _def_span(os.path.join(_ROOT, h['file']), h['name'])
        assert span is not None, f"hotpath def not found: {h['name']} @ {h['file']}"
        lo, hi = span
        assert 1 <= lo <= hi, f'bad span for {h["name"]}: {span}'
    # a fabricated name resolves to nothing (the resolver isn't vacuous)
    assert _def_span(os.path.join(_ROOT, 'Optimization', 'strategy_runner.py'),
                     '__definitely_not_a_symbol__') is None


# --- empirical: hotpaths actually execute under the in-process driver ---------------------

def test_hotpaths_execute_under_e2e_driver():
    coverage = pytest.importorskip('coverage', reason='empirical coverage needs the `coverage` package')
    driver = _load_driver()
    if not driver.has_profile_data():
        pytest.skip('no generated (inventory.db, affinity.db) pair to drive coverage_e2e')

    cov = coverage.Coverage(data_file=None)   # in-memory, writes no .coverage file
    cov.start()
    try:
        driver.main()
    except Exception as exc:                  # sim hotpaths already ran before any analysis error
        print(f'coverage_e2e.main() raised after the sim phase: {exc!r}')
    finally:
        cov.stop()
    data = cov.get_data()

    def executed(anchor) -> bool:
        abs_path = os.path.join(_ROOT, anchor['file'])
        span = _def_span(abs_path, anchor['name'])
        if span is None:
            return False
        lines = data.lines(abs_path) or []
        lo, hi = span
        return any(lo <= ln <= hi for ln in lines)

    hps = _hotpaths()
    # non-vacuity: the sentinel (driver calls it directly) MUST show as executed
    assert executed(hps[0]), f'sentinel hotpath never executed: {hps[0]}'
    missing = [f"{h['name']} @ {h['file']}" for h in hps if not executed(h)]
    assert not missing, 'declared hotpaths never executed under coverage_e2e: ' + '; '.join(missing)
