# S03 -- how much of the yard ranking does a drain consume?  S04 -- O1: the lazy yard plan

## S03 -- the pull fraction

### Question

`plan_order` ranks EVERY standing trailer: T rounds of a greedy that costs 2 x remaining
placements each, T(T+1) in all, and it cannot be made incremental (memory
`the-gain-sweep-cannot-be-made-incremental`).  But a drain stages only its free doors plus
the refills its unload reaches.  Round r's winner depends only on rounds 1..r-1, so the
rounds nobody pulls could simply not be priced.  How many are there?

### Prediction (registered in the plan)

`yard_pulls / yard_T` in 0.3-0.5, so O1 saves 40-60% of `inb_yplan_s`.  Drop O1 if it is
above 0.8.

### Measurement (`assets/s03_pull_fraction.py`, meso deep rung: 2,400 SKUs, 1 door, whistle 80 s)

**25 of 98 ranked trailers pulled: 25.5%.**  Per drain, a lazy plan pays p(2T - p + 1)
placements against T(T + 1).  At the rung's deepest drain (T = 36) that is 10.7% of the
eager cost; over the run, 5.3x fewer placements.

**Prediction: below the band** (0.255 < 0.3), which is the good direction.  O1 kept.

## S04 -- O1: the lazy yard plan

### What was built

* `Inbound/gain.py`: `plan_order_iter` is the greedy as a GENERATOR.  It yields each winner
  after its round has committed (`taken` updated, templates dropped, `remaining` cut).
  `_plan_order_replay` yields the same way.  `plan_order` is `list(plan_order_iter(...))`
  and stays the oracle.  `_traced` returns the generator when asked `lazy` and no trace
  sink is armed.  Each of the four gain entries takes `lazy=` and declares `LAZY`.
* `Inbound/priorities.py`: `bounded_order(..., lazy=True)` asks a `LAZY` ordering entry for
  its generator and returns a `LazyRanking`.  That is the deque surface the drain always
  consumed (`popleft`, `len`, truth), with the permutation check moved to the pull: foreign,
  duplicate, short and extra trailers raise at the pull that exposes them.  Each pull's
  seconds go to `inb_yplan`.
* `Inbound/transit.py`: `YardTransit.yard_ranking(ctx)` returns the pull queue: a
  `LazyRanking` for a gain policy, a deque over `yard_order` otherwise.
* `Inbound/receiving.py`: the door fill and the refills read `yard_ranking`.  The asap
  fill's plug stages `ranked.popleft()`, so ONE round is priced per plug instead of the
  whole plan.

### Why it is exact

Round r's winner is a function of the frozen view, the candidates and the takes of rounds
1..r-1.  A later round writes nothing an earlier round read, so the first p yields ARE the
first p entries of the eager list.  The inputs the later rounds read must not move between
the fill and the last refill:

* the `SpaceView` is frozen at ctx-freeze;
* every trailer's load is memoised in round 1 (every candidate is in `remaining` there);
* the unload touches only STAGED trailers, which are no longer candidates;
* no owner write happens before `_hand_off`, which runs after the unload.

The drain's shared gain cache (`ctx.gain_cache`) holds only pure functions of
(space, bundle).  A dock ranking that now runs BETWEEN yard rounds fills the same entries
with the same values.

### Proof

| check | result |
|---|---|
| shadow (`assets/s04_lazy_shadow.py --mode shadow`): the eager order on a FRESH gain cache vs every lazy pull | **0 mismatches**, 144 pulls over 5 configurations (myopic/forecast x drain/asap, gated with 2 doors) |
| lazy vs eager run outcome (picks, placements, reorders), 3 pairs | **identical** |
| `Tests/unit/test_lazy_yard_ranking.py` (9) | every prefix of the generator = the eager prefix on the merge-replay, set and pool paths (non-vacuous: >20 scenes rank off arrival order); one pull = one round = 2T placements; the seam; the per-pull permutation check |
| inbound unit tests (gain plan, plan trace, asap, doors, COW, site receiving, standing yard, replay, probe) | 313 passed |
| toy digests | pending (below) |

Test re-pins, each with its reason:

* `test_gain_plan.py::test_the_rider_plans_a_real_drain...` spies `plan_order_iter`; the
  deep yard plan no longer calls `plan_order`.
* `test_reorder_phases.py`'s empty-drain transit stub gains `yard_ranking`.
* Calltree: `yard_plans`, `dock_plans` and `plan_orders` are anchored on `gain:_traced`.
  The yard's plan now runs under `LazyRanking.popleft`, not `yard_order`, and
  `test_every_flow_anchor_that_should_fire_does_fire` REFUSED the old anchor, so the gate
  worked.  The yard knob's x is read off the probe (`yard_T_sum / inb_drains`) because
  `place_loads / plan_orders` no longer inverts to T.  `calltree_inbound_ladder`
  counts entries at `_traced`, its `T` is the counted mean depth, and the old inversion
  survives as `T_equiv`.

### Measurement (meso deep rung, 10 batches; lazy and eager run concurrently under the same load)

| config | yard plan wall eager -> lazy | yard placements eager -> lazy | t_reord eager -> lazy |
|---|---|---|---|
| gain_myopic, 1 door, drain fill | 3.37 s -> **1.04 s (3.2x)** | 2,262 -> 440 (5.1x) | 3.83 -> 1.59 s (-58%) |
| gain_myopic, 1 door, **asap** fill | 8.65 s -> **1.19 s (7.3x)** | 7,100 -> 442 (16x) | 9.05 -> 1.72 s (-81%) |
| gain_gated, 2 doors, drain fill | 2.28 s -> 1.51 s (1.5x) | 1,192 -> 592 (2.0x) | 2.89 -> 2.19 s (-24%) |

**Prediction (gmyopic reord -35% or more): met on the 1-door rung, and exceeded under
asap.**  The asap fill re-ranked the WHOLE yard at every plug and used only the head, so it
was the most wasteful consumer.  The 2-door gated rung saves less: more doors stage more of
the ranking, and the urgency gate's forced prefix is always pulled.

### Growth

The eager yard plan is Theta(T^2) placements per drain.  The lazy one is p(2T - p + 1),
which is linear in T at a fixed pull count p.  With p set by doors and refills rather than
by T, the drain's plan cost drops a power of T.  The ladder fit is S08's.
