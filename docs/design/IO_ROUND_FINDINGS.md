# The I/O round — `save_s`, measured and closed

2026-09-17/18. Successor to `docs/design/COMPLEXITY_ROUND_FINDINGS.md`, which recorded `save_s`
as "a real superlinearity and nobody's ticket yet". It now has an owner, a mechanism and a fix.

Every number here comes from the deep ladder (10k → 80k SKUs, 5 rungs, 136 arms, 18 workers,
quiet host, same catalogue both sides) or from the tiny profile against a fixed 12-run baseline.
Pre artifact `...20260917T233843Z_145800bbc9af.json`; post `...20260918T021600Z_efc977e37af1.json`.

---

## 1. The result

| | pre | post | |
|---|---|---|---|
| `save_s`, 80k rung | 11,127 s | **3,253 s** | **0.292x** |
| per-arm `total_s`, 80k | 24,290 s | 17,638 s | 0.726x |
| 80k rung wall | 40.4 min | 33.9 min | 0.839x |
| whole ladder wall | 106.3 min | 92.6 min | 0.871x |
| `save_s` exponent | **k = 1.29** | **k = 0.95** | superlinear → linear |
| cost per row | **k = 0.36** | **k = 0.05** | rising → flat |
| `save_s` share of the tier | 45.8 % | 18.4 % | |

The ratio improves monotonically with scale — 0.596, 0.448, 0.342, 0.321, 0.292 — which is the
signature of removing a cost that grew with table size rather than one that was constant.

**`save_s` appears in the offender table in no form at all after the change.** The only
superlinearity left in the deep tier is `uni_cmin` / `uni_cmax` / `opt_cmin` / `opt_cmax` at
k ≈ 1.25.

---

## 2. The mechanism

A table keyed on `(run_id, batch_id, seq)` only ever ascends, so a checkpoint **appends** to the
tail: the pages it touches are set by how many rows it writes. An index keyed on something
uncorrelated with insertion order — bin location, SKU — **scatters**, so the same checkpoint
rewrites pages across the whole index, and that page count grows every time.

**The cost of a save was set by how much had been written so far, not by how much was being
written now.**

Measured on 12 concurrent arms × 1.5M rows × 15 flushes, on the real output drive:

| | wall | MB/arm | last flush ÷ first |
|---|---|---|---|
| with the scattered index | 67.7 s | 71 | **20.27x** |
| without it | 12.0 s | 46 | 1.33x |

The last checkpoint cost twenty times the first for identical work. Estimated from page geometry
the index should cost ~6x the table (≈92,000 index page-writes against ≈15,000 table ones);
measured 5.6x. **The arithmetic and the stopwatch agree, which is the reason to believe it.**

### 2.1 The decomposition proves it, and it was the whole finding

`save_s` was ONE `perf_counter` window over three unrelated things: a Python drain, the SQLite
flush, and `save_worker_checkpoint`'s pickle write with a five-attempt `os.replace` retry loop.
Splitting them (`sql=` / `pkl=` / `drn=` on the checkpoint line, summing exactly to `db=`) showed
SQLite is **99.5 %** of the section — `t_save_sqlite` 0.366 → 5.42 s against `t_save_pickle`
0.0011 → 0.029 s. Every lever worth testing was a SQLite lever.

Adding `rows=` gave the section the denominator it never had, and the pre-change exponent then
factored exactly:

```
rows per checkpoint   k = 0.93
cost per row          k = 0.36
                      ---------
t_save                k = 1.29
```

**All of the superlinearity was the per-row term.** A volume story cannot produce that, and
without a denominator the two are indistinguishable — "each write got more expensive" and "there
are more writes" have different fixes.

---

## 3. What landed, each measured separately

Against a fixed 12-run tiny-profile baseline (`save_s`/arm 1.2776, sd 0.0339; the bar is ~5.3 %):

| | commit | save_s/arm | cumulative |
|---|---|---|---|
| build indexes at run end, not per insert | `c745de5a` | 1.0798 | 0.845x |
| hold one connection per arm, not one per flush | `e54489bb` | 1.0056 | 0.787x |
| drop three indexes nothing reads | `efc977e3` | 0.9454 | **0.740x** |

### 3.1 Deferral preserves comparability; deletion does not

This decided the landing order. A database indexed at close has the same final shape, the same
stamp and the same content, so the saving is entirely in flight and no consumer can tell — the
declared id stays `d9854632d1b0`, verified before the edit by building both statement orderings
in memory. Dropping an index moves it (`4e13ed321df9`) and needs a vintage. Keeping them as
separate commits keeps the vintage break separable from the in-flight win.

### 3.2 Deferral is opt-in, and the first attempt was wrong

Applying it inside `init_run_db` unconditionally broke 50 tests, and that failure was the real
obstacle rather than a test problem: **`Schema.identity.resolve(..., verify=True)` re-derives a
sim DB's shape and raises `SchemaDrift` against the stamp.** Those tests build a database by hand
and never reach a run end, so they were correctly observing an unfinished file. Only a writer
that owns the whole lifecycle may defer.

### 3.3 The build is timed and charged to `save`

Deferring work does not delete it. `build_run_indices` returns its seconds so the caller must
account for them (mean 0.237 s per arm at tiny scale), and every number above is **net** of the
build. A saving measured by moving cost into an unmeasured window is not a saving.

### 3.4 A 256 MiB page cache made it WORSE — and had measured 0.61x before

19.7 s against 17.5 s on its own; 16.6 s against 14.9 s alongside the held connection. Almost
all of its apparent benefit had been absorbing index scatter. With inserts touching only the
tail, a large cache is pure overhead.

**Re-earn a lever after changing what it was compensating for. Do not inherit its number.**
Inheriting this one would have landed a regression as an optimisation.

`synchronous=OFF` bought ~9 % and was **not** taken: durability on a file of record, for a small
gain, is a bad trade and the decision belongs to the owner rather than to the measurement.

### 3.5 The deletion test

`EXPLAIN QUERY PLAN` over every production read shape, against a real sim database — not by
reading SQL. Every reader of `bin_placement` / `bin_eviction` filters on `run_id` and/or
`batch_id` or scans ordered by `batch_id, seq`, and on a `WITHOUT ROWID` table the primary key
IS that index. Nothing filters, groups or orders `picks` by `sku` at all.

**A plan NAMING an index is not a query NEEDING one.** `ix_picks_run_sku` was named for an
aggregate that never mentions `sku`; the planner was picking the narrowest path to filter
`run_id`, and `ix_picks_run_batch` serves that identically — verified after the change, not
predicted. Every other index had a named consumer, and after the change every remaining one does.

---

## 4. The knee is withdrawn

`COMPLEXITY_ROUND_FINDINGS.md` records `save_s` local exponents `1.27, 1.06, 0.97, 2.30` — a knee
at the top rung. **It did not reproduce.** A fresh ladder on the same span, same catalogue, quiet
host reads `1.31, 1.33, 1.18, 1.34`: steady, no knee. This is the second top-rung knee on this
ladder to fail a repeat (`STRESS_TEST_FINDINGS.md:409` withdrew the first).

**Treat a top-rung jump here as machine variance until a second run shows it.** `_knee` is still
the right detector; it is the *reporting* that must wait for a repeat.

A RAM explanation was also tried and withdrawn: this host has 127.7 GiB and peak RSS across all
18 arms tops out at 20.7 GiB, so nothing stopped fitting.

---

## 5. The other three standing issues

### 5.1 `cmin` / `cmax`, k ≈ 1.25 — REAL, and it lives in `reord_s`

The cheap hypothesis was that the 1.29 might be `save_s` wearing cmin's name. **Refuted from the
existing artifact at zero cost**: `arm_growth_ex_save` reads k = 1.26–1.29 with local exponents
`[0.91, 1.35, 1.60, 1.48]`. It survives save removal entirely, and it survives this round's fix —
the four arms are still the only offenders after it, at k ≈ 1.25.

**Localised 2026-09-18.** Decomposing the cmin arms by section across the post-change ladder:
`reord_s` is **75.5 % of the arm at k = 1.42**, local `1.00, 1.49, 1.70, 1.79` — against 1.11 for
the ladder as a whole. `save_s` is 11.8 % at k = 0.94 and everything else is ≈1.0 and under 6 %.
So the growth is placement work in the reorder path, and it is not the I/O this round fixed.

`uses_aisle_index` is declared by **exactly these two arms**, and they are the only
affinity-scoring families with no pool, no SKU-run cache, no frozen `all_idx` and **no cold-start
shortcut at all** — they pay a full O(#aisles) lift scan even for a SKU with zero placed
partners, which is the case `cluster_map` short-circuits and whose share was measured rising
3.0 % → 14.4 % across a 16× ladder.

**A pool may not be the fix.** `cluster_map`'s cold short-circuit is byte-identical because an
empty intersection with the live union means no float addition happens at all — there is no
summation to reorder — and that argument needs a *live union*, which is a mirrored structure
rather than a cache. A mirrored structure has no validity window, so it does not need a scope
object. That is planned, not proven; measure the scan width first.

The older verdict, kept because its reasoning is still correct for a CACHE: a run cache here
needs a **pool** first, because `cmin`/`cmax`
are `place_one` with no wave, and a closure-scoped cache is the persistent dict that reintroduces
the one-ulp drift. This round sized it; it did not build it.

### 5.2 `cluster_map`'s warm path — quadratic in width, linear per arm

Measured with `Tests/calltree/scan_width.py`, running the ladder's own rungs through the ladder's
own `cluster_map` configuration:

| x | calls | mean live aisles | total width |
|---|---|---|---|
| 500 | 4,191 | 7.02 | 29,401 |
| 4,000 | 37,911 | 52.08 | 1,974,248 |
| 8,000 | 76,514 | 106.36 | 8,138,002 |

`calls` k = 1.02 × `mean |live|` k = 0.97 = **total width k = 1.99**, local `[2.38, 1.65, 2.04,
2.04]`. Genuinely quadratic, and invisible to the calltree, which records one frame entry however
wide the scan is.

The 37,911 calls at 4,000 SKUs matches the ladder's own recorded count exactly — the cross-check
that says this measured the right workload. A previous attempt at this kind of measurement drove
a plausible-looking neighbour and reported 676 calls where the ladder recorded 37,911.

**But the arm-level exponent is 1.01–1.04.** The quadratic term is real and is not yet the
dominant cost at these scales; `reord_s` and `extract_s` are far larger. Closed for now, with the
number, and it will need reopening at larger catalogues.

**Also measured: the cold short-circuit fires 0 % of the time on this configuration.** Ticket
05's benefit is not visible on this workload at all.

### 5.3 The analysis half of `Optimization/` — measured for the first time, and sublinear

Every deep rung already runs its analysis in the same subprocess, so the boundary is in the log
and no extra run was needed. Simulation ends at the last per-arm `[save] run-end close`.

| rung | total | simulation | analysis | analysis share |
|---|---|---|---|---|
| 10,000 | 421 s | 319 s | 102 s | 24.2 % |
| 20,000 | 627 s | 472 s | 155 s | 24.7 % |
| 40,000 | 1,138 s | 897 s | 241 s | 21.2 % |

**k = 0.62, local `[0.60, 0.64]` — sublinear.** It is a fifth to a quarter of every run and it is
not a growth risk. Closed.

Refitted on all five rungs of the post-change ladder: **k = 0.70**, local `0.56, 0.68, 0.92,
0.75`. Still clearly sublinear; the 0.62 above was three rungs.

### 5.3.1 `--granularity graph` does NOT cut it — measured, and the first answer was wrong

`analyze_run` already carries `--granularity {config,graph}`, and its own docstring says `config`
"emits one job per channel-run (a handful per cell, so a large `--workers` is mostly idle)" while
`graph` "is what actually saturates a big pool on a re-analysis". The job-count claim is true:
on the 10k rung, `config` emitted **4** jobs and `graph` **112**, a 28x difference.

**The wall claim is not.** Re-analysing that rung at `--workers 24`:

| order | config | graph | |
|---|---|---|---|
| config first | 116.5 s | 94.5 s | graph 0.81x — looks like a 19% win |
| **graph first** | **97.3 s** | **99.9 s** | **config 0.97x — the sign flips** |

**The 19% was the cold cache, not the granularity.** Whichever ran first paid for warming a run
tree the analysis half reads heavily, and reversing the order reverses the result. A single
ordered pair would have shipped a 19% saving that does not exist.

So more parallelism is not the lever here: 28x the jobs finishes no sooner, which says the
analysis half is not starved of workers at this scale. Whatever it is bound by, it is not job
granularity. **Do not re-propose this without a new measurement that controls for order.**

---

## 6. What is now the biggest thing in the deep tier

Not `save_s`. The post-change wall, split three ways off each rung's own log:

| rung | wall | sim model | analysis | startup + sched | per wave |
|---|---|---|---|---|---|
| 10k | 6.8 m | 1.9 m | 1.7 m | 3.2 m | **25 s** |
| 80k | 33.8 m | 16.3 m | 7.2 m | **10.2 m** | **81 s** |

1. **Startup + scheduling — ~30 % of the wall at 80k, and it GROWS.** 25 s → 81 s per wave,
   k = 0.56, local `0.21, 0.60, 0.75, 1.02` — accelerating toward linear.

   **The "~48 s FIXED per arm" figure recorded elsewhere is wrong at depth and is corrected
   here.** 48 s is the smallest rung's value. A term that scales with the catalogue is not
   interpreter spawn; it is per-arm catalogue *loading*, and that distinction decides the fix:
   worker recycling only helps if the catalogue survives the reuse.

   **Worker recycling may be revisited (user decision, 2026-09-18) — but the blocker is
   DETECTION, not resume.** The deadlock is a HANG: zero CPU, one live worker of eighteen, no
   exception, no exit. `_supervise` is a bounded retry driver that fires on *hard worker death*;
   a deadlocked pool never dies, so nothing rebuilds it. And the detection gap is wider than
   recycling — every worker can die and `run_simulation` still exits 0. Resume itself is the
   part that is already sound: a hard mid-flight kill resumes to 272/272 from `--resume DIR`
   alone. So the safeguard that unpins this is a **stall detector** — no unit completed in N
   minutes ⇒ tear the pool down and let the existing retry path resubmit from on-disk
   checkpoints — plus a proof that a resumed run is byte-identical to an uninterrupted one.

   **A second reason to measure before unpinning.** The pin's
   own docstring prices it at "~10 s of spawn per job against a job that runs for minutes" and
   notes that "workers reload their assets per job anyway". The split above sharpens that second
   clause into the decisive one: if the term scales with the catalogue, it is the reload — and a
   recycled worker performs the reload regardless, so recycling would buy the smaller half.
   `Tests/unit/test_worker_recycling_pin.py` now pins the decision so it cannot be undone by a
   refactor; a deadlock reproduction is deliberately NOT written, because it needs a real
   two-cell run and its failure mode is a hang. Anyone reopening this measures the spawn/load
   split first.

2. **The analysis half — 21–25 % of every run**, k = 0.70 on five rungs (an earlier k = 0.62 was
   fitted on three). Sublinear, so not a growth risk, but a large constant.

3. `reord_s` (k = 1.11 overall) is the largest per-arm section and is close to linear — **except
   on the `cmin`/`cmax` arms, where it is k = 1.42 and 75.5 % of the arm.** That is the tier's
   only remaining offender.

The ladder now prints this split itself (`save_tail` + the wall lines in `run_deep_ladder`), so
it is measured every run rather than reconstructed by hand. Before that the residual had no name,
and a residual nobody can name is a residual nobody fixes.

### What the deferred index build actually costs

Now that the two per-arm `[save]` lines are parsed, the build is visible: **4.33 s per arm at the
80k rung**, so ~589 s of that rung's 3,253 s `save_s`. It is charged to `save_s` and every figure
in §1 is net of it. At tiny scale it is 0.24 s per arm — the cost scales with the table, as a
single sorted build should.
