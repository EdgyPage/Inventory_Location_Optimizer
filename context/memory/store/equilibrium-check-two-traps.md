---
name: equilibrium-check-two-traps
description: "The era's equilibrium check as decided would have raised on a healthy run (lag belongs to the capped day before) and its receiving self-check as an average was 7x off; both fixed 2026-09-06, and both are easy to re-introduce"
metadata: 
  node_type: memory
  type: project
  originSessionId: f95ccfc4-4fad-4ddc-a370-b4b45fb0e63f
  modified: 2026-09-06T16:22:54.644Z
---

Two things the first calibrated-era run (2026-09-06, ticket 10 of the department-calibration
map) taught about reading the drain-or-cap ledger, each of which the written decision got
subtly wrong and which a future reader of `batch_stats.released_late` or `s_recv` will get
wrong again the same way.

**1. Lag lands on the day AFTER the one that caused it.** `released_late` is stamped on the
batch released INTO day d, but the crew that was still busy belongs to day d-1. A capped day 1
left 69.9 s of lag on day 2's release, and day 2 then drained. "released_late = 0 on every
drained day" therefore raises on a legitimate run. The honest self-consistency assertion is:
lag in day d exists only behind a CAPPED day d-1; lag behind a drained day, or on day 0,
is the instrument contradicting itself (`equilibrium.InstrumentError`).

**2. The receiving self-check must be exact per pack, never an average.** The derivation's
`s_recv` is a site average over the reorders the SCRIPT implies; a window's measured average
is over the packs the run actually received. On the smoke run those differed 7x (51.9 vs 7.4
s/pack) because heavy store packs dominate the script average while only the fulfillment leaf
received anything — and even within a channel the lot mix moves a per-pack average by a few
percent. The discipline is to re-price every receive row from its SKU and quantity with the
channel's own `UnloadCost` and agree to the second at a float tolerance — never to compare a
charge against a mean over a different mix.

**The check that did this is DELETED (verified 2026-09-11, site-dock 07).** It was
`reference.recv_exact_check`; `Optimization/simconfig/` has no `reference.py`, `unload_price_for`
returns zero hits repo-wide, and `Optimization/simconfig/equilibrium.py` records the retirement of
the driver that used it as a precondition. Its loader survives ORPHANED with zero callers —
`load_receive_events` and the `receive_event_frame` query in `Optimization/persistence/
Picking_Data.py`, whose comment still says the rows exist for this check. `staffing.py`'s
`derived.receiving.s_recv` is reported and read by nothing, and already carries a comment
correcting an EARLIER stale claim that the throughput audit performs this check — so "it lives in
the audit" has been believed once before and was not true either. Whether it returns at site scope
is site-dock 15.

**Why:** both are consequences of the same fact — the ledger and `batch_stats` describe
different instants (close-out fires at the NEXT day's first batch) — and the decisions were
written before any era run existed.

**How to apply:** attribute a per-batch lag to `work_day - 1` before judging it; compare a
labour charge against a re-pricing of the same rows, not against a mean over a different mix.
Also from the same run: a 5-day toy catalogue places NO store reorders (coverage 10 batches),
so put/receiving utilization reads 0 there — a 40-day window is not optional.

Related: [[a-count-is-not-a-claim]], [[cut-is-a-level-not-a-flow]],
[[empty-batch-clock-stall-is-a-contract]], [[working-day-clock-plan-corrections]].
