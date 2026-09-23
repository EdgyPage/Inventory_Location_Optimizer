# S13 — synthesis: the closed forms, what they say, and what is left

## The answers

**Where a SKU's equilibrium units come from (S02; `models.levels`, exact for all 800,000
declared SKUs).**
- Stock is the line floor: Q = L = ⌈f·E[q]⌉, E[q] = λ + e^{−λ}.
- f is solved per section so the first-pass fill clears √c: 1.31 store, 1.50 fulfillment at
  c = 0.95.
- The declared coverage C·d never binds.  Every SKU sits on its floor, with cover Q/d of 637
  days (store) and 108 (fulfillment).
- A SKU leaves the floor only when k > cover/C.

**When reorders trigger, and what lands (S03; `models.reorders`, −0.5% to −1.7%).**
- On the floor, rp = Q − 1 and P = 0, so EVERY line fires a lot equal to what it took.
- With a pipeline allowance (P ≥ 1) the fire is a renewal first passage of the running line
  quantity over P + 1 + ε, where ε is the supply noise.
- Units ordered equal units picked, plus a one-time pipeline fill.

**What labour costs per event (S00, S05, S06; the class `closed_form` laws, exact).**

| event | law | verified |
|---|---|---|
| pick | M(y)·(I + q·p + q·v_s) | held equal to `Pick._pick_time` over every registered config |
| put | travel + the same at-location term | held equal to `putaway.put_cost` |
| unload | p + (I + q·v) per pack | held equal to `unload.unload_cost` |

- Section pick labour per unit (the `expected_travel` chain, conditioned on the window's own
  SKUs) is −8% on the store and −3% to −6% on fulfillment.
- The store's residual is named by term: carry, affinity clustering, cart carry, plus a −9%
  handling term left unexplained.
- Receiving is exact.  Put-away is −0.2% to −3%.

**How much picking an inbound decision can reach (S08; `models.churn.FRESH`).**
- φ = Σ[x − (1 − e^{−x})] / ΣλH, with x = λ(H − l).  It is 8% (store) and 24% (fulfillment)
  at declared demand, and 50–70% at k = 10–30.
- It holds at every k at c = 0.95 (S11, 9 of 9).
- Lower confidence widens it through shortfall carries, which the law does not yet include.

**How long a placement rule keeps finding good bins (S09; the frontier + water-filling +
s* = σ_G).**
- The ranked rule spends each height bracket from its cheap end.  Its aisle choice is
  water-filling, and its ground share decays from about 64% to the occupied share.
- The law predicts that decay within 4 points on the 400k runs and within about 6 on the 40k.
- It cannot beat its own occupancy without a turnover differential.

**The churn thresholds (S11, S12).**

| decision | where it first moves pick labour | mechanism |
|---|---|---|
| placement rule, store | everywhere (k ≥ 1); grows to k ≈ 10 (−1.9%), then plateaus | height on fresh picks: φ × ΔM̄, where ΔM̄ decays as the good bins are spent |
| placement rule, fulfillment | once most stock is rule-placed (the 100-batch fill: −1.6% to −2.2%), or under dock saturation | co-location of co-drawn SKUs.  The independent-visit form INVERTS it; the Palm probability P⁰_a(s) prices it (S07) |
| unloading order (any velocity-blind pair) | only past the dock gate ρ_door = λ_T·E[o]/(doors·S) = 1: **k\*_order = 29.6** (store k, fulfillment at 10, c = 0.95) | exchangeable pairings below it (0 of 56 readings); above it the order decides the DAY a pack lands (S04, S12) |
| demand density itself | stops being a lever at **k\* = 12.8** on the store (40 on fulfillment) | one picker per aisle-day: past it the day-cut backlog grows whatever the crew (S04) |
| breathing room (free pool grows) | only off the floor: fulfillment k ≥ 10 (+13% to +25%) | declared coverage stock drawn down to the order-up-to cycle; sign and scale predicted, fragmentation open (S14) |

**k\*_place = 1** on the store: the placement gap clears twice its floor at the declared demand
(|−1.07%| > 2 × 0.40%, from `churn.threshold`).

**The answer to the user's question.**  Churn alone does not make the unloading decision
matter; saturation does.  More churn buys two things:
- **reach** (φ rises about 9× from k = 1 to 30);
- **breathing room**, but only once SKUs leave the one-line floor.

On this catalogue that needs demand about 10× the declared density for fulfillment and more
than 60× for the store.  The ranked placement rule, by contrast, already pays on the store at
any churn, through height.

## What was built

| module | purpose |
|---|---|
| `Warehouse/kernel/closed_form.py` | the DSL: evaluate, render LaTeX, compose, mirror-gate |
| `PickConfig` / `PutawayCost` / `UnloadCost.closed_form` | per-event laws as class attributes |
| `Optimization/simconfig/models/{levels,reorders,churn,dock,inbound,pick}.py` | the composed models: levels, reorders, the fresh-bin reach and frontier and noise floor, the dock gate and aisle ceiling, trailers and crews, the pick day and the location value |
| `Optimization/Performance_Evaluations/closed_form/` | render (graph, sweep, predicted-vs-realised); `docs_page` (generated `docs/closed-form-models.md`, freshness-tested); `evaluation` (`closed_form.predicted`, a run-scope dossier artifact) |

## Open, and proposed

| item | what it would add |
|---|---|
| ~~S04 (yard)~~ | **done**: `models/dock.py` + `models/inbound.py` (trailers within 2% of the yard; the gate at ρ_door = 1) |
| ~~S07 (g_b)~~ | **done**: `models/pick.py` (Palm P⁰_a(s); the script form sees co-location the independent form inverts) |
| carry term | φ_units = φ_lines + f(1 − fill_c): the shortfall's remainder served from the lot it triggered; dominates on short windows (the 6-batch toy: 1.8% realised against ~0 from the fresh-bin law) |
| off-floor drawdown | S14 is partial: compose the fragmentation chain, and replace the declared lead with the dock-coupled one |
| yard latency | the fixed yard wait grows with volume (3.4 → 6.7 h from k1 to k10) on top of Allen–Cunneen; the drain cadence is the candidate |
| **Proposed, not made:** multi-picker aisles (or more, smaller store aisles) | lifts the k\* = 12.8 aisle ceiling, without which store churn past ~13× measures the ceiling |
| **Proposed, not made:** the drain-order fix in `expected_travel.PlacementDist.initial` | correct the drain to smallest-first (S06 variant b).  A comparability break for `pick_owed_exact_s`, so it needs the user's decision. |
| **Proposed, not run:** one 400k confirmation | at the contention edge (store k ≈ 30, c = 0.95) with the gain policies |
| **Suggested design** | a turnover-aware placement rule (fast packs to good bins), the one lever the frontier law says can raise s* above σ_G |
