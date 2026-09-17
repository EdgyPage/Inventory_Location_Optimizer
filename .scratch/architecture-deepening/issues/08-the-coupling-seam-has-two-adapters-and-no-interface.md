# 08 - the coupling seam has two adapters and no interface

Type: refactor
Status: resolved
Blocked by: 06

## Context

`strategy_runner.py` asks the same question 28 times:

- `site is None` / `site is not None` at `1646, 1668, 1705, 1733, 1735, 2072, 2121, 2156, 2184,
  2203, 2241, 2530, 2531, 2627, 2662-2663, 2733`
- `pool is None` / `pool is not None` at `1587, 1589, 1617, 1618, 1621, 2033, 2362, 2379, 2380,
  2557, 2561, 2586, 2592`

Each pair is the same dispatch:

| solo | coupled |
|---|---|
| `mgr.transit_snapshot()` | `site.coord.transit_census_for(mgr)` |
| `mgr.receiving_snapshot()` | `site.coord.snapshot_for(mgr)` |
| `mgr.drain_receiving_records()` | `site.coord.drain_records_for(mgr)` |
| `mgr.dock_depth` | `site.coord.dock_depth_for(mgr)` |
| `mgr.drain_yard_trailers()` | (site collects -- the leaf accessor REFUSES) |
| `_recv_crew.workers(_uid)` | `site.workers` |
| `_put_crew.workers(_uid)` | `pool.workers` |

**Two adapters already exist, so the seam is real** -- solo is "the leaf's own manager answers",
site is "the coordinator answers on its behalf", and the variation is genuine per ADR-0005.

**Because there is no single place to consult, correctness is enforced by refusal in the domain
layer.** Eight accessors in `Warehouse/` and `Inbound/` raise under a site scope. The comment at
`2156-2163` says why: `receiving_snapshot` refuses "because three of the four RESET, so the first
caller would take the site's whole batch and leave the other channel reporting an idle dock." A
refusal in the domain layer is a leak -- `Warehouse/` and `Inbound/` had to learn that a DRIVER
concept exists.

**The scope is three parameters, not one.** `pool`, `site_gain` and `site` are each independently
`None`, giving 8 nominal combinations of which ~3 are legal, enforced by guards scattered across
`_build_site_dock` (`:1002`), `_build_put_pool` (`:758`) and `_bind_put_crews`.

## What to build

`LeafScope` with two adapters -- `SoloScope` and `SiteScope` -- built once (on the `ArmAssembly`
from ticket 06) and thereafter the only thing consulted:

```
workers / dock / transit / put_workers / put_clocks
transit_census(mgr)        receiving_snapshot(mgr)
drain_records(mgr)         note_records(mgr, recv_clock)
dock_depth(mgr)            yard_rows(mgr)        # SiteScope -> ([], [])
standing_yard(mgr)         put_base(put_clock) / reset_put_clocks
```

The `None` checks leave `_step` and `_finish` entirely; three injected parameters collapse to one.
The domain-layer refusals become a belt-and-braces backstop rather than the primary mechanism.

## ADR note

**This implements ADR-0005, it does not reopen it.** The ruling -- pack-denominated quantities stay
with the channel, trailer-denominated ones belong to the site -- becomes the shape of the interface
rather than a rule each of 28 call sites remembers. `CONTEXT.md` under *Site dock* names a
site-coupling successor effort; that would become a third adapter rather than a 29th ternary.

## Verification

- `SiteScope` tested against a fake coordinator. The property "each leaf gets its own share and
  the shares close against the dock's totals" -- today proved only through a full coupled run in
  `Tests/unit/test_site_receiving_totals.py`, with `test_coupled_unit_e2e.py` (673 lines) the only
  other coverage -- becomes a unit assertion.
- Both poles byte-identical: memory `coupled-runs-are-byte-identical-until-the-pool` notes a
  coupled-vs-uncoupled TIE is now the bug, not the feature. Prove each pole against itself.
- Gates 1, 2, 10.


---

## RESOLVED 2026-09-17  (0958f11f)

`Optimization/simdriver/leaf_scope.py`. All 28 checks consult one object; `_step` and `_finish`
have none left, and `ArmAssembly` carries `scope` in place of `pool` and `site`.

### THE TICKET COUNTED EIGHT CORNERS; THERE ARE THREE RUNGS

This is the correction that changed the design. The ticket reads the three parameters as
independent and the legality as scattered -- "8 nominal combinations of which ~3 are legal,
enforced by guards scattered across `_build_site_dock` (`:1002`), `_build_put_pool` (`:758`)
and `_bind_put_crews`". Reading those guards, the reason is structural:

  * `_build_put_pool` **never returns None** -- it raises or it builds -- and is only called on
    a coupled unit. So `pool is not None` IS "this is a coupled unit".
  * `_build_site_dock` mints the receiving block with `pool.workers[-1].uid + 1`. So a site dock
    **cannot exist without a pool**.

    SOLO      no pool, no site     an uncoupled run
    POOLED    pool, no site        a coupled unit, inbound OFF (site-dock 06)
    DOCKED    pool and site        a coupled unit, standing yard

Three rungs of a ladder, so the adapters are an inheritance chain rather than the two siblings
the ticket names: each rung is the one below it with one more group of answers moved to the
site. The fourth corner is **unnameable** -- there is no class for it -- which is a stronger
statement than three guards agreeing.

`site_gain` is not on the ladder. It is read at exactly one place (binding the gain bundle into
the transit), so it stays an argument; a scope member consulted once is a parameter in a costume.

### What the ladder bought that the ternaries could not

A coupled unit with inbound OFF must answer every inbound question exactly as an uncoupled run
does. That was a promise kept by twenty-eight call sites each testing the *right* variable --
`site is None`, never `pool is None` -- and a single slip would have moved that pole's numbers
with nothing to notice. It is now `test_the_pooled_rung_overrides_nothing_inbound`: one
assertion over `PooledScope.__dict__`, with `test_the_site_rung_overrides_every_inbound_member`
as its non-vacuity mirror.

### A SECOND CORRECTION: the closure property is not the scope's

The ticket asks for "each leaf gets its own share and the shares close against the dock's
totals" as a unit assertion against a fake coordinator. **That would assert the fake.** The
closure belongs to the coordinator and is proved against real rows in
`Tests/unit/test_site_receiving_totals.py`; what the SCOPE owns is that every site-owned
question is routed **with the asking leaf's manager**, which is the precondition that makes a
partition possible at all. `test_every_site_question_carries_the_ASKING_leafs_manager` asserts
that, and the manager stub refuses exactly what the real one refuses under a site scope -- so a
scope that fell through to the leaf fails in this file rather than being rescued by the
domain-layer refusal, which is the whole point of moving the decision out of `Warehouse/`.

### PRESERVED RATHER THAN TIDIED: the two poles read at different instants

`lead_depth` and `in_transit` are read by a solo leaf AFTER `check_reorders` has fired this
batch's reorders, and by the site off ONE partitioning pass BEFORE it. The obvious cleanup --
resolve all three at census time, which the site already does and which the comment there
recommends ("all three transit reads come off ONE pass so they cannot disagree") -- would have
moved every solo run's published `lead_queue_depth` and `in_transit_qty`. They stay METHODS
taking the census value, and `test_solo_reads_the_lead_numbers_off_the_manager_not_the_census`
asserts the solo rung ignores what it is handed.

Whether the asymmetry is itself right is a question for a successor, not for a refactor that
must not move a number. It is written down in the module docstring so it is a decision next time
rather than a discovery.

### Verification

| check | result |
|---|---|
| `Tests/unit` + `Tests/integration -k "not gpu"` | 3,207 passed / 2 skipped |
| `Tests/e2e` | 58 passed / 1 skipped (12m02s) |
| `Tests/unit/test_leaf_scope.py` | 19 tests |
| toy run vs baseline, `run_digest.py` | **IDENTICAL**, 136 arms |
| gates 1-5, 7-10 | green |
| gate 6 (profile-tree) | red at HEAD all session, unrelated -- none of its nine shape sources is in this diff |

**Digest reach again.** The tiny profile fields no site dock, so IDENTICAL covers the SOLO rung
only. The pooled and docked rungs rest on `Tests/e2e/test_coupled_unit_e2e.py` and
`test_coupled_resume_e2e.py`. Memory `coupled-runs-are-byte-identical-until-the-pool` is why a
coupled-vs-uncoupled tie could not have been the instrument: since the pool landed, a tie there
is the bug rather than the feature, so each pole is proved against itself.

### What this unblocks

Nothing was waiting on 08.
