# 10 - a short dock by door count: does unloading order matter where the doors bind?

Type: task
Status: resolved (probe answered; the priced spec waits on 04)
Blocked by: none for the fifo depth probe; the priced spec by 04, or by the probe's depth

Opened 2026-09-22 from the user's suggestion to "reduce the amount of available doors". The
campaign never tested a dock that was short: at four doors and a door team of 10 the dock
seats 40 receivers against a derived crew of ~23, so the crew bound and the doors were a
ceiling (Experiment 9's scorecard: dock ceiling ~174-182%). Yard contention -- a trailer
waiting for a door still held from the last drain (CONTEXT.md) -- was never the regime.

## Why doors, and why it does not break Q15/Q20

Q15/Q20 decided the FILL TRIAL's depth is declared, not understaffed, because an authored
headcount breaks ADR-0004 (demand declared, crew derived). A door count does not touch that
shape: no crew derivation reads it (`Tests/unit/test_door_scarcity.py` walks
`Optimization/simconfig/` to hold that true), so the crew stays derived and the DOCK is what
is short. Below three doors the door-team cap seats fewer receivers than the crew holds --
two doors seat 20 of ~23, one seats 10 -- so this is an understaffed unloader by physics, the
regime the user reached for in Q13, without an authored crew.

## The mechanism (built 2026-09-22)

The lever already existed per cell: `phase2_inbound_axis(doors=...)` threads `dock_doors` to
the site dock, and `YardTransit` stages at most `doors` trailers. What was missing was a sweep
that reads a door count honestly:

- `door_scarcity_axis(policies, levels)` in `whatif_config` builds each policy at each level,
  cells suffixed `_d<doors>`, beside its four-door twin under the campaign name, identical
  but for `dock_doors` -- a door count is read as a difference inside one matrix.
- `PHASE2_DOOR_PROBE_LEVELS = (3, 2, 1)`, `PHASE2_DOOR_LEVELS = (2,)`.
- Specs: `_toy_doors` (fifo at 4 and 1, toy), `_probe_door_depth` (fifo at 4/3/2/1, winner +
  rider, reference catalogue, staffing pin), `door_scarcity` (fifo, gforecast, ggated_h050 at
  4 and 2 -- registered, not launched).

## The levels are arithmetic, not taste

Seating `s` of the crew scales the receiving load factor by crew / s. From the campaign's
projected rho_recv ~0.82 (memory `inbound-yard-is-a-stable-queue-under-the-era`):

| doors | seats | rho, projected | regime |
|---|---|---|---|
| 4 | 40 | ~0.82 | crew binds (campaign) |
| 3 | 30 | ~0.82 | crew binds; fewer trailers stand at once |
| 2 | 20 | ~0.94 | dock binds; stable, several times deeper |
| 1 | 10 | ~1.9 | UNSTABLE: depth grows with the run |

One door ranks nothing: above 1 the drain order is forced by what stands and the policies
converge on arrival order (Q15/Q20's own argument, and the trailer-bound result). It is in
the fifo probe only as the control that shows the knee. Two doors is the priced level.

## Cost, which decides the order of work

The exact evaluator costs T(T+1) placements per plan, and the pool adapter's drain cost is
cubic in T (memory `inbound-pool-adapter-multiplier-is-not-13x`). At the campaign's depth
(mean ~13, max 23) a gain cell was already the whole run. An M/M/1-shaped depth goes as
rho / (1 - rho): ~4.6 at 0.82, ~16 at 0.94 -- about 3.5x deeper, so a gain cell at two doors
could cost 10-40x the campaign's. Hence: fifo first, to measure the depth instead of
projecting it; the priced spec waits for ticket 04's cheaper evaluator unless the probe shows
two doors standing shallow.

## Verified so far

- Toy (`_toy_doors`, 2026-09-22): both cells build and run, each records its own door count
  (`yard_drains.free_doors_start` 4 vs 1). At toy scale nothing else moves -- the yard never
  holds two trailers and the toy crew fits one door team -- which is the toy's known limit
  (memory `toy-fixture-cannot-discriminate-unload-policies`), not a failure of the lever.
- The binding itself is pinned by `Tests/unit/test_door_team_cap.py` (22 over two doors is
  10/10 with two idle; a binding cap moves the labour).

## Remaining

1. Launch `_probe_door_depth` after ticket 03's probe finishes (never beside it: the two
   would share the machine and the probe's wall is part of its answer). Read per cell: yard
   depth mean / p95 / max from `yard_drains`, contention share (drains starting with trailers
   standing and no free door), binding cuts, receiver busy, overage.
2. Decide the priced run from the measured depth at two doors: launch `door_scarcity` now if
   T stays near the campaign's, or after ticket 04 if not.
3. Optional, not decided: the fill trial (06) could take two doors as a second regime -- a
   fill whose dock is short as well as deep. The user's call once 06 exists.

## The depth probe's answer (2026-09-23)

`_probe_door_depth` (`comparison_whatif_20260923_023538`, reference catalogue, fifo, winner pair
+ rider, both stock modes, 16 units, ~75 min).  Per door count, over the 40 site days (ranges
across the four pair x stock-mode units):

| doors | yard depth mean | max | last 10 days | drains waiting on a door | standing at end | dwell median / p95 |
|---|---|---|---|---|---|---|
| 4 | 16.6-16.8 | 24-26 | 16.7-17.0 | 9-12 of 40 | 7-9 | 8.5 h / 11.4-11.9 h |
| 3 | 16.6-17.0 | 25 | 16.6-17.0 | 9-12 of 40 | 2-8 | 8.3-8.5 h / 11.2-11.8 h |
| 2 | 24.8-25.2 | 41-42 | 33.6-34.5 | 28 of 40 | 15-17 | 12.7-13.0 h / 20.0-20.3 h |
| 1 | 137.5-137.7 | 288-289 | 255-257 | 36 of 40 | 282 | 63-64 h / 136-137 h |

**The arithmetic held.**  Three doors equals four (the crew binds, not the doors).  Two doors
is the short dock: a trailer waits for a door on 28 of 40 drains, dwell rises ~50%, and the
yard is still DEEPENING at the run's end (last-10 mean 34 against 25 overall) -- near
saturation, not settled within 40 days.  One door is the unstable queue projected (~1.9):
depth grows through the run to ~290 with 282 trailers never unloaded.

**What it means for the priced spec.**  `door_scarcity` at 2 doors would plan at yard depths of
25-42.  The exact plan cost 146-267 s at depths 14-25 (ticket 03), and a pool-adapter drain
grows ~cubically in depth, so a gain unit there would run for a day or more.  It stays
registered and unlaunched until ticket 04 (now waiting on the user's reframing) gives a cheaper
evaluator.  The fifo half of the question is answered here.
