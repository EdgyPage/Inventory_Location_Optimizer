"""test_naming_alignment.py — module names, eval keys, and broker declarations stay aligned.

The 2026-08 audit found five modules whose file name, evaluation key, and output filenames
had all drifted apart, plus two evaluations consuming resources their `needs=` never
declared.  The renames fixed the state; THIS file freezes the rules so it cannot re-rot:

  R1  an evaluation's key namespace == the package directory its module lives in
      (one documented alias: the `agg.*` keys live in `aggregate/`);
  R2  an evaluation's key tail == its module's basename, unless the key is in the
      documented-exception map (the *.by_initial twins, co-registered with their flat twin);
  R3  keys whose primary OUTPUT stem cannot equal the key are enumerated with reasons —
      deleting or renaming such a module retires its entry, so the map cannot go stale;
  R4  a render may only touch broker resources its registration declares (the A4/A5 bug
      class: needs=('task',) while calling ctx.series()).  One-directional on purpose —
      over-declaring is style, under-declaring makes the access log lie.

All checks are AST/text-based on the module sources (the registry pattern from
test_registry_discovery), so this file needs no matplotlib import to run.

Run:  python -m pytest Tests/architecture/test_naming_alignment.py -q
"""
from __future__ import annotations

import ast
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_PKG = os.path.join(_ROOT, 'Optimization', 'Performance_Evaluations')

#: key namespace -> package dir, where they differ.  Keep this to ONE entry: `agg` is kept
#: short because the keys are typed at the CLI (--set agg.stats...) while the directory
#: matches the scope name 'aggregate'.
_NS_ALIAS = {'agg': 'aggregate'}

#: R2 exceptions — keys whose tail deliberately differs from the module basename.
_KEY_EXCEPTIONS = {
    'stats.by_initial': 'the by-initial suite is a structural fork co-registered in its '
                        'flat twin\'s module (stats/suite.py); splitting the file would '
                        'duplicate _run_config_stats',
    'agg.stats_by_initial': 'same pattern as stats.by_initial, in aggregate/stats.py',
}

#: R3 — keys whose output filenames cannot anchor the name, with the reason on record.
_OUTPUT_STEM_EXCEPTIONS = {
    'compare.faceted': 'emits the five shared over-time metric stems; the LAYOUT (faceted '
                       'vs overlay) is the differentiator and lives in the directory name',
    'compare.overlay': 'twin of compare.faceted — same five stems, different layout',
    'compare.top_metric': 'metric-family module: {tag}_{metric}_over_time.png per metric',
    'compare.delta_over_time': 'multi-output pair sharing the prodtime_ stem '
                               '(cum_improvement + delta_trend), both committed',
    'config.summary_csv': 'multi-output summary_ family (summary_batch/summary_task)',
    'per_strategy.report_bars': 'multi-output + contract-attributed (batches_long, '
                                'per_run_summary, *_per_run PNGs)',
    'per_strategy.metric_grids': 'grid_ family module, one figure per metric',
    'stats.suite': 'emits the {metric}_{dist,pmatrix,effect,rank}.png suite',
    'stats.by_initial': 'nested per-fn copies of the stats.suite output set',
    'agg.stats': 'aggregate twin of stats.suite',
    'agg.stats_by_initial': 'aggregate twin of stats.by_initial',
    'agg.cross_profile': 'suite-mirror: re-emits the compare basenames into the aggregate '
                         'tree, where the _aggregate/ dir is the differentiator',
}

#: R4 — source tokens -> the broker resource they consume.  ctx.maxb/ss_lo are derived
#: scalars over already-granted frames, and non-broker context fields (run_dir, strategies,
#: optimal, ...) are free.
_RESOURCE_TOKENS = {
    'ctx.batch_df(': 'batch', 'ctx.batch_frames(': 'batch',
    'ctx.task_df(': 'task', 'ctx.task_frames(': 'task',
    'ctx.series(': 'series',
    'ctx.breakdown(': 'breakdown',
    'ctx.agg_series(': 'series', 'ctx.profile_series_list': 'series',
}


def _eval_modules():
    """[(relpath, src, [(key, needs), ...])] for every module with an @evaluation."""
    out = []
    for dirpath, dirs, files in os.walk(_PKG):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for fn in sorted(files):
            if not fn.endswith('.py'):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding='utf-8') as fh:
                src = fh.read()
            if '@evaluation(' not in src:
                continue
            regs = []
            for node in ast.walk(ast.parse(src)):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'evaluation'):
                    kw = {k.arg: k.value for k in node.keywords}
                    key = kw['key'].value
                    needs = tuple(e.value for e in kw['needs'].elts) if 'needs' in kw else ()
                    regs.append((key, needs))
            rel = os.path.relpath(path, _PKG).replace(os.sep, '/')
            out.append((rel, src, regs))
    return out


_MODULES = _eval_modules()
_ALL_KEYS = {key for _rel, _src, regs in _MODULES for key, _needs in regs}


def test_the_scan_still_sees_the_registry():
    assert len(_ALL_KEYS) >= 20, 'the AST scan found almost nothing — its pattern rotted'


def test_key_namespace_matches_package_dir():
    bad = []
    for rel, _src, regs in _MODULES:
        pkg = rel.split('/')[0]
        for key, _needs in regs:
            ns = key.split('.')[0]
            if _NS_ALIAS.get(ns, ns) != pkg:
                bad.append(f'{rel}: key {key!r} (namespace {ns!r}) lives in package {pkg!r}')
    assert not bad, '\n'.join(bad)


def test_key_tail_matches_module_basename():
    bad = []
    for rel, _src, regs in _MODULES:
        basename = rel.split('/')[-1][:-3]
        for key, _needs in regs:
            tail = key.split('.', 1)[1]
            if tail != basename and key not in _KEY_EXCEPTIONS:
                bad.append(f'{rel}: key {key!r} tail != basename {basename!r} and not in '
                           f'_KEY_EXCEPTIONS')
    assert not bad, '\n'.join(bad)


def test_exception_maps_cannot_go_stale():
    for name, table in (('_KEY_EXCEPTIONS', _KEY_EXCEPTIONS),
                        ('_OUTPUT_STEM_EXCEPTIONS', _OUTPUT_STEM_EXCEPTIONS)):
        stale = set(table) - _ALL_KEYS
        assert not stale, f'{name} names unregistered key(s): {sorted(stale)}'
        empty = [k for k, reason in table.items() if not reason.strip()]
        assert not empty, f'{name} entries need a real reason: {empty}'


def test_renders_touch_only_declared_resources():
    """R4 — used ⊆ union of the module's declared needs.  Union, because two-eval modules
    (stats/suite.py, aggregate/stats.py) share helpers within one file."""
    bad = []
    for rel, src, regs in _MODULES:
        declared = {n for _key, needs in regs for n in needs}
        used = {res for tok, res in _RESOURCE_TOKENS.items() if tok in src}
        extra = used - declared
        if extra:
            keys = ', '.join(k for k, _n in regs)
            bad.append(f'{rel} ({keys}): consumes {sorted(extra)} beyond declared '
                       f'{sorted(declared)} — the access log would lie')
    assert not bad, '\n'.join(bad)
