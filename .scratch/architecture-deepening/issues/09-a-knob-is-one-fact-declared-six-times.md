# 09 - a knob is one fact, declared six times

Type: refactor
Status: resolved

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

## Answer -- RESOLVED (2026-09-16)

`sim_config.KNOBS` is the declaration: one `Knob` record per tunable, covering
`CONFIG['global']` **exactly** -- 70 declared against 70 actual, verified in both directions
and pinned by `Tests/unit/test_knob_registry.py`.

### Derived from it

| site | before | after |
|---|---|---|
| CONFIG write-back | ~35 hand-typed assignments + 2 family loops | `apply_cli_overrides(args)` |
| `run_spec.json` record | 49 hand-written entries | `**_run_spec_record(args)` |
| resume restore | a ~50-name tuple + 2 splices | `*SPEC_KNOB_NAMES` + the CLI-only names |
| `STAFFING_KEYS` / `INBOUND_KEYS` | two hand-kept tuples | derived from `family` |

### `spec_from` is a SOURCE, not a boolean, and that was not optional

`workers`, `keyframe_interval`, `aisle_columns` and `aisle_levels` are recorded from `args`,
not from `g`. `g['workers']` is `args.workers or 1`, so recording it from CONFIG would write
**1** where the run recorded **None** -- silently pinning every resumed flag-less run to one
process. A bool `in_spec` would have shipped that. The registry's flags were cross-checked
against what the recorder actually writes, programmatically, before anything was rewired.

### NOT derived: `_apply_run_shape`, the sixth seam

Every key there carries a bespoke absence rule encoding what a PRE-FIELD spec means for that
one knob -- `or 'v1'`, `bool(...)`, `or 0`, a plain `.get` that must never be an `or` because a
declared `0.0` is a real configuration, a skip-if-None. Flattening those into an enum would
risk exactly the silent wrong-regime re-analysis the rules exist to prevent.

So the registry guards the SET instead of the semantics: `ANALYSIS_EXEMPT` names the knobs that
seam deliberately skips, with reasons, and a recorded knob that is neither restored nor exempted
fails `test_every_recorded_knob_is_restored_by_the_analysis_seam_or_exempted`.

**Both exemptions were checked, not assumed.** `couple_channels` looked like a real gap. It is
not: its own docstring records that coupling reaches the analysis through `run_layout.json`'s
`coupled`, and `couple_channels()` has exactly two callers, both of which build the run and
neither of which executes during a standalone re-analysis. `workers` is pool size.

### The twelve tests that failed, and why that was the point

Twelve per-knob tests asserted the MECHANISM -- `src.count("'seed_world'") >= 2`, counting
literal occurrences in `run_simulation`'s source. Those lines are derived now, so the counts
went to zero while the property they cared about became more true. Each kept its specific claim
and now asks the registry. Two got stronger in the process:

- the `--n-batches 0` guard is asserted as BEHAVIOUR (apply the override, assert the zero
  survived) rather than as the substring `'if args.n_batches is not None:'`;
- the sentinel-flag check asks whether each sentinel declares `apply='if_set'` rather than
  whether `main` happens to contain `args.<x> is not None`.

### Verification

| check | result |
|---|---|
| `Tests/unit/test_knob_registry.py` | 10 passed (new) |
| `Tests/unit -k "not gpu"` | **2671 passed**, 1 skipped |
| real run: `run_spec.json` vs the previous run | **identical** but for `repo_commit` and four `fragmentation.seconds` |
| gates 1-5, 7-10 | green |
| gate 6 | RED, unchanged, pre-existing |

Those four are WALL-CLOCK measurements inside the calibration block, so `run_spec.json` is not
byte-comparable between runs even at rest -- worth knowing before anyone diffs two of them.
No configuration value moved.

### Still hand-written, and recorded as such

The 70 `add_argument` calls. Their help text, types, choices and metavars are genuinely
bespoke, and a record wide enough to carry them would be the parser with extra steps. The
`workunits._shared` payload also stays: it carries BUNDLES (the `*_spec()` accessors) rather
than flat keys for most of its contents, and the existing generic guard already covers those.
Adding a knob is now one record plus one flag, down from six places.
