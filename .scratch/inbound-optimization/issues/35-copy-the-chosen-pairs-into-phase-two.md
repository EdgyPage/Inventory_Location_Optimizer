# Copy the chosen rule pairs into phase 2

Type: task
Status: resolved
Blocked by: 20

## Question

Copy phase 1's two hand-off values out of `restock_selection.json` and into
`Optimization/config/whatif_config.py`, which is the last edit between here and the
phase-2 launch. Both are declared `None` today and REFUSED rather than defaulted, so
this is a copy with no judgement in it — the judgement was 08's (the funnel's shape),
phase 1's (the ranking), and 20's (which families the evaluator can price).

From `comparison_20260913_113512/restock_selection.json`:

```python
PHASE2_PAIRS = [('rank_cartlabor', 'rank_minlabor'),
                ('rank_minlabor', 'tmin'),
                ('rank_labor', 'rank_labor'),
                ('tmin', 'rank_cartlabor'),
                ('rank_random', 'rank_popularity'),
                ('fifo', 'fifo')]          # the mandatory rider, already in `chosen`

PHASE2_STAFFING_PIN = {'mixed_20260816_131535__mixed_realistic_bell_lt0': '0ed2dd1582af'}
```

Read both off the artifact rather than off this file: a hand-typed pair list is the one
thing `validate_spec` cannot check against phase 1, and the pin is a digest.

Why this is a ticket and not a line in the launch: `validate_spec` refuses a phase-2 spec
whose `rule_pairs` name a family outside `Inbound.gain.FAITHFUL_GAIN_FAMILIES` while any
cell names a gain policy, so until
[Extend the gain bundles](20-extend-the-gain-bundles.md) landed, this copy put a spec in
the tree that refused at build. With 20 resolved the refusal is satisfied by all six
pairs; this ticket is what proves it, by building the spec.

What "done" looks like:

- both constants carry the artifact's values, with a comment naming the run root they came
  from (the run is the provenance; the values alone are not);
- `validate_spec` builds the `inbound_policies` spec without refusing — the check that all
  six pairs are now faithful, run rather than argued;
- `channel_restocks_for` derives the per-channel arm sets from the pairs (never authored
  beside them) and they match the artifact's `channels.<ch>.chosen`;
- `Tests/unit/test_campaign_cells_can_run.py` and `Tests/unit/test_funnel_window.py` still
  pass — several of their fixtures assume the constants are `None`, and any that no longer
  can must be reshaped rather than deleted.

Then phase 2 launches: 120 coupled units, 30.8–35.3 h of unit-seconds, ~164 GiB, ~8.6–9.7 h
wall at 4 workers (31's clock; do not mix with 24's). After it, publish.

## Answer

**Done. Both constants carry phase 1's values, the spec builds, and phase 2 has nothing left
between it and a launch.**

Read off `comparison_20260913_113512/restock_selection.json` rather than off this ticket, via
`COMPARISON_OUTPUT_DIR`, by a script that renders the artifact's fields as source — so the one
thing `validate_spec` cannot check against phase 1 was never hand-typed. The artifact's values
matched this ticket's transcription exactly, which is a coincidence worth nothing and was not
relied on. Provenance rides in the comment at each constant: run root, `repo_commit`
`ba055dd0747f`, cell `k1_off`, sampler v3, 40 batches, metric `total_production_time`.

### The four done-criteria, run rather than argued

1. **The constants carry the artifact's values** — `PHASE2_PAIRS` equals `rule_pairs.chosen`
   (6 pairs), `PHASE2_STAFFING_PIN` equals `staffing.pin` (1 inventory pair).
2. **`validate_spec` builds `inbound_policies` without refusing.** `get_spec` returns the spec
   rather than raising, which is the check that all six pairs are now faithful — run, not
   argued. Independently: the union of families across the pairs is
   `{fifo, rank_cartlabor, rank_labor, rank_minlabor, rank_popularity, rank_random, tmin}` and
   `set(...) - set(Inbound.gain.FAITHFUL_GAIN_FAMILIES)` is empty. Ticket 20's extension covers
   exactly the three the artifact's `needs_bundle_extension` named — `rank_cartlabor`,
   `rank_minlabor`, `rank_labor` — with nothing left over and nothing short.
3. **`channel_restocks_for` derives the columns, and they match the artifact.** Store comes out
   `(rank_cartlabor, rank_minlabor, rank_labor, tmin, rank_random, fifo)`, fulfillment
   `(rank_minlabor, tmin, rank_labor, rank_cartlabor, rank_popularity, fifo)`. Each is its
   channel's `chosen` **in rank order**, with the rider appended — the rider rides outside k, so
   the derived column is `chosen + rider` and *not* equal to `chosen`; a check written as plain
   equality would have failed on a correct derivation. Both are `tuple`, not `set`, so the rank
   the zip reads survives.
4. **The tests pass.** The two this ticket named — `test_campaign_cells_can_run.py` and
   `test_funnel_window.py` — passed untouched. The None-assuming fixtures turned out to live in
   a third file, `test_funnel_spec_pairs.py`, which went 7 red; all seven were reshaped, none
   deleted. Full `Tests/unit`: **2438 passed, 1 skipped**. `path_guard` and `docref_guard` both
   clean.

### What the seven reshapes actually were, and the one thing they cost

Two distinct causes, not one:

- **Three were neutrality sweeps** (`..._reproduce_the_committed_install`,
  `..._descriptors_arm_field_is_unchanged...`, `..._no_committed_spec_moves_an_arm`), each
  parametrized over the whole registry and each comparing the derivation against a frozen
  pre-site-dock-23 head expression. They covered `inbound_policies` only *vacuously*: both
  sides returned `None` because it declared no arm set at all. With pairs committed it is by
  construction the spec that moves — rank-ordered columns against a grid-order filter — so it
  now falls outside a claim that was always about the flat-armed specs the archive was built
  from. Split **derived from the spec** (`_FLAT_SPECS` / `_PAIR_SPECS`), never by name, so a
  second pair campaign lands on the right side the day it is registered.
- **Four asserted the constants were still `None`**, directly or through the committed spec.
  The gates they guard are all still live; what changed is the reachable defect, which is no
  longer "phase 1 hasn't run" but "an edit dropped the pairing". Each now poses the committed
  campaign with `rule_pairs` taken back off — valid in every other respect, which is what makes
  the refusal attributable to the one thing removed. `test_every_other_registered_spec_...`
  lost its `inbound_policies` exemption and its "other", the kind of exemption that outlives
  its reason in silence.

Two additions, because excluding specs from a sweep is how a suite goes quietly vacuous:

- `test_the_registry_splits_into_flat_and_pair_specs_and_neither_side_is_empty` — a sweep whose
  parameter list empties reports PASSED for every name it no longer visits.
- `test_a_committed_pair_specs_columns_are_its_own_pairing_sliced` — the committed campaign's
  own columns had never been asserted, only synthetic pairings had. Asserted against the spec's
  own `rule_pairs` and round-tripped back through a zip, **not** against the artifact: the
  artifact is on the results drive and `Tests/` must not grow a dependency on a run tree.

**The cost, found by mutation and fixed.** The split was first written as
`rule_pairs_of(SPECS[n]) is None`, which normalises — and runs at *collection*. Mutating the
constant three ways (a pair transposed, the rider dropped, a rule twice in a column) errored the
whole module out at collection, taking with it the dozen refusal tests whose entire job is to
say *which* shape broke. Rewritten to test the key rather than normalise it; all five mutations
now land as ordinary failures with the module intact. Non-vacuity was checked by mutation
rather than assumed: `pairs → None`, a transposed pair, the rider dropped, a repeated rule, and
an emptied pin all go red.

### Caveat carried forward, unchanged

The pairing is one of several equally defensible draws, not a derived optimum — store's top
three separate by 0.005% and 0.12%, fulfillment's top eight span 0.73%, and the pairing is
rank-aligned. That is now written at the constant itself, where the next reader meets it, rather
than only here: the campaign publishes it as a caveat. What the campaign reads is the 6.1% /
8.0% gap down to the order-blind `fifo` control, and for inbound the within-pair comparison
across cells — neither of which turns on which near-tie took which rank.

### Committed, and what is now takeable

`Optimization/config/whatif_config.py` + `Tests/unit/test_funnel_spec_pairs.py` on `develop`.
The architecture-layer changes already sitting in the working tree are someone else's pending
review and were deliberately left unstaged.

Phase 2 is launchable: **6 pairs x 2 stock_modes = 12 coupled units per (cell, inventory pair)**,
120 units over the ten cells, 30.8-35.3 h of unit-seconds, ~164 GiB, ~8.6-9.7 h wall at 4
workers (31's clock; do not mix with 24's). The launch is deliberately **not** part of this
ticket — it is a multi-hour detached driver, and per
`launch-long-drivers-detached` it needs no console and a keep-awake. It is now the only thing
left on this map.

## Comments

2026-09-13, from resolving [Extend the gain bundles](20-extend-the-gain-bundles.md): this
ticket exists because 20's resolution made it takeable and nothing else stands between the
map and phase 2. One caveat rides along, from the ranking rather than from the copy: store's
top three are separated by 0.005% and 0.12% and fulfillment's top eight span 0.73%, and the
pairing is rank-aligned — so the diagonal above is one of several equally defensible draws
rather than a derived optimum. It is a caveat the campaign publishes, not a reason to
re-pair; the campaign's real signal is the 6.1% / 8.0% gap down to the order-blind `fifo`
control, and the inbound comparison is within-pair across cells.
