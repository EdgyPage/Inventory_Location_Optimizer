# Two values that no seam can reach

Type: task
Status: resolved

`settings.py` documents a five-seam path from a declared name to a spawned worker, and
`Tests/unit/test_run_shaping_params.py` checks it per knob. Two real modelling parameters sit
outside it entirely -- they cannot be set, are not recorded in `run_spec.json`, and do not move
the run-tree source fingerprint.

## 1. `_OVERSTOCK_MIN_HEADROOM = 10` -- `Optimization/simdriver/strategy_runner.py:44`

Empty bins kept per bucket at overstock fill. A modelling choice with a real effect on placement
pressure, living as a module-level literal INSIDE the worker. Same shape as the `n_bands` fallback
`config-centralization` ticket 01 found one line away in the same file: a value the five seams
cannot see, in the one process nothing reads back from.

## 2. Aisle geometry `(50, 10)` -- `Optimization/config/sim_config.py:75-76`

    _AISLE_W = aisle_width_for(50)
    _AISLE_H = aisle_height_for(10)

Fifty columns and ten levels: the reference warehouse's shape, as two bare integers with no
`settings.py` name and no CLI flag. Every run ever published used them, and nothing records that
they were a choice.

## What landing this means

A `settings.py` name each, threaded through seams 2-5 (`CONFIG` entry + accessor, CLI flag,
`run_spec` write and BOTH restore sites, worker payload). `Tests/unit/test_run_shaping_params.py`
is the per-knob template; `test_config_reaches_the_worker.py` covers seam 5 generically and needs
no edit.

## The judgement call, stated rather than assumed

Threading a knob is a small FEATURE, not a refactor: it adds a way to vary something that today
cannot be varied. The argument for doing it anyway is that both values are already varying
silently in the sense that matters -- nothing records them, so a run's own spec cannot answer
"what aisle geometry was this?" without reading the source at that commit.

The argument against doing it NOW is that `--max-skus` / `--coverage-days` already exist as the
supported ways to resize a run, and a new knob is a new surface to support. Left open
deliberately: this is the user's call, not a cleanup.


## Answer -- one of them was not a parameter at all

### `_OVERSTOCK_MIN_HEADROOM` is DEAD, and the fix is `del`, not five seams

This ticket's premise was wrong. It is not "a real modelling parameter unreachable from any
seam" -- it is **unreachable from anywhere**. The whole repo contains exactly one occurrence:
its own declaration.

    $ grep -rn "OVERSTOCK_MIN_HEADROOM\|overstock_min_headroom" --include=*.py --include=*.md .
    ./Optimization/simdriver/strategy_runner.py:44:_OVERSTOCK_MIN_HEADROOM: int = 10

`git log -S` dates it: commit **`8a20cdc9`** (2026-06-03), whose own message reads *"Removed
`_stock_to_target_fill`, `_OVERSTOCK_MIN_HEADROOM`, and the overstock [inflation loop]"*. It
removed the two USES and left the declaration standing, and the only commit to touch it since is
`2f60df25`, the subpackage regrouping, which moved it without reading it.

So it has been dead for three and a half months behind a comment describing behaviour that no
longer exists ("during overstock fill so reorder units always find a slot").

**Threading it would have been the worst available outcome**: a CLI flag, a `run_spec` field and
a worker payload entry for a knob that controls nothing, recorded in every future run as though
it meant something. Deleted instead.

### The aisle geometry was real, and it had a second defect the ticket did not name

`AISLE_COLUMNS = 50` / `AISLE_LEVELS = 10` now exist in `settings.py`, reach `CONFIG`, carry
`--aisle-columns` / `--aisle-levels`, are written into `run_spec.json`, and are restored on BOTH
paths -- resume (`_apply_run_spec`) and re-analysis (`_apply_run_shape`). The second matters for
the reason that function's own comment gives about `seed_world`: analysis REBUILDS the warehouse,
and rebuilding it to this checkout's geometry rather than the run's own is the silent
wrong-warehouse bug.

The defect the ticket missed is the SHAPE the old code was in.
`_AISLE_W = aisle_width_for(50)` was a module scalar evaluated at IMPORT, and
`Tests/unit/test_config_reaches_the_worker.py` names that exact pattern as something this project
has already shipped:

> No accessor snapshots CONFIG at import. The run-shaping values are functions rather than module
> scalars precisely so a CLI override is visible; a snapshot cannot see one. This project has
> shipped that defect (`_INITIAL_FILL`, which made a run misreport its own sizing in its
> warehouse DB) and caught five more before they shipped.

So adding a flag WITHOUT converting the scalars would have produced a flag that silently did
nothing -- the sixth instance. `aisle_geometry()` reads `CONFIG` at call time; the five consumers
(`sim_assets.plan_warehouse`, the two aisle-bin computations, `run_simulation`'s structural floor
check, and one test) call it.

### Seam 5 is deliberately absent

The warehouse is planned before any worker exists, so the geometry is PARENT-side. Registered in
that test's `PARENT_ONLY` with the reason, and added to its accessor-discovery regex -- both,
because an exemption for a name the discovery does not produce is itself flagged ("an exemption
for a deleted accessor silently exempts nothing and hides the next one").

**Proven non-vacuous**: rewriting `aisle_geometry` as a snapshot makes
`test_the_accessor_reads_config_at_call_time[aisle_geometry]` fail. The guard covers it.
