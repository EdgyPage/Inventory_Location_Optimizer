# Build the coupled work unit and its two-leaf worker

Type: task
Status: resolved
Blocked by: 02, 10, 11, 12, 13, 16

AFK. Graduated from the map's "remaining builds" fog by the execution override (map Notes),
because it is the **keystone**: almost every other patch in that fog says in so many words that
it "needs a second leaf to exist", and this is the ticket that makes a second leaf exist. No
design decision is open — [Design the coupled work unit](02-design-the-coupled-work-unit.md)
settled the shape and its 2026-09-11 amendment settled the clock.

Its seams are already seated and exercised by production, which is why this is a build and not a
redesign: `_stamp_identity` (11) means nothing slices a uid; `bind_crew(clocks=)`,
`drain_putaway_records(reset_clocks=)`, `_stock(charge_cut=)` and `count_put_cut` (12) are in;
`OneOwnerBundle` and the provider cursor (13) are in; `rule_pairs` in `restock_selection.json`
(14) is written and tested; and the torn-finalize window is closed (16).

## Question

Build 02's answer, sections 1 through 6, plus its amendment.

1. **The uid and `group_keys`.** `(label, 'coupled', arm_store, arm_ful)`; the payload carries
   `group_keys` and `_run_pool` iterates them instead of slicing. Flag-off keeps
   `(label, cfg_name, channel_key, strategy)` verbatim.
2. **The worker entry.** Split `_run_strategy_worker_impl` at its existing seam into
   `_build_leaf(args, channel) -> Leaf`, called twice, then ONE batch loop over both leaves.
   Not a coupled sibling entry point — 02 says why.
3. **`_prepare_channel_run` survives unforked**, called twice, with a thin `_prepare_site_run`
   composing the two results. `_derive_staffing_for_pair` must NOT be called twice.
4. **Both regime partitions are built BEFORE either manager exists**, and asserted to sum to the
   whole catalogue. The in-place `inventory.orders = [...]` at `strategy_runner.py:769-771` is
   safe only for one leaf; a second partition would filter an already-filtered list and produce
   a silently EMPTY channel.
5. **The clocks.** `recv_clock` and `put_clock` are site-wide (the amendment: a shared clock list
   cannot carry two epochs, and the coupled put base is the site day start); `arm_clock` stays
   per leaf. A leaf's span is its distinct `work_day` count, never `_arm_span_days`
   (`calendar-span-is-not-work-days`).
6. **The crew payload, where the fix is DELETION.** `put_crew` and `recv_crew` leave the per-leaf
   payload for unit scope; `_check_declared_crew` verifies them once per unit and `k_pickers` per
   leaf. This is what ends the double count at `workunits.py:366-369`.

**One thing to settle before writing any of it, and it is an ordering question, not a design
one:** whether a coupled unit can run its first batch with put-away still fielded per leaf — i.e.
whether this ticket lands before or after 04's `Inbound/putaway_pool.py`. Section 6 deletes the
per-leaf crew sizing that the current `_check_declared_crew` enforces, so the two may not be
separable. Establish that first and say so in the answer; if they are not separable, this ticket
grows to include 04's pool rule and the map's fog is corrected rather than this ticket's scope
being quietly widened.

**Explicitly NOT in scope** — each is its own patch of fog and each names this ticket as what it
waits on:
- 01's owner routing and the `{sku: leaf}` dict (the coordinator itself is already live).
- 05's `SiteGainBundle`, the second `_gain_bundle_for` call and the commensurability test.
- 03's `_site/` artifact declarations, `SITE_PHASES` and the contract bump.
- 07's site stage and `SiteContext`; 08's site-view composer.
- 10's `_reconcile_coupled_unit` and the torn-pair repair.
- 15's `reconcile_pair` and the site-scope uid clauses.
- 06's SPEC side (`PHASE2_PAIRS`, derived `CHANNEL_RESTOCKS`).

## What proves it

- **Flag-off is byte-identical, MEASURED.** Both preflight canaries (mixed and store-only)
  against a `git archive HEAD` copy, `tree shape UNCHANGED` — the acceptance 12, 13, 14 and 16
  all used. `strategy_runner.py` and `workunits.py` are in `contract.SHAPE_SOURCES`, so this
  costs the full preflight plus the arch chain regardless (16's correction).
- **A coupled unit actually runs**, end to end through the real pool, and writes two leaves whose
  `sim_meta.json` both exist — the completeness rule 10 rests on.
- **The empty-channel trap is planted**: a test that filters twice and asserts the partition sum,
  because the failure mode is a silent empty leaf, not an exception.
- **The double count is gone and visible**: the site put and receive crew appear ONCE per unit,
  and the per-leaf payload no longer carries them. Assert the payload, not only the behaviour.
- **Absolute put and travel numbers MOVE on a coupled run, and that is expected** — the map's
  Notes call it a comparability break of the same class as the four on the record. Record the
  measured size of the move; do not treat it as a regression.
- Nine verifiers green; `Tests/architecture` baselined against a `git archive HEAD` copy (four
  known reds plus the `.claude/worktrees/` fifth).

## Answer

**BUILT and live on `develop`** (`b8e6c770`, `beda6b77`). A coupled unit is
`(label, 'coupled', arm_store, arm_ful)`, two leaves prepared by `_prepare_site_run` and driven
through one batch loop, finalizing two groups from the `group_keys` it carries. It runs end to
end through the real seam, writes both leaves, and is proven **row-for-row identical to the two
units it replaces** — which is not what the ticket's acceptance expected, and the reason is the
ordering question.

### The ordering question, settled first — and the answer is not where the ticket looked

The ticket guessed **section 6**. Section 6 is separable: moving `put_crew`/`recv_crew` to unit
scope changes where a value is *stated*, not what is *built*, so it is byte-identical while the
crews are still fielded per leaf.

**Section 5 is the one that does not separate, and not because "the double count persists".**
`put_clock` is the absolute carry that a **batch-local** clock list is based from (`crew_clock`:
every clock starts at 0 each batch; `_put_base = max(bs.batch_start_time, put_clock)`). One
site-wide carry over **two independent lists** therefore makes leaf B's putters start where leaf
A's *finished* — two full crews serialized as if they were one. That is a **third model**,
neither today's nor the charter's, and it is the plausible-looking wrong number this repo's
memories are made of. `recv_clock` is the same argument over two docks: 01's coordinator is live
but per leaf, and its owner routing is explicitly out of this ticket.

So **the two site-wide carries move with the resources they belong to** — `put_clock` with 04's
shared clock list, `recv_clock` with 01's coupled coordinator — not with this ticket. This ticket
lands **before 04**, carrying sections 1, 2, 3, 4 and 6, and section 5 is 04's and 01's. The
ticket did **not** grow; the map's fog is corrected instead, which is what it asked for in the
other branch.

**That corrects the ticket's own acceptance.** "Absolute put and travel numbers MOVE on a coupled
run" is **false here** — they move when the pool lands. What replaces it is sharper:
`test_a_coupled_unit_matches_the_two_units_it_replaces` pins coupled == uncoupled row for row, so
every later ticket introduces **exactly one** measurable change, and that test fails naming the
leaf whose labour moved. A comparability break announced in advance by a test is worth more than
one measured after the fact.

### 1–6, as built

1. **The uid and `group_keys`.** `_stamp_site_identity` returns
   `(label, 'coupled', arm_store, arm_ful)` and carries both group keys plus `arm_keys`;
   `arm_key` is **None**, because a unit with two arms has no single arm and a reader taking one
   of them gets the other leaf's number with nothing to say so. `_run_pool` iterates `group_keys`
   for the finalize gate, the `expected_pick` attach and `record_arm`. Flag-off keeps
   `(label, cfg_name, channel_key, strategy)` verbatim.
2. **The worker entry.** `_build_leaf(args, unit=None) -> _Leaf`, then one batch loop over the
   unit's leaves. Not a coupled sibling entry point.
3. **`_prepare_channel_run` survives unforked**, called twice; `_prepare_site_run` adds only the
   pairing, the crew lift and the group keys. `_derive_staffing_for_pair` is untouched — it was
   already above the channel loop.
4. **The partition**, below.
5. **The clocks** — deferred, above. All three stay per leaf at this commit.
6. **The crew payload, deleted from the leaf.** Verified once per unit by `_check_site_crews`;
   `k_pickers` per leaf. A leaf that still carries one is **refused**, because per-leaf checking
   is exactly what guarded the double count into place.

### Deviations under force

- **`_Leaf` holds closures, not fields.** 02 says `_build_leaf -> Leaf`, and it returns one — but
  its two halves are `step(i)` and `finish()` closing over the setup, not ~104 attributes on a
  record. The census is why: **104 names are carried from the setup into the loop or the tail, 45
  of them rebound**, so a state object means rewriting 610 lines of loop body and 165 of tail by
  hand, where any miss is a silent wrong number. The closure form moves both **verbatim**
  (verified line-for-line against the pre-split file) and the carries survive through `nonlocal`
  exactly as they survived between iterations. The 74 per-iteration temporaries stayed local to
  `step` **only after checking that not one of them is read before it is written** inside a batch
  — a cross-iteration carry among them would have broken silently. The cost is that a driver
  cannot reach into a leaf; 04's pool and 01's coordinator are built ABOVE the leaves and
  injected, which is what both designs already say.
- **02 section 4 could not be built and did not need to be.** Each leaf calls
  `load_run_inventory` itself and that function does not cache, so there is no shared
  `inventory.orders` to filter twice and the trap 02 named cannot arise. The **assertion** it
  asked for is made where the facts are — the driver checks that every leaf saw the same
  catalogue and that the partitions sum to it — because the failure being guarded is a silently
  **empty** leaf, not an exception. `test_a_double_filtered_leaf_is_refused` plants it.
- **The charter's "coupling rides the inbound flag" is not a buildable rule.** Site-dock 06
  couples every cell **including its inbound-off pole**, so no flag implies coupling. It is
  DECLARED: `--couple-channels`, five seams, and `run_layout.json`'s `coupled` (v3) — the marker
  03 minted, which `run_restock_selection.select` has been refusing since 14 and which nothing
  could set until now. Resume restores it, because a run that resumed without it would rebuild
  per-channel units over a tree whose leaves were written by coupled ones.
- **One config per channel is refused, not paired by position.** 02 settled the arm pairing and
  said nothing about a config cross product; zipping two config sets here would make that
  decision silently.
- **11's `expected_pick` refusal is retired rather than kept.** A multi-leaf unit returns one
  result **per leaf**, each stating its own arm, so the single value that had to be refused no
  longer exists — 11 said outright the shape was 02's to settle, and this is it. What replaces
  the refusal is a **length check**: `group_keys` and the returned `leaves` are positional, and a
  disagreement is refused rather than zipped to the shorter side.

### Findings

- **The nine source-inspecting guards would have gone on passing while guarding nothing.**
  Pointed at `_run_strategy_worker_impl` they would have inspected the new 25-line driver and
  found their strings absent — or, worse for the `not in` assertions, absent and therefore
  *satisfied*. Two had to be made structural rather than literal to survive the move:
  `test_era_wiring`'s final-day-flush guard compared against a literal four spaces (now against
  `if pb:`'s own indent), and `test_receiving_params` asserted the crew is read from `args` (now
  that the read resolves through `_unit`, it asserts the resolution itself is from the function's
  arguments). Both mutation-checked afterwards.
- **A backbone edge had to be re-anchored on a NESTED closure.** `context/architecture.yml`
  declares an edge to `DeferredPickSimulation` from the worker body; the extractor attributes a
  call to the function it is *written in*, so the edge is now from `_step`, not `_build_leaf`. A
  curated backbone entry naming an outer function is only correct while the call happens to sit
  at the outer level — nothing says so, and the verifier's message ("no edge X -> Y in graph")
  does not hint at it.
- **`run_layout.json`'s field set is verified against its writer; the artifact catalogue is
  not.** `Tests/integration/test_run_layout.py` compares the written keys to the JSON schema's
  properties exactly, and caught the new field immediately. `context/artifacts.yml`'s `fields:`
  list for the same artifact is checked by nothing (14's finding, re-confirmed) — updating only
  the schema would have left the catalogue stale and green, so `test_couple_channels_param` now
  asserts both.
- **`--catalog-merge` seeded both new files' `purpose` from a docstring FRAGMENT again** (11's
  finding, reproduced exactly): not a `purpose: TODO`, so CLAUDE.md section 1's fill step does
  not see it and the entry reads like a description while saying nothing. Both were rewritten by
  hand.
- **A result dict had to start stating its own arm.** The parent could previously name the arm a
  result belongs to only by slicing `uid[3]`. That is the same defect 11 fixed on the *payload*
  side, left unfixed on the *result* side and invisible while every unit had one arm.

### Gates

2456 unit + integration (2 new test modules, 15 new tests), 41 e2e including 6 that drive a real
coupled unit end to end, both preflight canaries `tree shape UNCHANGED` on `5c9bc35db55b`, all
nine verifiers, arch chain regenerated. `Tests/architecture` is five red, every one baselined
against a `git archive` copy of `c96a4711`: four fail identically there and the fifth is
`.claude/worktrees/` only — the shape `arch-tier-is-red-on-head` records. Three guard mutations,
three caught.

### What this hands onward

- **04's pool inherits a live seam and a failing test.** When the shared clock list lands,
  `test_a_coupled_unit_matches_the_two_units_it_replaces` fails by construction; that failure is
  the measurement of the comparability break, and it names the leaf.
- **Section 5's two carries are now explicitly owned**: `put_clock` by 04, `recv_clock` by 01's
  owner routing. Neither is this ticket's leftover.
- The map's fog entry for the remaining builds drops everything this ticket landed.
