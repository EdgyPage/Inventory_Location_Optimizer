# S04 — the dock's saturation point, and the pick side's aisle ceiling

**Question.**  S12 bisected the unloading-order threshold to 24–27 trailers a day.  What sets
it, in closed form, so it can be predicted rather than bisected?

## Iteration 1 — a daily Lindley queue on the realised unload seconds

Take the dock's capacity as unload-seconds, K = doors × team × shift = 40 × 28,800, and the
backlog as B ← max(0, B + W_d − K).  **It predicts zero backlog at every point**, because no
day's work reaches K.  Kingman's heavy-traffic form gives ≤ 0.6 h.  The measured yard waits
climb 7 → 25 h over the 40 days at k30_c95.  **Refuted**: labour-seconds are the wrong capacity.

## Iteration 2 — doors held for whole trailers

A trailer holds its door for its whole unload.  At k30_c95 the doors were busy **31.9 of 32
door-hours a day**, at 1.15 h per trailer (cv 0.43).  That is 8% above work ÷ team, the loss
to packs one worker carries alone.

$$ \rho_{\mathrm{door}} = \frac{\lambda_T\,\mathbb{E}[o]}{n_{\mathrm{doors}}\,S},
\qquad \mathbb{E}[o] = \frac{W_T}{n_{\mathrm{team}}}(1+\omega) $$

| point | late-window trailers/day | E[o] | ρ_door | measured yard |
|---|---|---|---|---|
| k20_c95 | 23.3 | 1.15 h | 0.84 | stable, 6.7 h |
| k30_c80 | 25.3 | 1.07 h | 0.85 | stable, 6.9 h |
| k25_c95 | 26.3 | 1.13 h | 0.93 | stable, 7.6 h |
| k30_c95 | 28.0 | 1.15 h | **1.01** | **unstable**: 7 → 25 h over the window |

**The gate is ρ_door = 1.**  `models/dock.py` carries it as `DOCK`, and the S12 threshold falls
out of it.  Below the gate, Allen–Cunneen for 4 doors (`yard_wait`) prices the queueing part at
0.7–2.0 h.  The measured waits sit a further ~2.7 h above latency + queue.

Residual, named: the fixed latency itself grows with volume (3.4 h at k1, 4.5 h at k3, 6.7 h at
k10).  Candidates are the once-a-day yard drain and the staged remainder it leaves; open.

## Iteration 3 — why the declaration over-states the dock (the aisle ceiling)

Reading the record's declared load, k25 and k30_c80 both show ρ_door > 1, yet neither
saturated.  The realised inbound was 11–34% below the declaration, because **picking fell behind
demand**, and a reorder follows a pick.

- Store carry late in the window grows 2k → 34k → 65k → 112k units/day from k10 to k30_c95,
  almost all `unpicked_daycut`.
- That happens while the store's labour capacity is 1.4–1.9× demand: its realised cost per unit
  is 3–12% *below* the record.

The simulator gives each aisle's day to one picker.  At k20 and k30 only 31 of 56 and 42 of 84
pickers found work, over ~96 aisle-tasks a day.  The ceiling:

$$ k^* = \frac{S}{\max_a W_a(1)} $$

From the k = 1 run's own aisle loads, the busiest store aisle takes 2,251 s/day, so
**k\* = 12.8**.  The store's day-cut carry, per unit picked, is stable at k ≤ 10 (0.09–0.31) and
grows from k = 20 (2.0, then 2.8, then 3.0).  **The onset lands between the measured k = 10 and
20, as predicted.**  Fulfillment's busiest aisle takes 716 s, so k\* = 40, and its carry stays
small throughout.

Detail left open: no single task reaches the shift.  The cut falls on tasks that start late in a
picker's day, so the ceiling acts through the scheduler's sequencing of aisle-tasks rather than
through one over-long task.  k\* is where it starts, not the literal mechanism.

## What it says

- **The unloading order is gated by the doors.**  With ρ_door = λ_T·E[o]/(doors·S) it can be
  predicted from the realised trailer rate.
- **The realised trailer rate is capped upstream by the aisle ceiling.**  On this catalogue and
  layout, the demand-density lever stops working as a lever past k ≈ 13 on the store: picking
  falls behind whatever the crew, and the backlog it carries is exactly the churn that never
  happens.
- To churn the aisles harder, the simulator needs either more than one picker per aisle-day or
  more, smaller store aisles.  **That is a model change, and the user's decision.**
