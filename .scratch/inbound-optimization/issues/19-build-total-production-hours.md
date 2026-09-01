# Build total production hours

Type: task
Status: resolved
Blocked by: 08

## Question

Make the objective the effort has been reasoning about actually reportable. Ticket 10 fixed
the selection metric as TOTAL PRODUCTION HOURS = unload + put + pick, and 08 made it phase
1's selection metric — but **no artifact reports it today, and one of its three legs is not
persisted as a scalar at all.** Selecting on what exists instead (pick hours) would
systematically favour arms that buy pick time with put-away time, which is the exact trade
this effort exists to measure.

Where the three legs stand:

| leg | where it lives | reported today |
|---|---|---|
| pick | `task_stats.duration` summed per batch | yes — the `production_time` quantity |
| unload | `batch_stats.recv_seconds` | a table column with **zero quantities declared** |
| put | **not in `batch_stats` at all** — only `work_events.duration WHERE role='put'` | no |

- **The unload leg** is the straightforward half: a new declared Quantity over a column
  already in the frame. `recv_seconds` is declared FLOW/s/batch in `sim_semantics.py` and
  is deliberately disjoint from `putaway_seconds` — do not merge them.
- **The put leg is the real work.** `putaway_seconds` exists only as an in-sim property on
  `Inventory_Management` and is never written to `batch_stats`. The hours live in
  `work_events`, which **the analysis suite has never read a single row of** — so this
  needs a new frame kind, not just a new quantity.
- **The crew discriminator is `role`, not `queue`.** `work_events.role` is
  `'pick' | 'put' | 'receive'`; there is no `queue` column on that table (`queue` belongs to
  `put_queue_state` and `reorder_queue`). The wording in 08's early comments was loose about
  this. Per-crew hours are `SUM(duration) GROUP BY role`.
- **`work_events.duration` is nullable BY DESIGN** — an interval for put/receive, NULL for a
  pick row (an instant). It was `NOT NULL DEFAULT 0` until 2026-08-25, and the DDL comment
  records that `SUM(duration)` "silently returned put+receive labour only while looking like
  a total". Any aggregate here must state its NULL handling explicitly.
- Follow the route discipline in the `route-reviewer-finding` skill: the unload leg is a
  clean R3; the put leg is R3-with-a-new-frame-kind.
- Order is committed evidence: `_METRICS` order is the row order of every significance CSV,
  and `AGGREGATE_ORDER`/`HEADLINE_ORDER`/`SERIES_ORDER` are validated at import — **new
  quantities go at the END.**
- 08 also granted `yard_overage_days` a `headline` slot beside hours and missed share. That
  quantity belongs to [Build the yard metrics](17-build-the-yard-metrics.md); the
  `HEADLINE_ORDER` entries from both tickets need to land without fighting each other.

Honesty note inherited from 01: total unload hours vary across arms only through the
reorder feedback loop, so the first-order lever is placement quality buying put + pick
hours. Inbound-off (phase 1) the unload leg is near-absent, which means phase 1's metric is
effectively **put + pick** — and the put leg is exactly the piece that does not exist yet.

## Comments

2026-08-31, from resolving "Build the yard metrics" (17): the shared `HEADLINE_ORDER` edit
that comment anticipated is now UNAMBIGUOUSLY this ticket's, and it comes with a
prerequisite 08 did not name.

`yard_overage_days` is BUILT and is not in `HEADLINE_ORDER`. It cannot be: a slot there is
validated at import by `_check_order(..., 'steady_state')`, so every headline key must
declare a steady-state scalar in the series document — and the series document is built by
`_build_series` out of the batch and task frames alone. The yard's numbers are per-trailer
and per-drain, so the fee has no such scalar and would fail the import check on the spot.
Adding the panel before one exists would also put an empty sixth panel on every inbound-off
publish, which is every run in the archive.

That is the same seam this ticket already has to open (put hours are not a `batch_stats`
column either), so the two land together: whatever route is chosen for `total_production_hours`'
steady-state scalar, give the fee one on the same mechanism and append BOTH at the end of
`HEADLINE_ORDER`. A comment naming the debt is in `HEADLINE_ORDER` itself.

Two things from 17 that change the ground here:

- `core/quantities.PER_BATCH_KINDS` now exists and is load-bearing. `stats_core._metric_series`
  is written as "batch, ELSE the task frame", so a new `FRAME_TABLE` kind is silently looked
  up in `df_t` and comes back empty. Any new frame kind this ticket adds must be added to
  `PER_BATCH_KINDS` if it is meant to reach the significance suite, and left out with intent
  if it is not — a kind in neither now raises.
- The era gate's RUNTIME half is built (`ctx.capabilities()`, `requests.EraUnmet`, the
  `[era]` summary). If put hours read `work_events`, that table is on the conditional
  surface: the quantity needs a `work_events` capability name, and it will then be refused
  with an `[era]` line on runs that predate it rather than rendering a plausible zero.

## Answer

BUILT. The objective is reportable: `total_production_time` (unload + put + pick) is a
declared Quantity, reaches the significance suite as a paired per-batch metric, carries a
steady-state scalar, and has a headline panel — as do its two previously-unreported legs,
`putaway_time` and `unload_time`.

### The one deviation, and why the ticket's own plan could not stand

The ticket specified the unload leg as "the straightforward half: a new declared Quantity
over a column already in the frame" — `batch_stats.recv_seconds`. **That read does not pass
the era gate.** `recv_seconds` is NOT in `Schema.compat.guaranteed_surface('sim_db')`
(measured: `batch_stats is missing column(s): recv_seconds`), so a quantity reading it sits
outside the version-free surface and must name a capability — and no honest one exists.
Capabilities probe a TABLE for rows, `recv_seconds` is a column, and `work_events` (vintage
`1a594605a10e`) PREDATES the dock columns (`31cb7d1b1199`), so naming `work_events` for it
would be a claim the capability cannot support. Declared unguarded, it would have filled
every pre-dock vintage's unload leg with a plausible zero — the exact failure the gate
exists to stop.

So BOTH inbound legs read `work_events` (`role='receive'` / `role='put'`), which puts the
whole objective behind ONE capability that is true when it says it is. That is only safe
because the two surfaces agree, and they are written by different code from different
state — so agreement is evidence, and it is now ESTABLISHED rather than assumed:
`test_the_unload_leg_matches_the_dock_column_the_ticket_specified` reconciles them
seconds-for-seconds on a real run, promoting `Diagnostics/receiving_report.py`'s first
check from a diagnostic into a gate on the quantity. A second assertion in that test fails
if `recv_seconds` ever ENTERS the guaranteed surface, so the deviation cannot outlive its
reason.

The ticket's "do not merge them" is honoured in the sense it was meant: unload and put-away
stay two columns, two quantities, two segments.

### What was built

- **Persistence.** Named query `work_hours_frame` — `SELECT batch_id, role, SUM(duration),
  COUNT(*), COUNT(duration) ... GROUP BY batch_id, role` — plus `load_work_hours`, and
  `work_events` added to `CONDITIONAL_READS`. The fold is in SQL because the rows are
  EVENTS (~30k an arm) and the consumer wants three numbers a batch. **`n_rows` and
  `n_timed` are part of the answer, not diagnostics**: `SUM` skips NULLs silently, so
  without them "this role did no timed work" and "this role's rows carry no durations"
  arrive as the same number — precisely the shape of the pre-2026-08-25 defect where 29,657
  pick rows claimed zero seconds. The count surfaces on the figure's subtitle.
- **The `work` frame** (`frames._wdf`), the fourth metric-source kind. Put and unload from
  the fold, pick JOINED from the task frame — `work_events` structurally cannot supply the
  pick leg (a pick row is an instant with a NULL duration; the work is the span BETWEEN
  two), so reading it there would re-derive a number `task_stats` states directly and would
  have read ZERO on every vintage before the column became nullable.
- **EMPTY, never zeros, when there are no rows at all.** The load-bearing rule, and the one
  `_ydf`/`_ddf` already follow: a run predating `work_events` and a run whose crews did
  nothing are indistinguishable by row count, and a frame of zeros would publish
  `total = pick` as if the put leg had been measured and found to be nothing. Zeros WITHIN
  a populated frame are the opposite case and are filled deliberately, so this frame's batch
  index is the batch frame's and every paired statistic can align on it.
- **`_metric_series` now dispatches and RAISES.** It was "batch, ELSE the task frame", so a
  kind added to `FRAME_TABLE` and forgotten was looked up in `df_t`, found absent, and
  returned as an empty Series — reported as unmeasurable rather than as misrouted, on every
  arm, in silence. It now takes a `{kind: frame}` mapping (a mapping, because each new
  source used to mean a positional argument threaded through six call sites, and the one
  that was missed would have fallen through), and an unknown kind is an error.
  `ctx.metric_frames_for(key)` / `ctx.metric_frames()` are the single assembly point.
- **`work` IS in `PAIRED_KINDS`.** Not optional: 08's pre-registered decision rule is a
  moving-block CI on the total-production-hours gain, which is the significance suite's
  paired machinery and nothing else.
- **The series builder opened.** `_build_series` gained the work and yard frames — the seam
  17's comment predicted — and writes `ss_prod_total`, `ss_putaway`, `ss_unload` and
  `yard_overage_total`. All NaN, never zero, when their frame is absent.
- **Both owed headline slots paid**, appended at the end: `total_production_time` and
  `yard_overage_days`.
- **`labor.production_legs`** — a stacked composite of the three legs per arm with each
  bar's put SHARE printed, which is what makes "this arm bought its picking win with
  put-away hours" visible across arms of different sizes.

### Two mechanisms the appended headline slots forced

1. **`yard_overage_total` is a run TOTAL, not a steady-state mean**, and is named so it
   cannot be read as one. The fee's instances are TRAILERS, which carry no batch index to
   window over; the whole run's accrued trailer-days is also the number a carrier bills,
   which is how `yard.fee` already draws it. `_METRIC_GROUPS` also gained a PAIRABLE flag —
   the fee has no batch i to difference against batch i, so its group renders without the
   paired effect annotation its neighbours carry, rather than asking `_metric_series` for a
   'trailer' kind that now raises.
2. **`quantities_optional=` on `@evaluation`** — a genuinely new seam, and a narrow one. A
   quantity there feeds the view derivation (it is drawn, so it must be drawable) but is
   excluded from `era_shortfall`. Without it the headline had two bad options: declare the
   appended quantities and lose the ENTIRE headline figure to an era refusal on every
   archived run, or omit them from `quantities=` and reintroduce exactly the declaration
   drift `core/families.py` was written to end. The permission is paid for by
   `headline._drawable`, which DROPS a group with no finite value on any arm and logs which
   — a labelled panel with no bars reads as "measured, and it was nothing", which is the era
   gate's own failure mode arriving one layer below where the gate can see it.
   `labor.production_legs` declares the same quantities as REQUIRED and takes the refusal;
   that contrast is what keeps the permission narrow.

### One thing only LOOKING at the figure caught

`untimed_rows` first counted every role. Rendered, the subtitle then announced that
**51,440 work rows carried no duration and were "excluded from every leg"** — on a
completely healthy run, about the pick rows, whose exclusion is the contract (their leg
comes from the task frame). A warning that fires correctly on every correct run is worse
than no warning: it trains a reader to ignore the line, and it makes a healthy run look
like it lost fifty thousand rows of work. The count is now scoped to the roles the frame
FOLDS, where a missing interval genuinely does leave a leg short, and the subtitle says
"UNDERSTATED" rather than "excluded". A healthy run reports nothing — asserted both ways,
in the unit tier and against the real file. Every test in this build passed before that
change and after it; nothing but rendering the picture would have surfaced it.

The same look confirmed the figure earns its place: the put SHARE annotations spread 1%–5%
across arms (`Rank_maxlabor` 4%, `TripMax` 5% against `Rank_labor` 1%), which is the
put-versus-pick trade visible at a glance. The unload segment is invisible at test scale —
a 4-batch store run does almost no receiving — which is the model, not the chart.

### Verified, not assumed

- **On a real standing-yard run** (`Tests/e2e/test_production_hours_e2e.py`, 7 tests): the
  put leg is non-zero and matches the raw `work_events` sum; the unload leg matches
  `batch_stats.recv_seconds`; the pick leg joins batch-for-batch (a per-batch off-by-one
  would leave the run total right while every paired comparison lined up batch i's picking
  against batch i-1's put-away); pick rows carry no duration and the untimed count is exact;
  and the figure actually DRAWS with an empty error tally — a grant is not an output.
- **On a real archived run** (2026-08-20, no `work_events`, no `recv_seconds`): the loader
  returns `[]`, the frame is empty, the work metric series has length 0 (absent, not zero),
  both new scalars are NaN so the headline drops those two groups, `ss_prod_hours` is
  unchanged, and the capability probe does not report `work_events` — so
  `labor.production_legs` is refused with an `[era]` line.
- Unit tier **1495 green** (+20 here), integration + e2e **426 green**, era gate
  `findings() == []`, and the seven non-architecture gates pass.

### Schema and contract

No schema event: sim_db stayed `be2a593727be`, the run tree stayed `51f99901f03c`. Both
FINGERPRINTS moved because the sources changed, and both were re-recorded through the
pipeline — `schema_report.py --sync` (0 shapes committed, already current) and
`preflight --yes`, whose two canaries proved the tree shape unchanged.
`_FIGURE_FAMILY_EVALUATIONS['labor']` gained the new evaluation (unhashed attribution, so
it cannot move `schema_id`).

### Owed, and to whom

- **The derived architecture layer** — `graph.json`, `nodes.json`, `files.yml` (two new
  files, several new functions) and the HTML suite. Left to the `architecture-maintainer`,
  the same debt tickets 09, 13, 15 and 17 each recorded.
- **One PRE-EXISTING failure, not from this work**:
  `test_bin_mutation_sites.py::test_the_dead_site_is_still_dead`, which substring-matches
  `add_from_bin` in an `Inbound/trailer.py` docstring citation — already spun off at 16.

### A finding [Build the run-shape layer](18-build-the-run-shape-layer.md) needs

`total_production_time` has a steady-state scalar (`ss_prod_total`, in every profile's
series document) but **no aggregate name**, so it is deliberately absent from
`AGGREGATE_ORDER` and from the cross-profile summary CSV. Two reasons, the second
decisive: `test_quantities` pins `_AGG_METRICS` to its legacy literal with no growth list;
and `_aggregate_series` NORMALIZES each profile to its own baseline, so that CSV holds
RATIOS, not summed hours. 08 ranks phase 1 on total production hours SUMMED ACROSS
PROFILES, so the selection artifact must sum `ss_prod_total` over the profiles' series
documents itself — reading the aggregate CSV would silently rank on normalized ratios. It
must also fail LOUDLY when the metric is absent: on a run without `work_events` the row is
simply not in the significance CSVs, and a selector that reads absence as a tie ranks 17
rules on nothing.
