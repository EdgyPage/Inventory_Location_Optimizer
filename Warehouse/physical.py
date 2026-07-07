"""physical.py — the two physical footprint constants (zero-dependency leaf).

Single source for the numbers that used to be repeated as literals across
Order (dim clamps), Storage_Primitive (unit footprints + tallest pallet tier),
and the docs:

  * PALLET_FOOTPRINT  — pallet max width/length AND the tallest pallet-tier
    height (extra_large), in storage units (inches).
  * COMPACT_FOOTPRINT — the small forward-pick footprint shared by the store
    Singleton and the fulfillment bin.

Kept in a leaf module (imports nothing) so both Order and Storage_Primitive can
read it without a cycle (Storage_Primitive imports Order).
"""

PALLET_FOOTPRINT:  int = 48
COMPACT_FOOTPRINT: int = 16
