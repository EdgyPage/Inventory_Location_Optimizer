# Re-shape the funnel spec for arm pairs

Type: task
Status: resolved

AFK. **Takeable now** — graduated from the map's "remaining builds" fog by the execution
override (map Notes). [Re-shape the funnel for arm pairs](06-reshape-the-funnel-for-arm-pairs.md)
settled the shape, [Re-shape the selection hand-off](14-reshape-the-selection-handoff.md) built
the HAND-OFF side (an ordered list of rule pairs, and `select()` already refuses a coupled run),
and [Build the coupled work unit](18-build-the-coupled-work-unit.md) built the CONSUMER. What
waits is the spec side, which is now the only half missing. It blocks nothing on this map and
nothing blocks it, so it runs alongside [Build the site space view](20-build-the-site-space-view.md)
and [Build the coupled resume reconciler](22-build-the-coupled-resume-reconciler.md).

## Question

Build 06's spec side: `PHASE2_ARMS` becoming `PHASE2_PAIRS`, `CHANNEL_RESTOCKS` derived from the
rule-pair list rather than declared beside it, the pair-shaped shape refusal in
`_run_whatif_matrix`, and the rewrite of `whatif_config.py:166-170` — whose cross-phase claim
06's own answer makes false, so leaving it is publishing a false statement about the campaign.

Three things later tickets settled that the spec must now reflect:

- **Coupling is DECLARED, not derived from the inbound flag** (`--couple-channels`, 18). Every
  cell couples, including its `inb_off` pole, so the spec must carry the flag on the cell rather
  than inferring it — the charter's original "coupling rides the inbound flag" is unbuildable.
- **`_prepare_site_run` REFUSES more than one config per channel** and refuses ragged arm lists
  (18). The spec is where `CHANNEL_RESTOCKS` is curated, so a spec that produces either is a
  run that dies in the parent rather than a run that silently truncates — worth a spec-side
  check that says so first.
- **Phase 1 is UNCOUPLED and phase 2 is coupled**, so phase 1 ranks placement arms under 2x the
  site put labour ([Build the site put-away pool](19-build-the-site-putaway-pool.md) measured
  it). 06 section 3 says the caveat is a STAMP; this is where the stamp is written.

## What proves it

- **The pair-shaped refusal fails on the defect**, not merely absent from the happy path: an
  arm-shaped `PHASE2_PAIRS` and a ragged pair list both raise, mutation-checked.
- **`CHANNEL_RESTOCKS` derived == `CHANNEL_RESTOCKS` declared today**, asserted, so the
  derivation is proven to reproduce the curated value before it replaces it.
- **No run output moves**: the spec is parsed, not executed, so both preflight canaries and a
  row-level diff against a `git archive HEAD` copy on one uncoupled run.
- Nine verifiers; `Tests/architecture` baselined against a `git archive` copy (five known reds).

## Answer

**BUILT and left in the working tree** (not committed — two other sessions share this tree).
`PHASE2_ARMS` is `PHASE2_PAIRS`, the per-channel arm sets are DERIVED from the pairing,
`get_spec` refuses ten shapes a spec cannot mean, and `whatif_config.py:166-170`'s
cross-phase claim is rewritten. **One half is deliberately NOT wired and is handed onward
below**: the driver-side install lives in `Optimization/simdriver/scenario.py`, which another
session owns this session.

### What is in

1. **`PHASE2_PAIRS`** replaces `PHASE2_ARMS` — an ordered tuple of `(store_rule,
   fulfillment_rule)`, store first, `restock_selection.json`'s `rule_pairs.chosen`. Still
   `None`. `PHASE2_RIDER = ('fifo', 'fifo')` is the mandatory rider, as a pair.
2. **The derivation.** `channel_restocks_for(spec, channels)` returns what a spec installs into
   `strategies.CHANNEL_RESTOCKS` — `None` (leave as committed), `{ch: None}` for `'all'`,
   `{ch: arms}` for a flat subset, and for a rule-pair spec the two columns of the pairing,
   each in RANK order. Proven equal to today's driver install for every registered spec and
   both catalogue shapes.
3. **The refusals**, all at `get_spec`, which is the single door a run comes through and fires
   before the run directory exists: a stale FLAT arm tuple under the new name; a malformed
   entry (not a 2-tuple, an unknown rule, an empty or non-list `rule_pairs`); no
   `('fifo','fifo')` rider; a rule ranked twice in one column; `arms` and `rule_pairs`
   declared together; `rule_pairs` without `couple_channels`; an inbound matrix stating no arm
   set; and (in the derivation) a pair spec on a single-channel catalogue.
4. **Coupling is declared on the run.** `PHASE2_RUN_DEFAULTS = {**ERA_RUN_DEFAULTS,
   'couple_channels': True}`, carried by `inbound_policies`. `run_defaults` rather than a
   remembered command line, so `_apply_run_defaults` notes an explicit disagreement instead of
   swallowing it, and every cell couples including `inb_off`.
5. **`phase2_inbound_axis`'s docstring**, rewritten rather than amended: the anchor is the
   inbound-OFF pole INSIDE the coupled model, phase 1 ranks under 2x the site put labour
   (site-dock 19 measured it), a RANK comparison across the boundary is no more invariant than
   an hours one, the caveat is the `put_regime: 'per-leaf'` stamp plus the published caveat,
   and there is no runtime gate because nothing reads two run roots.

### Five deviations under force

- **The columns are ordered TUPLES, not the sets section 2 wrote** (`{p[0] for p in pairs}`).
  A set destroys rank, and rank is the pairing — see the first finding.
- **`strategies_for` had to change with them.** It re-imposed the grid's order on any subset,
  which is where a derived column's rank would have been thrown away. It now orders by
  (initial, the caller's rule order), refuses a `set`, and is byte-identical for any
  grid-ordered subset — which every committed value and every registered spec's arm set is,
  asserted exhaustively.
- **`inbound_policies` carries NO `arms` key at all.** A flat arm set beside the pairs is the
  one the driver currently reads; it installs a single tuple into both channels, the two
  columns become the same set, and `_prepare_site_run` zips two identical lists into a
  campaign that runs to completion and is not the diagonal phase 1 ranked. With the key absent
  the only reachable failure while the install is unwired is a loud one.
- **Five refusals beyond the ticket's two**, each guarding a silent defect rather than a typo:
  the duplicate-in-a-column (below), the double declaration, the undeclared coupling, the
  single-channel pair spec, and the set-typed restock list.
- **`run_layout.json`'s `arms` now resolves through `swept_rules_of`.** Not in the ticket; it
  became false the moment a spec could state its arms as pairs. A pair spec has no `arms`, and
  `None` in that field means "the committed full suite" — the descriptor would have claimed 34
  arms for a run that swept a twelve-rule diagonal. Identical for every arm-shaped spec, and
  measured identical in the neutrality run (30 `run_layout.json` keys, only `base` and
  `created` differ).

### One thing the ticket asked for that is NOT spec-side

**"More than one config per channel" cannot be checked here, and the reason is worth recording.**
A spec declares cells, arms and run defaults; the CONFIGS come from
`sim_config.STORE_CONFIGS` / `FULFILLMENT_CONFIGS` and the command line, never from the spec —
so no spec can produce the shape `_prepare_site_run` refuses, and a "spec-side check that says
so first" would have to reach out of a pure validator into mutable run state. The half of that
refusal a spec CAN reach is the channel set, and that one is made here:
`channel_restocks_for` refuses a rule-pair spec on a single-channel catalogue. An earlier gate
for the config COUNT belongs in the driver beside `_check_era_flags`, where both the flag and
the config lists are already resolved; it is deliberately not built here because the correct
predicate has to know whether the catalogue is mixed (a store-only catalogue under
`--couple-channels` legitimately runs uncoupled leaves — `_build_work_units` gates on
`couple_channels() and mixed`), and getting that wrong would refuse a run that works today.

### Findings

- **`strategies_for` destroyed the diagonal one level below where 06 looked.** Section 0's
  finding was `sorted(set(arms))` in the selection artifact; the same shape sat in the
  consumer. `_prepare_site_run` zips the two channels' lists AS `strategies_for` RETURNS THEM,
  and `[s for s in STRATEGIES if s.restock in restocks]` returns grid order, not the caller's.
  So a correctly derived per-channel column would have been re-sorted on the way in and rank 1
  paired against whichever rule sits earliest in `_RESTOCKS` — a real campaign, running to
  completion, with nothing raising. **Deriving `CHANNEL_RESTOCKS` from the pairing is not
  sufficient on its own**; the ordering had to become load-bearing at the same time.
- **The rider can make the two columns ragged, and the rider is appended automatically.**
  `run_restock_selection` appends `('fifo','fifo')` when that PAIR is absent — which it is
  when phase 1 ranked `fifo` on one side only. `('fifo','rank_labor')` plus the rider gives the
  store column `fifo` twice; distinct rules are what a channel sweeps, so the column collapses
  to k-1 while fulfillment keeps k, and `_prepare_site_run` refuses by LENGTH after the parent
  has planned the inventory and prepared both leaves. Refused at spec build now, naming the
  rule and both of its ranks. This is a real reachable interaction between 14's rider rule and
  18's ragged refusal that neither ticket could see alone.
- **Both config files are `contract.SHAPE_SOURCES`** (so is `run_simulation.py`), so a pure
  comment-and-constant edit here moves the run-tree source fingerprint and costs a preflight
  canary pair — 14's finding, re-confirmed from the other side of the funnel.
- **Figures are not byte-reproducible run to run.** A control that ran the UNCHANGED HEAD copy
  twice into two output dirs produced **51 of 51 PNGs pixel-differing** (compared on the IDAT
  chunks, so this is not metadata). Any neutrality claim that diffs figure bytes will report a
  break that is not there; the evidence has to be DB rows and JSON keys. Worth knowing before
  the next comparability measurement.

### What proves it

- **Derived == declared today, asserted**, not eyeballed: a reference implementation
  transcribed from `_run_whatif_matrix` is compared against `channel_restocks_for` for all
  seven registered specs x both catalogue shapes, and a like-with-like rule-pair list derives
  exactly the flat arm set the same campaign would have declared.
- **The rank order survives the whole chain**: pairs -> columns -> `strategies_for` -> the zip
  `_prepare_site_run` does, asserted to return the rule pairs that went in, each exactly twice
  (once per `stock_mode`), on an ASYMMETRIC pairing so the diagonal and the grid order cannot
  agree by accident.
- **Seventeen mutations planted, seventeen caught.** Every refusal disabled in turn, the
  columns derived as sets (06's literal text), `strategies_for` reverted to the grid-order
  filter and to a rank-only key, `get_spec` not validating, the campaign dropping its coupling
  declaration, `swept_rules_of` ignoring the pairing, and the driver reverting to the old
  descriptor expression.
- **No run output moves, measured.** Two `git archive HEAD` copies — one untouched, one
  carrying only this ticket's three source files — ran both preflight canaries over the same
  catalogue. Tree shape identical (333 and 94 files, same relative paths); **762,769 DB rows
  across 28 distinct tables** identical after stripping `simulation_runs.created`; the only
  residual row difference is `runtime_metrics.runtime`, whose differing columns are exclusively
  wall-clock seconds and peak RSS (every structural column identical). `run_layout.json` 30
  keys / 2 differing (`base`, `created` — **`arms` identical**); `run_spec.json` 1,798 keys /
  5 differing (the interpreter path in the two copies, plus four wall-clock timings). Two
  copies rather than working-tree-vs-HEAD because the other two sessions' uncommitted edits
  would otherwise have landed in the diff.
- Gates: `Tests/unit` + `Tests/integration` **2,580 passed, 1 skipped** (61 new in one new
  module); `Tests/e2e` **42 passed, 1 skipped**; seven of the nine verifiers OK
  (`verify_architecture` is the arch-chain debt below; preflight was not run — it rewrites
  shared files another session owns this session).
- `Tests/architecture`: **8 new reds attributable to this ticket**, isolated by running the
  suite in a HEAD copy carrying only this ticket's files (16 reds) against the untouched copy
  (8 reds, the `arch-tier-is-red-on-head` baseline plus four copy artefacts). All eight are
  arch-layer STALENESS — `graph.json`, `nodes.json`, `architecture.yml`, `files.yml` — with no
  boundary violation and no backbone-edge break; `verify_architecture` names exactly three
  items: a stale graph, the new test module uncatalogued, and `PHASE2_ARMS` no longer resolving.

### What this hands onward

- **The driver-side install is NOT wired, by constraint.** `_run_whatif_matrix`
  (`Optimization/simdriver/scenario.py:98-126`) still installs a single flat `arms` tuple into
  every channel, so a filled `PHASE2_PAIRS` refuses at `get_spec`'s inbound check rather than
  running. The change is small and stated here so it is not re-derived: replace the arm-override
  block with `validate_spec(spec)` plus
  `installed = channel_restocks_for(spec, CONFIG['channels'])`, and when it is not `None` loop
  `strategies.CHANNEL_RESTOCKS[ch] = rules` / `CONFIG['channels'][ch]['restocks'] =
  strategies.restocks_for(ch)` per channel; keep today's flat `'fifo' not in arms` refusal for
  arm-shaped specs (the pair-shaped rider check does not cover them), and make the
  `arms={spec.get("arms")!r}` log line pair-aware. Until then the campaign cannot launch —
  which it could not anyway, since `PHASE2_PAIRS` is `None`.
- **The arch chain owes four regenerations plus one hand fill**: `extract.py --write`,
  `--catalog-merge` (the new `Tests/unit/test_funnel_spec_pairs.py` entry, and a `purpose` for
  it — expect the docstring-FRAGMENT seeding 11, 18 and 19 all hit), `--write-nodes`,
  `render_html.py --build`. `context/files.yml`'s `key_symbols` for `whatif_config.py` names
  `PHASE2_ARMS`, which no longer exists.
- **Preflight owes a canary pair**: all three edited source files are `SHAPE_SOURCES`. The
  neutrality run above IS that pair by construction (both canaries, `CANARY_ARGS`, tree shape
  unchanged), so the `--accept` should be uneventful.
- **The campaign's remaining hand-off is one copy**: `PHASE2_PAIRS = <rule_pairs.chosen>` out
  of `restock_selection.json` once phase 1 has run. Everything else about the arm sets is
  derived from it.

## Addendum — the driver-side install, wired (parent session)

The answer above hands onward "the driver-side install is NOT wired, by constraint". That
constraint was mine, not the ticket's: `Optimization/simdriver/scenario.py` was fenced off so two
sessions could run in one working tree. With both sessions finished it is nobody's, so the last
mile is in rather than left as a half.

`_run_whatif_matrix` now **installs an arm set and never authors one**: `validate_spec(spec)`,
then `channel_restocks_for(spec, CONFIG['channels'])`, then one loop writing
`strategies.CHANNEL_RESTOCKS[ch]` and refreshing `CONFIG['channels'][ch]['restocks']`. The log
line is pair-aware — `spec.get('arms')` is None on a coupled campaign, and a line reading
`arms=None` there would say "the committed default", which is the one thing the refusal exists to
prevent — so it prints `pairs=` / `arms=` from `swept_rules_of`.

**Two branches came out because no input could reach them, and neither was in the ticket.**

1. **The cells-based "an inbound cell matrix states no arm set" refusal.** It moved into
   `validate_spec`, and that is STRICTLY STRONGER rather than equivalent: a cell's `inbound` is
   built FROM `spec['inbound']` (`cells._inbound_axis`), so the two can never disagree, and the
   cell form additionally let a SINGLE-cell inbound spec through by also requiring
   `len(cells) > 1`. Two refusals for one rule is how one of them ends up saying something
   slightly different.
2. **The `if _pairs is None:` guard around the flat `fifo` rider check.** `validate_spec` wants
   the rider as a PAIR — `('fifo', 'fifo')` — which puts `fifo` in BOTH derived columns, so any
   pair spec reaching the flat check has already satisfied it. The guard existed to skip a check
   that could not fire. Both were found by MUTATION: disabling each changed nothing, which is
   what a dead branch looks like from the outside.

**Gates for the addendum:** 68 tests in `Tests/unit/test_funnel_spec_pairs.py` (5 new, driving
the real `_run_whatif_matrix` with only `_run_scenario` stubbed — a single-cell spec skips the
freeze, so the install is the only thing that happens first); **4 mutations, 4 caught** (the
driver reverting to the flat arms-only install, `CONFIG['restocks']` not refreshed, the flat rider
refusal removed, the driver no longer validating a hand-built spec).

The transcribed reference implementation in that test file was relabelled: it was "as it stands
BEFORE the derivation is wired in", and it is now a FROZEN COPY of the behaviour being preserved.
That distinction is the only thing keeping "derived == declared today" a claim rather than a
tautology once the driver reads the derivation.

**What is still open:** the campaign's one remaining hand-off, `PHASE2_PAIRS =
<rule_pairs.chosen>` out of `restock_selection.json` once phase 1 has run. Everything else about
the arm sets is derived from it.
