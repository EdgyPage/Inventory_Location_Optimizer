# At campaign scale the pool-adapter multiplier is 3.24x, and ticket 11 is retracted

Type: research
Status: resolved

**This retracts ticket 11's headline and the map's destination clause.** Both said the worry that
chartered this effort was not supported. They were measured on a ladder that could not reach the
regime the campaign runs in, for a reason ticket 12 identifies: the catalogue was the ceiling.

## The measurement

`--coupled --rungs 25000 50000 100000 200000 --batches 10`, then `--rungs 200000 400000`, both on a
quiet host, all rungs binding ONE catalogue — `mixed_20260816_131535`, which declares 400,000 SKUs,
the campaign's own size.

| skus | drain_u | drain_p | **DRAIN x** | run_u | run_p | **RUN x** | T | maxdep | pools | crew | **rho** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 25,000 | 0.150 | 1.477 | 9.83 | 62.6 | 58.7 | 0.94 | 1.61 | 2 | 792 | 2 | — |
| 50,000 | 0.310 | 5.963 | 19.22 | 79.2 | 89.4 | 1.13 | 2.74 | 4 | 1,860 | 3 | — |
| 100,000 | 0.617 | 25.429 | 41.22 | 122.4 | 148.3 | 1.21 | 4.36 | 6 | 4,988 | 6 | — |
| 200,000 | 1.398 | 118.255 | 84.62 | 217.3 | 333.1 | 1.53 | 6.90 | 12 | 9,580 | 11 | 0.836 |
| 200,000 *(repeat)* | 1.408 | 118.791 | 84.38 | 223.9 | 333.5 | **1.49** | 6.90 | 12 | 9,580 | 11 | 0.836 |
| **400,000** | 2.739 | **956.165** | **349.07** | 423.4 | **1372.9** | **3.24** | **12.97** | 23 | 24,912 | 21 | **0.837** |

**rho = 0.837 at the top rung**, against the campaign's projected 0.819. This is not an
extrapolation into the campaign's regime; it is a measurement inside it, which is exactly what
section 4's "a ladder must fit against measured rho and must reach rho ~ 0.82" asked for.

**The top rung repeats.** 200,000 was run twice, in separate invocations: drain 118.255 vs 118.791
(0.45% apart), RUN x 1.53 vs 1.49. The trap that cost this effort a retracted knee finding, and
cost it ticket 11, does not apply here.

## The answer to the question that chartered the effort

`inbound-optimization` ticket 31 sized phase 2 at **1.63-1.93x** per coupled unit, and recorded its
own caution: it measured `('fifo','tmin')`, **the only two adapters that open no pool**, while
eight of `PHASE2_PAIRS`' twelve arm-slots are pool adapters.

**On a pool adapter, coupled, at the campaign's catalogue size and rho: 3.24x.**

That is 1.85x the midpoint of the band the campaign's 8.6-9.7 h sizing rests on. The worry **is**
supported. Ticket 11's "the worry is not supported" was measured on a store leaf whose yard never
stands (T 1.14-1.30) and then on a coupled ladder whose catalogue capped T at 2.25. The campaign
runs at **T = 12.97**.

## The mechanism, fitted over a 16x span

| quantity | vs | k | r^2 |
|---|---|---|---|
| drain seconds (priced) | skus | 2.30 | 0.993 |
| yard depth T | skus | 0.74 | 0.997 |
| **drain seconds (priced)** | **T** | **3.13** | **0.998** |
| `_make_pool` calls | T | 1.67 | 0.996 |
| seconds per pool open | T | 1.46 | 0.976 |
| `place_load` per entry | T | 1.81 | 0.999 |
| run wall (unpriced) | skus | 0.70 | 0.970 |

**The drain is cubic in yard depth, and the cube decomposes exactly**: pools ~ T^1.67 times
seconds-per-open ~ T^1.46 is T^3.13, the fitted value to two decimals. `place_load` per entry at
T^1.81 is `plan_order`'s O(T^2) greedy; pools per `place_load` is flat at ~10; and the remaining
T^1.46 is the cost of opening one pool, which is the term the candidate slice attacks.

**`entries` is constant at 18 at every rung.** Drains do not multiply with the catalogue. Every bit
of this growth is work per drain, which is why an exponent on drain seconds alone would have
reported the wrong thing ([[a-count-is-not-a-claim]]).

**And the drain is the whole cost.** Run delta against drain delta:

| skus | run_p - run_u | drain_p - drain_u | drain share |
|---|---|---|---|
| 25,000 | -3.9 | 1.3 | -34% |
| 50,000 | 10.2 | 5.7 | 55% |
| 100,000 | 25.9 | 24.8 | 96% |
| 200,000 | 115.8 | 116.9 | 101% |
| 400,000 | 949.5 | 953.4 | 100% |

At 400,000 the receive drain is **956 s of a 1,373 s run — 70% of the entire run**. The small-rung
noise that made ticket 12 conclude "RUN x is not resolvable" was real and is now irrelevant: at
campaign scale the signal is 950 s against a ~20 s wall variance.

## What this does NOT say

- **It is 10 batches, not 40 site days.** The campaign's unit is far longer, and `entries` would
  grow with it. The RATIO is what transfers; the absolute seconds do not.
- **It is one pool adapter** (`uni_rank_labor_norsl`), not the twelve arm-slots. It is the family
  eight of them belong to, which is the gap ticket 31 flagged — but `rank_minlabor`, store's #2 and
  fulfillment's #1, copies the two-level `aisle_member_pos` and was not separately measured here.
- **It is one fullfid run per pole**, not a full coupled unit with every arm.
- **It does not restate the campaign's hours by itself.** 3.24x against 1.75x suggests the 8.6-9.7 h
  sizing is low by roughly 1.85x, i.e. ~16-18 h at 4 workers — but that arithmetic assumes the
  ratio transfers across the two differences above, and that assumption is not measured.

## Consequence for ticket 10

The candidate slice was rejected on a range topping out at 6,000 SKUs / 5,878 opens, where it was
already **-48.1%**. The separating variable it identified is OPENS. This ladder runs **24,912 opens
at 400,000** — 4.2x past the point where the slice was already winning by half — and its regression
band (600-2,000 SKUs, +128.3% and +20.5%) is far below anything phase 2 runs.

That rejection was made on the same too-small range as everything else this effort measured, and it
should be revisited. Ticket 10 already names the shape that needs no tuned constant — bucket and
select in ONE pass per open, no memo, "a win at every scale" — and also names why it was not taken:
it needs the pool to accept pre-bucketed input, a signature change in `Assignment_Functions` that
touches the restock path this effort kept out of scope. **That is a scope decision, not a technical
one, and it is now worth taking to the owner** with the T^1.46 per-open term as the reason.
