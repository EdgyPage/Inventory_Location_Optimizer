# Build the empty-first top-up

Type: task
Status: resolved

Graduated from [Let a base-stock top-up reach the shelf](20-let-a-base-stock-top-up-reach-the-shelf.md),
decisions 1-7. AFK build. Skills: `codebase-design` (the put-away fallback chain as one
contract), `schema-maintainer` agent (the `sim_db` vintage and the semantic layer),
`test-developer` for the equivalence and the new behaviour, `code-reviewer`,
`memory-maintainer` to amend `empty-bin-preference-is-structural`. ADR-0003 is the decision
record; do not re-decide.

## Question

Not a decision: the build that makes ADR-0003 true. Put-away fills an empty bin first and
consolidates into the SKU's own bin only when no empty bin fits; the rework is priced to the
receiving crew and recorded.

What lands:

- **The fallback chain** in `Inventory_Manager._stock_per_unit`: when `place_one` returns
  None, try the SKU's own bins (most on-hand first; a unit that does not fit whole fills own bins
  to capacity in that order and sends the remainder on), THEN the repack / singleton rescues,
  THEN `pending`. The own-bin write is the fifth mutation site: `storage.quantity += n`,
  `_current_quantities` incremented, `_bin_sku` unchanged, `_cost_putaway` charged as for any
  placement; `Tests/architecture/test_bin_mutation_sites.py` allowlists it by name.
- **Inbound does the repacking.** When a rescue fires, the receiving crew's clock is charged per
  RESULTING pack at the dock's per-pack unload price (`Inbound/unload.py`, `Dock.charge`), no
  travel term, no new coefficient. The dock's `records` / `recv_seconds` see it.
- **The vintage.** One `sim_db` shape move through the pipeline (`--sync` before, `--accept`
  after, the outgoing id vetted by name): `work_events` event type `repack` (role receive,
  duration, qty, sku, rescued source); a `bin_placement` column for the bin's state at landing
  (empty / occupied); `batch_stats` flows `put_topups`, `recv_repacks`, `recv_repacked_packs`
  and level `free_bins`. `sim_semantics` entries for each; older vintages served by a `dataset`
  override (new columns zero, bin state empty).
- **The audit and the record.** The equilibrium audit reports own-bin share and free-index depth
  per day, reported not judged; the staffing record stamps `f_repack = 0`, provenance
  `assumed`, and the check flags any measured repack against it. Put-away expectation unchanged
  at one placement per top-up.
- **Pick-drain order.** `drain_sku` takes the smallest-quantity bin first, ties by location
  order.
- **Memory.** `empty-bin-preference-is-structural` amended: put-away prefers an empty bin, then
  the SKU's own bin, and the no-meeting rule held as an invariant only while reorder lots were
  pallet-sized.

Done when: a unit that fits no empty bin lands in its SKU's own bin before any rescue fires
(unit test); a run whose free index never exhausts is byte-identical to HEAD (equivalence
test over the store-only path); a forced rescue writes a `repack` work event charged to the
receiving crew and the three `batch_stats` flows count it; `Optimization.runschema.contract
--check`, `test_schema_identity`, `test_column_semantics`, `test_schema_compatibility` and
`test_bin_mutation_sites` are green; the memory is amended; `context/` anchors and the
architecture layer are re-synced by their maintainers.

## Answer

**LANDED 2026-09-08.** Put-away fills an empty bin first and consolidates into the SKU's own
bin only when no empty bin fits; the rework is priced to the receiving crew and recorded.
Every item on the ticket's list is built.

**THE ONE THING THAT DID NOT COMPOSE, and the decision that resolved it.** The ticket asked
for BOTH "`drain_sku` takes the smallest-quantity bin first" (decision 7) and "a run whose
free index never exhausts is byte-identical to HEAD". Measured before building: those are
incompatible. Right after INITIAL STOCKING -- no reorder fired, free index nowhere near dry --
800-1,145 of ~1,200 SKU-tiers already hold 2+ bins and 224-327 of them are drain-order
sensitive, across coverage 1-10 days; one SKU-tier held 199 bins. So the drain change moves
travel, tasks and depletion on essentially every run, gated on nothing. Decision 7's own
rationale ("when a SKU sits in two") does not survive that measurement either.

Put to the user with four options. **The user chose to land both and accept the break.** So:

- The byte-identity gate holds for the OWN-BIN RUNG, which is where it was always true, and
  is tested that way (`test_the_rung_never_fires_while_an_empty_bin_fits` plus its
  non-vacuity twin).
- **Absolute pick / travel / throughput numbers published before this commit are not
  comparable with ones after it.** Third comparability break in a fortnight, after the
  per-item charge (ADR-0001) and 23's aisle-split inflation. Memory:
  `drain-order-is-smallest-first`.
- Choosing that over the within-tier variant also **retires the forward-pick preference from
  the drain**: singletons no longer drain before pallets. The split still exists everywhere
  else; it no longer decides pick order. Nothing tested the old contract -- the one test that
  named it passed after the change because its fixture gave every bin the same quantity.

**What landed, against the ticket's list.**

- **The fallback chain.** `_stock_per_unit` gains rung 0 ahead of both rescues:
  `_top_up_own_bins` fills the SKU's own bins FULLEST FIRST (ties by `location`) to capacity,
  and a partial fill sends the remainder back through the chain from the top as one unit, so
  a smaller unit can still find an empty bin. Split into a decider (`_top_up_own_bins`) and a
  single per-bin commit point (`_execute_topup`) so an observer has something to wrap --
  `BinRecorder` needs it, and so does the replay oracle. Capacity comes from
  `inventory_common.own_bin_room`, measured against the BIN'S OWN TIER; singleton bins are
  measured against `Singleton` directly because `_max_qty_fitting_size` falls back to the
  pallet tables for them and would overstate the room by the whole difference between the
  families.
- **Inbound does the repacking.** Both rescues call `_charge_repack`: one
  `dock.unload_seconds`-priced act per RESULTING pack, on the receiving crew's clock, into
  `dock.repacks`, accruing `recv_seconds`. No travel term, no new coefficient. The counters
  increment even with NO dock bound, so a dockless run still reports its rework.
- **The vintage.** `sim_db` `798778f4fae1` -> `02a78953886c` through the pipeline (`--sync`
  before, `--accept` after, the outgoing id named in the commit-window comment).
  `bin_placement.bin_state` ('empty' | 'occupied'), `batch_stats.put_topups` /
  `recv_repacks` / `recv_repacked_packs` / `free_bins`, all six semantic entries, and
  `work_events.event_type = 'repack'` with `role='receive'` (`repack_rows` threads an
  `event_type` through `recv_rows`; the two streams share `(batch_id, seq)` so repacks
  continue past the unload rows). Older vintages are served by the `batch_frame` OPTIONAL
  set and a column guard in `load_bin_placements` -- a pure column addition needs no
  `override`, and 0 / 'empty' is the TRUE value there rather than a stand-in.
- **The audit and the record.** A fifth equilibrium clause, `rework`: JUDGED on the repack
  (fails against the record's `f_repack`), REPORTED on own-bin share and free-index depth per
  day. The asymmetry is deliberate -- a repack contradicts something the record asserts,
  while the other two are new instruments with no observed steady state, so a threshold now
  would be invented. `staffing.derive` stamps `f_repack = 0`, provenance `assumed`, with
  `F_REPACK` / `--f-repack` behind it.
- **Pick-drain order.** Smallest-on-hand first, ties by `location`, across both tiers as ONE
  ordering. The manager-less fallback in `Task.from_batch` now CALLS `drain_sku` instead of
  re-implementing the walk inline, so the two branches cannot drift again.
- **Memory.** `empty-bin-preference-is-structural` amended; `drain-order-is-smallest-first`
  written.

**Three things the ticket did not foresee.**

1. **Every fold over the bin log needed teaching, and each under-counted SILENTLY until it
   was.** A PLACE row used to mean "the bin's contents are now this"; a top-up means "add
   this". Three folds: `Visualization/precompute._bin_spans` (a top-up closes the running
   span and reopens one carrying `remaining + qty`, because a span holds a single `qty0` by
   construction), `Visualization/readers/base._state_from_log` (the live path, which also
   needed a column-level vintage check, `_has_col`), and the test harness's independent
   oracle in `Tests/bench/bin_log_harness.py`. Fourteen integration tests caught it.
2. **`put_topups` counts BINS TOUCHED, not calls.** The audit divides it by
   `reorder_placements`, which is per bin, so a per-call count would understate the own-bin
   share exactly when a unit was split across the most bins.
3. **A staffing input has a CLI seam.** `STAFFING_KEYS` are read off argparse, so `f_repack`
   in the spec without `--f-repack` made both preflight canaries die with
   `AttributeError: 'Namespace' object has no attribute 'f_repack'`. The memory
   `config-knob-has-five-seams` names four seams; this is argparse, and the canary is what
   caught it.

**Evidence the rung is not inert** (the trap `empty-bin-preference-is-structural` warns
about). On a normal run it correctly never fires: the planner sizes the warehouse to the
declared levels, so the free index goes 6,189 -> 9,000 free bins over 15 batches with
`put_topups = 0`. Forcing the condition it exists for -- every free bin removed from the
index, then reorders offered for SKUs that already hold bins -- it fires: 5 top-ups, 7 items
landed, 5 `bin_placement` rows all `bin_state='occupied'`, and the 54 units whose own bins had
no room stayed queued rather than vanishing.

**Verification.**

- **1,823 unit** (1,798 baseline + 25 new in `Tests/unit/test_empty_first_topup.py`).
  **394 integration** and **31 e2e** green.
- **27 source mutations, 27 caught** -- two only after tests were added for them
  (`_reorder_placements += 0` in `_execute_topup`, and the bookkeeping a top-up must NOT do:
  Sigma f.D, `space_timeline.fill`, re-registering the bin). The mutation that makes the
  equivalence test non-vacuous is moving the rung AHEAD of `place_one`.
- **Both preflight canaries ran end to end** (87s + 27s) and the run-tree shape is unchanged
  at `5c9bc35db55b` -- a real simulation exercised the whole build.
- Nine gates green. `test_column_semantics`, `test_schema_identity`,
  `test_schema_compatibility`'s vintage tests and `test_bin_mutation_sites`' allowlist tests
  all pass.

**Four PRE-EXISTING failures found and left alone**, each verified on a clean tree at
`1bef426f` by stashing, and each spawned as its own task rather than folded into this commit:
`test_bin_mutation_sites::test_the_dead_site_is_still_dead` (`Inbound/trailer.py` now calls
the "dead" `add_from_bin`, so the log may be lossy -- plus the scan walks stale
`.claude/worktrees/`); three schema/run-tree architecture gates (`_shift_day_select`
undeclared, two new hand-written run-tree paths, the worktree scan); and
`Tests/calltree::test_minlabor_cache_matches_frozen_oracle`, where the production minlabor
pool and its oracle put the same units in different bins -- placement/pick/reorder counts
agree exactly, only the end-state hash differs. That last one is a fourth finding from the
blind spot `hand-run-test-tiers-rot-silently` names.

### What the code review changed

`code-reviewer` found three CRITICAL defects, all real, all fixed before the commit. Two of
them make the point that a feature can be fully built and still not work:

1. **The rework clause was VACUOUS through its only production caller.**
   `throughput/audit.py` builds `batch_rows` from `Performance_Evaluations/common/frames._bdf`,
   which assembles an EXPLICIT column dict -- and I had added the four new columns to the DDL,
   the dataclass, the insert, the loader and the semantic layer, but not to that dict. So
   `_rework_clause` read its defaults for every row and a run that repacked 9 packs rendered
   `rework=ok (0 pack(s) repacked ...)`. The one instrument ADR-0003 built to make rework loud
   reported a clean bill on exactly the run it exists for. `equilibrium.check` (the DB-direct
   path the unit tests use) worked the whole time, which is why nothing caught it. Now
   reproduced as a check: the same 9-pack run FAILS the clause through the audit's own frame.
2. **A FOURTH bin-log fold.** `Diagnostics/replay_run.occupied_from_bin_log` also did
   `qty[loc] = r['qty']  # a fill REPLACES the bin's contents`. The answer above named three
   folds and said "and so does the replay oracle" -- and then missed this one. It advertises
   itself as EXACT with an empty caveat, so a bin topped from 5 to 7 would be recorded as 2,
   deleted by the next 3-unit pick, and dropped from the aisle occupancy curve while still
   full. Nothing tests it.
3. **`storage.quantity += n` left the unit's cached geometry stale, in the UNSAFE direction.**
   A `StorageUnit` caches `_height`/`_width`/`_length`/`_stack_axis` and `Pallet.storage_size`
   at construction and nothing recomputes them. Picks mutate quantity DOWNWARD, where a
   stale-large cache is merely conservative; this is the first upward mutation. Measured: a
   42x12x46 order at qty 1 caches `'small'`, and at qty 4 is really `'extra_large'`.
   `requeue_bin` re-admits the SAME object and `_candidates_raw` reads `unit.storage_size`, so
   an evicted top-up would be offered small bins and placed in one with no fit check -- a
   warehouse that cannot physically exist. Fixed with `bin_.storage._fit(order)` after the
   increment (cannot raise: `n <= own_bin_room`, which caps on the bin's own tier).

Also taken from the review: `free_bins` is now UNKNOWN rather than 0 on a pre-ADR vintage (a
run that had free bins and never counted them is not a run with none, and a floor of 0 reads as
an exhausted index -- the three flows keep their zero-fill, which IS true for them); the budget
is charged per BIN TOUCHED, matching `_reorder_placements` and the `bin_placement` rows;
`_execute_topup` asserts bin OWNERSHIP, since three observers now rebind it; a nonzero
`f_repack` RAISES rather than comparing a per-pack rate to a pack count; and the rung's
docstring records that it crosses the forward-pick split, which nothing else does.

**One claim in this answer was stated against the wrong condition, and is corrected in the
code.** "A no-op in any run whose free index never exhausts" is a GLOBAL reading of a
PER-BUCKET condition: the rung fires when `place_one` returns None, which is the free index
empty for the unit's own BinKey, reachable in a run with plenty of free bins elsewhere. The
checkable claim is "a no-op in any run where every unit finds an empty bin". The rung also
pre-empts the rescues whenever the SKU has room, so an arm that fires a rescue is not
byte-identical to HEAD even with a deep index.

**Measured, not asserted** (the review asked): the new drain key costs ~1.9x the old
location-order walk -- 4,000 SKUs holding 137,552 bins (34.4 per SKU), 22.9 ms -> 42.8 ms for
one drain each. A batch touches ~15% of the catalogue, so roughly +3 ms per batch at this size
against a makespan in minutes. It scales with per-SKU bin multiplicity, which the deep ladder
already flags as growing, so it needs re-measuring on a much larger catalogue.
