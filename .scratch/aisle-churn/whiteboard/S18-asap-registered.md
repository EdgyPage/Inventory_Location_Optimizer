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

## Measured (2026-09-24; roots `comparison_whatif_20260924_083017` A-d, `_083037` A-a, `_084252` B, `_094737` C)

`opt_fifo` and `uni_fifo` are byte-identical (FIFO restock ignores the initial layout), so "of 8"
is 6 independent arms.

| # | predicted | measured | verdict |
|---|---|---|---|
| A1 | \|diff\| < 0.5% on every store arm | -0.45 / +0.16 / -0.06 (fifo cell), -0.17 / -0.02 / **+0.51** (lifo cell); the seed-1337 fifo-arm rise (+0.75 to +1.3%) reverses sign | ✗ by 0.01 pt on one arm; the rise is noise |
| A2 | asap wait < 1 h | fifo cell **1.66 h** (median 0, p95 9.3 h), lifo cell 1.10 h; drain 7.4 / 6.8 h | ✗ -- the residual queue is seed-dependent |
| B1 | offered rho +-5% of drain | arrivals equal (34.0 vs 34.1/day, days 20-38); occupancy **+1.0% / +2.6%** (fifo / lifo cell), so offered load +0.7% / +2.2% | ✓ |
| B2 | 0 of 8 significant | **2 of 8 = 1 of 6 independent**: fulfillment opt/uni_fifo -0.22% [-0.38, -0.06]; all 8 lifo-cheaper, -0.02 to -0.22% (drain: 0 of 8, -0.01 to -0.11%) | ✗ by the letter, at the margin |
| B3 | fifo wait <= 9.9 h; standing <= 22 | wait **7.99 h** ✓; standing 39 ✗ -- not comparable: 35 trailers arrived on day 39 under asap, 2 under drain (drain never admits the last day's arrivals) |
| C1 | unstable | fifo-cell wait 7.8 -> 46.2 h (days 5-14 -> 29-38) | ✓ |
| C2 | >= 5 of 8 significant, all lifo-cheaper | **5 of 8** (store 4 of 4, fulfillment 1 of 4), all 8 lifo-cheaper | ✓ count; ✗ "sharpens": fulfillment -0.21 to -0.51% vs drain -0.44 to -0.92% |
| C3 | fifo wait <= 23.9 h | **22.8 h** (drain 27.3) | ✓ |

**The quirk is door capacity, and it is small.**  Under asap every trailer holds its door
1-2.6% longer (occupancy 0.929 -> 0.938 h, 0.931 -> 0.955 h), and arrivals do not change.  At
k = 2.2, which sits on the edge, that is enough to push one fulfillment arm across
significance.  Scaled through W(k) = 0.140 M + 0.4376 M k, a 2% capacity loss moves the gate
from k = 2.31 to about **2.26**, inside the grid's resolution.  The gate stands; asap does not
make the unloading order matter earlier in any way a campaign could use.

**The drain quantum is what asap removes, at every scale.**  Fifo-cell waits, drain -> asap:
40k k10 6.7 -> 0.7 h (seed 1337) and 7.4 -> 1.7 h (seed 2024); 400k k2.2 13.3 -> 8.0 h; k2.6
27.3 -> 22.8 h.  4.5-6 h comes off in each case, a little more than the 3.4-3.8 h wait-to-the-next-drain,
because a drain-frozen queue also cannot re-deal a door the moment it frees.
Past the gate the queue dominates, so the saving is proportionally small.

**lifo under asap starves the oldest trailers.**  A re-rank at every plug serves a newcomer at
once: lifo-cell waits of the trailers that ARE staged fall to 0.9-1.7 h, while 197 trailers
stand unserved at the end of the k2.6 run (drain: 186).  The mean over staged trailers flatters
lifo; the order's labour gap is the comparable number.

**Measurement note.**  A drain-mode yard never admits the arrivals of its final day, so
end-of-window counts (standing, arrivals, whole-window rho) differ between the modes by about
one day's trailers.  Compare days 20-38.
