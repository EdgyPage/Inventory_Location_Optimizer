# Build the coupled work unit and its two-leaf worker

Type: task
Status: open
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
