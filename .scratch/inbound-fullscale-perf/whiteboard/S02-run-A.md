# S02 -- run A: the 400k baseline with the inbound carve attached (registered 2026-09-24)

## Question

At full scale, where inside `reord_s` does the time go -- per cell, per rule -- and how much
of each arm's wall is setup?  This run is the "before" every optimisation is measured
against.

## Run

Code de605170 (I0 on top of 109891a1), from a `git archive` snapshot.
`_inbound_perf_400k`: cells fifo + gmyopic x {PHASE2_WINNER (rank_cartlabor store /
rank_minlabor fulfillment), (tmin, tmin), PHASE2_RIDER (fifo, fifo)} x {uni, opt} = 12
units; `_inbound_perf_400k_sm`: fifo x {(rank_sortmatch, rank_sortmatch), rider} x {uni,
opt} = 4 units.  Both at `--n-batches 20 --profiles-dir <PROFILE_INPUT_DIR>/../
catalogue_reference_lt0 --profile-run mixed_20260816_131535 --workers 12
--max-tasks-per-child 1 --no-analyze`.

## Predictions (from the plan, before any 400k number with the carve exists)

| # | quantity | predicted | falsified by |
|---|---|---|---|
| A1 | gmyopic x winner: `inb_yplan_s` / `reord_s` | >= 0.60 | < 0.40 |
| A2 | fifo x winner: `put_open_s` / `reord_s` | >= 0.50 | < 0.30 |
| A3 | `sib_setup_s` against each leaf's unnamed remainder (total - named sections) | explains >= 50% | < 25% |
| A4 | yard depth `yard_T_sum / inb_drains` over 20 batches | 10-17 (the campaign's T) | < 5 |
| A5 | gmyopic x tmin: `inb_yplan_s` / `reord_s` | <= 0.30 (the frontier replay) | > 0.50 |

## Measurement

Root `comparison_whatif_20260924_165350` (the sort-match leg is `comparison_20260924_184614`).
Code 75787912 (I0, unpinned specs).  Wall 16:53:50 -> 18:46:11 = **1 h 52 min**:
* parent setup ran to 17:23 (29 min; coverage alone took 1,385 s, because the snapshot's
  fill curve is unmemoised);
* the sim stage took 83 min against a slowest unit of **4,459 s (74 min)**, the gmyopic
  winner pair, uni;
* the sort-match leg took a further 32 min.

Read with `assets/s02_read_run.py`.

### Per-arm anatomy (seconds; shares are of `reord_s`)

| cell | arm (store / ff leaf) | total | reord | yplan | dplan | put | put_open | startup | sib_setup |
|---|---|---|---|---|---|---|---|---|---|
| fifo | uni rider (fifo) | 522 / 388 | 60 / 62 | -- | -- | 20% | 0 | 103 / 250 | 147 |
| fifo | uni winner (cartlabor / minlabor) | 747 / 595 | 267 / 269 | -- | -- | **82%** | 4% | 113 / 275 | 163 |
| fifo | opt winner | 1,109 / 394 | 167 / 163 | -- | -- | **73-75%** | 4% | 161 / **886** | **725** |
| fifo | uni tmin | 612 / 463 | 129 / 131 | -- | -- | 60% | 41% | 111 / 273 | 162 |
| gmyopic | uni rider | 726 / 566 | 265 / 263 | **76%** | 4% | 4% | 0 | 124 / 294 | 170 |
| gmyopic | uni winner | **4,459 / 4,283** | **3,997 / 3,997** | **86%** | 9% | 4% | 0.2% | 138 / 324 | 186 |
| gmyopic | opt winner | 3,155 / 2,490 | 2,299 / 2,294 | **85%** | 9% | 4% | 0.3% | 192 / **866** | **674** |
| gmyopic | uni tmin | 601 / 424 | 129 / 129 | 12% | 1% | 53% | 37% | 137 / 325 | 188 |

Yard: 20 drains per arm; `yard_T_sum` is 298-312 (mean T = **15.3**, max 24-26) and
`yard_pulls` is **294-295**.  gmyopic plan work is 376-384 rounds and 6,124-6,458
placements per arm.

### Against the predictions

| # | predicted | measured | verdict |
|---|---|---|---|
| A1 | gmyopic x winner yplan/reord >= 0.60 | 0.85-0.86 | **met** |
| A2 | fifo x winner put_open/reord >= 0.50 | 0.044-0.045 | **REFUTED**: the put drain is 73-82% of reord but its opens are only 4%, so the TAKES are the sink (~470 units per open) |
| A3 | sib_setup explains >= 50% of the unnamed remainder | 40-43% on most store leaves, 82% on the opt winner | **partly**: ~220 s per store leaf stays unnamed after sib_setup (fulfillment leaves ~90 s) |
| A4 | mean yard depth 10-17 | 15.3 (max 26) | **met**: 20 batches reach the campaign's depth |
| A5 | gmyopic x tmin yplan/reord <= 0.30 | 0.12-0.14 | **met** |

### What this changes

1. **O1 saves almost nothing at 400k under the drain fill.**
   * A drain stages 294 of 306 ranked trailers (**96%**): four doors plus refills empty the
     yard.
   * The meso rung's 25.5% (S03) came from its 80 s receiving whistle, an artificial
     stall.  That ladder's own docstring calls the whistle an intervention, not the
     campaign's mechanism.
   * By the plan's rule (drop O1 if pulls/T > 0.8), O1 would not have been built for this
     shape.  It stays because it is exact, costs nothing, and pays under the asap fill,
     which re-ranked the whole yard per plug (S04).
   * Corrected in S03-S04, in the map and in memory `lazy-yard-plan`.
2. **The priced unit is the yard plan through the pool adapter: 3,440 s of the slowest
   unit's 4,459 s** (86% of its 3,997 s reord).  Every `place_load` opens pools, so this
   is O3's territory, and run B measures it.
3. **The put drain's cost is in its takes, not its opens.**  A2's refutation sends O3 to
   the put shape.  `assets/s05b_put_shape.py` (470 units per open, repeated SKUs)
   measures open + seat:
   * travel **151 -> 25 ms (6.0x)**;
   * min-labour **361 -> 55 ms (6.5x)**;
   * the same sequence digests on both snapshots.

   The O(A) argmin per take did not cost what its Big-O suggested.
4. **The opt winner pair's setup costs ~600 s per unit more than any other pair.**  The
   ff leaf's startup is 866-886 s against 250-325 s elsewhere, and the store leaf carries
   it as sib_setup (674-725 s).  `opt_` means the initial placement uses the arm's own
   rule over the whole catalogue, so rank_minlabor places 160k SKUs at setup.  This is a
   new sink, filed under S06b; O3 may already have cut it, and run B will say.
5. **~220 s per store leaf is still unnamed** after sib_setup.  Filed under S06b.
