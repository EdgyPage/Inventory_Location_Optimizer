# Decide the contention regime under the derived crew

Type: grilling
Status: open

Graduated 2026-09-10 from
[Verify the derived receiving crew under arrivals](23-verify-the-derived-receiving-crew.md).
HITL: `grilling` + `domain-modeling`.

## Question

Under the calibrated era the yard does not bind: 23 read strict contention **0 of 40 drains** on
every leaf and arm, binding cuts 0 (fulfillment) / 2 (store, staged remainder only), door
utilization 15% / 59%, detention p50 0.18 / 0.32 days. The derived receiving crew (22 receivers
on the site's day) clears the 7-15 trailers standing at a freeze through 4 doors inside the
drain. 22's arrival regime -- `PHASE2_DOCK_DOORS = 4`, lead 480 min, spread 0.7 -- was a pilot
OUTPUT found under a per-batch crew grant the era retired, and `yard.binding`'s own sentence on
this run is the declared stop 22 wrote: "NO DRAIN was ever door-bound ... no ordering rule could
have changed anything".

What contention regime does the campaign want, and which knob carries it?

- **Fewer doors** (1-2): the only knob that makes doors bind against a crew sized to its load;
  `PHASE2_DOCK_DOORS` is a run-level default, so the pilot and phase 2 move together.
- **A burstier arrival regime**: a larger lead spread, or a loading rule that batches
  dispatches -- but loading/dispatch is out of scope (v1 FIFO next-fit stays), so only the spread
  is this map's.
- **Accept a slack yard**: then the campaign's premise ("does space-aware inbound beat FIFO")
  has no lever and the fee axis reads zero everywhere (no trailer exceeded 2 days); the honest
  form of this is a declared stop, not a run.

Constraints: the receiving crew is derived and not a knob (department-calibration, "Design the
staffing record", decision 4); the strict reading of criterion (a) stands (22); whichever knob
moves must move on `PILOT_RUN_DEFAULTS` and `phase2_inbound_axis` together, never on a command
line. The answer records the regime and the expected contention it is chosen for; the run that
checks it is
[Re-verify the gate under the lead-aware record](26-reverify-the-gate-under-the-lead-aware-record.md).
