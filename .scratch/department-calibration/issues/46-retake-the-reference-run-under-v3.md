# Re-take the reference run under v3 and re-establish the gap

Type: task
Status: resolved

Graduated 2026-09-12 from
[Re-measure the fill-law targets under v3](45-remeasure-the-fill-targets-under-v3.md), by user
decision to hoist the run in front of the form work. AFK. Execution override is ON.

## Question

**Does a fill-law gap survive the sampler fix at all?**

Everything 38, 39 and the 40-43 chain were built to explain was measured on
`comparison_20260912_055947`, a **v2** run. 45 has since shown that v2's defect manufactured the
evidence: 335 fulfillment SKUs were drawn on at least half of the 20 measured days (one on 17 of
20) because they sat on Fenwick boundaries with `p_s` ~0.75-0.80, and under v3 no SKU reaches
half and the maximum is 5. On the generator side every symptom is gone -- the touched-SKU
shortfall (-25.2% -> +3.3%), the lag-1 suppression (0.838x -> 1.014x), and the repeat statistic
itself (3.74x the record's Poisson -> 0.87x).

**The realized missed share is the one number 45 could not re-measure**, because it takes a run:
store 0.0302 and fulfillment 0.1044 are v2 outcomes. And the same artifact SKUs, drawn 15-17 days
out of 20 against levels sized for ~1.5 lines per SKU, would have been missing almost constantly
-- so the defect plausibly inflated the realized miss as well. That is a PREDICTION and this
ticket measures it.

### What to run

One era run on the reference `lt0` pair, the same shape as `comparison_20260912_055947` (40
coupled days, days 20-39 measured), under the declared v3 sampler. Note that the run is not
merely a re-take: **the geometry moves**. v3 delivers 9.5% more fulfillment lines and 1.0% more
store lines, the coverage fixed point reads `n` off the sampler's unit, so the levels, the solved
line floor and the derived picking crew all move with it. Report what the solve asked for --
floors per leaf against 1.2668 / 1.4994 and the aisle count against 2,774 -- as a measurement,
not a surprise.

### What the answer must state

1. **The realized first-pass fill per leaf**, read against the equilibrium instrument's `supply`
   band, and the realized missed share against the stamped, on both leaves. This is the verdict:
   in band means there is no gap left for the 40-43 chain to close.
2. **The realized order-to-shelf lead** under v3, re-measured the way 39 measured it
   (`TrailerTransit.lead_for` reconstructed from `yard_trailers.seq`, never inferred from a
   level). The DRAWN law is untouched by the sampler; the realized leg is not.
3. **A verdict on the chain.** If the gap is closed, say so and rule
   [Characterise the draw probability](40-characterise-the-draw-probability.md),
   [Gate the form on the generator](41-gate-the-form-on-the-generator.md) and
   [Land the draw probability through every closed form](42-land-the-draw-probability.md) out of
   scope or re-scope them; if a gap survives, give it its v3 SHAPE (which leaf, how big, which
   direction per channel) so 41 can be aimed at a live target. 45 found the record under-prices
   the store by 19% and over-prices fulfillment by 16% -- opposite directions -- so a surviving
   gap is not the single-multiplier shape 39 fitted.
4. **Whether [Re-run the reference pair and record the form](43-rerun-and-record-the-form.md)
   still needs its own run**, or collapses into this one plus its paper trail. Do not re-run an
   era for a second time without saying why.

Done when the run is clean, the fill and lead are read, the geometry move is reported, and the
verdict on 40/41/42/43 is stated plainly.

**Method warnings:**
- Check `run.log` for `Traceback`, `produced no data` and `Config stage: 0 job(s)` before
  trusting the run -- every worker can die and `run_simulation` still exits 0 (memory
  `pool-run-swallows-dead-arms`).
- Launch the driver DETACHED, not through Bash background mode, which dies at the 10-minute cap
  and orphans `run_simulation` (memory `launch-long-drivers-detached`).
- The whole-run entry point is `python -m Optimization.analyze_run <run_root>`; `run_analysis.py`
  takes a CELL directory and exits 0 doing nothing when handed a run root (CLAUDE.md 3).
- Denominate crew shares on distinct `work_day` values, never on calendar span (memory
  `calendar-span-is-not-work-days`).
- The v2 run's batch caches and its two `_drawp_*.npz` artifacts are v2 artifacts; a v3 run
  fingerprints its own apart and can never be served them (memory `v3-sampler-era`).
- A line is a distinct `(batch_id, sku)`, never a row of `picks`.

## Answer

Resolved 2026-09-12 on `comparison_20260912_134002` (v3, 40 coupled days, days 20-39, four arms,
clean exit; zero `Traceback` / `produced no data` / `Config stage: 0 job(s)` in `run.log`).

**THE GAP IS CLOSED. 12 arms judged, 0 FAILED**, against v2's 4. The defect was 95.5-97.1% of it.

### 1. The realized first-pass fill -- the verdict

`Diagnostics/equilibrium_report.py <run> --window 20-39`, supply clause:

| fulfillment | v2 | v3 | expected | tol | defect's share of the gap |
|---|---|---|---|---|---|
| `uni_fifo_norsl` | 0.10445 **FAIL** | **0.02842 PASS** | 0.02512 | 0.020 | 95.8% |
| `uni_tmin_norsl` | 0.10245 **FAIL** | **0.02860 PASS** | | | 95.5% |
| `opt_fifo_norsl` | 0.10445 **FAIL** | **0.02842 PASS** | | | 95.8% |
| `opt_tmin_norsl` | 0.10344 **FAIL** | **0.02738 PASS** | | | 97.1% |

| store | v2 | v3 | expected | tol | defect's share |
|---|---|---|---|---|---|
| `uni_fifo` / `uni_tmin` / `opt_fifo` / `opt_tmin` | 0.0302 / 0.0300 / 0.0302 / 0.0300 (all PASS) | **0.0271 / 0.0272 / 0.0271 / 0.0285** | 0.02481 | 0.020 | 58% / 55% / 58% / 30% |

The fulfillment gap was **+0.0793**; it is now **+0.0033**, a quarter of the tolerance. The store,
which always passed, tightened from +0.0054 to +0.0023.

**The labour clause moved the other way and is now the closer one.** Fulfillment labour rose
0.0081-0.0099 -> 0.0339-0.0362 against an expected 0.0246 at tol 0.032 -- still passing with
delta <= 0.0093, and expected: v3 delivers 9.5% more fulfillment lines, so more work arrives at
the same crew. Worth watching, not acting on.

### 2. The realized order-to-shelf lead

Re-measured exactly as 39 did (`TrailerTransit.lead_for` reconstructed from `yard_trailers.seq`,
never inferred from a level):

| | stamped | drawn | realized | detention |
|---|---|---|---|---|
| v2 | 1.7656 (E[K^2] 4.2310) | 1.6477 | 1.803 - 1.829 | 1.127 - 1.149 d |
| **v3** | 1.7656 (unchanged) | **1.6742** | **1.7799 - 1.8355** | **1.084 - 1.131 d** |

309-310 trailers against v2's 298-300 -- more lines, more reorders, more trailers, exactly as
expected. The DRAWN law is untouched by any sampler change, as 45 predicted; the realized leg
moved slightly. Re-pricing `coverage.fill_rate` at the realized pmf moves fulfillment **+0.0002**
(opt_tmin +0.0008). In absolute terms the lead is worth what it always was; as a share of a gap
that shrank 24x it is now a material fraction of the residual, which is a statement about the
residual's size, not about the lead.

### 3. The geometry did NOT move -- this ticket's own prediction was wrong

This ticket said "the geometry moves ... v3 delivers 9.5% more fulfillment lines, the coverage
fixed point reads `n` off the sampler's unit, so the levels, the solved line floor and the derived
picking crew all move with it." **It does not.** `n` is DECLARED -- `mean_fraction` x section size,
a config value -- and the log says so outright: `the fixed point is the declaration`. Every
derived quantity came back bit-identical to the reference:

- store 588.7 and fulfillment 2,903.2 lines/day; line floors **1.3078** / **1.4994**
- sum Q **3,086,462** / **2,595,593**; **2774 aisles / 2,505,050 bins**, expected_fill 89.3%
- picking crew **K=23**, `s_pick` 17.657 (ff) / 105.130 (store) s/unit
- bins filled 1,020,434/1,213,550 (84.1%) store, 1,215,708/1,291,500 (94.1%) fulfillment

That makes this a perfectly controlled experiment: same warehouse, same levels, same picking
crew, only the batch CONTENT differs. It is a better comparison than the ticket expected.

**Two crews DID move**, and the distinction is the useful part:

| | v2 | v3 |
|---|---|---|
| put crew | 60 | **64** (load 1,448,387 -> 1,547,546 s/day, +6.8%) |
| receiving crew | 22 | **23** (15,645.5 -> 17,105.1 packs/day, +9.3%) |

**A crew that sizes on LINES or PACKS moves with the sampler; a crew that sizes on declared UNITS
does not.** Picking is denominated in demanded units (declared, unchanged); put-away and receiving
are denominated in the replenishment each delivered line triggers, and v3 delivers the 9.5% more
lines that v2 was swallowing. The +9.3% in packs/day tracks the +9.46% in delivered lines almost
exactly.

### 4. The verdict on the chain (user decision, 2026-09-12)

**[Characterise the draw probability](40-characterise-the-draw-probability.md),
[Gate the form on the generator](41-gate-the-form-on-the-generator.md) and
[Land the draw probability through every closed form](42-land-the-draw-probability.md) are RULED
OUT OF SCOPE.** The draw probability had exactly one consumer -- correcting a fill law that was
under-predicting the realized miss by 4x -- and that law now reads in band on both leaves with no
correction at all. Deriving `p_s` would be a measurement with nothing waiting on it, and buying a
comparability break to land it would be worse. The map's Out-of-scope section carries the gist;
40's built module (`Optimization/simdriver/draw_probability.py` and its 13 green tests) stays in
the tree, unused, as the sampler effort's starting point.

The record still misprices its own prior-line event ON THE GENERATOR -- over-prices fulfillment by
16% (0.04140 vs 0.03485), under-prices the store by 19% (0.00607 vs 0.00721), in opposite
directions. That is recorded rather than corrected: the instrument accepts the realized result on
both leaves, and a two-sided ~18% error on an intermediate quantity does not earn three tickets
and a seventh era's worth of churn.

**[Re-run the reference pair and record the form](43-rerun-and-record-the-form.md) is RE-SCOPED**
from "confirm and record the form" to "record the v3 era and close the calibration", and **needs
no run of its own** -- this ticket's run IS its confirming gate, and it passed on the first read.
What survives of 43: the comparability-break record, the ADR-0004 amendment, and the `CONTEXT.md`
glossary distinction between **line share** (a WEIGHT) and **draw probability** (an OUTCOME),
which is now more worth writing, not less, since a whole effort chased the difference. ADR-0006
loses its subject; what it should record instead is that the gap was an artifact.

### 5. An operational finding that cost an hour

**Modern Standby killed the first run.** It died at the analysis stage with
`STATUS_IN_PAGE_ERROR` (0xC0000006) after Kernel-Power **506/507** transitions from 14:59:15
onward -- Application Error 1005, "Windows cannot access the file ... the disk that the file is
stored on". The simulation work survived intact (all 8 DBs `PRAGMA quick_check` ok, 646k
fulfillment and 86k store picks per arm) and `--resume` recovered it in five minutes, re-declaring
from the run's own record rather than re-deriving.

Three launches before that died within a second, each with a literal `^C` and no EXITCODE line:
`Start-Process -RedirectStandardOutput`, a WMI-created cmd wrapper, and a cmd-wrapped scheduled
task. **A long driver must have NO CONSOLE** -- a scheduled task whose action is `python.exe`
directly. Memory `launch-long-drivers-detached` corrected; its Start-Process recipe was dead.
