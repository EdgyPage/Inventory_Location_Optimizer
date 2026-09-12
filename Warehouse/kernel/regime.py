"""regime.py — storage-regime identity (store vs fulfillment).

A *regime* is a self-contained ``(handling, storage_type)`` family with its own bin
catalog, batch stream, and picker cost.  Fulfillment items/bins/aisles are tagged with
the ``'fulfillment'`` handling + storage_type (+ ``unit_category``); everything else is
``'store'``.  ``regime_of`` duck-types over Orders, StorageUnits, Bins, and Aisles so every
layer (Warehouse primitives, planner, placement, cost routing, runner, analysis) can key
on the SAME single-valued regime without importing warehouse internals — this module has
no dependencies, so it never introduces an import cycle.

Two spellings, one rule: ``regime_of`` reads an ENTITY and ``regime_of_key`` reads a
**BinKey**.  The second exists because a key is a plain tuple that the first cannot read
and does not refuse — see its docstring.

The single-valued-per-entity property is what keeps the per-regime cost routing cheap:
each bin's ``_D`` and each order's ``labor_cost`` is one value, computed with that entity's
regime parameters, not a per-regime dict.
"""
from __future__ import annotations

STORE: str = 'store'
FULFILLMENT: str = 'fulfillment'
REGIMES: tuple[str, ...] = (STORE, FULFILLMENT)


def regime_of(x) -> str:
    """Storage regime ('store' | 'fulfillment') of an Order, StorageUnit, Bin, or Aisle.

    Duck-typed and single-valued.  Unknown / legacy objects fall back to 'store', so any
    inventory or warehouse built before this feature behaves exactly as before.
    """
    # StorageUnit (Pallet/Singleton/FulfillmentBin): unit_category is the strongest signal.
    if getattr(x, 'unit_category', None) == FULFILLMENT:
        return FULFILLMENT
    # Order, or a unit's .order: storage_handle_config carries handling/category.
    shc = getattr(x, 'storage_handle_config', None)
    if shc is None:
        order = getattr(x, 'order', None)
        if order is not None:
            shc = getattr(order, 'storage_handle_config', None)
    if shc is not None:
        if (getattr(shc, 'handling', None) == FULFILLMENT
                or getattr(shc, 'category', None) == FULFILLMENT):
            return FULFILLMENT
        return STORE
    # Bin / Aisle: unit_type / handling_type / storage_type.
    if getattr(x, 'unit_type', None) == FULFILLMENT:
        return FULFILLMENT
    if (getattr(x, 'handling_type', None) == FULFILLMENT
            or getattr(x, 'storage_type', None) == FULFILLMENT):
        return FULFILLMENT
    return STORE


def regime_of_key(key) -> str:
    """Storage regime of a **BinKey** — the 4-tuple ``(handling, category, storage_size,
    unit_category)`` a bin or a unit is classified under, never an object.

    Its own function rather than a branch inside ``regime_of``, because a key is a PLAIN
    TUPLE: every ``getattr`` above falls through it, so ``regime_of(key)`` answers 'store'
    for a fulfillment key — silently, and always in the same direction.  Nothing raises,
    which is why the trap needs a correct spelling to point at rather than a warning
    (``Inbound/site_space.py`` records the same trap on the bin side, where the fix was to
    ask the bin instead).

    Reads the same three fields ``regime_of`` reads — handling, category, unit_category —
    which is what makes a key sufficient to name an OWNER: regime is itself a BinKey
    component (see ``inventory_common``), so a group of units sharing a key shares a
    regime.  Slot 2 (``storage_size``) is deliberately not read: tier names are per
    unit_category and carry no regime of their own.

    A malformed key raises on the unpack, which is the loud end of the same argument.
    """
    handling, category, _storage_size, unit_category = key
    if (unit_category == FULFILLMENT or handling == FULFILLMENT
            or category == FULFILLMENT):
        return FULFILLMENT
    return STORE
