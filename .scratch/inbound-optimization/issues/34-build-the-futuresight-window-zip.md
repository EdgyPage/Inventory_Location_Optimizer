# Build the coupled futuresight window zip

Type: task
Status: open
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

## Comments

2026-09-13, from 32: the two cells this unblocks stay OUT of the recommendable set
unconditionally, and the campaign's published reading is pre-committed in 32 section 3 --
including what it must NOT say if futuresight loses to `gforecast`, which is a live outcome
rather than a pathology. Whoever builds this does not need to act on that, but should not
publish anything that contradicts it.
