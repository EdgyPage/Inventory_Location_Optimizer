# Build the site put-away pool and the site put clock

Type: task
Status: resolved
Blocked by: 04, 12, 18

AFK. Graduated from the map's "remaining builds" fog by the execution override (map Notes),
now that [Build the coupled work unit](18-build-the-coupled-work-unit.md) has made a second
leaf exist. Every seam this needs is seated and exercised by production — `bind_crew(clocks=)`,
`drain_putaway_records(reset_clocks=)`, `_stock(charge_cut=)` and `count_put_cut` (12) — and
the unit that binds them is in (18). No design decision is open:
[Design the site put-away pool](04-design-the-site-put-away-pool.md) settled the rule.

**18 moved one thing INTO this ticket, and said why.** 02 section 5's site-wide `put_clock`
was to land with the coupled unit; it cannot, because `put_clock` is the absolute carry a
BATCH-LOCAL clock list is based from, so a site-wide carry over two per-leaf lists starts leaf
B's putters where leaf A's finished — two full crews serialized as if they were one, which is
a third model and neither of the two on offer. The carry and the shared list are one change.

## Question

Build 04's answer, sections 1 through 8, plus 02's amendment.

1. **The shared list.** One `_Crew(PUT, size=derived.put.crew)` at UNIT scope, one `workers()`
   tuple, both leaves' queues bound to the SAME `list[float]` via `bind_crew(clocks=)`.
   Segregation survives in the queues, not the people.
2. **The uid block** starts at `max(k_store, k_ful)` so it clears both channels' dense picker
   uids. The smaller leaf then has a gap — harmless, and the trade is that a putter's uid means
   the same person in both DBs, which 15's site-role clauses require.
3. **The day's division.** A proportional sub-deadline from
   `derived.put.expected_utilization[channel]` (crew and day cancel, so no new record field),
   then a **residue pass** against the full day. Without it fulfillment absorbs every cut as a
   pure artefact of loop order, and that bias would look exactly like a finding.
4. **The reset.** On a coupled run the manager drain stops resetting the shared clocks; the
   pool resets the one list once per site day, after BOTH leaves have drained. Giving that trap
   a single owner is the main argument for the module.
5. **The site `put_clock`** (02's amendment), based at the site day start:
   `max(day_start(i), put_clock_site)`. 04 section 9 holds the rejected alternative.
6. **`Inbound/putaway_pool.py`** owns the rule, the list and the once-per-day reset; the
   boundaries leave `Inbound/` the only package that may sit above two managers.
7. **Two prices over one crew**, stated and TESTED against the `s_put` ratio.
8. **Two loud refusals:** no working-day grid, and `PUT_QUEUE_SPLIT` (two answers to the same
   question — 12 found this is the same incompatibility stated a third time, because
   `refuse_unpriceable_put` already refuses the split for the era derivation).

**Explicitly NOT in scope:** 01's coupled coordinator and the site-wide `recv_clock` that
depends on it; 07's band reading one site number (04 section 7) and the retirement of the
"single-channel leaves undercut rho" caveat for put, which need the site analysis stage.

## What proves it

- **THIS IS THE COMMIT THAT BREAKS COMPARABILITY**, and a standing test says so before it is
  measured: `Tests/e2e/test_coupled_unit_e2e.py::test_a_coupled_unit_matches_the_two_units_it_replaces`
  pins coupled == uncoupled today and MUST fail here. Do not delete it — amend it to assert the
  new relationship, and **record the measured size of the move per leaf** in the answer. The
  failure names the leaf whose labour moved; that is the measurement.
- **Flag-off and uncoupled are both byte-identical, MEASURED.** Both preflight canaries
  (`tree shape UNCHANGED`), which are uncoupled, plus the unit suite.
- **The residue pass runs in BOTH directions**, proven by a scenario where the STORE leaf
  finishes early — the direction loop order hides.
- **A single-channel catalogue gets share 1.0 and the residue pass is a no-op**, so the
  degenerate case is correct for free (04 section 3). Assert it rather than assuming it.
- **The reset is owned once**: a test that drains leaf A and asserts leaf B's records are still
  on the clocks A left, because the silent zeroing is the failure mode this module exists for.
- Nine verifiers; `Tests/architecture` baselined against a `git archive` copy (five known reds).

## Answer

**BUILT and live on `develop`** — `9b0e21e8` (the pool, the split, the tests), `59a9927b`
(the architecture layer). **This is the map's first real comparability break**, and it is
measured below rather than argued.

`Inbound/putaway_pool.PutawayPool` is the site's put crew: one `list[float]` of worker clocks
bound to both channels' put queues, so `crew_clock.charge` books every put to whichever putter
is free earliest ACROSS BOTH CHANNELS. The sharing is identity, not arithmetic — `reset`
mutates in place — so a putter busy on one stream is busy on the other, with no new scheduling
vocabulary. Sections 1–8 and 02's amendment are all in. What is NOT in is what the ticket
excluded: 01's coupled coordinator, the site-wide `recv_clock`, and 07's band.

### The ordering question, again — and 04 section 9 rested on a loop order that did not exist

04 section 9 says "02's loop order already runs both leaves' phases 1-3 before the drain". It
does not. 18 built `for i: for lf in leaves: lf.step(i)` — the WHOLE batch per leaf — and said
so. Under that loop the residue pass is not merely awkward, it is **wrong**:

- it fires inside the LAST leaf's `check_reorders`, which is after the FIRST leaf has already
  taken its `queue_state_rows` / `carryover_rows` / `free_bin_depth` snapshots and already
  drained and stamped its put records;
- so the earlier leaf's residue placements are real, its rows are stamped against the NEXT
  day's base, and `put_queue_state` reports a depth that was never standing. Nothing raises.

The alternatives were worse. Dropping the residue pass leaves the earlier leaf capped at its
expected share while the later one takes the whole day — the same loop-order artefact section 3
exists to remove, merely inverted. Deferring only the record drain fixes the rows and leaves the
snapshots lying.

**So the batch became two halves.** `_build_leaf` returns `replenish(i)` beside `step(i)`, and a
unit runs EVERY leaf's replenishment before ANY leaf's picks — `for lf in leaves: lf.replenish(i)`,
then `for lf in leaves: lf.step(i)`.

`replenish` is the release instant, the two whistles, the standing-demand injection, the re-slot
and `check_reorders`; `step` is everything from `pop_churn` down. The cut is 79 lines moved
VERBATIM, and six names cross it (`_t`, `_late`, `_day_end`, `_put_base`, `_batch_early`,
`triggered`) — seeded in the setup scope and rebound through `nonlocal`, exactly as the carries
already were. One leaf calls the two back to back, which is the loop they came out of, and the
uncoupled diff below proves it. This is the cheap version of what 18 declined to do: the leaf is
still closures over its setup, and no state object was invented.

### 1–8 and the amendment, as built

1. **The shared list.** `_build_put_pool` at unit scope mints one `_Crew(PUT, size=derived.put.crew)`
   and one `new_clocks`; `_build_leaf(…, pool=…)` passes `pool.clocks` through
   `enable_putaway_timing(clocks=)`, so both managers' queues hold the SAME object. Pinned on
   identity (`qa.clocks is p.clocks`), because two lists of the same length is the defect.
2. **The uid block** starts at `max(k_pickers)` over both leaves. New, and not in the ticket:
   **the receiving cursor had to chain off the POOL's block end**, not the leaf's. Left at
   `k_pickers + put_size`, the smaller leaf's receivers land INSIDE the putters' block whenever
   `k_small + P` falls in `[max(k), max(k)+P)` — two crews merged in one DB, silently.
3. **The day's division** is `derived.put.expected_utilization`, normalised and **stacked**
   (0.25, 1.0), not flat. Stacking is forced by the shared clocks: the second leaf starts where
   the first left them, so its whistle is a point on the same day. That also makes the
   earlier→later direction free — the last leaf's share is 1.0 by construction, so it takes
   whatever the earlier ones did not spend — and leaves the residue pass to do only the
   later→earlier direction, the one the loop order hides. A single channel gets 1.0 and the
   residue is a no-op; a site expecting no put work at all gives everyone the whole day rather
   than starving one.
4. **The reset** is the pool's alone: `drain_putaway_records(reset_clocks=False)` on both leaves,
   `crew_clock.reset` once when the last leaf reports. The two orderings are refused rather than
   assumed (a second report, and a report while a leaf still owes a drain).
5. **The site `put_clock`**, based at `max(day.start_of(i), put_clock)`, with the whistle as
   `day.remaining(base)` — so `base + deadline` is the day's end however far the carry has run.
   `open_batch` is idempotent per day, because one clock list cannot answer two leaves
   differently.
6. **`Inbound/putaway_pool.py`** owns the rule, the list, the carry and the reset; zero imports
   beyond `crew_clock`; the leaves are duck-typed through `drain_putaway` / `count_put_cut`.
   `_drain_putaway` is promoted to the public `drain_putaway(deadline, charge_cut=True)` —
   01's two ports become three, as designed.
7. **Two prices over one crew**, tested against the `s_put` difference.
8. **The refusals**, and there are four rather than two (below).

### Deviations under force

- **A coupled unit without a derived staffing block is REFUSED.** Not in the ticket, and it is
  load-bearing: without the block there is no site crew to pool and no recorded expectation to
  divide the day by, and the only alternative — falling back to the per-leaf crews — is the
  double count with a coupled label on it. `--couple-channels` is not gated on
  `--shift-drain-or-cap` at the parser, so this shape was expressible; it now raises. The
  consequence is that **`Tests/e2e/test_coupled_unit_e2e.py` runs under a derived block and the
  working-day grid**, which is a fixture change, not a scope change.
- **The working-day refusal is TWO conditions, not one.** "No working-day grid" reads naturally
  as `cut_at_day_end`, but `releases_per_day` matters just as much: with it unset, `day_of(i)`
  is 0 forever, the base collapses to the carry alone and the putters run far ahead of the
  pickers on a continuous schedule. One batch is one site day is the pool's premise, so it is
  checked (the era completes `--releases-per-day` to 1 and refuses any other value).
- **`count_put_cut` is called by the pool against the FULL day**, per 04 section 4 — which is
  only correct BECAUSE the residue pass exists. Had the residue been dropped, the cut would have
  had to move to each leaf's own sub-deadline, and 12's `charge_cut` seam would have gone unused.
  The two decisions stand or fall together; that is worth knowing if the split is ever revisited.

### The measurement

A 12-batch mixed pair, site put crew 2 — uncoupled the same site fields **2 per leaf = 4**:

| leaf | put rows (c/u) | put seconds (c/u) | rows moved on the absolute axis | put actors (c/u) |
|---|---|---|---|---|
| store | 85 / 85 | 23,849.4 / 23,694.2 | mean 50.7 s, max 244.8 s, 12/85 unmoved | [25, 26] / [25, 26] |
| fulfillment | 42 / 42 | 2,509.6 / 2,629.6 | mean 507.7 s, max 1,586.3 s, 7/42 unmoved | [25, 26] / **[20, 21]** |

The fulfillment leaf moves an order of magnitude further, which is the shape the charter
predicted: it is the leaf whose putters now queue behind the store's. The put SECONDS move too
(+0.7% store, −4.6% fulfillment) — the two-pass drain changes which unit reaches which bin, so
this is not only a re-stamping. And the fulfillment actors move from `[20, 21]` to `[25, 26]`:
that is the uid fix, visible.

**`test_a_coupled_unit_matches_the_two_units_it_replaces` was amended, not deleted.** It used to
pin coupled == uncoupled and to say in its own docstring that this ticket would break it. It now
asserts the relationship that makes the move a FIX — one crew, one uid block, the same people in
both DBs, the same work done — and REPORTS the size per leaf. A numeric pin on a three-batch
fixture would be a different claim every time the fixture moved.

### Findings

- **The e2e fixture cannot measure this break, and that is worth saying.** Three batches of a
  300-SKU catalogue produce ONE store put row and none for fulfillment, so every numeric claim
  there would be vacuous. The measurement above comes from a 12-batch probe run outside the
  suite. A test that asserts "coupled differs from uncoupled" on that fixture would pass for the
  wrong reason today and fail for no reason tomorrow.
- **`--catalog-merge` seeded both new `purpose` fields from a docstring FRAGMENT again** — 11's
  finding, 18's finding, and now a third. Worse: a **multi-line** `purpose` breaks
  `test_merge_is_idempotent` (the merger re-emits it on one line), so the hand-written fill has
  to be one line, which nothing says.
- **`Tests/architecture` is five red and one of them was mine for a while.** Four fail
  identically in a `git archive HEAD` copy, the fifth is `.claude/worktrees/` only — the shape
  memory `arch-tier-is-red-on-head` records. The catalog-idempotency failure above was the sixth
  and is fixed. Diffing the failure LISTS against the copy (never the totals — the copy has four
  reds of its own from being outside git) is what separated them.
- **`render_html --build` raised `OSError: [Errno 22]` on one node page and succeeded on a
  re-run** — the same "run it twice" shape as memory `case-only-rename-deletes-its-own-page`,
  from a different cause.

### What proves it

- **Uncoupled is BYTE-IDENTICAL, measured.** One mixed pair run in this tree and in a `git
  archive HEAD` copy: `batch_stats` + `work_events` diff row for row to zero over 4,917 events,
  **36 of them puts** (counted before trusting the diff — an empty table diffs clean). Both
  preflight canaries `tree shape UNCHANGED`.
- `Tests/unit` + `Tests/integration` **2,485 passed, 1 skipped** (29 new, one new module);
  `Tests/e2e` **41 passed, 1 skipped**; all nine verifiers OK.
- **Nine guard mutations, nine caught**: the residue pass dropped, the day split evenly instead
  of by the expectation, a drain charging the cut, the pool resetting on the first report, the
  base as the carry alone, the uid block off the first leaf, the two-days check disabled, a
  pooled manager draining phase 5 itself, and `charge_cut` ignored.
- The residue pass is proven **in both directions**, including the one the loop order hides (the
  store leaf finishing early, over a real clock list).

### What this hands onward

- **04 section 7 is now collectable.** Both leaves' `_put_seconds` over one crew × day × days,
  banded against the summed expectation — the caveat's cause is gone. Ticket 07 owns the report.
- **01's coupled coordinator inherits the two-halves loop.** `replenish` is exactly the seam
  `SITE_PHASES` wants, and a site-wide `recv_clock` can now be based the same way `put_clock` is.
- **Phase 1 is asymmetric and the caveat is real**: it is inbound-off and UNCOUPLED, so it ranks
  placement arms under 2× the site put labour while phase 2 runs under 1×. Published with the
  campaign, not discovered by it.
