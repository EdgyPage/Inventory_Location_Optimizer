# Choose the coverage floor

Type: grilling
Status: open
Blocked by: 14

Graduated from [Rescale stock coverage at setup](14-rescale-coverage-at-setup.md), which
built the rescaling as specified and measured what its unit floor amounts to on the reference
catalogue (`assets/expected-travel-derivation.md`, section 5a). HITL: a decision, not a build.
Skills: `grilling` + `domain-modeling` (the glossary's *Stock coverage* entry and the charter's
"no bespoke conversions implicit in the inventory" line are what the answer must square with).

## Question

The store section of the reference pair is a slow-moving catalogue: the average SKU carries
~0.024 units a day (a line every ~430 days, ~10 units a line), and its generation-batch levels
were worth ~1,785 days of coverage; fulfillment's were worth 278. Under the formula as
decided -- `Q = max(1, round(coverage_days x d_s))` -- ANY coverage short enough to land the
first reorder wave inside a 40-day window (the wave lands at `coverage - safety` days for
SKUs above the floor) puts every store SKU on the one-unit floor: at the 10-day default
100% of store SKUs and 100% of store demand sit at Q = 1 (30 d: 87% / 63%; 90 d: 46% / 13%;
365 d: 18% / 1%), and a Q = 1 SKU picked in ~10-unit lines stocks out on every line. So the
ticket's two aims -- a wave inside the window and a store that is still a warehouse -- are
mutually exclusive on this catalogue, and the default is provisional.

Decide what the floor IS, and therefore what `coverage_days` means for a slow mover:

1. **A line-sized floor** -- `Q >= round(λ_s + e^{-λ_s})` (one line's units), so no SKU
   stocks out on a single line and coverage counts from there. Changes the formula the
   closed form and the record were decided on; the reorder point then needs its own floor
   rule.
2. **Per-channel coverage** -- two declared numbers (the store's ~5-year levels are the
   catalogue's honest shape; fulfillment's 278 days rescale cleanly to a month). Keeps the
   formula; makes the store's first wave land outside any window by design, and says so.
3. **The catalogue's own levels** -- `coverage_days = None` means "do not rescale"; the era
   keeps the generation-batch shape and the rescaling is opt-in per run. Cheapest; leaves
   the charter's "no bespoke conversions implicit in the inventory" line unmet on the store.
4. **A demand-mass floor** -- rescale only SKUs whose `coverage_days x d_s >= 1.5` and hold
   the rest at the catalogue's level, recording the split.

Whichever wins, the answer must name the default the era ships with, its provenance, and
what the 40-day window then verifies on the store side (a wave, or explicitly no wave).

Done when: the rule is decided and recorded, `coverage.py` / the defaults follow it (a task
ticket if the rule changes the formula), and the glossary's *Stock coverage* entry says what
the floor is.
