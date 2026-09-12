# Build the site crews' cross-leaf checks

Type: task
Status: resolved
Blocked by: 24

AFK. Graduated from the map's "remaining builds" fog by the execution override (map Notes).
[Reconcile the site crews' work stream](15-reconcile-the-site-crews-work-stream.md) settled the
rule and named the ordering itself: the second `SEMANTIC_USES` family is **behind 03's site-DB
family registration**, because `semantics_for` RAISES on an unregistered family. That
registration is part of [Build the site analysis stage](24-build-the-site-analysis-stage.md),
which is why this is blocked on it rather than on the coordinator directly.

## Question

Build 15's held half: the two site-scope uid clauses (section 1), the site-total closure against
the coordinator's own accumulator (section 2), constant-C's cross-leaf `C_store == C_ful` clause
(section 3 item 4), the sibling `reconcile_pair` entry with its `coupled`-marker grouping and its
FAIL on an absent site DB (section 5), the second `SEMANTIC_USES` family (section 6), and the
per-batch site-total table in the site DB (section 7).

**The uid clauses have a fact now that 15 could only assume.**
[Build the site put-away pool](19-build-the-site-putaway-pool.md) made the put uid block start at
`max(k_pickers)` over both channels, so a putter's `actor_uid` means the SAME person in both
leaves' DBs — which is precisely what a cross-leaf, role-qualified disjointness check needs. It
also left the smaller leaf a deliberate GAP in its uid space, so any clause that assumes a dense
uid range is wrong by construction. Check role-qualified identity, never density.

**`C_store == C_ful` IS RETIRED, and what replaces it is stronger.**
[Decide the site dock's unload price](27-decide-the-site-docks-unload-price.md) settled it: the
unload price is a statement about the MERCHANDISE, so the site dock holds a price LIST keyed by
the unloaded unit's regime and the equality is false BY CONSTRUCTION. Do not write it, and do not
write a weakened version of it. 15 called it "the site dock's sharpest falsifier" and it was
sharp about the wrong thing — re-read under 27 it was a claim about PLUMBING (that two leaves
resolved one price list) dressed as a claim about physics.

**What replaces it is check 6 in a two-constant form**, which 15's own machinery already
supports: re-price each receiving row against ITS OWN regime's constant, so the check becomes two
exact equalities instead of one and FAILS if a row is ever charged at the other channel's rate —
the defect the equality was reaching for and could not see.

**The precondition, and it is this ticket's to establish BEFORE writing the check:** a receive
row's regime has to be resolvable from the sim DB alone. `work_events` at the `receive` role may
or may not carry it. If it does not, that is a COLUMN, and a column is a schema change that rides
the pipeline (`--sync` before the DDL edit, `--accept` after), never a consumer edit. Establish it
first — a check that silently cannot resolve the regime falls back to one constant and passes.

## Amendment (2026-09-12, from ticket 24)

**THE PRECONDITION IS ESTABLISHED — no schema change is needed, and the reason matters.** A
receive row's regime IS resolvable from the sim DB alone, but not the way this ticket assumed.
Under coupling the coordinator partitions the dock's records **by SKU before the driver stamps
anything**, so every `receive` and `repack` row lands in its OWNING channel's `work_events`, and
`simulation_runs.channel` names that channel.

**So do NOT write check 6 as "read the regime off the row".** The two-constant form is `C_store`
over the STORE LEAF's rows and `C_ful` over the FULFILLMENT LEAF's, each re-priced against its own
regime's entry in the dock's price list. **One DB does not hold both regimes' rows** — it cannot,
by construction — and a check written on that assumption finds one constant, re-prices every row
against it, and passes.

**15's other precondition is built too:** each leaf's `batch_stats` receiving scalars are now its
own share (the dock is partitioned back by SKU and the shares are CLOSED against its own totals),
so the site-total closure check has both sides to compare.

**One gap 24 names rather than leaves silent:** `equilibrium_report`'s accumulation loop is not
unit-tested — `_site_verdicts` is, but the loop that feeds it needs a run tree with a staffing
record and a closed ledger. If this ticket's site-total closure runs through that loop, it is
testing two things at once and only one of them is pinned.

**And the trap 24 fell into, which this ticket sits one layer above.** `SiteContext` left `_caps`
uninitialised; because it SUBCLASSES `EvalContext` the era gate found the inherited
`capabilities()` and called it, three of four evaluations raised inside the driver's swallow, and
the tally reported a GRANT while nothing rendered. Memory `a-grant-is-not-an-output` is about
exactly this and it still caught nobody out until the stage was driven end to end on a real
coupled tree. **A subclass is not exempt from a duck-typed gate**, and the `[access]` summary
reports INPUTS — only the `[render]` run summary says whether anything was written. Drive whatever
you build on a real coupled tree before believing a tally.

## What proves it

- **The site-total closure FAILS on a planted leak**, not merely passes on a clean run — memory
  `conservation-ledger-is-bin-only` is the shape: a check that can only pass proves nothing.
- **The re-pricing check re-prices ROWS.** Memory `equilibrium-check-two-traps`: an average was
  7x off, so the constant is compared against re-priced rows, never against a mean.
- **`_TOL`'s form is decided here or explicitly deferred.** The map's fog carries
  `receiving_report`'s absolute 1e-6 s tolerance against sums that grow with row count — a
  relative error of 3e-13 reported as a FAIL on four archived arms today, and it gets sharper
  under coupling where a site total is the sum of two leaves'. If the right form is clear once
  the site totals are in front of you, fix it; if not, say so and leave it in the fog.
- Nine verifiers; `Tests/architecture` baselined against a `git archive` copy (five known reds).

## Answer

**BUILT** — the site crews are falsifiable across the pair. `receiving_report.reconcile_pair`
is a sibling of `reconcile` that takes one coupled arm pair's two leaf DBs and its site DB and
makes four claims none of which is stateable from one file; `site_receiving` is a new declared
table carrying the site dock's OWN per-batch counters, which is what gives the closure a
second side to compare against; and `_TOL` is **DECIDED**, not graduated.

### What the checks are, and what each one sees that nothing else can

**A. No uid is a site crew member in one leaf and a per-channel role in the other.** Site-dock
04's defect exactly: a leaf that builds its put crew off its OWN pick cursor puts the smaller
channel's putter inside the larger channel's picker block, and every per-actor rollup then
merges two people with no symptom anywhere. **B. Every site-role uid sits above BOTH leaves'
pick blocks** — a FLOOR, never set equality, because the smaller leaf is left a deliberate uid
GAP (19) and an idle putter writes no rows. Which roles are SITE roles is a constant in the
scope the check runs in — no roster artifact, no magic uid range — and the partition is
asserted TOTAL, so a fourth role added to `Warehouse/operations/roles.py` cannot be silently
unclassified.

**C. The site total closes against the two leaves' rows, PER BATCH.** The dock's own seconds
accrue at `Dock.charge`; the leaves' rows carry per-row durations stamped from drained
records. Different code, so agreement is evidence. The defect it exists for is the one no
per-leaf check can see: a run-end writer that misses the final flush takes a batch's
`work_events` AND its `batch_stats` with it, so check 1 compares two equally-short surfaces on
each leaf and PASSES on both. Per batch and not a run total, because the drain IS the boundary
— and a leak in one batch offset by a double count in another passes a total, which is a test
in the suite. The COUNT closure filters `event_type='receive'`: a repack is receiving labour
that no `unloaded` counter counts, so the seconds side includes repacks and the count side
cannot.

**D. The two constants are per REGIME** — check 6's cross-leaf half, in the two-constant form
27 chose when it retired `C_store == C_ful`. Three clauses, because the defect has three
shapes and the per-leaf spread sees only one of them:

- each leaf's own residuals collapse to ONE constant (check 6, restated at pair scope so the
  pair verdict does not depend on a reader having run the per-leaf one);
- NO ROW of either leaf prices at the OTHER regime's constant — the exact second equality,
  which NAMES a contaminated row where a spread of 2.5 s says nothing about what 2.5 s is;
- and **if the two leaves price their PICKING differently, they must price their UNLOADING
  differently too.** `sku_scores.labor_cost - handle_var` is `pick_intercept + pick_per_item`
  (`Order.compute_labor_cost`), one constant per run, off the same file; `C` is a positive
  linear map of that same coefficient pair through site-wide scales. **That is the only clause
  that can see a WHOLE leaf charged at the other channel's rate** — a uniformly mispriced leaf
  has a spread of ZERO, a cross-price count of ZERO, and check 6 green on both DBs.

Both cross-leaf clauses go INACTIVE **and say so** when the two constants coincide: two
channels may legitimately share a pick config, and from two sim DBs "the same config" and "one
regime resolved to the other" are the same reading. Stating that is the honest version; a
silent pass there would be the contiguity check's sin rebuilt.

### The precondition, confirmed and then sharpened

24's amendment holds: a receive row's regime is resolvable from the sim DB alone, **no schema
change and no `--sync` for it**, because the coordinator partitions by SKU before the driver
stamps anything and `simulation_runs.channel` names the owning channel. The check is written
as `C_store` over the store leaf and `C_ful` over the fulfillment leaf, never as "read the
regime off the row", and the channel is read from that column rather than from the directory
the file sits in.

**Section 6 dissolves rather than being satisfied.** 15 ordered the second `SEMANTIC_USES`
family behind 03's site-DB family registration because `semantics_for` raises on an
unregistered one. There is no second family: 24 made the site DB the `sim_db` family precisely
so every broker binds it with no new loader, so the site reads are `sim_db` reads and
`SEMANTIC_USES` grows columns, not a key. The ordering constraint is discharged, not met.

### The site total: a new table, and why it is not `batch_stats`

`site_receiving` — `(run_id, batch, recv_depth, recv_unloaded, recv_cut, recv_seconds)`,
written only into a coupled unit's site inbound DB and EMPTY in every other sim DB. The
coordinator parks the row in `_partition`, **after** the per-channel closure assertions, so a
site total the channels cannot account for is a crash rather than a row; `_SiteDock.collect`
drains it every batch and REFUSES a day index that is not the batch it is collecting (one
batch IS one site day — the pool refuses every other grid, and this is the one place that
promise is read rather than restated); `save_site_inbound` writes it once at run end.

Putting the four scalars in the site DB's existing `batch_stats` was the cheaper option and is
rejected: a `batch_stats` row whose `duration`, `num_tasks` and `total_items` are zero is a
batch that did not happen, and this repo's whole idiom is that an absent model and a
zero-filled one are different claims. `INSERT OR REPLACE` was rejected for the same class of
reason — one producer writing once means a duplicate batch is two site days under one index,
not a re-flush, and `carryover-two-producers-one-key` is what silently keeping the second
looks like. The insert RAISES.

**Both pipelines, in the right order.** `schema_report --sync` before the DDL edit and
`--accept` after: `sim_db` moved `b87cfbb8d041 -> c6bacfdb5c77`, with the commit-window comment
written by hand and the outgoing id adopted. No optional-fill or per-vintage override is owed —
this is a TABLE, not a column, and a consumer asks whether it exists (`_has`) exactly as it
already asks about `sku_scores`; no earlier vintage is missing data, it simply had no site. The
run tree rode `contract --write` (`341e1422457c`, `site_inbound_db.tables` grew) plus the full
preflight, **both canaries `tree shape UNCHANGED`**, run again after the last source edit.

### `_TOL` IS DECIDED: scaled for sums, absolute for spreads

The map's fog closes. `_tol_for(*magnitudes)` is `max(1e-6, 1e-9 x max|magnitude|)` and is
applied ONLY where a SUM is compared against a SUM (check 1, and the site closure). A spread
does not accumulate — its error is bounded by one row's rounding however many rows there are —
so check 6 keeps the flat `_TOL`, and the e2e test now pins the ASYMMETRY: a move of `2 x _TOL`
breaks check 6 and leaves check 1 green, while a move of `2 x _tol_for(sum)` breaks both.

**What settles the objection** (a relative tolerance hides a small real discrepancy on a large
arm — which is the failure check 1 exists for) is that the discrepancy it hunts has a PHYSICAL
LOWER BOUND. Every defect it was written for moves the sum by at least ONE UNLOAD, and the
smallest unload the archive actually prices is 5.1 s. On the 505,177-row arm the map names,
`1e-9 x 4,177,040.9 s` is **4.2e-3 s — under a thousandth of the smallest thing that can
really go missing**, and three orders of magnitude above the 1.3e-6 s of float re-association
that reddens four archived arms today.

**The upper margin is EMPIRICAL, not a bound, and the review made that explicit.** Worst-case
float summation error is `N x eps x |sum|` — at 505,177 rows ~1.1e-10 relative, only ~9x under
the floor rather than the ~3e4x the OBSERVED 3e-13 suggests. An arm an order of magnitude
larger would put the worst case above `_REL_TOL` and the false FAIL would return; at that
point the relative term wants scaling by row count, not raising. Recorded on `_tol_for`.

**Not re-verified against those four arms**: `COMPARISON_OUTPUT_DIR` is not mounted in this
checkout (memory `results-drive-location` — the archive is on the cold drive), so the decision
is pinned by arithmetic in a test plus a `reconcile`-level test on a real file that shows
1.3e-6 s of drift over a 4.18M s arm PASSING while a 7.6 s loss on the same arm FAILS.

### Where the checks run, and the one place 15's rule had to be sharpened

A sibling `reconcile_pair`, with `main()` grouping into pairs when the run layout carries the
`coupled` marker. Widening `reconcile` with an optional peer stays rejected. An **uncoupled**
run reports `0 coupled pair(s)` — the third idiom, and the one that keeps the tool from lying
by omission across the whole archive.

**15 section 5's "an absent site DB on a coupled pair is FAIL" could not be built as written.**
06 couples every cell INCLUDING its inbound-OFF pole, and 24's `_build_site_dock` returns None
there — no trailer pipeline, no dock, no site DB. An unconditional FAIL would have reddened
half the campaign. So the rule is conditioned on the evidence: a pair whose leaves recorded
RECEIVING and has no site DB FAILs (21's parked rows never reaching an artifact — the defect 15
wrote it for, still reachable and tested both ways); a pair whose leaves recorded none is the
inbound-off pole and is a PASS with `active=False`.

**The closure does NOT run through `equilibrium_report`'s untested accumulation loop** — 24
flagged that as this ticket's natural neighbourhood. 15 section 2 put the closure in
`receiving_report` and section 6 rejected the alternative by name, so the two halves of one
claim stay in one tool and nothing here touches that loop. It is still untested; it is still
not this ticket's.

### What proves it

- **The 12-batch coupled probe, driven end to end on a real tree**, outside the suite because
  the e2e fixture is 3 batches and produces ONE receive row in ONE leaf (19's finding,
  unchanged — reported there rather than hidden). Clean pair: **all eight clauses ACTIVE and
  green**, `C_store = 7.599999999999994 s` and `C_ful = 5.1 s` — the archive's own two
  constants, which 17 derived independently from each channel's pick config — pick constants
  15.5 / 10.5, and the closure at **62,278.86347640174 s (leaves) vs 62,278.86347640173 s
  (site) over 12 batch rows**. Measured twice, on the first build and again on the final code.
- **Six planted defects, six FAILs, each on the right clause** and each localised: one lost
  receive row (batch 5 named); a whole batch missing from BOTH leaves (batch 9 — the defect
  per-leaf check 1 passes over); one store row at the fulfillment rate
  (`rows_priced_at_the_other_regime: {store: 1}`); **the WHOLE fulfillment leaf at the store
  rate, where `unload_price_is_constant_per_leaf` stays TRUE** and only clause D's third
  sub-clause fires; a putter inside the other leaf's picker block; and no site DB at all.
- **Uncoupled byte-identity, measured on DB ROWS**: one standing-yard store arm (20 batches,
  250 SKUs, 3 doors, a 600 s receiving day, split allocation — the corner where the yard binds)
  run in this tree and in a `git archive HEAD | tar -x` copy. **149,271 rows across 15
  non-empty tables, ZERO differing.** Evidence COUNTED first (76 receive rows, 76 put rows, 22
  trailers, 20 yard drains) because an empty table diffs clean, and the three tables empty in
  both are named rather than counted. **With an ORACLE**: a 1.000001x factor on `unload_cost`
  in the HEAD copy moves **96 rows**, so the diff can fail. The only masked columns are
  `simulation_runs.created` and `sim_schema_id` — the latter is the stamp this ticket's own
  schema change moves, and it is the ONLY thing that moves on an uncoupled run. Figures were
  deliberately NOT diffed (23's finding).
- **47 guard mutations, 47 caught.** Every mutation asserts its ANCHOR COUNT before it is
  applied, so a mutation that matched nothing could not read as a caught one — six read as
  ANCHOR MISS on the first pass (CRLF, memory `head-copy-via-git-archive`) and were fixed
  rather than counted. **Five SURVIVED across the passes**, and every one was a real test
  gap, closed with a new test rather than by weakening a mutation. One of those survivors is
  where the campaign-scale arm-pair defect below was found — the mutation, not a reading.
- `Tests/unit` + `Tests/integration` **2,778 passed, 1 skipped** (three vintage fixtures
  amended, below); `Tests/e2e/test_receiving_e2e.py` **10 passed**;
  `Tests/e2e/test_coupled_unit_e2e.py` gains
  `test_a_coupled_pair_reconciles_across_both_leaves_and_the_site_dock`. **51 new test
  functions** across two new files plus four extended.
- **Nine verifiers green**; both preflight canaries `tree shape UNCHANGED` on the final code.
- **`Tests/architecture` at the known baseline: FIVE red, the same five by NAME** — 5 failed,
  378 passed, 1 skipped. FOUR are shared with a `git archive HEAD` copy (which reds EIGHT: the
  same four plus `test_architecture_html`, `test_docref_guard`, `test_memory_sync` and
  `test_path_guard`, all four copy-only artefacts of a tree with no git and no worktrees), and
  the fifth is `test_every_consumer_that_declares_requirements_is_validated_here`, the
  `.claude/worktrees/`-only one memory `arch-tier-is-red-on-head` records. **Zero new reds**,
  and the run-tree ratchet's per-file list is byte-for-byte the copy's — see the findings.

### The review round, and what it found

Run before the commit, and it earned its place: **seven real defects**, one of them the
sharpest thing on this ticket.

- **THE PAIR-SCOPE FAIL WAS UNREACHABLE ON A REAL CAMPAIGN.** The pairing is read off the
  site DBs' own filenames — so an arm pair whose `inbound_*.db` never reached disk is not
  ENUMERATED. On a 34-arm cell the other 33 files name 33 healthy pairs, the tool prints
  `33 coupled pair(s) … 0 FAILED` and exits 0, and the one pair that lost its site scope is
  neither FAIL nor MISSING but absent. 15 section 5's whole rule fired on nothing but the
  inbound-off pole, where it is correctly suppressed. The arms each leaf RAN are on disk
  either way, so every arm no site-DB stem names is now swept up and takes the same rule.
- **A narrower witness than the tool's own definition of "received", twice.** Both the
  dockless rule and `reconcile_pair`'s gate counted `work_events` rows alone, while
  `reconcile` defines activity as `n_ev or unloaded or cut`. The completest form of the
  defect they exist for is a drain that reached NO artifact — no rows, no site DB, and
  `batch_stats.recv_unloaded` the only surface that still remembers — which a row-only
  witness reads as the inbound-off pole and PASSES.
- **Two runs in one file, on both sides.** The site side was last-wins across runs while the
  leaf side summed; both now REFUSE, because `find_run` answers every other query in the repo
  from the OLDEST run and merging an abandoned run into a live one is not a number either.
  That refusal subsumed the two-channel guard, which became dead and was replaced by the
  NULL-channel one it had been hiding.
- **`pick_constant_spread` was computed, documented as reported, and read by nothing** — so
  the whole-leaf clause leant on two MINIMA being constants without checking it. It is now
  the clause's gate.
- **`pick_intercept` was read and never printed**, while the clause's own comment promised a
  reader could use it to tell which branch of its implication they were in.
- **A non-finite sum passes every comparison**, and it is REACHABLE — not through NaN (SQLite
  stores that as NULL) but through INFINITY, which round-trips: `_tol_for(inf)` is itself
  `inf`, so `abs(x - inf) > inf` is False for any finite other side.
- **The cross-price clause was ill-posed in a one-tolerance band** (`_TOL < |Ca - Cb| <=
  2*_TOL` makes every CLEAN row of one leaf fall within `_TOL` of the other's constant).
  Unreachable on real data; now excluded by construction rather than by luck.

Three more landed as corrections rather than defects: the duplicated arm-pair split moved to
`RunTree.arm_pair_halves` beside `arm_pair_of` (one home for one inversion — and the
duplication was proven real by both copies having the same bug); the dockless loop stopped
re-reconciling leaf DBs the first pass had already read; and `_dominant_constant`'s bucket
width is DERIVED from `_TOL` rather than a literal 6.

**Two corrections to claims this answer would otherwise have overstated.** `_REL_TOL`'s
upper margin is EMPIRICAL, not a bound: worst-case float summation error is `N x eps x |sum|`,
which at 505,177 rows is ~1.1e-10 relative — only ~9x under the 1e-9 floor rather than the
~3e4x the OBSERVED 3e-13 suggests, so an arm an order of magnitude larger would need the
relative term scaled by row count. And the closure's unique coverage is narrower than "it
sees a pack lost at site level": `_partition` already ASSERTS the leaves' seconds sum to the
dock's and RAISES, so a routing defect kills the run before a row is written. What is left
for the DB-level check is everything that goes wrong AFTER the partition — which is the class
no per-leaf check can see either, so it still earns its keep. Both are now in the docstrings.

### Findings

- **THE DEFECT A MUTATION FOUND, AND IT WAS IN 24's CODE TOO.** `_split_arm_pair` first
  resolved which leaf owns which half of `<store_arm>__<fulfillment_arm>` by MEMBERSHIP — "the
  channel that holds exactly one of the two names". Both channels draw restock rules from ONE
  17-rule vocabulary, so on a real campaign each leaf holds arms named like BOTH halves;
  membership then matches neither leaf, every pair is skipped, and the pass reports `0 coupled
  pair(s)` over a run that has many. A one-arm probe cannot see it —
  `a-grant-is-not-an-output` in its purest form. **`run_analysis._site_jobs` had the same logic
  and the same hole**, which would have run 24's whole site stage on nothing the first time a
  campaign cell carried more than one arm per channel; it is fixed here as `_arm_pair_halves`,
  with the planted case as a test. Both now resolve POSITIONALLY against the run layout's
  declared `channels` — the order `workunits._site_db_path` BUILT the stem in — and use
  membership only to decide whether the positionally-named arm was actually run. Neither falls
  back when that order is absent: a fallback has to guess which half is which channel, and both
  refusal tests use the SWAPPED fixture so an alphabetical guess would resolve them (a fixture
  where the guess fails anyway would pass over the fallback).
- **`min` is the wrong summary of a contaminated leaf's residuals, and the test found it.**
  `reconcile` reports C as `min(residuals)`, which is right there (a clean spread is ~1e-15).
  Reused at pair scope it is a hole: ONE store row charged at the fulfillment rate makes `min`
  return the FULFILLMENT constant, the two leaves then measure the same C, and the cross-regime
  clause goes INACTIVE on exactly the defect it exists to name. `_dominant_constant` is the
  mode over residuals bucketed at the tolerance, and it returns a real residual rather than the
  rounded key.
- **"The site total is missing" and "this vintage never recorded one" are different claims**,
  and conflating them reddens every site DB 24's build wrote. `_site_totals` returns
  `answerable` for that split; without it the vintage case took 15's FAIL.
- **Three vintage fixtures had to drop the new table.** `test_era_wiring` and
  `test_free_index_by_bucket` fake an outgoing vintage by stripping the CURRENT DDL back, so a
  table added since is one they must strip too — `dataset.bind(verify=True)` re-derives the
  shape and raises rather than trusting the stamp, which is the recipe's own
  checkpoint-and-assert step working (memory
  `immutable-readers-see-only-the-checkpointed-file`).
- **The run-tree ratchet counts PROSE, for the third time** (22 and 24 found it before). Five
  new `(file, token)` occurrences appeared from docstrings alone — `_site/` and
  `run_layout.json` written out in comments — in files that hand-join no path at all: every
  consumer here resolves through `rt.site_inbound_dbs` / `rt.arm_pair_of` / `rt.layout`. All
  five were REWORDED away rather than baselined, and the per-file sweep is now byte-for-byte
  the HEAD copy's.
- **`--catalog-merge` seeded the `purpose` from a docstring FRAGMENT for the FIFTH time** (11,
  18, 19, 24, now 25), on both new test files. Filled by hand, on one line each.
- **The arch layer's `arch-synced-commit` marker has been stale since ticket 21** — 24 ran the
  chain and committed the regenerated layer without bumping it, so `context/INDEX.md` still
  named 68b262de across two tickets. Bumped here with this ticket's source commit, on 11's
  precedent (the same rot, found the same way).
- **`Tests/calltree` is RED, and it is NOT this diff's.**
  `test_rank_cache_equivalence::test_minlabor_cache_matches_frozen_oracle` fails on a
  placement-row digest with every count identical — the frozen oracle is pinned at `831571f`
  and the pools refactor re-derives placement, which memory `placement-pools-and-the-audit-
  point` predicts exactly (`a033aff` was the last byte-identical placement commit). Nothing in
  this diff touches placement, ranking or assignment. That tier is in no gate
  (`hand-run-test-tiers-rot-silently`), which is why nothing flagged it when it rotted. It
  does not block this map, and it wants its own ticket: a frozen oracle that has silently
  diverged is a dead check, and the file's own docstring says the counts-match / hash-differs
  shape is what it exists to catch.
- **The declared channel ORDER is a two-place agreement with no third witness**, and nothing
  enforces it: `run_simulation` passes the list to `write_run_layout`, and
  `workunits._prepare_site_run` zips its leaves in the order `_channel_runs_for` produced. The
  stem is built from the second; `arm_pair_halves` reads the first. Emit them differently and
  the mis-pairing is silent ONLY where both channels ran arms with the same names — every
  other shape fails to resolve, because membership still validates. Recorded on the accessor
  rather than tied by a test, because there is no third source to tie it to; a successor who
  wants it structural would have to mint one.
- **`Schema/shapes/INDEX.json` hashes the WRITER FILE, not the declared shape.** A
  docstring-only edit to `Picking_Data.py` after `--accept` reddens
  `test_the_committed_index_is_current_with_the_tree` — a sixth architecture red that looks new
  and is a stale fingerprint. `schema_report --sync` after the LAST edit to a writer, the same
  discipline the arch chain and preflight already have.

### What this closes

Nothing on this map is left open by this ticket. 15's held half is built in full: both uid
clauses, the site-total closure, check 6's cross-leaf half in 27's two-constant form, the
`reconcile_pair` sibling with its `coupled`-marker grouping and its conditioned
FAIL-on-absent-site-DB, the per-batch site total, and section 6 discharged. The "Not yet
specified" entry for `receiving_report`'s absolute tolerance closes with a decision rather than
a deferral.

**What it does NOT close, and neither did any earlier ticket:** `equilibrium_report`'s
accumulation loop is still not unit-tested (24 named it; nothing this ticket built runs through
it), and `Tests/architecture` is still five red on clean develop.
