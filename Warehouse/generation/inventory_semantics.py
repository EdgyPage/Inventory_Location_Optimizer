"""inventory_semantics — the profiles-tree (inventory) family's column semantics.

The catalogue's per-SKU constants: physical dimensions in inches (the inch/second boundary
is `cost_model.sec_per_inch`), demand rates per BATCH, the stamped line law (`line_family` +
`line_params`, department-calibration "Stamp the line distribution on the SKU"), and the
batch-denominated lead the trailer feature replaces flag-on.  `stock_plan` earns the loudest note in the family: a
planned SKU bypasses the pallet/singleton packing rule, which is why splitting a delivery
can produce FEWER units — "splitting is worse" is not a safe assumption on most of a
generated catalogue.

Tags are metadata: importing this module changes no DDL, no shape id, no result.
"""
from __future__ import annotations

from Schema.semantics import (
    BATCHES, Col, COUNT, LABEL, LEVEL, PIECES, RATE, SCORE, SHARE, SPAN, register)

_KEY = Col(LABEL, 'id', 'row')
_ENUM = Col(LABEL, 'enum', 'row')
_DIM = Col(LEVEL, 'inches', 'sku',
           note='catalogue constant; seconds enter only through sec_per_inch')

INVENTORY_SEMANTICS: dict = {
    'cartons': {
        'sku':                   _KEY,
        'handling':              _ENUM,
        'category':              _ENUM,
        'length':                _DIM,
        'width':                 _DIM,
        'height':                _DIM,
        'weight':                Col(LEVEL, 'weight-units', 'sku',
                                     note='catalogue constant'),
        'relative_frequency':    Col(SHARE, '1', 'sku'),
        'demand_qty_rate':       Col(RATE, 'items/batch', 'sku', per='batch'),
        'line_family':           Col(LABEL, 'enum', 'sku',
                                     note='the family one pick LINE\'s quantity is drawn from '
                                          '(Demand.line); every reader goes through the object, '
                                          'none re-derives a law from demand_qty_rate'),
        'line_params':           Col(LABEL, 'json', 'sku',
                                     null_means='the family takes no parameters'),
        'expected_batch_demand': Col(RATE, 'items/batch', 'sku', per='batch'),
        'equilibrium_qty':       Col(COUNT, 'items', 'sku', account=PIECES),
        'reorder_point':         Col(COUNT, 'items', 'sku', account=PIECES),
        'lead_time_mean':        Col(SPAN, 'batches', 'sku', clock=BATCHES,
                                     note='the batch-denominated lead the trailer feature '
                                          'replaces flag-on'),
        'supply_cv':             Col(SCORE, 'cv', 'sku'),
        'stock_plan':            Col(LABEL, 'plan', 'sku',
                                     null_means='no plan — the default pallet/singleton '
                                                'packing rule applies',
                                     note='a planned SKU OVERRIDES the packer: tier mix '
                                          'becomes a generation-time knob, and splitting '
                                          'a delivery can produce FEWER units'),
        'subtype':               Col(LABEL, 'enum', 'sku',
                                     null_means='no subtype assigned'),
    },
    'creation_plan': {
        'handling':       _ENUM,
        'storage_type':   _ENUM,
        'parameter':      _ENUM,
        'distribution':   _ENUM,
        'params':         Col(LABEL, 'json', 'row',
                              null_means='the distribution takes no parameters'),
        'handling_share': Col(SHARE, '1', 'row'),
        'family_share':   Col(SHARE, '1', 'row'),
    },
    'run_metadata': {
        'key':   Col(LABEL, 'enum', 'row'),
        'value': Col(LABEL, 'text', 'row'),
    },
}

COVERED: tuple = tuple(INVENTORY_SEMANTICS)

register('inventory_db', INVENTORY_SEMANTICS, COVERED)
