# Column-semantics audit — every "wrong column for the wrong thing" incident, and where the next one is waiting

Date: 2026-08-26. Sources: all 59 memory files in `context/memory/store/`, `git log` back through
the 2026-08 refactor era, every `*_DDL` / `_SCHEMA` constant in `Optimization/persistence/` and
`Warehouse/generation/`, and the readers in `Optimization/metrics/`,
`Optimization/Performance_Evaluations/`, `Optimization/run_whatif_*.py`, `Diagnostics/`, and
`Visualization/readers/`. Grounds the upcoming convention + semantic-layer refactor of column
semantics.

The one-sentence summary: **this repo has hit at least 12 distinct classes of column-semantic
misread, every one failed silently, and today the semantics that would have prevented them live
almost entirely in DDL comment prose that no gate checks.**

---

## 1. Incident classes

Each class is a *different way* a correct value in a correct column produced a wrong number
because the reader held the wrong semantic. Ordered roughly by measured damage.

| # | Class | Concrete incident | Column(s) | Recorded in |
|---|-------|-------------------|-----------|-------------|
| 1 | **Wrong unit / scale** | Analysis declared "durations are milliseconds" and divided by 3.6e6 while the sim emits seconds — every published absolute duration 1000× too small, every rate 1000× too large; every ratio correct, which is why nothing caught it for the life of the project. Five modules had each restated the divisor. | `batch_stats.duration`, `task_makespan`, and every derived hour/rate in `Performance_Evaluations/` | commit `5434605`; memories `sim-time-unit-is-seconds-not-ms`, `putaway-break-even-is-nine-minutes-per-trip` (the 0.5 s → 14 s casualty whose conclusion inverted) |
| 2 | **Stamp read as span** | `extract_batch_stats` read "the last done event's timestamp" as `duration` and `task_makespan` (and `thr_batch`/`thr_task` through them); the picking split and `avg_concurrent_pickers`' divisor were anchored at a literal 0.0. Only survivable because every picker clock was reborn at 0 per batch — the moment a clock carried, `duration` silently became an absolute END time (6,504 s reported against a true makespan of 2,262 s). | `batch_stats.duration`, `task_makespan`, `picking_pct`, `traveling_pct`, `avg_concurrent_pickers` | commit `399de2a`; memory `one-clock-one-speed-one-config` |
| 3 | **Level summed as flow** | `cut` resets every batch, so it *looks* per-batch-additive and was labelled FLOW in four places — but its value is `len(items)` at the whistle, a re-counted standing queue. `Diagnostics/receiving_report.py` published `SUM(recv_cut) = 619,418` against a dock that never exceeded 6,162 — **101×**, as its headline number. Measured: `recv_cut == recv_depth` in 200/200 batches. | `put_queue_state.cut`, `batch_stats.recv_cut` | commits `36f494f`, `a49e4d2`; memory `cut-is-a-level-not-a-flow` |
| 4 | **Two producers, one key** | A put-away LEVEL (the standing queue, re-emitted each batch) and a pick FLOW (this batch's shortfall) both wrote `reason='unplaced'` into `carryover`'s composite PK under `INSERT OR REPLACE`; the pick row overwrote the put row and **500 units vanished with no error** (3 colliding keys, 200-batch run). | `carryover.reason` + PK `(run_id, batch_id, reason, sku)` | commit `3a8b4ab` (also `b7b55ed` for the dock class); memory `carryover-two-producers-one-key` |
| 5 | **Zero where NULL belongs (absent ≠ 0)** | `work_events.duration` was `REAL NOT NULL DEFAULT 0`, so all 29,657 pick rows of a 200-batch run claimed to have taken no time — `SUM(duration)` silently returned put+receive labour only *while looking like a total*. A pick event is an instant, not an interval; it now writes NULL. Siblings designed the right way from the start: `qty` NULL on state changes, `bin_placement.score` NULL ("a zero would claim a perfect placement"), `runtime.precomp_s` NULLable with `precomp_src` to tell "not measured" from "measured 0". | `work_events.duration` | commit `5cfdfbe` |
| 6 | **Written but unreadable (writer/reader column-list drift)** | `batch_stats.work_day` and `released_late` were on the dataclass, in the DDL and in the INSERT — and in neither `_BATCH_OPTIONAL` nor the `batch_frame` SELECT, so **every reader got 0 on every run**, including runs recording a real working day. Nothing could catch it: schema id right, insert succeeded, and 0 is also what a pre-column vintage legitimately returns. | `batch_stats.work_day`, `released_late` | commit `20571a1`; ratchet `Tests/architecture/test_written_columns_are_readable.py` |
| 7 | **Mixed units of account in one row** | `tables/per_run.py` put `mean_queue_depth` / `mean_recv_depth` (STORAGE UNITS — packs) adjacent to `mean_in_transit` (MERCHANDISE PIECES) in one CSV row with nothing to distinguish them; measured divergence reached 2.00× on one batch. Fixed by naming: `*_depth_units`, `mean_in_transit_pieces`. | `batch_stats.queue_depth`, `recv_depth` (packs) vs `in_transit_qty` (pieces) | commit `de6a66e` |
| 8 | **Rate against the wrong denominator** | `thr_batch = total_items / duration` divides by the batch MAKESPAN — the rate the crew worked *at*. Under a paced schedule it was read as the rate the day *delivered*: 4× the truth at a 400 s slot with a 100 s wave. `thr_elapsed` is the second number; a scheduling change moves the two in opposite directions. | `batch_stats.thr_batch` vs the derived `thr_elapsed` | commit `857fc21`; memory `working-day-clock-plan-corrections` |
| 9 | **Count without a denominator** | A growth-ladder exponent on raw candidate-bin scans (k = 1.74) could not distinguish "work per unit rose" from "more units"; the ratio (bins-scanned **per placement** 2.47 → 71.7, takes flat) answered instantly. Sibling: raw AUC of a cumulative volume curve restates `items × hours / 2` — shape index 0.987–1.025, no information beyond its endpoints. | calltree flow counters; the retired `volume_curve` AUC | memories `a-count-is-not-a-claim`, `auc-degenerate-on-volume-curves`; commits `2164c3a`, `1552eed` (ladders report flows-per-unit, not levels) |
| 10 | **Plan read as actuals** | Two generations. (a) `Task.items[sku]` is per-AISLE demand and both picker loops consumed it per-BIN — a SKU over three bins was picked three times; pre-`0b0d7d7` absolutes read high (items −9.2 %, duration −11.3 % after fix). (b) `task_stats.total_items` / `num_bins_visited` are the PLAN; a cut or clamped task reported full workload against partial time until `items_realized` / `bins_realized` were read off the event stream. | `task_stats.total_items`, `num_bins_visited` vs `items_realized`, `bins_realized` | commit `de1ee6e`; memory `pickers-over-picked-until-planned` |
| 11 | **Same name, different instrument (incommensurable "seconds")** | The deep ladder's `t_*` are MEAN seconds per batch per arm (`statistics.fmean` over checkpoint lines); summing one against a phase wall is a units mismatch that cost a day and produced a wrong committed claim. Also: `runtime.gc_pause_s` spans worker LIFETIME while `total_s` spans only the batch loop — never ratio them; traced seconds are never regression baselines; and all `runtime` seconds are wall-clock compute, orthogonal to sim-modeled seconds despite the shared unit. | ladder `t_*` vs `runtime.*_s`; `gc_pause_s` vs `total_s` | memories `runtime-metrics-is-the-deep-instrument`, `calltree-framework-first-findings`; commit `808adbd` |
| 12 | **Id-space / label confusion** | `actor_uid` (unique across crews) vs `actor_local` (dense per crew): a uid in a local slot lands inside `[0, k)` and is *accepted* — `_group_events_by_picker` now raises instead of silently dropping. Same family: `batch_stats.work_day` (release-day label) is NOT the shift work landed in (`work_events.shift_index`), and the two measurably come apart (work_day = 0 throughout a run with 1,418 cut events over 14 shifts). | `work_events.actor_uid` / `actor_local`; `batch_stats.work_day` vs `work_events.shift_index` | commits `ca24eb3`, `18bd375`, `a49e4d2`; memory `one-clock-one-speed-one-config` |

Three near-classes worth keeping on the list even though the damage was caught earlier or scoped
smaller:

- **Ledger-scope over-read** — `cons_breaks == 0` read as a conservation law over all merchandise
  when it is a stock ledger over BINS; three 2026-08 defects (incl. #4 above and a skipped batch
  deleting its own demand) hid in exactly that gap. The retired `bin_inventory` is the same shape:
  it logged picks and never restocks, so replaying it forward only ever decayed (−59 % of the
  warehouse in 5 batches). Memory `conservation-ledger-is-bin-only`; sunset note in
  `Optimization/persistence/Picking_Data.py`.
- **Drained counter read as cumulative total** — `queue_state_rows` DRAINS the flow counters each
  snapshot; a test read `q.placed` at the end as the total and passed only while the loop never
  snapshotted. Commit `2529fbb`.
- **Declaration rot beside the column** — three DDL comments lied after the receiving crew landed
  (`role` said `'pick' | 'put'` against 2,412 `'receive'` rows; `reorder_queue.kind` missed
  `'dock'` against 52,479 rows), and `Tests/bench/bench_sections.py`'s `_SEC_RE` required `inv=`
  after the log line renamed it `cons=` — parsing zero rows from every current run. Commit
  `ebe0c46`; memory `calltree-framework-first-findings`. Prose semantics rot because nothing
  executes them — which is the whole case for a semantic layer.

One statistical cousin, same lesson at the series level: per-batch series are autocorrelated
(lag-1..3 outside the white-noise band), so an iid bootstrap treats the rows as something they are
not and under-reports every CI — `common/stats_core.py:_boot_ci` uses a moving-block form. Memory
`per-batch-series-are-autocorrelated`.

---

## 2. Column census, tagged by semantic kind

Legend — **grain**: the per-what of one row; **kind**: STAMP (a point on a clock), SPAN (elapsed
time), LEVEL (a standing quantity, re-measured; never sum across snapshots), FLOW (an increment
belonging to this row's grain; additive), COUNT (flow of discrete things), RATE (flow ÷ span),
SCORE (policy-relative ordering value), SHARE (0–1 or percent), LABEL/ID (identity, ordinal, enum).
Sim time is SECONDS everywhere (`Warehouse/kernel/timeline.SECONDS_PER_HOUR` is the one divisor);
distances feed `cost_model.sec_per_inch`, i.e. inches upstream, seconds once persisted.

### sim_db family (`Optimization/persistence/Picking_Data.py`, one DB per arm)

**`batch_stats`** — grain: (run, batch). The epicenter; five of the twelve classes above hit it.

| Column | Kind | Unit | Notes |
|---|---|---|---|
| `duration` | SPAN | s | batch MAKESPAN (first start → last finish). Divides `thr_batch`; is NOT elapsed day time (class 8) |
| `task_makespan` | SPAN | s | Σ task durations = total labour; invariant `== SUM(task_stats.duration)` |
| `thr_task`, `thr_batch` | RATE | items/s | per-what of the denominator differs: labour vs makespan |
| `batch_start_time`, `batch_end_time` | STAMP | s (arm axis) | pinned 0 until the absolute clock; how a consumer recovers the epoch |
| `num_tasks`, `total_items`, `items_demanded` | COUNT/FLOW | tasks, items | `items_demanded >= total_items` is the check that caught the over-pick |
| `avg_concurrent_pickers` | LEVEL (time-avg) | pickers | divisor was anchored at 0.0 pre-`399de2a` |
| `picking_pct`, `traveling_pct` | SHARE | 0–1 in DB | ×100 at the frame boundary (`common/frames.py`) — a unit-change point; sums to 1 by construction for ONE crew only |
| `sigma_fd` | SCORE/LEVEL | s-weighted | placement quality snapshot |
| `reload_moves`, `reorder_placements`, `skus_reordered`, `units_ordered` | FLOW | moves, units | additive |
| `queue_depth`, `lead_queue_depth`, `recv_depth` | LEVEL | **storage units (packs)** | `recv_depth` disjoint from `queue_depth` — a reader wanting the whole unbinned backlog SUMS the two (same unit of account) |
| `in_transit_qty` | LEVEL | **merchandise pieces** | the OTHER unit of account (class 7) — never add to the depths |
| `work_day` | LABEL | ordinal day | RELEASE day, not the shift work landed in (class 12) |
| `released_late` | SPAN | s | the only record of a miss the release clamp erases |
| `recv_unloaded` | FLOW | storage units | |
| `recv_cut` | **LEVEL** | storage units | DO NOT SUM (class 3); additive statistic = count of batches non-zero |
| `recv_seconds` | FLOW | s of labour | deliberately NOT in any put-away total |
| `is_outlier` | LABEL | flag | |

**`task_stats`** — grain: (run, batch, task). `task_start_time`/`task_end_time` STAMPS (picker
clock); `duration` SPAN s; `W` labour SCORE (s); `lift_sum` SCORE; `num_bins_visited`,
`total_items` **PLAN** counts vs `items_realized`, `bins_realized` **ACTUAL** counts (class 10 —
plan/actual is a per-column semantic, and they agree everywhere until a clamp or cut bites).

**`picks`** — grain: one pick. `sim_time` STAMP (batch-local s); `quantity` FLOW count.

**`picker_events`** — grain: one event, batch-relative, pick-only, dense `picker_id`.
`time` STAMP; `bins_completed`/`items_picked`/`total_bins`/`total_items` are the picker's
**SESSION-CUMULATIVE** counters (a realized quantity is the *difference* between two events —
reading one row's value as a per-task flow repeats class 10); the five travel fields
(`pick_travel_x/y`, `non_pick_travel_x/y`, `cart_move`) are SPANS in seconds accrued since the
previous event — and the reconciliation identity has FIVE terms plus handling:
`pick + nonpick + cart + handling(no column) + other == duration` (memory
`lockstep-tests-compare-aggregates-only`).

**`work_events`** (+ `work_events_merged` view) — grain: one event, ANY stream, absolute axis.
The best-annotated table in the repo, because every annotation is a scar:
`t_abs` STAMP (s since arm start) / `t_local` STAMP (s since batch start); `shift_index` LABEL
("shifts label and never schedule" — restated in five places); `actor_uid` vs `actor_local` two ID
SPACES (class 12); `role`/`mode`/`event_type` enums (for `put`/`receive` event_type == role; for
`pick` a row is a state change and the work is the span BETWEEN rows); `qty` SIGNED FLOW
(pick < 0, put/receive > 0, NULL = moved no merchandise); `duration` SPAN-or-NULL (NULL = the row
is an instant, class 5); `source` enum. Per-ARM timeline: two arms' `t_abs` are not comparable.

**`aisle_metrics`** — grain: (run, batch, aisle). `n_skus`, `n_bins` LEVELs; `demand_sum`,
`pick_load_sum` per-batch FLOW aggregates; `lift_sum` SCORE (digest-stored float, summation-order
locked).

**`reorder_queue`** — grain: one queue entry per batch snapshot (a LEVEL listing). `qty` count;
`remaining_lead` a countdown **denominated in BATCHES, not seconds** (`Inventory_Manager.LEAD_TIME_UNIT`
— a standing unit trap the trailer feature must decide); `kind` enum (now incl. `'dock'`);
`queue` routing label.

**`put_queue_state`** — grain: (run, batch, queue). `depth` LEVEL; `oldest_age` STAMP of the head
(NULL = empty); `staging` configured LEVEL limit; `admitted`, `placed`, `blocked`, `cart_swaps`
FLOWs; `cut` **LEVEL mislabelled FLOW by analogy with its three flow neighbours** — the canonical
class-3 column, with the 101× warning now in its DDL comment.

**`carryover`** — grain: (run, batch, reason, sku). `qty` count whose LEVEL/FLOW kind **depends on
the `reason` value in the same row**: `dock`/`unplaced`/`held` are levels re-emitted every batch,
`unpicked_*` are flows. Never `SUM(qty)` across the table (classes 3+4 in one PK). The table now
raises on duplicate keys instead of `OR REPLACE`.

**`bin_scores`** — grain: (run, bin). `travel_d` SPAN-equivalent s; `height_mult` dimensionless
multiplier; `layout_score` SCORE (s); `map_pref` SCORE, NULL-able.

**`sku_scores`** — grain: (run, sku). `labor_cost`, `handle_var` s/unit; `expected_popularity`
RATE (freq × qty per batch); `expected_labor` s/batch expectation; `equilibrium_qty`,
`reorder_point` counts; `lead_time_mean` **in batches** (same trap as `remaining_lead`).

**`bin_placement`** — grain: one placement. `seq` run-scoped ordinal (NOT per batch — the PK
collision note is in the DDL); `qty` FLOW; `cause` enum; `score` SCORE in **policy-relative
units, not comparable across arms** (`policy` says which); `score_rank` ordinal within a group
the table otherwise does not record. NULL score ≠ 0 score (class 5).

**`bin_eviction`** — grain: one eviction. `qty` FLOW (unit re-enters the stock queue).

**`bin_inventory`** — RETIRED, archive-only, frozen shape in a comment. `pre_qty`/`post_qty`
LEVELs bracketing picks only — restocks invisible (the ledger-scope near-class). Two reader traps
documented: order by `(batch_id, id)`, and a resumed arm's first snapshot is not batch 0.

**keyframe family** (`bin_keyframe`, sibling `.keyframes.db`) — `qty` LEVEL snapshot per
(run, batch, bin).

**`simulation_runs`** — identity + params. `x_speed`/`y_speed` paces; `pick_intercept` 15 s;
`cart_swap_coef` 300 s (these constants being *warehouse-sized seconds* is what convicted the ms
divisor); `optimal_sigma_fd`/`optimal_work` yardstick LEVELs; `created` wall-clock TEXT;
`sim_schema_id` NULL = pre-stamp vintage.

### runtime family (`Optimization/persistence/runtime_metrics.py`, one DB per run root)

Grain: one row per ARM. All `*_s` are SPANS of **wall-clock compute seconds** — the same word
"seconds" as the sim tables and a completely different clock (class 11). `total_s` spans the
batch loop only; `gc_pause_s` spans the worker LIFETIME — not commensurable with `total_s`;
`reord_s`…`p2_s` section totals partition `total_s` minus a measured 0.8–2.2 % tail; `precomp_s`
NULLable with `precomp_src` provenance (NULL = not measured ≠ 0); `peak_rss_mib`,
`live_objects`, `n_bins`, `regime_bins`, `n_aisles`, `batches` LEVELs; `rate` RATE;
`map_lap_pct` SHARE.

### warehouse family (`Optimization/persistence/Warehouse_Data.py`)

`warehouse_stats`: build-time COUNTS + `expected_fill`/`target_fill` SHARES + fingerprint LABELs.
`aisle_type_stats`: COUNTS + `size_*_pct` SHARES. `aisle_layout`: geometry (bays as grid counts).
No time columns; low risk.

### inventory family (`Warehouse/generation/generate_inventory.py`, profiles tree)

`cartons`: `length`/`width`/`height` INTEGER catalogue dimensions (the inch/second boundary is
`cost_model.sec_per_inch`); `weight` catalogue weight units; `relative_frequency` SHARE;
`demand_qty_rate`, `expected_batch_demand` RATEs **per batch**; `equilibrium_qty`,
`reorder_point` COUNTs; `lead_time_mean` **in batches**; `supply_cv` dimensionless; `stock_plan`,
`subtype` LABELs. `creation_plan`: generation provenance (shares + JSON params).
`run_metadata`: key/value stamps.

### affinity family (`Warehouse/generation/generate_affinity.py`)

`affinity.lift` dimensionless ratio; `sku_group.lift_group` LABEL. (The `.arrays.npz` sidecar
staleness stamp earned two terms by failing tests — a file-level analog of vintage discipline.)

---

## 3. Analysis-path reads most at risk today

Ranked by (bypass depth × how many incident classes the touched columns carry). "At risk" means
raw SQL or column/positional indexing that no `Requires`, `dataset.bind`, or named query
validates — the semantic layer's first customers.

1. **`Diagnostics/receiving_report.py:120-186`** — raw f-string SQL over `work_events` and
   `batch_stats` with no `Requires`; probes tables by hand (`_has`, line 93). This file IS the
   101× incident site; the fix lives in a SQL comment (`:123-131` — "NOT SUM(recv_cut)"), i.e.
   the do-not-sum rule is enforced by prose at the exact place it was once violated. Any new
   query here can re-violate it silently.
2. **`Optimization/Performance_Evaluations/catalog/inventory.py:57-62`** — hand-built URI +
   raw `sqlite3.connect` + `SELECT … FROM cartons`, no bind, no Requires, no vintage check. The
   profiles-tree family has a declared shape (`generate_inventory.py`), and this reader uses none
   of it.
3. **`Optimization/Performance_Evaluations/common/frames.py:39-52`** — `getattr(s, col, 0)`
   defaults for eleven late columns: an absent or renamed column reads as a **plausible zero** on
   a published figure. This is the documented silent-failure surface of the shared `REQUIRES`
   (`Optimization/persistence/Picking_Data.py:991-998` — "seven of them flow straight into a
   published figure or CSV; 'guarded' means the failure is silent, not that it is safe").
   `task_makespan` is the only one that fails safe (0 → NaN at `frames.py:29-32`). Also the
   `picking_pct × 100` unit-change point (`:34-35`) and the `recv_cut` passthrough (`:46`) which
   hands a do-not-sum LEVEL to every downstream frame consumer with nothing marking it.
4. **`Diagnostics/replay_run.py:153,219-227,274,294,378,384`** — raw SELECTs including
   `SELECT * FROM simulation_runs` (`:378`); negotiates by probing (`_SOURCES`) and degrades with
   a caveat, by design — but every read is positional/name-fragile and the file reads the two
   trickiest archive tables (`bin_inventory` with its two documented ordering traps).
5. **`Optimization/run_whatif_labor.py:97-103`** — declared `Requires` (good) but positional
   `row[0..3]` indexing over a hand-written aggregate; a reordered SELECT is a silent
   labour/batch-hours swap. Note both this file and `run_whatif_volume.py` carry the scar
   header: each once restated `MS_PER_HOUR = 3.6e6` as "a second copy of the same wrong number".
6. **`Optimization/run_whatif_volume.py:93-94`** — the documented `.con` escape hatch: binds via
   `dataset.bind` + `REQUIRES` when the file is vetted, then still runs raw SQL; the unvetted
   fallback (`:90-91`) runs the same SQL with no validation at all (synthetic fixtures, cold
   archives — exactly where vintage drift lives).
7. **`Visualization/readers/base.py:303,336,394,412,431-437`** — mixes named queries
   (`self._sql('sim_db', 'batch_timing')`, `:379` — the good pattern) with raw `SELECT *` sites
   in the same class. `SELECT *` + `row.keys()` guards is the exact shape `dataset.py`'s
   docstring names as "the quietest schema change" (rename → default 0.0 on a figure).
8. **Non-DB analogs, same class:** `Tests/bench/bench_sections.py:22` (`_SEC_RE`) parses log
   lines by named regex groups and already rotted once (`inv=` → `cons=`, zero rows parsed from
   every current run); `Tests/bench/run_digest.py` hashed 11 of 14 tables until `20571a1` — a
   put-away refactor could pass the byte-identity gate while changing every row it wrote.
   Hand-run tiers (`Tests/calltree`, `Tests/bench`) are in no gate (memory
   `hand-run-test-tiers-rot-silently`), so these rot silently by construction.

Not at risk but worth naming: `Optimization/metrics/Simulation_Analytics.py:641` is SQL in a
*docstring* (a recommended viewer query) — documentation that will drift like the DDL comments in
`ebe0c46` did, with no gate.

---

## 4. What the existing machinery already provides (what a semantic layer rides on)

The repo already has three schema contracts and one analysis-side declaration system. The gap is
precise: **shape is declared and enforced; semantics are prose.**

- **`Schema/identity.py`** — `declared_id()` / `declared_shape()` are BUILT from the DDL the
  writer actually executes (never a hand-list, cannot drift), hashed into the committed shape
  store (`Schema/shapes/**`). `known_ids` carries per-vintage prose notes — e.g. the outgoing
  sim_db id is recorded with "its pick rows claim zero duration", which is exactly a semantic
  fact with nowhere structured to live.
- **`Schema/compat.py`** — `Requires` (a consumer's declared read surface, validated in CI
  against the *intersection* over every vetted vintage), `stamp_checked` at writer creation,
  `ANY_COLUMNS`, and the sim_db `CONDITIONAL_READS` registry
  (`Optimization/persistence/Picking_Data.py:1045+`). A semantic layer can hang per-column tags
  off the same table/column tuples `Requires` already names.
- **`Schema/dataset.py`** — `bind()` resolves a file to its OWN vintage; **named queries** with
  a stable LOGICAL output vocabulary (publisher-side, beside the DDL), per-vintage `override`,
  and the SQL policy (identifier positions generated, predicates hand-written; `.con` escape
  hatch reads must still appear in a `Requires`). Logical column names are already "the
  contract" — they are the natural attachment point for units and kinds.
- **`Optimization/runschema/`** — `resolver_for` (the run's own tree contract, positional levels,
  feature negotiation), `reader_for`/`analysis_path` (HEAD-first for analysis reads — memory
  `ingest-must-prefer-head-contract`), `sim_manifest`. Path semantics are solved; column
  semantics are the missing sibling.
- **`Optimization/Performance_Evaluations/core/quantities.py`** — the closest thing to a semantic
  layer today, and the pattern to extend: a `Quantity` declares **unit** (a declared `Unit`, not
  a lambda — "a conversion cannot be spelled inline in one consumer and forgotten in the next",
  which is verbatim how the 1000× divisor survived in five copies), **direction**, **stance**
  (`level` | `contrast` — the analysis-end cousin of level/flow!), a `Source` whose
  `db_columns`/`db_reads` name the sim-DB read so `core/era.py` can DERIVE a `Requires` from the
  quantity table (`QUANTITY_READS` — the schema↔semantics bridge already exists in one
  direction), and `views_suppressed` with a mandatory reason. Its limits are the refactor's
  scope: it covers only quantities that become figures, only at the analysis end, and it has no
  vocabulary for STAMP/SPAN, LEVEL/FLOW-as-storage-kind, per-what, or unit-of-account.
- **`common/units.py`** — `KINDS = ('duration_s', 'rate_per_s', 'count', 'share', 'score',
  'dimensionless')` with `SECONDS_PER_HOUR` imported from `Warehouse/kernel/timeline` (the fix
  that made the two declarations unable to diverge). A storage-side kind vocabulary would be a
  superset of this list.
- **Ratchets that already gate adjacent failure modes:**
  `Tests/architecture/test_schema_compatibility.py` (Requires vs guaranteed surface),
  `Tests/architecture/test_written_columns_are_readable.py` (writer column list ⊆ reader list —
  born from class 6, sabotage-checked), `Tests/bench/run_digest.py` (byte-identity, now all 14
  tables), `Schema/profile_tree.py --check` and the runschema contract/preflight pair.

**The concrete gap the refactor should fill:** today the answers to "is this a stamp or a span",
"may I SUM this across batches", "what unit of account is this count in", "per what is this
rate" live in DDL comments (`put_queue_state.cut`'s 15-line warning is the state of the art), in
memory files, and in five-line scar headers — none machine-readable, all rot-prone (`ebe0c46`
proves the comments drift within days of a feature landing). Every one of the twelve classes is a
missing per-column tag: kind (stamp/span/level/flow/count/rate/score/share/label), unit +
unit-of-account (sim-seconds vs wall-seconds vs batches; packs vs pieces), per-what
(batch/arm/day/picker/session-cumulative), nullability meaning (absent ≠ zero), and
producer-uniqueness on shared-key tables. The attachment points all exist: the DDL-derived shape
(add tags where the shape is declared), the named-query logical vocabulary (tags survive
renames), `Requires` (a consumer could declare not just *what* it reads but *how* — `sums`,
`ratios-against`), and `Quantity.Source` (already halfway there from the figure end).
