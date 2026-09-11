# Seat the one-owner bundle indirection

Type: task
Status: resolved

AFK. The execution override (map Notes) graduating one byte-identical precursor out of
[Design the composite gain bundle](05-design-the-composite-gain-bundle.md). No decision is open
here; every one is settled in that ticket and this one adds none.

**Byte-identical today** — the wrapper returns the same `GainBundle` instance the evaluator reads
now, so every priced hour, every trailer ordering and every published number is unchanged. It goes
in first and alone for the reason 05 decision 3 gives: the alternative is `_Evaluator` sniffing for
a `for_key` attribute, which is two paths wearing one name, and a second path through the pricing
hot path is exactly the kind of thing that rots unnoticed. Same shape as
[Harden the three positional seams](11-harden-the-positional-seams.md) and
[Seat the put-pool injection seams](12-seat-the-put-pool-seams.md).

## Question

Nothing to decide. Build:

1. **The one-owner wrapper.** A trivial adapter in `Inbound/gain.py` whose `for_key(key)` ignores
   the key and returns the single `GainBundle` it holds. `strategy_runner.py:1100` wraps what
   `_gain_bundle_for` returns before attaching it to `mgr.transit.gain_bundle`; `_gain_bundle_for`
   itself is **not** touched (05 decision 1 — it stays unchanged and is later called twice).

2. **The cursor in `_Evaluator`.** `self._key = own_key` at the top of `_params`
   (`Inbound/gain.py:218`) — before the memo check, unconditionally — and `b` becomes a property
   resolving `self._site.for_key(self._key)`. `_params` is called exactly once per BinKey group by
   `place_load`, before it branches on the adapter, so the cursor is set for every read that
   follows.

   **Do not wrap `place_load`'s group loop from the outside.** 05 decision 2 records why: the loop
   shares one `avail_cache` across a load's groups so spill into an already-touched tier continues
   where consumption left off (`gain.py:355-360`), and wrapping silently breaks that continuity.

3. **The docstring pin.** `_Evaluator`'s docstring records the property the whole design rests on:
   a spill chain never crosses regimes, because `_chain` (`:227`) varies only `size` while regime
   lives in the other three BinKey fields — and every other cache (`_sorted_now`, `_sorted_pred`,
   `_wp`, `_chain_cache`, `_worst`, `_mom`, `taken`) is BinKey- or bin-id-keyed. This is what will
   let one evaluator serve two leaves; without it written down, a later reader has no way to know
   the cursor is safe.

4. **The equivalence proof.** A test that the wrapped path prices identically to the bare one —
   the byte-identical discipline (CLAUDE.md section 2) applies here even though nothing coupled
   exists yet, because that identity is the entire justification for landing this separately.

## Notes

The commensurability test (05 decision 4, all three parts including the sabotage) does **not** ride
here: it needs a two-owner bundle, which does not exist until the composite lands. It stays in the
map's fog with the rest of the coupled half.

Prototype for the shape: [`../assets/prototype_site_gain_bundle.py`](../assets/prototype_site_gain_bundle.py)
(throwaway — `SiteEvaluator` there is the cursor, built against the real `_Evaluator`).

## Answer

**BUILT and live on `develop`** — `4ecdf43d` (the seam) and `9bb25333` (the derived
architecture layer). All four items landed as written; the byte-identity that justified
landing this alone is **measured, not argued**.

### What is in

1. **`OneOwnerBundle`** in `Inbound/gain.py`, exported from the package and wrapped by the
   driver at `strategy_runner.py`'s injection site. `_gain_bundle_for` is untouched, as 05
   decision 1 requires — it stays one leaf's builder and is later called twice.
2. **The cursor.** `_Evaluator.b` is a property resolving `self._site.for_key(self._key)`;
   `_params` sets `self._key = own_key` as its first statement. `place_load`'s group loop,
   its `avail_cache` and its `alloc` are untouched — 05 decision 2's rejected wrapper stays
   rejected.
3. **The docstring pin.** `_Evaluator`'s class docstring now carries the property the design
   rests on (a spill chain never crosses regimes; every other cache is BinKey- or
   bin-id-keyed) **and** the one read that precedes any cursor — `place_load` calls
   `self.b.binkey_of(u)` to key the groups in the first place, at `_key is None`. That is
   lawful only because the three site-wide fields are one object for every owner, which the
   composite must not break.
4. **The equivalence proof**, below.

### One decision the ticket did not contain: the gate's two knobs

`gain_gated` reads `fee_threshold_days` / `urgency_horizon_days` **off `ctx.gain` itself** —
and `ctx.gain` is now the provider, not a bundle. Something had to give. The knobs are site
CONFIG (05: "`fee_threshold_days` / `urgency_horizon_days` are site CONFIG"), and the gate
composes hours and days **above** any one owner's placement machinery, so it has no BinKey
to resolve with and must not silently pick an arbitrary owner's copy.

So the **provider** carries them, forwarding to the one bundle. Rejected: `for_key(None)` at
the gate (picks an owner by accident) and moving the knobs off `GainBundle` (touches
`_gain_bundle_for`, which this ticket may not).

**This leaves the composite an obligation, stated here so 05's build does not have to
rediscover it:** a `SiteGainBundle` owes a loud **refusal** when its two owners' copies of
those knobs disagree. Both come from one `inbound_spec()`, so they cannot differ on a lawful
run — which is exactly why a silent `max`/first-wins would never be noticed.

### The finding: a single-call test cannot see the memo hazard

The ticket says the cursor goes in "before the memo check, unconditionally", and the build
does that. The finding is about **proving** it. A mutation that moves the cursor *inside*
`if got is None:` **survived** the first version of the cursor test — because `_wp` is keyed
by `own_key` and on the FIRST `place_load` every group's key is cold, so the memo misses and
the cursor gets set anyway. The defect only appears on the **second** call over the same
keys, which is what `plan_order` actually does (one evaluator prices every candidate every
round). The test now re-prices on a **warm** evaluator, and the mutant is caught.

Same family as `real-test-coverage-is-317`: the assertion was true, the scenario could not
make it false.

Two smaller ones. `OneOwnerBundle` **refuses a double wrap** (`isinstance(bundle,
GainBundle)`): a provider handed to a provider resolves to itself and would price every
owner with whatever the outer lookup returned — silent, and reachable the moment a second
provider exists. And the one-path rule needed a **refusal, not a sniff**: `_Evaluator`
raises on anything without `for_key`, so a mis-wired driver fails at the seam instead of
several frames into pricing.

### The equivalence proof, measured

A seeded probe drives the REAL `_Evaluator` / `place_load` / `plan_order` and all three
registered entries over **6 scenarios x 5 adapters** (merge-min, merge-max, uniform, pool,
pool+expect_heads) **x both deferral modes** — four BinKeys per scenario so a load's groups
are several and two share a spill chain, every load cost at full float `repr`, every take
identity, every greedy ordering, the gate included. Run against a `git archive HEAD` copy
and against the working tree:

> **4,675 lines, ONE differing** — the flag naming which path ran. A mutant resolution
> (`for_key` returning a bundle with `minimize` flipped) moves **1,506** of them, so the
> probe is a real oracle and not a tautology.

Both **preflight canaries** re-ran through the real pool: `tree shape UNCHANGED`.

Four unit tests seat the properties a probe cannot: `for_key` returns the wrapped
**instance** for every key including `None`; a bare bundle is refused; the cursor visits each
group once in group order, cold **and warm**; and the sabotage — answering one key with a
different arm moves the priced hours, **by exactly that group's own delta** (no cross-talk,
which is the caches' claim made falsifiable).

**Gates:** 2027 unit, 31 e2e (1 skipped), context + contract + preflight + profile-tree +
memory + path + docref, architecture + site. **Five guard mutations, five caught.**

### What this does NOT do

The commensurability test (05 decision 4, all three parts) still waits on a two-owner bundle,
as the ticket's Notes say. Nothing here prices two arms; there is still exactly one.
