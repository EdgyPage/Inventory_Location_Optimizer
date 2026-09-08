# Give put-away a per-channel expected travel

Type: task
Status: resolved

Graduated 2026-09-08 from [Re-read the check under the fielded floor](25-re-read-the-check.md),
failure 1. AFK build. Skills: `codebase-design` (this changes what the derived record's put block
means), `domain-modeling` if a glossary term moves.

## Question

Make the put-away department's expected utilization a per-channel quantity, the way picking's and
receiving's already are.

**The defect, measured.** `simconfig/staffing.py` `derive` prices each channel's put-away load as
`ch_put_s = t.per_day(t.put_units) * s_put` -- per-channel UNITS times ONE site-wide `s_put`
(41.24 s/unit on the reference pair: an expected travel from the aisle mouth over the class-uniform
destination, plus handling at the class-mean height). The two sections do not share that geometry.
25 measured **98.50 s/unit realized on the store and 29.15 s/unit on fulfillment, a 3.4x spread.**
The consequence is that the `utilization` clause fails on BOTH leaves in OPPOSITE directions
(fulfillment 0.477 realized against 0.691 expected, store 0.362 against 0.155) while the site total
is very nearly exact: 1,425,557 realized s/day against a derived 1,437,958, **-0.9%**.

**What is NOT wrong, and must not move.** The crew SIZE. `put_crew = crew_size(put_load_s, S,
rho_put)` sums the site load and is right to: 25 measured the units per channel within 2% of the
script (6,241/day store, 27,814/day fulfillment) and the total load within 1%. A change that
re-sizes the crew on this pair has broken something rather than fixed it.

**The shape of the fix**, from the two departments that already get this right:

- `s_pick` is derived PER CHANNEL and stamped per channel (108.371 store / 17.362 fulfillment on
  this pair -- a 6.2x spread the derivation already respects).
- Receiving takes its per-channel seconds STRAIGHT FROM THE SCRIPT (`ch_recv_s =
  t.per_day(t.recv_s)`), exact because an unload has no travel term; its `s_recv` is a reported
  average, not a price anything is computed from. Receiving is in band on both leaves.

Put-away is the only one of the three departments that multiplies per-channel units by a site-wide
constant, and it is the only one out of band. The closed form that produces `s_put` already knows
the section's geometry -- it is computed over a warehouse whose aisles the channel determines -- so
the question is whether to derive it per channel from that same expectation
([Derive the expected-travel closed form](13-derive-the-expected-travel-closed-form.md)) or to take
the per-channel seconds from the script the way receiving does.

**The one decision inside this ticket**: what the record keeps stamping at the SITE level.
`put.s_put` is currently a single `constant(...)`; after this change a site value is at best a
weighted average and at worst misleading. Either it becomes a per-channel map beside the
per-channel expectations, or it stays as a reported average carrying a note that nothing is priced
from it (receiving's `s_recv` is the precedent for the second). Do not leave a site-wide `s_put`
that a future consumer could price something from.

## Done when

- Each channel's put load is priced at that channel's own expected seconds per unit, and the record
  stamps it per channel beside `put.expected_utilization`.
- The put crew size is unchanged on the reference pair (a test pins it), because the site load is
  unchanged.
- Any new input rides all five seams (memory `config-knob-has-five-seams`), and the restore/drift
  check (`derived_differs`) covers the new record shape.
- Flag-off is byte-identical; the derivation stays pure and still runs after precompute.
- ONE 40-day era re-read on the reference pair shows put utilization IN BAND on both leaves. On this
  pair that means expectations landing within `band_tol` of a realized 0.477 fulfillment and 0.362
  store -- the realized numbers are not in question, the expected ones are.

## Answer

**LANDED 2026-09-08. The put price is per channel, the crew is provably unchanged, and the
`utilization` clause reads OK on BOTH leaves for the first time under the era.** Verified by a
40-day era run on the reference pair (`comparison_20260908_094846`).

### What the defect actually was

Narrower than the ticket assumed, and the ticket's premise that `expected_travel.py` needed no
change was right for the wrong reason. `ScriptTotals.put_s` was ALREADY the channel's own
expectation -- `put_site_pricer` is built inside the per-channel stage-B loop and `derive` already
stamped `channels[<ch>].script.put_s` into the record. The harness then threw that structure away:
`workunits` collapsed both channels into one ratio `sum(put_s) / sum(put_units)` and `derive`
re-expanded it as `per_day(put_units) * s_put`. So the closed form was per channel, the record was
per channel, and exactly one line in between was not.

Measured on `comparison_20260908_075736` before the change:

| channel | closed-form s/unit | realized s/unit | ratio |
|---|---|---|---|
| store | 98.858 | 98.502 | 1.004 |
| fulfillment | 28.332 | 29.151 | 0.972 |

against a single site-wide `s_put` of 41.241.

### The change

`constants['s_put']` is now a `{channel: constant}` map, exactly like `constants['s_pick']`, built
inside the loop that already computes each channel's script (`workunits`). `derive` charges
`per_day(put_units) x that channel's price`, and `put.s_put` in the record is a channel map with
**no site scalar beside it** -- so there is nowhere left for a future consumer to price something
from a site-wide put number. Four files, ~45 lines of code plus docstrings.

**The one decision, decided against the ticket's framing.** The ticket proposed retiring `s_put`
for `--s-put-store` / `--s-put-ff`, mirroring the pick pair. Rejected: `--s-put` has no caller
anywhere in the repo (no driver, no doc, no committed command line), and the key is flattened into
the PUBLISHED factor register `held_fixed.json`, so retiring it renames a key across the experiment
boundary for zero numerical gain. It also drags in settings / CONFIG / CLI / both restore sites and
~11 test-line edits, mixing a pure rename into a diff that moves every era run's utilization. The
ticket's actual constraint is about the DERIVED record, and the per-channel map discharges it
completely. `--s-put` therefore stays ONE key and stamps the declared value onto every channel,
with each channel's own expectation recorded beside it as `expected`: an operator declaring one
price for the whole site is making an honest declaration, which is a different thing from the
derivation averaging a real 3.4x spread. The symmetry rename remains available as a follow-up with
zero numerical content.

**No `declared` / `derived` branch on the load line, deliberately.** With a per-channel constant,
`units x price` reproduces the script's own seconds when the price IS the expectation, so one code
path serves both. The alternative -- branching to `per_day(put_s)` when derived -- puts the override
branch and the load line in different modules, which is precisely how a declared price silently
stops reaching a number while the record still stamps `provenance: declared` and the audit prints
"overridden". One path makes that failure unrepresentable.

### The crew invariance, stated correctly

The ticket claimed the site load and crew were unchanged. Adversarial verification refuted the
WORDING and confirmed the substance, which is worth recording because a test written to the
ticket's phrasing would have been wrong:

- It is **not an identity of `derive()`**. `derive` receives `s_put` as an opaque constant; the
  ratio is built a module away. It holds iff (a) every channel's `ScriptTotals.batches` is equal
  and (b) the old constant was exactly `sum(put_s)/sum(put_units)` over the same channel set.
- (a) was guaranteed only by one local `n_batches` in the single production caller and was
  **asserted nowhere**. At b = (40, 20) on the reference numbers the crew would have moved 105 ->
  90. `derive` now RAISES on unequal batch counts: under the era one batch is one site day, so
  summing per-day loads across different day counts is summing different days.
- It is **not bitwise exact** -- 1 ulp, since `(sum u/B)(sum s/sum u)` and `sum(s/B)` differ in
  float. Far inside `derived_differs`' `rel_tol=1e-9`, but the pinning test compares with a
  tolerance and asserts the two site LOADS agree, not that the crew equals 59 (pinning the integer
  pins a coincidence of this pair).

Measured end to end on the verification run: **put crew 59, site load 1,437,958 s/day -- both
identical to the pre-change run**, with the price now logged per channel.

### THE CHECK

`comparison_20260908_094846`, same spec (`_canary_single`, fifo, 40 era days, reference pair):

| leaf | put realized | expected BEFORE | expected AFTER | `utilization` |
|---|---|---|---|---|
| fulfillment | 0.477 | 0.691 (-0.214) | **0.475 (-0.002)** | FAIL -> **ok** |
| store | 0.362 | 0.155 (+0.207) | **0.371 (+0.009)** | FAIL -> **ok** |

Every other clause reading is **byte-identical** to the pre-change run (fulfillment: 18/40 drained,
22 capped, max lag 260.6 s, missed share 0.144 / -0.052, 0 repacks, free index floor 1,197,833;
store: 5/40 drained, 35 capped, max lag 2,215.4 s, missed share 0.169 / +0.120, free index floor
1,220,376). The simulation did not move -- only the expectation did, which is what a correctly
unchanged crew means in practice.

Both leaves still FAIL overall, now on `drained` and `missed_share` only. Those are
[Fit the store's window to its own steady state](27-fit-the-store-window-to-its-steady-state.md)'s
question; the department bands are no longer among the reasons.

### Found while verifying, NOT fixed here

- **The put closed form is biased, and the store's agreement is two errors cancelling.** Store
  travel -6.3% against handling +1.8% nets +0.36%; fulfillment is -2.8% almost entirely in travel.
  One root cause: `implied_reorders` prices ONE pack plan at a ROUNDED lot and scales it by a
  FRACTIONAL reorder count, so units-per-pack runs +7.0% / +3.1% high. Also: no cart-swap term at
  all (zero only because `PUT_SWAP_COEF = 0` and `PUT_QUEUE_SPLIT = False`), and
  `class_mean_travel` prices an unbuilt BinKey at ZERO travel silently. -> [Close the put closed
  form's three known gaps](28-close-the-put-closed-forms-gaps.md).
- **The per-channel price is per-channel by SIDE EFFECT.** In the class-uniform branch
  `put_site_pricer` never reads the channel's placement distribution and the geometry it walks is
  the whole SITE; the 98.9-vs-28.3 spread survives only because the two sections' BinKeys are
  disjoint. If one class were ever reachable from both, `class_mean_travel` would average both
  sections' aisles and silently reinstate this exact defect. Now pinned by
  `test_a_binkey_names_its_own_regime_so_the_two_sections_can_never_share_a_class`: `binkey_of`
  returns `(handling, category, size, unit_category)` and `regime_of` decides the section on those
  same two fields, so the invariant is provable rather than incidental.
- **The put band has no per-arm re-centring**, unlike picking's `arm_expectations`. Invisible while
  every era run is `fifo` (whose uniformly-random free bin IS the class-uniform assumption). Added
  to the map's "Ranked arms' steady-state placement" fog patch as its sharper half.
- **`s_recv` has ZERO readers** and its comment claimed the audit's receiving self-check reads it;
  that check re-prices every row instead (memory `equilibrium-check-two-traps`). Comment corrected
  in passing -- it was the precedent this ticket was told to follow, and following it would have
  meant writing a third dead field.
- **A pre-existing failure**, unrelated and confirmed identical with the change stashed:
  `Tests/integration/test_runschema_contract.py::test_the_evaluation_attribution_matches_the_registry_both_ways`
  -- `throughput.audit` writes leaf-scope figures but is not attributed in `figures_throughput_pngs`.
  Raised as its own task.

### What the review passes changed

Both repo reviewers ran report-only over the change, and between them they found one defect in the
production code and two weak tests. All are fixed, and the fixes are the reason this ticket is
worth reading twice:

- **The resume refusal MISDIAGNOSED a store-only run.** `put.s_put` changed shape, so
  `derived_differs` refuses every pre-change run -- but on a store-only pair every derived NUMBER
  is identical (a one-channel site average IS that channel's own ratio), and the generic message
  said the run "cannot continue under different crews", which is false there. The raise now names
  the 2026-09-08 shape move when the diff is on `/put/s_put`, and says the numbers are unchanged
  on a store-only pair. The general drift check was NOT weakened.
- **My BinKey invariant test asserted something FALSE.** I wrote the marker as living in
  `binkey_of(...)[:2]`; `regime_of` actually reads `unit_category` FIRST ("the strongest signal"),
  so a unit can be fulfillment on slot 3 alone -- verified:
  `('conveyable', 'food', 'ff_small', 'fulfillment')` is fulfillment with neither of the first two
  slots marked. The test also asserted `regime_of` on the ORDER while keying the UNIT (they
  disagree for exactly that shape), and it never touched the AISLE branch of `binkey_of` -- which
  is the branch `Geometry.by_class` keys on, i.e. the only path the contamination could actually
  take. Rewritten over the WHOLE key, on the same object, across both branches, with two orders
  off the production builder rather than hand-built namespaces.
- **The site-total test was half tautology.** Comparing `derive` to `derive` with a
  test-computed flat price is scale- and offset-invariant: a mutant that silently DOUBLED the load
  passed it. It now pins absolute values (39,000 s/day both ways, crew 2, each leaf's utilization
  by hand) and pins `put.load_units_per_day`, which production writes and nothing read -- it is the
  denominator a reader now needs to recover the site average the record no longer stores.
- **The harness seam had no behavioural test at all.** `_derive_staffing_for_pair` is covered
  only by source-text greps, so the per-channel derivation, the zero-unit guard and the override
  branch could each fail silently. The ten-line block is now `workunits.put_constant(totals,
  override)` -- module-level and pure, so it is spawn-safe by construction -- with its own test.
- Two smaller ones: an unpriceable section recorded `s_put = 0.0` under a note claiming an
  expected travel (now warns, matching what the pick side already does), and `derive` now names
  the missing map and channel instead of failing on a bare `KeyError` -- for `s_pick` as well as
  `s_put`, since guarding only the new one would have been the asymmetry that caused this ticket.

**Mutation-verified after the rework.** Six mutants of `derive` (re-average both channels; ignore
the constant and charge the script; price both at the store's price; double the load; drop the
batch guard; record a site scalar again) and three of `put_constant` (stamp the override as its own
`expected`; drop the units divisor; divide by zero on an empty section) -- **every one is caught**,
where the pre-review tests missed the doubled load entirely.

### Gates

1,828 unit (4 new behavioural + 1 new invariant), 394 integration + 1 pre-existing failure above,
31 e2e + 1 skipped. Architecture tier: the 3 failures this change caused are fixed by the re-sync
chain (graph 3083/3758, nodes 3079, 4 new symbols catalogued, site rebuilt, both verifiers exit 0);
7 others fail identically at HEAD. All nine CLAUDE.md gates green, run-tree schema id UNCHANGED at
`5c9bc35db55b` -- the FULL preflight re-proved the tree with both canaries (unchanged; only the
source fingerprint refreshed), and canary B is the store-only single-cell shape, which is an
end-to-end check of the path where the per-channel constant collapses to one channel.

**Known break:** `put.s_put` changed shape, and put `expected_utilization` moves by design, so
`derived_differs` refuses to RESUME any run started before this commit. Re-analysis warns and
stamps rather than raising. No in-flight run existed.
