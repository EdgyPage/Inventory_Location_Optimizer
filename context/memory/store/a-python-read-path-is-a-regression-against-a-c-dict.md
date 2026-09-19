---
name: a-python-read-path-is-a-regression-against-a-c-dict
description: "Ticket 03 cut 4 (2026-09-19): replacing a whole-aisle dict copy with a lazy view whose items() was a Python generator made the centroid 3.4x DEARER (85 s -> 292 s on the same coupled unit) although the copy it removed cost 71 s. A view over a C dict must read at C speed: hand back the live dict's own items()/keys() when nothing was written, and merge ONCE into a plain dict per new key otherwise. Measure the read side of any copy-on-write change, not only the write side."
metadata:
  type: feedback
---

**What happened.** `_CowListsByKey` (`Inbound/gain_cow.py`) used to materialise an aisle's
whole `{sku_idx: [x_phys, ...]}` map the first time a pool touched it: 768k copies, 71 s on a
six-day coupled unit at campaign scale. The first replacement, `_CowInner`, copied one list on
a write and read through `items()` written as a Python generator (`for k, v in live.items():
yield k, (o[k] if k in o else v)`). `_demand_weighted_partner_centroid` folds every member of
the winning aisle on every unit, so the read side runs ~425k times x hundreds of members: the
generator turned an 85 s fold into 292 s. The change removed 71 s and added 207 s.

**Why:** iterating a dict's own `items()` view is a C loop; a generator that wraps it adds a
Python frame resume plus a dict probe PER ELEMENT. When the hot path is a fold over members,
the read multiplier dwarfs the write saving. A copy-on-write structure has two costs and a
profile of the write side alone mis-ranks them.

**How to apply:** a view over a C-level container must return the container's OWN view
objects (`items()`, `keys()`, `values()`, `iter`) whenever the overlay is empty -- the
overwhelming case, since a winner is read before it is written -- and, once written, merge
once into a plain dict (`{**live, **over}`) cached until the overlay gains a KEY (an append to
an already-copied list is visible through the merge, which holds the same list object). Order
must equal the old copy's order when a float fold reads it: live keys in live order, created
keys appended. Gate: the toy digest ([[toy-run-is-the-byte-identity-instrument]]) for identity,
and a coupled-unit profile of the READ site before and after. Related:
[[pool-open-was-a-tier-rebuild]], [[priced-rank-random-cost-was-the-cow-union]].
