"""policy — one placement family, declared once.

Competing placement policies are what this repo exists to measure, and adding one cost
5-8 declarations across four packages: a pool class and builder here, an import plus a
`_build_*` helper plus a `_RESTOCKS` row in `Optimization/config/strategies.py`, a branch in
`strategy_runner._gain_bundle_for` naming the exact aisle books its `take` commits to, a
name in `Inbound.gain.FAITHFUL_GAIN_FAMILIES`, rows in three dead registries, an equivalence
test and an arch hint.

Three of those are already gone (the dead registries, deleted 2026-09-16). This record takes
four more: the positional `_RESTOCKS` tuple becomes named fields, `FAITHFUL_GAIN_FAMILIES`
becomes derived, the gain evaluator's per-family `aisle_state=` dicts become derived, and its
`if restock == ...` chain becomes a lookup on `gain`.

## Where the records live, and why not here

The TYPE is here because a placement family is a placement concept. The seventeen INSTANCES
live in `Optimization/config/strategies.py`, beside the `build` functions they carry: a build
takes `(mgr, StrategyContext)`, and `StrategyContext` is the run harness's own assembly of
what a worker happens to have. Moving the records here would drag that up with them, and
`wh_placement -> optimization` is a forbidden edge for exactly the reason it should be —
"an assignment function must not import the run harness; it is a closure over its own
inputs".

## `ledger_terms` is DECLARED, and then checked against the wiring

It names the `AisleLedger` books a family's `take` commits to. It has to be declared rather
than read off a pool, because the gain evaluator needs it BEFORE it builds one: it copies
exactly those books so a virtual placement cannot advance the real warehouse. The
declaration's own hazard is the one `_gain_bundle_for` states — "a dict left off that list is
not a refusal, it is a virtual placement advancing the REAL warehouse" — so it is not left
as a claim: `Tests/unit/test_placement_policy.py` BUILDS each family's pool and compares the
declaration against `AisleLedger.bound`, the books that pool was actually handed. A symbol
table that resolves names cannot catch a wrong relationship; exercising it can.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from Warehouse.inventory.aisle_ledger import AisleLedger

__all__ = ['PlacementPolicy', 'GAIN_ADAPTERS']


#: How the inbound gain evaluator prices an arm faithfully.  `None` means it cannot, and the
#: evaluator REFUSES that arm rather than pricing a fiction under its name.
#:
#: - `uniform` — no pool and no direction: the tier's mean and a seat count, a closed form
#:   over the tier's own geometry (`fifo`).
#: - `merge`   — the proven k-cheapest merge over extremal-D bins (`tmin`, `tmax`), with
#:   `gain_minimize` giving the direction.
#: - `pool`    — the arm's OWN builder, reopened over copy-on-write views of the books
#:   `ledger_terms` names, so the virtual placement writes into the copies.
GAIN_ADAPTERS: frozenset = frozenset({'uniform', 'merge', 'pool'})


@dataclass(frozen=True)
class PlacementPolicy:
    """One restock (reorder placement) rule: what it needs, what it builds, what it commits.

    Frozen, because the grid in `strategies.py` reads these to build every `Strategy` and a
    mutable record would let one arm's setup change another's.
    """

    key: str                        #: restock-rule key — 'fifo', 'rank_labor', …
    label: str                      #: plot label — 'FIFO', 'Rank_labor', …
    build: Callable[[Any, Any], None]   #: (mgr, ctx) -> None; sets exactly one mgr.placement

    #: What the worker must arm BEFORE `build` runs.  `needs_affinity` rebuilds the aisle
    #: ledger's membership half; `needs_demand` prices the levels; `uses_aisle_index` runs
    #: `init_travel_costs` so a per-unit policy can read `mgr._aisle_index`.
    needs_affinity: bool = False
    needs_demand: bool = False
    uses_aisle_index: bool = False

    #: The `AisleLedger` books this family's `take` COMMITS to — see the module docstring.
    #: Empty for the families with no pool at all.
    ledger_terms: tuple[str, ...] = ()

    #: The gain adapter, or None for "the evaluator cannot price this arm faithfully".
    gain: str | None = None
    gain_minimize: bool = True          #: `merge` only — inert otherwise
    gain_expect_heads: bool = False     #: `pool` only — price at the mean over the heads

    def __post_init__(self) -> None:
        if self.gain is not None and self.gain not in GAIN_ADAPTERS:
            raise ValueError(
                f'{self.key!r} declares gain adapter {self.gain!r}; known adapters are '
                f'{sorted(GAIN_ADAPTERS)}, and None means the evaluator refuses this arm')
        bad = [t for t in self.ledger_terms if t not in AisleLedger.POLICY_BOOKS]
        if bad:
            raise ValueError(
                f'{self.key!r} declares ledger terms {bad}, which are not books a placement '
                f'policy can write; those are {list(AisleLedger.POLICY_BOOKS)}. A term with '
                f'no book is a copy the gain evaluator would never make.')
        if self.gain == 'pool' and not self.ledger_terms:
            raise ValueError(
                f'{self.key!r} opens a POOL for the gain evaluator but declares no ledger '
                f'terms, so the evaluator would copy nothing and the virtual placement '
                f'would advance the real warehouse — the exact failure the declaration '
                f'exists to prevent')
        if self.gain_expect_heads and self.gain != 'pool':
            raise ValueError(
                f'{self.key!r} asks for expectation pricing over pool heads without a pool')

    @property
    def state_names(self) -> tuple[str, ...]:
        """`ledger_terms` as the MANAGER attribute names the gain evaluator keys on.

        Two vocabularies for the same books: the ledger calls them `sku_sets`, the manager
        `_aisle_sku_sets`, and `Inbound.gain`'s copier tables key on `aisle_sku_sets` because
        `Inbound/` may not import the placement engine.  The mapping is mechanical and lives
        here so no third hand-written list exists to disagree.
        """
        return tuple('aisle_' + t for t in self.ledger_terms)
