# S11 — the grid, measured against S10's registered predictions

Nine runs of `_churn_probe` from snapshot `5e8b778e` (40k perf catalogue, both stock modes,
winner pair + fifo rider, fifo and lifo unloading cells).  Every run exited 0, in 4–10 minutes
each.  Measured by `assets/s11_measure.py`, tabulated by `assets/s12_summary.py`; per-point JSON
is in `assets/results/S11_*.json`.

## Scorecard

**P1 — fresh-bin share** (measured in units, mean over the reference cell's arms; predicted in
lines).  Band: ±25%.

| k (store / ff) | c = 0.95: store | c = 0.95: ff | c = 0.80: store | c = 0.80: ff |
|---|---|---|---|---|
| 1 / 1 | 8.2% / 7.8% ✓ | 23.5% / 26.6% ✓ | 13.9% / 7.8% **✗** | 32.5% / 26.6% ✓ |
| 3 / 3 | 22.5% / 20.3% ✓ | 46.6% / 54.5% ✓ | 30.1% / 20.3% **✗** | 57.1% / 54.5% ✓ |
| 10 / 10 | 50.0% / 45.9% ✓ | 72.0% / 79.8% ✓ | 60.2% / 45.9% **✗** | 76.1% / 79.8% ✓ |
| 30 / 10 | 65.2% / 69.3% ✓ | 70.7% / 79.8% ✓ | 72.0% / 69.3% ✓ | 75.9% / 79.8% ✓ |
| 1 / 1, H = 80 | 15.8% / 15.5% ✓ | 40.1% / 46.0% ✓ | | |

The H = 80 prediction was computed after the H = 40 grid had finished but before the H = 80
run was read.

- **At c = 0.95 the fresh-bin law holds at every k** (9 of 9).
- The fulfillment residual keeps S08's sign: units run below lines.
- **The registered claim "φ does not depend on c" is REFUTED on the store** at k ≤ 10.
  Diagnosis: at the one-line floor the first-pass fill drops (0.975 → 0.917).  A short line's
  remainder is carried to the next day, and it is served from the lot the short line
  triggered — a fresh bin.  Carried units, as a share of units picked, rise from 14% to 36%
  (store, k = 1) as c falls 0.95 → 0.80.
- Revision: φ_units = φ_lines + (a carry term growing with 1 − fill_c).  So a LOWER confidence
  does widen the inbound decision's reach, but through shortfalls, not through churn.

**P3 — the store placement gap** (uni rank vs uni fifo, pick labour, mean of the two cells).
The prediction is gap_1 × [φ(k)/φ(1)] × [ΔM̄_k/ΔM̄_1], anchored on the grid's own k = 1.

| point | measured | predicted | ratio |
|---|---|---|---|
| k1_c95 | −1.07% | anchor | |
| k1_c80 | −1.24% | anchor | |
| k3_c95 | −1.34% | −1.45% | 0.92 ✓ |
| k3_c80 | −1.90% | −1.68% | 1.13 ✓ |
| k10_c95 | −1.88% | −1.87% | 1.00 ✓ |
| k10_c80 | −2.88% | −2.16% | 1.33 (✗, just) |
| k30_c95 | −1.74% | −2.21% | 0.79 ✓ |
| k30_c80 | −1.25% | −2.56% | **0.49 ✗** |
| k1_c95, H = 80 | −1.47% | between k = 1 and 3 (collapse) | ✓ |

- **The gap grows with churn, sub-linearly, up to k = 10.**  Through k = 10 the law is within
  8% at c = 0.95.
- **Beyond k = 10 it turns over**; the registered monotone growth is refuted at k = 30.
  Diagnosis:
  - φ saturates (69%), so there is no more fresh reach to gain.
  - At c = 0.80 the store leaves the floor (79.5% on floor): lots aggregate several lines into
    fewer, larger packs, so the rule makes fewer placement decisions per picked unit.
  - At c = 0.95 the saturated dock (P4) delays fresh packs, so fewer lines find one.
- The store placement gap is significant at EVERY point, already at k = 1: the rank rule's
  height mechanism always pays on the store.

**P4 — the unloading order** (lifo cell vs fifo cell, 8 arms per point).

| point | 95% intervals excluding 0 |
|---|---|
| every point except k30_c95 | **0 of 8** |
| k30_c95 | **5 of 8**: fulfillment −2.3% to −3.0% (lifo cheaper), store uni_rank −0.9% |

- The mean yard wait at k30_c95 is **15.4–17.1 h** (max ~30 h), against 6.4–7.1 h at every
  other point.  That point has ~1,090 trailers in 40 days (27/day); k30_c80 has ~930 (23/day)
  and k10_c95 ~570 (14/day).
- **Exchangeability holds wherever the dock keeps up, and breaks when it saturates.**  The S09
  argument assumed the order only permutes WHICH BIN a pack takes.  Under a saturated dock it
  also decides WHICH DAY a pack reaches the shelf, and that is not exchangeable: a pack the
  order delays is a line carried.
- So the unloading order's threshold is set by dock CONTENTION, not by aisle churn.  The churn
  lever only matters here because it raises the trailer rate.

**P5 — the initial layout** (opt rank vs uni rank, store).  Band: ratio to the grid's own k = 1
at the same c.  The layout's value falls with churn as (1 − φ) predicts at 5 of 6 points.

| point | ratio |
|---|---|
| k3_c95 | 0.74 |
| k3_c80 | 1.03 |
| k10_c95 | **0.65 ✗** |
| k10_c80 | 0.84 |
| k30_c95 | 1.16 |
| k30_c80 | 1.11 |

**P6 — the free pool.**
- **On the floor it is flat**: the store within ±1% at every point, except k30_c95 (+5%).
- **Off the floor it GROWS**: fulfillment at k = 10 (68% / 36% on floor) +13% to +25%.  The
  declared coverage stock (Q = C·d) is drawn down toward its order-up-to cycle, which frees
  bins.  At k = 3, fulfillment's first pipeline fills (P ≥ 1) shrink it by 3–4% (S03's
  one-time transient).
- **This is the breathing room the user asked for.  It appears where SKUs leave the one-line
  floor, not before.**

## Registered before running — the S12 bisection of the order threshold

Trailers per day at c = 0.95 with fulfillment k = 10 grow by about 0.64 per unit of store k
(14.3/day at k = 10, 27.1 at k = 30).  The dock kept up at 23.3/day (k30_c80: 6.9 h waits) and
not at 27.1.  Prediction:

| store k (c = 0.95, ff 10) | trailers/day | predicted state | predicted P4 |
|---|---|---|---|
| 20 | ~20.7 | unsaturated, waits ~7 h | 0 of 8 significant |
| 25 | ~23.9 | at the edge (capacity ≈ 24–26/day) | 0–2 of 8 |

A significant order effect at k = 20 would refute the contention gate.
