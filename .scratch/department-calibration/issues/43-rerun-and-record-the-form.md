# Re-run the reference pair and record the form

Type: task
Status: open
Blocked by: 46

Graduated 2026-09-12 from
[Close the fulfillment fill-law gap](38-close-the-fulfillment-fill-law-gap.md), decisions 9, 11
and 12. The confirming gate and the paper trail. **The inbound campaign unblocks HERE, not at 38**
-- [Re-run the gate and fix the fee threshold](../../inbound-optimization/issues/29-rerun-the-gate-and-fix-the-threshold.md)
is blocked on this ticket, because a decision is not a corrected era.


## RE-SCOPED 2026-09-12 -- read this before the body above

User decision while resolving
[Re-take the reference run under v3 and re-establish the gap](46-retake-the-reference-run-under-v3.md).

**There is no form to record, and this ticket needs NO RUN OF ITS OWN.** 46's run
(`comparison_20260912_134002`) is the confirming gate the body below asks for, and it passed
on the first read: 12 arms judged, 0 failed, fulfillment supply 0.0284 against an expected
0.0251 at tol 0.020, the store 0.0271 against 0.0248. Do not take a second era run.

The body's premises that are now DEAD: the primary gate (41) is out of scope; there is no
corrected form and no fitted multiplier to reject; the geometry did not move, so there is no
floor/aisle change to report (floors stayed 1.3078 / 1.4994, the warehouse 2774 aisles /
2,505,050 bins). ADR-0006 loses its subject.

**What this ticket is now:** record the v3 era and close the calibration.

1. **The comparability break** -- the v3 sampler flip, already carried by memory
   `v3-sampler-era`; make sure the break is named in the same shape as the fourth and fifth.
   Note that put crew 60 -> 64 and receiving 22 -> 23 moved with it while picking did not.
2. **ADR-0006, re-aimed**: record that the fulfillment fill-law gap was an ARTIFACT of the v2
   sampler's duplicate draws (95.5-97.1% of it), with the fitted multiplier `m` = 1.739 /
   3.968 and its 52% / 71% as the rejected alternative -- rejected now because the thing it
   fitted was not real, which is a sharper lesson than the one 38 intended.
3. **The ADR-0004 amendment**, whose first-time confidence is priced through this law.
4. **The `CONTEXT.md` glossary**: **line share** (`freq / sum freq`, a WEIGHT) against **draw
   probability** (`p_s`, an OUTCOME). Worth MORE now, not less: a whole chain of tickets
   chased the difference between them.
5. **Sync the memory mirror.**

Done when the break, the ADRs and the glossary are written and the mirror is synced. The
method warnings below about launching and checking a run no longer apply -- there is no run.

## Question

Confirm the form on a run, then record it so nobody re-derives it in a year.

**The confirming gate.** One era run on the reference `lt0` pair, same shape as
`comparison_20260912_055947` (40 coupled days, days 20-39 measured). The realized first-pass fill
must read in the equilibrium instrument's `supply` band on BOTH leaves, and the store must not
regress -- the store control has caught a wrong answer twice. This is the CONFIRMING gate;
[Gate the form on the generator](41-gate-the-form-on-the-generator.md) was the primary one and has
already passed by the time this ticket runs.

**Record the geometry move.** 38 decision 11 accepted whatever warehouse the solve asks for, so
report what it asked for: the new floors per leaf against 1.2668 / 1.4994, the aisle count against
2,774, and the run-cost consequence. The expectation carried in from 38 is a fulfillment floor
nearer 2-2.5 lines than 4x -- state the realized number against that expectation plainly, in
either direction.

**This is the SIXTH comparability break.** Absolute travel, pick, put and labour numbers do not
cross it. Name it as such alongside the fifth
([Lead-aware record](36-declare-the-coverage-against-the-inbound-lead.md), 2026-09-10) and the
fourth (derived fill), and write the memory, in the same shape the existing break memories use.

**The paper trail (38 decision 12):**
- **ADR-0006**, recording the form and carrying the FITTED MULTIPLIER as the rejected
  alternative, with 39's numbers (`m` = 1.739 store / 3.968 fulfillment, closing 52% / 71%) so the
  rejection is legible rather than merely asserted.
- **An amendment to ADR-0004**, whose first-time confidence is priced through exactly the law
  this replaced.
- **`CONTEXT.md` glossary**: **line share** (`freq / sum freq`, a WEIGHT) against **draw
  probability** (`p_s`, the probability the sampler includes the SKU in a batch, an OUTCOME).
  These must be distinguishable by name or the distinction rots.

Done when the run reads in band on both leaves, the geometry move and the break are recorded, the
ADRs and the glossary are written, and the memory mirror is synced.

**Method warnings:**
- Check `run.log` for `Traceback`, `produced no data` and `Config stage: 0 job(s)` before trusting
  the run -- every worker can die and `run_simulation` still exits 0 (memory
  `pool-run-swallows-dead-arms`).
- Launch the driver DETACHED, not through Bash background mode, which dies at the 10-minute cap
  and orphans `run_simulation` (memory `launch-long-drivers-detached`).
- The whole-run entry point is `python -m Optimization.analyze_run <run_root>`; `run_analysis.py`
  takes a CELL directory and exits 0 doing nothing when handed a run root (CLAUDE.md 3).
- Denominate crew shares on distinct `work_day` values, never on calendar span (memory
  `calendar-span-is-not-work-days`).
