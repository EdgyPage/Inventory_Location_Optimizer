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

Code: ad62b9eb (O1 + O3 + O9), from a `git archive` snapshot, launched with run A's exact
flags the moment run A finished (no overlap).  Roots: `comparison_whatif_20260924_191909`
and the sort-match leg `comparison_20260924_203032`.

### Identity

`run_digest --cell` against run A: **k1_off_fifo IDENTICAL, k1_off_gmyopic IDENTICAL,
the sort-match leg's k1_off_fifo IDENTICAL.**  Every decision counter agrees as well
(drains, yard depth, yard pulls, put opens, put units).  Only the plan's work counters
moved: rounds 384 -> 367, placements -1 to -2%, which is O1 skipping the unpulled tail.

### Wall

| | run A | run B | ratio |
|---|---|---|---|
| whole run (main leg) | 112 min | **71 min** | **1.58x** |
| parent setup | 29.6 min | 18.2 min | 1.63x (coverage 1,385 -> 782 s: O9) |
| sim stage | 83 min | 53 min | 1.57x |
| slowest unit (gmyopic uni winner) | 4,459 s | **2,699 s** | 0.61x |

### Per arm, B / A (`assets/s02_read_run.py A B`)

| arm | total | reord | yplan | dplan | put | ff startup | store sib_setup |
|---|---|---|---|---|---|---|---|
| gmyopic uni winner | 0.59-0.61x | **0.57x** | **0.56x** | 0.61x | 0.48x | 0.98x | 0.98x |
| gmyopic opt winner | 0.63-0.67x | **0.63x** | 0.63x | 0.65x | 0.57x | **0.58x** | **0.49x** |
| fifo uni winner | 0.85-0.86x | 0.63x | -- | -- | **0.54x** | 0.96x | 0.94x |
| fifo opt winner | 0.63x (store) | 0.78x | -- | -- | 0.65x | **0.50x** | **0.43x** |
| riders (fifo) and tmin, both cells | 0.95-1.01x | 0.93-1.02x | ~0.9-1.0x | noise | ~0.9-1.08x | ~0.95-1.0x | ~0.93-1.0x |
| sort-match leg, every arm | 0.96-1.04x | 0.95-1.07x | -- | -- | 0.92-1.06x | -- | -- |

### Reading

* **The win is O3's, three ways.**
  1. It sped up the gain evaluator's virtual placements, the yard and dock plans (0.56-0.65x).
  2. It sped up the put drain's takes (0.48-0.65x).
  3. It halved the `opt_` winner's setup: the initial placement of the whole catalogue
     runs through the same pools, with ff startup going 886 -> 446 s and the store's
     sib_setup 725 -> 309 s.  That is S02's new sink, half gone as a side effect.
* **The controls held.**  O3 does not touch fifo, tmin or sort-match (the ranked-assign
  heap pool), and they move within the ~5% band.  That is the noise floor of this A/B.
* **O1 is ~0 here, as run A predicted** (a 400k drain stages 96% of the yard).
* **O9** took the parent's coverage step from 23 to 13 min.  The rest of setup is the
  unmemoised line-floor solve (4 + 3 min) and the warehouse build.

### What is left (the next sinks, by size)

1. **The yard plan is still 85% of the slowest unit's reord: ~1,930 s of 2,699 s.**  The
   bench saw 4.6x per pool open, but the yard plan moved only 1.8x.  So the pool open is
   no longer most of a `place_load`.  Next: cProfile one 400k `place_load` to name the
   residue (the evaluator's per-group pricing, `_make_pool`'s construction and the
   tier slices are the suspects).
2. **The put drain** is still 60-70% of the fifo cell's winner reord after 0.54-0.65x.
3. **~220 s per store leaf stays unnamed** after sib_setup, unchanged by B.  This needs
   a section of its own.
4. **The line-floor solve**: ~7 min of the 18-min parent setup.
5. **The S10 vectorisation survey**, which will size candidates against run B's columns.

### Prediction check

The plan's S08 prediction was that the T exponent drops from 3.13 to <= 2.5.  On the meso
ladder, placements went T^1.86 -> T^1.01 (Part 1).  At 400k the yard empties every drain,
so the drain fill's T-growth is not what moved.  The whole curve shifted down by O3's
constant instead.  The campaign's exponent cannot be refit from two runs at the same T;
S09 extrapolates from the per-arm ratios instead.
