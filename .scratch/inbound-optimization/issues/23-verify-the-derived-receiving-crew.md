# Verify the derived receiving crew under arrivals

Type: task
Status: resolved
Blocked by: ../../department-calibration/issues/13-derive-the-expected-travel-closed-form.md

AFK once unblocked. Cross-map gate: this ticket may not start until the department-calibration
map's [Take the reference run](../../department-calibration/issues/09-take-the-reference-run.md)
is resolved — the calibration record it commits is what this run derives its crew from.

## Question

Re-run the pilot gate under the calibrated era as a VERIFICATION, not a search. The original
pilot ([Run the pilot gate](22-run-the-pilot-gate.md)) searched for a receiving regime in which
10's two acceptance criteria hold and committed it as `--recv-crew-size 4 --recv-day-seconds
43200`. Under the era that regime is an ERROR (department-calibration
[Design the staffing record](../../department-calibration/issues/03-design-the-staffing-record.md),
decision 4): the receiving crew derives from the per-channel picker counts via ρ_recv = 0.85 and
f = 1.0, and receives the SITE's 28,800 s day. Nothing is searched for; the question is whether
the DERIVED crew lands the yard in band once arrivals are switched on.

Scope:
- Reshape the `inbound_pilot` spec: era on (drain-or-cap, one release per day, cut and rollover),
  NO crew flags, scheduler `lpt`, one cell, inbound on with the pilot's arrival regime, `fifo` +
  `tmin` (both in `FAITHFUL_GAIN_FAMILIES`), the reference window (40 site days, days 20–39
  measured).
- Read the two acceptance criteria (yard contention, binding-but-stable cuts) THROUGH the
  equilibrium REPORT of
  [Declare the equilibrium bands](../../department-calibration/issues/04-declare-the-equilibrium-bands.md):
  every day drained, `released_late` = 0, utilization in band per department per leaf, missed
  share not trending. A capped day on this cell is "declared throughput not delivered" and is
  reported, not judged.
- Record the outcome on this ticket: in band → phase 1 is launchable; out of band → the
  answer names which department and which leaf, and the campaign holds again (a scalar change
  is a department-calibration decision, not this map's).

Decided at department-calibration
[Sequence the inbound funnel](../../department-calibration/issues/05-sequence-the-inbound-funnel.md)
(2026-09-05), decisions 3 and 5.

## Answer

Resolved 2026-09-10. One 40-day era run on the reference pair (`comparison_20260910_173151`:
`--spec inbound_pilot --n-batches 40` on `catalogue_reference_lt0`, launched detached, `run.log`
clean -- no Traceback, no dead arm, conservation OK on all eight arms) with the yard on and NO
crew flag, read through `Diagnostics/equilibrium_report.py --window 20-39` and the yard tables.

**OUT OF BAND -- the fulfillment leaf, the SUPPLY clause, on every arm. The campaign holds
again.** Not the receiving crew: every crew of both leaves is in band and the dock never stood
a unit overnight. The failing quantity is the coverage record's LEAD TERM, which is a
department-calibration decision (graduated below). And the second reading, which 22 called a
declared stop: under the derived crew **the yard never binds** -- strict contention 0 of 40
drains on every leaf and arm.

### The verdicts (days 20-39; the four arms of a leaf agree to the third digit)

| leaf | labour: cut share vs stamped (band) | drained / capped | utilization pick / put / recv (realized / expected) | supply vs stamped (band +/-0.02) |
|---|---|---|---|---|
| fulfillment | 0.0002 vs 0.0194 (-0.0191, +/-0.0281); carry max 134 u | 19 / 1 | 0.773/0.741 . 0.453/0.474 . 0.173/0.181 | **FAIL: 0.148 vs 0.025 (+0.123), trend +0.020**; 447,915 re-attempt units |
| store | 0.0087 vs 0.0211 (-0.0123, +/-0.0337); carry max 461 u | 12 / 8 (3 by overtime alone) | 0.788/0.756 . 0.380/0.368 . 0.694/0.665 | ok: 0.034 vs 0.025 (+0.009) |

`released_late` ok on both (max lag 252 s / 1,601 s, all behind capped days); rework ok, own-bin
share 0.000, no top-up, no spill. Against the inbound-off reference (`comparison_20260909_204522`,
31): the same crews, the same floors, the same warehouse; the store moved 0.026 -> 0.034 on supply
(in band), the fulfillment 0.027 -> 0.148.

### The yard (fifo; the other three arms are within one drain / one trailer)

| leaf | yard depth at freeze, mean / max | free doors at freeze, mean | contention strict / loose (of 40) | binding cuts | trailers (standing at end) | detention p50 / p90 / max | door utilization |
|---|---|---|---|---|---|---|---|
| fulfillment | 6.5 / 12 | 4.00 | **0** / 36 | 0 | 261 (0) | 0.18 / 0.32 / 0.36 d | 15.4% |
| store | 8.9 / 15 | 3.92 | **0** / 36 | 2 (staged remainder <= 228 u; `yard_end` 0) | 357 (2) | 0.32 / 0.40 / 0.45 d | 58.7% |

`recv_depth` max 0 and `shift_days.standing_dock` 0 on every day of every arm; `recv_cut` nonzero on
0 (fulfillment) / 2 (store) of 20 window batches. No trailer exceeded 2 days on site. The derived
receiving crew is **22 receivers** on the site's 28,800 s day (633,600 crew-seconds against a
derived site load of 536,127 s/day); it clears the 7-15 trailers a day that stand at a freeze
through 4 doors inside the drain, so a door is never held across a freeze. The LOOSE reading
(more trailers standing than free doors, 36 of 40) is the one 22 rejected: a deep yard that
drains completely changes only the ORDER trailers are worked in, never the SET, which is
exactly the case the gain policies gain nothing on.

### Why fulfillment fails: the lead is not in the coverage record

The three flows are the same as the reference's: ordered/day 27,294 vs 27,862, placed/day
13,245 vs 13,518, receiving in band. What differs is WHERE the units are. Under the trailer
pipeline a mean of **48,598 units stand in transit** on the fulfillment leaf against 27,294
ordered/day -- a Little's-law order-to-shelf lead of **1.78 days** (store: 11,510 / 6,580 =
1.75 d); the reference run's in-transit is 0 on every batch, because flag-off a reorder lands
in the batch that placed it. The supply carry follows: mean 26,475 units standing (reference
790), climbing through the window (trend +0.020), demand/day re-offered at 53,438 against a
fresh 28,724.

The coverage record (`Optimization/simconfig/coverage.py`) sets `reorder_point =
d_s * (lead_days + safety_days)` with `lead_days` read from the catalogue's `lead_time_mean`,
which is **0.0 on every carton of the reference pair**, and stamps `pipeline_qty = 0`. So the
era's reorder point covers two safety days of demand and NO lead, and the trailer pipeline
adds a lead of ~1.8 days that the fulfillment section's line-sized levels (floor 1.2668 lines,
order-up-to a median of 13 units) cannot absorb at its demand variance. The store passes
because its implied coverage is ~1,785 days per SKU (memory
`coverage-in-days-floors-the-store-section`): a 1.75-day lead is noise against it. The 1.8
days decompose as the trailer's transit (median 480 min, lognormal sigma 0.7) plus the wait
for the next day's single drain plus the loading wait at the ordering site; the receiving day
itself adds nothing (detention p50 0.18 d).

### Ruled

- **Phase 1 is NOT launchable.** 05's rule: out of band names the department and the leaf,
  and the campaign holds until the record is fixed. The department is none of the three crews;
  the record's lead term is what moves. Fixing it is a department-calibration decision,
  graduated as
  [Declare the coverage against the inbound lead](../../department-calibration/issues/36-declare-the-coverage-against-the-inbound-lead.md).
- **The contention regime is open again.** 22's doors / lead / spread were pilot OUTPUTS under
  a per-batch crew grant that the era retired; under a crew sized to its load the yard is
  slack and `PHASE2_DOCK_DOORS = 4` is a number nothing binds on. Whether the campaign wants
  fewer doors, a burstier arrival regime, or accepts a slack yard (and what the fee axis then
  reads) is
  [Decide the contention regime under the derived crew](25-decide-the-contention-regime-under-the-derived-crew.md).
- **The gate re-runs** once both close:
  [Re-verify the gate under the lead-aware record](26-reverify-the-gate-under-the-lead-aware-record.md).

### Built on the way (committed with this resolution)

- **The standing yard refused every era launch.** `sim_config.inbound_spec()`'s "a yard nobody
  can unload" guard read only the declared `recv_crew_size` key, which under the era is the
  flag-off input the derivation never writes (recorded 0) -- the crew lives in
  `derived.receiving.crew`. The accessor now takes `recv_crew_size=` on the `recv_crew_spec(size=)`
  pattern and `workunits` hands it the derived crew; None (flag-off) reads the declared key,
  byte-identically. The sibling read one call earlier -- the resume planner's `receiving=` --
  had the same blindness (an era+yard run would have accepted a batch-level resume that drops
  the dock's standing units) and now reads the same derived crew.
- **`inbound_pilot` carries its arrival regime as `run_defaults`** (`PILOT_RUN_DEFAULTS`: the
  era plus trailer type 53, standing yard, 4 doors, lead 480 / 0.7 -- the same constants the
  phase-2 axis reads), so the gate re-runs as `--spec inbound_pilot --n-batches 40` and 22's
  reproducibility seam is closed. `_apply_run_defaults` now refuses a key that is not a parser
  flag (a misspelt one would have landed on the Namespace and reached nothing).
- Cost facts, for 24: at 40 site days a store arm is 72-135 s wall, a fulfillment arm 172-471 s
  (tmin is the slow one), peak RSS 2.0-2.6 GB per worker; the whole run -- two preflight
  canaries, build, stock, eight arms on two workers, 56 analysis jobs -- was 36 minutes.
  22's 1,230-1,320 s/unit at published depth is not this regime's number.
