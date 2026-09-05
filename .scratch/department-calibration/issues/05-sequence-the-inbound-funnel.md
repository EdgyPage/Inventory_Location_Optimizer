# Sequence the inbound funnel

Type: grilling
Status: resolved

## Question

Does the inbound funnel's PHASE 1 also wait for the calibrated era, or run now under the
historical continuous regime? (Phase 2 is already held — settled at the pilot resolution.)

The original recommendation ("run phase 1 now — calibration cannot invalidate its
ranking") was WRONG, and the correction is on record: the calibrated era turns on
`cut_at_day_end`, which "changes WHICH units get picked in WHICH batch"
(`strategy_runner.py:549`), and if the era's calibration touches batch sizing the script
itself changes. A phase-1 ranking taken now would feed a phase 2 running in a different
era — and the `inb_off` anchor cell would then compare ACROSS eras, breaking the very
transfer check it exists to perform (funnel design, inbound map ticket 08).

The trade: phase 1 is ~3 h of simulation and unblocks the inbound map's one remaining
ticket ([Extend the gain bundles](../../inbound-optimization/issues/20-extend-the-gain-bundles.md));
holding it costs that wait, running it now risks running it twice and produces a ranking
whose regime nobody will ever run again.

On resolution, edit the inbound map's campaign entry (Not-yet-specified, "The funnel
campaign") to record the decision — that map's execution order is where the answer lives;
this ticket is where it gets decided.

**Recommendation:** hold phase 1. Cheap to defer, expensive to duplicate, and a number
with no consumer is not worth 3 hours. The funnel restarts from phase 1 under the
calibrated era once this map lands.

## Comments

2026-09-05, from resolving [Define the calibrated era](01-define-the-calibrated-era.md): the
era now also changes the COST MODEL (per-item charge, hard break — ticket 06, ADR-0001), so a
phase-1 ranking taken today would be taken under a labour model no later run uses. This
strengthens "hold".

## Answer

Resolved 2026-09-05. **Hold phase 1**, as recommended. Eight decisions were put to the user over two
rounds; every recommendation was accepted.

1. **Hold, not run, not shake down.** The ranking has no consumer, the cost model it would be taken
   under hard-breaks at [Add the per-item charge](06-add-the-per-item-charge.md), and the `inb_off`
   anchor's transfer check is the funnel's only validity proof. A reduced-depth shakedown was
   declined too: the selector was proven runnable at inbound ticket 18, and a `restock_selection.json`
   under `COMPARISON_OUTPUT_DIR` is exactly the gate inbound ticket 20 checks for — a shakedown
   artifact would trip it falsely.
2. **What lifts the hold is a named ticket: [Take the reference run](09-take-the-reference-run.md).**
   Phase 1 needs the era's mechanics AND its measured constants; under an analytic seed its staffing
   would be `seed`-stamped and phase 2 would run against a different derivation. The
   interaction-effects read is layered on top, not a precondition. The whole map closing is not
   the gate.
3. **The pilot gate is no longer a SEARCH; it is a VERIFICATION.** The pilot's committed regime
   (`--recv-crew-size 4 --recv-day-seconds 43200`, the `inbound_pilot` spec, `PHASE2_RECV_CREW_SIZE`
   / `PHASE2_RECV_DAY_SECONDS`) is an ERROR under the era (03, decision 4): the crew derives from
   pickers via ρ and f and receives the SITE's 28,800 s day. So phase 2's "whole experimental
   condition" ceases to exist as a knob. When the funnel restarts it re-runs the pilot as ONE
   inbound-on cell (`fifo` + `tmin`, no crew flags) whose two acceptance criteria are read through
   04's equilibrium REPORT. Skipping it was declined — the derivation has never met a trailer; the
   reference run cannot stand in — it has no inbound.
4. **The funnel inherits the reference run's window**: 40 site days, days 20–39 measured. Under
   one-release-per-day pacing depth IS a day count. A campaign window that differed from the
   calibration's would report utilization against a warm-up the constants never saw. Phase 2's
   sizing (480 units, ~13 h, ~1.1 TB at the old depth) must be re-estimated against it.
5. **Retiring the dead regime is split.** The era-wiring build (08) flips the DOCSTRINGS — the
   `phase2_inbound_axis` block literally teaches "size it against the BATCH, never against a shift",
   the inverted form of this map's denomination invariant. The constants and flags stay
   flag-off-live (byte-identical discipline). The spec's new shape is the inbound map's work.
6. **Two new tickets on the inbound map, not fog**, both sharp-but-blocked on 09 here:
   [Verify the derived receiving crew under arrivals](../../inbound-optimization/issues/23-verify-the-derived-receiving-crew.md)
   and [Re-size the funnel in site days](../../inbound-optimization/issues/24-resize-the-funnel-in-site-days.md).
   Cross-map blocking has no tracker convention, so each carries a `Blocked by` line naming 09 by
   path; inbound ticket 20 carries a comment pointing here.
7. **Pin the calibration record across phases.** `restock_selection.json` stamps the staffing
   record's identity, and `inbound_policies` refuses to start under a different one — the era's
   twin of the existing `PHASE2_ARMS` refusal. Otherwise a `calibration_stale` re-derivation between
   phases makes the `inb_off` anchor compare across derivations silently. Scoped on 24.
8. **Glossary**: `CONTEXT.md` gained **Pilot gate** with the verification sense; the search sense is
   recorded as retired.

**Campaign order after this resolution** (recorded on the inbound map's campaign entry):
reference run (this map, 09) → pilot verification (inbound 23) → phase 1 → selection → phase 2 →
publish, with 24's re-sizing done before phase 1 launches.

**Consequence for this map:** 08 gains the docstring flip as scope (comment appended). Nothing
graduated from fog here; the two graduations landed on the inbound map.
