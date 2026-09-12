# Re-run the gate and fix the fee threshold

Type: task
Status: claimed
Blocked by: ../../department-calibration/issues/43-rerun-and-record-the-form.md

Graduated 2026-09-12 from
[Re-verify the gate under the lead-aware record](26-reverify-the-gate-under-the-lead-aware-record.md).
AFK once unblocked.

## Question

Re-run the coupled gate once department-calibration
[Close the fulfillment fill-law gap](../../department-calibration/issues/38-close-the-fulfillment-fill-law-gap.md)
lands, and fix `PHASE2_THRESHOLD_DAYS` on it.

`--spec inbound_pilot --n-batches 40` on the reference pair and nothing else -- the spec now
carries the coupling, so there is no flag to remember. Read exactly as 26 did:
`Diagnostics/equilibrium_report.py --window 20-39`, `Diagnostics/receiving_report.py`, and the
site yard tables under `<pair>/_site/inbound_*.db`.

**One reading is genuinely open and the other is not.** 26 already passed the yard criterion on
the coupled dock -- strict contention 20-40% of window drains, binding cuts 11-12 of 20, depth
falling, `recv_depth` max 0 -- and nothing in 38's fix is aimed at the dock. So this re-run is a
CONFIRMATION there and a verdict on the supply clause: fulfillment within +/-0.02 of its stamped
fill rate, store still in band, every other clause unchanged.

Expect the yard to get BUSIER, not quieter: closing the fill gap raises fulfillment's served
units ~8%, and ordered units -- hence trailers -- follow. That is why the threshold was not
fixed at 26.

**Fixing the threshold is a lookup, not a search.** The sweep is recorded in the comment block
on `PHASE2_THRESHOLD_DAYS` in `Optimization/config/whatif_config.py`; re-take it at the same
grid on the passing run and set the value at the knee (~1.3 d on 26's run: a quarter of trailers
accruing overage, the arms separating ~1.7x, neither pole saturated). 3.0 is degenerate under
one dock -- no trailer of 609 exceeded 1.837 d -- so the value MUST move; the only question is
where the passing regime puts the knee. Committing it also un-degenerates `gain_gated`'s H grid,
which is derived from the threshold, and retires the "fulfillment-calibrated compromise" caveat
the leaf model forced: one dock has one detention distribution.

In band -> phase 1 is launchable and
[Re-size the funnel in site days](24-resize-the-funnel-in-site-days.md) sizes it. Out of band ->
name the clause and the leaf, as 23 and 26 did.

## PROGRESS, 2026-09-12 -- session paused, read this before doing anything

**THE GATE RUN ALREADY EXISTS AND IT PASSES. Do not launch a new run.**
`comparison_20260912_134002` -- department-calibration's
[Re-take the reference run under v3](../../department-calibration/issues/46-retake-the-reference-run-under-v3.md)
-- IS this ticket's gate run. Verified on its `run_spec.json`, not inferred:

    argv = [run_simulation.py, --profiles-dir <...>\catalogue_reference_lt0,
            --spec inbound_pilot, --n-batches 40, --workers 2]
    spec = "inbound_pilot"   n_batches = 40   couple_channels = true   sampler = "v3"

That is this ticket's command exactly. This collapses the same way department-calibration's
[Re-run the reference pair and record the form](../../department-calibration/issues/43-rerun-and-record-the-form.md)
did: the run was already taken, so what is left is reads and one commit.

### Criterion (a) -- the supply clause: PASS, both leaves, every arm

`run.log` clean first (0 `Traceback`, 0 `produced no data`, 0 `Config stage: 0 job(s)`).

`Diagnostics/equilibrium_report.py comparison_20260912_134002 --window 20-39` --
**12 arm(s) judged, 0 FAILED, 0 instrument error(s)**. All eight leaf-arms PASS all five
clauses:

| clause | fulfillment (4 arms) | store (4 arms) |
|---|---|---|
| supply level vs expected 0.025, band +/-0.02 | 0.027 - 0.029 (**+0.002 to +0.004**) | 0.027 - 0.028 (+0.002 to +0.004) |
| labour cut share vs expected ~0.0246, band +/-0.032 | 0.0339 - 0.0362 (+0.0093 to +0.0113) | 0.0017 - 0.0034 (-0.0156 to -0.0175) |
| realized lead (stamped 1.766 d) | 1.647 - 1.686 d | 1.843 - 1.962 d |

The supply verdict this ticket was waiting on is **in band on both leaves**, at a fifth of the
tolerance. Labour is the closer clause on fulfillment, as 46 flagged -- still passing on every
arm, worth watching, not acting on.

The four site rows: `site_utilization=ok` on all four coupled pairs (put 0.812 - 0.857 vs 0.840;
recv 0.869 - 0.871 vs 0.842). Receiving utilization is LOWER than 26's 0.889 despite more
trailers, because the derived crew grew 22 -> 23 with the v3 line count.

`Diagnostics/receiving_report.py -v`: **8 arm(s), 0 FAILED** and **4 coupled pair(s) with a site
dock, 0 FAILED**; `depth_max=0` on every arm, so the dock still never stands a unit overnight.

### Criterion (b) -- the yard: the busier-yard prediction is CONFIRMED, reading not finished

`yard_trailers` holds **642 rows** on `uni_fifo_norsl` against 26's 609: **+5.4% trailers**,
which is the direction this ticket predicted. `yard_drains` has its 40 rows. The contention /
binding-cut / depth / detention table 26 built was NOT re-read before the pause.

### What is left, and the method notes to do it with

1. Finish the yard reading (26's table shape: strict contention, binding cuts, depth mean/max,
   free doors at freeze, detention p50/max, standing at end, trailers cleared).
2. Re-take the overage sweep at 26's grid (0.75 / 1.00 / 1.10 / 1.20 / 1.25 / 1.30 / 1.50 /
   1.75 / 2.00 d) over the window population, find the knee, and COMMIT
   `PHASE2_THRESHOLD_DAYS` in `Optimization/config/whatif_config.py` -- updating the comment
   block, which currently records 26's sweep and says to re-take it on the passing run.

Method notes, all verified this session:

- The four site DBs are `<pair>/_site/inbound_<arm>__<arm>.db`. `yard_trailers` is
  `(seq, arrived_s, staged_s, emptied_s, status)`; `yard_drains` is
  `(batch, yard_start, free_doors_start, yard_end, staged_remainder_end)`.
- **Reuse `Optimization/Performance_Evaluations/common/frames.py:_ydf`** rather than
  re-deriving: `detention_days = max(0, end - arrived) / units.SECONDS_PER_DAY` (86400,
  calendar seconds -- correct for a carrier's detention and the same converter 26 used), a
  censored row closed at `run_end_s` and kept, and the threshold entering ONLY there, which is
  what makes the sweep a re-report rather than a re-simulation.
- The **window population** is trailers arriving in days 20-39. The site DB's `shift_days` is
  EMPTY -- take the day boundaries from a leaf DB's `shift_days.end_s` (one absolute clock
  site-wide, so either leaf serves).
- This run recorded `inbound_fee_threshold_days = 2.0`, not the 3.0 on `PHASE2_THRESHOLD_DAYS`.
  Irrelevant to the sweep (raw stamps), but do not read the recorded value as the campaign's.
- `repo_commit = 6a424b5ef171`, `repo_dirty = true` on the gate run.
- The data drive is external and is NOT always attached; both the run tree and
  `PROFILE_INPUT_DIR` live on it.

**No run was launched and nothing is in flight.** 26's run (`comparison_20260912_055947`) is
still on disk beside this one for the before/after comparison.

## PROGRESS 2, 2026-09-12 -- the reading tool is built and tested

The drive was detached again before the yard reading and the sweep could be taken, so this
session built the tool that takes both:
[assets/sweep_fee_threshold.py](../assets/sweep_fee_threshold.py).

    python .scratch/inbound-optimization/assets/sweep_fee_threshold.py comparison_20260912_134002

One command, no arguments beyond the run: `--window 20-39` and 26's grid
(0.75 / 1.00 / 1.10 / 1.20 / 1.25 / 1.30 / 1.50 / 1.75 / 2.00 d) are the defaults, so the two
runs' tables are read side by side. It prints BOTH remaining sections -- the yard reading in
26's shape (strict contention, binding cuts, depth, free doors at freeze, detention p50/max,
standing/done, `recv_depth` max) and the overage sweep over the window population.

**It reuses the production derivations rather than restating them.** `frames._ydf` and
`frames._ddf` are the same functions the yard renderers call, so `detention_days`,
`overage_days` and `binding_cut` cannot drift from what the campaign will report. Three
decisions ride along with that reuse and would each have been easy to get wrong by hand:

- a right-CENSORED trailer is kept at its lower bound, not dropped -- dropping it removes
  exactly the longest-held trailers, which is the population the fee is about;
- the censoring bound is the SITE's, `max(batch_start_time + duration)` over BOTH leaves, the
  way `requests._arm_end_s` closes it at site scope;
- the per-drain levels are never summed -- `binding_cut` is a boolean per drain and its
  statistic is a COUNT of drains (the `recv_cut` scar).

**Tested end to end on a synthetic run tree**, built through the repo's own `init_run_db` so
the derived `sim_schema_id` is HEAD's and `dataset.bind` serves it. Two arms, four days,
window 2-3, a hand-computed expectation for every cell -- population membership, detention
p50/max, contention, binding cuts, depth, doors, `recv_depth`, and the overage share and total
at five thresholds. Every number matched, including the two cases most likely to be wrong: the
censored trailer reads 1.00 d against the site bound rather than vanishing, and a detention
exactly EQUAL to the threshold is not counted as over (`over_threshold` is `overage_days > 0`).

Two details worth keeping:

- **The reads are `immutable=True`**, through `Schema.connect.read_only`, for the reason
  `_query_rows` uses it: a plain `mode=ro` open creates `-wal`/`-shm` beside an archived DB and
  cannot remove them (memory `wal-sidecars-come-from-readers`), and the preflight canaries read
  that litter as an undeclared tree path. Confirmed on the fixture: no sidecars after a full run.
- **Every printed line is ASCII.** A box-drawing character in an output line kills the run
  half-way through the table on a cp1252 console (memory `windows-console-is-cp1252`).

**Still to do, and it is now one command plus one edit:** run the tool on
`comparison_20260912_134002`, read the knee off the table, and commit `PHASE2_THRESHOLD_DAYS`
in `Optimization/config/whatif_config.py` -- updating the comment block, which currently
records 26's sweep and instructs the reader to re-take it on the passing run. Then the yard
reading goes in the answer beside the supply verdict already recorded above.
