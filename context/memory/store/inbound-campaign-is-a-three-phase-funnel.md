---
name: inbound-campaign-is-a-three-phase-funnel
description: "User decision 2026-09-18: the inbound campaign is a three-phase FUNNEL, not a factorial. Phase 1 ranks restocking rules on picking labour (exists); phase 2 ranks UNLOADING policies under phase 1's single winner, scored on total site labour tie-broken by yard overage; phase 3 crosses best 3 x best 3 x 2 stock modes (18 units) to confirm. The 120-unit factorial launched that morning was stopped after three cells; those cells are calibration evidence, and every relaunch is a fresh root under a new spec."
metadata:
  type: project
---

On 2026-09-18 the phase-2 inbound campaign (`inbound_policies`, 6 rule pairs x 2 stock modes x
10 inbound cells = 120 coupled units) was stopped after three cells and reframed with the user:

- **Phase 1** — restocking rules ranked on picking labour. Exists (`inbound_select` ->
  `restock_selection.json`). Phase 2 takes its rank-1 pair; phase 3 its top three. The
  near-tie margins become FIELDS (`margin_pct`) rather than a hand comment.
- **Phase 2** — UNLOADING policies under that one winner (+ the mandatory `('fifo','fifo')`
  rider): 10 cells x 2 pairs x 2 stock modes = 40 units (`inbound_unload` spec; 24 with the
  yard-bench pre-screen). **Score = total site labour (pick + put-away + receiving under the
  one dock), tie-broken within the noise floor by `yard_overage_days`.** Picking labour alone
  CANNOT rank unload policies: across the three finished cells it was identical to 0.01%.
- **Phase 3** — best 3 restocking pairs x best 3 unloading cells x 2 stock modes = 18 units
  (`inbound_confirm` spec), the only multiplicative run, confirming the scores. A swap inside
  the noise floor is a tie, not a reversal.

**Why:** the cost of the factorial was never the factorial, it was the drain: the priced cell
spent 85.4 h of leaf-time against 5.3 h unpriced, 95.8% in `reord_s` (the gain evaluator's
T(T+1) `place_load` calls over pools that copy every aisle). As launched it was a 4-5 day run
for a signal that lives only in the yard family's tail (p90/max detention, standing at end).

**How to apply:** never rebuild the 120-unit factorial; size a campaign as a funnel over sets
of three. Every relaunch is a FRESH root under its own registered spec (no CLI axis filter
exists; specs are the subsetting mechanism; `run_layout.json` walks declared cells with no disk
probe). The stopped root's three cells (fifo / lifo / gain_myopic under `k1_off_*`) are the
calibration evidence for the optional yard bench. Ticket 02 (the all-aisle union under the
evaluator) is fixed BEFORE phase 2 so the negative-control pair can be carried into phase 3.
Launch per [[detached-runs-import-the-working-tree]] and [[launch-long-drivers-detached]]; the
three-cell numbers are in `.scratch/phase-2-campaign/map.md`. Predecessor:
[[inbound-optimization-map-closed]]; the refuted cheap key: [[unload-key-does-not-rank-like-gain]].
