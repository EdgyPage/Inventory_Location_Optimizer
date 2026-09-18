---
name: deep-tier-save-knee-and-fixed-arm-cost
description: "two deep-tier confounds that make every arm look superlinear — save_s is ~half the run with a knee at the top rung, and each arm pays ~48s before its batch clock starts"
metadata: 
  node_type: memory
  type: project
  originSessionId: 738f91ee-ad65-4791-a2df-c8daafeefa81
  modified: 2026-09-17T00:27:14.614Z
---

Measured on the 2026-09-16 deep ladder (10k → 80k SKUs, 136 arms, 5 rungs). Both confound any
per-arm growth reading and neither is a placement cost.

**`save_s` is the largest growing term in the deep tier and has no owner.** It runs 35.9 % → **48.6 %**
of `total_s` across the ladder with local exponents `1.27, 1.06, 0.97, 2.30` — a knee at the top
rung. On `total_s`, 33 of 34 arms read as accelerating (median last local k 1.68); on
`total_s − save_s`, 22 of 34 and **1.09**. Always read arm growth with save excluded; the ladder now
carries `per_arm_ex_save_s` beside `per_arm_total_s` for this.

**Each arm pays ~48 s of fixed cost before its batch clock starts.** That is why the
commensurability check (`Σtotal_s / workers` vs the wall) reads 0.26–0.57 rather than ~1: the gap
divided by arm-slots is 48.4, 50.0, 77.0, 107.2, 139.0 s — flat across the first doubling, then
catalogue-proportional. `max_tasks_per_child` is pinned at 1 (see [[worker-recycling-pinned-at-one]]),
so every arm spawns a fresh interpreter, re-imports, and reloads the catalogue.

Consequences: the section exponents are **not** contaminated (they come from per-arm sums, and this
is per-arm *startup*); the deep **wall** is the number not to fit (k = 0.72, sub-linear, because a
fixed term amortizes — fit it and you conclude the simulation gets cheaper per SKU); and early local
exponents are depressed, so an amortizing linear arm reads as "accelerating".

With both removed, exactly four of 34 arms are superlinear — `uni_cmin`, `uni_cmax`, `opt_cmin`,
`opt_cmax` at k = 1.26–1.29. See [[a-fitted-exponent-cannot-see-its-own-shape]].

## And `save_s` is COST PER ROW, not volume (measured 2026-09-17, tiny profile)

Eight byte-identical toy runs, 136 arms each. Every arm wrote **166,078-166,278 rows** — a
**1.00x spread** — while its `save_s` varied **2.8x** (0.80-2.20 s) at identical `n_bins`
(102,800) and `n_batches` (6). **Nothing about write VOLUME will move this section.** `save_s`
is 33-34% of total even on the tiny profile, against 48.6% deep.

No save-storm signature at tiny scale (first/last completion quartile 1.04x): the documented
storm is a deep-scale/18-worker phenomenon and the toy run cannot reproduce it.

**Candidate mechanism, NOT yet measured.** `_open_db` sets `journal_mode=WAL` and
`synchronous=NORMAL` and nothing else, so every connection runs on SQLite's stock ~2 MiB page
cache against a database reaching ~1 GB carrying **16 indices** — three of which
(`ix_bp_bin`, `ix_be_bin`, `ix_picks_run_sku`) have keys uncorrelated with insertion order, so
every insert dirties a random leaf page. `CheckpointBuffer._write` opens a **fresh connection
per flush**, so that cache is cold ~10 times per arm. `Schema.connect.writer(tuned=True)`
already implements the fix (256 MiB cache, `temp_store=MEMORY`) and the sim-DB write path does
not use it. This predicts the knee: linear while the hot index pages fit, then degrading — and
**invisible at tiny scale**, which is why the toy profile cannot falsify it.

Since 2026-09-17 the stopwatch is decomposed on the checkpoint log line as
`sql=` / `pkl=` / `drn=` (summing to `db=`) with `rows=` / `dbmb=` / `walmb=` beside it, so the
denominator no longer has to be reconstructed from the databases by hand. Read a ladder's
`save_decomposition` block, not `save_s` alone. See [[toy-run-noise-floor-is-three-percent]]
for what size of win that instrument can actually resolve.

## THE KNEE DID NOT REPRODUCE — it is withdrawn (2026-09-17)

A fresh 5-rung ladder on the same 8x span, same catalogue, quiet host, with `save_s` decomposed,
reads `t_save` **k=1.29 with local exponents 1.31, 1.33, 1.18, 1.34** — steady, no knee. The
`2.295` recorded above does not survive a repeat, exactly as the 2026-08 ladder's 18,343 s knee
did not (`STRESS_TEST_FINDINGS.md:409`, withdrawn). **Two knees at the top rung of this ladder
have now both failed to reproduce; treat a top-rung jump here as machine variance until a second
run shows it.** `_knee` is still the right detector — it is the reporting that must wait.

What IS real and did reproduce: `save_s` is ~46% of the deep tier (11,127 s of 24,290 s at 80k),
and it is superlinear at k=1.29. The cause is not a cliff — it is index maintenance, and it
factors cleanly into rows (k=0.93) x cost-per-row (k=0.36). See
[[save-s-is-index-maintenance]] for the mechanism and the fix (26% cut, three commits).

The **fixed ~48 s per-arm startup** in the section above is unaffected and still stands.
