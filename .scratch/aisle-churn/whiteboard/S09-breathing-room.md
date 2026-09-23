# S09 — breathing room: how long a placement rule keeps finding good bins, and what order can move

**Question.**  The user's direction: the aisles must churn enough to give the bins breathing
room, so that the inbound can make a decision.  In closed form:
1. What does a ranked placement rule actually get to choose from, and how fast does that run out?
2. How much of the rank-vs-fifo pick gap does that explain?
3. How much could ANY unloading order move, and why was the fill trial null?

Measured on the 400k campaign `comparison_whatif_20260920_150203`, cell `k1_off_fifo`, store,
batches 0–39.  The arms are uni/opt initial placement × fifo/rank_cartlabor restock.  Note that
`opt_fifo` and `uni_fifo` are byte-identical, because fifo restock ignores the initial placement.

## 1. The free pool is at a flow equilibrium

Between keyframes 0 and 25, in the classes the store places into (1,202,250 bins):

| arm | free bins @0 → @25 | placements/day | bins freed/day | ground share of frees |
|---|---|---|---|---|
| uni_fifo | 181,816 → 181,031 | 1,769 | 1,737 | 20.1% (= the free pool's 20.2%) |
| uni_rank_cartlabor | 181,816 → 181,489 | 1,750 | 1,737 | 22.6% |
| opt_rank_cartlabor | 181,816 → 181,214 | 1,762 | 1,737 | 25.8% |

**Frees = placements.**  Every line refills what it took (S03) and every pick-out frees a bin, so
the free pool's SIZE never moves.  What moves is its QUALITY:

- under uni_rank, free ground bins fall from 36,766 to 25,933 (a net 433 per day);
- the rule places 825 per day to the ground, and only 392 per day come back.

## 2. The frontier law (the rule's choice, in closed form)

`_TravelBalancedPool.take` puts a unit in the head of the bracket b that minimises
M_b·h_s + D_b, where h_s is the SKU's `labor_cost` and D_b is the travel cost of the bracket's
nearest free bin.  Each bracket is therefore a queue consumed from its cheap end.

Freed bins return at costs distributed like the bracket's occupied bins.  A free that lands in
front of the front is taken at once; one that lands behind waits for it.  The front solves

$$ N_b(D_b) + R_b(t)\,Q_b(D_b) = C_b(t), \qquad
   s_b(t) = \Pr_h\!\left[\arg\min_{b'}\left(M_{b'}h + D_{b'}(t)\right) = b\right], \qquad
   \dot C_b = m_a\,s_b $$

where:
- N_b = free bins at or below cost D at the start;
- R_b = frees to date, at rate r_b = r[(1−ψ)σ_b + ψ s_b];
- Q_b = the share of occupied bins at cost ≤ D;
- σ_b = the bracket's share of occupied bins;
- ψ = the S08 re-picked share (0.092).

**The per-aisle rate m_a is water-filling.**  The aisle is chosen by LPT on load_a + fq·cost,
where load_a is the ledger's Σ of the aisle's SKUs' expected labour.  That is water-filling:

$$ \sum_a (\lambda - L_a)^+ = W(t) $$

and each aisle receives placements in proportion to its rise.  The water-filling prediction of
the concentration (batches 1–9, `assets/s09_waterfill.py`):

| class | aisles | measured: receiving / effective | water-fill: on / effective |
|---|---|---|---|
| conveyable/food/small | 50 | 48 / 40.3 | 48 / 41.5 |
| conveyable/food/medium | 94 | 82 / 68.9 | 90 / 72.7 |
| conveyable/clothing/medium | 195 | 105 / 71.2 | 105 / 67.0 |
| non-conveyable/chemical/medium | 83 | 53 / 38.1 | 54 / 39.7 |
| conveyable/electronic/small | 36 | 17 / 10.6 | 16 / 10.4 |

**Iterations** (`assets/s09_front.py`): ground share of fresh placements per 10-day block, with
the mean height multiplier.  Model rows marked † are the full prediction, with nothing measured
fed back.

| arm, block | measured | ① pooled, D only | ② pooled, M·h + D | ③ per aisle, measured m_a | ④ per aisle, water-filled m_a † |
|---|---|---|---|---|---|
| uni_rank 0–9 | 64.1% · 1.082 | 45.0% · 1.146 | 80.4% · 1.040 | 66.3% · 1.078 | **67.9% · 1.074** |
| uni_rank 10–19 | 43.5% · 1.147 | 24.4% · 1.236 | 47.7% · 1.129 | 45.0% · 1.141 | **46.4% · 1.136** |
| uni_rank 20–29 | 34.5% · 1.184 | 18.9% · 1.265 | 31.2% · 1.190 | 33.5% · 1.187 | **33.3% · 1.187** |
| uni_rank 30–39 | 31.6% · 1.200 | 13.1% · 1.306 | 30.6% · 1.199 | 31.8% · 1.199 | **31.6% · 1.198** |
| opt_rank 0–9 | 28.2% · 1.216 | | 33.9% · 1.192 | 25.9% · 1.234 | **26.0% · 1.233** |
| opt_rank 10–19 | 24.7% · 1.237 | | 23.8% · 1.239 | 22.9% · 1.246 | **22.6% · 1.246** |
| opt_rank 20–29 | 25.1% · 1.235 | | 23.5% · 1.243 | 24.1% · 1.242 | **23.6% · 1.244** |
| opt_rank 30–39 | 23.7% · 1.242 | | 25.6% · 1.230 | 26.0% · 1.231 | **26.0% · 1.229** |

The residuals and their diagnoses, in order:
- **①** Wrong key.  The rule prices height (M·h), not travel alone.
- **②** Pooling every aisle of a class hides the LPT concentration.  A heavy class's placements
  crowd into the ~half of its aisles with the lowest load, and burn their ground bins ~2× faster.
  Per-class check: non-conveyable/chemical/medium with h ≈ 295 s was predicted at 99.9% ground
  and measured at 71.9%.
- **③** The concentration taken as measured closes the law, to within 2.5 points.
- **④** The concentration predicted by water-filling keeps it, to within 4 points and 0.017 of M.
  Accepted.

### The steady state: the rule cannot beat its own occupancy

Once the good free pool is spent, the ground bins handed out are exactly the ground bins freed.
With r = m, the balance m·s* = r[(1−ψ)σ_G + ψ s*] gives

$$ s^* = \sigma_G $$

The ranked rule's long-run ground share EQUALS the ground share of the occupied stock, for any
ψ.  The opt start is already there: σ_G = 24.0% predicted, 23.7–25.1% measured.  The uni start
approaches it from above: 31.6% at block 4, with free ground still being spent.

The only way past σ_G is a turnover differential: good bins that free faster than bad ones.
The rule's bracket choice (argmin M·h + D) is **velocity-blind**: it puts HEAVY SKUs low, not
FAST ones.  So its good bins turn over only incidentally.  Measured: ground frees 22.6% against
the occupied ground share of ~21%, a turnover ratio of ~1.06.

**This is the breathing-room lever the user named, in closed form.**  Breathing room is not
created by more churn in general.  More placements per day spend the good free pool FASTER:

$$ B = G_{\text{free}} / \left(m\,(s - \sigma_G)\right) $$

It is created by the good bins' own turnover: s* = (G·ν_G) / Σ_b n_b ν_b, where ν_b is the
bracket's bin turnover.

## 3. The pick gap, by where it is earned

`assets/s09_gap.py` prices every pick row with `PickConfig.closed_form` ('at_location').
uni_rank vs uni_fifo, batches 0–39:

| component | gap, share of pick time T |
|---|---|
| total (`task_makespan`) | **−0.80%** |
| picks from bins a reorder filled in the window (M 1.162 vs 1.263 per unit) | −0.60% |
| picks from the setup stock | −0.04% |
| travel, tasks and swaps | −0.16% |

The closed-form height law on fresh picks, U_fresh · h̄ · ΔM̄ / T, gives
22,299 × 59.0 s × (−0.101) / 26.8 M s = **−0.50%**.  The remaining −0.10% of the fresh part is
the covariance: the rule puts heavy h low, so E[h·ΔM] > h̄·E[ΔM].  The frontier law carries h per
unit, so it prices that term too.

The **opt gap (−3.63%) is not a restock gap.**  opt_fifo is byte-identical to uni_fifo, so
opt_rank vs opt_fifo carries the whole initial layout.  The restock rule's own share is the uni
gap, −0.80%.

Chain for the store placement gap, every term now a closed form:

$$ \Delta T/T \;\approx\; \varphi(\lambda, H, \ell)\cdot\frac{U\,\bar h}{T}\cdot
   \left(\bar M_{\text{rank}}(t) - \bar M_{\text{free}}\right) $$

with the following sources:

| term | source | verified |
|---|---|---|
| φ | S08 fresh-bin law | 7.7% predicted vs 8.6% measured |
| M̄_rank(t) | the frontier law above | to within 0.017 |
| M̄_free = 1.26 | the free pool's mean multiplier | exact, what fifo gets |

## 4. The unloading-order bound, and the fill-trial null retrodicted

An unloading order only permutes WHICH of the day's packs takes WHICH of the bins the rule hands
out that day.  The fronts move by the day's count whatever the order.  Take pick weight w_i (h_i
× units later picked from pack i in the window) and bin multiplier M_j, per (day, class).  By the
rearrangement inequality every order lies in [C_min, C_max].  Two velocity-blind orders (fifo,
lifo) are **exchangeable** pairings, so their expected gap is exactly zero, with spread

$$ \mathrm{Var} = \tfrac{1}{n-1}\textstyle\sum (w-\bar w)^2 \sum (M-\bar M)^2 $$

`assets/s09_order.py`, realised w (a hindsight upper bound), store, batches 0–39:

| arm | fresh at-location cost | oracle order could win | adversarial could lose | fifo vs lifo: E[gap] ± sd |
|---|---|---|---|---|
| uni_rank_cartlabor | 5.64% of T | −0.65% | +0.91% | **0 ± 0.012%** |
| uni_fifo | 6.19% of T | −1.20% | +0.66% | **0 ± 0.015%** |

**The fill-trial null is retrodicted.**  lifo vs fifo has E = 0 and an sd twenty times smaller
than the 0.24% noise floor.  No velocity-blind order can be told apart from another at k = 1,
and nothing about the run's length changes that.

Even an order that knew the future is capped at 0.65% on top of the ranked rule.  That cap is
the hindsight value of the ~9% of fresh packs that get re-picked at all.  A forecasting order
captures only the predictable part of w, which is less.

## What it says, for the grid (S10)

| lever | what it does to breathing room | what it does to the decision's value |
|---|---|---|
| demand density k ↑ | m ∝ k: the good free pool is spent k× faster, so B ∝ 1/k | φ grows ~linearly in k·H (S08): more picks come from fresh bins; ψ ↑ |
| stock depth: c ↓ (floor ↓) | cover falls, frees per day rise (r ≈ Occ / residence) | less bulk stock sits between a fresh pack and its picks |

- **Unloading order.**  It stays at E = 0 for any velocity-blind pair at every grid point.  Its
  ceiling grows with φ·Var(w)·Var(M).  The grid measures it against the oracle bound, not
  against zero.
- **A turnover-aware rule** (fast packs to good bins) is the one design that raises s* above σ_G.
  It is the natural successor to test.

## Residuals accepted

| term | size | named cause |
|---|---|---|
| ground share, full prediction | ±4 points by block | LPT loads also FALL as SKUs leave an aisle; the water-fill adds only |
| fresh gap: −0.50% law vs −0.60% measured | −0.10% of T | the h·M covariance, priced by the per-unit frontier |
| travel/tasks/swaps | −0.16% of T | aisle co-location (S07, open) |

**Next.**  `models/churn.py`:
- φ (conditional and unconditional);
- the frontier law with water-filling;
- s* = σ_G;
- the height-gap chain;
- the rearrangement bound.

Then S10: register the grid predictions before running it.
