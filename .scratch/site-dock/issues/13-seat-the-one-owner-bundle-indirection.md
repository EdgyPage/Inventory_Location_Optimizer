# Seat the one-owner bundle indirection

Type: task
Status: open

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
