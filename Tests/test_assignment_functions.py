"""test_assignment_functions.py — the composable, named assignment-function layer.

Verifies the decoupled Assignment_Functions module: programmatic names, the
name->builder registries, scorer needs-flags, and that the composed scorers place
as intended (travel picks the low-W aisle; cohesion co-locates with partners).

Usage:  cd Tests && python test_assignment_functions.py
"""
from __future__ import annotations

import os
import sys
import types
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Warehouse'))

import numpy as np
from scipy.sparse import csr_matrix
from Affinity_Store import AffinityStore
import Assignment_Functions as A

_PASS = _FAIL = 0


def check(label, ok, detail=''):
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f'  PASS  {label}')
    else:
        _FAIL += 1
        print(f'  FAIL  {label}' + (f'  ({detail})' if detail else ''))


class _B:
    __slots__ = ('location', 'x_phys', 'y_phys')
    def __init__(self, aid, x):
        self.location = (aid, 0, 0);  self.x_phys = x;  self.y_phys = 0


def _aff(skus, pairs):
    aff = AffinityStore(':memory:')
    idx = {s: i for i, s in enumerate(skus)}
    rows, cols, data = [], [], []
    for i, j, l in pairs:
        rows += [idx[i], idx[j]];  cols += [idx[j], idx[i]];  data += [l, l]
    aff._sku_to_idx = idx
    aff._matrix = csr_matrix((data, (rows, cols)), shape=(len(skus), len(skus)), dtype=np.float32)
    return aff, idx


def _unit(sku):
    return types.SimpleNamespace(carton=types.SimpleNamespace(sku=sku))


def test_registries_and_names():
    print('\n-- programmatic names + registries --')
    check('ASSIGNMENT_BUILDERS names',
          set(A.ASSIGNMENT_BUILDERS) == {'travel_min', 'travel_max', 'cohesion_max',
                                         'cohesion_min', 'uniform_min', 'load_min', 'load_max'},
          str(sorted(A.ASSIGNMENT_BUILDERS)))
    check('RANKED_BUILDERS names',
          set(A.RANKED_BUILDERS) == {'travel_min', 'travel_max', 'uniform_ranked'})
    check('SCORER_NEEDS travel = (affinity, demand)', A.SCORER_NEEDS['travel_min'] == (True, True))
    check('SCORER_NEEDS uniform = (neither)', A.SCORER_NEEDS['uniform_min'] == (False, False))

    wp = types.SimpleNamespace(x_speed=1.0, y_speed=0.5)
    aff, _ = _aff([1, 2], [])
    fn = A.ASSIGNMENT_BUILDERS['travel_min'](aff, wp, defaultdict(set), defaultdict(set),
                                             defaultdict(float), {}, {1: 0.5}, {1: 1.0})
    check("built 'travel_min' carries .name", getattr(fn, 'name', None) == 'travel_min')
    fn2 = A.ASSIGNMENT_BUILDERS['cohesion_max'](aff, wp, defaultdict(set), defaultdict(set),
                                                defaultdict(float), {}, {1: 0.5}, {1: 1.0})
    check("built 'cohesion_max' carries .name", getattr(fn2, 'name', None) == 'cohesion_max')


def test_composed_scorer_placement():
    print('\n-- composed scorers place as intended (shared core) --')
    wp = types.SimpleNamespace(x_speed=1.0, y_speed=0.0)
    aff, idx = _aff([1, 2], [(1, 2, 5.0)])
    fbi = {idx[2]: 1.0};  fbs = {1: 0.5, 2: 1.0};  qbs = {1: 1.0, 2: 1.0}
    cands = [_B(10, 9.0), _B(20, 1.0)]                    # aisle10 W=9 (partner), aisle20 W=1

    ss, ii, dd = defaultdict(set), defaultdict(set), defaultdict(float)
    ss[10] = {2};  ii[10] = {idx[2]}                      # partner sku2 placed in aisle 10
    b = A.build_cluster_maximizing_assignment_fn(aff, wp, ss, ii, dd, fbi, fbs, qbs)(_unit(1), cands)
    check('cohesion_max co-locates with partner (aisle 10) despite higher W', b.location[0] == 10)

    ss, ii, dd = defaultdict(set), defaultdict(set), defaultdict(float)
    b = A.build_trip_minimizing_assignment_fn(aff, wp, ss, ii, dd, fbi, fbs, qbs)(_unit(1), cands)
    check('travel_min picks the lowest-W aisle (20)', b.location[0] == 20)


if __name__ == '__main__':
    print('=' * 64);  print('  Assignment_Functions tests');  print('=' * 64)
    test_registries_and_names()
    test_composed_scorer_placement()
    print('=' * 64)
    print(f'  All {_PASS} checks passed.' if _FAIL == 0 else f'  {_PASS} passed  {_FAIL} FAILED')
    print('=' * 64)
    sys.exit(0 if _FAIL == 0 else 1)
