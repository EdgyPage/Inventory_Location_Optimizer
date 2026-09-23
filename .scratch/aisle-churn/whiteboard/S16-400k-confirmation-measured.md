# S16 — the 400k confirmation, measured; and S16b, the corrected gate, registered

## S16: k = 1.35, 400k, c = 0.95 (`comparison_whatif_20260923_145701`, 55 min)

| # | predicted | measured | verdict |
|---|---|---|---|
| Q1 trailers/day, days 20–39 | 22.2 | **21.3** | ✓ |
| Q2 ρ_door, unstable yard | 1.08, unstable | **0.80**; waits 6.6 h (days 5–14) → 6.5 h (days 30–39), stable | **✗** |
| Q3 lifo vs fifo | ≥ 2 of 4 fulfillment arms significant | **0 of 8** | ✗, but consistent with the gate at the realised ρ |
| Q4 fresh-bin share | store 10.2%, fulfillment 33.2% | **10.3%, 29.5%** | ✓ (fulfillment −11%, the usual units-vs-lines sign) |
| Q5 store placement gap | −0.89% | **−0.77%** (ratio 0.87) | ✓ |
| Q6 aisle ceiling | carry stable | **0** day-cut carry | ✓ |
| Q7 store free pool | flat ±2% | **−0.3%** | ✓ |

**The 40k laws transfer to 400k**: reach, placement gap, trailers, ceiling, flow equilibrium.
The gate prediction failed, and the failure is informative.

**Diagnosis (Q2).**  Occupancy per trailer FELL from 1.55 h (k = 1) to 1.20 h (k = 1.35).  At
400k the derived RECEIVING CREW is smaller than the door slots: 23 people at k = 1 and 30 at
k = 1.35, against 4 doors × 10.  So every door works short-handed, and the crew, sized by the
derivation to its utilisation target (~0.85), grows with the demand.  The realised utilisation
sits at the target whatever the demand: **0.854 and 0.846** measured, realised load ÷ (crew × S).
The S04 law measured occupancy at k = 1 and held it fixed; that holds only once the crew fills
the doors (the 40k grid points k ≥ 25, crews 47–55).  Revision, `models/dock.SITE`:

$$ \rho_{\mathrm{site}} = \frac{W}{\min(K_{\mathrm{recv}},\ n_{\mathrm{doors}} n_{\mathrm{team}})\,S},
\qquad k_{\mathrm{gate}} = \frac{n_{\mathrm{doors}}\,n_{\mathrm{team}}\,S}{r\,W_1} $$

The yard can only go unstable once the load outgrows the door slots.  At 400k
(W₁ = 557,625 s/day declared, r = 1.02 realised/declared), **k_gate = 2.03**, not 1.25.
Retrodicted: k = 1.35 gives ρ_site = 0.846, measured 0.846.

## S16b registered: k = 2.2, 400k, c = 0.95

| # | quantity | predicted | band |
|---|---|---|---|
| R1 | derived receiving crew | ⌈557,625 × 2.2 / (28,800 × 0.85)⌉ = **51**: past the 40 door slots | exact |
| R2 | ρ_site (whole window) | 557,625 × 2.2 × 1.02 / (40 × 28,800) = **1.086** | ±5% |
| R3 | yard | **unstable**: mean wait for arrivals in days 30–39 exceeds days 5–14 by ≥ 4 h | — |
| R4 | lifo vs fifo | **≥ 2 of 4 fulfillment arms significant**; sign unregistered (lifo won at 40k) | count |
| R5 | trailers/day, days 20–39 | (21.3 − ½) × 2.2 / 1.35 + ½ = **34.4** | ±10% |
| R6 | fresh-bin share | store **15.7%**, fulfillment **45.9%** | ±25% relative |
| R7 | store placement gap | −0.80% × (15.71 / 7.76) × (ΔM̄₂.₂ / ΔM̄₁) = **−1.01%** | ratio 0.7–1.3 |
| R8 | aisle ceiling | k = 2.2 ≪ 10.1: no growing day-cut carry | — |
