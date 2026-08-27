"""warehouse_semantics — the warehouse-DB family's column semantics, beside its DDL.

Build-time counts, target shares and identity fingerprints; no time columns, low risk —
tagged anyway, because "leave no remainder" is the rule that keeps the next column honest.

Tags are metadata: importing this module changes no DDL, no shape id, no result.
"""
from __future__ import annotations

from Schema.semantics import Col, COUNT, LABEL, LEVEL, SHARE, register

_KEY = Col(LABEL, 'id', 'row')
_ENUM = Col(LABEL, 'enum', 'row')

WAREHOUSE_SEMANTICS: dict = {
    'aisle_layout': {
        'aisle_id':      _KEY,
        'handling_type': _ENUM,
        'category':      _ENUM,
        'unit_type':     _ENUM,
        'storage_size':  _ENUM,
        'bay_x':         Col(COUNT, 'bays', 'aisle'),
        'bay_y':         Col(COUNT, 'bays', 'aisle'),
    },
    'aisle_type_stats': {
        'id':                 _KEY,
        'warehouse_id':       _KEY,
        'handling_type':      _ENUM,
        'category':           _ENUM,
        'unit_type':          _ENUM,
        'replica_count':      Col(COUNT, 'aisles', 'type'),
        'eff_bins_per_aisle': Col(LEVEL, 'bins', 'type'),
        'total_bins':         Col(COUNT, 'bins', 'type'),
        'size_small_pct':     Col(SHARE, '1', 'type'),
        'size_medium_pct':    Col(SHARE, '1', 'type'),
        'size_large_pct':     Col(SHARE, '1', 'type'),
        'size_xlarge_pct':    Col(SHARE, '1', 'type'),
    },
    'warehouse_stats': {
        'id':                    _KEY,
        'inventory_db':          Col(LABEL, 'path', 'run',
                                     note='where the catalogue CAME from at build time — '
                                          'the run manifest’s inv_db pointer went stale '
                                          'against the moved archive, so treat any path '
                                          'column as provenance, never as a live location'),
        'timestamp':             Col(LABEL, 'timestamp', 'run',
                                     note='wall-clock TEXT, identity only'),
        'n_skus':                Col(COUNT, 'skus', 'run'),
        'n_pallet_units':        Col(COUNT, 'units', 'run',
                                     note='storage units at build — packs'),
        'n_singleton_units':     Col(COUNT, 'units', 'run'),
        'total_aisles':          Col(COUNT, 'aisles', 'run'),
        'total_bins':            Col(COUNT, 'bins', 'run'),
        'expected_fill':         Col(SHARE, '1', 'run'),
        'target_fill':           Col(SHARE, '1', 'run'),
        'max_aisles_cap':        Col(LEVEL, 'aisles', 'run',
                                     null_means='no cap configured'),
        'max_bins_cap':          Col(LEVEL, 'bins', 'run',
                                     null_means='no cap configured'),
        'avg_equilibrium_qty':   Col(LEVEL, 'items', 'run'),
        'avg_reorder_point':     Col(LEVEL, 'items', 'run'),
        'warehouse_fingerprint': Col(LABEL, 'digest', 'run',
                                     null_means='pre-fingerprint vintage'),
    },
    'schema_meta': {
        'key':   Col(LABEL, 'enum', 'row'),
        'value': Col(LABEL, 'text', 'row',
                     null_means='a flag-style key with no payload'),
    },
}

COVERED: tuple = tuple(WAREHOUSE_SEMANTICS)

register('warehouse_db', WAREHOUSE_SEMANTICS, COVERED)
