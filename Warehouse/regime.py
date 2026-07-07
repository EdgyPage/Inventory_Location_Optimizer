"""regime.py — storage-regime identity (store vs fulfillment).

A *regime* is a self-contained ``(handling, storage_type)`` family with its own bin
catalog, batch stream, and picker cost.  Fulfillment items/bins/aisles are tagged with
the ``'fulfillment'`` handling + storage_type (+ ``unit_category``); everything else is
``'store'``.  ``regime_of`` duck-types over Orders, StorageUnits, Bins, and Aisles so every
layer (Warehouse primitives, planner, placement, cost routing, runner, analysis) can key
on the SAME single-valued regime without importing warehouse internals — this module has
no dependencies, so it never introduces an import cycle.

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
