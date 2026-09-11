# Extract the one-leaf receiving coordinator

Type: task
Status: resolved
Blocked by: 01

AFK. The execution override (map Notes) graduating 01's design into a build. Every governing
decision is in
[Design the site receiving coordinator](01-design-the-site-receiving-coordinator.md); this ticket
adds none.

## Question

Nothing to decide — this is the **byte-identical half** of 01, landable on its own. It needs
nothing from 02 (the coupled work unit), 03 (the site scope) or 08 (the site space view), because
it drives exactly **one** leaf and changes no behaviour. Doing it first de-risks everything
downstream: the coordinator exists and is proven equivalent before any second leaf is attached.

The work:

1. **Add the two ports** to the reorder mixin as public methods, carved out of `_receive_standing`
   (`Warehouse/inventory/inventory_reorder.py:708-833`):
   - `plan_lot(sku, qty) -> list[PutawayItem]` — step 1's body: resolve `_originals`, apply
     `inbound_split`, pack, `_stamp`, debit the pack shortfall against `_deferred_qty`.
   - `accept(item, t0, dur, w) -> None` — step 4's body: the deferred-to-queued flip, `_queue`,
     `_recv_seconds`.

2. **Create `Inbound/receiving.py`** holding `SiteReceiving`: the one `Dock`, the one
   `YardTransit`, the yard-drain record, and `drain(put_deadline, recv_deadline, now_s)`. It takes
   its leaves by injection and calls only the two ports — it must not import
   `Warehouse.inventory` (`forbid: [inbound, wh_inventory]`, `context/architecture.yml:100`).
   `regime_of` (`wh_kernel`) is legal and is what routes step 4.

3. **Keep `check_reorders` untouched.** It stays the single-channel composition; the coordinator is
   a second composition over the same phases. `strategy_runner.py:1359` is unchanged by this
   ticket.

4. **The equivalence test** (01 §9): the same seeded single-channel standing-yard run driven (a)
   through `check_reorders` and (b) through `SiteReceiving` with one leaf, asserting identical
   `dock.records`, identical `_yard_drains` and identical put-queue contents per batch. Assert the
   **record sequences**, never their sums (memory `lockstep-tests-compare-aggregates-only`).

Deliberately **out of this ticket**, all deferred to the coupled build: the `{sku: leaf}` owner
dict, the refusal on a non-standing transit, the leaf-accessor refusals, the `SITE_PHASES`
sequence in `Tests/unit/test_reorder_phases.py`, the ADR, and the `CONTEXT.md` amendments. Those
only mean something once a second leaf exists.

Gates to re-run before handing back (CLAUDE.md §1): `python -m pytest Tests/unit -q`, then
`python context/arch/verify_architecture.py` — the new module adds a node and an import edge, so
the arch layer needs regenerating.

## Answer

**Done, and the coordinator is LIVE rather than shadowing.** `Inbound/receiving.py` owns the
standing drain; `Warehouse/inventory/inventory_reorder.py` keeps only the two ports; the driver
binds the coordinator where it already builds the `Dock` and the `YardTransit`. There is exactly
one implementation of the drain, and production runs it.

### The decision this ticket turned out to contain

The ticket said it added none. Carving it surfaced one, and it was load-bearing: **items 2 and 3
pull against each other.** `Warehouse -> Inbound` is forbidden in both directions
(`context/architecture.yml:102-103`), so a manager can never call the coordinator except by
injection. With `check_reorders` and the driver both untouched, `SiteReceiving` would be reachable
only from its own test — and because `_unload_merged` / `_unload_split` cannot be imported back
into the manager, the drain would exist **twice**, 157 lines of dock physics included.

Put to the user, who chose to wire it now and to do the test sweep properly rather than land
something that had to be unpicked later.

**What that bought:** the byte-identity claim is now carried by
`Tests/e2e/test_standing_yard_e2e.py:193`, which drives the production seam
(`_prepare_channel_run` + `_run_strategy_worker`) and compares **every table row-for-row**. Under
the shadowing option nothing but one hand-written test would ever have run the module.

**A correction worth recording, because it nearly changed the decision:** the test sweep was
estimated at ~56 sites from a raw count of `YardTransit(` constructions. It is **five** — every
manager that gets driven is built by a `_manager(transit, ...)` helper, and `test_door_team_cap`
and `test_gain_plan` import the one in `test_standing_yard`. Most `YardTransit(` calls construct a
transit to test the transit, and never build a manager at all. Counting constructor calls is not
counting call sites.

### What moved

| | from | to |
|---|---|---|
| the drain | `inventory_reorder._receive_standing` | `SiteReceiving.receive(leaf, deadline)` |
| both unload modes | `inventory_reorder` (157 lines) | `SiteReceiving` |
| plans-at-arrival, per lot | inline in step 1 | `leaf.plan_lot(sku, qty, source)` |
| the handoff, per unit | inline in step 4 | `leaf.accept(item, dur)` + the dock's own row |

`_unload_merged` and `_unload_split` contained **no `self` at all** — verified before the move —
so they were already pure functions of `(dock, transit, deadline, epoch, work_order, yard_next,
cap)`. That is why 01 §7's table could put them on the coordinator without an argument.

### Two signature deviations from 01, both forced

**`plan_lot(sku, qty, source) -> (plans, items)`, not `-> list[PutawayItem]`.** Step 1 produces
both halves and both are consumed — the `LoadPlan`s become `trailer.plans` and the dock's arrival
notice, the stamped items become `trailer.pending` — and a plan holds several units, so the
grouping is not recoverable from the items. A single-return port would have thrown away data the
caller needs. `test_plan_lot_returns_both_halves_and_neither_derives_the_other` asserts a plan with
more than one unit exists, so the claim is not vacuous on this fixture.

**`accept(item, dur)`, not `accept(item, t0, dur, w)`.** `t0` and `w` are fields of the DOCK's row
and the dock is the coordinator's; handing them to a leaf would be two dead parameters plus an
invitation to write the row twice. The leaf's half and the dock's half are separate statements
over disjoint state, so the split costs no byte — each accumulator still sees the same `dur`
values in the same order.

### The yard row

`receive` **returns** the row rather than recording it, so the caller places it. One leaf's
`_yard_drains` today; a site-scoped artifact once there are two
([Design the site scope in the run tree](03-design-the-site-scope-in-the-run-tree.md) §2). That
kept `drain_yard_drains()` and all five of its call sites untouched, which is the difference
between a byte-identical change and a migration.

### The refusal

A standing transit with no coordinator bound **raises**, rather than falling back to an inline
drain. A fallback would be the second implementation this ticket removed, and a silent one would
produce a run that looks healthy and answers a different question — `pool-run-swallows-dead-arms`.

### What the tests actually pin, and the one that did not

`Tests/unit/test_site_receiving.py`, eight tests: the one-leaf behavioural equivalence (both
allocations), the returned row, the two ports in isolation, the refusal, and the phase order.

**The behavioural equivalence test was vacuous with respect to phase order, and that was found by
mutating the code rather than assumed.** Swapping `_advance_lead_queue` and `_fire_reorders` inside
`drain` — the exact reordering `check_reorders`' own docstring warns about — left every record,
ledger, on-hand figure and yard row identical, because the scenario's leads are all zero and
nothing is ever in flight for a tick to advance. The test passed on mutated code.

So the order is pinned for what it is, a claim about the **sequence of calls**, by recording it:
`test_the_site_composition_substitutes_only_the_receive_phase`. That test then caught a real
discrepancy — `drain` does not call the leaf's `_receive` at all, it substitutes the coordinator's
one shared receive in that slot, which is the design (01 §4) and not a drift. The assertion says
exactly that: the site sequence is `check_reorders`' sequence with **one** substitution, and
differs by no more than one position.

Both failure modes were re-checked by mutation after the fix: reordering the phases fails it, and
dropping the per-leaf `_release_arrivals` (01 §4's amendment — the phase that lands trailers in the
yard, a structural no-op in standing mode and still load-bearing) fails both it **and** the
behavioural test. `real-test-coverage-is-317` is why this is in the record: a pass is not evidence
until it has been shown it can fail.

### Gates

| gate | result |
|---|---|
| `python -m pytest Tests/unit -q` | **2010 passed** (3m09) |
| `python -m pytest Tests/e2e/test_standing_yard_e2e.py -q` | **4 passed** — includes the row-for-row DB comparison |
| `python context/arch/verify_architecture.py` | OK — 3231 nodes, 3953 edges, 20 backbone, 25 layers |
| `python context/arch/verify_site.py --fast` | OK |
| `python context/verify_context.py` | OK |
| `path_guard --scan` / `docref_guard --scan` / `verify_memory` | OK |

The arch layer was regenerated in the load-bearing order (`--write`, `--catalog-merge`, fill,
`--write-nodes`, `render_html --build`). Both new files catalogued in `context/files.yml`; the
coordinator carries a note about the injection seam, because a reader finding a class in `Inbound/`
that drives a manager will want to know why it cannot import one.

**Three stale anchors repaired** while the name was still findable: `Inbound/space.py:36`,
`Inbound/priorities.py:66` and `Inbound/gain.py:616` all named `_receive_standing`, as did
`test_standing_yard.py`'s header and a comment in `sim_config.py`. A method name in a docstring is
exactly the anchor `context/` cannot verify.

### What this did NOT do

Unchanged from the ticket's own deferral list, and all of it still waiting on a second leaf: the
`{sku: leaf}` owner dict, the refusal on a non-standing transit, the leaf-accessor refusals
(`dock_depth`, `recv_snapshot`, `transit_snapshot`, `in_transit_qty`), `SITE_PHASES` in
`Tests/unit/test_reorder_phases.py`, the ADR, and the `CONTEXT.md` amendments.

`drain(leaves, ...)` accepts a list and loops, but it is honestly the **one-leaf** composition and
says so: it routes the shared receive to `leaves[0]` and gives each leaf its own put deadline. The
owner routing and the shared put budget ([Design the site put-away pool](04-design-the-site-put-away-pool.md))
are what turn that loop into the site interleave.

### `Tests/architecture`: one failure was mine, four were already there

The suite was run beyond the ticket's asked-for gates because the arch layer and the HTML site were
regenerated. It reports **4 failed, 378 passed, 1 skipped**. Each failure was attributed rather
than assumed, by extracting HEAD to a clean copy (`git archive HEAD | tar -x` — `git worktree add`
dies on this repo's long generated filenames, memory `head-copy-via-git-archive`) and running them
there:

| test | on clean HEAD | verdict |
|---|---|---|
| `test_files_catalog_sync::test_merge_is_idempotent` | passed | **MINE — fixed** |
| `test_archgraph_extract::test_key_backbone_edges_present` | **fails** | pre-existing |
| `test_bin_mutation_sites::test_the_dead_site_is_still_dead` | **fails** | pre-existing |
| `test_schema_compatibility::..._conditional_table_is_declared` | **fails** | pre-existing |
| `test_schema_compatibility::..._requirements_is_validated_here` | passed | **not the code** — see below |

**The one that was mine:** a hand-written multi-line `notes:` in `context/files.yml` does not
survive a re-merge — `--catalog-merge` canonicalises notes to ONE line, so the committed file and
the rebuilt one differed and idempotency broke. Rewritten as a single line; the gate passes. Worth
knowing before hand-editing that file again: the merge is the authority on formatting, not the
author.

**The last one is an environment artifact, not a defect.** Every unvalidated declaration it names
is under `.claude/worktrees/` — two live git worktrees on other branches
(`clever-poitras-b487d3`, `peaceful-wiles-3853f3`), both predating this session. The test walks
them as if they were source. It "passed" in the HEAD copy only because `git archive` does not
include untracked worktrees, so the clean copy had none to scan. Left alone: deleting another
branch's worktree is not this ticket's to do, and changing the walk is its own decision.

The three genuinely pre-existing failures are untouched and unexplained here — they were failing
before this work and are not in its scope.
