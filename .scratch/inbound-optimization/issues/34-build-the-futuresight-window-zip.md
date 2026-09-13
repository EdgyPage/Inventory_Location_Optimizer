# Build the coupled futuresight window zip

Type: task
Status: resolved
Blocked by: 32

Graduated 2026-09-13 by [Decide the futuresight family's place](32-decide-the-futuresight-familys-place.md),
which decided BUILD and kept phase 2 at ten cells. That resolution holds the reasoning; this
ticket holds the work.

Blocks phase 2: `fsight_w5` and `fsight_wall` die at their first drain until this lands.

## Question

Make `compose_site_view` compose two futuresight windows, prove it neutral, and put the first
number on what a futuresight cell costs.

### The build

Two edits in `Inbound/site_space.py`, both already written down beside the code they replace:

- **The refusal at `:98-113`** becomes the zip the comment above it specifies: zip the two
  window tuples BY BATCH INDEX, then union each pair of `{sku: qty}` dicts.
- **`window=None` at `:190`**, hard-coded in the composed view's return, takes the zipped
  window. Easy to miss; the zip is inert without it.

Settled by 32, do not re-open:

- **Raise on collision, do not trust.** `predicted` (`:148`) and `emptied_at` (`:161`) are both
  unioned with a raise under exactly this "disjoint by construction" argument. Follow that
  pattern; `len(merged) == len(a) + len(b)` rides an iteration the union already does. Do not
  invent a new discipline for this one field.
- **`_window_rates` does not change.** SKUs are single-regime, so the flat disjoint union keeps
  its `{sku: (total, events)}` shape and every load still reads exactly its own leaf's entry.
  A per-regime window would break `_window_rates` and is the wrong shape.
- **Zipping by batch index needs no new guard.** `_futuresight_window` clamps at `n_batches`,
  the staffing derivation refuses channels with different batch counts, and the composer
  already checks both leaves froze at one instant. A length mismatch reaching the zip means one
  of those three broke, so assert it rather than handling it.

### The obligations

- **06's Tier-1 pair**, as for every gain family that landed with 14: equivalence and sabotage.
  The equivalence case is unusually strong here and should be written as the headline test --
  **composed pricing is EXACTLY per-leaf pricing**, because the union is disjoint, so the zip
  is a strict no-op against two independent evaluations. A test that only asserts "the composed
  window has both leaves' keys" leaves the thing that matters unpinned.
- **Replace, do not delete, the refusal's test.**
  `Tests/unit/test_site_space_view.py::test_a_futuresight_window_is_refused_rather_than_zipped`
  pins behaviour that is now wrong. Its successor pins the zip AND the collision raise.
- **The docstrings must agree with the glossary as 32 rewrote it.** `Inbound/gain.py`,
  `site_space.py` and `whatif_config.py` describe futuresight as an upper-bound reference in
  several places. It is a CLAIRVOYANCE REFERENCE: exact PRICING at w=inf, not an optimal
  OUTCOME (32 section 2). Anywhere the old phrasing survives is a place the barred claim gets
  re-derived by the next reader.

### The probe, which is part of this ticket and not optional

32 declined to fold in the `_window_rates` memo because the cost is unmeasured and 06's caching
contract is a real obligation to pay blind. This ticket produces the measurement instead:

Run ONE `fsight_w5` coupled unit and read its wall against 31's `gforecast` pole -- **cell mean
1,236 s, on the probe's clock**. Take the reading on the SAME machine and run shape as whatever
it is compared against; 31 section 5 is why (the gate run's clock is ~1.6x slower per unit and
~3.4x slower in the freeze, and the two must never be mixed).

- **Inside ~2x**: the memo is never built, and that gets RECORDED here the way 31 recorded the
  batch-script sharing -- so the next reader stops before optimising it.
- **Past it**: the memo graduates as its own ticket, with this measurement attached. It is the
  legal-keyed-not-built case from 06 (the window is immutable and replaced wholesale per batch,
  aggregated once per drain), so the key is available; what it needs is a number, which is this.

Either way the answer records the reading, because "nobody has ever timed a futuresight cell"
stops being true here and phase 2's ten-cell sizing is a LOWER bound until it does.

## Answer

RESOLVED 2026-09-13. **BUILT -- the zip composes, both futuresight cells are alive, and the
first number on a futuresight cell says it is CHEAPER than the `gforecast` pole, not dearer:
`fsight_w5` runs at 0.91x it. The `_window_rates` memo is therefore NEVER built, and this is
the record that says so.**

### 1. The build: three edits, as 33 counted them

`'window'` moved from `UNCOMPOSED_VIEW_FIELDS` into `COMPOSED_VIEW_FIELDS`; the zip landed in
`compose_site_view` beside the other three merges; `_KNOWN_DEAD` in
`Tests/unit/test_campaign_cells_can_run.py` is now `set()`. 33's warning was accurate -- the
`window=None` hard-coded in the composed return is the edit that hides, and
`test_a_composed_field_actually_survives_the_composition[window]` is exactly what catches
stopping at two (verified by sabotage, section 3).

`UNCOMPOSED_VIEW_FIELDS` is KEPT at `frozenset()` rather than deleted. Deleting it deletes the
PARTITION, and the partition is what makes the next `SpaceView` field a decision somebody makes
instead of a default somebody inherits -- which is the exact route `window` took in. The zip
RULE moved off that declaration and into the code it now describes.

Three refusals ride the zip, all three settled by 32 and none of them invented here: a SKU in
both leaves' windows raises (naming the SKU and the batch), windows of different depths raise,
and a window on ONE leaf only raises. The last is the one 32 did not name and it is the one with
teeth: `_fs_w` comes off the inbound spec and a cell names ONE policy, so production feeds every
leaf or none -- a half-fed site composed rather than refused would run `futuresight` as
`gain_forecast` on one channel under its own name, which is the fake-arm hazard `_require`
exists for. An EMPTY window on every leaf is NOT that case: it composes to `()`, a run at the
end of its script, and stays distinct from `None`.

### 2. THE FINDING THAT OUTLIVES THE BUILD: the by-index rule is a SHAPE contract, not a pricing one

**Concatenating the two windows instead of zipping them by batch index prices IDENTICALLY.**
Measured, not reasoned: sabotaging the composer to concatenate breaks three structural tests and
leaves both Tier-1 pricing tests green.

The reason is that `_window_rates` FLATTENS -- it walks `for d in window: for sku, q in d.items()`
and accumulates `(total, events)` per SKU, so a disjoint union spread across 2w slots aggregates
to exactly what it aggregates to across w. Nothing that reads the window today can see the
alignment at all.

So the by-index rule earns its place on two other grounds, and they should be the ones quoted
next time: the composed DEPTH stays w rather than becoming 2w (which `_futuresight_window`'s
clamp at `n_batches` and the depth refusal both depend on), and the moment anything reads the
window per-batch -- the timed-lookahead family still sitting in this map's fog -- alignment
becomes load-bearing retroactively. `test_the_composed_depth_is_the_window_depth_not_the_sum`
pins it with that reasoning written on it, because a reader who discovers the pricing-invariance
on their own will otherwise conclude the rule was decoration.

### 3. The obligations, and the sabotage that proves they can fail

06's Tier-1 pair, in `Tests/unit/test_gain_plan.py`:

* **Equivalence** -- composed pricing is EXACTLY per-leaf pricing, an exact float and not an
  approximation, because the union is disjoint so a load reads only its own leaf's entries.
  Written TWO-SIDED: the defect the zip exists to prevent is one leaf's half being dropped, and a
  one-sided test sees that in only one of the two directions.
* **Sabotage** -- price the fulfillment load against store's window alone (the drop-the-other-half
  defect) and its SKU falls out of the aggregate, so it prices put-travel-only. Plus the vacuity
  guard the pair needs: the composed price must differ from the STATIC price, or every assertion
  above would hold with the window ignored entirely.

Five sabotages of `Inbound/site_space.py`, each caught, and the spread is the useful part:

| sabotage | what fails |
|---|---|
| `window=None` left in the return (33's careless landing) | 6 tests, incl. BOTH Tier-1 pricing tests |
| concatenate instead of zip by index | 3 structural tests, NO pricing test (section 2) |
| trust disjointness, drop the collision check | the collision refusal alone |
| fill in a half-fed site instead of refusing | the all-or-none refusal alone |
| drop one leaf's half of the window | 6 tests |

**The refusal's own test went vacuous and is replaced, not deleted.** Emptying the declaration
makes `test_an_uncomposed_field_is_refused_rather_than_dropped` parametrize over an empty set --
pytest reports it as a visible SKIP ("got empty parameter set"), and the composer's refusal loop
becomes unexercised code (memory `hand-run-test-tiers-rot-silently`). So
`test_the_generic_refusal_still_fires_when_the_declaration_is_empty` re-classifies a field for
the length of one call. That also proves the loop reads the DECLARATION rather than a
hard-coded field name, which is what the next field's classification depends on.

`Tests/unit/test_campaign_cells_can_run.py`'s sample became a per-leaf (store, ful) PAIR, because
the two leaves' values now have to be legal TOGETHER -- the old single value applied to both
leaves is a SKU collision the composer correctly refuses, which would have made the gate's own
agreement test fail for the right reason at the wrong place.

### 4. The glossary correction: FIVE sites, not the three the ticket named

The ticket listed `gain.py`, `site_space.py` and `whatif_config.py`. The surviving "upper bound"
phrasing was actually in `Inbound/gain.py` (x2), `Optimization/config/settings.py`,
`Optimization/run_simulation.py` (the CLI help) and `README.md` -- and NOT in `site_space.py` or
`whatif_config.py`, neither of which ever made the outcome claim. All five now say CLAIRVOYANCE
REFERENCE and state what it bounds: pricing accuracy, not achievable gain. `CONTEXT.md` was
already correct (32 wrote it). The gain module note gained the reading 32 pre-committed,
including that a reference arm finishing BEHIND a lawful one is a finding rather than a
pathology.

### 5. The probe: a futuresight cell, timed for the first time

`comparison_whatif_20260913_095904`, throwaway spec `_probe_fsight` (registered for the run and
REVERTED with this resolution -- it is not on `develop`), `PHASE2_RUN_DEFAULTS` verbatim,
`CAMPAIGN_DEPTH_DAYS` (40 site days), the one-pair reference view under `PROFILE_INPUT_DIR`,
`--workers 4 --no-analyze`. 8 coupled units, 16 leaves, 0 failures, nothing published.

TWO cells, and the second is 31's lesson applied: `gforecast` as an IN-RUN CONTROL, so the ratio
is a within-run one. Arms `('fifo', 'tmin')` -- 31's own pair, both gain adapters, both ends of
the arm-dependent multiplier it found.

Per coupled unit, seconds, 40 site days, both leaves:

| unit | `gforecast` (control) | `fsight_w5` | x |
|---|---|---|---|
| opt_fifo_norsl | 1,037 | 941 | 0.91 |
| uni_fifo_norsl | 1,027 | 952 | 0.93 |
| opt_tmin_norsl | 1,292 | 1,144 | 0.89 |
| uni_tmin_norsl | 1,559 | 1,451 | 0.93 |
| **cell mean** | **1,229** | **1,122** | **0.91** |

**THE VERDICT: 0.91x, far inside the ticket's ~2x threshold, so the `_window_rates` memo is
NEVER BUILT** -- recorded here the way 31 recorded the batch-script sharing, so the next reader
stops before optimising it. The memo would have been five lines plus 06's caching contract paid
against a computation that is not merely affordable but cheaper than the pole it was suspected
of doubling.

**And the direction is the interesting half.** A short window makes the arm FASTER, which is
mechanism and not noise: the window stands where the static rate stands, so a SKU absent from
five batches of realized demand prices put-travel-only and the evaluator skips the pick terms
entirely. Clairvoyance at w=5 buys pricing accuracy AND less pricing work. Nothing about that
transfers to `fsight_wall`, which is the opposite regime -- see the caveat below.

**Peak RSS is identical across the two cells to within 1 MiB** (4,072/4,073, 4,705/4,705,
4,072/4,073, 4,743/4,744): the ARM sets it and the policy does not touch it. That extends 31's
"pricing costs TIME, NOT MEMORY" to the futuresight family INCLUDING the window feed itself --
w=5 batches of copied `{sku: qty}` dicts on the view slot do not move the number. Disk held at
5.5 GiB per cell = 1.375 GiB per coupled unit, against 31's 1.37. The reshape was 234 s against
31's 221 s.

### 6. The clocks: unit walls transfer between runs, SETUP does not

This refines 31 section 5 rather than repeating it, and it is why the number above can be quoted
two independent ways.

The `gforecast` control reproduced 31's published pole to **0.6%** -- cell mean 1,229 s here
against 1,236 s there, and every one of the four units within 2% (1,037/1,038, 1,027/1,049,
1,292/1,296, 1,559/1,563). But the FREEZE in the same run took **1,208 s against 31's 966 s**,
25% slower. So the two stages have different clock sensitivities: the pooled per-unit work
reproduces across runs and days, while the serial setup does not. 31's "the gate run's absolutes
are NOT reproducible" holds for setup, and its per-unit poles are steadier than it claimed. The
0.91x is therefore a within-run ratio AND a cross-run comparison, and they agree.

### 7. What this does and does not settle for phase 2

The axis is alive: `uncomposable_policies` returns `{}` for every policy the phase-2 spec names,
`validate_spec` no longer refuses `inbound_policies` by name, and **phase 2 can now launch in
any form** -- which until this landed it could not, not even for its eight runnable cells.

**`fsight_wall` (w='all') is STILL UNMEASURED and phase 2's sizing stays a lower bound for that
one cell.** Nothing here transfers to it: the mechanism that made w=5 cheap is SKUs missing from
a short window, and an 'all' window is the regime where that stops being true and the
aggregation grows with the remaining script instead. 22 measured 1.00 drains/batch structurally,
which bounds how often the aggregation happens but not how much it costs each time. 32's
ten-cell figure (120 coupled units, ~164 GiB) is otherwise unchanged; the w5 cell prices
slightly UNDER what 32 assumed for it, so the estimate moves the safe way.

### 8. Found on the way, and it blocked more than this probe

**The one-pair reference view's junction was DEAD.** Its leaf pointed at a drive letter that no
longer exists on this machine, so the launch failed with `No inventory+affinity DB pairs found`
-- the misleading error memory `results-drive-location` warns about, naming the view rather than
the junction under it. The catalogue itself was intact; only the link had rotted.

This is not a probe-only problem: **phase 1's own documented launch command points at that same
view**, so the campaign's next step would have failed identically and reported a missing
catalogue. Repaired (junction repointed at the live catalogue, verified to resolve to exactly one
pair) with the user's approval, since it is outside the repo.

### 9. Gates and what is owed

Unit tier **2423 passed, 1 skipped** (the skip is section 3's now-empty parametrize, deliberate
and visible). Yard/receiving/coupled e2e **23 passed**. `path_guard`, `docref_guard`,
`verify_context`, `contract --check`, `profile_tree --check` all clean. **No schema event** --
the run-tree head `341e1422457c` is unchanged; the probe's canary re-recorded the source
fingerprint while the throwaway spec was in the tree, so preflight was re-run after the revert
and re-proved the tree shape UNCHANGED against the real one.

Owed: the derived arch layer, as at 09/13/15/21/28/30 -- this ticket's share is the renamed
refusal test; the two uncatalogued test files pre-date it. Pre-existing and NOT folded in: the
memory mirror is 45 files behind (`verify_memory`), which is `memory-maintainer`'s.

## Comments

2026-09-13, from 32: the two cells this unblocks stay OUT of the recommendable set
unconditionally, and the campaign's published reading is pre-committed in 32 section 3 --
including what it must NOT say if futuresight loses to `gforecast`, which is a live outcome
rather than a pathology. Whoever builds this does not need to act on that, but should not
publish anything that contradicts it.

2026-09-13, from resolving [Gate the campaign axis on what a coupled run can do](33-gate-the-campaign-axis-on-the-coupled-model.md):
**the zip is now THREE edits, not the two 32 counted, and a test will tell you if you stop at
two.** The refusal at `Inbound/site_space.py` is no longer a hand-written `window` branch: it
is driven by `UNCOMPOSED_VIEW_FIELDS`, which partitions `SpaceView.__slots__` with
`COMPOSED_VIEW_FIELDS`. So landing the zip means:

1. move `'window'` from `UNCOMPOSED_VIEW_FIELDS` into `COMPOSED_VIEW_FIELDS` (the partition is
   asserted exhaustive, so the two halves of this move are not optional);
2. merge the field in the composed view's return, where `window=None` is hard-coded today —
   `test_a_composed_field_actually_survives_the_composition[window]` fails if you do 1 without
   2, which is the careless-landing case and was sabotage-tested;
3. empty `_KNOWN_DEAD` in `Tests/unit/test_campaign_cells_can_run.py` — it pins
   `{fsight_w5, fsight_wall}` today and its failure message says exactly this.

The zip RULE itself moved with the refusal and now lives on the `UNCOMPOSED_VIEW_FIELDS`
declaration, verbatim (zip by batch index, union each `{sku: qty}` pair, disjoint by regime).
Nothing else about 32's plan changes: the equivalence test it asks for (composed pricing ==
per-leaf pricing) is still the Tier-1 cross-check, and `_window_rates` stays probe-gated.

One more thing this ticket now unblocks rather than merely fixing: until it lands,
`validate_spec` REFUSES the phase-2 spec by name, listing `fsight_w5` and `fsight_wall`. That
is correct — those cells cannot run — but it means phase 2 cannot launch in any form, not even
its eight runnable cells, without either this build or dropping the two cells from the axis.
