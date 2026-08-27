---
name: receiving-is-its-own-crew
description: "inbound got a fourth crew 2026-08-25 — the dock intercepts inside _admit (which is why the reorder ledger needed zero edits), packing stays at arrival, and arrivals are still batch-quantized so crew SIZING is not answerable"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-27T03:01:26.766Z
---

The user's decision, 2026-08-25: **inbound is its own crew.** Merchandise whose lead time has
elapsed lands on a `Dock` and a receiving crew with its own hours works through it; what they
do not reach stays for tomorrow. Nine commits, `33911b3..4b8770c`.

**The one design fact everything else follows from: the dock intercepts inside `_admit`,**
after the arrival stamp and before the put queue. `_release_to_stock` credits `_queued_qty`
AFTER its admit loop, so the credit survives the divert and
`position = on_hand + queued + deferred` is unchanged — **the reorder ledger needed zero
edits at either of its two sites.** Intercepting one level up (the obvious place, and what
two of three design proposals chose) would drop merchandise out of `_deferred_qty` without
adding it to `_queued_qty`, and the SKU would re-order every batch for as long as the dock
was backed up, silently. It also inherits the age stamp for free.

**Three things that will produce plausible wrong numbers if forgotten:**

- **Arrivals are still quantized to BATCHES** (`LEAD_TIME_UNIT`), so every trailer in a batch
  lands at the batch epoch and the crew faces its whole day at once. Direction is knowable:
  the makespan reads LONG, so **sizing a receiving crew off this model over-staffs it.** Not
  fixable without converting lead time to seconds, which moves every restock result.
- **A put row can carry a smaller `t_abs` than the receive row of the same unit.** Both
  clocks restart at the batch epoch and the streams are merged, not sequenced. Bounded by one
  batch's receive makespan. Widening `_put_base` over-corrects — it would make the first
  pallet off the truck wait for the last.
- **The dock has no floor limit and exerts backpressure on nothing.** A refusal would need
  somewhere to put the merchandise, and the only candidate (`_held`) is retried inside
  `_stock`, which would put trailer goods away for free, bypassing the crew.

**Where to look.** `Inbound/dock.py`'s docstring (moved from the old path
Warehouse/inventory/dock.py by the 2026-08-26 skeleton landing, see
[[inbound-pipeline-wayfinder-decisions]]) carries all of the above plus the three verified
reasons a dock cannot be a `PutQueueSet` member. `Inbound/unload.py` (moved from the old path
Warehouse/operations/unload.py, same landing) carries why
there is no travel term, no speed and no mode (and therefore why crew SIZE is the only
lever) — it also now owns `unload_seconds`, the dock's own price list.
`Diagnostics/receiving_report.py` is the only reader of `work_events` in the repo — five
sabotage-verified cross-checks; run it after any change to either stream.

**Planned overturn (2026-08-26, decision-only at the wayfinder session; the `Inbound/`
skeleton itself landed later the same day):** "packing stays at arrival" is scheduled to be
OVERTURNED — a wayfinder session decided packing moves to unload time (dock-side
receiving-crew work), with the pack plan held fixed per trailer's full per-SKU contents so
tier mix stays independent of crew size. See [[inbound-pipeline-wayfinder-decisions]] for the
full record and for what the skeleton pass actually shipped (package move + injection seam
only — pack-at-arrival itself is not overturned yet); everything else in this memory (the
`_admit` intercept, the three quantization traps) still describes the current code.

Related: [[working-day-clock-plan-corrections]] (the day this rides on),
[[putaway-seams-for-inbound]], [[config-knob-has-five-seams]],
[[empty-bin-preference-is-structural]], [[inbound-pipeline-wayfinder-decisions]].
