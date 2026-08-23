"""test_figure_registry.py — the figure registry stays whole, inert, and tied to its writers.

docs/experiments/figures.yml is the single declaration behind three consumers that used to
drift independently (ingest's DEFAULT_* staging lists, each experiment.yml's figure lists,
macros' caption tables).  These tests make the collapse provably inert (the derived defaults
byte-equal the historical literals), tie every entry to the code that writes the figure (the
param_frequency silent-drift class fails CI now), and keep every committed experiment.yml
inside the registry vocabulary.

Run:  python -m pytest Tests/architecture/test_figure_registry.py -q
"""
from __future__ import annotations

import importlib.util
import os
import re

import pytest

yaml = pytest.importorskip('yaml')

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_EXP = os.path.join(_ROOT, 'docs', 'experiments')


def _registry():
    with open(os.path.join(_EXP, 'figures.yml'), encoding='utf-8') as fh:
        return yaml.safe_load(fh)['figures']


def _src(relpath):
    with open(os.path.join(_ROOT, *relpath.split('/')), encoding='utf-8') as fh:
        return fh.read()


# ── shape ────────────────────────────────────────────────────────────────────────

def test_registry_parses_names_unique_sections_valid_captions_present():
    figs = _registry()
    names = [f['name'] for f in figs]
    assert len(names) == len(set(names)), 'duplicate figure names'
    assert {f['section'] for f in figs} <= {'top3', 'full_suite', 'inventory'}
    for f in figs:
        if f['section'] != 'inventory':
            assert (f.get('caption') or '').strip(), f"{f['name']}: reader-facing figures " \
                                                     f"need a caption"
            assert f.get('eval'), f"{f['name']}: analysis figures need an owning eval key"
            if f.get('retired'):
                # a retired figure exists only for archived snapshots — it must never be
                # offered to a new experiment as a default going forward, EXCEPT the frozen
                # historical defaults that legacy-mode Experiment 1 still renders.
                assert f['name'] in (_HISTORICAL['top3'] + _HISTORICAL['full_suite']) \
                    or not f.get('default'), \
                    f"{f['name']}: retired figures cannot join the default lists"


# ── the migration golden pin: derived defaults == the historical literals ────────

_HISTORICAL = {
    'top3': ['top3_by_initial_prodtime_cum_improvement.png',
             'top3_by_initial_production_time_over_time.png',
             'top3_by_initial_prodtime_delta_trend.png',
             'top_vs_baseline_table.png',
             'top_vs_baseline.png'],
    'full_suite': ['task_duration_by_strategy.png',
                   'production_time_over_time.png'],
    'inventory': ['group_sizes.png', 'demand.png', 'param_relative_frequency.png',
                  'param_quantity.png', 'equilibrium_qty.png'],
}


def test_legacy_lists_byte_equal_the_historical_literals():
    """What a no-manifest experiment renders is EXACTLY what the old hardcoded lists said —
    order included.  Experiment 1 is the only such experiment and its sweep predates every
    figure since, so this set is frozen: it may shrink if a figure leaves the repo entirely,
    never grow."""
    figs = _registry()
    for section, want in _HISTORICAL.items():
        got = [f['name'] for f in figs if f['section'] == section and f.get('legacy')]
        assert got == want, section


def _ingest():
    spec = importlib.util.spec_from_file_location(
        'docs_ingest', os.path.join(_EXP, 'ingest.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ingest_derives_the_same_legacy_lists():
    ingest = _ingest()
    assert ingest.LEGACY_TOP3 == _HISTORICAL['top3']
    assert ingest.LEGACY_FULL_SUITE == _HISTORICAL['full_suite']
    assert ingest.LEGACY_INVENTORY_PLOTS == _HISTORICAL['inventory']


def test_the_starter_defaults_are_live_figures_only():
    """A scaffolded manifest must name figures a run can actually produce.  Handing a new
    experiment a retired name costs nothing but a MISSING line in an ingest log — which is
    precisely the silent drift this registry exists to end."""
    figs = _registry()
    starters = [f for f in figs if f.get('default')]
    assert starters, 'no starter defaults — a new experiment would scaffold an empty manifest'
    retired = [f['name'] for f in starters if f.get('retired')]
    assert not retired, f'retired figure(s) in the starter lists: {retired}'
    ingest = _ingest()
    for section, got in (('top3', ingest.DEFAULT_TOP3),
                         ('full_suite', ingest.DEFAULT_FULL_SUITE),
                         ('inventory', ingest.DEFAULT_INVENTORY_PLOTS)):
        want = [f['name'] for f in starters if f['section'] == section]
        assert got == want, section


# ── every eval key is registered; every stem traces to its writer ────────────────

def test_every_eval_key_is_registered():
    """Retired entries are exempt: their eval keys document the HISTORICAL owner (the
    chart-family redesign removed those registrations), and the entries persist only so
    archived experiment snapshots keep resolving."""
    from Optimization import Performance_Evaluations  # noqa: F401
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
    bad = [f['name'] for f in _registry()
           if f.get('eval') and not f.get('retired') and f['eval'] not in EVAL_BY_KEY]
    assert not bad, f'registry names unregistered eval key(s) on: {bad}'


_TAG_PREFIX = re.compile(r'^top\d+(_by_\w+)?_')          # legacy names: tag led the stem
_TAG_SUFFIX = re.compile(r'_top\d+(_by_\w+)?$')          # family names: view leads, tag trails
#: The view prefix is minted by the family GRAMMAR (chartkit composes `<view>_<stem>` and
#: refuses a filename that contradicts the declared view), so a writer never spells it.
#: Strip it before looking for the stem, exactly as the top-tag is stripped.
_VIEW_PREFIX = re.compile(r'^(absolute|percent|delta|effect|table)_')

#: eval key -> module path; stems produced by the shared metric specs resolve via painters.
_PAINTERS = 'Optimization/Performance_Evaluations/common/painters.py'
_GENERATOR = 'Warehouse/generation/generate_inventory.py'


def test_every_figure_stem_appears_in_its_writer_source():
    """THE anti-drift tie: strip the top-tag prefix and the extension; the remaining stem
    must appear verbatim in the owning eval's module (or common/painters.py for the shared
    over-time stems), or — for generator-owned inventory plots — in generate_inventory.py
    (minus the composed 'param_' prefix).  This is the check that would have caught
    param_frequency going stale the day the generator renamed it."""
    from Optimization import Performance_Evaluations  # noqa: F401
    from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
    import inspect

    misses = []
    for f in _registry():
        if f.get('retired'):
            continue                    # the writer no longer exists, by declaration
        stem = _TAG_SUFFIX.sub('', _VIEW_PREFIX.sub(
            '', _TAG_PREFIX.sub('', f['name'])[:-len('.png')]))
        if f.get('eval'):
            mod_src = inspect.getsource(inspect.getmodule(EVAL_BY_KEY[f['eval']].render))
            if stem not in mod_src and stem not in _src(_PAINTERS):
                misses.append(f"{f['name']}: stem {stem!r} not in {f['eval']}'s module "
                              f'or painters')
        else:
            gen = _src(_GENERATOR)
            bare = stem[len('param_'):] if stem.startswith('param_') else stem
            if bare not in gen:
                misses.append(f"{f['name']}: stem {bare!r} not in {_GENERATOR}")
    assert not misses, '\n'.join(misses)


# ── committed manifests stay inside the vocabulary ───────────────────────────────

def test_every_committed_manifest_figure_is_in_the_registry():
    """A stale name in an experiment.yml used to log MISSING at ingest time and stage
    nothing; now it fails here, in CI."""
    registered = {f['name'] for f in _registry()}
    bad = []
    for exp in sorted(os.listdir(_EXP)):
        yml = os.path.join(_EXP, exp, 'experiment.yml')
        if not os.path.isfile(yml):
            continue
        with open(yml, encoding='utf-8') as fh:
            m = yaml.safe_load(fh) or {}
        names = [n for kind in ('top3', 'full_suite')
                 for n in (m.get('figures') or {}).get(kind, [])]
        names += (m.get('inventory_plots') or [])
        bad += [f'{exp}: {n}' for n in names if n not in registered]
    assert not bad, 'experiment.yml figure(s) missing from figures.yml:\n' + '\n'.join(bad)


# ── the old triplication stays dead ──────────────────────────────────────────────

def test_macros_no_longer_carries_caption_tables():
    src = _src('docs/macros.py')
    for token in ('_EXTRA_CAPTIONS', '_FULL_SUITE_FIGURES'):
        assert token not in src, f'{token} resurfaced in macros.py — captions live in ' \
                                 f'figures.yml now'
