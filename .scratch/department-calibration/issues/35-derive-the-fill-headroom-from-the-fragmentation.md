# Derive the fill headroom from the fragmentation

Type: task
Status: resolved
Blocked by: 34

Graduated 2026-09-10 from
[Band the own-bin share and the free-index depth](32-band-the-own-bin-share-and-free-index.md),
decision 7. AFK build. Skills: `codebase-design`; `schema-maintainer` if the record's shape
moves; `memory-maintainer` for the comparability break.

## Question

Not a decision: the build that makes 32's decision 7 true. Today `STORE_FILL` / `FF_FILL`
(0.85, `assumed`) stand where a closed form belongs: the planner sizes each bucket at
`ceil(requirement / (bins_per_aisle x fill))`, so 15% of every bucket is headroom nobody
derived, and the store's slide (32) ends somewhere that number was never chosen to cover.

What lands:

- Under the era the fill is DERIVED per bucket: `requirement / (requirement + E[extra])` from
  34's stamp, floored at a declared minimum headroom (a new staffing key on all five seams, the
  memory `config-knob-has-five-seams`; provenance `assumed`). The regime decides which keys are
  inputs (29): flag-off keeps the typed fill and records the derived one as None; the era
  refuses `--store-fill` / `--ff-fill` the way it refuses the picker flags.
- The sizing promise is re-stated: the warehouse holds the declaration AND its stationary
  fragmentation; `UnfieldableRequirement` names the extra bins short, not only the requirement.
- The fixed point (`era_coverage`) re-runs with the derived fill inside the loop, and the record's
  `fielded.buckets[]` carries `fill` beside `expected_extra`.
- The reference warehouse moves. Record the new size on the ticket and write the comparability
  memory: absolute travel numbers across this commit are not comparable (the fourth such break).

## Done when

- Flag-off is byte-identical (the planner runs once at the typed fill, proven); the era sizes
  every bucket from the derived fill; a bucket whose derived headroom is below the declared
  minimum takes the minimum and the record says which; both preflight canaries and an era canary
  through the pool; nine gates green.

## Answer

**RESOLVED 2026-09-10.** Under the era the planner's fill is DERIVED per bucket from the
stationary fragmentation and floored at a declared minimum headroom; flag-off the typed fill
stands byte for byte. The reference warehouse moved (below) and the comparability memory is
written (`derived-fill-is-the-fourth-comparability-break`).

**What landed.**

- **The planner** (`Warehouse/inventory/inventory_planning.py`): `plan_warehouse(bucket_hold=)`,
  a `{bucket: bins}` map. A named bucket is sized at `max(1, ceil(hold / eff))` replicas through
  the SAME `ceil(x / (eff · f))` arithmetic with `x = hold, f = 1` (the aisle-split inflation still
  divides `f`); the promise check reads `capacity >= hold` for it and the refusal names the extra
  bins ("must hold N bins (R declared + E expected extra)"). `bucket_hold=None` leaves both sizing
  tables untouched -- a `hold = req / fill` rewrite was deliberately NOT done, because
  `ceil(req / (eff · fill))` and `ceil((req / fill) / eff)` differ by an ulp exactly on the
  boundaries the fill sits on. Every fielding row now carries `fill` and `hold` (what the bucket
  was sized at); `declared_packing` exposes the one-pass requirement + slots.
- **The derivation** (`Optimization/simdriver/era_coverage.py`): `derive_fill(req, extra, h)` --
  `hold = max(req + E[extra], req / (1 - h))`, `fill = req / hold`, `headroom_floored` when the
  minimum binds; a negative extra takes the floor, a bucket with extra and no requirement (the
  tier a remainder migrates INTO) is sized to its extra. `derived_holds` stamps every SKU's plan
  before the plan (`declared_packing` + `field_requirement` with its slots), runs the chain per
  section and returns the hold map (a bucket that must hold nothing is not named). `fixed_point`
  takes that branch each round when `inputs['min_headroom']` is not None and calls
  `plan_fn(bucket_hold=holds)`; flag-off `plan_fn()` exactly as before. The post-plan
  `stamp_fragmentation` reuses the pre-plan chain (`frag=`), and `stamp_fill` writes
  `headroom_floored` per row plus `fielded.fill` (`provenance`, `min_headroom`, `typed`,
  `derived: {section_fill, expected_extra, headroom_floored_buckets}`) -- flag-off `assumed` with
  `derived: None`. `holds_at(record)` reads the holds back for a rebuild.
- **The asset builder** (`sim_assets.py`): the run path hands the loop's holds through; the
  REBUILD path (`coverage_record=`) sizes from `holds_at` -- never re-derives; the FROZEN path (a
  multi-cell run's every cell) derives from the frozen declaration under the era, so no cell is
  sized at the typed fill while the first was derived. `warehouse_stats.target_fill` is the
  derived store section fill under the era (semantic note updated: regime-dependent).
- **The knob, all five seams**: `MIN_HEADROOM = 0.05` (settings, `assumed`; it covers what the
  chain does not model -- supply jitter, spills, own-bin top-ups, same-day double lines), on
  `STAFFING_KEYS` and `ERA_ONLY_KEYS`, `_ERA_DEFAULTS`, `min_headroom()` accessor (refuses outside
  [0, 1)), `--min-headroom` (`_free_share` type), restored at both sites, in the payload via
  `staffing_spec()`. Under the era `--store-fill` / `--ff-fill` REFUSE (`_ERA_DERIVED_FILL_FLAGS`,
  they are channel keys, not staffing keys) and the run spec records both as None; flag-off
  `--min-headroom` refuses via `ERA_ONLY_KEYS`. A resume of an era run recorded BEFORE this
  commit (numeric `store_fill` under the era) is refused rather than re-sized.

**The reference pair** (re-planned under the era, 172 s, one round):

| | 2026-09-09 (typed 0.85) | derived fill |
|---|---|---|
| warehouse | 2,761 aisles / 2,466,650 bins | **2,536 aisles / 2,311,000 bins** |
| store capacity (req 1,008,933) | 1,229,750 (free 220,817) | 1,198,300 (free 189,367), section fill 0.872, 20 of 48 buckets at the floor |
| fulfillment capacity (req 1,049,107) | 1,236,900 (free 187,793) | 1,112,700 (free 63,593), section fill 0.944, 2 of 3 at the floor |
| expected extra | -- | +62,305 store / +48,204 fulfillment |

The store's six `small` pallet buckets GROW (conveyable/food/small 76,000 -> 98,000 bins, fill
0.652; electronic/small 54,000 -> 70,000; clothing/small 12,000 -> 18,000, fill 0.560): picked
singleton remainders return as pallets of 1 and stay. Every `singleton` bucket SHRINKS to the
5% floor (food/singleton 58,500 -> 52,500, fill 0.950). Crews K = 31 / 23 and floors
1.2728 / 1.2668 did not move; s_pick 105.474 / 17.135.

**Done-when, verified.**

- Flag-off byte-identical: a per-bucket oracle (`max(1, ceil(req / (eff · 0.85))) · eff` for every
  bucket, with and without an empty map), `test_warehouse_sizing`'s pinned numbers unchanged, the
  flag-off loop calling a planner that never learned the keyword, the flag-off sweep canary clean.
- The era sizes every bucket from the derived fill: the 90-SKU pair's rows are TIGHT
  (`capacity - eff < hold`), every fill `<= 1 - h`, both floored and un-floored buckets present and
  counted; the 15-bucket canary and the 51-bucket reference pair agree.
- A rebuild reproduces the run: `holds_at(record)` equals the map the planner was handed, the
  rebuild's geometry equals the run's with `derived_holds` patched to raise; the single-cell era
  canary's analysis read "sizing from the 15 bucket hold(s) the run derived" and rebuilt
  63 aisles / 71,100 bins; the two-cell era sweep's analysis rebuilt both cells (56 jobs each).
- Both preflight canaries end to end, tree shape unchanged, fingerprint refreshed; the era canary
  through the pool (`_canary_single` and `_canary_sweep`, `--workers 2`), no Traceback.
- `Tests/unit/test_fill_headroom.py` (21 tests) + `test_coverage_rescale` row keys and stub;
  `Tests/unit` green; the nine gates green after the maintainers' regen.

**Found on the way.** A MULTI-cell era run lost its coverage record: the freeze recorded it
(`_record_coverage`), then the first cell's `_record_derived` replaced the whole calibration
block with one carrying `coverage: None`, and the analysis stage found "no stock declaration"
for every cell and rebuilt nothing (`Config stage: 0 job(s)`, exit 0 -- the CLAUDE.md §3 silence).
No multi-cell era run had been analysed before. Fixed in `workunits._record_derived` (a recorded
coverage stands), pinned by a test; a multi-cell era run recorded earlier cannot be re-analysed
from its catalogue.

**From the reviews** (`code-reviewer`, `test-reviewer`; nothing critical, all applied):
`--min-headroom` took the utilization validator ((0, 1]) where the domain is [0, 1) -- now
`_free_share`; a pre-feature era resume would have been silently re-sized -- refused; an unnamed
empty bucket's row read the typed fill beside a derived block -- reads `1 - h`; the flag-off
refusal message and the setup-cost note; `target_fill`'s semantic note. Tests: a CONFIG leak past
the restore (whole-dict snapshot + `_RUN_STAFFING`), an ordering claim captured at call time, a
two-thirds tautological byte-identity test replaced by the per-bucket oracle, the floored /
un-floored split and tightness asserted on the real pair, the frozen-cell path and a mixed
catalogue covered, an 8 s flag-off test cut to one round.

**Not on this ticket.** `MIN_HEADROOM = 0.05` is an ASSUMPTION; whether the floor can be derived
from the supply-jitter law is fog (map). The trajectory band and the affinity-aware line share are
unchanged. The architecture / context / memory layers need their regen (maintainers after the
commit).
