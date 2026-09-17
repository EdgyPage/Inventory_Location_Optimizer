# 09 - a knob is one fact, declared six times

Type: refactor
Status: needs-triage

## Context

`Optimization/config/settings.py:1-57` counts the seams itself -- four, then "And then the FIFTH
seam, which this list used to omit". The honest count is **six**, and
`Optimization/run_analysis.py:135` names the sixth in as many words: "THE STAFFING RECORD, whole,
as one key -- the sixth seam", because an analysis worker is also spawned and also re-imports
pristine defaults. Seam 4 is two sites (`_apply_run_spec` for resume, `_apply_run_shape` for
re-analysis), and `simdriver/cells.py:190` adds the what-if cell write.

The bands one knob passes through:

| # | Site |
|---|---|
| 1 | `settings.py` constant + `CONFIG['global']` key (`sim_config.py:135-333`) |
| 2 | `_build_parser` -- one of 70 `add_argument` calls (`run_simulation.py:454-964`) |
| 3 | `main()` write-back -- 31 hand-typed `g['k'] = args.k` lines (`:1025-1082`) |
| 4 | `_write_run_spec` -- 45 entries (`:1192-1297`) |
| 5 | `_apply_run_spec` (`:235-312`) AND `run_analysis._apply_run_shape` (`:462-575`) |
| 6 | `workunits._shared` -- the payload a spawned worker unpickles (`:470-521`) |
| 7 | `run_analysis._sim_result_from_meta` (`:114-142`) |

**38 of 70 keys ride a list** and cost one line: `STAFFING_KEYS` (20, `sim_config.py:384-390`) and
`INBOUND_KEYS` (18, `:722-732`). `sim_config.py:722-724` states the design -- "so its CLI flags,
its run-spec record and both restore sites are derived from one list rather than three
hand-maintained ones". **Two adapters, so the mechanism is proven. It just never became a module.**

**32 of 70 are retyped at every band** -- `seed_world`, `seed_batches`, `n_batches`, `max_skus`,
`sampler`, `work_day_seconds`, `releases_per_day`, `cut_at_day_end`, `roll_over_unpicked`,
`shift_drain_or_cap`, `couple_channels`, `recv_crew_size`, `recv_day_seconds`, `recv_day_origin`,
the eight `put_*` queue keys, `put_crew_size`, the three crew-price scalars, `keyframe_interval`,
`checkpoint_frac`, `aisle_columns`, `aisle_levels`, `workers`. Roughly 128 hand-written assignments
for 32 facts, and every omission is silent for a whole run.

## Three gaps in the existing generic guards

1. **The payload guard is bundle-only.** `test_config_reaches_the_worker.py:418-433` scopes
   statement 2 to "every knob BUNDLE". A new FLAT key -- 32 of 70 are flat, and 12 already ride
   `_shared` by hand -- is caught by nothing.
2. **It reads the first `_shared = ...` assignment only** (`:380-397` breaks out of the AST walk).
   `workunits._prepare_site_run:637-651` builds a second, differently-shaped payload it never
   inspects.
3. **Nothing joins settings -> CONFIG -> flag -> write-back.** `_apply_run_defaults`
   (`run_simulation.py:331-355`) carries a hand-written `if not hasattr(args, key): raise` because
   the author knew the write-back iterates key lists, not `vars(args)`.

## What to build

One `Knob` record per tunable, declared in `settings.py` beside its comment:

```
Knob(name='put_swap_coef', default=0.0, flag='--put-swap-coef',
     type=_nonneg_float, scope=GLOBAL, reaches=WORKER,
     era=FLAG_OFF_ONLY, help='...')
```

All eight sites become a loop over `KNOBS` filtered by `scope`/`reaches`. `INBOUND_KEYS` and
`STAFFING_KEYS` become `[k.name for k in KNOBS if k.family == ...]`. `_ERA_DERIVED_FLAGS` (`:315`),
`_ERA_DERIVED_FILL_FLAGS` (`:324`), `ERA_ONLY_KEYS` and `FLAG_OFF_ONLY_KEYS` (`:408`, `:410`)
collapse into `k.era`. A parent-only knob declares `reaches=PARENT` with a reason, as `PARENT_ONLY`
does today.

**The `*_spec()` bundle accessors STAY** -- `inbound_spec` (`:757-911`) is 150 lines of
contradiction refusals behind one call and is genuinely deep. They read their keys from the
registry instead of `g.get('...')` literals. **The `CONFIG` read contract does not move**; this
changes how CONFIG is populated and propagated, so the never-rebind rule (`sim_config.py:10-13`)
is untouched.

## Verification

- `test_run_shaping_params.py`'s AST scan becomes a set comparison against `KNOBS`. The `_shared`
  check extends to flat keys and to BOTH payload expressions for free.
- Per-knob substring tests keep their specific claims but stop being the only line of defence.
- **Watch `.scratch/architecture-drift/issues/06`** -- the ratchet on hand-written run-tree path
  knowledge has 11 recorded sites, bulk in `whatif_config.py`. This work should move the count
  DOWN, which the ratchet permits; confirm rather than assume.
- Gates 1, 2, 4 (if `SHAPE_SOURCES` moves), 8, 10.
