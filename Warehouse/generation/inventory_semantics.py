"""inventory_semantics — the profiles-tree (inventory) family's column semantics.

Two tables, two different kinds of statement.  `cartons` holds the SKU's own facts: physical
dimensions in inches (the inch/second boundary is `cost_model.sec_per_inch`), demand rates
per BATCH, the stamped line law (`line_family` + `line_params`, department-calibration "Stamp
the line distribution on the SKU"), and the batch-denominated lead the trailer feature
replaces flag-on.  It declares NO stock level at all (ADR-0002, department-calibration "Field
the floor", decision 5): an order-up-to quantity is a decision a run makes about a warehouse,
not a fact about a carton, and while it lived here it was authored as
`coverage_batches x expected_batch_demand` — a generation batch is not a unit of time, so the
authored level was worth 1,771 store days on the reference catalogue.

`stock_levels` is where that decision lands instead.  A run derives its levels at setup from
a declared coverage in DAYS (`Optimization/simconfig/coverage.py`) and records them in its
own `planned_inventory.db`; a GENERATED catalogue leaves the table empty.  That emptiness is
the contract — the absence of a ROW, not a NULL column, is what "no declaration" means, so
`Order.stock_declared()` can tell "nobody has decided yet" from "decided, and the answer is
missing".  `stock_plan` still earns the loudest note in the family, now from its new home: a
planned SKU bypasses the pallet/singleton packing rule, which is why splitting a delivery can
produce FEWER units — "splitting is worse" is not a safe assumption on any run that planned
its packing.

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
        'lead_time_mean':        Col(SPAN, 'batches', 'sku', clock=BATCHES,
                                     note='the batch-denominated lead the trailer feature '
                                          'replaces flag-on'),
        'supply_cv':             Col(SCORE, 'cv', 'sku'),
        'subtype':               Col(LABEL, 'enum', 'sku',
                                     null_means='no subtype assigned'),
    },
    #: THE RUN'S DECLARATION, not the SKU's facts — empty on a generated catalogue, filled
    #: by the run that derived it (`planned_inventory.db`).  Every column here left `cartons`
    #: in the ADR-0002 vintage; the three older vintages are served through a `dataset`
    #: override that reads them back out of `cartons` under these same logical names, so an
    #: archived planned inventory still yields the levels its run actually fielded.
    'stock_levels': {
        'sku':             _KEY,
        'equilibrium_qty': Col(COUNT, 'items', 'sku', account=PIECES,
                               note='the order-up-to a RUN declared, derived at setup from '
                                    'a coverage in DAYS (simconfig/coverage.py) in every '
                                    'mode; never authored at generation, where it was a '
                                    'coverage in BATCHES and therefore no span of time'),
        'reorder_point':   Col(COUNT, 'items', 'sku', account=PIECES,
                               note='declared with equilibrium_qty by the same fixed point; '
                                    'under the line floor it encodes a LINE, which is why '
                                    'pipeline_qty exists rather than being inferred from it'),
        'stock_plan':      Col(LABEL, 'plan', 'sku',
                               null_means='no plan — the default pallet/singleton '
                                          'packing rule applies',
                               note='a planned SKU OVERRIDES the packer: tier mix becomes a '
                                    'setup-time knob of the run that planned it, and '
                                    'splitting a delivery can produce FEWER units'),
        'pipeline_qty':    Col(COUNT, 'items', 'sku', account=PIECES,
                               null_means='not stamped: the manager infers the lead '
                                          'pipeline as rp x lead / (lead + 1)',
                               note='the era STAMPS the in-transit allowance '
                                    '(round(d_s x lead), simconfig/coverage.py) because '
                                    'under the line floor rp encodes a LINE, not '
                                    'lead-time demand; only a run\'s planned inventory ever '
                                    'carries a row here, read through '
                                    'Order.pipeline_allowance'),
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
