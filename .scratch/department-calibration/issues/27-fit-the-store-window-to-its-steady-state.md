# Fit the store's window to its own steady state

Type: grilling
Status: resolved

Graduated 2026-09-08 from [Re-read the check under the fielded floor](25-re-read-the-check.md),
failure 2. HITL. Skills: `grilling` + `domain-modeling`.

## Question

The store section does not reach a steady state inside the 40-day reference window, so no clause
that judges a LEVEL can be read on it. Decide what the window should be -- or whether the window is
the wrong lever entirely.

**What 25 measured** on `comparison_20260908_075736` (fifo, 40 era days, the reference pair):

- days drained **5/40, and 0/20 inside the measured window 20-39** -- every one of the last twenty
  days capped;
- picking realized **0.905 across days 0-39 (IN band of 0.850) but 0.963 across days 20-39 (OUT,
  +0.113)**; the second half works 693,672 s/day against the first half's 609,703, **+14%**;
- yet seconds per unit picked is **FLAT**: 100.41 over days 0-4 against 99.19 over days 35-39. The
  store is not getting more expensive per unit, it is doing more units. Fulfillment is flat too
  (18.52 -> 18.72), and ADR-0003's own-bin share of 0.000 rules out fragmentation from the other
  side;
- `missed_share` **rises for about thirty days and then falls**: 0.109 -> 0.229 across days 0-39
  (+0.120), but 0.288 -> 0.170 within days 20-39 (-0.118). The measured window is sitting inside a
  hump.

**Why this may be structural rather than a warm-up.** The store's implied coverage puts **every**
store SKU at Q = 1 on the line floor, running base stock (memory
`coverage-in-days-floors-the-store-section`): each pick empties a bin and each replenishment opens a
fresh one -- 77,544 put events for 249,640 units over the run. A shelf shaped like that may have a
settling time set by the replenishment cycle rather than by anything a window can outlast, in which
case a longer window is expensive and still wrong.

**The open questions**, in the order they gate each other:

1. Is the hump a transient with an end? Days 0-39 alone cannot say. The cheapest evidence is one
   longer run (60 days? 80?) on the store leaf only -- but a longer window multiplies every later
   campaign's cost, so the length is a decision, not a default.
2. If it settles, does the reference window move for BOTH channels or only the store? Fulfillment
   already passes its `missed_share` trend clause at 20-39 (level 0.118, trend -0.010), so a longer
   window buys fulfillment nothing and costs it wall time. The era currently declares ONE window for
   the site ([Sequence the inbound funnel](05-sequence-the-inbound-funnel.md), decision 4), and the
   inbound map's
   [Re-size the funnel in site days](../../inbound-optimization/issues/24-resize-the-funnel-in-site-days.md)
   takes its depth from it -- so a per-channel window is a change to that contract, not a parameter.
3. If it does NOT settle, the question is not the window at all: it is whether a Q = 1 store shelf
   can be in equilibrium under the era, which reaches back to
   [Choose the coverage floor](15-choose-the-coverage-floor.md) and the floor's own premise.
4. Whatever the answer, `missed_share`'s LEVEL clause (store 0.229, fulfillment 0.118, both against
   0.078 expected) cannot be judged until this resolves -- it is currently being read from inside
   the transient. Say explicitly whether the level is expected to land at 0.078 once settled, or
   whether the stamped fill rate is itself the thing to re-examine.

Do NOT re-run the 40-day check to answer this: 25 already took it and its readings are above.
Whatever run this ticket decides on is a new shape, and its cost is part of the decision.

## Done when

The reference window is declared (length, per-channel or not, and the reasoning), OR the effort is
redirected at the store's shelf shape with this ticket recording why the window was the wrong lever.
Either way the answer names what happens to `missed_share`'s level clause.

## Answer

Resolved 2026-09-08 (HITL, three grilling rounds, two read-only fact-finding passes over
`comparison_20260908_094846`). **The window was the wrong lever.** The reference window stays
**40 days with days 20-39 measured, one window for the site** (05 decision 4 untouched; the user
may play with the measured range later, which is a parameter, not a contract change). No
diagnostic run was taken and none is needed.

### What the store's series actually show

- **Nothing is lost.** Every unit the store misses or cuts is re-offered the next day
  (`roll_over_unpicked` is on under the era). Lead is 0 batches for every SKU on this pair, and
  `check_reorders` fires the depleted SKU's top-up and lands it before the next day's picks are
  built, so 92% of stocked-out SKU-days are back on shelf the next day and only 8% of unstocked
  units miss twice. Picked (254,646) plus standing carry at day 39 (1,171) equals the script's
  demand (255,817) exactly. (The ticket's "249,640 units" was the PUT total, not the picks.)
- **So the crew was sized on the wrong quantity.** `derive` priced 25 pickers on SERVED units
  (demand x the 0.922 fill rate = 5,647/day at 108.4 s/unit = 612,000 s = 0.85 of the cap). The
  crew must pick ALL of demand: on the record's own numbers that is 663,800 s = **0.92**; the
  sampled script (6,395/day, +4.4% over the expectation, ~1 SE) at the realized 102.4 s/unit is
  **0.91**. Fulfillment has the identical defect and passed only because its script came in 6.7%
  UNDER its expectation.
- **The hump is labour overflow, not supply.** The supply-only missed share is FLAT at 0.070 in
  every window (record expects 0.078 -- it would pass). The entire rise-and-fall is
  `unpicked_daycut`: fresh demand ran heavy on days 20-29 (6,843/day needs 0.99 of the cap), the
  labour carry built to a 3,505-unit peak on day 24 and drained back to 104 by day 35 as demand
  fell to 6,201/day. A queue near saturation, not instability: on-hand stock is flat (2,510,168
  -> 2,505,162, -0.2%), and lead, put, dock and reorder queues are 0 on every batch. There is no
  replenishment transient to outlast.
- **The instrument mislabels it.** `_missed_share_clause` reads `items_demanded - total_items`,
  which is day-cut PLUS stockout, with re-offered units counted again in both terms
  (`items_demanded` includes `_pending`); its docstring, `frames.MISSED_REASONS` and
  `throughput/missed.py` all say it is supply-only. Cumulative `items_demanded` is 312,302
  against 255,817 fresh.
- **"Every day drained" cannot hold at any sane crew.** The day's line count is one Gaussian
  draw with a DECLARED cv (store 0.05/0.15 = 1/3, fulfillment 1/4; `settings.py:388-391`, kept
  through `batch_content`), so fresh demand ranged 3,500-10,654 units on a 6,395 mean and 14 of
  40 days exceed what a full 28,800 s day can pick. Fulfillment drained 12/20 window days with
  utilization IN band.

Two premises of this ticket did not reproduce and are corrected here: seconds per unit is NOT
flat (task makespan over picked units rises 97.9 -> 106.7 across the halves; the "100.4 -> 99.2"
figure matches no column combination), and the `work_events` table carries 77,544 `receive`
rows despite `recv_crew_size 0` (unexplained; handed to 31 to read).

### Decisions (all the user's, in three rounds)

1. **Drained is the equilibrium.** A section whose days never drain is out of equilibrium
   whatever its levels do; stationarity of a backlog is not a steady state.
2. **One site window** (40 / 20-39). Per-channel run lengths ruled out (they make the funnel's
   site-day depth ambiguous).
3. **No diagnostic run**; the shelf shape is not the cause and is not reopened for this reason.
4. **The crew is sized on DEMANDED units, never on served.** Fill rate is a supply expectation
   for the supply clause only; it never reduces load.
5. **The derivation prices the actual sampled script** (the charter's knowability), with the
   declared law used for the guarantee and both stamped in the record.
6. **The joint first-time completion rule replaces `rho_pick`.** The picking crew is staffed so
   that a pick is completed the first time -- reached on its day AND filled from the shelf --
   with confidence >= 0.95, as a CLOSED FORM over the chosen inventory and the declared day law.
   Per PICK (expected cut share of units), not per day (the 95th-percentile day fitting is
   staffing to the peak). Equal split: each side at sqrt(0.95) = 0.9747 -- the line floor is
   SOLVED so the stamped fill clears it (~1.26-1.27 lines on this catalogue, ~19% more stock),
   the crew is solved so the expected cut share is <= 0.0253. One declared scalar, nothing else
   authored. The put and receiving crews keep rho (0.85); a trailer backlog on heavy loads is a
   legitimate, reported scenario, never legislated away.
7. **The declared input flips: DEMAND is declared, the crew is DERIVED** (reverses 01's "pickers
   are the one declared input"; ADR-0004). The declaration is stored in the sampler's native unit
   (fraction of the section's SKUs per day) so it scales to any catalogue; its value on the
   reference pair is today's derived content (588.65 store / 2,903 fulfillment lines a day), so
   the warehouse, the levels and the script family stay put and only the crew moves (estimate:
   store 25 -> ~32 pickers at a mean utilization near 0.72). `--store-pickers` / `--ff-pickers`
   are REFUSED under the era like the legacy crew flags; two demand flags replace them. Pickers
   become `derived` in the record, demand `declared`.
8. **The closed form is exact enough to promise on.** Units per day: `E[U] = mu_L * m1`,
   `Var[U] = mu_L * v + sigma_L^2 * m1^2`; the Gaussian line count carries all but 0.001 of the
   day's unit cv (0.3343 vs 0.333 declared), so the normal quantile / partial expectation
   `E[(W-C)^+] = sd * [phi(z) - z(1-Phi(z))]` over the routing chain (`expected_pick(n)` monotone
   in n, Gauss-Hermite nodes already in `expected_travel._nodes`) is the guarantee. Fill is a
   pure catalogue closed form while every SKU sits on the floor (`coverage.py:200-253`).
9. **`missed_share` splits into two clauses**: SUPPLY-only (stockout units over FRESH demand,
   re-attempts counted once, level against 1 - fill, trend as today) and LABOUR (the realized
   cut share within a declared tolerance of the STAMPED expected cut share, plus the standing
   labour carry bounded and its half-window means not trending). The strict "every day drained"
   clause is replaced by that labour clause; a band around a stamped expectation, never around
   the declared scalar.
10. **Both leaves are re-checked** after the build before the era's numbers stop being
    provisional and 05's hold on the inbound funnel lifts.

### What happens to `missed_share`'s level clause

It is read from inside a labour overflow it was never meant to measure. Once split (30), the
supply level lands at the stamped 1 - fill (0.070 realized against 0.078 today; against ~0.025
once the floor is solved for 0.9747), and the labour level is judged against the derivation's
expected cut share. Neither is judged until 31 reads them under the new derivation.

### Graduated

- [Declare the demand and derive the crew from the joint first-time confidence](29-declare-demand-derive-crew.md)
  (task, AFK) -- decisions 4-8, the record, the flags, ADR-0004.
- [Split the missed-share clause into supply and labour](30-split-the-missed-share-clause.md)
  (task, AFK, blocked by 29) -- decision 9 and the instrument defect.
- [Re-check the reference pair under the first-time guarantee](31-recheck-under-the-first-time-guarantee.md)
  (task, blocked by 29, 30) -- decision 10; also reads the `receive` rows and the fill/floor
  fixed point.

Glossary: **First-time completion** added; **Utilization**, **Headroom**, **Equilibrium** and
**Staffing record** amended (`CONTEXT.md`). ADR-0004 written.
