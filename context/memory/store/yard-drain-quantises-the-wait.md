---
name: yard-drain-quantises-the-wait
description: "The once-a-day yard drain, not the dock, made most of every trailer's yard wait (6.7 h at 40k k10); --inbound-door-fill asap (df814fc0) plugs doors at arrival and cut it to 0.7 h"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-24T16:01:46.525Z
---

Measured 2026-09-23 on the 40k `_churn_probe` k10 point (drain root `comparison_whatif_20260923_104100`,
asap root `comparison_whatif_20260923_215459`, same argv plus `--inbound-door-fill asap`).

- Under `drain` (the default) `YardTransit` admits arrivals only at the daily drain, so a trailer
  arriving after it stands beside an idle door until tomorrow. Its yard wait was a constant
  ~3.4-3.8 h "to the next drain" at every volume, plus a queue after the drain that grows with volume.
  At k10, staged - arrived: mean 6.7 h, p95 10-14 h, 0% plugged at arrival; dwell 8.4 h.
- `asap` (split crews + standing yard only): arrivals are events, every door plug re-ranks over the
  trailers standing THEN (the drain's frozen ctx stays the ranking input), idle receivers wait for
  the next plug, nobody starts before the trailer reached its door. At k10: wait mean 0.7 h, median
  0, ~50% plugged at arrival; dwell 2.2 h; in-transit stock halved; unloaded units +2%.
- Pick s/item: flat. The store +0.75 to +1.3% on seed 1337 reversed sign on seed 2024 (S18): noise.
- 400k (S18): asap takes 4.5-6 h off the fifo-cell wait at every k (k2.2 13.3 -> 8.0 h, k2.6
  27.3 -> 22.8 h). Past the gate the queue dominates.
- The gate does NOT move usefully: asap raises door occupancy 1-2.6% (the quirk below was door
  capacity), which moves k_gate 2.31 -> ~2.26. At k2.2, 1 of 6 independent arms turned
  significant (-0.22%). k2.6: 5 of 8, all lifo-cheaper, as under drain.
- A drain yard never admits its final day's arrivals: compare end-of-window counts on days 20-38.
- Drain-mode quirk kept for byte identity: a teammate idle on an emptying trailer starts the NEXT
  staged trailer at their own clock, before its door frees. `asap` holds them to the door-free instant.

**Why:** any yard-latency or unloading-order result under `drain` carries the drain quantum; the
Allen-Cunneen `yard_wait` in `models/dock.py` does not model it. **How to apply:** for dock/yard
questions pass `--inbound-door-fill asap`; a `drain` vs `asap` comparison is not byte-comparable.
Related: [[aisle-ceiling-and-dock-gate]], [[inbound-yard-is-a-stable-queue-under-the-era]],
[[site-dock-is-shared-across-channels]].
