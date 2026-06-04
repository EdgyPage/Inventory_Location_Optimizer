"""Aisle_Dimensions.py — physical dimension helpers for warehouse aisle layout.

All sizes in "storage units" where:
  - PALLET_WIDTH      = 48  (Pallet.max_width)
  - PALLET_HEIGHT_MAX = 48  (Storage_Size extra_large height)
  - SINGLETON_WIDTH   = 16  (Singleton.max_width)

Bin counts per aisle are derived from physical dimensions and unit type:
  bins_per_row  = aisle_width  // unit_width      (x direction)
  bins_per_col  = aisle_height // bin_height       (y direction, per size tier)
"""

from Storage_Primitive import Pallet, Singleton, Storage_Size

PALLET_WIDTH:         int = Pallet.max_width          # 48
SINGLETON_WIDTH:      int = Singleton.max_width        # 16
SIZE_HEIGHTS:         dict[str, int] = Storage_Size.available_sizes_heights
PALLET_HEIGHT_MAX:    int = max(SIZE_HEIGHTS.values()) # 48  (extra_large)
# Singleton bins are a single fixed height — no size tiers.
# Set equal to PALLET_HEIGHT_MAX so any valid singleton item fits.
SINGLETON_BIN_HEIGHT: int = PALLET_HEIGHT_MAX          # 48


def aisle_width_for(n_pallet_columns: int) -> int:
    """Physical aisle width for *n_pallet_columns* pallet columns.

    Example: aisle_width_for(25) → 25 × 48 = 1200 physical units.
    """
    return n_pallet_columns * PALLET_WIDTH


def aisle_height_for(n_levels: int, reference_size: str = 'extra_large') -> int:
    """Physical aisle height for *n_levels* of *reference_size* pallets.

    Example: aisle_height_for(30) → 30 × 48 = 1440 physical units.
    """
    return n_levels * SIZE_HEIGHTS[reference_size]


def unit_bin_width(unit_type: str) -> int:
    """Physical width of one bin for *unit_type* ('pallet' or 'singleton')."""
    return SINGLETON_WIDTH if unit_type == 'singleton' else PALLET_WIDTH


def bins_along_x(aisle_width: int, unit_type: str) -> int:
    """Number of bins per row given *aisle_width* and *unit_type*."""
    return aisle_width // unit_bin_width(unit_type)


def bins_along_y(aisle_height: int, size: str) -> int:
    """Number of bin rows given *aisle_height* and pallet *size* tier."""
    return aisle_height // SIZE_HEIGHTS[size]


def uniform_aisle_bins(unit_type: str, storage_size: str,
                       aisle_width: int, aisle_height: int) -> int:
    """Bin count for a single-size-tier aisle (every bin one storage_size).

    Pallet aisles: n_cols (48-wide) × n_rows (aisle_height // tier height).
    Singleton aisles: n_cols (16-wide) × n_rows (aisle_height // 48).
    """
    n_cols = aisle_width // unit_bin_width(unit_type)
    if unit_type == 'singleton':
        return n_cols * (aisle_height // SINGLETON_BIN_HEIGHT)
    return n_cols * (aisle_height // SIZE_HEIGHTS[storage_size])
