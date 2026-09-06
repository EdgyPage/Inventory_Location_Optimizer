# Choose the calibration procedure

Type: grilling
Status: resolved
Blocked by: 01

## Question

Analytic, empirical, or hybrid — and which arm prices the empirical half?

The knowability split is exact (assets survey, `arm_invariance`): the batch script is a
pure function of (inventory, affinity, config, seed) shared identically across arms, so
per batch the SKU set, demanded units, and handling mass `sum(qty · handle_var(w,v))` are
computable with NO run. Receiving goes further: an unload has no travel term
(`Inbound/unload.py`), so the dock's whole demand is analytic. But WHICH bins serve the
demand — and therefore all pick and put-away travel, intercept counts, height multipliers,
cart swaps, and shortfall — is arm-dependent placement state: it needs a measured run.

So the choice is really about the empirical half:

- Which ARM prices it? (Travel differs per arm by design — that variance is the whole
  experiment.)
- What does the calibration run look like — depth, leaves, what it measures (the
  heaviest-aisle floor from 01, realized makespans per department, duty cycles)?
- What does the procedure RECORD? A calibration that keeps only its output counts cannot
  be audited or re-run when the catalogue changes.

**Recommendation:** hybrid, priced under `fifo` — the campaign's own reference and
negative control, and the arm the pilot already measured. Analytic first-cut from the
script (dock exact; pick/put handling mass exact, travel estimated), then ONE calibration
run under `fifo` in the 01 era to measure the travel-bearing makespans and set final
counts. Record the analytic prediction AND the measured value per department — their
ratio IS the travel share, and it is the number that lets the next catalogue re-calibrate
cheaply.

## Comments

2026-09-05, from resolving [Define the calibrated era](01-define-the-calibrated-era.md): the
question is RESHAPED. Calibration is now a DERIVATION from declared pickers (the chain is in
01's answer), not a search — so this ticket no longer chooses counts. It decides the REFERENCE
RUN that supplies the two measured constants, `s_pick` and `s_put` (seconds per unit picked /
put): which arm (the `fifo` recommendation stands), depth, leaves, and what it records — the
analytic prediction beside the measured value, their ratio being the travel share, as already
recommended — plus how the constants are declared with provenance so the next catalogue can
re-calibrate. Receiving needs no measurement (exact from the script). The heaviest-aisle floor
is still measured here, but it now BOUNDS the picker count a scenario may declare rather than
setting one. The run must be taken under the new cost model
([ticket 06](06-add-the-per-item-charge.md)) or it measures labour no later run uses.

## Answer

Resolved 2026-09-05. Hybrid, priced under `fifo`, as recommended. Seven decisions were put to the
user in one round and every recommendation was confirmed; the three details below marked
*assumed* were settled by the session rather than grilled, because each has one sensible value.

1. **Bootstrap — a fixed point.** The era's batch content derives from `s_pick`, and `s_pick` is
   measured under the era, so the procedure iterates: pass 0 seeds `s_pick` / `s_put` from the
   analytic prediction scaled by the pilot's measured picking/traveling split (*assumed*), the
   era runs, the constants are measured, daily demand is re-derived, and a second pass runs.
   Stop when derived daily demand moves less than **5%**; **at most two passes**. The record
   carries the pass count and every pass's values, so a reader can tell converged from cut off.
   Measuring under today's 0.15-fraction script was rejected: pick density per aisle visit
   changes with batch size, so its travel per unit is not the era's.
2. **The constants.** `s_pick` is **per channel** (machine pickers and walkers differ by
   construction): Σ `task_makespan` ÷ Σ `total_items` over the window — a ratio of sums, never a
   mean of per-batch ratios — both columns already in `batch_stats`. `s_put` is **one site value**
   consumed (01's one put crew), with per-stream cart / pallet / ff values recorded as
   diagnostics only, so the split family can be sized later without a new run; the put-side
   seconds must be derived from `work_events` spans (no duration column exists) and the run
   ticket confirms that instrument. `s_recv` is **exact from the script**; the reference run
   records its measured receiving labour as a self-check, and a mismatch beyond a float
   tolerance (*assumed*) **fails the run**, because it means the per-pack charge that ran is not
   the one 06 built.
3. **Depth and window.** **40 days** (one batch per day under the era), **days 20–39 measured**.
   Precondition on the window: 04's equilibrium check (`released_late` at zero, most days ending
   drained). A window that fails it is **discarded and the next pass runs**; it is never
   averaged in.
4. **Leaves and mechanics.** One cell, `fifo` only, scheduler pinned to `lpt` as the pilot did,
   one inventory pair, **both channels** (crews are site totals). **Minimal mechanics**: the
   legacy batch-denominated restock path, receiving crew on at its derived size, no trailers and
   no standing yard. Labour per unit picked or put does not depend on yard policy, and receiving
   is exact regardless; the yard moves WHEN work arrives, not how long a unit takes.
5. **The calibration record.** A committed artifact under `Optimization/simconfig/` (JSON or a
   small module) holding the constants **and their provenance**: run root name, commit,
   warehouse fingerprint, batch fingerprint, cost-model parameters, era flags, pass count and
   per-pass values, date. `settings.py` loads it as the default; a CLI flag overrides each
   constant. **Staleness:** run setup compares the loaded catalogue's warehouse fingerprint with
   the record's; a mismatch **warns and stamps `calibration_stale` into the run spec**. Failing
   would block every new catalogue; a bare warning gets lost; the stamp is readable from the
   results forever.
6. **The heaviest-aisle floor bounds, it does not set.** Per channel per day,
   `K_max = floor(total task seconds ÷ heaviest task seconds)`; the window minimum is recorded
   in the calibration record. A scenario declaring more pickers than `K_max` **warns and
   stamps** — the era verifier shows the idle capacity anyway, and a scenario may want
   saturation on purpose.
7. **Analytic beside measured.** For each channel and department the record holds the
   script-only prediction (per-item charge, `qty × handle_var` at ground height, one intercept
   per SKU per batch as the line-count floor) next to the measured value; their ratio is the
   **travel share**. It **may** price a new catalogue provisionally
   (`s_new = analytic_new × travel_share_old`, stamped `provenance: derived`) until a reference
   run replaces it — the cheap path this ticket's recommendation asked for, with the stamp
   keeping a derived constant from passing as a measured one.

**Facts found while resolving.** `SHIFT_DRAIN_OR_CAP` has **no CLI flag** — only
`--releases-per-day`, `--cut-at-day-end`, `--roll-over-unpicked` and `--work-day-seconds` exist —
so the era cannot be launched from the command line until the era wiring lands; that is now part
of ticket 08. `batch_stats` already carries `task_makespan` and `total_items`, so `s_pick` needs
no new instrument.

**Glossary:** `CONTEXT.md` gained **Reference run**, **Calibration record** (Day-over-day) and
**Travel share** (Measurement), 2026-09-05.

**Map consequences, applied this session:** two builds graduated from fog —
[Build the derivation, the calibration record, and the era wiring](08-build-the-derivation-and-era-wiring.md)
(blocked by 03, 06, 07) and [Take the reference run](09-take-the-reference-run.md) (blocked by
08). [Design the staffing record](03-design-the-staffing-record.md) gains the calibration
record's constants with provenance plus the two stamps; [Declare the equilibrium bands](04-declare-the-equilibrium-bands.md)
learns its check doubles as the reference run's window precondition.

## Comments

2026-09-06: **decisions 1-3 (hybrid under `fifo`, the fixed point, the 40-day window) are
SUPERSEDED.** [Take the reference run](09-take-the-reference-run.md) executed them and found
the procedure cannot close on the production catalogue (six passes to converge picking; the
replenishment cycle longer than the window; stockouts defeating the drained clause). The user
then ruled out calibration simulations altogether: every constant is a closed-form expectation
over the known inventory distribution and the warehouse geometry built at runtime --
[Derive the expected-travel closed form](13-derive-the-expected-travel-closed-form.md). What
stands from this ticket: the constants live in a committed record with provenance (now
`derived`, stamped with geometry and placement fingerprints), `K_max` bounds pickers, and the
receiving constant is exact.
