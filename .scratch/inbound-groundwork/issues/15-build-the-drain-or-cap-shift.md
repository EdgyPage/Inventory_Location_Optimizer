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
