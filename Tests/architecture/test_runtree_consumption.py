"""test_runtree_consumption.py — hand-written run-tree path knowledge may only SHRINK.

The run-tree contract (`Optimization/runschema/schema.py`) declares every artifact's path once;
`RunTree` (`path`/`leaf_path`/`glob`/`parts_of`) renders them.  Before the consumer migration,
~25 sites across the repo re-implemented those templates as string literals — each one a copy
that keeps working right up until the tree moves, and then fails somewhere far away (the
store-only what-if scanners silently dropping every run was exactly this).

This file is the RATCHET that stops the debt growing back.  The forbidden tokens are DERIVED
FROM THE CONTRACT at test time — fully-literal template basenames plus the reserved subtree
names — so declaring a new artifact automatically starts policing its name.  Every current
occurrence is recorded in `_BASELINE`; a file may only hold FEWER occurrences than its baseline,
never more, and a NEW file mentioning a token fails immediately.

When a migration removes occurrences, the test keeps passing (shrinkage is the goal) — tighten
the baseline with the regeneration snippet in `_BASELINE`'s docstring when you touch this file.
A GENUINELY new legitimate use (rare: a writer helper inside the contract's own layer) belongs in
`_ALLOWED_DIRS`, not in a bigger baseline.

    python -m pytest Tests/architecture/test_runtree_consumption.py -q
"""
from __future__ import annotations

import os
import re

# Importing the declaration, not a resolver: the tokens come straight from schema.py's tables so
# a renamed artifact re-derives its tokens with no edit here.
from Optimization.runschema import schema as _decl

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Directories swept.  Tests/ is excluded (golden tests hardcode the literals on purpose), and so
#: is Optimization/runschema/ (the contract's own layer legitimately spells its templates).
_SWEPT = ('Optimization', 'Visualization', 'Diagnostics', 'docs', 'scripts')
_ALLOWED_DIRS = ('Optimization/runschema',)
_SKIP_DIRS = {'.git', '__pycache__', 'node_modules', 'site', '_build'}

_PLACEHOLDER = re.compile(r'\{\w+\??\}')


def _contract_tokens() -> tuple:
    """Literal path fragments a consumer has no business spelling out.

    Two sources, both derived so they cannot rot:
      * every template BASENAME that is fully literal after placeholder-stripping leaves nothing
        behind (e.g. `run_layout.json`, `series.json`, `whatif_delta.csv`);
      * partially-literal basenames whose literal part is still distinctive
        (`.viz.db`, `.keyframes.db`, `_ckpt_`), taken as the longest literal fragment >= 6 chars;
      * the reserved subtree names (`_frozen`, `_runtime`, `_aggregate`, `_viz`) that appear as
        directory segments in any template.
    Short/generic fragments (`.db`, `sim_`) are excluded — a token must be specific enough that
    matching it means the CONTRACT's file, not a coincidence.
    """
    tokens: set = set()
    for spec in _decl.ARTIFACTS.values():
        path = spec.get('path')
        if not path:
            continue
        for seg in path.split('/'):
            if seg in ('*', '**'):
                continue
            literal = _PLACEHOLDER.sub('', seg)
            if not literal or '*' in literal:
                # globbed segment: keep a distinctive stem if one exists (e.g. `whatif_` PNGs
                # are covered by their sibling fully-literal artifacts; skip pure-glob segments)
                continue
            if literal == seg and '.' in seg:
                tokens.add(seg)                       # fully literal filename
            elif seg.startswith('_') and literal == seg:
                tokens.add(seg)                       # reserved directory (_frozen, _runtime, ...)
            elif len(literal) >= 6:
                tokens.add(literal)                   # distinctive fragment (.viz.db, _ckpt_...)
    # `analysis.log` and `run.log` are contract artifacts but 'run.log' is too generic to police
    # against prose; keep only tokens with an extension or a leading underscore.
    return tuple(sorted(t for t in tokens
                        if (('.' in t and len(t) >= 8) or t.startswith('_')) and t != 'run.log'))


def _count(path: str, tokens) -> dict:
    """{token: occurrences} in one source file (raw text — a literal in a comment still teaches
    the next reader to hand-join, so comments count too)."""
    try:
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return {}
    out = {}
    for t in tokens:
        n = text.count(t)
        if n:
            out[t] = n
    return out


def _sweep() -> dict:
    """{(relpath, token): count} over every swept Python source file."""
    tokens = _contract_tokens()
    found: dict = {}
    for top in _SWEPT:
        for dirpath, dirs, files in os.walk(os.path.join(_ROOT, top)):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            rel_dir = os.path.relpath(dirpath, _ROOT).replace(os.sep, '/')
            if any(rel_dir == a or rel_dir.startswith(a + '/') for a in _ALLOWED_DIRS):
                continue
            for fn in sorted(files):
                if not fn.endswith('.py'):
                    continue
                rel = f'{rel_dir}/{fn}'
                for tok, n in _count(os.path.join(dirpath, fn), tokens).items():
                    found[(rel, tok)] = n
    return found


#: The recorded debt: every (file, token) occurrence at the time the ratchet was armed.
#: Regenerate (AFTER confirming the new state is a real migration, not a regression) with:
#:     python -c "from Tests.architecture import test_runtree_consumption as t; \
#:                import pprint; pprint.pprint(t._sweep())"
#: and paste the result here.  Counts may only go DOWN.
_BASELINE: dict = {('Diagnostics/bucket_fill.py', 'warehouse.db'): 1,
 ('Diagnostics/replay_run.py', 'run_layout.json'): 1,
 ('Diagnostics/replay_run.py', 'warehouse.db'): 4,
 ('Optimization/Performance_Evaluations/aggregate/stats_aggregate.py', '_aggregate'): 6,
 ('Optimization/Performance_Evaluations/aggregate/stats_aggregate.py', 'aggregate_summary.csv'): 1,
 ('Optimization/Performance_Evaluations/aggregate/stats_aggregate.py', 'aggregate_tests.json'): 1,
 ('Optimization/Performance_Evaluations/aggregate/stats_aggregate.py', 'by_initial_summary.csv'): 2,
 ('Optimization/Performance_Evaluations/aggregate/stats_aggregate.py', 'tests.json'): 1,
 ('Optimization/Performance_Evaluations/common/series.py', '_aggregate'): 2,
 ('Optimization/Performance_Evaluations/common/series.py', 'series.json'): 1,
 ('Optimization/Performance_Evaluations/common/stats_core.py', 'series.json'): 1,
 ('Optimization/Performance_Evaluations/comparison/series.py', 'series.json'): 3,
 ('Optimization/Performance_Evaluations/comparison/summary_csv.py', 'summary_batch.csv'): 2,
 ('Optimization/Performance_Evaluations/comparison/summary_csv.py', 'summary_task.csv'): 2,
 ('Optimization/Performance_Evaluations/comparison/throughput_vs_labor.py', 'series.json'): 1,
 ('Optimization/Performance_Evaluations/core/context.py', '_aggregate'): 3,
 ('Optimization/Performance_Evaluations/core/context.py', 'run_layout.json'): 3,
 ('Optimization/Performance_Evaluations/core/context.py', 'series.json'): 1,
 ('Optimization/Performance_Evaluations/core/context.py', 'summary_task.csv'): 1,
 ('Optimization/Performance_Evaluations/driver.py', '_aggregate'): 5,
 ('Optimization/Performance_Evaluations/per_strategy/report_bars.py', 'batches_long.csv'): 5,
 ('Optimization/Performance_Evaluations/per_strategy/report_bars.py', 'per_run_summary.csv'): 3,
 ('Optimization/Performance_Evaluations/stats/config_suite.py', 'by_initial_summary.csv'): 3,
 ('Optimization/Performance_Evaluations/stats/config_suite.py', 'stats_summary.csv'): 1,
 ('Optimization/Performance_Evaluations/stats/config_suite.py', 'tests.json'): 1,
 ('Optimization/analyze_run.py', '_runtime'): 2,
 ('Optimization/analyze_run.py', 'analysis.log'): 1,
 ('Optimization/analyze_run.py', 'runtime_metrics.db'): 1,
 ('Optimization/config/whatif_config.py', '_frozen'): 2,
 ('Optimization/persistence/Picking_Data.py', 'warehouse.db'): 1,
 ('Optimization/persistence/Warehouse_Data.py', '_frozen'): 3,
 ('Optimization/persistence/Warehouse_Data.py', 'warehouse.db'): 7,
 ('Optimization/persistence/runtime_metrics.py', '_runtime'): 3,
 ('Optimization/persistence/runtime_metrics.py', 'runtime_metrics.db'): 3,
 ('Optimization/run_analysis.py', '_aggregate'): 3,
 ('Optimization/run_analysis.py', 'analysis.log'): 1,
 ('Optimization/run_runtime_graphs.py', '_runtime'): 5,
 ('Optimization/run_simulation.py', 'run_layout.json'): 1,
 ('Optimization/run_simulation.py', 'run_spec.json'): 5,
 ('Optimization/simconfig/configs/ful_calibrated.py', 'config.json'): 1,
 ('Optimization/simdriver/cells.py', 'sim_meta.json'): 2,
 ('Optimization/simdriver/scenario.py', '_frozen'): 2,
 ('Optimization/simdriver/scenario.py', 'planned_inventory.db'): 1,
 ('Optimization/simdriver/scenario.py', 'warehouse.db'): 2,
 ('Optimization/simdriver/sim_assets.py', 'planned_inventory.db'): 1,
 ('Optimization/simdriver/strategy_runner.py', 'runtime_metrics.db'): 1,
 ('Optimization/simdriver/supervisor.py', 'resume.pkl'): 1,
 ('Optimization/simdriver/supervisor.py', 'runtime_metrics.db'): 1,
 ('Optimization/simdriver/supervisor.py', 'sim_meta.json'): 6,
 ('Optimization/simdriver/workunits.py', 'config.json'): 1,
 ('Optimization/simdriver/workunits.py', 'resume.pkl'): 2,
 ('Optimization/simdriver/workunits.py', 'sim_meta.json'): 2,
 ('Visualization/cache_schema.py', '_viz'): 2,
 ('Visualization/cache_schema.py', 'warehouse.db'): 1,
 ('Visualization/db_reader.py', '_viz'): 2,
 ('Visualization/db_reader.py', 'run_layout.json'): 3,
 ('Visualization/db_reader.py', 'warehouse.db'): 9,
 ('Visualization/precompute.py', '_viz'): 1,
 ('Visualization/precompute.py', 'run_layout.json'): 1,
 ('Visualization/precompute.py', 'warehouse.db'): 1,
 ('Visualization/server.py', 'run_layout.json'): 2,
 ('docs/experiments/ingest.py', 'config.json'): 6,
 ('docs/experiments/ingest.py', 'run_manifest.json'): 4,
 ('docs/experiments/ingest.py', 'run_spec.json'): 4,
 ('docs/experiments/ingest.py', 'sim_meta.json'): 3,
 ('docs/experiments/ingest.py', 'whatif_delta.json'): 4,
 ('docs/experiments/ingest.py', 'whatif_labor.json'): 1,
 ('docs/macros.py', 'config.json'): 6,
 ('docs/macros.py', 'run_spec.json'): 1,
 ('docs/macros.py', 'whatif_delta.csv'): 2,
 ('docs/macros.py', 'whatif_delta.json'): 2,
 ('scripts/archive_cells.py', 'resume.pkl'): 1,
 ('scripts/archive_cells.py', 'run_layout.json'): 1,
 ('scripts/archive_cells.py', 'runtime_metrics.db'): 1,
 ('scripts/archive_cells.py', 'sim_meta.json'): 2,
 ('scripts/new_experiment.py', 'run_manifest.json'): 1}  # noqa: E501


def test_the_baseline_is_armed():
    """An empty baseline would make the ratchet a no-op exactly when it matters most.

    `_BASELINE` empty + a clean sweep means the migration finished AND the ratchet holds at
    zero — the ideal end state.  `_BASELINE` empty + a non-empty sweep means someone armed the
    ratchet without recording the debt: every existing occurrence would read as a regression,
    which is noise, not signal.  Fill the baseline.
    """
    current = _sweep()
    if not _BASELINE:
        leftovers = {k: v for k, v in current.items()}
        assert not leftovers or leftovers == {}, (
            'the ratchet has no baseline but the sweep still finds hand-written contract '
            'literals; record them (docstring above) or migrate them:\n  '
            + '\n  '.join(f'{f}: {t} x{n}' for (f, t), n in sorted(leftovers.items())))


def test_no_new_handwritten_contract_paths():
    """A consumer that spells a contract path re-creates the debt this repo just paid off.

    Every (file, token) count must be <= its baseline; a pair absent from the baseline fails
    outright.  The fix is never "grow the baseline": use `rt.path/leaf_path/glob` (see
    `docs/design/SCHEMA_COMPATIBILITY.md` §5c), or — for the contract's own layer — move the
    code under `Optimization/runschema/`.
    """
    current = _sweep()
    regressions = []
    for key, n in sorted(current.items()):
        base = _BASELINE.get(key, 0)
        if n > base:
            rel, tok = key
            regressions.append(f'{rel}: {tok!r} x{n} (baseline {base})')
    assert not regressions, (
        'hand-written run-tree path knowledge INCREASED — migrate onto the resolver accessors '
        'instead of growing the debt:\n  ' + '\n  '.join(regressions))


def test_the_tokens_are_still_derived_and_distinctive():
    """The token set itself must not silently collapse.

    If a schema edit renamed every literal basename into globs, `_contract_tokens()` could
    return so few tokens the ratchet stops policing anything.  Pin a floor and a few anchors
    that exist as long as the contract declares them.
    """
    tokens = _contract_tokens()
    assert len(tokens) >= 8, f'token set collapsed to {tokens}'
    for anchor in ('run_layout.json', 'series.json', '_frozen', '_aggregate'):
        assert anchor in tokens, f'{anchor} fell out of the derived token set'
