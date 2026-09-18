---
name: inbound-optimization-map-closed
description: "The inbound-optimization wayfinder map CLOSED 2026-09-13 at 'the campaign can run' — all 35 tickets resolved, committed on develop. PHASE2_PAIRS and PHASE2_STAFFING_PIN are now COMMITTED VALUES, not None, so any context expecting the placeholder is stale. Phase 2's LAUNCH and PUBLISH were deliberately ruled outside the destination and are the successor effort's first act. Carries the launch sizing (31's clock, not 24's), the three caveats the campaign must publish WITH, and the rider-outside-k trap that makes a naive derived-column equality check fail on a CORRECT derivation."
metadata: 
  node_type: memory
  type: project
  originSessionId: b3572a52-9f1e-4106-a04c-5f550cce8192
  modified: 2026-09-13T22:24:04.063Z
---

The map at `.scratch/inbound-optimization/map.md` reached its destination on 2026-09-13 and is
closed. All 35 tickets resolved; the last two were
[Extend the gain bundles](.scratch/inbound-optimization/issues/20-extend-the-gain-bundles.md)
(commit `6de12cae`) and
[Copy the chosen rule pairs into phase 2](.scratch/inbound-optimization/issues/35-copy-the-chosen-pairs-into-phase-two.md)
(commit `9f97489f`); the closure itself is `0a7c6694`.

**The scope call, because the map contradicted itself and someone will hit it again.** The
Destination line read "…decided and ready, so the campaign *can run*", while a fog narrative had
drifted to "the destination is reached when phase 2 *has run and been published*". Resolved
2026-09-13 in the Destination's favour: the spec builds with no edit and no decision in front of
it, so "can run" is met. Running and publishing decide nothing, cost ~9 h and ~164 GiB, and
scoping them in would have redrawn the destination rather than walked to it. They are the
successor effort's first act, parked in the map's Out-of-scope section — **not** an unfinished
ticket.

## What changed in the code, and what is now stale

`Optimization/config/whatif_config.py`'s `PHASE2_PAIRS` and `PHASE2_STAFFING_PIN` are **committed
values now**, read off phase 1's `restock_selection.json` (run `comparison_20260913_113512` under
`COMPARISON_OUTPUT_DIR`, repo_commit `ba055dd0747f`, cell `k1_off`, sampler v3, 40 batches). Any
memory, docstring or test that expects them to be `None` predates this and is stale —
`get_spec('inbound_policies')` now BUILDS rather than refusing, and that build *is* the check that
all six pairs name only families in `Inbound.gain.FAITHFUL_GAIN_FAMILIES`.

**The trap: the derived column is `chosen` + the rider, never `chosen`.** `channel_restocks_for`
derives each channel's arm set from the pairs, and the `('fifo','fifo')` rider rides OUTSIDE k —
so a check written as `derived == artifact['channels'][ch]['chosen']` fails on a *correct*
derivation. Store comes out `(rank_cartlabor, rank_minlabor, rank_labor, tmin, rank_random, fifo)`,
fulfillment `(rank_minlabor, tmin, rank_labor, rank_cartlabor, rank_popularity, fifo)`. Both are
tuples, not sets: the rank order is what the `_prepare_site_run` zip reads, and losing it silently
re-pairs the campaign. See [[fifo-restock-ignores-initial-placement]] for why `fifo` is the
order-blind control rather than an arm anyone expects to win.

## The successor effort's first act, with its numbers

Launch phase 2, then publish. **120 coupled units** — 6 rule pairs x 2 stock_modes x 10 cells —
30.8–35.3 h of unit-seconds, ~164 GiB, **~8.6–9.7 h wall at 4 workers**. That sizing is ticket
31's clock; **do not mix it with ticket 24's**, which predates the site-days re-size, nor with the
much older 136/480-unit figures, which are stale twice over. Launch detached per
[[launch-long-drivers-detached]] (no console, plus a keep-awake) and check `run.log` per
[[pool-run-swallows-dead-arms]] before trusting the output.

**Three caveats the campaign must publish WITH rather than discover:**

1. It is mostly a **fulfillment** result — store binds 14–17 of 75 drains against fulfillment's
   46–60.
2. `gain_gated`'s H grid is **fulfillment-only**: one global fee threshold cannot serve channels
   whose non-saturated bands sit 3.5x apart.
3. The rule pairing is **one of several equally defensible draws**, not a derived optimum — store's
   top three separate by 0.005% and 0.12%, fulfillment's top eight span 0.73%, and the pairing is
   rank-aligned. The signal is the 6.1% / 8.0% gap down to the `fifo` control plus the WITHIN-PAIR
   inbound comparison across cells, neither of which turns on which near-tie took which rank.

Two other remnants sit in the same Out-of-scope section: the **resume-guard extension to yard
state** (a refusal, not a checkpoint format — a v1 trailer run with a trailer type and no receiving
crew has worker-local trailers in no checkpoint and is not refused under
`--resume-granularity batch`), and the **timed / deeper lookahead views** parked by ticket 03.

Predecessor: [[inbound-pipeline-wayfinder-decisions]] (the inbound-groundwork map, closed
2026-08-27, whose Out-of-scope list this map started from — the same hand-off shape).

**SIZING CORRECTION 2026-09-18.** The "~8.6–9.7 h wall at 4 workers" above is ticket 31's
clock and was RESTATED by `.scratch/inbound-performance/issues/16-phase-2-restated-13-hours-not-8-6.md`
on 2026-09-14: **~13 h at 4 workers, ~9 h at 6**, arm-slot weighted because eight of the twelve
arm-slots are pool adapters at 3.24x (see [[inbound-pool-adapter-multiplier-is-not-13x]]). The
164 GiB stands; pricing costs time, not memory, so the worker count is an unpriced lever and
the pool fans out at most 12 units per cell. The launch itself is recorded in
`.scratch/phase-2-campaign/map.md`.
