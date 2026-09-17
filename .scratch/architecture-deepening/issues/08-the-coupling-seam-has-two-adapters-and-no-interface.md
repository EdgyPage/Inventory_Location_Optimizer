# 08 - the coupling seam has two adapters and no interface

Type: refactor
Status: needs-triage
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
