---
status: accepted
date: 2026-09-12
---

# The fulfillment fill-law gap was a sampler artifact, so the fill law is not corrected

For a month the era's stamped first-pass fill under-predicted the realized miss on the
fulfillment leaf by roughly four times -- 0.1044 realized against a stamped 0.0251, a +0.0793
gap outside the equilibrium instrument's 0.020 supply band on every fulfillment arm, while the
store control passed at +0.0054. Three tickets chased a correction to the law. One fitted a
multiplier on the SKU's prior-line probability; another argued the structural cause was that
`coverage.py` prices a prior line at the SKU's line share, `freq / sum freq`, which is the
sampler's base WEIGHT and not the probability the sampler includes the SKU in a batch, and
chartered a generator-drawn draw probability to replace it. Then the sampler itself turned out
to be broken: v2 re-drew SKUs it had already selected and the sku-keyed `Batch.items` collapsed
the repeats, so every batch delivered fewer distinct lines than the era declared. **We decided
that the gap was an artifact of that defect and that the fill law is correct as written.** On
`comparison_20260912_134002`, the same reference pair under the v3 sampler, the instrument reads
12 arms judged and 0 failed: fulfillment supply falls to +0.0033 against the same 0.0251
expectation in the same 0.020 band, and the store tightens from +0.0054 to +0.0023. The defect
was 95.5-97.1% of the fulfillment gap. No correction is made to the law, the draw probability is
not landed, and the era is CALIBRATED again on both leaves.

## Considered options

- **The fitted multiplier** -- scale the prior-line probability by a per-channel `m`, measured on
  the reference run at 3.968 fulfillment / 1.739 store, closing 71% of the fulfillment gap and 52%
  of the store's. Rejected because the concentration it fitted was manufactured by the defect, not
  by the demand: under v3 the touched-SKU shortfall the fit rested on goes -25.2% to +3.3%, the
  prior-line probability goes 3.74x the record's Poisson to 0.87x, and the lag-1 suppression
  disappears (0.838x to 1.014x). A fit that closes most of a gap is not evidence the gap is real.
  The rejection is sharper than the one the chase intended: the residual errors on the two
  channels now run in OPPOSITE directions -- the record over-prices fulfillment by 16% and
  under-prices the store by 19% -- and a single positive multiplier cannot produce that shape,
  so the fit would have been wrong even had the gap survived.
- **Land the draw probability through every closed form** -- replace the line share with `p_s`
  characterised on the generator. Correct in itself, and the distinction it rests on is real
  enough to be glossary language now. Rejected on cost: `p_s` had exactly one consumer, a law
  that under-predicted the realized miss by 4x, and that law now reads in band on both leaves
  with no correction at all. Landing it would buy a comparability break to fix an intermediate
  quantity nothing is waiting on.
- **Accept the fulfillment leaf as out of band and press on with the campaign** -- rejected
  before the sampler was found, and moot after: the leaf is in band.

## Consequences

- A ~18% two-sided error on the record's own prior-line probability, measured on the generator,
  is RECORDED and not corrected. The instrument judges the realized result, and it passes on both
  leaves; a future effort that needs the intermediate quantity itself starts here.
- The v3 flip is the sixth comparability break (after the per-item charge `fc7a46a5`, the
  placement pools `a033aff`, ADR-0003's drain order, the derived fill, and the lead-aware record).
  It moves every batch sequence, so absolute pick, travel, throughput and labour numbers do not
  cross it. The geometry does NOT move with it: `n` is declared, so the floors, the levels, the
  warehouse and the derived picking crew came back bit-identical, and only the put-away and
  receiving crews -- denominated in lines and packs rather than in declared units -- moved
  (60 to 64 and 22 to 23).
- `Optimization/simdriver/draw_probability.py` and its 13 green tests stay in the tree with no
  caller, as the starting point for the sampler effort that owns the generator's design. Its two
  `_drawp_*.npz` artifacts are v2 archive, not input.
- Every number the chase produced was measured under v2 and must not be carried forward: the
  fitted multipliers above, the cluster-mate densities that argued the channel asymmetry, and the
  -24.5% touched-SKU shortfall. The METHOD of that chase survives -- reproduce the baseline
  exactly, keep the store as a live control, separate the defect from the mechanism -- and the
  store control is what caught two dead variants before this one.
