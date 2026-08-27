# Set the shift-end rule

Type: grilling
Status: resolved

## Question

Define the shift: crews (pick, put, receiving) work one absolute clock, every shift begins with
standing work everywhere (yesterday's carryover, arrived trailers, the day's demand), and — the
user's stated intent, verbatim from "Choose the lead-time denomination" — "the day [corrected:
shift] ends when no work is left anywhere or with a global cap ie an 11 hour shift" (drain-or-cap).
Decide: how the shift boundary relates to batches (is a shift one demand batch, or several batch
slots as `docs/design/WORKING_DAY_CLOCK.md` currently models?); how drain-or-cap composes with
the existing per-crew hours, the put cut (a START gate), and the empty-batch clock-stall contract
(`empty-batch-clock-stall-is-a-contract`); what "no work left anywhere" means precisely (which
queues count — picks, puts, dock, parking lot?); and the fate of the `SHIFT_SECONDS` reporting
frame, whose name now collides with a shift that actually dispatches (rename rides the
convention pass, ticket 03). Traps on record: `thr_batch` is not per-day; batch resume drops the
carry; cap authored in minutes per the minutes-at-the-surface decision.

## Context update (post "Set the column-semantics conventions")

The SHIFT_SECONDS fate is decided: the reporting frame renames at the logical layer
(`REPORTING_FRAME_SECONDS` / logical `frame_index`), so *shift* is free for the drain-or-cap
dispatcher this ticket defines. The cap knob is authored in minutes (`*_MINUTES` in settings.py)
per the forward naming rules.

## Answer

Resolved in one round — the drain-or-cap shift is a new DAY-END MODE over the existing
working-day machinery (`WORK_DAY_SECONDS` / `RELEASES_PER_DAY` / `CUT_AT_DAY_END`), not a
rebuild:

1. **The shift is the day's labor window; batches stay demand waves within it** — multiple
   batches release inside one shift by the schedule. Flag-off (continuous, no cap) is today's
   behavior exactly.
2. **"No work left anywhere" counts STANDING WORK**: released-but-unpicked demand (incl.
   carry), stock queue + put queues + `_held`, the dock floor — and the parking lot once
   trailers land. The lead queue is EXCLUDED (calendar, not labor). Drain additionally
   requires no releases remaining in the day: scheduled afternoon work keeps the shift open.
3. **One site-wide shift when the mode is on** — all crews share the drain-or-cap boundary;
   per-crew day lengths remain the flag-off configuration. Per-crew offsets are a later
   policy.
4. **Origin-aligned days**: after an early drain, labor stops accruing at the drain instant
   and the next day begins at the next day ORIGIN on the absolute clock — "the shift timers
   happen at the same time every day" (user, verbatim). The dead evening is skipped for
   labor, never for the calendar, so release schedules and day labels stay aligned and
   day-over-day state carries as decided.
5. **One new mode flag, default off = byte-identical.** The cap reuses the day length (no
   second duration to reconcile) and capping IMPLIES the cut/carry semantics — work standing
   at the cap rolls via the existing carryover machinery. Minutes authoring rides the
   convention pass's `*_MINUTES` surface.

This was the map's LAST open decision. Graduated with it: the three remaining implementation
waves — [Move transit behind the order port](13-move-transit-behind-the-order-port.md),
[Build the trailer pipeline v1](14-build-the-trailer-pipeline-v1.md), and
[Build the drain-or-cap shift](15-build-the-drain-or-cap-shift.md).
