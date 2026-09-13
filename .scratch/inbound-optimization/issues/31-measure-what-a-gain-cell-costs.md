# Measure what a gain cell actually costs

Type: task
Status: resolved

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

## Answer

RESOLVED 2026-09-12. **The probe could not price the cell it was sent to price: `fsight_wall`
cannot run AT ALL under the coupled model, and neither can `fsight_w5` -- 2 of phase 2's 10
declared cells are dead, not merely expensive.** With them out, both unmeasured costs are now
measured and both land at the CHEAP end of 24's brackets: a gain cell costs **1.6-1.9x** an
unpriced one, a per-cell reshape is **221 s** (not the hour 24 bracketed as its ceiling), and the
freeze is **975 s**. Phase 2, as 8 runnable cells, is **24-27 h of unit-seconds and ~132 GiB**,
about 7 h wall at 4 workers.

### 1. The blocker, which is the finding that matters

`Inbound/site_space.py:98-113` REFUSES to compose two futuresight windows -- deliberately, with
the lift rule written down beside it ("zip the two tuples BY BATCH INDEX ... then union each pair
of `{sku: qty}` dicts, which are disjoint because no SKU belongs to both leaves"), and with a
unit test pinning the refusal
(`Tests/unit/test_site_space_view.py::test_a_futuresight_window_is_refused_rather_than_zipped`).

Every phase-2 cell couples (`PHASE2_RUN_DEFAULTS`), so every `futuresight` cell composes two
leaves, so every one of them dies at its first drain. Measured, not reasoned: the first probe
launch ran `fsight_wall` and all four of its coupled units failed identically with
`leaf/leaves ['store', 'fulfillment'] carry a futuresight window` (log kept beside this ticket's
assets). And the refusal keys on the window's PRESENCE, not its width -- a direct call to
`compose_site_view` with a w=5 window and a w='all' window refuses both, while the same w=5
window composes fine with one leaf and two windowless leaves compose fine -- so it is
`fsight_w5` as well as `fsight_wall`.

**Nothing was wrong in `site_space.py`; the gap is that nothing checks the campaign's declared
axis against the coupled model's refusals.** The refusal is honest, local, tested and written on
purpose; `phase2_inbound_axis()` is honest too. No test and no spec-time check joins them -- so
an axis naming a policy the coupled composer declines has been sitting in the registry since
site-dock closed, and the first thing that would have noticed is a phase-2 launch burning its
freeze and then failing 24 units.

Successors: [Decide the futuresight family's place in the campaign](32-decide-the-futuresight-familys-place.md)
(build the zip, or drop the family) and, whichever way that goes,
[Gate the campaign axis on what a coupled run can do](33-gate-the-campaign-axis-on-the-coupled-model.md).

### 2. What was run

`comparison_whatif_20260912_221448`, throwaway spec `_probe_gaincost` (added to the registry for
the run and REVERTED with this resolution; it is not on `develop`), `PHASE2_RUN_DEFAULTS`
verbatim, `CAMPAIGN_DEPTH_DAYS` (40 site days), the one-pair reference catalogue,
`--workers 4 --no-analyze`. 12 coupled units, 24 leaves, 0 failures, nothing published.

THREE cells, not two, and the third is the reason every number below is trustworthy:

* **`fifo` -- an IN-RUN control.** The gate priced nothing, so 24 had to compare two runs' walls.
  A control cell inside the probe makes the multiplier a within-run RATIO: same machine, same
  hour, same worker count, same frozen warehouse. Section 5 is why that mattered more than it
  looked like it would.
* **`gmyopic`** -- the cheap gain pole.
* **`gforecast`** -- the most expensive RUNNABLE gain pole, replacing `fsight_wall`. The three
  `ggated_h*` cells sit INSIDE this band by construction: gated is gforecast plus a FIFO forced
  prefix, which prices FEWER trailers, so these two poles bracket all five runnable priced cells.

Arms `('fifo', 'tmin')` -- the gate's own pair, so every unit is arm-for-arm comparable with it,
and the two cheapest gain ADAPTERS are both exercised (`fifo` is priced by the closed-form
expectation of its own draw, `tmin` by the k-cheapest merge).

### 3. What a gain cell costs

Per coupled unit, seconds, 40 site days, both leaves:

| unit | `fifo` (control) | `gmyopic` | x | `gforecast` | x |
|---|---|---|---|---|---|
| opt_fifo_norsl | 561 | 1,002 | 1.79 | 1,038 | 1.85 |
| uni_fifo_norsl | 575 | 1,018 | 1.77 | 1,049 | 1.82 |
| opt_tmin_norsl | 723 | 1,049 | 1.45 | 1,296 | 1.79 |
| uni_tmin_norsl | 709 | 1,107 | 1.56 | 1,563 | 2.20 |
| **cell mean** | **642** | **1,044** | **1.63** | **1,236** | **1.93** |

**The multiplier is 1.6-1.9x and it is ARM-DEPENDENT, so size on the upper end.** On the `fifo`
arms the gain bundle adds a near-constant 441-477 s whichever policy prices; on `tmin` it ranges
326-854 s. The restock rule also moves the unpriced baseline by itself (+26%: tmin 709-723 s
against fifo 561-575 s), which is why the ratios are taken per arm and not off the cell means.

**Pricing costs TIME, NOT MEMORY.** Peak RSS is identical to three digits across all three cells
-- 3,762 MiB on both `fifo` arms and 4,470/4,502 MiB on the `tmin` arms, in every cell. The ARM
sets the RAM; the inbound policy does not touch it. So the campaign stays RAM-bound at ~4.5 GiB
per worker exactly as 24 budgeted, and the gain cells buy no extra risk there.

Disk is 1.37 GiB per coupled unit -- 24's ~1.4 GiB stands unchanged.

### 4. The reshape and the freeze: both at the BOTTOM of 24's bracket

24 bracketed the per-cell reshape at +1.0 h (freeze only) to +10.1 h (a reshape costing a full
setup) over ten cells, and said the true number decides whether the freeze is a rounding error
or a quarter of the campaign. **It is the rounding error.**

* **Reshape: 223 / 222 / 219 s** -- three samples, ~1% spread, so it is a constant.
  Decomposed: staffing ~100 s (put crew 44 s + the fulfillment block 42 s + the store block
  16 s), batch precompute 37-40 s, the per-bucket fill derivation 21-23 s, the sigma solve 13 s.
* **Freeze: 975 s** once per pair.
* **Fixed cost for the campaign: 0.76 h at 8 cells, 0.88 h at 10** -- against 24's 1.0-10.1 h.

One thing seen on the way past and deliberately NOT ticketed: each cell re-precomputes the batch
script at a fingerprint IDENTICAL to its siblings' (`d2c6a1ad` in all three cells), and it lives
per cell rather than beside the frozen inventory. Sharing it would save ~37 s per cell -- about
6 minutes over a ten-cell campaign. It is not worth a build, and recording that here is how the
next reader stops before "optimizing" it.

### 5. THE GATE RUN'S ABSOLUTES ARE NOT REPRODUCIBLE, AND THAT IS WHY THE CONTROL WAS IN-RUN

The probe's freeze does bit-for-bit the same work as the gate's setup -- identical line floors
(1.3078 store / 1.4994 fulfillment), identical units fielded (3,086,462 / 2,595,593), identical
fragmentation (+68,929 / +51,020) -- and takes **966 s against the gate's 3,261 s**, uniformly
~3.4x faster at EVERY stage (store floor 689->200 s, fulfillment floor 569->158 s, fill
104->26 s, store coverage 793->279 s, fulfillment coverage 797->240 s). The unpriced units run
~1.6x faster (uni_fifo 964->575 s, uni_tmin 1,077->709 s).

`--workers` does not explain it: the freeze has no worker pool at all, it is serial Python and
numpy. The derivation is identical and only the clock differs, so whatever the cause -- host
contention, the drive the catalogue sat on, the working copy -- **no absolute wall taken from
`comparison_20260912_134002` can be used to size the campaign, including 24's 1,134 s per
coupled unit and its 3,633 s setup.** 24's 37.8 h figure is not a floor that this ticket
multiplies; it is a number from a different clock. The two must not be mixed, and the only
machine-independent quantity this ticket produces is the RATIO in section 3.

### 6. Phase 2 restated, on the probe's own clock

12 units per cell (6 rule pairs x 2 stock modes x 1 pair). Priced cells costed at `gforecast`
(the upper pole, conservative, since `ggated_h*` price fewer trailers). `inb_off` has no yard at
all, so costing it at the control is conservative too.

| shape | cells | units | unit-seconds | serial | wall @4w | wall @6w | disk |
|---|---|---|---|---|---|---|---|
| **8 runnable (futuresight dropped)** | 3 unpriced + 5 priced | 96 | **23.8-27.0 h** | 0.76 h | **6.7-7.5 h** | 4.7-5.3 h | **~132 GiB** |
| 10 (the zip built) | 3 unpriced + 7 priced | 120 | 30.8-35.3 h | 0.88 h | 8.6-9.7 h | 6.0-6.8 h | ~164 GiB |

The ten-cell row is a LOWER bound for its two extra cells: futuresight is strictly more work than
`gforecast` (it re-aggregates the window on every entry call on top of the same pricing), and
nothing has ever timed it, because nothing can until the zip exists.

**Is `fsight_wall` affordable at 40 site days?** The question as asked is moot: it is not
runnable, so it has no price. What the campaign actually faces is a decision with a build in it,
which is ticket 32. Dropping the family costs the campaign nothing it can have today, and 24-27 h
is affordable by any reading.

### 7. Two cautions for whoever launches phase 2

* **The multiplier was measured on `fifo` and `tmin`.** Phase 2 runs phase 1's chosen pairs,
  which may carry the pool-rebuild adapter (`rank_popularity`) or a family
  [Extend the gain bundles](20-extend-the-gain-bundles.md) adds. Neither is bracketed here. The
  1.45-2.20x spread across two adapters says the multiplier is adapter-sensitive; size on 1.9x
  and re-read the first priced cell's wall against this table before committing the rest.
* **This ticket's own framing said "eight of phase 2's ten cells" price. It is SEVEN**
  (`gmyopic`, `gforecast`, three `ggated_h*`, two `fsight_w*`) against THREE that do not
  (`fifo`, `lifo`, and `inb_off`, which the sentence forgot). The arithmetic above uses seven.
