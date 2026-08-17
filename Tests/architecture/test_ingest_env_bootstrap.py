"""test_ingest_env_bootstrap.py — ingest.py must load `.env`, or BY-NAME resolution is dead.

`docs/experiments/ingest.py` resolves a run's catalogue BY NAME through the run-layout's
`pair_bindings` (v2), under `--profiles-root`, whose documented default is `$PROFILE_INPUT_DIR`.

The defect this file pins, found in the 2026-08 dress rehearsal: **that default could never
populate.**  `PROFILE_INPUT_DIR` lives in `.env`, nothing in ingest.py ever read `.env`, so
`os.getenv` returned None -> `--profiles-root` defaulted to None -> `_inv_root_from_bindings`
returned at its first guard -> the catalogue silently resolved through `sim_meta.json`'s
recorded ABSOLUTE `inv_db` instead.  That is precisely the route `pair_bindings` exists to
replace, and the one that does not survive a moved drive.

It was silent because the fallback is legitimate for pre-v2 runs, so nothing warned.  Observed
against a real run: 0 `resolve … by NAME` lines with a fully non-null `pair_bindings`; 4 once
`--profiles-root` was passed by hand.

    python -m pytest Tests/architecture/test_ingest_env_bootstrap.py -q
"""
from __future__ import annotations

import importlib.util
import os

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
_INGEST = os.path.join(_ROOT, 'docs', 'experiments', 'ingest.py')


def _load_ingest():
    spec = importlib.util.spec_from_file_location('_ingest_under_test', _INGEST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── the regression itself ────────────────────────────────────────────────────────

def test_ingest_loads_dotenv_at_import_time():
    """The argparse default is evaluated when the parser is BUILT, so `.env` has to be in
    os.environ before then — i.e. at import, not inside main()."""
    with open(_INGEST, encoding='utf-8') as fh:
        src = fh.read()
    assert 'def _load_env(' in src, 'ingest.py must carry the entry-script .env loader'
    # Column 0 == module level: it must run at IMPORT.  Defining it is not enough, and calling
    # it inside main() is too late for a default evaluated while the parser is built.
    assert "\n_load_env(os.path.join(_REPO_ROOT, '.env'))" in src, (
        'the loader must RUN at module level, unindented')
    # …and before the flag whose default depends on it (the add_argument, not the docstring).
    assert src.index("\n_load_env(") < src.index('ap.add_argument("--profiles-root"')


def test_profiles_root_default_is_the_env_var_not_a_literal():
    with open(_INGEST, encoding='utf-8') as fh:
        src = fh.read()
    assert 'os.getenv("PROFILE_INPUT_DIR")' in src or "os.getenv('PROFILE_INPUT_DIR')" in src


# ── the loader's own contract ────────────────────────────────────────────────────

def test_load_env_injects_missing_keys(tmp_path, monkeypatch):
    mod = _load_ingest()
    env = tmp_path / '.env'
    env.write_text('PROFILE_INPUT_DIR=/somewhere/profiles\n# a comment\nEMPTY\n',
                   encoding='utf-8')
    monkeypatch.delenv('PROFILE_INPUT_DIR', raising=False)
    mod._load_env(str(env))
    assert os.environ['PROFILE_INPUT_DIR'] == '/somewhere/profiles'
    assert 'EMPTY' not in os.environ, 'a line with no "=" is not a variable'


def test_the_shell_wins_over_dotenv(tmp_path, monkeypatch):
    """Same precedence as every sibling entry script: an explicit shell var is not overwritten."""
    mod = _load_ingest()
    env = tmp_path / '.env'
    env.write_text('PROFILE_INPUT_DIR=/from/dotenv\n', encoding='utf-8')
    monkeypatch.setenv('PROFILE_INPUT_DIR', '/from/shell')
    mod._load_env(str(env))
    assert os.environ['PROFILE_INPUT_DIR'] == '/from/shell'


def test_load_env_is_a_noop_on_a_missing_file(tmp_path):
    mod = _load_ingest()
    mod._load_env(str(tmp_path / 'nope.env'))       # must not raise — a fresh clone has no .env


def test_quoted_and_raw_string_values_are_unwrapped(tmp_path, monkeypatch):
    """`.env` here carries Windows paths written as r"..." — the sibling loaders strip both
    forms, and a value left wrapped in quotes resolves to a directory that does not exist."""
    mod = _load_ingest()
    env = tmp_path / '.env'
    env.write_text('A_QUOTED=\"plain\"\nB_RAW=r\"raw/value\"\n', encoding='utf-8')
    for k in ('A_QUOTED', 'B_RAW'):
        monkeypatch.delenv(k, raising=False)
    mod._load_env(str(env))
    assert os.environ['A_QUOTED'] == 'plain'
    assert os.environ['B_RAW'] == 'raw/value'


# ── the guard that made the failure silent ───────────────────────────────────────

def test_binding_resolution_returns_none_without_a_profiles_root():
    """The guard is correct — but it is why a missing env var produced no warning at all.
    Kept as documentation of the silent path, so a future reader does not re-lose the hour."""
    mod = _load_ingest()

    class _RT:
        layout = {'pair_bindings': {'cat__prof': {'profile_run': 'cat', 'profile': 'prof'}}}

    log = []
    assert mod._inv_root_from_bindings(_RT(), 'cat__prof', None, log) is None
    assert log == [], 'the silent path: no root, no binding attempt, and nothing logged'


def test_binding_resolution_logs_by_name_when_it_succeeds(tmp_path):
    """The positive half: with a root and a real inventory DB present, the BY-NAME line is
    emitted.  That line is the only externally visible proof the fingerprint path ran."""
    mod = _load_ingest()
    inv = tmp_path / 'cat' / 'prof' / 'inventory'
    inv.mkdir(parents=True)
    (inv / 'inventory.db').write_bytes(b'')

    class _RT:
        layout = {'pair_bindings': {'cat__prof': {'profile_run': 'cat', 'profile': 'prof'}}}

    log = []
    got = mod._inv_root_from_bindings(_RT(), 'cat__prof', str(tmp_path), log)
    assert got is not None and os.path.samefile(got, str(inv))
    assert any('catalogue by NAME via pair_bindings' in m for m in log), log
