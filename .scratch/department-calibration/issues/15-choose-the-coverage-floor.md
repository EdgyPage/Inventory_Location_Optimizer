# Choose the coverage floor

Type: grilling
Status: resolved
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

## Answer

**Resolved 2026-09-06 (grilling, three rounds, all recommendations accepted with one user
amendment).** The floor is a LINE, and the SKU carries the law the line is drawn from.

1. **The floor is one line, on both levels.** `Q = max(round(coverage_days x d_s), L_s)` and
   `rp = min(Q - 1, max(round(d_s x (lead + safety)), L_s))`, where `L_s = ceil(E[line of s])`
   is the SKU's own mean line rounded up. A SKU whose coverage is shorter than its inter-line
   interval collapses to `rp = Q - 1`: every pick fires an order-up-to for what it took -- the
   textbook base-stock (S-1, S) policy, which `_fire_reorders` already implements on inventory
   position, so it costs no new mechanism. `coverage_days` now means "hold this many days of
   demand, never less than one pick's worth". Rejected: per-channel coverage (restates the
   generation-batch artefact as a chosen number and never exercises the store's put/receiving
   bands), the catalogue's own levels (breaks the charter's no-implicit-batch line), a 1.5-unit
   demand-mass threshold (a cliff between regimes with no physical meaning). Consequences
   accepted: the store drops from ~5 lines a SKU to ~1, its warehouse shrinks to roughly a
   quarter, reorders arrive as line-sized cartons rather than the pallets the generator's
   `stock_plan` authored, and the fixed point re-sizes everything.
2. **The height is the mean line rounded up** -- one physical number per SKU, no service-level
   knob. The shortfall (~30% of picks exceed the shelf on a lambda=10 SKU, ~7% of first-pass
   units) is a REPORTED level, carried a day and served after the restock.
3. **`floor_lines` is a declared staffing input** (default 1.0, provenance `assumed`) on
   `STAFFING_KEYS`, all five seams -- the record names the floor the run shipped with.
4. **`coverage_days` 10 / `safety_days` 2 stay** as `assumed` defaults. On the reference
   catalogue both sections sit 100% on the floor at any coverage below the fastest SKU's
   inter-line interval (~25 days fulfillment, ~200 store at the fixed point), so the two days
   are inert here and the record's floor share says so. A default that bites on this catalogue
   would be tuned to it, which decision 9 rules out.
5. **The record stamps the expected first-pass fill rate** per section, demand-weighted
   `E[min(X, S)] / E[X]` under base-stock, so the report's `missed_share` LEVEL has an
   expectation to be read against -- the same expectation-not-measurement discipline as 13.
6. **The lead pipeline is stamped, not inferred.** Under the era each SKU carries
   `pipeline_qty = round(d_s x lead)`; `_fire_reorders` prefers it when present and keeps the
   `rp x lead / (lead + 1)` heuristic otherwise (flag-off byte-identical). The heuristic assumed
   `rp` encodes lead-time demand; under the line floor `rp` encodes a line, and a floored SKU
   with a 2-day lead would have ordered up to ~1.7 lines.
7. **The drained clause judges LABOUR only** (amends decision 04): a day drained when nothing
   was cut, no put or dock work stands, and no `unpicked_daycut` carry survives it. The two
   supply reasons (`unpicked_unavailable`, `unpicked_unstocked`) stay recorded and are judged
   by `missed_share`. Today `_shift_close_out` counts them as standing work, so under lumpy
   lines NO finite floor can ever drain a day (ticket 09: 0/20 drained with pickers in band and
   every task realized).
8. **No wave.** The window verifies put and receiving utilization in band; under base-stock
   replenishment is a trickle in lockstep with picks from day `lead` on, and there is no wave
   at all. The "reorder wave inside the window" requirement was a calibration-window artefact
   and is retired; on the store the record's answer is explicitly "no wave".
9. **The catalogue is a given** and the rule is distribution-agnostic. The store's shape
   (239,938 SKUs sharing 556 lines a day, a line per SKU every ~431 days) is a fact the record
   reports, not something this map reshapes.
10. **AMENDMENT (user): the line distribution is stamped on the SKU.** Every
    distribution-dependent number -- mean line, fill rate, pipeline, batch content -- is read
    off a first-class **line distribution** object on the SKU (family + parameters; `mean`,
    `cdf`, `quantile`, `expected_min`, `sample`), written to the inventory file as two columns
    (family, params JSON -- the `creation_plan` table's precedent) through the schema pipeline
    (the `inventory_db` family), and never re-derived by a consumer. Sampling (`Order.py`,
    `Demand.sample`) and every closed form (`expected_travel`, `staffing`, `coverage`) become
    readers of the one object; the Poisson family keeps Knuth's sampler and its draws, so a
    flag-off run is byte-identical. A pre-stamp vintage reconstructs Poisson(lambda) from
    `demand_qty_rate` at load, provenance `assumed`, named in the record; no regeneration is
    forced. Scope: the line QUANTITY law only -- the arrival law is the batch sampler's one
    site. Found while grilling: `staffing.py` takes a line as `max(1, lambda)` and
    `coverage.py` as `lambda + e^-lambda` -- two answers for one quantity, the drift the stamp
    ends (the correction is a small change to era batch content, stated in the record).

**What the 40-day window verifies on the store:** put and receiving in band, days drained
under the labour-only clause, the store's fill rate against its stamped expectation -- and
explicitly no wave.

**Graduated (three AFK task tickets):**
[Stamp the line distribution on the SKU](16-stamp-the-line-distribution-on-the-sku.md),
[Build the line floor](17-build-the-line-floor.md) (blocked by 16),
[Narrow the drained clause to labour](18-narrow-the-drained-clause-to-labour.md).
Glossary: *Stock coverage* rewritten; *Line distribution*, *Line floor*, *Base stock* added.
