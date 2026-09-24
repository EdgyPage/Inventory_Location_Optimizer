# S18 registered: does door_fill 'asap' move the gate?  (2026-09-24, before any run)

## Question

S16–S16c confirmed the site gate at k = 2.31 (400k) under `drain`.  `drain` carries a quirk:
a receiver idle on an emptying trailer starts the NEXT staged trailer at their own clock,
before its door frees, so a door is briefly worked by two trailers' teams.  That is door
capacity `asap` does not have (it holds every dealt worker to the door-free instant).  If the
quirk carried measurable capacity, the gate moves EARLIER under `asap`.  Separately, the 40k
k10 `asap` run read store pick s/item +0.0% to +1.3% against `drain` on one batch seed.

## Hypothesis

The quirk is one trailer's last pack per door-freeing, small against ~0.9–1.2 h occupancy, so
the gate does not move; `asap` removes the drain quantum from the wait and nothing else.  The
40k store reading is noise.

## Runs (snapshot df814fc0)

| id | scale | k | fill | batch seed |
|---|---|---|---|---|
| A-d | 40k | 10 | drain | 2024 |
| A-a | 40k | 10 | asap  | 2024 |
| B   | 400k | 2.2 | asap | 1337 (pairs with `comparison_whatif_20260923_164427`) |
| C   | 400k | 2.6 | asap | 1337 (pairs with `comparison_whatif_20260923_175345`) |

## Predictions

| # | quantity | predicted | falsified by |
|---|---|---|---|
| A1 | store pick s/item, asap vs drain, seed 2024, 4 store arms | \|diff\| < 0.5% on every arm | the fifo arms again ≥ +0.5% (same sign as seed 1337) |
| A2 | yard wait mean, seed 2024 | drain ~6.7 h → asap < 1 h | asap ≥ 1.5 h |
| B1 | offered ρ_site at k 2.2 | 0.96 ± 5% (arrivals and occupancy are not the fill's) | outside the band |
| B2 | lifo vs fifo, k 2.2 | **0 of 8** arms significant, as under drain | ≥ 2 of 8 significant (the gate moved below 2.2) |
| B3 | fifo-cell yard wait mean, k 2.2 | drain 13.3 h → ≤ 9.9 h (at least the ~3.4 h quantum removed); standing at end ≤ drain's 22 | wait ≥ 13.3 h or standing grows |
| C1 | yard, k 2.6 | unstable (late waits > early by ≥ 4 h) | stable |
| C2 | lifo vs fifo, k 2.6 | ≥ 5 of 8 significant, all lifo-cheaper (re-rank at every plug only sharpens lifo) | < 5 of 8, or a fifo-cheaper arm |
| C3 | fifo-cell yard wait mean, k 2.6 | drain 27.3 h → ≤ 23.9 h | ≥ 27.3 h |

Measured with `assets/s11_measure.py` (P4, the paired moving-block interval) and the yard
columns `yard_trailers.arrived_s / staged_s / emptied_s`.
