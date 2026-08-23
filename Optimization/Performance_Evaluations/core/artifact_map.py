"""The output map — which directories each evaluation's outputs land in, derived, not retyped.

Two sources, cross-checked against each other and against the run-tree contract:

  * every `@evaluation`'s `out_subdir=` declaration (core/registry.py) — the dormant field,
    now VALIDATED: `io._save_close` warns when a figure lands outside its evaluation's
    declared subdir, and `findings()` fails the consistency test when a declaration
    contradicts the contract;
  * the HEAD run-tree contract's `evaluation:` attributions (Optimization/runschema/schema.py)
    — the CSV/JSON artifacts each evaluation owns, with their full path templates.

Derived, memoised lazily (the registry is populated by Performance_Evaluations.import_all(),
so nothing here may run at import time).  `config_dirs()` replaces the hand-typed literals in
`driver.prepare_config_dirs` with a rule instead of a list:

    a top-level subdir is PRE-WIPED by the parent iff two or more evaluations write under it
    (no single owner may wipe a shared dir — the worker-race rule the driver docstring states);
    a subdir with exactly one owner is that evaluation's own to wipe (`_fresh_dir` in its
    render), which is what stats/ and stats_by_initial/ have always done.
"""
from __future__ import annotations

import posixpath

from Optimization.Performance_Evaluations.core.registry import EVALUATIONS

_MEMO: dict = {}


# ── contract side: evaluation -> owned artifact templates ────────────────────────

def owned_artifacts() -> dict:
    """{eval_key: ((artifact_name, path_template), ...)} from the HEAD run-tree contract's
    unhashed `evaluation:` attributions.  Artifacts with no attribution belong to no
    evaluation (sim writers, run-root docs) and do not appear."""
    if 'owned' not in _MEMO:
        from Optimization.runschema import contract
        doc = contract.load(contract.head()) or contract.build()
        out: dict = {}
        for name, art in sorted(doc['artifacts'].items()):
            ev = art.get('evaluation')
            if ev:
                out.setdefault(ev, []).append((name, art['path']))
        _MEMO['owned'] = {k: tuple(v) for k, v in out.items()}
    return _MEMO['owned']


def _rel_dir(template: str) -> str:
    """A template's directory relative to its stage root — the segments after the tree levels
    (`{cell}/{pair}/{config}/{channel?}` per-leaf, `{cell}/_aggregate/{config}/{channel?}`
    aggregate, `_dossier/...` run).  Level placeholders are exactly the `{...}`-braced
    segments; `{initial_group}` is NOT a level (it is a subdir the eval creates), so braced
    segments only strip from the FRONT.

    A leading `_`-prefixed segment is a RESERVED STAGE ROOT, not an output subdir: the
    contract uses that prefix for exactly these (`_aggregate`, `_dossier`, `_frozen`) and
    the run tree reserves it, so matching the prefix is the rule rather than listing the
    names.  This read `parts[0] == '_aggregate'` when `_aggregate` was the only one."""
    parts = template.split('/')[:-1]                       # drop the basename
    while parts and (parts[0].startswith('{') or parts[0].startswith('_')):
        parts = parts[1:]
    return '/'.join(parts)


# ── the cross-check ──────────────────────────────────────────────────────────────

def findings() -> list:
    """Inconsistencies between `out_subdir=` declarations and the contract's attributions.
    Empty when honest; the consistency test asserts exactly that."""
    out = []
    by_key = {ev.key: ev for ev in EVALUATIONS}
    for key, arts in owned_artifacts().items():
        ev = by_key.get(key)
        if ev is None:
            out.append(f'contract attributes {[n for n, _ in arts]} to unregistered '
                       f'evaluation {key!r}')
            continue
        subs = ((ev.out_subdir,) if isinstance(ev.out_subdir, str) else ev.out_subdir)
        for name, template in arts:
            rel = _rel_dir(template)
            # A root-level doc (series.json, batches_long.csv) is always legitimate; a nested
            # one must sit under one of the declared out_subdirs or the declaration is a lie.
            if rel and ev.out_subdir and not any(
                    rel == s or rel.startswith(s + '/') for s in subs):
                out.append(f'{key}: artifact {name!r} lands in {rel!r} but out_subdir '
                           f'declares {ev.out_subdir!r}')
            if rel and not ev.out_subdir:
                out.append(f'{key}: artifact {name!r} lands in {rel!r} but out_subdir '
                           f'declares the leaf root')
    return out


# ── the save-time map (figures go through io._save_close) ────────────────────────

def figure_subdir(eval_key: str):
    """The subdir(s) (relative to the context root) evaluation `eval_key`'s FIGURES belong in
    — its `out_subdir` verbatim: a str, '' meaning the root itself, a TUPLE for a declared
    multi-dir owner (agg.cross_profile), None for an unregistered key."""
    if 'subdir' not in _MEMO:
        _MEMO['subdir'] = {ev.key: ev.out_subdir for ev in EVALUATIONS}
    return _MEMO['subdir'].get(eval_key)


def save_in_bounds(eval_key: str, save_dir: str) -> bool:
    """Is a figure save into `save_dir` consistent with `eval_key`'s declared out_subdir?

    True when ANY declared subdir's segments appear as a contiguous run in the save path —
    checked WITHOUT knowing the context root, because `_save_close` only sees the path.  An
    eval declaring the root ('') makes no checkable claim; unknown evals are not this
    function's problem (the driver only sets registered keys)."""
    sub = figure_subdir(eval_key)
    if not sub:
        return True
    have = posixpath.normpath(save_dir.replace('\\', '/')).split('/')
    for member in ((sub,) if isinstance(sub, str) else sub):
        want = member.split('/')
        if any(have[i:i + len(want)] == want
               for i in range(len(have) - len(want) + 1)):
            return True
    return False


# ── the derived prepare-list (driver.prepare_config_dirs) ────────────────────────

def config_dirs() -> tuple:
    """(wipe_tops, nested_creates) for the parent pre-pass over one config leaf.

    wipe_tops: top-level out_subdir segments shared by >= 2 config-stage evaluations —
    pre-wiped once by the parent so no worker races.  nested_creates: every declared
    multi-level out_subdir under a wiped top, created empty after the wipe (workers only ever
    write files into them).  Single-owner tops (stats/, stats_by_initial/) are absent: their
    owner wipes its own leaf, as the driver docstring has always promised."""
    if 'config_dirs' not in _MEMO:
        owners: dict = {}
        for ev in EVALUATIONS:
            # tuple declarations only occur on aggregate scope today; the isinstance guard
            # keeps this derivation honest if one ever appears on a config-stage eval.
            if (ev.scope in ('per_strategy', 'config') and ev.out_subdir
                    and isinstance(ev.out_subdir, str)):
                owners.setdefault(ev.out_subdir.split('/')[0], set()).add(ev.key)
        tops = tuple(sorted(t for t, ks in owners.items() if len(ks) >= 2))
        nested = tuple(sorted({ev.out_subdir for ev in EVALUATIONS
                               if ev.scope in ('per_strategy', 'config')
                               and isinstance(ev.out_subdir, str)
                               and '/' in ev.out_subdir
                               and ev.out_subdir.split('/')[0] in tops}))
        _MEMO['config_dirs'] = (tops, nested)
    return _MEMO['config_dirs']


def run_dirs() -> tuple:
    """Every declared out_subdir of a RUN-scope evaluation, deepest-safe creation order.

    The config stage needs the shared-top/single-owner distinction because its evaluations
    run in a worker pool and two owners must not race to wipe one directory.  Run scope has
    no such hazard — `analyze_run` executes these serially in the parent, after every cell
    is finished — so the whole `_dossier/` root is wiped once and every declared subdir is
    recreated, with no ownership rule to get wrong.
    """
    if 'run_dirs' not in _MEMO:
        subs: set = set()
        for ev in EVALUATIONS:
            if ev.scope != 'run':
                continue
            # A tuple declaration is a multi-dir owner; both members are real directories
            # and both must exist before its render writes.  Flattening here is why
            # `prepare_run_dir` needs no knowledge of which evals declare tuples.
            for s in ((ev.out_subdir,) if isinstance(ev.out_subdir, str) else ev.out_subdir):
                if s:
                    subs.add(s)
        _MEMO['run_dirs'] = tuple(sorted(subs))
    return _MEMO['run_dirs']
