---
name: phases-one-and-two-are-sequencing-independent
description: "Decided/measured 2026-09-19: phase-2 geometry is shared with phase 1 by construction (one freeze per pair before any cell scope opens), and the only machine-enforced cross-phase dependency is PHASE2_STAFFING_PIN, a digest keyed by inventory-pair label that does not require phase 1 to have run; the new inbound_unload_rider spec drops the last hand-copied constant that forced sequence"
metadata: 
  node_type: memory
  type: project
  originSessionId: 54a9e9be-5880-4d00-9c09-172dafbe4eb1
  modified: 2026-09-20T04:25:02.068Z
---

Geometry is shared between the inbound campaign's phases BY CONSTRUCTION, not by running order:
a multi-cell run freezes inventory once per pair at run level before any cell scope opens, so
all ten phase-2 cells (including `inb_off`) plan the identical warehouse, and phase 1 was moved
onto the same arrival regime as phase 2. See [[inbound-campaign-is-a-three-phase-funnel]] for
the funnel shape this sits inside.

**The only machine-enforced cross-phase dependency is `PHASE2_STAFFING_PIN`**
(`Optimization/config/whatif_config.py`, checked by `_check_campaign_pin` in
`Optimization/simdriver/workunits.py`) — a DIGEST of the staffing derivation keyed by
inventory-pair label. It does **not** require phase 1 to have run first; it only requires that
whichever staffing derivation ran for a given pair label matches the pin. The chosen rule pairs
themselves are **not** checked against phase 1's output at all — nothing stops phase 2 launching
with a pair phase 1 never ranked.

**The only thing that DID force sequence was a hand-copied constant** (the phase-1 winning pair,
copied by hand into the phase-2 spec), and the new `inbound_unload_rider` spec
(`Tests/unit/test_run_history.py`, `Optimization/config/whatif_config.py`) drops it.

**Why:** it is tempting to assume phase 2 "depends on" phase 1 having completed because the
funnel narrative reads that way; in the actual run tree the dependency is a label match on a
digest, not a completion check, so phase 2 can be (and was) launched before phase 1 finished
without the pin catching it.

**How to apply:** before trusting a phase-2 run's pair choice, check the pin's label against
phase 1's actual winner by hand — the pin will pass even if they don't correspond to the same
ranking exercise. Related: [[cell-record-overlay-at-job-build-time]],
[[cell-complete-skips-a-torn-pair]].
