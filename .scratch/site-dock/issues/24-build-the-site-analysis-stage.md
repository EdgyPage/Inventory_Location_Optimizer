# Build the site analysis stage

Type: task
Status: resolved
Blocked by: 21

AFK. Graduated from the map's "remaining builds" fog by the execution override (map Notes).
[Re-scope the analysis surfaces to the site](07-rescope-the-analysis-surfaces.md) settled the
rule and said its builds "cannot be written before the coupled unit builder produces a pair of
leaves to read" — that exists now
([Build the coupled work unit](18-build-the-coupled-work-unit.md)). What still blocks it is
[Build the coupled receiving coordinator](21-build-the-coupled-receiving-coordinator.md): the
`_site/` tree has nothing in it until one dock writes there, and a site stage reading an empty
scope reports zeros rather than refusing (memory `a-grant-is-not-an-output` — the `[access]`
summary reports INPUTS, so only the `[render]` run summary says whether anything was written).

## Question

Build 07's answer: the site stage and `SiteContext` itself, the `yard` family's move to a third
scope value (its `schema_id` bump and the four test ties), the site clause in `equilibrium.py`
with the report's two-leaf accumulation, the rollup's `ValueError` refusal and its `analyze_run`
skip, and the two unlisted leaf surfaces — `series.py`'s `yard_overage_total` and
`throughput.audit`'s undeclared door read.

**04 section 7 comes due here, and it is now collectable.** The coupled put clause reads ONE
site number: both leaves' `_put_seconds` over `crew x day_seconds x days`, banded against the
SUMMED expectation — which sits at rho rather than below it, because
[Build the site put-away pool](19-build-the-site-putaway-pool.md) removed the cause of
`expected_utilization`'s "single-channel leaves undercut rho" caveat. Retire that caveat for put
on coupled runs and keep it verbatim flag-off. The per-leaf realized numbers stay visible beside
it: they are how the pool's fairness rule is seen working. The receiving clause takes the same
shape from 21's crew.

Two traps this stage sits on top of:

- **Consume run-tree levels POSITIONALLY** (CLAUDE.md section 3). A site scope is a new level
  shape, and `run_channel_rollup.py` must refuse a coupled run rather than sum two leaves whose
  savings are no longer additive.
- **A crew share denominated on span over-reads ~3x** (memory `calendar-span-is-not-work-days`);
  a leaf's span is its distinct `work_day` count, and the site's is the same count because one
  batch is one site day.

## Amendment (2026-09-11, from ticket 21)

**THIS TICKET NOW CARRIES THE SECOND COMPARABILITY BREAK.** 21 built the coupled receiving
coordinator and correctly declined to measure a break, because the thing that breaks
comparability is a coupled run FIELDING one dock — and three things such a run needs were on
21's own exclusion list. Two of them are this ticket's:

- **The site-scoped rows have no home.** A coupled drain's `yard_drains` and `yard_trailers`
  are trailer- and door-denominated, so they are the site's and not a channel's. Their declared
  artifact is `<pair>/_site/inbound_<arm-pair>.db` (ADR-0005), and until it exists 21 PARKS the
  row rather than handing it to `leaves[0]` — writing it there would be
  `a-right-site-total-hides-two-wrong-shares` built deliberately.
- **One transit carries ONE `gain_bundle` slot for two owners** — that is 05's, and it is
  [Build the composite gain bundle](26-build-the-composite-gain-bundle.md), which runs
  alongside this ticket rather than before it.

So the run that fields the site dock is this one, and its acceptance gains what 21's could not
have: **measure the break per leaf and record it**, the way
[Build the site put-away pool](19-build-the-site-putaway-pool.md) did.
`Tests/e2e/test_coupled_unit_e2e.py::test_a_coupled_unit_matches_the_two_units_it_replaces` was
amended by 19 and left untouched by 21; extend it here.

**21's hand-off list for the driver, verbatim, so it is not re-derived:** build the dock, yard
and coordinator at UNIT scope the way `_build_put_pool` does; hand the SAME transit to both
leaves and `bind` each; chain the receiving uid block off the put pool's block end; base
`_recv_base` on `open_batch(day_of(i))` and report each leaf's finish to `note_records`; drive
`SiteReceiving.drain` INSTEAD of two `check_reorders` — phase 5 must not go back through each
leaf's own composition, or the earlier leaf's put drain runs before the site's receive; drain
the dock once and partition its records by owner (the six refused leaf accessors are the list of
what needs a site-scoped replacement); and drain `site_rows` every batch.

**The price decision is CLOSED and it is yours to build.**
[Decide the site dock's unload price](27-decide-the-site-docks-unload-price.md) answered: the
unload price is a statement about the MERCHANDISE, so the site dock holds a price LIST keyed by
the unloaded unit's own regime — the same shape 19 already tested one crew over, where `s_put`
is keyed by channel while the putters are one pool. So: `DockSpec`/`Dock` take a per-regime
mapping instead of one `UnloadCost` and `Dock.unload_seconds` resolves per unit at the charge
site, where 21's step-4 handoff already holds `regime_of(item.unit)`. Flag-off and uncoupled
construct NO list, structurally, in the `recv_crew_spec` style — an uncoupled leaf builds its own
dock from its own pick config exactly as today, which is what keeps every archived run
byte-identical. **That decision costs no comparability break**; yours is the one that does, and
it comes from fielding the dock, not from pricing it.

## What proves it

- **The site number equals the two leaves' sum**, asserted rather than assumed — the whole
  argument for one site clause is that `expected_utilization` is linear in load.
- **The rollup refuses a coupled run and its exception TYPE is the one 07 section 4 chose**;
  mutation-checked, because a refusal nothing tests is a refusal that gets caught by an
  `except Exception` somewhere upstream.
- **Uncoupled analysis output is byte-identical, MEASURED** — an existing archived cell
  re-analysed before and after, not just a canary.
- Nine verifiers, including the run-tree contract (`--sync` before the DDL edit, `--accept`
  after: the `yard` family's scope move is a schema change and rides the pipeline, never a
  consumer edit). Nine-verifier + `Tests/architecture` baselined against a `git archive` copy.

## Answer

**BUILT** — the site analysis stage, the per-regime dock price, and the first coupled run
that actually FIELDS one dock. `_SiteDock` mints one `Dock`, one `YardTransit`, one
`SiteReceiving` and one receiving roster at UNIT scope; the leaves' phases 0–5 run once
through `SiteReceiving.drain`; the dock's rows, flows and census are partitioned back to the
owning channel by SKU and CLOSED against the dock's own totals; the trailer- and
door-denominated rows go to `<pair>/_site/inbound_<arm-pair>.db`, now a declared artifact;
and the analysis gains a fourth simulation-reading scope — `SiteContext`, a third
`run_analysis` stage, four site-scope yard evaluations, `equilibrium.site_utilization_clause`
and the rollup's `ValueError` refusal.

### THE COMPARABILITY BREAK, measured per leaf

A 12-batch mixed pair, 600 SKUs, standing yard, 2 doors, derived site crews put 2 / receiving
2 — **uncoupled the same pair fields 2 per leaf, so 4 receivers and 4 putters where the
record derives 2 and 2**. Measured twice, on the first build and again on the final code
after the review round; identical both times.

| leaf | role | rows c/u | seconds c/u | `t_abs` move | actors c/u |
|---|---|---|---|---|---|
| store | receive | 434 / 434 | 61,816.1 / 61,816.1 (**+0.00%**) | mean 69.6 s, max 455.9 s, 333/434 unmoved | [27, 28] / [27, 28] |
| store | put | 434 / 434 | 101,217.8 / 100,479.6 (+0.73%) | mean 194.0 s, max 619.8 s, 57/434 unmoved | [25, 26] / [25, 26] |
| fulfillment | receive | 47 / 47 | 462.8 / 462.8 (**+0.00%**) | mean 2,675.8 s, max 10,433.2 s, **0/47 unmoved** | [27, 28] / **[22, 23]** |
| fulfillment | put | 47 / 47 | 2,474.7 / 2,720.3 (−9.03%) | mean 3,331.6 s, max 7,336.8 s, 0/47 unmoved | [25, 26] / **[20, 21]** |

**The receiving SECONDS do not move at all, and that is the price decision showing up as a
measurement.** Same rows, same durations, to the last decimal on both leaves — because the
site dock's two price-list entries ARE the two constants each leaf already charged (27). What
moves is WHEN and WHO: the absolute axis, because the site base is the shift start rather
than each leaf's `arm_clock` and one crew of 2 now does what two crews of 2 did; and the
fulfillment leaf's receiver uids, `[22, 23] → [27, 28]`, which is the uid block chaining off
the put pool's end instead of off that leaf's own pickers — the same fix 19 made visible for
the putters, now visible for the receivers.

**The yard moves out of the leaves entirely.** Uncoupled: 22 trailers + 12 drains in the
store leaf and 5 + 12 in the fulfillment leaf. Coupled: **0 and 0 in both leaves, 22 trailers
+ 12 drains in the site DB.** Two numbers worth reading twice — 22 + 5 = 27 trailers across
two yards becomes **22** across one, because a mixed load consolidates; and 12 + 12 = 24
drains becomes 12, one per site day. Neither is a rounding: they are the double count of the
yard itself ending.

The put numbers are 19's break re-measured under a dock, and they reproduce it.

### Uncoupled is BYTE-IDENTICAL, measured on DB rows

One standing-yard **store arm** (20 batches, 250 SKUs, 3 doors, a 600 s receiving day, split
allocation — deliberately the corner where the yard binds) run in this tree and in a
`git archive HEAD | tar -x` copy: **149,713 rows across 19 tables identical**, the only
masked column being `simulation_runs.created`. The evidence was COUNTED BEFORE the diff was
trusted — **126 `receive` rows, 126 `put` rows, 17 `yard_trailers`, 20 `yard_drains`** —
because an empty table diffs clean. **With an ORACLE**: a 1.000001× factor planted on
`unload_cost` in the HEAD copy moves 147 rows, so the diff can fail. Measured twice, before
and after the review round, identical both times. Figures were deliberately NOT diffed (23's
finding: a HEAD-vs-HEAD control differs on every PNG) — which is exactly why the review's
finding 6 below mattered.

### What was built

**1. The price list, and it is structurally absent flag-off.** `Dock` takes `cost` XOR
`costs`; `unload_seconds(w, v, q, unit=)` returns before ever looking at `unit` when `cost`
is set, so the archived path is byte-identical rather than merely equivalent, and a site
dock with no unit to resolve from REFUSES instead of defaulting. Keyed by **regime**, not by
channel name — `Channel` keeps the two independent and `regime_of` is what resolves.
`_charge_repack` and the v1 deque drain pass the unit too: a repacked fulfillment tote is a
fulfillment tote.

**2. `_SiteDock` at unit scope**, the receiving twin of `_build_put_pool`: one dock priced
per regime from each leaf's own pick config through the same chain a leaf builds for itself
(ONE function now — `_site_unload_cost` — called by both, so "the entries are the two
constants" is structural), one `YardTransit` both leaves hold, one coordinator, one roster
chained off the pool's block end, and the site DB. **Five refusals**, each a degradation
that would be the double count with a coupled label on it: a non-standing pipeline, a
standing yard with no receiving crew, two different inbound records, one leaf with inbound
beside one without, and a unit with no `site_db`. `None` on the coupled inbound-OFF pole,
which the funnel runs in every cell — a return, not a refusal.

**3. The partition, and its closure.** The dock counts one site total; ADR-0005 puts the
pack-denominated half back on the owning channel. `_partition` runs once per site day and
serves each leaf once (`_take` POPS, so a second ask raises), routing every unload and
repack row by the same `{sku: leaf}` dict step 1 packs by. The per-leaf SECONDS come from
each leaf's own `receiving_seconds` — 15's precondition — precisely because that is the only
surface a repack passes through; and the shares are **asserted to sum back** to the dock's
own `depth`/`unloaded`/`cut`/`seconds`. That closure is a real check rather than a
restatement, and two tests break the decomposition to prove it can fail.

**4. Four more leaf refusals, ten in all.** `drain_yard_drains`, `drain_yard_trailers`,
`standing_yard_trailers` and `lead_queue_depth` join the six. The third is the worst of the
ten because it does not DRAIN: two leaves reading it bill every censored trailer twice. Each
has a site-scoped replacement, and `transit_census_for` answers all three transit reads off
ONE pass so they cannot disagree.

**5. The site scope in the analysis.** `SiteContext` subclasses `EvalContext` and populates
the same `_by_key` shape, so every broker works unchanged — 07 section 1's whole design. Its
one load-bearing override is `batch_df`, which concatenates BOTH leaves' frames, and that one
concat answers both of 07 section 2's denominator questions with no new arithmetic: door
utilization takes the union of the two clocks, receiver busy takes both leaves'
`recv_seconds` over DISTINCT `work_day` (never the calendar span —
`calendar-span-is-not-work-days`). `_arm_end_s` now resolves through `ctx.batch_df`, which is
what makes the censoring bound the SITE end. The per-channel shares are printed BESIDE the
site number in the scorecard, never instead of it.

**6. The rollup refuses with a `ValueError`** — checked FIRST, before a series doc is read —
and `analyze_run` skips the step outright so the log reads `rollup skipped: coupled run`
rather than a failure line on every cell of a healthy run. Its docstring's independence claim
is rewritten as conditional on the inbound-off model rather than caveated.

**7. `site_utilization_clause`**: one band over the site's put and recv crews against the
SUMMED expectation, with each leaf's share carried inside it unbanded. The leaf clause drops
put and recv on a coupled run (`LEAF_DEPARTMENTS`) — site bands, leaves report. The
"single-channel leaves undercut ρ" caveat is retired for put on a coupled run and kept
verbatim flag-off and for the integer-crew half.

### Deviations under force

- **07 section 6 could not be built as written.** Moving `FAMILIES['yard']['scope']` to
  `'site'` and taking `yard` OUT of `_LEAF_FIGURE_FAMILIES` would delete the yard report from
  every run in the archive, and would put two uncoupled channel leaves' figures in ONE
  pair-level directory where they overwrite each other. A yard genuinely IS a leaf's on an
  uncoupled run — each channel fields its own transit, dock and crew. So the site tree is an
  **addition**: `SITE_SCOPE_FAMILIES` beside `LEAF_FAMILIES`, `_SITE_FIGURE_FAMILIES` beside
  `_LEAF_FIGURE_FAMILIES`, `yard` in both, and four site-scope registrations over the same
  four render bodies. That preserves 07's actual goal — the site's yard rows get a declared
  home and a report that reads them — at strictly smaller cost. The four test ties are
  extended rather than replaced, and the naming ratchet gets four declared `_KEY_EXCEPTIONS`
  of the `sig.by_initial` class (a structural twin co-registered in its twin's module).
- **One job per `(cell, pair)`, not per `(pair, arm-pair)`.** The yard family's marks are
  `ranked` and `serial`, so every arm pair has to be in ONE context to be ranked against the
  others, and the declared output tree is one directory per pair, which two concurrent
  per-arm-pair jobs would race to wipe.
- **`DockSpec` is unchanged.** 27 says "`DockSpec`/`Dock` take a per-regime mapping"; `DockSpec`
  holds no cost today, so putting one there would be a second home for the price.
- **The run-tree contract rides `contract --write`, not `--sync`/`--accept`.** That pair is
  the DB-shape pipeline, and no DB shape moved: the site DB IS a sim DB carrying only the two
  yard tables, which is what lets every broker bind it with no new loader. `schema_id` moved
  `5c9bc35db55b → 49274a556866` (three artifacts added); `schema_report --sync` re-recorded
  the shape store's fingerprint after the writer edit.

### The review round, and what it found

Run before the commit (ticket 26's omission). It found **two defects, both real**:

- **The site stage emitted ZERO jobs on every coupled run.** `sim_meta['strategies']` is a
  list of DICTS and `_site_jobs` tested membership on the dict, which matches nothing — so
  every leaf resolved zero arms, every pair was skipped, and the stage logged the same line
  an uncoupled run logs. `a-grant-is-not-an-output` in its purest form, and not one of the
  110 tests reached it because none built a real `sim_meta`. Three now do.
- **The site DB had no duplicate-run guard.** A coupled pair killed mid-flight with BOTH
  leaves at the same batch is in step and therefore not stale, so the reconciler left its site
  DB alone while the planner replayed both leaves from batch 0 — a second run appended, and
  `find_run`'s `ORDER BY run_id LIMIT 1` then answers every filtered query from the ABANDONED
  one. Fixed three ways: the discard set is now every rank that REPLAYS (not only the ones
  that disagree), `finish()` refuses a file that already holds a run, and the run is stamped
  with its arm pair so the lookup is by name.

Five more landed: the site clause was keyed by the leaf's arm key while its own docstring
claimed otherwise (now keyed by RANK, which is what makes a pair whose halves are named
differently one site); the site window was taken from whichever leaf iterated first (now
refused when they differ); `_dept_seconds`' docstring claimed a factoring that had not
happened (`_utilization_clause` now calls it); `lead_queue_depth` was the one transit read
that did not refuse; and **the per-channel column was added to the leaf scorecard too** —
which would have changed every archived run's `absolute_yard_scorecard.png` on re-analysis, a
rendered regression the row-level byte-identity measurement could not see. It is site-only
now, with its own test file that reads the COLUMN LIST rather than the picture.

**And the review did not find the worst one.** Driving the whole stage end to end over the
probe's real coupled tree did: `SiteContext` left `_caps` uninitialised, and because it
SUBCLASSES `EvalContext` the era gate's `hasattr(ctx, 'capabilities')` found the inherited
method and called it — so three of the four yard evaluations raised `AttributeError` inside
the driver's swallow, rendered nothing, and left the tally reporting a GRANT. The docstring
saying "no `capabilities()`, deliberately" was the defect written down. **A subclass is not
exempt from a duck-typed gate**, and only the `[render]` line says whether anything came out.
After the fix: 4 granted, 0 errors, **7 PNGs** under `<pair>/_site/figures/yard/`.

### What proves it

- **The site number equals the two leaves' sum, asserted.** `test_the_site_number_equals_the_two_leaves_sum`
  asserts `reading[dept]['worked_s']` is the sum of the two leaves' own `_dept_seconds` and
  `realized` the sum of the two `share_of_site_grant` values — non-vacuous, verified
  out-of-process (a one-leaf clause reports 103,680 s against the two-leaf 168,480 s).
- **The rollup's refusal TYPE is the one 07 chose**, mutation-checked in both directions: a
  mutation that drops it and a mutation that makes it a `SystemExit` both fail the suite.
- **Uncoupled byte-identity**, above, with an oracle.
- **Nine verifiers green**; both preflight canaries `tree shape UNCHANGED` on the final code.
- `Tests/unit` + `Tests/integration` **2,724 passed, 1 skipped**; `Tests/e2e/test_coupled_unit_e2e.py`
  **8 passed** including the new `test_a_coupled_site_dock_matches_the_two_docks_it_replaces`.
  **~130 new test functions** across six new files plus four extended.
- **`Tests/architecture` at the known baseline: FIVE red**, the same five by NAME as on HEAD.
  Four are shared with a `git archive HEAD` copy; the fifth
  (`test_every_consumer_that_declares_requirements_is_validated_here`) is `.claude/worktrees/`
  only, exactly as memory `arch-tier-is-red-on-head` records. **Zero new reds**, and the
  run-tree ratchet's per-file list is byte-for-byte the copy's.
- **46 guard mutations, 46 caught.** Each asserts its anchor count before it is applied, so a
  mutation that matched nothing could not read as a passing test. Two SURVIVED on the first
  pass — the partition's closure checks, which held on correct data — and the fix was two
  tests that BREAK the decomposition; two more survived after the review fixes (a stamp test
  that read through `find_run`'s fallback, and a refusal absent from the read list).

### Findings

- **The `_site` token starts a ratchet.** Declaring `site_dir` put a reserved segment into
  the contract, and `test_runtree_consumption` swept the whole repo for the raw substring —
  flagging `_prepare_site_run`, `put_site_pricer` and `test_bin_mutation_sites` in files
  nobody had touched. The token is now bounded on BOTH sides (the collisions come in both
  shapes), and the one remaining entry is the writer-side join, baselined with its reason.
  22 found the same ratchet counts prose; this is the identifier half of it.
- **`--catalog-merge` seeded the `purpose` from a docstring FRAGMENT for the FOURTH time**
  (11, 18, 19, now 24), across six new files. Filled by hand, on one line each.
- **The e2e fixture still cannot measure a break** (19's finding, unchanged): three batches
  produce ONE receive row, so the numeric claim lives in a 12-batch probe outside the suite
  and the test asserts the structure.
- **The arch chain must run after the LAST source edit** — it ran four times here, because
  each review round was a source edit. The key rename alone reddened `test_archgraph_nodes`
  and both `test_artifact_map` ties until the contract document was re-written (the
  `evaluation` attribution is UNHASHED, so `contract --check` stayed green while the stored
  document named the old keys).

### What this hands onward

- **[Build the site crews' cross-leaf checks](25-build-the-site-crew-checks.md)** — and the
  question it is obliged to settle first: **a receive row's regime IS resolvable from the sim
  DB alone, without a schema change.** Under coupling every `receive` and `repack` row lands
  in its OWNING channel's `work_events` — the coordinator partitions by SKU before the driver
  stamps anything — and `simulation_runs.channel` names that channel. So check 6's two-constant
  form is `C_store` over the store leaf's rows and `C_ful` over the fulfillment leaf's, each
  re-priced against its own regime's entry, with no new column and no `--sync`. What 25 must
  NOT assume is that one DB holds both regimes' rows: it does not, by construction, and a
  check written that way would find one constant and pass.
- **The leaf's `batch_stats` receiving scalars are now each leaf's own share** (15's
  precondition, built), and the site total is the coordinator's independently-accrued one —
  so 15's site-total closure check has both sides to compare.
- **`lead_queue_depth` changes UNIT with the run shape** — in-flight entries (trailers)
  uncoupled, this leaf's in-flight LOTS coupled. Stated on both the accessor and
  `transit_census_for`; a `sim_semantics` `Col` note would be the place to say it where a
  consumer sees it, and a note moves no `schema_id`.
- **`equilibrium_report`'s own accumulation loop is not unit-tested** — `_site_verdicts` is
  (both refusals, mutation-checked), but the walk that fills it needs a run tree with a
  staffing record and a closed ledger. That is 25's natural neighbourhood.
- **26's three review findings are folded in**: the gain bundle now binds by `channel_regime`
  rather than by channel NAME (`for_key` dispatches on the regime and `Channel` keeps the two
  independent); `_build_site_gain` refuses an asymmetric gain policy at build time (hardening,
  not a live bug — the reachability is unchanged, but under ONE shared transit the consequence
  changes from inert to a mid-batch `ValueError`); and `gain.py`'s false justification about
  `_wp_for` dispatching per regime is corrected — `workunits` nulls `by_regime` on every mixed
  leaf, so it is the leaf's own regime-pure `wp` that makes the answer right.
- **`regime_of` now REFUSES a tuple.** A BinKey fell through every `getattr` and answered
  `'store'` for a fulfillment key, silently; two tests pinned that wrong answer and now pin
  the refusal. `regime_of_key` is the spelling, and the two are three characters apart.
