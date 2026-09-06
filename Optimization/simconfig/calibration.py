"""calibration.py — the committed CALIBRATION RECORD and its loader.

The calibration record (`calibration_record.json`, beside this module) is the committed
declaration of the labour constants a run prices its derivation with, together with the
provenance that produced each one ("Choose the calibration procedure", decision 5;
glossary: *Calibration record*).  A run READS it and copies what it ran under onto its own
staffing record; it never edits it.  The reference run
(`.scratch/department-calibration/issues/09-take-the-reference-run.md`) is what writes a
new one.

Three constants, two of them hybrid:

    s_pick   per channel   seconds per unit picked   -- measured under `fifo`, or SEEDED
    s_put    one site value seconds per unit put     -- measured, or SEEDED
    s_recv   exact from the script                   -- never in the record's constants;
                                                        the derivation computes it live

and one bound, `k_max` per channel (the heaviest-aisle floor's window minimum, "bounds, it
does not set").

**The pass-0 seed.**  Until a reference run exists there is no measured value for ANY
catalogue, so the record cannot hold a number that is right for the catalogue a run loads.
What it holds instead is the seed's SHAPE: a `travel_share` per constant, and `value: null`.
The derivation prices the catalogue's own analytic prediction (`staffing.analytic_pick`)
and multiplies by that share -- `s = analytic × travel_share` -- stamping the result with
the record entry's provenance (`seed`).  This is the same arithmetic decision 7 uses to
price a NEW catalogue provisionally from a MEASURED record (`s_new = analytic_new ×
travel_share_old`, stamped `derived`), so the loader has one resolution rule for both:

    a CLI override          -> its value, `declared`
    a recorded `value`      -> that value, the recorded provenance (`measured`)
    a recorded travel_share -> analytic × share, `seed` if the entry says seed else `derived`

**Staleness.**  The record names the warehouse fingerprint it was measured on.  A run
whose catalogue fingerprints differently gets a WARNING and `calibration_stale: True`
stamped into its staffing record; failing would block every new catalogue, a bare
warning gets lost, the stamp is readable from the results forever.  A record with no
fingerprint (the seed) is stale for nobody: there is nothing to be stale against, and
`calibration_stale` is False with `calibration_measured` False beside it so a reader can
tell "matched" from "never measured".
"""
from __future__ import annotations

import json
import os

from Optimization.simconfig.constants import PROVENANCE

#: The committed record, beside this module.  `--calibration-record PATH` points a run at
#: another one (a candidate written by a reference run, before it is committed).
RECORD_PATH: str = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                'calibration_record.json')

#: The record's own format version.  Bumped when a field changes meaning, never for a
#: new pass -- passes are data.
RECORD_SCHEMA: int = 1

#: The channels a record prices.  The derivation's CHANNELS, restated here so this leaf
#: does not import the derivation.
_CHANNELS: tuple[str, ...] = ('store', 'fulfillment')


class CalibrationRecordError(ValueError):
    """The record on disk is not one this loader understands."""


def load_record(path: str | None = None) -> dict:
    """Load and validate the calibration record.  Raises `CalibrationRecordError` on a
    shape this build does not understand -- never returns a half-read record."""
    p = path or RECORD_PATH
    with open(p) as f:
        rec = json.load(f)
    validate_record(rec, source=p)
    rec['_path'] = os.path.abspath(p)
    return rec


def validate_record(rec: dict, *, source: str = '<record>') -> None:
    """Every constant entry carries a legal provenance and either a value or a share."""
    if rec.get('schema') != RECORD_SCHEMA:
        raise CalibrationRecordError(
            f'{source}: schema {rec.get("schema")!r} is not {RECORD_SCHEMA}')
    consts = rec.get('constants')
    if not isinstance(consts, dict):
        raise CalibrationRecordError(f'{source}: no `constants` block')
    s_pick = consts.get('s_pick')
    if not isinstance(s_pick, dict) or not all(ch in s_pick for ch in _CHANNELS):
        raise CalibrationRecordError(f'{source}: `constants.s_pick` must hold {_CHANNELS}')
    for ch in _CHANNELS:
        _validate_entry(s_pick[ch], f'{source}: s_pick.{ch}')
    if 's_put' not in consts:
        raise CalibrationRecordError(f'{source}: no `constants.s_put`')
    _validate_entry(consts['s_put'], f'{source}: s_put')
    k_max = consts.get('k_max') or {}
    for ch, v in k_max.items():
        if ch not in _CHANNELS:
            raise CalibrationRecordError(f'{source}: k_max names unknown channel {ch!r}')
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1):
            raise CalibrationRecordError(f'{source}: k_max.{ch} must be a positive int or '
                                         f'null; got {v!r}')


def _validate_entry(e: dict, where: str) -> None:
    if not isinstance(e, dict):
        raise CalibrationRecordError(f'{where}: entry must be a dict')
    prov = e.get('provenance')
    if prov not in PROVENANCE:
        raise CalibrationRecordError(f'{where}: provenance {prov!r} not in {PROVENANCE}')
    v, share = e.get('value'), e.get('travel_share')
    if v is None and share is None:
        raise CalibrationRecordError(f'{where}: needs a `value` or a `travel_share`')
    if v is not None and not (isinstance(v, (int, float)) and v > 0):
        raise CalibrationRecordError(f'{where}: value must be positive seconds; got {v!r}')
    if share is not None and not (isinstance(share, (int, float)) and share >= 1.0):
        raise CalibrationRecordError(
            f'{where}: travel_share must be >= 1.0 (measured ÷ analytic, and the analytic '
            f'carries no travel); got {share!r}')


def is_measured(rec: dict) -> bool:
    """Whether any constant in the record was READ OFF A RUN (measured), as opposed to
    seeded.  The seed record answers False."""
    consts = rec['constants']
    entries = [consts['s_pick'][ch] for ch in _CHANNELS] + [consts['s_put']]
    return any(e.get('provenance') == 'measured' for e in entries)


def resolve_constant(entry: dict, *, override: float | None, analytic: float,
                     name: str) -> dict:
    """One constant, resolved by the rule in the module docstring.

    Returns `{value, provenance, source, ...}` where `source` says which branch fired
    (`override` / `record` / `analytic`), so a reader of the staffing record can tell a
    typed number from a copied one from a computed one without inferring it from the
    provenance alone.  A seed with no analytic to scale (an absent channel, or a
    catalogue whose section is empty) resolves to 0.0 and is flagged `unpriced`.
    """
    if override is not None:
        if override <= 0:
            raise ValueError(f'{name} override must be positive seconds; got {override!r}')
        return {'value': float(override), 'provenance': 'declared', 'source': 'override'}
    v = entry.get('value')
    if v is not None:
        return {'value': float(v), 'provenance': entry['provenance'], 'source': 'record'}
    share = float(entry['travel_share'])
    prov = 'seed' if entry['provenance'] == 'seed' else 'derived'
    out = {'value': float(analytic) * share, 'provenance': prov, 'source': 'analytic',
           'travel_share': share, 'analytic': float(analytic)}
    if analytic <= 0.0:
        out['unpriced'] = True
    return out


def resolve_constants(rec: dict, *, overrides: dict, analytic_pick: dict,
                      analytic_put: float) -> dict:
    """Every constant the derivation needs, resolved against `rec`.

    `overrides` is `{'s_pick_store', 's_pick_ff', 's_put'}` (None = no override -- the
    three CALIBRATION_KEYS of the staffing inputs); `analytic_pick` maps a channel name to
    its stage-A `seconds_per_unit` (absent channels omitted); `analytic_put` is the site's
    analytic put seconds per unit (from the script, stage B) -- zero when the seed has to
    be resolved before stage B, in which case the caller re-resolves `s_put` once it has it.
    Returns `{'s_pick': {channel: constant}, 's_put': constant, 'k_max': {channel: int|None},
    'record': <provenance stub>}`.
    """
    consts = rec['constants']
    key_for = {'store': 's_pick_store', 'fulfillment': 's_pick_ff'}
    s_pick = {}
    for ch in _CHANNELS:
        if ch not in analytic_pick:
            continue
        s_pick[ch] = resolve_constant(consts['s_pick'][ch],
                                      override=overrides.get(key_for[ch]),
                                      analytic=float(analytic_pick[ch]), name=key_for[ch])
    s_put = resolve_constant(consts['s_put'], override=overrides.get('s_put'),
                             analytic=float(analytic_put), name='s_put')
    return {
        's_pick': s_pick,
        's_put': s_put,
        'k_max': {ch: (consts.get('k_max') or {}).get(ch) for ch in _CHANNELS},
        'record': record_stub(rec),
    }


def record_stub(rec: dict) -> dict:
    """The provenance a run copies from the record: enough to find the record again and to
    tell which pass it was, never the constants themselves (those ride `resolve_constants`)."""
    return {
        'path': os.path.relpath(rec.get('_path', RECORD_PATH),
                                os.path.dirname(os.path.dirname(os.path.dirname(
                                    os.path.abspath(__file__))))).replace(os.sep, '/'),
        'pass': rec.get('pass'),
        'created': rec.get('created'),
        'commit': rec.get('commit'),
        'run_root': rec.get('run_root'),
        'warehouse_fingerprint': rec.get('warehouse_fingerprint'),
        'measured': is_measured(rec),
    }


def staleness(rec: dict, warehouse_fingerprint: str | None) -> dict:
    """The two stamps the staffing record carries about the record it ran under.

    `calibration_stale` is True only when the record NAMES a fingerprint and the run's
    differs; `calibration_measured` says whether there was a measurement to be stale
    against at all.  Both False is the seed on any catalogue; False/True is a measured
    record on its own catalogue; True/True is the warning case.
    """
    rec_fp = rec.get('warehouse_fingerprint')
    measured = is_measured(rec)
    stale = bool(rec_fp) and (warehouse_fingerprint != rec_fp)
    return {'calibration_stale': stale, 'calibration_measured': measured,
            'record_fingerprint': rec_fp, 'run_fingerprint': warehouse_fingerprint}
