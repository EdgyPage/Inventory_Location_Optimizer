"""test_knob_registry.py — one declaration per knob, and every seam derived from it.

A knob used to be one fact written down in six places: a settings constant, a CONFIG key, an
`add_argument`, a write-back line in `run_simulation.main`, a `run_spec.json` entry, and TWO
restore sites — `run_simulation._apply_run_spec` for a resume and `run_analysis._apply_run_shape`
for a standalone re-analysis, which that file calls "the sixth seam" in as many words because
an analysis worker is spawned too and re-imports pristine defaults.

Every omission was silent and lasted a whole run.  `put_swap_coef` lost its restore once and
all 1,534 tests stayed green.

`sim_config.KNOBS` is now the declaration, and the write-back, the record and the resume
restore are derived from it.  What this file pins is that nothing has drifted back out:

    1. The registry covers CONFIG['global'] exactly — no key undeclared, none invented.
    2. Both family tuples are derived, so a new inbound or staffing knob cannot miss one.
    3. Every recorded knob is restored by BOTH restore sites, or is named in
       `ANALYSIS_EXEMPT` with a reason.  This is the one that could ship unnoticed.
    4. Every knob with a CLI flag actually has one, and every `apply='parent'` knob has none.

    python -m pytest Tests/unit/test_knob_registry.py -q
"""
from __future__ import annotations

import re
import pathlib

from Optimization.config.sim_config import (
    CONFIG, KNOBS, KNOB_BY_NAME, SPEC_KNOB_NAMES, ANALYSIS_EXEMPT,
    STAFFING_KEYS, INBOUND_KEYS, apply_cli_overrides, run_spec_record)

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _source(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding='utf-8')


# ── the registry against CONFIG ───────────────────────────────────────────────

def test_the_registry_covers_config_global_exactly():
    """A key in CONFIG with no Knob is a knob no loop will carry; a Knob with no CONFIG key
    writes into a dict nothing reads."""
    declared = {k.name for k in KNOBS}
    actual = set(CONFIG['global'])
    assert declared - actual == set(), f'declared but absent from CONFIG: {sorted(declared - actual)}'
    assert actual - declared == set(), f'in CONFIG but undeclared: {sorted(actual - declared)}'


def test_knob_names_are_unique():
    names = [k.name for k in KNOBS]
    assert len(names) == len(set(names)), 'a knob is declared twice'
    assert set(KNOB_BY_NAME) == set(names)


def test_the_two_family_tuples_are_derived():
    assert STAFFING_KEYS == tuple(k.name for k in KNOBS if k.family == 'staffing')
    assert INBOUND_KEYS == tuple(k.name for k in KNOBS if k.family == 'inbound')
    assert STAFFING_KEYS and INBOUND_KEYS, 'a family went empty — the derivation is broken'


def test_every_field_takes_a_declared_value():
    for k in KNOBS:
        assert k.apply in ('always', 'if_set', 'parent'), f'{k.name}: apply={k.apply!r}'
        assert k.coerce in (None, 'bool', 'or_one'), f'{k.name}: coerce={k.coerce!r}'
        assert k.spec_from in (None, 'config', 'args'), f'{k.name}: spec_from={k.spec_from!r}'


# ── THE ONE THAT COULD SHIP UNNOTICED ─────────────────────────────────────────

def test_every_recorded_knob_is_restored_on_resume():
    """`_apply_run_spec` splices `SPEC_KNOB_NAMES`, so this is structural — the test states
    the property rather than re-deriving it, and fails loudly if the splice is edited out."""
    src = _source('Optimization/run_simulation.py')
    body = src[src.index('def _apply_run_spec('):src.index('def ', src.index('def _apply_run_spec(') + 10)]
    assert '*SPEC_KNOB_NAMES' in body, (
        'the resume restore no longer splices the registry — a recorded knob can now be '
        'dropped from the restore with nothing to say so')


def test_every_recorded_knob_is_restored_by_the_analysis_seam_or_exempted():
    """THE SIXTH SEAM.  `_apply_run_shape` is NOT derived from the registry — every key there
    carries a bespoke absence rule for what a pre-field spec means — so the registry guards
    the SET instead: a recorded knob that is neither restored nor exempted fails here."""
    src = _source('Optimization/run_analysis.py')
    i = src.index('def _apply_run_shape(')
    body = src[i:src.index("return spec.get('max_skus')", i)]

    restored = set(re.findall(r"g\['([a-z_0-9]+)'\]", body))
    restored |= set(re.findall(r"'([a-z_0-9]+)'", body))
    if 'STAFFING_KEYS' in body:
        restored |= set(STAFFING_KEYS)
    if 'INBOUND_KEYS' in body:
        restored |= set(INBOUND_KEYS)

    missing = [k for k in SPEC_KNOB_NAMES if k not in restored and k not in ANALYSIS_EXEMPT]
    assert not missing, (
        f'recorded but neither restored by _apply_run_shape nor named in ANALYSIS_EXEMPT: '
        f'{missing}. A standalone re-analysis will read this checkout\'s value instead of the '
        f'run\'s — silently, and for the whole analysis.')


def test_every_exemption_names_a_recorded_knob_and_gives_a_reason():
    """An exemption for a knob that is not recorded is stale; a blank reason is not one."""
    for name, why in ANALYSIS_EXEMPT.items():
        assert name in KNOB_BY_NAME, f'{name!r} is exempted but is not a knob'
        assert name in SPEC_KNOB_NAMES, f'{name!r} is exempted but is not recorded anyway'
        assert len(why) > 40, f'{name!r}: the exemption reason is too thin to be one'


# ── the derived loops behave ──────────────────────────────────────────────────

class _Args:
    def __init__(self, **kw):
        for k in KNOB_BY_NAME:
            setattr(self, k, None)
        for k, v in kw.items():
            setattr(self, k, v)


def test_write_back_respects_if_set_and_never_truthiness():
    """`--n-batches 0` was silently discarded once, because the guard was truthiness."""
    before = dict(CONFIG['global'])
    try:
        apply_cli_overrides(_Args(n_batches=0))
        assert CONFIG['global']['n_batches'] == 0, 'a zero was discarded again'
        CONFIG['global'].update(before)
        apply_cli_overrides(_Args())          # n_batches unset
        assert CONFIG['global']['n_batches'] == before['n_batches'], (
            'an unset if_set knob overwrote CONFIG with None')
    finally:
        CONFIG['global'].clear()
        CONFIG['global'].update(before)


def test_write_back_coerces_and_skips_parent_only_knobs():
    before = dict(CONFIG['global'])
    try:
        apply_cli_overrides(_Args(cut_at_day_end=1, workers=None, shift_seconds='IGNORED'))
        assert CONFIG['global']['cut_at_day_end'] is True, 'store_true knob not coerced to bool'
        assert CONFIG['global']['workers'] == 1, 'workers did not take its or-one default'
        assert CONFIG['global']['shift_seconds'] == before['shift_seconds'], (
            'a parent-only knob was written back from args')
    finally:
        CONFIG['global'].clear()
        CONFIG['global'].update(before)


def test_run_spec_record_reads_each_knob_from_its_declared_source():
    """`workers` resolves to 1 in CONFIG and must still be recorded as TYPED, or a resume of
    a flag-less run would be pinned to one process."""
    before = dict(CONFIG['global'])
    try:
        apply_cli_overrides(_Args(workers=None, n_batches=7))
        rec = run_spec_record(_Args(workers=None, n_batches=7))
        assert CONFIG['global']['workers'] == 1
        assert rec['workers'] is None, 'workers was recorded resolved instead of as typed'
        assert rec['n_batches'] == 7, 'a config-sourced knob was not read from CONFIG'
        assert set(rec) == set(SPEC_KNOB_NAMES), 'the record and SPEC_KNOB_NAMES disagree'
    finally:
        CONFIG['global'].clear()
        CONFIG['global'].update(before)
