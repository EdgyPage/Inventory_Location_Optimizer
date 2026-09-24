# S08 -- the growth curves after O1 + O3, and run B

## Part 1 -- the yard-depth ladder (`assets/s08_yard_ladder.py`)

### Question

Did O1 (lazy yard plan) and O3 (numpy boundary) shrink the GROWTH in yard depth T, or
only the constant?

### Prediction (registered in the plan)

T exponent 3.13 -> <= 2.5.  The 3.13 was the priced drain's wall against T at the
campaign shape.  On this ladder, the eager plan's placements are T(T+1), so the lazy
plan's p(2T - p + 1) should take the placements' slope from ~2 to ~1.

### Method

The calltree growth ladder's five `yard` rungs (lead 960 -> 0 min) on its
`inbound_gain_pool` config: gain_forecast on both knobs, the rank_labor pool adapter,
1 door, 10 batches.  One fresh interpreter per rung.  T is the mean standing yard at a
drain's freeze, read off the inbound probe, which exists on BOTH sides, so the x axis
means the same thing.  Three sides ran concurrently under the same load (run A's 12
workers were busy alongside):

* `base`: 75787912 (I0 only: eager yard plan, Python boundary);
* `head, eager`: ad62b9eb with the yard ranking forced eager (O3 alone);
* `head`: ad62b9eb (O1 + O3).

### Measurement (`assets/results/s08_*.txt|json`)

| lead | T (max) | places base -> head | yard plan s: base / O3 only / O1+O3 | reord s: base / O3 only / O1+O3 |
|---|---|---|---|---|
| 960 | 1.5 (4) | 70 -> 68 | 0.39 / 0.46 / 0.49 | 0.63 / 0.76 / 0.80 |
| 480 | 2.5 (7) | 158 -> 116 | 0.69 / 0.85 / 0.84 | 1.02 / 1.28 / 1.31 |
| 240 | 4.9 (18) | 578 -> 226 | 1.64 / 1.77 / 1.07 | 2.32 / 2.27 / 1.68 |
| 120 | 6.6 (25) | 1,038 -> 326 | 2.40 / 2.51 / 1.15 | 2.91 / 3.02 / 1.74 |
| 0 | 9.8 (36) | 2,262 -> 440 | 3.99 / 3.21 / **1.23** | 4.55 / 3.68 / **1.84** |

Picks and placements are identical across all three sides at every rung.

| log-log slope vs T | base | O3 only | O1 + O3 |
|---|---|---|---|
| yard placements | 1.86 | 1.86 | **1.01** |
| yard plan seconds | 1.25 | 1.06 | **0.47** |
| reord seconds | 1.07 | 0.85 | **0.43** |

### Reading

* **O1 removed a power of T from the plan's work**: placements went from quadratic to
  linear, as p(2T - p + 1) at a pull count set by doors and refills says they should.
  **Prediction met**, on the ladder that can measure it.  The 400k rung of the same curve
  is run B's.
* **O3 is a constant, and on the meso rung it is a small LOSS at shallow T.**  The meso
  warehouse has a few dozen aisles, so numpy's per-call overhead outweighs a Python loop
  over them.  It pays from T ~ 5 up, and at the campaign shape (1,400 aisles) it is 4.6x
  per open (S05).  Memory `meso-ladder-cannot-size-the-pool-prologue` warned about exactly
  this: the meso ladder cannot price a pool open.  The wall differences at T <= 2.5 also
  sit inside the concurrent-load noise.
* The 3.13 exponent in the plan was the priced drain's wall against T at 400k, where pool
  opens dominate the drain.  This ladder's reord slope was already 1.07 at base, because
  its drains are small.  Compare like with like: run B against run A.

## Part 2 -- run B (400k, 20 batches)

Pending: run A (`comparison_whatif_20260924_165350`) must finish first.
