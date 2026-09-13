# Gate the campaign axis on what a coupled run can do

Type: task
Status: resolved
Blocked by: 32

Surfaced 2026-09-12 by [Measure what a gain cell actually costs](31-measure-what-a-gain-cell-costs.md).

Blocked by [Decide the futuresight family's place](32-decide-the-futuresight-familys-place.md)
only because the gate would fail on today's axis -- which is the point of it, and also why it
cannot land until that decision has moved the axis or the composer.

## Question

Close the class of failure 31 found, not just the instance.

`phase2_inbound_axis()` declares ten cells. `Inbound/site_space.py` refuses two of them under
coupling. Both files are honest and both are tested; **nothing checks them against each other**,
so the axis has named two unrunnable cells since site-dock closed and the first thing that would
have noticed is a phase-2 launch paying its freeze and then failing 24 units.

The existing refusals do not help here, and it is worth being precise about why:
`validate_spec` fires before a directory exists and reads the spec's SHAPE (pairs, pin, arm
set); `_inbound_axis` refuses unknown keys, missing suffixes, duplicates and partial entries.
Neither knows what the coupled composer will decline at a drain, because that is a property of
the POLICY the cell names, not of the spec's shape.

So: what is the cheapest check that would have caught this, and where does it live?

Sketch, to be argued with rather than implemented as written -- a test that walks
`phase2_inbound_axis()` (and every registered campaign spec's inbound axis), and for each cell
asserts its named yard/dock policy can survive a two-leaf composition. The pieces are already
there: `compose_site_view` is a pure function over frozen data, and 31 exercised exactly this
in four lines against a hand-built `SpaceView` pair. Questions the build has to answer:

- **Test, or spec-time refusal?** A test fails in CI at zero cost to a launch; a refusal in
  `validate_spec` fails the launch itself, before the freeze. They are not exclusive and the
  cheap one may be enough.
- **What is the general predicate?** "Carries a futuresight window" is the instance. The class
  is "this cell's policy needs a per-leaf structure the composer does not compose". Decide
  whether that can be asked generically or whether the honest version is a small declared list
  the composer and the axis both read.
- **The same gap in the other direction**: `_gain_bundle_for` raises for any restock family
  outside `FAITHFUL_GAIN_FAMILIES`, and phase 2's arm set is phase 1's output, so the same
  shape of failure -- a declared campaign that cannot run -- is reachable from the ARM axis too.
  `run_restock_selection` already enforces the cap and backfills, so that side may be covered;
  check, and say so either way.

## Comments

2026-09-13, from resolving [Decide the futuresight family's place](32-decide-the-futuresight-familys-place.md):
**the decision was BUILD, so the axis keeps its ten cells and this ticket's gate now points the
other way.** Three consequences for the work here:

- **The instance is being removed, not the class.** Once
  [Build the coupled futuresight window zip](34-build-the-futuresight-window-zip.md) lands,
  `compose_site_view` composes windows and no declared cell fails -- so a check written today
  against the futuresight example would be GREEN on arrival and prove nothing. The gap 31 found
  is that nothing joins `phase2_inbound_axis()` to the coupled composer's refusals at all; that
  gap is unchanged and is what this ticket closes.
- **Which makes the "what is the general predicate?" question load-bearing rather than
  optional.** With the one known instance gone, a check that can only express "carries a
  futuresight window" has nothing left to catch. The honest small declared list the sketch
  offers as the fallback is now the MINIMUM bar, and it has to be a list both the composer and
  the axis read -- a list only one of them reads is the same two-honest-files gap in a new
  place.
- **The sabotage direction is the only way to keep this test non-vacuous.** There will be no
  failing cell to point at, so the test needs a synthesised one (a cell whose policy the
  composer declines) to prove the check can actually fail -- memory `real-test-coverage-is-317`
  and the repo's non-vacuity discipline. Order matters: this can be written and proven against
  the CURRENT composer, which still refuses, if it lands before 34.

The third sub-question (the same shape of gap reachable from the ARM axis via
`FAITHFUL_GAIN_FAMILIES`) is untouched by 32 and still needs checking either way.

## Answer

RESOLVED 2026-09-13. **BOTH, and they are not redundant — but the load-bearing decision was
neither of the two the ticket named.** The general predicate is not "which policies are bad";
it is **"does this cell's policy need a `SpaceView` structure a composed view does not
carry"**, asked against what the composer COMPOSES rather than against what it refuses. That
one turn is what keeps the check alive after 34 empties the refusal set.

### 1. The join: two declarations, one predicate

Neither existing refusal could see this class because neither knows what a POLICY does:
`validate_spec` reads the spec's shape, `_inbound_axis` reads the axis's keys. The gap is
closed by declaring the two halves and putting them together in exactly one place:

* **`Inbound/priorities.py: POLICY_VIEW_NEEDS`** — the `SpaceView` fields each yard/dock
  entry reads, registered BESIDE the entry (the gain family adds its four rows from
  `Inbound/gain.py`, in the same breath as its ordering functions). An empty set is a real
  answer (`fifo`/`lifo` never open the view); a missing row is not — `view_needs` raises,
  because a policy answering "reads nothing" by default would pass every gate.
* **`Inbound/site_space.py: COMPOSED_VIEW_FIELDS` / `UNCOMPOSED_VIEW_FIELDS`** — an
  EXHAUSTIVE partition of `SpaceView.__slots__`. The composer's refusal is now driven by
  the second set generically instead of a hand-written `window` branch, so the refusal
  cannot fall behind the declaration.
* **`site_space.uncomposable_policies(names) -> {policy: missing fields}`** — the only place
  the two meet, a pure function over declared lists. It needs no warehouse, which is what
  lets a spec-time gate ask it.

`Optimization/config/whatif_config.py` reads both registries the same way
`run_restock_selection` already reads `FAITHFUL_GAIN_FAMILIES`: a declared list, never the
simulation. No new architecture boundary — `opt_config -> inbound` is legal and now real
(verified against a freshly extracted graph).

### 2. Test or spec-time refusal — both, doing different jobs

* **`validate_spec` (`_refuse_unrunnable_cells`)** is the one that would actually have caught
  31. The probe that burned the freeze ran a THROWAWAY spec (`_probe_gaincost`, never on
  `develop`), so no committed test could have seen it — but it copied `PHASE2_RUN_DEFAULTS`
  verbatim and came through `get_spec` like every run does. It now names the dead cells
  before the run directory exists.
* **`Tests/unit/test_campaign_cells_can_run.py`** is the one that costs a launch nothing and
  carries the non-vacuity proof.

`validate_spec` was the seam, not `_inbound_axis`: the gate is conditional on COUPLING (a
one-leaf composition is the view by identity, so `futuresight` is perfectly runnable
uncoupled, and phase 1 is uncoupled by design), and `validate_spec` is the only one of the
two that sees `run_defaults`. Its docstring's "reads the spec and the rule grid, nothing
else" was rewritten rather than left standing.

**The residual hole, stated rather than papered over:** `--couple-channels` typed over a spec
whose `run_defaults` do not declare it is out of reach, because the gate reads the SPEC's
coupling — the same source the `rule_pairs` refusal above it reads. No registered spec with
an inbound axis leaves coupling to the command line, and a gate that guessed at the effective
value would refuse legal runs; that trade is written on the check.

### 3. What made the test survive 34 — three things, all deliberate

* **The predicate subtracts the composed set** rather than intersecting the refused one. The
  two are equal today (they partition the view) and will not be after 34: an intersection
  with an empty set can never catch anything again. Subtraction still has an answer for a
  field nobody has classified, and for one that is not on the view at all.
* **The partition is asserted exhaustive against `SpaceView.__slots__`.** A new field lands
  in neither set and fails, so classifying it is a decision somebody makes rather than a
  default somebody inherits — which is how the window got here in the first place. Deriving
  either set from the other would hand the new field the cheap default silently.
* **A composed field must SURVIVE a composition, not merely be allowed into one.** Refusal
  and silent drop are the same defect arriving two different ways, and a check written only
  against the refusal sees one of them. `window=None` is hard-coded in the composed view's
  return today; sabotaged (declared composed, still dropped), the suite fails on exactly
  that test.

Non-vacuity proved by sabotage, three ways, each run and each caught: a careless 34 (window
declared composed but still dropped) → the survives-composition test; a new `SpaceView` field
nobody classified → the partition test; a registered policy with no declared reads → six
tests, loudly. The synthesised saboteur needs a structure that is **not on the view at all**,
so it keeps failing after `UNCOMPOSED_VIEW_FIELDS` empties.

`_KNOWN_DEAD = {'fsight_w5', 'fsight_wall'}` is pinned rather than asserted empty, because
the two cells really are dead until 34 lands. Emptying that set is the visible half of 34,
and the test says so in its own failure message. It is not an allowlist: a THIRD name
appearing is the defect this module exists to catch.

### 4. The third sub-question: the ARM axis is NOT covered — it is now

Checked, and the answer is no. `run_restock_selection` enforces the cap and backfills, but
`PHASE2_PAIRS` is a HAND-COPIED artifact field and `_rule_pairs` validates it against
`RESTOCK_KEYS` (the rule universe) and never against `FAITHFUL_GAIN_FAMILIES`. The artifact
legitimately reports chosen families that still need `_gain_bundle_for` extended
(`needs_bundle_extension`, ticket 20), so copying a ranking before that extension lands is a
reachable edit — and every gain cell of the campaign then dies at its first drain, the same
failure shape from the other axis.

Now refused in the same function, conditional on some cell naming a `GAIN_POLICIES` entry
(`_gain_bundle_for` is only called then, so a fifo/lifo-only matrix may sweep any rule — and
a test pins that the condition is real, not decoration).

### 5. What landed

* `Inbound/priorities.py` — `POLICY_VIEW_NEEDS`, `view_needs`.
* `Inbound/gain.py` — the gain family's four rows, registered beside its entries.
* `Inbound/site_space.py` — `COMPOSED_VIEW_FIELDS` / `UNCOMPOSED_VIEW_FIELDS` (carrying the
  zip rule), `uncomposable_policies`, and the refusal driven by the declaration.
* `Optimization/config/whatif_config.py` — `inbound_policies_of`, `_refuse_unrunnable_cells`,
  called last from `validate_spec`.
* `Tests/unit/test_campaign_cells_can_run.py` — 22 tests, new.
* `Tests/unit/test_site_space_view.py` — the refusal message is generic now, so its match is
  the field name; `Tests/unit/test_funnel_window.py` — the pin test's happy path states the
  two things "valid in every other respect" now requires.

`Tests/unit` 2415 passed. `verify_context`, `path_guard`, `docref_guard` clean; architecture
boundaries clean against a freshly extracted graph (the catalog gap and the preflight
fingerprint nag both predate this change and belong to the maintainer pass).
