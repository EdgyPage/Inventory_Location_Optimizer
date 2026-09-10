# Band the own-bin share and the free-index depth

Type: grilling
Status: resolved

Graduated 2026-09-09 from
[Re-check the reference pair under the first-time guarantee](31-recheck-under-the-first-time-guarantee.md),
which read the two instruments under the solved floor. HITL. Skills: `grilling` +
`domain-modeling`.

## Question

The rework clause REPORTS the own-bin share and the free-index depth and judges neither
("Build the empty-first top-up", 24): no steady state had been observed. Three readings now
exist, all fifo: the 2026-09-08 pair at the 1.0-line floor (own-bin share exactly 0.000 on
every day of both leaves; free index never below 1,197,833 of 2,096,050 bins, 57%) and the
2026-09-09 pair at the solved 1.27-line floor (0.000 again; floor 1,450,954 store / 1,399,264
fulfillment of 2,466,650, 59% / 57%).

Under ADR-0003 a top-up consolidates into the SKU's own bin ONLY when no empty bin fits, so a
non-zero own-bin share says a size bucket's free index ran dry -- the same finding the repack
term already fails on, one step earlier. Decide:

1. Is the own-bin share's band **exactly zero** (any top-up is a sizing finding, judged like a
   repack), or a declared tolerance (some buckets legitimately run tight)?
2. What free-index depth does the era declare -- a floor as a fraction of bins (the readings sit
   near 57-59% with 60 store buckets and 3 fulfillment buckets), per bucket or in total -- and
   is it judged or only reported? The put-away pricer assumes a free bin is always found
   (`s_put`, no search term); a floor is where that assumption is defended.
3. Whether either reading must be taken PER BUCKET rather than per leaf: a 3-bucket section can
   run one bucket dry while the total stays at 57%.

## Done when

- Both instruments have a declared band (or a recorded decision to keep one as a report), the
  rework clause judges accordingly, and its docstring names the readings the band was drawn
  from. A sabotage test proves each new term can fail.

## Answer

**RESOLVED 2026-09-10.** Eight decisions, taken on readings the ticket did not have -- because
the ticket's own premise was an instrument artefact, corrected first.

**The 57-59% was never headroom.** `Inventory_Manager.free_bin_depth` sums the free index over
the WHOLE geometry, and a leaf simulates only its own section, so each leaf's `batch_stats.free_bins`
counts the other channel's untouched bins as free. Read against the record's own `fielded` block
(`coverage.final.<ch>.fielded.buckets`, which already stamps requirement / capacity / free PER
BUCKET at setup), the 2026-09-09 run (`comparison_20260909_204522`) says:

| leaf | bins in section | free at setup | share | drop over 40 days | drop in days 20-39 |
|---|---|---|---|---|---|
| store | 1,229,750 | 220,817 | 18.0% | 6,763 | 3,239 |
| fulfillment | 1,236,900 | 187,793 | 15.2% | 18,279 | 7,305 |

That share is the declared 0.85 fill plus aisle rounding (`bin_slack_pct 19.85` in the leaf
config), and both series FALL on every one of the 40 days. Fulfillment's rate decays (~540/day in
the first half, ~365 in the second); the store's does not yet, because a store SKU sees a line
every ~400 days and most have not had a first partial pick. The mechanism is the rules already
decided: a partial line leaves a remnant, the top-up lands in an EMPTY bin (ADR-0003), and
smallest-first drain clears the remnant only on a later line -- so a picked SKU settles at two bins
most of the time, three sometimes, and returns to one only when a line takes its whole on-hand.
The stationary extra-bin count per SKU is a small Markov chain over the SKU's line law: a closed
form in this map's sense that nothing derives today. ADR-0003's consequence 1 ("the free index
runs to near zero in steady state") is therefore the PREDICTION of this, not a contradiction; the
40-day window cannot see where the store's slide ends. (Per size class the only reading is the
keyframe sidecar at batches 0 and 25: store `small` 143,581 -> 150,282 occupied, `singleton`
138,732 -> 133,168; the singleton bucket is where remnants clear fastest.)

**Two more facts the ticket's framing missed.** `_candidates_raw` spills UP to a larger tier of
the same handling x category before the own-bin rung is reached, so the rung fires only when a
whole TIER CHAIN is dry (singletons have no spill); a tier spill is the FIRST deviation from the
put pricer's per-class assumption and is recorded nowhere. And the per-bucket free index exists in
RAM (`_index: dict[BinKey, list]`) but is persisted only as the total.

**Decisions.**

1. **The own-bin share's band is exactly zero.** A top-up into an occupied bin is the repack
   finding one rung earlier -- a whole tier chain dry -- and fails the rework clause with a
   sizing message. No tolerance: nothing on the record prices a top-up differently from a
   placement, so a tolerance would have no number to derive from.
2. **The tier spill is recorded and judged at zero** the same way: a `batch_stats` flow and the
   landing tier against the unit's own on `bin_placement`. It is the one of the three events
   that can fire while own-bin share and repacks both read 0.
3. **The free-index depth is recorded per bucket per batch** (a narrow table keyed by BinKey,
   ~60 store + 3 fulfillment rows per batch), which counts only the leaf's own section by
   construction. A leaf total dominated by near-empty structural-floor buckets says nothing about
   the busy ones.
4. **No knob.** The zero expectations are the ADR's own claim, hard-coded in the clause -- not a
   staffing key beside `f_repack`. A run that wants a tight warehouse gets the finding it asked for.
5. **ADR-0003 gains a dated observation** (the slide toward a stationary fragmentation, not a
   reversal), and *Free index*, *Bucket* and *Tier spill* enter `CONTEXT.md`. Both done this session.
6. **The depth is REPORTED per bucket** against its stamped setup free, and judged only through the
   events. A typed level floor was rejected: it certifies nothing on a store whose slide outlives
   the window and fails a healthy run the moment the window moves. The trajectory band (window
   drawdown within a band of the closed-form expected drawdown for those days) waits for the
   closed form and is fog.
7. **The planner's fill headroom becomes DERIVED** from the expected stationary extra bins per
   bucket -- fill = requirement / (requirement + E[extra]), floored at a declared minimum headroom
   -- so "the warehouse is sized to hold its declaration" also holds the fragmentation base stock
   creates. It is 29's crew move applied to the shelf. It moves the reference warehouse and is a
   comparability boundary; its own ticket, after the closed form.
8. **`batch_stats.free_bins` keeps its name and its whole-geometry meaning**; the artefact is
   named in its `sim_semantics` entry and the clause reads the new table. Rewriting a level's
   meaning under an existing name is the trap `cut-is-a-level-not-a-flow` records.

**Graduated:** [Build the per-bucket free index, the tier spill and the judged rework clause](33-build-the-per-bucket-free-index-and-judged-rework.md)
(task, unblocked), [Derive the stationary fragmentation closed form](34-derive-the-stationary-fragmentation-closed-form.md)
(task, unblocked, derivation reviewed before it lands), [Derive the fill headroom from the fragmentation](35-derive-the-fill-headroom-from-the-fragmentation.md)
(task, blocked by 34). The trajectory band joined the fog. Memory: `free-bins-counts-the-whole-geometry`.
