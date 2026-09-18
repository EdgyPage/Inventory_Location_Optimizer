# 06 - hand-written run-tree path knowledge increased across eleven sites

Type: debt
Status: needs-triage

`Tests/architecture/test_runtree_consumption.py::test_no_new_handwritten_contract_paths`

```
AssertionError: hand-written run-tree path knowledge INCREASED - migrate onto the resolver
  accessors instead of growing the debt:
    Optimization/config/settings.py:          'run_layout.json'         x1  (baseline 0)
    Optimization/config/sim_config.py:        'run_layout.json'         x1  (baseline 0)
    Optimization/config/whatif_config.py:     'restock_selection.json'  x9  (baseline 0)
    Optimization/config/whatif_config.py:     'run_layout.json'         x1  (baseline 0)
    Optimization/config/whatif_config.py:     'run_spec.json'           x1  (baseline 0)
    Optimization/run_restock_selection.py:    'run_layout.json'         x1  (baseline 0)
    Optimization/run_simulation.py:           'run_layout.json'         x2  (baseline 1)
    Optimization/simconfig/staffing.py:       'restock_selection.json'  x1  (baseline 0)
    Optimization/simdriver/supervisor.py:     'resume.pkl'              x3  (baseline 1)
    Optimization/simdriver/supervisor.py:     'sim_meta.json'           x8  (baseline 6)
    Optimization/simdriver/workunits.py:      'restock_selection.json'  x1  (baseline 0)
```

**A ratchet, not a break** - the test compares against a recorded baseline and fails on any
INCREASE, so nothing is broken today. Eleven sites across seven files is too many for one accidental
commit; this reads as a feature landing without migrating onto `runschema.resolver_for`.

Why it matters is in `CLAUDE.md` section 3: run-tree levels must be consumed positionally, never by
directory name, because the store *config* and the store *channel* are both named `store` and two
levels are conditional. Assuming otherwise once silently dropped every store-only run from the
what-if scanners - and `whatif_config.py` carries nine of the eleven sites here.

Fix shape: resolve through `runschema.resolver_for(base_dir)` accessors (`path` / `leaf_path` /
`glob`), never a joined string; then re-record the baseline in the same commit.

## Comments

**2026-09-18, triage from the outstanding-work audit.** Re-run today the detector reports
thirteen rows. One of them is a false positive: `Optimization/simdriver/leaf_scope.py: '_site'
x15 (baseline 0)` is the `__slots__ = ('_site',)` entry plus fourteen `self._site` attribute
reads -- the reserved-directory token colliding with an attribute NAME, which the detector's own
docstring says it tries to avoid but only for longer identifiers, not for `.`-preceded ones.
The other twelve rows (`run_layout.json`, `restock_selection.json`, `run_spec.json`,
`resume.pkl`, `sim_meta.json` across seven files) are genuine filename literals and remain this
ticket. Fix the detector (exclude attribute access, or match tokens only inside string literals)
and re-baseline BEFORE migrating the twelve, or the ratchet keeps counting the attribute.
