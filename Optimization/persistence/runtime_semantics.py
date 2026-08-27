"""runtime_semantics — the runtime-metrics family's column semantics, beside its DDL.

One row per ARM, and every `*_s` here is a SPAN of WALL-CLOCK COMPUTE seconds — the same
word "seconds" as the sim tables and a completely different instrument (incident class 11:
summing a deep-ladder mean against a phase wall cost a day and a wrong committed claim).
The clock axis on every time column is what makes that mistake refusable now.

Tags are metadata: importing this module changes no DDL, no shape id, no result.
"""
from __future__ import annotations

from Schema.semantics import Col, COUNT, LABEL, LEVEL, RATE, SHARE, SPAN, WALL, register

_ID = Col(LABEL, 'enum', 'arm')

def _wall(note=None, null_means=None):
    return Col(SPAN, 's', 'arm', clock=WALL, note=note, null_means=null_means)

RUNTIME_SEMANTICS: dict = {
    'runtime': {
        'cell':        _ID,
        'pair':        _ID,
        'config':      _ID,
        'channel':     _ID,
        'arm':         _ID,
        'initial':     _ID,
        'assignment':  _ID,
        'n_bins':      Col(LEVEL, 'bins', 'arm'),
        'regime_bins': Col(LEVEL, 'bins', 'arm'),
        'n_aisles':    Col(LEVEL, 'aisles', 'arm'),
        'batches':     Col(COUNT, 'batches', 'arm'),
        'total_s':     _wall('spans the BATCH LOOP only — not the worker lifetime; '
                             'the section totals partition it minus a measured '
                             '0.8-2.2% tail'),
        'rate':        Col(RATE, 'batches/s', 'arm', clock=WALL, per='total_s'),
        'reord_s':     _wall(),
        'build_s':     _wall(),
        'pre_s':       _wall(),
        'sim_s':       _wall(),
        'extract_s':   _wall(),
        'inv_s':       _wall(),
        'save_s':      _wall(),
        'smpl_s':      _wall(),
        'task_s':      _wall(),
        'kf_s':        _wall(),
        'p1_s':        _wall(),
        'p2_s':        _wall(),
        'gc_pause_s':  _wall('spans the worker LIFETIME, not the batch loop — never '
                             'ratio against total_s'),
        'gc_gen2':     Col(COUNT, 'collections', 'arm'),
        'peak_rss_mib': Col(LEVEL, 'MiB', 'arm',
                            null_means='not measured on this platform'),
        'live_objects': Col(LEVEL, 'objects', 'arm',
                            null_means='not measured'),
        'precomp_s':   _wall('inline vs backfill provenance do not form a ratio — '
                             'precomp_src says which this was',
                             null_means='not measured — which is not zero'),
        'precomp_src': Col(LABEL, 'enum', 'arm',
                           null_means='no map precompute ran for this arm'),
        'map_lap_pct': Col(SHARE, '1', 'arm',
                           null_means='not a Map-family arm'),
    },
    'schema_meta': {
        'key':   Col(LABEL, 'enum', 'row'),
        'value': Col(LABEL, 'text', 'row',
                     null_means='a flag-style key with no payload'),
    },
}

COVERED: tuple = tuple(RUNTIME_SEMANTICS)

register('runtime_metrics_db', RUNTIME_SEMANTICS, COVERED)
