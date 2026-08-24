"""dossier.index — the one document a page loads first.

Run identity, the worker count the timings were taken under, and the cost rows: enough
that no macro has to know the layout of the tree it is reading from.

It used to be written by `cost.rollup`, which declares `out_subdir='tables'` and reached
its parent with `os.path.dirname(out)` — see this package's docstring for why that made
the file invisible to `artifact_map`.  The rows come from the same shared `cost_rows`
helper `cost.rollup` uses, so the CSV and the index cannot disagree about a number; the
broker has already served the runtime metrics by the time the second of the two runs.

The basename comes from the HEAD run-tree contract rather than being spelled here, so the
declaration and the writer cannot disagree and the filename appears in this module as no
text at all.

**HEAD, not the run's own contract.**  `ctx.rt` resolves against the document the RUN was
made under, which is right for a CONSUMER and wrong for a writer: this run predates the
dossier artifacts entirely, so `ctx.rt.path('dossier_json')` raises `KeyError` on it.  A
writer emits the tree the current code declares; the resolver exists to read a tree that
was already written.  (The failure was silent-shaped and only visible because the render
error tally now names an evaluation that raised.)
"""
import json
import os
import posixpath

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io
from Optimization.Performance_Evaluations.cost.rollup import BASE_RULE, cost_rows

#: The run-tree artifact this evaluation writes, and its basename taken from the HEAD
#: contract's declaration of it.  Resolved once at import: `contract.build()` reads
#: `schema.ARTIFACTS`, which is a literal, so this costs nothing per render and cannot
#: drift from the declaration it is derived from.
_ARTIFACT = 'dossier_json'


def _head_basename(artifact: str) -> str:
    from Optimization.runschema import contract
    return posixpath.basename(contract.build()['artifacts'][artifact]['path'])


_BASENAME = _head_basename(_ARTIFACT)

#: Why the two span totals must not be added together.  Carried in the document rather
#: than in a caption, because a reader who reaches this file through a macro never sees
#: the caption.
_SPAN_NOTE = ('Wall-clock compute cost of the SIMULATOR, not modeled warehouse labor. '
              'Setup and loop spans are disjoint: precomp_s is measured before the batch '
              'loop starts and is not part of total_s.')


@evaluation(key='dossier.index', label='Run dossier index',
            scope='run', needs=('runtime',), out_subdir='')
def render(ctx, params):
    rows = cost_rows(ctx)
    if not rows:
        ctx.log.warning('  dossier index: the run published no cost rows; skipped')
        return
    # `io.out_dir` gives this root-scope evaluation the dossier root; the basename comes
    # from the contract, so the name lives in one declaration.
    path = os.path.join(io.out_dir(ctx), _BASENAME)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump({
            'run': os.path.basename(os.path.abspath(ctx.run_root)),
            'workers': ctx.run_workers(),
            'n_batches': rows[0]['n_batches'],
            'baseline_rule': BASE_RULE,
            'note': _SPAN_NOTE,
            'cost': rows,
        }, fh, indent=2)
    ctx.log.info(f'  wrote the dossier index ({len(rows)} cost rows)')
