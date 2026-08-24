"""dossier — the run-root index a page loads first.

One evaluation, and it exists so that every output in the run tree has exactly ONE owner.

`cost.rollup` declares `out_subdir='tables'` and wrote the run-root index to the parent of
that, through `os.path.dirname(out)`.  Nothing stopped it: `artifact_map.save_in_bounds`
checks a path against the evaluation's declared subdir, and an evaluation that writes
outside its own bounds is invisible to a check keyed on those bounds.  So the index — the
document every dossier macro loads before anything else — was written by an evaluation
that did not declare it, and `artifact_map.findings()` could not see the discrepancy.

The obvious fix is worse than this one: giving `cost.rollup` `out_subdir=('', 'tables')`
makes `save_in_bounds` unmatchable on the `''` member, which converts a silent gap into a
silent pass.  A root-scope evaluation of its own is what the `catalog.*` evaluations
already do, and it costs one module.
"""
