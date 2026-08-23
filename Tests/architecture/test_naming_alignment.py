"""test_naming_alignment.py — module names, eval keys, and broker declarations stay aligned.

The 2026-08 audit found five modules whose file name, evaluation key, and output filenames
had all drifted apart, plus two evaluations consuming resources their `needs=` never
declared.  The renames fixed the state; THIS file freezes the rules so it cannot re-rot:

  R1  an evaluation's key namespace == the package directory its module lives in
      (two documented aliases, both because the key is typed at the CLI while the directory
      spells the concept out: `agg.*` -> aggregate/, `sig.*` -> significance/);
  R2  an evaluation's key tail == its module's basename, unless the key is in the
      documented-exception map (the *.by_initial twins, co-registered with their flat twin);
  R3  a render may only touch broker resources its registration declares (the A4/A5 bug
      class: needs=('task',) while calling ctx.series()).  One-directional on purpose —
      over-declaring is style, under-declaring makes the access log lie.

The former R3 — an enumeration of keys whose output stem could not equal the key — retired
with the chart-family redesign.  Output filenames are no longer named after their key at all:
the family grammar composes them as `<view>_<stem>`, `chartkit.Chart.save` refuses a filename
whose prefix contradicts the declared view, and the figure registry's stem-in-writer-source
test ties every published name back to the module that mints it.  That is a positive check
where the enumeration only ever asserted its own freshness.

All checks are AST/text-based on the module sources (the registry pattern from
test_registry_discovery), so this file needs no matplotlib import to run.

Run:  python -m pytest Tests/architecture/test_naming_alignment.py -q
"""
from __future__ import annotations

import ast
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_PKG = os.path.join(_ROOT, 'Optimization', 'Performance_Evaluations')

#: key namespace -> package dir, where they differ.  Keep this SHORT: both entries exist for
#: the same reason — the key is typed at the CLI (--set agg.tables..., --set sig.suite...)
#: while the directory spells the concept out for a reader browsing the package.
_NS_ALIAS = {'agg': 'aggregate', 'sig': 'significance'}

#: R2 exceptions — keys whose tail deliberately differs from the module basename.
_KEY_EXCEPTIONS = {
    'sig.by_initial': 'the by-initial fork is a structural twin co-registered in its flat '
                      'twin\'s module (significance/suite.py); splitting the file would '
                      'duplicate the panel-building helpers',
    'tables.stats': 'the stats CSV writers share one module with their by-initial twin; the '
                    'module is named for the artifact class it writes, not for either key',
    'tables.by_initial': 'same module as tables.stats — the shared statistics computation '
                         'feeds both forks and the significance panels',
}

#: R3 — source tokens -> the broker resource they consume.  ctx.maxb/ss_lo are derived
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
    for name, table in (('_KEY_EXCEPTIONS', _KEY_EXCEPTIONS),):
        stale = set(table) - _ALL_KEYS
        assert not stale, f'{name} names unregistered key(s): {sorted(stale)}'
        empty = [k for k, reason in table.items() if not reason.strip()]
        assert not empty, f'{name} entries need a real reason: {empty}'


def test_renders_touch_only_declared_resources():
    """R3 — used ⊆ union of the module's declared needs.  Union, because two-eval modules
    (the stats CSV pair, the significance pair) share helpers within one file."""
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
