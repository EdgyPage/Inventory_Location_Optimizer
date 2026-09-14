# The coupled ladder saturated at the catalogue ceiling, and printed the saturation as data

Type: defect
Status: resolved

The coupled ladder from ticket 11's "honest next step" ran, and its first four rungs looked like
the finding the whole effort was chartered to find. They were not. What the second run established
is a tool defect, a contaminated measurement, and one real relationship.

## What the first coupled ladder showed

`--coupled --rungs 5000 10000 20000 40000 --batches 10`, quiet host:

| skus | drain_u | drain_p | **DRAIN x** | run_u | run_p | **RUN x** | T |
|---|---|---|---|---|---|---|---|
| 5,000 | 0.052 | 0.203 | 3.89 | 18.2 | 17.8 | 0.97 | 1.00 |
| 10,000 | 0.103 | 0.391 | 3.78 | 27.8 | 26.1 | 0.94 | 1.00 |
| 20,000 | 0.132 | 1.069 | 8.10 | 35.4 | 37.0 | 1.05 | 1.27 |
| 40,000 | 0.265 | 4.389 | **16.59** | 57.6 | 77.3 | **1.34** | **2.25** |

Read as a trend this says RUN x tracks yard depth, and that the uncoupled store-leaf ladder
(ticket 11) reported flat 1.08-1.12 only because the store leaf's yard never stands. That reading
was written down and then refuted by its own repeat, which is the point of repeating a top rung.

## What the repeat showed

`--coupled --rungs 40000 60000 80000 --batches 10`:

| skus | drain_p | place_ld | pools | T | maxdep | DRAIN x | run_u | run_p | RUN x |
|---|---|---|---|---|---|---|---|---|---|
| 40,000 | 6.184 | 132 | 1,282 | 2.25 | 3 | 16.19 | 72.1 | 96.1 | 1.33 |
| 60,000 | 6.044 | 132 | 1,282 | 2.25 | 3 | 15.95 | 91.4 | 90.8 | 0.99 |
| 80,000 | 6.001 | 132 | 1,282 | 2.25 | 3 | 16.66 | 81.4 | 82.8 | 1.02 |

**Every priced quantity is identical at all three rungs.** Same `place_load` count, same pool
count, same yard depth, same max depth, drain within 3%. These are not three rungs. They are the
same run three times.

## Cause 1: the catalogue is the ceiling, and `max_skus` above it is silent

`run_fullfid` took `pairs[0]` of `find_latest_db_pairs`, and the LATEST catalogue under
`PROFILE_INPUT_DIR` declares **40,000 SKUs**. Three are available:

| catalogue | declares |
|---|---|
| `perf_mixed_40k__...` (latest) | 40,000 |
| `mixed_rehearsal_20260815__...` | 150,000 |
| `mixed_20260816_131535__...` | 400,000 |

`--max-skus 60000` against a 40,000-SKU catalogue is not an error and not a warning: it takes
everything. In the output that is indistinguishable from a subsystem that stopped growing — which
is exactly how it was first read. This is the `a-bin-cap-is-self-defeating` shape in the other
direction: there a cap below the declaration REFUSES; here a declaration above the fixture is
silently truncated.

**Fixed** by letting the declaration pick the fixture. `run_fullfid(min_catalogue=N)` walks
`ProfileTree` newest-first and binds the most recent catalogue that DECLARES at least N SKUs
(read from `run_metadata.params_json['num_skus']` — the declaration, not a row count, so a
truncated table cannot agree with itself), and refuses with the largest available when none can
serve. The ladder sizes the floor on the **top rung** and binds ONE catalogue for every rung:
per-rung selection would change generator vintage partway up the ladder (40,000 is 2026-09-13,
400,000 is 2026-08-16) and report that as growth. A rung that still exceeds its catalogue now
prints `SATURATED ... this rung RE-RUNS the one at the ceiling and is not a measurement.`

`min_catalogue=None` keeps the historical path exactly, so every existing caller is unchanged.

## Cause 2: the RUN column needs a quiet host, and did not have one

The second ladder ran concurrently with a 13-minute CPU-bound `Tests/calltree` pytest. The DRAIN
column survived that — it measures a section, and reproduced to 3% (6.184 / 6.044 / 6.001) — but
the run WALL is precisely what a competing job destroys: the unpriced pole alone read 72.1 / 91.4
/ 81.4 seconds for identical work.

That is larger than the entire pricing cost (~5.8 s of drain), which is the real conclusion:

**RUN x is not resolvable by this instrument at this scale.** Four samples of the same quantity —
identical catalogue, identical counts — gave run differences of +19.7, +24.0, -0.6 and +1.4
seconds. Ticket 31 section 5 already recorded that no absolute wall survives a comparison across
runs here; pairing within a rung does not rescue it when the two poles are sequential subprocesses
and the host is busy.

## What survives

1. **DRAIN x rises with yard depth**, and this is reproducible across two independent ladders:
   3.89 and 3.78 at T = 1.00, 8.10 at T = 1.27, and 16.59 / 16.19 / 15.95 / 16.66 at T = 2.25.
   Four independent measurements of the T = 2.25 point agree to 4%.
2. **`entries` is constant at 18 across every rung.** The number of drains does not grow with the
   catalogue; what grows is the work per drain. A raw exponent on drain seconds would have
   reported "receiving got more expensive" when the correct statement is "each drain got more
   expensive" ([[a-count-is-not-a-claim]]).
3. **Ticket 11's conclusion stands, with a better reason.** Not "the multiplier is flat" but "the
   pricing is a few seconds of drain against a run wall whose own variance exceeds it".

## What is still open

Whether DRAIN x keeps climbing with T at campaign scale. T = 2.25 is the ceiling of a
40,000-SKU catalogue; the campaign runs 400,000. That ladder is now a command rather than a build
— `--rungs 20000 50000 100000 200000` binds the 400,000 catalogue for all four rungs — and it must
run on a quiet host.
