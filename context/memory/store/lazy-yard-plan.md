---
name: lazy-yard-plan
description: "Since 626daa92 the yard ranking is a pull queue: asap plugs price one round, not T(T+1) (7.3x); but a 400k DRAIN stages 96% of the ranked yard, so the drain fill saves ~nothing"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-24T22:22:23.853Z
---

**Read this first.**  At the 400k campaign shape (run A, 2026-09-24) a drain stages 294 of
306 ranked trailers, 96%: four doors plus refills empty the yard.  The 25.5% below is the
meso deep rung, whose 80 s receiving whistle stalls the unload artificially.  Under the
campaign's drain fill, then, the lazy plan skips only each plan's last and cheapest rounds.
Its real payoff is the asap fill and any yard that really stands.

The gain sweep cannot be made INCREMENTAL ([[the-gain-sweep-cannot-be-made-incremental]]),
but it can be made LAZY.  Round r's winner depends only on rounds 1..r-1, and a drain stages
only its free doors plus the refills its unload reaches.  Measured on the meso deep rung
(2026-09-24): **25 of 98 ranked trailers were ever pulled (25.5%).**  At T=36 a lazy plan
costs 10.7% of the eager one.  Commit 626daa92 (O1 of `.scratch/inbound-fullscale-perf/`)
made the yard plan lazy:

- `gain.plan_order_iter` is the greedy as a generator, and `plan_order = list(...)` is the
  oracle.
- `priorities.LazyRanking` is the pull queue; the permutation check runs per pull.
- `YardTransit.yard_ranking` is what the door fill, the refills and the asap plug read.
  The dock ranking and a traced drain stay eager.

Cost is p(2T - p + 1) placements for p pulls instead of T(T+1): **linear in T at fixed p.**

**The asap fill was the worst consumer.**  It re-ranked the WHOLE yard at every plug and
staged only the head: 7,100 -> 442 placements, yard plan 8.65 -> 1.19 s on the meso rung.
With the drain fill it went 3.37 -> 1.04 s.  With two doors under gain_gated it went only
2.28 -> 1.51 s, because more doors pull more of the ranking and the urgency gate's forced
prefix is always pulled.  This matters for [[yard-drain-quantises-the-wait]], which
recommends asap.

**How to apply:**
- To force the pre-O1 behaviour for an A/B, patch `YardTransit.yard_ranking` to
  `deque(self.yard_order(ctx))`.  `.scratch/inbound-fullscale-perf/assets/s04_lazy_shadow.py
  --mode eager|shadow` does exactly this.
- Anything counting yard plans must anchor on `gain._traced`, not `plan_order`: the yard's
  rounds now run under `LazyRanking.popleft`.
- `place_loads / entries` no longer inverts to T.  Read the depth off the probe
  (`yard_T_sum / inb_drains`).
