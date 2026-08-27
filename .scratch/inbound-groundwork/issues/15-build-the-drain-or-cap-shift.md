# Build the drain-or-cap shift

Type: task
Status: open

## Question

Execute "Set the shift-end rule": one new mode flag (default off = byte-identical) making the
site-wide shift end at drain-or-cap. Standing work = released-unpicked demand + stock/put
queues + `_held` + dock floor (+ parking lot when trailers land), NEVER the lead queue; drain
also requires no releases remaining in the day; the cap reuses the day length and implies
cut/carry semantics; days stay origin-aligned on the absolute clock (early drain skips the
dead evening for labor, never for the calendar). One site-wide boundary when on; per-crew
days remain the flag-off configuration. Traps on record: `thr_batch` is not per-day
(`thr_elapsed` is the day-rate); the empty-batch clock stall is a contract; batch-granular
resume already refuses with carry on. Minutes authoring arrives with the convention pass.

Done when: gates green; flag-off digests byte-identical; a drain-early case and a capped case
each pinned by test; nothing committed without the user's go-ahead.

## Answer

EXECUTED 2026-08-26, commit `900baa7` — the map's final ticket.

1. **One mode flag** (`SHIFT_DRAIN_OR_CAP`, default off = byte-identical), riding the
   working-day record (`work_day_spec`) so the mode and the day it caps against cannot
   disagree in a worker — the one-accessor-one-payload-key discipline that record exists for.
2. **The cap implies the cut**: mode-on forces `_cut_at_day_end` rather than trusting two
   flags to agree (a cap without carry loses demand). `roll_over_unpicked` stays independent
   — two of its three causes have no day boundary in sight.
3. **One site-wide boundary**: the receiving crew shares the cap when the mode is on; its
   own day knobs are the flag-off configuration, per the decision.
4. **The end instant** is pure kernel arithmetic — `timeline.shift_end(cap_end, last_finish,
   drained)`: a drain before the cap ends the shift when the crews finish ("off the clock");
   standing work, or START-gate overtime finishing past the whistle, ends it at the cap —
   whichever came FIRST, exactly. Days stay origin-aligned: an early drain stops the labour,
   never the calendar.
5. **The judgment lives in the runner's per-day ledger**, closed at the first batch of the
   next day: drained = nothing cut all day (pick carry AND `recv_cut`) and no standing work
   (put queues + held + the dock floor + carried demand — never the lead queue: transit is
   calendar, not labour; releases are exhausted by construction at a day boundary). Reported
   as a `[shift]` log line per day — a REPORT, never a scheduler, so no schema change.
6. **Pinned as the ticket demanded**: the drain-early case, the capped case, the
   overtime-past-the-whistle case and the exact boundary in `Tests/unit/test_shift_end.py`,
   plus source-pins on the two one-line implications (forced cut, shared receiving day)
   that a refactor could silently drop. One placement lesson recorded: the ledger reads
   this batch's `put_clock`, which is computed late in the loop iteration — the block lives
   at the loop TAIL, where every clock and column for the batch is final.

Verification: 1,758 unit+integration + 6 receiving-e2e untouched flag-off; all gates green.
