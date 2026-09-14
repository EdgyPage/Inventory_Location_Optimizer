---
name: inbound-yard-is-a-stable-queue-under-the-era
description: "the receiving yard's growth quantity is rho, not catalogue size or batch count; rho=0.819 is stable but confirmed 2026-09-14 at a T=12.97 mean depth where the evaluator's drain cost is cubic in T"
metadata: 
  node_type: memory
  type: project
  originSessionId: 07c0329c-5d0a-4525-8fb1-528b0979b765
  modified: 2026-09-14T14:21:53.808Z
---

`RECV_DAY_SECONDS = None` (`Optimization/simdriver/strategy_runner.py:1676-1682`) is the SECOND
of two branches for `_recv_day`, and the campaign never reaches it: under `shift_drain_or_cap`
(set by `ERA_RUN_DEFAULTS`, inherited by `PHASE2_RUN_DEFAULTS`), `_recv_day` comes from the
site-wide shift and the constant is never consulted. Reading the constant in isolation produced
the claim "production never stands a yard" — published, then retracted (both versions kept, dated,
in `.scratch/inbound-performance/map.md` ticket 06). **The general rule: trace the value the
runner computes, not the constant the settings file declares.**

**Measured under the era, the campaign's yard is a stable queue.** `recv_deadline = 28800.0` at
every drain (flag-off: `None` at every drain — the no-whistle signature); the whistle BINDS
(drain load ÷ (crew × 28,800 s) = 1.000-1.008 on 6 of 40 drains, against a flag-off control
reaching 13.04, unbounded); yard depth after the drain is 0 on all 200 instrumented drains.
`RHO_RECV = 0.85` is the target, but `crew_size` takes a `ceil`
(`Optimization/simconfig/staffing.py:652`), so small catalogues float the realized crew far under
target — measured ρ_recv 0.044/0.095/0.211/0.427/0.445/0.619 at N=1k..40k SKUs. **The campaign's
projected ρ_recv is 0.819** — high, still under 1, i.e. a stable queue with finite mean depth, not
the unstable one an earlier ladder found (see below).

**Fitting T (the yard's growth quantity) against catalogue size, or against batch count outside a
calibrated whistle, is not a fit.** T is a queueing quantity in ρ, and ρ(N) is a **non-monotone
sawtooth** from that same `ceil` — projected 0.446/0.647/0.525/0.619/0.694/0.837/0.819 at
N=5k..400k SKUs. Separately, below ~100k SKUs the warehouse is sized to the per-bucket AISLE
FLOOR (`Warehouse/inventory/inventory_planning.py:86`), not the catalogue, so a ladder topping out
at 40k SKUs fits the floor, not the catalogue knob. A queue also has two regimes, both measured:
at a 10-20 SECOND whistle (starved, ρ≫1) batch count 10→40 drove yard depth 3→391 with **no
saturation** — a queue compounds at ρ≥1 and settles at ρ<1, and that starved measurement says
nothing about the campaign's ρ=0.819 regime. This AMENDS [[growth-ladder-use-the-skus-knob]],
which is right about the PUT queue (drains every batch, saturates cleanly on `skus`) and does not
cover the receiving yard.

**Why:** two silent traps stack here — a two-branch constant read in isolation, and a queueing
quantity mistaken for a size-fittable one. Both produce a confident, wrong, and unfalsified-looking
number.

**How to apply:** before citing any inbound yard number, check which `_recv_day` branch a run
actually took (`shift_drain_or_cap` on = site shift, not `RECV_DAY_SECONDS`), and check ρ_recv,
not N or batch count, before extrapolating. Full record: `docs/design/INBOUND_PERF_FINDINGS.md`
§4, `.scratch/inbound-performance/map.md` tickets 05/06. Related:
[[inbound-pool-adapter-multiplier-is-not-13x]], [[site-dock-is-shared-across-channels]].

**CONFIRMED 2026-09-14, and sharpened.** A campaign-scale ladder (`calltree_inbound_ladder.py
--coupled`, one 400,000-SKU catalogue) measured ρ_recv = 0.836/0.837 at 200k/400k SKUs —
confirms the projected 0.819 as stable. But stable is not cheap: at that ρ the yard depth T
reaches 12.97 (max observed 23), and the pool-adapter evaluator's drain cost is CUBIC in T (see
[[inbound-pool-adapter-multiplier-is-not-13x]]). **A stable queue with a deep mean is exactly
where an O(T^2)-class greedy becomes the whole run** — "stable" describes the queue, not the cost
of serving it.
