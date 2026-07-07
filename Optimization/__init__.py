"""Optimization — the run harness as a package.

Simulation runner (run_simulation / strategy_runner), analysis
(run_analysis + Performance_Evaluations), channel rollup, and the SQLite
persistence layers.  Import style is package-absolute
(``from Optimization import channels``); the repo root must be on sys.path —
entry scripts self-bootstrap, pytest gets it from Tests/conftest.py.

Deliberately side-effect-free: no imports here (Performance_Evaluations keeps
its own deliberate registry side effects in ITS __init__, fired only when the
analysis package itself is imported).
"""
