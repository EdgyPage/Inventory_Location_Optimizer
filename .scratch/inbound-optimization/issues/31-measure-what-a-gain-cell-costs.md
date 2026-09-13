# Measure what a gain cell actually costs

Type: task
Status: open

Graduated 2026-09-12 from [Re-size the funnel in site days](24-resize-the-funnel-in-site-days.md),
which could only size phase 2 to a FLOOR. AFK.

Not blocking phase 1. Must land before phase 2 is launched — or phase 2 is launched against a
number that is known to be wrong in one direction and unbounded in the other.

## Question

24 sized phase 2 at **37.8 h of unit-seconds and ~169 GiB** from the gate run
`comparison_20260912_134002`. That run priced nothing: `inbound_yard_policy` and
`inbound_dock_policy` were both `fifo`, and it was a SINGLE cell. Phase 2 is neither. Two costs
are therefore unmeasured, and one two-cell probe settles both.

**1. The gain evaluator has never run in a timed cell.** Eight of phase 2's ten cells
(`gmyopic`, `gforecast`, three `ggated_h*`, two `fsight_w*`, against `fifo` and `lifo` which do
not price) run a gain bundle that prices every trailer against the arm's own machinery at every
drain. `fsight_wall` is the one with no paper bound: `_window_rates` re-aggregates the whole
remaining script once per ENTRY CALL, i.e. once per DRAIN, so its cost is the recorded
O(n^2 . |batch|) of `_futuresight_window` multiplied by drains per batch
([Extend the gain bundles](20-extend-the-gain-bundles.md) carries the derivation and the legal
fix, which is memoizing `_window_rates` keyed on `demand_v` — 06's "legal-keyed-not-built" case,
not new cache machinery).

**2. The per-cell reshape is unmeasured.** On a multi-cell run `scenario.py` freezes the
inventory once per pair (3,633 s measured on the gate run: line-floor solve, fill derivation,
staffing, warehouse planning, batch precompute) and each of the ten cells then RESHAPES the
frozen inventory. Nobody has timed a reshape under the era. 24's bracket is +1.0 h (freeze only)
to +10.1 h (a reshape costing a full setup), and the true number decides whether the freeze is a
rounding error or a quarter of the campaign.

### What to do

Run a two-cell probe on the reference pair at `CAMPAIGN_DEPTH_DAYS`, on the rule pair the funnel
already has a baseline for (`fifo` x `fifo`) so no phase-1 output is needed:

- **cell A: `fsight_wall`** — the expensive pole, and the one with no bound.
- **cell B: `gmyopic`** — the cheap gain pole, so the probe brackets the eight priced cells
  rather than reporting one point.

Report, per cell: the per-coupled-unit wall against the gate run's 1,134 s mean, the peak RSS
against its 3.2–4.1 GiB, and the second cell's pre-worker stretch (the reshape). Then restate
phase 2's sizing on the campaign entry with the multiplier applied, and say whether
`fsight_wall` is affordable at 40 site days at all — dropping it is a legitimate answer, since
it is a declared-unlawful upper-bound reference and the H grid is where the campaign's real
question lives.

Two cautions. `runtime_metrics.db`'s `t_*` are MEAN seconds per batch per arm, never a share of
the wall (memory `runtime-metrics-is-the-deep-instrument`), so read the unit wall from the
worker timestamps and the sim DB mtimes as 24 did. And a probe cell is not a phase-2 cell: use a
throwaway spec, publish no number from it, and do not let it write into the campaign's tree.
