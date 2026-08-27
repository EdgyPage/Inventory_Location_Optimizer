# Choose the lead-time denomination

Type: grilling
Status: resolved
Blocked by: 01

## Question

Trailer transit time is lead time, and lead time is denominated in BATCHES
(`Inventory_Manager.LEAD_TIME_UNIT`); converting to seconds moves every restock result. The
memories (`putaway-seams-for-inbound`, `one-clock-one-speed-one-config`) explicitly defer this:
"the trailer feature should choose." Choose: batches, seconds, or a per-run flag — and how the
choice interacts with batch-quantized arrivals, working-day step 6b (inbound hours, the one open
step in `docs/design/WORKING_DAY_CLOCK.md`), and comparability with the v2-sampler archive.

## Context update (post "Name the inbound pipeline")

Decided since charting: the lead queue IS the transit leg — trailers ride it (a trailer models
what the floating quantity abstracted), and site state persists day over day. The denomination
choice now reads as "what unit does trailer transit tick in."

## Context update (post "Draw the Inbound package boundary")

Transit is Inbound-owned behind the manager's order port (injected object; the manager keeps only
the scalar deferred ledger). The denomination decision now shapes that transit object's tick, not
the manager's `_advance_lead_queue` internals. Facts: `../assets/boundary-facts.md`.

## Answer

Resolved over two grilling rounds. The ticket's original either/or (batches vs seconds) dissolved
into a sharper model:

1. **A trailer's lead is an optional per-trailer delay on the absolute clock** — authored in
   MINUTES at the settings surface, stored in SECONDS internally (`TIME_UNIT` stays `'seconds'`;
   internal minutes would be a third unit era for zero modeling gain). **Default zero: trailers
   arrive in the parking lot instantly.** The delay merchandise experiences becomes EMERGENT —
   parking lot → finite dock doors → crew hours — instead of a configured transit tick.
2. **The ledger stays batch-review and untouched.** Order-up-to math already handles lead-0
   generically (`inventory_reorder.py:407`), lead-0 orders release the same batch, and the
   deferred→queued credit survives because the dock intercept sits after it. Flag-off keeps
   today's per-SKU batch-denominated lead queue byte-identical for the archive; flag-on replaces
   its transit role with trailers carrying optional time-leads. Era discipline confirmed: the
   knob defaults OFF (`SAMPLER` precedent).
3. **Queueing turns spatial**: *parking lot* (arrived trailers awaiting a door, unbounded) →
   *dock doors* (finitely many staging slots) → unload. Which parked trailer stages next, and
   which staged trailer unloads, are the global-priority seam's decisions (yet to be developed
   — ticket 06); the door count is a knob (ticket 07).
4. **Clock semantics confirmed**: the absolute clock is the recorded truth; "every day begins at
   0" is a day-local VIEW (minutes since day start) for reports and authoring — never what is
   written to `work_events` (the frozen-clock bug, 6504 s vs 2262 s, is the receipt).
5. **The drain-or-cap rule ends the SHIFT, not the day** (user's correction): work stops when no
   work remains anywhere or at a global cap (e.g. 11 h). This is a new scheduler concept that
   COLLIDES with `SHIFT_SECONDS` (today a reporting frame that dispatches nothing) — flagged to
   the convention pass, and the rule itself graduates to
   [Set the shift-end rule](10-set-the-shift-end-rule.md).
6. `test_lead_time_unit`'s note gets rewritten at implementation to record the pick the trailer
   feature made: legacy batch lead flag-off, trailer time-leads flag-on — per the note's own
   instructions.
