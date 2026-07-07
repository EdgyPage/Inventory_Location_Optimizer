from __future__ import annotations
from Storage_Primitive import StorageUnit
from Aisle_Dimensions import (
    PALLET_WIDTH, SINGLETON_WIDTH, PALLET_HEIGHT_MAX,
    SINGLETON_BIN_HEIGHT, SIZE_HEIGHTS, unit_bin_width, bins_along_x, bins_along_y,
)


class Aisle:
    next_aisle_id: int = 1

    class Bin:
        def __init__(
            self,
            aisle: Aisle,
            bayX: int,
            bayY: int,
            storage_size: str,
            x_step: int,
            y_step: int,
            y_phys: int,
        ) -> None:
            self.aisle: Aisle = aisle
            self.bayX: int = bayX
            self.bayY: int = bayY
            self.storage_size: str = storage_size
            self.handling_type: str = aisle.handling_type
            self.storage_type: str = aisle.storage_type
            self.unit_type: str = aisle.unit_type
            self.storage: StorageUnit | None = None
            # Physical step sizes — distance to the next bin along each axis.
            self.x_step: int = x_step
            self.y_step: int = y_step
            # Physical centre position along the Y axis.  Stored directly
            # because multi-size aisles have non-uniform y_step values.
            self._y_phys: int = y_phys

        @property
        def x_phys(self) -> int:
            """Physical centre X position of this bin."""
            return (self.bayX - 1) * self.x_step + self.x_step // 2

        @property
        def y_phys(self) -> int:
            """Physical centre Y position of this bin."""
            return self._y_phys

        @property
        def location(self) -> tuple[int, int, int]:
            return (self.aisle.aisle_id, self.bayX, self.bayY)

        @property
        def storage_handling_type(self) -> tuple[str, str]:
            return (self.handling_type, self.storage_type)

    def __init__(
        self,
        storage_size: str,
        handling_type: str,
        storage_type: str,
        unit_type: str,
        aisle_width: int,
        aisle_height: int,
        bin_width: int | None = None,
        bin_height: int | None = None,
    ) -> None:
        """Create a uniform-size aisle from physical dimensions.

        Parameters
        ----------
        storage_size  : single size tier for all bins ('small', 'medium', etc.)
        aisle_width   : physical width in storage units (e.g. 25 × 48 = 1200)
        aisle_height  : physical height in storage units (e.g. 30 × 48 = 1440)
        bin_width     : explicit bin footprint width (fulfillment); None ⇒ from unit_type
        bin_height    : explicit bin height (fulfillment); None ⇒ from unit_type/SIZE_HEIGHTS
        """
        self.aisle_id: int = Aisle.next_aisle_id
        Aisle.next_aisle_id += 1
        self.handling_type: str = handling_type
        self.storage_type: str = storage_type
        self.unit_type: str = unit_type
        self.aisle_width: int = aisle_width
        self.aisle_height: int = aisle_height

        # Explicit bin geometry (fulfillment tiers) overrides the unit_type-derived
        # defaults; both None keeps the store pallet/singleton behavior byte-for-byte.
        x_step = bin_width if bin_width is not None else unit_bin_width(unit_type)
        if bin_height is not None:
            y_step = bin_height
            bin_size: str = storage_size
        elif unit_type == 'singleton':
            # All singleton bins are the same fixed height — no size tiers.
            y_step = SINGLETON_BIN_HEIGHT
            bin_size = 'singleton'
        else:
            y_step = SIZE_HEIGHTS[storage_size]
            bin_size = storage_size
        self.storage_size: str = bin_size

        n_cols = aisle_width  // x_step
        n_rows = aisle_height // y_step

        self.bayXPerAisle: int = n_cols   # actual bin grid extents (for visualization)
        self.bayYPerAisle: int = n_rows

        self.bins: list[Aisle.Bin] = [
            Aisle.Bin(
                self,
                col + 1,
                row + 1,
                bin_size,
                x_step = x_step,
                y_step  = y_step,
                y_phys  = row * y_step + y_step // 2,
            )
            for col in range(n_cols)
            for row in range(n_rows)
        ]

    @property
    def storage_handling_type(self) -> tuple[str, str]:
        return (self.handling_type, self.storage_type)

    @classmethod
    def from_size_distribution(
        cls,
        storage_sizes: list[str],
        probabilities: list[float],
        handling_type: str,
        storage_type: str,
        unit_type: str,
        aisle_width: int,
        aisle_height: int,
    ) -> Aisle:
        """Create a multi-size aisle from physical dimensions.

        Each size tier is allocated a proportional share of *aisle_height*.
        Within each tier slice, bins stack at the tier's bin height, giving
        more Y levels for small tiers and fewer for large tiers.
        """
        # Singleton aisles have no size tiers — delegate to __init__.
        if unit_type == 'singleton':
            return cls(None, handling_type, storage_type, unit_type, aisle_width, aisle_height)

        aisle = object.__new__(cls)
        aisle.aisle_id = cls.next_aisle_id
        cls.next_aisle_id += 1
        aisle.storage_size = None
        aisle.handling_type = handling_type
        aisle.storage_type = storage_type
        aisle.unit_type = unit_type
        aisle.aisle_width = aisle_width
        aisle.aisle_height = aisle_height

        x_step = unit_bin_width(unit_type)
        n_cols = aisle_width // x_step

        # Allocate physical height per size tier proportionally.
        remaining_h = aisle_height
        tier_heights: dict[str, int] = {}
        for size, prob in zip(storage_sizes[:-1], probabilities[:-1]):
            h = round(prob * aisle_height)
            tier_heights[size] = h
            remaining_h -= h
        tier_heights[storage_sizes[-1]] = remaining_h

        # Build row entries: (bayY, storage_size, y_step, y_phys)
        row_entries: list[tuple[int, str, int, int]] = []
        cumulative_h = 0
        bay_y = 1
        for size in storage_sizes:
            bin_h = SIZE_HEIGHTS[size]
            alloc = tier_heights[size]
            n_rows = alloc // bin_h
            for row_in_tier in range(n_rows):
                y_phys = cumulative_h + row_in_tier * bin_h + bin_h // 2
                row_entries.append((bay_y, size, bin_h, y_phys))
                bay_y += 1
            cumulative_h += alloc

        aisle.bayXPerAisle = n_cols
        aisle.bayYPerAisle = bay_y - 1

        aisle.bins = [
            cls.Bin(
                aisle,
                col + 1,
                bay_y_idx,
                size,
                x_step = x_step,
                y_step  = y_step,
                y_phys  = y_phys_val,
            )
            for col in range(n_cols)
            for bay_y_idx, size, y_step, y_phys_val in row_entries
        ]
        return aisle
