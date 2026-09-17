# Architecture deepening

Label: wayfinder:map
Opened: 2026-09-16

The ordered work lives in [PLAN.md](PLAN.md) beside this file; this map holds the destination,
the decisions, and the fog.

## Destination

Every place where one fact is declared in N places has either been given a module that declares it
once, or has been closed with a written reason. The two aisle-ledger quantities that drift are
fixed or deleted, and the arm they corrupt no longer does.

Reached when:

1. `_aisle_vol_sum` no longer drifts, and the fix landed BEFORE the phase-2 campaign rather than
   after it.
2. The aisle ledger is one module with an `add`/`drop` pair and a `reconcile()` that makes its
   invariant assertable for the first time.
3. Adding a placement policy, a config knob, a DB column, a recorded stream or a frame kind is
   ONE declaration each, and a missed seam is a failure rather than a silent default.
4. Every ticket below is `resolved` -- landed, or closed with a reason a future review will find
   before re-suggesting it.

## Notes

- **Execution override: ON.** Once a ticket's governing decisions close, implementation graduates
  from fog into `task` tickets on this map. Tickets here resolve deliverables, not only decisions.
- **Ticket 01 is PRE-RUN and everything else is POST-RUN.** The split is not stylistic: see the
  campaign decision below.
- **Post-run work happens in a `git archive HEAD | tar -x` copy, never on `develop`, and never in
  a `git worktree`.** Worker recycling is pinned at 1 (`Optimization/simdriver/supervisor.py:203`,
  "one fresh process per job"), and the pool is spawn-not-fork, so a 120-unit campaign spawns 120
  fresh processes across ~13 h and each re-imports the tree at the moment it starts. Editing
  `develop` under a running campaign silently gives later units different code than earlier ones,
  and no gate would catch it. `git worktree add` is the wrong tool -- it dies on this repo's long
  generated filenames (memory `head-copy-via-git-archive`), and mixed CRLF/LF breaks scripted
  edits in a copy four separate ways.
- **Four of the six surfaces this arc touches already carry a red architecture test** --
  `Warehouse/inventory/`, `Optimization/simdriver/strategy_runner.py`,
  `Optimization/persistence/Picking_Data.py` and `Optimization/config/`. They are NOT ours; they
  are handed over in `.scratch/architecture-drift/`. Capture a per-gate before/after baseline
  before the first commit and attribute on the individual assertion, never on the failure COUNT
  -- that count moves with tree state (16 vs 13 vs 5, all measured).
- **`Warehouse/placement/` is the one clean surface**, and it has a ~4 s frozen-oracle
  equivalence suite (`test_co_demand_pool_equivalence.py`, `test_ranked_assign_pool_equivalence.py`,
  `test_travel_balanced_equivalence.py`). That is the cheapest real safety net in the repo, which
  is why the placement arc goes first after the run.
- **Byte-identity discipline holds for every ticket except 01**, which is a named break.

## Decisions so far

- **The split at the run is the organizing decision** (2026-09-16). A fresh campaign re-baselines
  everything, so the moment before a launch is the cheapest this repo will ever have to take a
  comparability break. Break after it and the campaign's own results are permanently tainted AND
  the break still has to happen later. Ticket 01 therefore lands pre-run; every structural ticket
  waits.

- **`_aisle_vol_sum` is a LIVE defect, not a reporting one, and `rank_cartlabor` is in the
  campaign twice.** It is read in the scoring expression --
  `_TravelBalancedPool._score_of` does `score += self._cart_cost(self._vol_load[aid] + add)`
  (`Warehouse/placement/Assignment_Functions.py:1745-1756`, function form at `:1510-1515`) -- and
  the argmin over `score` picks both the aisle and the bin, so a monotonically-growing penalty can
  flip it. `PHASE2_PAIRS` (`Optimization/config/whatif_config.py:106-111`) carries
  `('rank_cartlabor','rank_minlabor')` and `('tmin','rank_cartlabor')`. Two bounds, stated
  conservatively: only `cart_on` arms are affected, and the `max(0.0, v/cap_raw - 1.0)` clamp makes
  the term identically zero until an aisle crosses cart capacity -- "inert for the big store cart,
  bites for the small fulfillment cart" (`Optimization/config/strategies.py:130-132`).

- **`_aisle_lift_sum` is write-only end to end and gets DELETED, not fixed** (ticket 03). It lands
  in `aisle_metrics.lift_sum` and nothing reads that column -- not a `Quantity`, not a figure, not
  a view, not a published experiment. Its only in-memory reader is
  `_build_load_assignment_fn`, whose `load_min`/`load_max` wrappers have no row in
  `strategies._RESTOCKS` and no production caller. The repo's own capability contract already
  excludes the column: `CAP_AISLE_METRICS` declares only `('run_id','batch_id','aisle_id','n_bins')`
  (`Optimization/persistence/Picking_Data.py:1458-1464`). There is nothing to fix -- no consumer
  wants a correct value -- so repairing it would mean re-deriving a number to feed nobody. Four of
  `aisle_metrics`' five payload columns are in the same state.

- **The `vol_sum` fix is a symmetric decrement now, and derive-on-read later** (tickets 01, 02).
  Subtracting in `_drop_sku_from_aisle` and its inline twin matches what the other three sums
  already do and is the smallest diff. Dropping the running sum entirely in favour of
  `sum(_sku_vol_product[s] for s in members(aid))` is the better end state -- it makes the drift
  unrepresentable rather than merely absent -- but it changes the hot path in the scoring loop,
  which is both a perf question and a float-accumulation-order question. Neither belongs in a
  pre-run patch. Ticket 02 revisits whether the sum should exist at all.

  **AMENDED 2026-09-17 (ticket 02 stage B): derive-on-read is REFUSED, not deferred.** The
  "better end state" above was wrong about where the leverage is. The levels are read inside
  the scoring expression on every candidate, in a loop whose width is the live aisle count and
  grows with the catalogue, so deriving on read turns an O(1) dict read into a sum over the
  aisle's members there. And it is not what makes the drift unrepresentable: what does that is
  the add and the drop living in one module with `reconcile()` between them, which the running
  sums now have.

- **Accepted for ticket 01: the fix is made by hand in two places, which is the exact failure mode
  ticket 02 exists to kill.** `_drop_sku_from_aisle` carries a hoisted-locals inline twin in
  `_reclaim_empty_bins` and its own docstring says KEEP THE TWO IN SYNC. Two lines in two places is
  an acceptable price for a pre-run patch ON THE CONDITION that ticket 02 is the first thing that
  lands after the run.

- **An agreement pin does not notice a fix to shared state** (2026-09-16, ticket 01). This map
  predicted the three placement equivalence suites would fail on ticket 01 and need re-baselining.
  **They passed, 95 tests, untouched.** They pin float-exact agreement between a family's POOL half
  and its WAVE half, and the fix is in the manager's shared teardown that both halves read -- so
  both moved together and the pin held. Only a fix to ONE SIDE of an agreement pin breaks it.
  The corollary is the part to carry forward: those suites are an internal-consistency instrument,
  not an absolute-value one, so **they would not have caught the drift either**, and a green
  equivalence run is not evidence that a ledger quantity is correct. Ticket 02 needs
  `run_digest.py` DB-row neutrality, not just the 4-second suite.

- **Ticket 01 is RESOLVED and landed pre-run** (2026-09-16). Symmetric decrement in both twins, 5
  guarding tests, non-vacuity proved by stashing the fix (2 fail by exactly 1036.8). Strict no-op
  on every non-cart arm, because `_sku_vol_product` is `{}` until a `wp` is passed. Gates 1-5 and
  7-10 green; gate 6 red with the message identical to the phase-0 baseline, so unchanged and not
  ours. Full record in `issues/01`.

- **`GLOBAL_POLICIES` is deleted; `LOCAL_POLICIES` is kept** (ticket 10). The standing yard
  REPLACED the global ranking rather than deferring it -- `YardTransit.__init__` hardcodes
  `global_policy='fifo'` (`Inbound/transit.py:364`) and `Inbound/priorities.py:11` confirms the
  registry "and its knob stay untouched and are simply unread in standing mode". So it cannot come
  back, and deleting `global_key` makes complexity vanish rather than reappear, which is the
  definition of a pass-through. `LOCAL_POLICIES` is a plausible future axis and cheap to keep.

- **The seven drift failures are NOT cleared first** (destination note above). Clearing them is a
  real effort that would push this arc past the launch; a captured per-gate baseline buys the same
  attribution safety in an hour. One exception: `.scratch/architecture-drift/issues/05` (the
  fingerprint disagreement) is owned before ticket 13 touches any shape store -- its own file calls
  it "the dangerous one of the seven" because one caller population thinks a tree is current while
  another thinks it is stale, with no error either way.

- **A frozen oracle must keep its own copy, so a body refactor must not move a signature**
  (2026-09-17, ticket 02 stage B). The three placement equivalence suites hold hand-copied
  `_impl` bodies and call `_ranked_assign_impl` / `_TravelBalancedPool(...)` with the same
  positional dicts. Changing a signature forces the oracle to be rewritten, which re-freezes it
  against the change it exists to check. The copies inside `Tests/` are the reference, not
  duplicates to delete. Consequence: ticket 02's add half binds `AisleLedger.over(...)` to the
  dicts each function was ALREADY handed instead of taking a ledger parameter, and the oracles
  stayed untouched. Carry this into ticket 04, whose fog item asks the same question.

- **Binding a ledger per pool was a cost class, not noise** (2026-09-17, ticket 02 stage B).
  The gain evaluator rebuilds the arm's policy per VIRTUAL placement at 12.59 pool opens per
  placement, so `AisleLedger.over()` at 2.00 us was 23.4% of a whole pool open. Spelling the
  signature out, deriving `bound` on read and binding one shared sentinel for the books a
  caller omits took it to 0.29 us / 4.2%. Recorded because the instinct was "ten empty dicts,
  who cares" and the instinct was wrong -- and because the measurement was cheap.

- **`complexity-round/17` folds in as ticket 05, by cross-reference rather than by moving it.**
  Grouping it with the placement tickets means one pass over `Assignment_Functions.py` and one
  re-run of the equivalence suite instead of three. The original file stays where it is so that
  effort's record of WHY A HEAP LOSES HERE -- the round's third reach for one and the first time it
  lost -- does not get orphaned.

## Fog

- ~~Whether the aisle ledger's priced quantities should be running sums or derived on read.~~
  **DECIDED 2026-09-17, running sums stay.** They are read inside the scoring expression on every
  candidate, in a loop whose width is the live aisle count and grows with the catalogue
  (k = 1.963 against the ladder knob), so derive-on-read turns an O(1) dict read into a sum over
  the aisle's members there. What makes the drift unrepresentable is not derive-on-read; it is
  that the add and the drop now live in one module with `reconcile()` between them.
- Whether ticket 20's two abandoned levels are repaired, symmetrised or deleted. `pick_load_sum`
  and `vol_sum` are seeded by `init_demand_state` and then maintained by only the two labour
  families, so they are stale for the whole run on the other fifteen arms. The persisted one has
  no reader outside its writer/loader pair, which is the `lift_sum` evidence exactly -- but
  `vol_sum` had no reader either, until `rank_cartlabor` grew one.
- Whether the knob registry (ticket 09) trips `.scratch/architecture-drift/issues/06`, the ratchet
  on hand-written run-tree path knowledge -- 11 of its recorded sites are in
  `Optimization/config/whatif_config.py`. It may go DOWN, which the ratchet permits.
- Whether ticket 04's collapse of the ranked `_impl` twins can keep the frozen oracles in
  `test_rank_cache_equivalence.py` meaningful, or whether those oracles have to be re-frozen. That
  suite is 7-13 min and pre-merge only.
- Whether ticket 12's `Frame` table can express `SiteContext`'s one honest override as data, or
  whether one subclass survives.

## Out of scope

- Launching the phase-2 campaign. It is staged, committed and idle -- `PHASE2_PAIRS`,
  `PHASE2_STAFFING_PIN`, `PHASE2_THRESHOLD_DAYS` all carry real values, `get_spec('inbound_policies')`
  builds rather than refuses, and no run root exists. The launch is a detached multi-hour driver
  plus a publish loop, not a decision, and it belongs to its own effort.
- Re-litigating ADR-0001 through ADR-0006. Ticket 08 IMPLEMENTS ADR-0005 more locally; ticket 17
  acts on ADR-0003's own "may become a knob later". Neither reopens a ruling.
- The six other `.scratch/` efforts. `architecture-drift` and `complexity-round` are the only ones
  still open, and this map touches each at exactly one named point.

- **The toy run is byte-reproducible, and that is what makes the rest of this arc provable**
  (2026-09-16). `smoketest.py --profile tiny` is 4.5 min at 12 workers and produces every
  structural feature of a real run: 2 cells, both channels, all 34 arms, the `_frozen` level and
  the what-if outputs -- 136 sim DBs across 4 leaves. Two HEAD-vs-HEAD runs digest **IDENTICAL**
  on the comparable surface (136 arms, 2,584 table digests), and `run_digest.py --self-test`
  detects planted 1e-9 float damage, so the instrument can fail. Every neutrality claim from here
  costs one 4.5-minute run rather than an argument. Checked the run log directly rather than
  trusting exit 0 (memory `pool-run-swallows-dead-arms`): zero Tracebacks, zero "produced no
  data", zero "Config stage: 0 job(s)" across 4,714 lines.

- **`FRAME_TABLE` and the frame LOADERS are two taxonomies, not one** (2026-09-16, ticket 12).
  The ticket proposed collapsing `FRAME_TABLE`, `PAIRED_KINDS` and the nine `requests.py`
  loaders into a single `Frame` record. They do not share a key space:
  `quantities.FRAME_TABLE` keys on the kinds a QUANTITY declares -- `task_mean`, `task_sum`,
  `trailer`, `carryover` -- while `requests.py` keys on the frames a CONTEXT caches --
  `task`, `yard`, `missed`, `carry`, `free_index`. Eight kinds against nine, overlapping but
  not equal. So the merge is two merges: `FRAME_TABLE` + `PAIRED_KINDS` into one
  `FrameKind` record (same taxonomy, and the pair that could silently misroute a quantity),
  and the nine loader bodies into one `_FrameSpec` table with one cache. Both landed; they
  are not the same table and should not be forced into one.

- **An identical code block in two classes defeats `replace(..., 1)`** (2026-09-16, ticket 12).
  `SiteContext` overrode `yard_df`/`drain_df` with bodies byte-identical to `EvalContext`'s,
  so a patch removing "the no-op overrides" removed the BASE class's real accessors instead.
  The full unit tier passed anyway -- 2652 green with `EvalContext.yard_df` gone -- which is
  itself the finding: the yard frame at config scope has no unit test, exactly the coverage
  gap ticket 12 named. It was caught by a test written for this ticket, not by the tier.
