"""affinity_semantics — the affinity family's column semantics.

Three tiny tables; `lift` is a dimensionless co-demand ratio, comparable only within one
catalogue's generation — a SCORE, not a share and not a rate.

Tags are metadata: importing this module changes no DDL, no shape id, no result.
"""
from __future__ import annotations

from Schema.semantics import Col, LABEL, SCORE, register

_KEY = Col(LABEL, 'id', 'row')

AFFINITY_SEMANTICS: dict = {
    'affinity': {
        'sku_i': _KEY,
        'sku_j': _KEY,
        'lift':  Col(SCORE, 'ratio', 'sku-pair',
                     note='dimensionless co-demand lift; 41 MB as a CSR in RAM, not the '
                          '291 MB its file suggests'),
    },
    'sku_group': {
        'sku':        _KEY,
        'lift_group': Col(LABEL, 'enum', 'sku'),
    },
    'run_metadata': {
        'key':   Col(LABEL, 'enum', 'row'),
        'value': Col(LABEL, 'text', 'row'),
    },
}

COVERED: tuple = tuple(AFFINITY_SEMANTICS)

register('affinity_db', AFFINITY_SEMANTICS, COVERED)
