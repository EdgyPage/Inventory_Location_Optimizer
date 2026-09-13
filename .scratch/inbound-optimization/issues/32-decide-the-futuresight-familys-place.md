# Decide the futuresight family's place in the campaign

Type: grilling
Status: resolved

Surfaced 2026-09-12 by [Measure what a gain cell actually costs](31-measure-what-a-gain-cell-costs.md),
which went to price `fsight_wall` and found it cannot run.

Blocks phase 2: two of its ten declared cells currently fail at their first drain, so the
campaign cannot be launched as declared whichever way this goes.

## Question

**Build the window zip, or drop the futuresight family from phase 2?**

`Inbound/site_space.py:98-113` refuses to compose two futuresight windows. Every phase-2 cell
couples, so `fsight_w5` and `fsight_wall` both die at their first drain -- proven on a probe run
(31, asset `31-fsight-coupled-refusal.md`) and, for the w=5 half, by calling `compose_site_view`
directly. The refusal is deliberate, documented and unit-tested; it declines to SHIP the zip
unexercised, which was correct when no arm needed it. An arm needs it now.

### What each side costs

**Build it.** The rule is already written down on the refusal: zip the two window tuples BY
BATCH INDEX (one batch is one site day, and the staffing derivation refuses channels with
different batch counts), then union each pair of `{sku: qty}` dicts, which are disjoint because
no SKU belongs to both leaves. So the change itself is small. What is NOT small, and is the real
question:

- the disjointness is currently an ARGUMENT, not an assertion -- decide whether the zip checks
  it or trusts it, and say why;
- 06's Tier-1 obligations (equivalence + sabotage) apply, as they did to every gain family;
- the unexercised-path objection the refusal was written for does not disappear: `futuresight`
  is the declared-unlawful reference arm, so the zip is exercised by exactly one cell family and
  by whatever tests are written for it;
- and the two cells' RUNTIME is still unmeasured and unbounded on paper (`_window_rates`
  re-aggregates the whole remaining script once per DRAIN). 31 measured the runnable poles at
  1.6-1.9x an unpriced cell and could bracket futuresight only from BELOW. The legal fix if it
  bites is memoizing `_window_rates` on `demand_v` -- 06's "legal-keyed-not-built" case -- which
  is a second build hiding behind the first.

**Drop it.** The family is the declared-unlawful upper-bound reference (objective resolution,
10): w batches of read-ahead, w=inf the oracle, never recommendable. Dropping it costs the
campaign its answer to *how much of the achievable gain the lawful arms capture* -- the H grid
still says which lawful policy wins and by how much against FIFO, which is the campaign's own
question, but nothing then bounds how much was left on the table. Phase 2 becomes 8 cells,
96 units, 24-27 h and ~132 GiB (31, section 6).

### What this ticket must produce

A decision, and if it is "build", the zip graduates as its own task ticket rather than being
done here. Either way, say what the campaign PUBLISHES about the missing or present upper bound
-- a campaign that quietly has no oracle arm reads as though none was ever wanted.

## Answer

RESOLVED 2026-09-13. **BUILD the zip. Phase 2 stays at TEN cells.** But the family is not what
this map has been calling it: futuresight is a **clairvoyance reference**, not an upper bound,
and the campaign's published claim narrows accordingly. The `_window_rates` memo is NOT folded
into the build -- it is probe-gated, for the reason 31 declined the batch-script sharing.

### 1. The build is smaller than the refusal made it sound

Five facts, all read off the code rather than argued:

* **Both leaves' windows are the same length by the time they meet.** `_futuresight_window`
  clamps at `n_batches`, the staffing derivation refuses channels with different batch counts,
  and `compose_site_view` already checks that both leaves froze at one instant. Zipping by
  batch index is not an assumption the zip has to defend; it is a coordinate both leaves
  already share.
* **The union is disjoint and the zip is therefore a strict no-op against per-leaf pricing.**
  A SKU is single-regime (`regime_of` is single-valued per entity) and each leaf's window is
  built from its own `_batches_*.pkl`. So a store load looking itself up in the composed
  aggregate finds exactly its own leaf's entry, and `_window_rates` needs no change at all --
  its `{sku: (total, events)}` shape survives untouched. **This is 06's Tier-1 equivalence
  test, and it is nearly free to write**: composed pricing == per-leaf pricing, exactly.
* **"Assert or trust" was never open -- the composer already has a house pattern.** `predicted`
  and `emptied_at` are both unioned with a raise-on-collision under exactly this kind of
  "disjoint by construction" argument (`Inbound/site_space.py:148`, `:161`). The zip follows
  suit; `len(merged) == len(a) + len(b)` costs nothing beside an iteration the union already
  does. Do not invent a new discipline for this field.
* **It is TWO edits, not one.** The refusal at `:98-113`, and `window=None` hard-coded in the
  composed view's return at `:190`.
* **The unexercised-path objection dissolves on contact.** The refusal was right to decline
  shipping a zip no arm reached (memory `hand-run-test-tiers-rot-silently`). Two cells reach it
  now, on every drain of every coupled unit. That is the objection's own remedy.

### 2. What futuresight actually bounds -- the correction that matters

`CONTEXT.md:220` called it *"an upper-bound reference only"*. **That is a category claim the
model does not support, and it has been load-bearing on this map since the objective
resolution.**

Futuresight changes exactly one thing: inside `_cost_at`, the SKU's REALIZED window demand
stands where the static rate stands (`Inbound/gain.py:560`). Same `plan_order`, same greedy,
same frozen view, same candidates -- a better ESTIMATE feeding an unchanged heuristic. The
docstring is precise where the glossary was not: the visit cap *"is what makes w=inf honestly
the oracle"* -- the PRICING is exact at w=inf, not the OUTCOME.

So what `fsight_wall` bounds is **pricing accuracy**, not achievable gain. The real ceiling
would be choosing the trailer order jointly across all drains, an offline optimisation nothing
in this codebase does. And a greedy fed an exact estimate is not guaranteed to finish better
than a greedy fed a noisy one; there is an expectation, not a theorem.

The consequence is a sentence the campaign may NOT write. With futuresight present it can say
*"perfect demand knowledge buys X% over standing demand"*. It may NOT say *"the lawful arms
captured (100-X)% of the achievable gain"* -- which was the sentence the family's value was
being defended with. The family survives on the narrower claim, because that claim is still the
only thing in the campaign that says whether better forecasting is worth pursuing at all.

`CONTEXT.md` is edited with this resolution: **Futuresight window** is now a clairvoyance
reference, with the pricing-accuracy/achievable-gain distinction stated on the term itself.

### 3. What the campaign publishes -- pre-committed, both directions

`fsight_wall` losing to `gforecast` is a LIVE outcome, not a pathology (section 2: better input,
unchanged greedy). Pre-committed now, because after the run it would be rationalised:

* **The reading, either direction: "perfect demand knowledge buys X% over standing demand."**
  X negative is reported exactly as readably as X positive, on the same axis, with no change of
  framing between the two cases.
* **Explicitly NOT published: "the lawful arm is at the ceiling."** Futuresight is not the
  ceiling, so its loss says nothing about headroom. This is the tempting reading and it is
  barred by section 2.
* **X <= 0 is a FINDING, not a null result**, and the page says what it means: the greedy's
  binding constraint is the ORDERING HEURISTIC, not the demand estimate -- which is where the
  next investment would go.
* **10's honesty flag is carried onto the page, not left in the ticket**: modest separation was
  expected up front (futuresight's edge over static rates is sampling-noise knowledge), so a
  small X must not read as a surprise or a failure.
* Both cells stay OUT of the recommendable set, unconditionally, whatever X is.

### 4. The `_window_rates` memo is probe-gated, not folded in

This reverses the recommendation this session opened with, and the reversal is the budget
answer's doing (no wall-clock or disk ceiling binds).

`_window_rates` aggregates once per DRAIN while the window is replaced once per BATCH, so the
same immutable tuple is re-aggregated drains-per-batch times, on top of `_futuresight_window`'s
n(n-1)/2 copies. On paper that is unbounded. **It has never been measured, and 31 could bracket
futuresight only from below.**

Two reasons not to build it now:

* **The memo is not five lines -- it is five lines plus 06's caching contract**, which
  obligates a behaviour-neutrality proof (cached == recomputed, cross-checked) at a declared
  freeze point. That is a real obligation to pay against an unmeasured cost.
* **31 just declined the identical trade in the other direction**: each cell re-precomputing an
  identical batch script, fix priced at ~6 minutes over the campaign, recorded as *"not worth a
  build, and recording that here is how the next reader stops before optimizing it."* Building
  a cache from an on-paper argument with no ceiling to protect is the same move inverted.

So: the build ticket runs ONE `fsight_w5` coupled unit and reads its wall against 31's
`gforecast` pole (cell mean 1,236 s, on the probe's clock). Inside ~2x, the memo is never built
and that is RECORDED the way 31 recorded the batch script. Past it, the memo graduates as its
own ticket -- with the measurement attached, which is also the only way its Tier-1 cross-check
arrives with a number to be judged against.

### 5. What phase 2 now costs

Ten cells, on 31's probe clock and mixable with nothing from the gate run
(`comparison_20260912_134002` runs a different clock -- ~3.4x in the freeze, ~1.6x per unit):

**120 coupled units, 30.8-35.3 h of unit-seconds, ~164 GiB, ~8.6-9.7 h wall at 4 workers,
plus 0.88 h of freeze and reshape.** The two futuresight cells are a LOWER bound inside that
(futuresight is strictly more work than `gforecast` and has never been timed); the probe in
section 4 is what first puts a number on them, and it lands before the campaign commits.

Against dropping the family: +~3 h wall, +32 GiB. Neither binds.

### 6. What this changes on the map

* Graduates [Build the coupled futuresight window zip](34-build-the-futuresight-window-zip.md)
  -- the zip, its Tier-1 pair, the `CONTEXT.md`-consistent docstrings, and the probe of
  section 4.
* Unblocks [Gate the campaign axis on the coupled model](33-gate-the-campaign-axis-on-the-coupled-model.md),
  and changes what it gates against: the axis KEEPS its ten cells, so the check is written
  against a composer that composes windows, and the futuresight instance stops being the
  example it fires on. Commented there.
* Does NOT touch [Extend the gain bundles](20-extend-the-gain-bundles.md): still gated on
  phase 1's ranking, which does not exist.
