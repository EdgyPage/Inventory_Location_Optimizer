# S12 — bisecting the unloading-order threshold

**Registered in S11, before running:** at c = 0.95 with fulfillment at k = 10, trailers grow
about 0.64 a day per unit of store k.  The dock keeps up below its capacity (≈ 24–26
trailers/day) and saturates above it.  The order is null wherever it keeps up.

| store k | trailers/day, predicted → measured | mean / max yard wait | P4, 95% intervals excluding 0 |
|---|---|---|---|
| 10 | 14.3 (grid) | 6.7 h / 12.0 h | 0 of 8 |
| **20** | 20.7 → **21.1** | 6.7 h / 10.2 h | **0 of 8** ✓ |
| **25** | 23.9 → **24.0** | 7.6 h / 11.6 h, starting to rise | **0 of 8** ✓ |
| 30 | 27.1 (grid) | 15.4–17.1 h / ~30 h | 5 of 8 (fulfillment −2.3% to −3.0%) |
| 30, c = 0.80 | 23.3 (grid) | 6.9 h / 10.2 h | 0 of 8 |

**The contention gate holds.**  The unloading order moves pick labour only when the site dock
saturates, between 24 and 27 trailers a day on this site (store k between 25 and 30).  At
c = 0.80 even k = 30 stays under it: the one-line floor ships less, 23.3 trailers/day.  Below
the gate, every velocity-blind order pair is exchangeable (S09), 0 of 56 readings.  Above it,
the order decides which DAY a pack reaches the shelf, a line waiting on a delayed pack is
carried, and lifo wins by 2–3% on fulfillment.

**The placement threshold needs no bisection.**
- On the store, the rank rule beats fifo beyond twice the floor at EVERY grid point, already
  at k = 1 (−1.07%, 95% [−1.40, −0.73]).  It is a height mechanism, and it pays whether or not
  the aisles churn.
- It grows with churn to k ≈ 10 (−1.9%) and then plateaus (−1.5% at k = 20 and 25, −1.7% at 30).
- On fulfillment, where every bin is at M = 1, placement is null (|gap| ≤ 0.13%) everywhere
  except the saturated k30_c95 point (−0.9% to −1.1%).  There it comes with the order effect,
  from the same congestion.

**Next.**  S13, the synthesis.
