# Decide the contention regime under the derived crew

Type: grilling
Status: resolved

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

## Comments

2026-09-10, from department-calibration
[Declare the coverage against the inbound lead](../../department-calibration/issues/36-declare-the-coverage-against-the-inbound-lead.md):
the record now derives the pipeline's expected order-to-shelf lead from the regime this ticket
picks, `E[ceil(L/D)] = 1 + sum_k (1 - Phi(ln(kD/m)/sigma))`, and solves the fulfillment floor at
it -- so the regime has a STOCK cost readable without a run, and it must be chosen before the
funnel's phase 1 (which now runs with the yard on, 36 decision 11).

| median (min) | spread | expected lead (site days) |
|---|---|---|
| 480 | 0.0 | 1.000 |
| 480 | 0.3 | 1.511 |
| 480 | 0.5 | 1.600 |
| 480 | 0.7 | 1.766 (pilot; the run read 1.78 / 1.75) |
| 480 | 1.0 | 2.170 |
| 240 | 0.7 | 1.192 |
| 360 | 0.7 | 1.460 |
| 600 | 0.7 | 2.084 |
| 960 | 0.7 | 3.050 |

Doors do NOT enter the form (36 decision 3: the record stamps the unconstrained lead, the
yard's excess is the campaign's effect), so doors are the record-neutral contention knob; a
larger spread buys burstiness at the price of more fulfillment stock and a larger warehouse.
The re-check (26) will print the realized lead and `1 - fill(realized)` as the explained
supply level, so a regime that binds is expected to read above the supply band by what the
detention explains.

## Answer

Resolved 2026-09-10 (grilling, six rounds). **The regime the campaign wants is the SITE'S OWN
dock, and the leaf model cannot express it.** Every number this ticket was asked to tune -- the
0.17 fulfillment receiving utilization, the four-times channel asymmetry, "fulfillment cannot
bind" -- is an artefact of running each channel's inbound alone: a mixed catalogue runs store
and fulfillment as independent workers (`channel_regime` filters the inventory; the reorder
stream, trailers, yard, doors and crew all live inside each leaf's manager), while the
staffing record derives ONE site receiving crew and hands it whole to each leaf. A real site
has one dock, trailers carrying both channels' lots, and one crew -- and that dock binds by
itself at the record's headroom with no knob search at all.

### The regime, as chosen

1. **One dock, mixed trailers, one crew.** Both channels' reorders load onto the same trailers
   (FIFO next-fit over the site's released orders), stand in one yard, take one door set and
   one receiving crew -- the crew `staffing.py` already derives as a site total. Expected
   receiving utilization is then the record's own: on the reference pair `0.846` stamped
   (`536,127 / 633,600` crew-seconds per day), `0.867` realized (`109,600 + 439,700` over the
   two leaves of the 23 run). Contention is the day cutting under the arrival law's
   burstiness, not a door count: with ~15 trailers a day (Poisson-like, cv ~0.25) a single
   day's load exceeds the crew's day on roughly a quarter of days before carry-over, inside the
   acceptance band below. The closed form proper -- trailers per day from the script's lots
   over the trailer's capacity, crew-seconds per pack from the cost model -- is what 26 reads
   the realized run against; the figures here are the 23 run's realized loads, recorded so the
   band has a number to be chosen for.
2. **The lead law is held at median 480 min, spread 0.7** (`E[ceil(L/D)] = 1.766` site days).
   Median and spread move the coverage record, the floor and the warehouse (36); doors and the
   cap do not. Burstiness comes from the shared dock, not from a wider spread.
3. **Door-team physics: at most ten receivers support one trailer's unload and pack**, every
   worker additive, even splits across staged trailers, the steps unmodelled. The cap is a
   DECLARED knob (`INBOUND_DOOR_TEAM`, default None = today's uncapped dealing) on
   `PILOT_RUN_DEFAULTS` and every axis entry, applied in both allocation modes. At cap 10 the
   site's 22 receivers need three doors to be fully dealt; `PHASE2_DOCK_DOORS = 4` stays, so the
   CREW is the binding resource and doors are a ceiling, which is the physical picture (the
   receiver count is implicitly capped by unmodelled unloading equipment). Built by
   [Cap the door team](28-cap-the-door-team.md).
4. **Receiver utilization is a REPORTED metric, never a re-derivation.** The crew stays as
   derived; the scorecard and the equilibrium report show the crew's busy share and the dock's
   parallelism ceiling (`cap x doors` over the crew), no band. Same ticket, 28.
5. **Acceptance band per site, over the measured window** (what "regularly" means in 10's
   criteria): strict contention on a quarter to a half of drains; detention p50 under the fee
   threshold with a nonzero, non-saturated overage share; yard depth stable across the window
   (no monotone climb); trailers standing at run end a tail, not a queue. Above the band is a
   runaway, below it is slack. Read per SITE now, not per leaf: under one dock both channels
   experience the same contention, and the per-leaf "both must bind" reading is retired with
   the leaf model.
6. **The fee threshold is 26's.** `PHASE2_THRESHOLD_DAYS = 3.0` was a pilot output under the
   retired per-batch grant; detention under the site dock will be shorter and the
   overage-by-threshold table moves. 26 reports the table and fixes the threshold before phase
   2 (`gain_gated` reads it at simulation time); phase 1 (`fifo` only) does not need it.
7. **Off the route.** Fewer doors: hollow, since an uncapped team unloads at the crew's rate
   at any door count, and under the cap a leaf still cannot bind (fulfillment's whole inbound
   is about a third of one ten-person door). A wider spread: buys burstiness at the price of
   fulfillment stock and warehouse (36). Share-slicing a leaf's crew and doors by channel load:
   the same binding at 0.85, but it is the separate-crews picture the user rejected. Background
   trailers from the other channel's script: one crew and mixed cargo at the dock, but a gain
   policy would need a stand-in value for a lot that places nowhere here, and put-away keeps
   the same artefact. Rejected in that order.

### What this forces

**Coupling the channels at the dock is beyond this map's destination** and is ruled out of
scope here with a successor effort (map, Out of scope): one inbound simulation of both
channels' reorders, one yard/doors/crew, unloaded lots handed to each channel's own put-away
and picking; every phase-2 cell a PAIR of arms (a store rule and a fulfillment rule) under one
inbound policy; a site level in the run tree above the two channel leaves; the faithful-to-arm
gain evaluator pricing a trailer against two arms' machinery at once; the funnel's selection
per channel feeding paired cells rather than independent ones. It reaches the leaf model, the
run-tree contract, the funnel's cell arithmetic and the analysis -- a charting session, not a
ticket. The sizing inventory of the seams: [site_dock_sizing.md](../assets/site_dock_sizing.md)
(a redesign of the leaf model; the gain evaluator pricing two arms from one trailer is the
sharpest open question, and the staffing record is the one layer already describing the site).

**The campaign holds behind it.** Execution order is now: 27 and 28 (independent, this map) ->
the site-dock effort -> 26 re-verifies the gate on the coupled dock -> 24 -> phase 1 ->
selection -> phase 2 -> publish. 26's "supply in band AND the yard binds" is read on the site.

**Glossary** (`CONTEXT.md`): *Yard contention* sharpened to the strict reading 22 settled;
*Door team* gains the cap; *Site dock* added.
