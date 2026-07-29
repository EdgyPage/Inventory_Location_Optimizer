"""runschema.resolver — ONE generic resolver that interprets ANY run-tree contract document.

There is deliberately no per-schema Python.  A contract is pure data (levels + path templates), so
this class reads it the way a template engine reads a template: change the tree by editing
``schema.py``, not by writing a new resolver.  That is what makes a content-addressed schema id
workable — the id names a *document*, and this module is the single interpreter of all of them.

Everything that used to be hardcoded here now comes from the contract:

  * the frozen-vs-cell-local planned inventory fallback  -> ``resolves_via`` (first existing wins)
  * ``sim_<arm>.db`` -> arm                              -> inverse-render of the ``{strategy}``
                                                            placeholder in the sim_db template
  * the cross-cell what-if artifact list                 -> ``group: 'whatif'``
  * the ``_aggregate`` literal                           -> the ``aggregate_dir`` template

Tree TRAVERSAL still belongs to ``Optimization/runlayout.py`` — this class delegates to it and adds
the cell level, optional-segment handling, and the axis inventory the viewer navigates by.

Feature negotiation replaces a version number: a contract lists the template vocabulary it uses and
``SUPPORTED_FEATURES`` says what this build implements, so an unreadable contract fails by NAMING
the missing feature instead of comparing integers.
"""
from __future__ import annotations

import glob as _glob
import os
import re
from typing import Iterator

from Optimization import runlayout
from Optimization.sim_manifest import read_run_layout

# The template vocabulary this build can interpret.  A contract naming anything outside this set is
# rejected by runschema.resolver_for with the missing feature spelled out.
SUPPORTED_FEATURES = frozenset({
    'optional-segments',    # `{name?}` — segment dropped, with its separator, when the part is None
    'globs',                # `*` within a segment, `**` across any depth
    'resolves-via',         # artifact = first EXISTING of an ordered candidate list
    'strategy-capture',     # `{strategy}` is invertible: a concrete path yields its arm
})


# ── template rendering ──────────────────────────────────────────────────────────

def render(template: str, **parts) -> str:
    """Render a contract path template to a relative path.

    `{name}` substitutes parts[name]; `{name?}` is an OPTIONAL segment that is dropped along with
    its separator when parts.get(name) is None.  Raises KeyError for a missing required part, so a
    typo fails loudly instead of producing a path with a literal brace in it.
    """
    out = []
    for seg in template.split('/'):
        if seg.startswith('{') and seg.endswith('?}'):
            val = parts.get(seg[1:-2])
            if val is None:
                continue                      # optional segment absent (e.g. store-only channel)
            out.append(str(val))
            continue
        out.append(seg.format(**parts) if '{' in seg else seg)
    return '/'.join(out)


def _capture_regex(template: str, placeholder: str) -> re.Pattern:
    """Compile a template into a matcher that CAPTURES one placeholder from a concrete path.

    Every other `{part}` becomes a wildcard; `{part?}` becomes an optional segment.  This is how the
    resolver inverts `…/sim_{strategy}.db` back to the arm name without hardcoding `len('sim_')`.
    """
    segs = []
    for seg in template.split('/'):
        opt = seg.startswith('{') and seg.endswith('?}')
        body = seg[1:-2] if opt else seg
        if opt:
            segs.append('(?:[^/]+/)?')
            continue
        pat = ''
        for tok in re.split(r'(\{\w+\})', body):
            if tok == '{%s}' % placeholder:
                pat += r'(?P<capture>.+)'
            elif tok.startswith('{') and tok.endswith('}'):
                pat += r'[^/]+'
            else:
                pat += re.escape(tok).replace(r'\*\*', '.*').replace(r'\*', '[^/]*')
        segs.append(pat + '/')
    return re.compile('^' + ''.join(segs)[:-1] + '$')


class RunTree:
    """Resolver for one run directory.  Construct via ``runschema.resolver_for(base_dir)``.

    Every accessor returns an ABSOLUTE path (or an iterator of them).  Existence is NOT implied —
    optional artifacts legitimately do not exist on some runs (see each artifact's `condition`), so
    callers check `os.path.exists` where it matters.
    """

    def __init__(self, base_dir: str, contract: dict, layout: dict | None = None):
        self.base = os.path.abspath(base_dir)
        self.contract = contract
        self.artifacts = contract['artifacts']
        self.layout = layout if layout is not None else (read_run_layout(self.base) or {})

    # ── identity ───────────────────────────────────────────────────────────────
    @property
    def schema_id(self) -> str:
        return self.contract['schema_id']

    @property
    def schema_short(self) -> str:
        """The 12-hex display form — what goes in logs, the viewer badge, and filenames."""
        return self.schema_id.split(':', 1)[-1][:12]

    def __repr__(self) -> str:                                    # pragma: no cover - debug aid
        return f'<RunTree {self.schema_short} {os.path.basename(self.base)}>'

    # ── generic ────────────────────────────────────────────────────────────────
    def path(self, artifact: str, **parts) -> str:
        """Absolute path of any contract artifact, e.g. path('sim_db', cell=…, pair=…, config=…,
        channel=None, strategy='uni_fifo_norsl').

        An artifact declaring `resolves_via` resolves to the first CANDIDATE THAT EXISTS; when none
        do, the last candidate is returned so the caller gets the canonical location to create.
        """
        spec = self.artifacts[artifact]
        via = spec.get('resolves_via')
        if via:
            last = None
            for cand in via:
                last = self.path(cand, **parts)
                if os.path.exists(last):
                    return last
            return last
        return os.path.join(self.base, render(spec['path'], **parts).replace('/', os.sep))

    def by_group(self, group: str) -> list[str]:
        """Artifact names tagged with `group` — lets callers ask for a family (e.g. every cross-cell
        what-if output) without hardcoding a name list that goes stale."""
        return sorted(k for k, v in self.artifacts.items() if v.get('group') == group)

    @property
    def is_sweep(self) -> bool:
        """True when the run has >1 cell — the condition that produces _frozen/ and the
        cross-cell what-if outputs."""
        return len(self.layout.get('cells') or ()) > 1

    # ── run root ───────────────────────────────────────────────────────────────
    def run_layout_json(self) -> str:
        return self.path('run_layout')

    def run_spec_json(self) -> str:
        return self.path('run_spec')

    def runtime_db(self) -> str:
        return self.path('runtime_metrics_db')

    def whatif_outputs(self) -> list[str]:
        """Existing cross-cell what-if artifacts at the run root (empty on a single-cell run).

        Driven by the `whatif` group tag, so adding a what-if output to the contract surfaces it
        here automatically.
        """
        out: list[str] = []
        for name in self.by_group('whatif'):
            p = self.path(name)
            out.extend(sorted(_glob.glob(p)) if '*' in p else [p])
        return [p for p in dict.fromkeys(out) if os.path.exists(p)]

    # ── cells ──────────────────────────────────────────────────────────────────
    def cells(self) -> list[tuple[str, str]]:
        """[(cell_name, cell_dir), …] in descriptor order (so partial/crashed cells are included)."""
        return list(runlayout.cells(self.base))

    def cell_dir(self, cell: str) -> str:
        return os.path.join(self.base, cell)

    def run_manifest(self, cell: str) -> str:
        return self.path('run_manifest', cell=cell)

    def rollup_csv(self, cell: str) -> str:
        return self.path('channel_rollup_csv', cell=cell)

    def aggregate_dir(self, cell: str, group_key: str) -> str:
        """<cell>/_aggregate/<group_key> — the cross-profile aggregate subtree.

        group_key is ChannelRun.group_key: `<config>` on a store-only run, `<config>/<channel>` on a
        mixed one, so the optional channel level is already folded in by the caller.
        """
        config, _, channel = group_key.partition('/')
        return self.path('aggregate_dir', cell=cell, config=config, channel=channel or None)

    # ── channel runs (the analysis leaf) ───────────────────────────────────────
    def channel_runs(self, cell: str | None = None) -> Iterator[tuple[str, runlayout.ChannelRun]]:
        """Yield (cell_name, ChannelRun) for every analyzed leaf.  `cell=None` spans the whole run.

        ChannelRun.channel is None on a store-only run — do NOT assume a channel segment.
        """
        items = [(cell, self.cell_dir(cell))] if cell is not None else self.cells()
        for name, cdir in items:
            for cr in runlayout.iter_channel_runs(cdir):
                yield name, cr

    def sim_dbs(self, cell: str | None = None) -> Iterator[tuple[str, runlayout.ChannelRun, str]]:
        """Yield (cell_name, ChannelRun, sim_db_path) across the run (or one cell).

        Structural — finds arms whose sim_meta.json was never finalized (crashed runs) too.
        """
        items = [(cell, self.cell_dir(cell))] if cell is not None else self.cells()
        for name, cdir in items:
            for cr, db in runlayout.iter_sim_dbs(cdir):
                yield name, cr, db

    def strategy_of(self, sim_db: str) -> str:
        """'…/sim_uni_fifo_norsl.db' -> 'uni_fifo_norsl', by inverting the sim_db template.

        Derived from the contract rather than slicing fixed prefix/suffix lengths, so renaming the
        sim DB pattern in schema.py cannot leave this behind.
        """
        rel = os.path.relpath(os.path.abspath(sim_db), self.base).replace('\\', '/')
        m = _capture_regex(self.artifacts['sim_db']['path'], 'strategy').match(rel)
        if m:
            return m.group('capture')
        stem = os.path.basename(sim_db)                      # fall back to the filename alone
        m = _capture_regex(self.artifacts['sim_db']['path'].rsplit('/', 1)[-1], 'strategy').match(stem)
        return m.group('capture') if m else os.path.splitext(stem)[0]

    def keyframe_db(self, sim_db: str) -> str:
        """The keyframe DB beside a sim DB, via the contract's keyframes_db template."""
        rel = os.path.relpath(os.path.abspath(sim_db), self.base).replace('\\', '/')
        parts = self._parts_from_leaf(os.path.dirname(rel))
        return self.path('keyframes_db', strategy=self.strategy_of(sim_db), **parts)

    def _parts_from_leaf(self, leaf_rel: str) -> dict:
        """Split a channel-run's relative dir into level parts, POSITIONALLY.

        Never by name: the store config and the store channel share the name `store`, so
        `<pair>/store/store/` is real and name-matching would mis-assign it.
        """
        segs = [s for s in leaf_rel.split('/') if s]
        names = [lv['name'] for lv in self.contract['levels']]
        parts = {n: None for n in names}
        for name, seg in zip(names, segs):
            parts[name] = seg
        return parts

    def sim_meta(self, cr: runlayout.ChannelRun) -> str:
        return os.path.join(cr.path, os.path.basename(self.artifacts['sim_meta']['path']))

    def series_json(self, cr: runlayout.ChannelRun) -> str:
        return os.path.join(cr.path, os.path.basename(self.artifacts['series_json']['path']))

    def config_json(self, cell: str, pair: str, config: str) -> str:
        return self.path('config_json', cell=cell, pair=pair, config=config)

    # ── pair-level assets ──────────────────────────────────────────────────────
    def warehouse_db(self, cell: str, pair: str) -> str:
        return self.path('warehouse_db', cell=cell, pair=pair)

    def planned_inventory_db(self, cell: str, pair: str) -> str:
        """The planned inventory for (cell, pair).

        Multi-cell runs freeze it once at _frozen/<pair>/ and share it across cells; single-cell runs
        write it under <cell>/<pair>/.  The precedence is declared in the contract
        (`planned_inventory.resolves_via`), not branched on here.
        """
        return self.path('planned_inventory', cell=cell, pair=pair)

    # ── navigation axes (the viewer contract) ──────────────────────────────────
    def axes(self) -> dict[str, list[str]]:
        """Sorted distinct values per axis, observed from the tree and backfilled from the
        descriptor.  `channel` is [] on a store-only run — that emptiness is meaningful, and the UI
        should hide the channel selector rather than invent a value.
        """
        axes = tuple(self.contract['axes'])
        found: dict[str, set] = {a: set() for a in axes}
        for cell, cr, db in self.sim_dbs():
            found['cell'].add(cell)
            found['pair'].add(cr.pair)
            found['config'].add(cr.config)
            if cr.channel:
                found['channel'].add(cr.channel)
            found['strategy'].add(self.strategy_of(db))
        # Descriptor backfill: a crashed/partial run may have cells with nothing on disk yet.
        for cell in (self.layout.get('cells') or ()):
            found['cell'].add(cell['name'])
        for pair in (self.layout.get('pairs') or ()):
            found['pair'].add(pair)
        return {a: sorted(found[a]) for a in axes}
