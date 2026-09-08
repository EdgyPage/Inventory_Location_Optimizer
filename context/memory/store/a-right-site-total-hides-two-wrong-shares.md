---
name: a-right-site-total-hides-two-wrong-shares
description: "A site-level department total can be exact while both per-channel shares are badly wrong, because the errors cancel through the unit mix; check the split separately"
metadata: 
  node_type: memory
  type: project
  originSessionId: 6ff537d5-aa9a-4dea-8b0f-d07d77514194
  modified: 2026-09-08T13:22:47.587Z
---

A department whose crew is a SITE total gets two readings: the site load and the per-leaf
apportionment. **They fail independently, and a correct site total is not evidence for the split.**

Measured 2026-09-08 on the era re-read (`comparison_20260908_075736`, department-calibration 25):
put-away's site load was derived at 1,437,958 s/day against a realized 1,425,557 -- **0.9% off, so
the crew of 59 was correctly sized** -- while the per-channel expected utilization was out of band
on BOTH leaves in opposite directions (fulfillment 0.477 realized against 0.691 expected, store
0.362 against 0.155). Cause: realized put-away costs **98.50 s/unit on the store and 29.15 on
fulfillment**, and the derivation priced both at ONE site-wide `s_put`. The errors cancelled because
the unit mix is ~28k fulfillment to ~6k store, dragging the weighted mean onto the truth.

**Why:** in `simconfig/staffing.py` `derive`, put-away is the only one of the three departments that
crosses the section boundary with a single price -- `ch_put_s = per_day(put_units) * s_put`. Picking
derives `s_pick` per channel (108.4 store / 17.4 ff on the reference pair, a 6.2x spread), and
receiving takes per-channel seconds straight from the script because an unload has no travel term.
Both of those read in band on the same run.

**How to apply:** when a department reads out of band, compute the site total FIRST. If it is exact,
the crew size is not the defect and re-sizing it will break something that works -- the defect is in
how the load is apportioned, which usually means a constant is being applied across two geometries
that do not share it. The same shape can recur for any future site-level crew. Note also that this
signature was already present in the line-floor check (2026-09-06, ff 0.507/0.713 and store
0.291/0.134) and was read as a consequence of the fulfillment treadmill rather than as its own
defect; it survived every change since. Related: [[equilibrium-check-two-traps]],
[[a-count-is-not-a-claim]], [[receiving-is-its-own-crew]], [[config-knob-has-five-seams]].
