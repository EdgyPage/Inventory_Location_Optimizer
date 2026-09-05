# Define the calibrated era

Type: grilling
Status: resolved

## Question

Decide the MECHANISM of the calibrated era, and which levers calibration may legally move.
This is the map's root decision: every other ticket consumes its outputs.

**(a) Is the era `SHIFT_DRAIN_OR_CAP` + `releases_per_day`, as-built?** The dormant
machinery already implements the charter's day model: `SHIFT_DRAIN_OR_CAP=True`
(`Optimization/config/settings.py:197`, off by default) forces `cut_at_day_end` on (a cap
without carry loses demand), gives the receiving crew the SITE's day — one boundary for
every crew, overriding the dock's own day knobs — and closes each day with the
drain-or-cap ledger (`timeline.shift_end`: the drain instant when the day drained, else
the cap; days stay origin-aligned). `releases_per_day=N` (`Warehouse/kernel/timeline.py:231`)
cuts the day into N fixed slots — batch i releases at `max(ready, origin + i·cadence)`,
`day_of(i) = i // N`, and `released_late` records every second a batch lags its slot.
Under pacing, the dock's day-remainder-per-batch grant becomes CORRECT, because a batch no
longer outlives its day — the clock mismatch the pilot found dissolves without a mechanics
rewrite. The alternative is a soft equilibrium (no whistles, staffing sized so makespans
happen to fit): weaker, no era break, and "the day" stays a fiction the moment any queue
drifts.

**(b) The declared shift length.** `work_day_seconds` defaults to `shift_seconds()` =
28,800 s. Is one 8-hour production stretch the declared S, or something else?

**(c) Which levers may calibration move to make a batch fit its slot?** A store batch is
~19 working days of pick makespan at 25 pickers (pilot measurement). Three levers:

- **Picker count** — pure staffing, but bounded below by the heaviest single aisle: one
  task per aisle is atomic (`Workload_Builder.py` Task.from_batch), so parallelism has a
  structural floor the calibration run must MEASURE before any count is chosen.
- **Cadence** (`releases_per_day`) — integer slots per day; one batch per day is a
  legitimate equilibrium if makespans fit.
- **Batch content** (`STORE_BATCH_MEAN` = 0.15 of the channel's SKU count per batch,
  `FF_BATCH_MEAN` = 0.20) — moving it rewrites the batch script (the fingerprint keys on
  it), which the calibrated era does anyway; the cost is honesty in the record, not
  comparability.

**(d) Name what the era makes inert and what it breaks**, so nothing is discovered later:
`recv_day_seconds` / `recv_day_origin` apply flag-off only (the dock inherits the site day
under drain_or_cap); archive row-comparability ends (a declared results era, like naming a
trailer type); and if (c) touches batch content, the script itself is new.

Facts with anchors: [assets/calibration-facts.md](../assets/calibration-facts.md),
sections *The working-day machinery* and *Batch content and arm-invariance*.

**Recommendation:** (a) yes — drain-or-cap paced days ARE the era; the close-out ledger
gives a per-day drained-vs-capped verdict for free. (b) S = 28,800 s, the site's existing
shift. (c) all three levers legal, moved in order: pickers first (the thing this map
exists to declare — and the seam must be built regardless), cadence second, batch content
last and only if the heaviest-aisle floor proves the first two cannot compose. (d) record
all three consequences in the resolution verbatim.

## Answer

Resolved 2026-09-05. The four questions are answered below, but the root moved during
resolution: the era is not a calibration SEARCH that finds staffing, it is a DERIVATION from
one declared input, with the day machinery as its verifier. The user reframed it that way to
make the whole map simpler, and the arithmetic agreed.

**(a) Mechanism — yes, as built, and it is the VERIFIER, not the calibrator.**
`SHIFT_DRAIN_OR_CAP=True`, `releases_per_day=1`, the cut forced on, and `roll_over_unpicked=True`
(a day that reads "drained" because cut demand was dropped is not equilibrium). The close-out
ledger's drained-vs-capped verdict, `released_late`, and the duty cycles are how the derived
staffing is checked. The soft alternative was rejected: nothing measures a day that has no
boundary.

**(b) S = 28,800 s, origin 0**, one boundary for every crew.

**(c) Levers — superseded by a derivation.** The ticket's three levers assumed calibration
moves them by search, and its recommended order (pickers first) died on arithmetic: the pilot's
store batch is 546,315 s of makespan against a 28,800 s day, a 19× gap, so pickers-first needs
~475 store pickers. Decision: **pickers are the ONE declared staffing input, per channel, and
everything else derives from them**, live at run setup, with a declared scalar at every step so a
staffing scenario is a flag change. Inputs and derived sizes both land in the run spec.

| Step | Formula | Scalar | Default |
|---|---|---|---|
| Pick capacity | K × S × ρ_pick | ρ_pick, picking utilization | 0.85 |
| Daily demand | capacity ÷ s_pick, summed across channels | s_pick, measured seconds per unit picked | reference run |
| Batch content | one batch = one day's demand | (derived, not searched) | — |
| Put load | demand × f_put | f_put, units put per unit picked | 1.0 |
| Put crew | ceil(put load × s_put ÷ (S × ρ_put)) | ρ_put; s_put measured | 0.85; reference run |
| Receive load | packs the script implies × f_recv | f_recv | 1.0 |
| Receiving crew | ceil(receive load × s_recv ÷ (S × ρ_recv)) | ρ_recv; s_recv exact from the script | 0.85 |
| Put intercept | picking's × put_intercept_scale | | 0.5 |
| Put per-item charge | picking's × put_item_ratio | | 0.2 |
| Receive intercept | put-away's × recv_intercept_scale | | 1.0 |

Cadence is pinned at one release per day. Crews are SITE totals (one put crew, one receiving
crew) summing both channels' derived demand; the pilot's 7.4× receiving-load spread across
leaves is a recorded consequence, not something this map resolves (per-stream sizing stays in
"staffing as a swept axis", out of scope). Seconds per unit are hybrid, as the charter's
knowability split says they must be: ONE reference run under `fifo` measures pick and put
seconds per unit (travel is arm-dependent); receiving is exact from the script because an unload
has no travel term. f_put and f_recv at 1.0 are steady state — what is picked is replenished;
any other value models a growing or shrinking warehouse, which is exactly why they are knobs.
Every default in the table is a declared ASSUMPTION, not a measurement, and the record must
say so.

**The cost model changes — HARD BREAK.** Picking gains a per-item charge:
`M(y) · (intercept + qty · per_item + qty · var)`, dataclass default 0.5 s per item, both
channels. It is the fixed labour of placing one item into the cart and labelling it — which
the original model was believed to carry in its intercept and never did: the intercept is
charged once per pick LINE (one bin visit for one SKU), not per item, and no per-item constant
exists anywhere in the handling model. The docstring gets corrected to say so. Put-away keeps
picking's shape with a declared intercept scale (default 0.5, "putting is less work") and a
per-item charge at a declared ratio of picking's (default 0.2, i.e. 0.1 s). Receiving keeps ONE
intercept per pack (scale default 1.0) plus ONE per-item charge per PACK, not per item: an order
split into five pallets and a bag of singletons is six charges. The 15% receiving cut floated
during grilling is withdrawn — the per-item cost it meant to remove was never in the model, and
receivers already carry no travel, height, or cart-swap term. The default is non-zero in the
dataclass by decision: old fixtures re-baseline, no archived result stays comparable, the
legitimacy of old models is deliberately not preserved (ADR-0001, `docs/adr/`). The charge
scales with units per line, so store's arms (pallet picks in quantity) move at least as much as
fulfillment's (singletons).

**(d) What the era makes inert and what it breaks — verbatim, as the ticket asked:**

1. `recv_day_seconds` / `recv_day_origin` apply flag-off only; under drain-or-cap the dock
   inherits the site day.
2. Archive row comparability ends TWICE: by the derived script (batch content changes, so the
   batch fingerprint changes) and by the cost model (every arm's labour moves).
3. The script itself is new — batch content is derived from picker capacity, not declared.
4. Lead time is denominated in batches (`LEAD_TIME_UNIT`), so wall-clock lead time SHRINKS in
   step with batch content. The conversion stays out of scope; the record must say the lead
   time moved.
5. The gain evaluator prices expected pick + put hours from the cost model, and the runner's
   demand-mass yardsticks (`optimal_sigma_fd`, the W* floor) do too. Both read `PickConfig`, so
   they inherit the charge if it lives inside `per_pick`/`_pick_time` — a test must PROVE they
   did, or the evaluator prices a fiction under the arm's name (faithful-to-arm, inbound 10).
6. `thr_batch` divides by makespan, not by the day; under pacing with slack it reports the rate
   the crew worked AT, not what the day delivered. `throughput_elapsed` is the day's number
   (memory `working-day-clock-plan-corrections`).

**Glossary:** `CONTEXT.md` gained **Era** (Day-over-day) and **Per-item charge** (2026-09-05).

**Map consequences, applied this session:**
[Choose the calibration procedure](02-choose-the-calibration-procedure.md) becomes the
reference run and its measured constants;
[Design the staffing record](03-design-the-staffing-record.md) gains the scalars and derived
outputs; [Declare the equilibrium bands](04-declare-the-equilibrium-bands.md) becomes the
utilization targets and the verification read;
[Sequence the inbound funnel](05-sequence-the-inbound-funnel.md) is strengthened toward
"hold". Two builds graduated from fog:
[Add the per-item charge and break the cost model](06-add-the-per-item-charge.md) (unblocked)
and [Build the picker staffing seam](07-build-the-picker-staffing-seam.md) (blocked by 03).
