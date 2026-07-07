"""Tests/conftest.py — single sys.path bootstrap for the whole suite.

Replaces the per-file ``sys.path.insert(...)`` boilerplate that every test used
to carry.  Project imports are package-absolute: ``from Warehouse.Order import
Order``, ``from Optimization import channels``.  Tests/ itself stays a
NON-package (no __init__.py): pytest's default prepend import mode puts each
test file's own directory on sys.path, which is what keeps helper imports
between sibling tests (``from test_fulfillment_channels import _mixed_inventory``)
and the bare sibling imports inside Tests/gpu/ working with zero per-file setup.
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Tests/bench holds shared scenario builders (perf_simulation._build_inventory etc.)
# that tests import by bare name (test_index_equivalence).  bench_* files are not
# collected (not test_*), so this adds helpers only — no extra tests.
_BENCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bench')
if _BENCH not in sys.path:
    sys.path.insert(0, _BENCH)
