# CLOSED: the per_pick memo is worth under 1%, and the suggested form is the worse one

Type: research
Status: resolved

`docs/design/INBOUND_PERF_FINDINGS.md` names this as the cheap half of the `R x A` candidate:

> Within it, `_aisle_best` calls `per_pick(m, intercept, var, 1, per_item)` once per (aisle,
> bracket) for a value that depends only on `m` and `var` -- ~223 calls per boundary for ~1.4
> distinct values.

The HEAD ladder convicted it independently: **`cost_model:per_pick`, k = 1.31, 542,781 calls** at
the 2,400-SKU `split_staging4` rung -- an offender that appears in no archived artifact.

## Measured before building, and it does not survive the measurement

Three forms, 400,000 iterations each, priced against the count the ladder actually recorded:

| form | per 542,781 calls | saving | share of the 3.78 s rung |
|---|---|---|---|
| `per_pick(m, i, var, 1, pi) + d` -- today | 42.8 ms | -- | -- |
| `cache[m] + d` -- **the memo the doc suggests** | 15.5 ms | 27.3 ms | **0.72%** |
| `m * base + d` -- hoist the invariant instead | 10.4 ms | **32.4 ms** | **0.86%** |

**Under one percent either way, and the suggested form is 1.5x worse than the alternative.** A
dict keyed on a float pays hashing and equality where the hoist pays one multiply; what is
actually being removed in both cases is the Python FUNCTION CALL, and the hoist removes it
without adding anything back.

The method is the one this document's own corrections insist on: two factors measured
independently -- a counted 542,781 (exact, traced, deterministic) times a per-call cost measured
on the real function -- rather than a share derived by dividing the total it claims to explain.

## Why it is closed rather than landed

0.86% does not justify the change it would need. The hoist means NOT calling `per_pick`, and that
primitive exists precisely to stop callers computing the expression themselves:

> Previously inlined at 7 sites with "mirrors _pick_time" comments; one helper makes the
> invariant structural. -- `cost_model.per_pick`

Re-inlining it at the hottest site would undo a deliberate consolidation for a sub-1% gain. Doing
it *properly* -- a `per_pick_base` / `per_pick_from_base` pair in the kernel so the single source
of truth survives -- is a kernel signature change, which is a large blast radius for the same
sub-1%.

The bit-identity is not the obstacle and was checked: with `qty=1`, `1*per_item` is exact and
`intercept + per_item + var` associates left-to-right in both forms, so `m * base` is IEEE
byte-equal to `per_pick(m, i, var, 1, pi)` (verified with `struct.pack('<d', ...)`).

## The scale at which it becomes real

It would need roughly **ten times the share** to be worth the kernel change. `per_pick`'s count
grows at k = 1.31 while the rung wall grows at a similar rate, so its share is roughly
scale-stable -- growing the catalogue does not get there. What WOULD get there is the structural
half of the same candidate landing first: if the run-boundary rebuild stops calling `_aisle_best`
once per aisle, `per_pick`'s count falls with it and this question disappears rather than
improving.

**So the ordering is settled: the `R x A` rebuild is the candidate, and this was never the half
worth attacking.**
