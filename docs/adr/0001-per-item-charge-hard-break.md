---
status: accepted
date: 2026-09-05
---

# The pick cost model gains a per-item charge, as a hard break

The at-location handling model was `M(y) · (intercept + qty · var)`, and the intercept was
believed to carry the fixed labour of placing each item into the cart and labelling it. It never
did: the intercept is charged once per pick line (one bin visit for one SKU), and no per-item
constant existed anywhere. We added one — `M(y) · (intercept + qty · per_item + qty · var)`,
default 0.5 s, both channels — and made the default NON-ZERO in the dataclass rather than
0.0-with-a-flag, so every archived pick result stops being comparable with new runs. That was the
point: the calibrated era (`.scratch/department-calibration`) already ends comparability by
deriving batch content, and preserving the old model's legitimacy would have cost a dead
`0.0` default that rots plus a second labour regime to reconcile forever.

## Considered options

- **Document only** — fix the docstring to say "per pick line", change no model. Rejected: the
  user wants the per-item labour modelled, and the calibration derives crews from it.
- **Default 0.0, era turns it on** — byte-identical, tests hold. Rejected: two regimes to
  reason about, and a coefficient defaulted to 0.0 is dead code (the `Inbound/unload.py`
  argument).

## Consequences

Put-away carries the charge at a declared ratio of picking's (default 0.2) with a declared
intercept scale (0.5); receiving carries ONE charge per pack, not per item. Both keep their
by-reference coefficient link to picking; the scales are the only legal divergence. The gain
evaluator and the demand-mass yardsticks inherit the term through `per_pick` and must be proven
to. Golden fixtures re-baseline with the change.
