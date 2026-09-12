# Build the composite gain bundle

Type: task
Status: resolved
Blocked by: 21

AFK. Graduated from the map's "remaining builds" fog by the execution override (map Notes).
[Design the composite gain bundle](05-design-the-composite-gain-bundle.md) settled the shape and
[Seat the one-owner bundle indirection](13-seat-the-one-owner-bundle-indirection.md) seated the
cursor and the provider protocol it hangs on — the evaluator already resolves arm machinery per
`BinKey` owner through `for_key`, and today's single-leaf run answers every key with one
instance. What is missing is a SECOND owner, which is
[Build the coupled receiving coordinator](21-build-the-coupled-receiving-coordinator.md)'s
`{sku: leaf}` owner dict.

## Question

Build 05's answer: `SiteGainBundle` itself, the second `_gain_bundle_for` call, its refusal when
two owners' gate knobs disagree (13), and the three-part commensurability test **including its
sabotage**.

**The commensurability claim is the charter's, and it is stated so it can be falsified.** Gain
prices a mixed trailer per unit, keyed by owning channel, summed to one trailer score in hours.
The objective is already denominated in put + pick hours from the shared cost model with no
per-channel weighting (inbound-optimization decision 10), so the hours are commensurable by
construction — but that quietly decides a fulfillment hour and a store hour are worth the same to
the site. The charter requires that be TESTED, not assumed, which is what the third part of the
test is for.

**One fact 19 added that the design predates:** the two channels keep two per-unit put prices
over ONE crew, and that is now built and tested (`s_put` is keyed by channel and the crew is
not). So a per-unit gain keyed by owning channel is consistent with how the labour is actually
priced — the bundle is not introducing a per-channel asymmetry, it is reflecting one that the
cost model already has.

## What proves it

- **The commensurability test's SABOTAGE**: a planted per-channel weight must make it fail. A
  test that only passes on the correct code is the defect memory `a-count-is-not-a-claim` warns
  about one level up — divide by a denominator before concluding.
- **The disagreeing-gate refusal fires**, mutation-checked; 13 seated the indirection precisely so
  two owners with different gate knobs cannot silently resolve to one.
- **A single-owner run is BYTE-IDENTICAL** — `OneOwnerBundle` answers every key with the same
  instance today, so every seeded `fifo`/`lifo` arm and every single-channel gain arm must diff
  row for row to zero against a `git archive HEAD` copy. Count the gain-scored rows before
  trusting the diff: an unscored table diffs clean (memory `map-exact-solver-rarely-fires` is the
  same shape — a gate that rarely admits anything makes an empty comparison look green).
- Nine verifiers; `Tests/architecture` baselined against a `git archive` copy (five known reds).

## Answer

**BUILT** — `Inbound/gain.SiteGainBundle` is the second owner the indirection was seated
for: one whole `GainBundle` per channel, resolved by the BinKey's OWN regime, with the
site-wide refusal 13 left it as an obligation. `_gain_bundle_for` is **untouched** and is
now called twice, once per leaf, from the same line it always was. The commensurability
claim is **recovered and falsified**, not asserted.

### What is in

1. **`regime_of_key`** in `Warehouse/kernel/regime.py`. The dispatch is `BinKey -> regime`,
   and `regime_of` **cannot read a key and does not refuse one**: a `BinKey` is a plain
   tuple, so every `getattr` falls through and it answers `'store'` for a fulfillment key,
   silently and always in the same direction. 20 hit the identical trap on the bin side and
   worked around it by asking the bin; there is no bin here, so the key form became its own
   function — beside `regime_of`, reading the same three fields, so the two cannot drift.
2. **`SiteGainBundle`**, in `gain.py` beside `OneOwnerBundle` because it is the same
   protocol answering two ways. `bind(regime, bundle)` per leaf, mutating, the shape
   `SiteReceiving.bind` and `PutawayPool.bind` have — and for the same reason: the leaves
   are built one at a time, so the first leaf's transit needs the object the second binds
   into.
3. **The second `_gain_bundle_for` call**, at the driver. `_build_site_gain(args)` sits at
   unit scope beside `_build_put_pool` and is handed down to `_build_leaf`; the injection
   line branches on whether it got one. Uncoupled it is `OneOwnerBundle(...)` character for
   character as before.
4. **The refusal 13 made this ticket owe**, generalised — see below.
5. **The three-part commensurability test with its sabotage**, `Tests/unit/test_gain_plan.py`
   section 10.

### The gate-knob refusal became a FIVE-field refusal, and that is the finding

13 asked for a refusal when two owners' `fee_threshold_days` / `urgency_horizon_days`
disagree. Writing it exposed that those two are not the only site-wide fields on a bundle —
they are the two the GATE reads, and the evaluator reads three more **above or before any
owner**:

- `binkey_of` is read with the cursor still `None`: `place_load` calls it to form the groups
  that the owner lookup is keyed by. A provider whose owners keyed it differently would be
  answering a different question than the evaluator asks, and `_Evaluator`'s own docstring
  (13 wrote it) says so.
- `tier_ranks_for` decides the spill chain, which must be one walk for everyone.
- `put_speed` is the ONE put crew's — `put_crew_spec()`, one site CONFIG. Two owners pacing
  the same putters differently is the double count wearing a bundle.

So `_site_wide_disagreement` checks all five, and `for_key(None)` is answered with the
first-bound owner — which is exact *because* of that refusal, rather than a first-wins that
happens to be right. Two comparison rules, deliberately: the pure lookups by **identity**
(two equivalent functions are still two answers), the floats by **tolerance**. An identity
test on `put_speed` would refuse every lawful coupled run — the driver builds a
`SpeedProfile` per leaf from one payload record — and that mutation is one of the fifteen.

### What the composite does NOT do

- **It never rebuilds, reinterprets or averages an arm.** Each owner is the whole bundle
  `_gain_bundle_for` returned for that leaf, and the e2e asserts that by **identity**. That
  is what keeps faithful-to-arm structural rather than argued (05 decision 1): the
  alternative, owner-keyed fields on one bundle, would have silently dropped
  `GainBundle.__init__`'s cross-field refusals.
- **It adds no per-unit loop.** `place_load` already groups by BinKey; the cursor 13 seated
  is where the dispatch happens, at a granularity the loop already had.
- **It does not touch the receiving price.** The gain score is put travel + E[visits] x pick
  — `UnloadCost` appears nowhere in it — so 27's per-regime price list cannot move the
  exchange rate measured here. The direction, stated for whoever wires 27: a per-regime
  unload price would enter as a per-regime COST, the same kind of thing `wp.by_regime`'s
  different pick intercepts already are, and the test as built would still pass. What the
  test forbids is a per-channel COEFFICIENT on a channel's summed hours. Those are
  different claims and the fixture keeps them apart on purpose: the two arms here run
  genuinely different pick costs AND different adapters, and the rate still recovers at 1.

### The commensurability test, and why the sabotage is the point

Three parts, over a mixed load whose store half runs the **merge** adapter (tmin) and whose
fulfillment half runs the **uniform** one (fifo) — so the composite must swap the CODE PATH
mid-trailer, not merely the data:

1. **the decomposition is exact** — the mixed score is its two halves, to within 1e-9
   relative. BinKeys partition bins by regime, so the owners never contend; without that
   exactness "hours per channel" is not a separable quantity at all.
2. **the rate is RECOVERED** — two deliberately lopsided mixes give a 2x2 system whose
   determinant is asserted non-zero first (collinear mixes would make `a = b = 1` one answer
   among infinitely many), and it returns `a = b = 1`. The reference hours come from each
   arm's OWN `OneOwnerBundle` over its own units: if they came through the composite too,
   any coefficient inside it would cancel and the recovery would report 1.0 whatever the
   code did — `a-count-is-not-a-claim`, one level up.
3. **the sabotage** — `_WeightedSiteEvaluator` weights fulfillment hours 1.3 where the owner
   groups are summed (the only place a per-channel coefficient could live; everything below
   it is an hour at a bin). `_assert_commensurable` is one callable, so part 3 asserts that
   **that very check** fails, and then that the recovery returns **1.3** — the planted rate
   itself, which is what makes it an instrument rather than an alarm. An unweighted control
   with the same re-partitioning passes, so the failure is attributable to the weight and
   not to the plant's shape.

**The finding inside the sabotage:** the weight breaks **part 1** first. A per-channel
coefficient stops the trailer score being the sum of its owners' honest hours, so the
decomposition fails before the rate is solved for. Part 2 then names the rate. Worth knowing
because it means the precondition is not merely a precondition — it is the sharper of the
two detectors.

### What proves it

- **The single-owner path is BYTE-IDENTICAL, measured on DB rows.** Six store arms
  (`opt_/uni_` x `fifo/tmin/tmax` — the uniform adapter and the merge adapter both), 20
  batches, 250 SKUs, 3 doors and a 600 s receiving day, `gain_myopic` on the yard and
  `gain_gated` on the dock, run in this tree and in a `git archive HEAD | tar -x` copy:
  **899,068 rows across 19 tables x 6 arms, ZERO differing**, masking only
  `simulation_runs.created`. **The gain evidence was counted before the diff was trusted**:
  181 `plan_order` calls, **154 of them ranking more than one trailer** (max 16), 120
  `yard_drains` and 132 `yard_trailers` rows — an unscored table diffs clean
  (`map-exact-solver-rarely-fires` is the same shape one level up). And the probe is a real
  oracle, not a tautology: inverting the gain sign in the HEAD copy moves **62,001 rows
  across 72 table instances**.
- **`Tests/unit` + `Tests/integration`: 2,632 passed, 1 skipped** — **9 new test functions**
  in `test_gain_plan.py` section 10 and **1** in `Tests/e2e/test_coupled_unit_e2e.py`.
  `Tests/e2e/test_coupled_unit_e2e.py` **7 passed** (3m27).
- **15 guard mutations, 15 caught**, each with its anchor count asserted first so none could
  be a no-op (20's `tuple(x)` lesson): the key read as an object; an unowned regime
  borrowing the first owner; a provider accepted as an owner; a channel that is not a
  regime; one regime bound twice; the site-wide disagreement accepted; the gate knobs, the
  put paces and the pure lookups each dropped from the site-wide set; `put_speed` compared
  by identity instead of tolerance; an empty composite answering the pre-cursor read; that
  read answered by the LAST owner; the composite built with no gain policy named; a bundle
  per leaf instead of one provider; and the unit never handing the composite down.
- **Nine verifiers OK.** `strategy_runner.py` IS a `contract.SHAPE_SOURCES` file, so
  `preflight --check` went stale and the **canaries were re-run**: `tree shape UNCHANGED`,
  schema `5c9bc35db55b` still valid, fingerprint refreshed (`Optimization/schemas/run_tree/
  INDEX.json` rides the source commit).

### What 24 needs to know

- **The slot is already filled the way 24 wants it.** Both leaves' transits hold the SAME
  provider today, so when 24 hands the two leaves ONE `YardTransit`, its single
  `gain_bundle` slot needs no new wiring — the count of transits changes, nothing else.
- **A leaf that names no gain policy binds no owner**, and the first mixed trailer then
  refuses loudly at `for_key` rather than pricing one channel's units under the other's arm.
  Under one shared yard that is a config error worth catching early: the site dock ranks
  with ONE policy, so 24 should expect both leaves' inbound specs to name the same standing
  policies (they come from one `inbound_spec()`).
- **The composite refuses at bind, i.e. at worker startup**, not at the first priced unit —
  so if 24 ever gives the leaves different put crews or different gate knobs, it fails
  before the doors are filled and the crew charged (21's rule, applied here).
- **27 is orthogonal to this.** See "What the composite does NOT do": no unload price enters
  the gain score, so the dock's price list keyed by the unloaded unit's own regime neither
  needs nor disturbs anything here.
