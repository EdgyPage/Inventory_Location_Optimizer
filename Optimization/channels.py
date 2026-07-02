"""channels.py — the Channel abstraction: one operation over a shared warehouse.

A *Channel* bundles the three things that differ between the store and fulfillment
operations while the pick MECHANICS stay shared:

  * regime      — which SKU family / bins it uses (BinKey routing; see regime.py)
  * batch stream — its own order-generation rate/size (a BatchConfig, built per run once
                   the channel's SKU-subset size is known) + an independent seed offset
  * picker pool — its own worker count + cost regression (a PickerProfile wrapping a
                   PickConfig)

The channel list is the single source of truth the runner uses to (a) generate a batch
stream per channel, (b) simulate each channel with its own picker pool + cost, and (c)
derive ``WP_BY_REGIME`` for the per-regime cost routing in placement/labor.  Adding a new
operation later = adding one Channel.

Backward-compatible: a run with a single ``store`` channel reproduces today's behavior
exactly (one batch stream, one picker pool, one cost, one DB subtree).
"""
from __future__ import annotations

from dataclasses import dataclass

from Pick import PickConfig, DEFAULT_HEIGHT_BRACKETS
from Workload import WorkloadParams
from Workload_Builder import BatchConfig
from regime import STORE, FULFILLMENT


@dataclass(frozen=True)
class PickerProfile:
    """A pool of one kind of picker: a name, its pick-time cost regression (a PickConfig
    carrying coefficients + travel speeds), and how many of them work concurrently."""
    name: str
    cost: PickConfig
    num_pickers: int


@dataclass(frozen=True)
class Channel:
    """One operation (store | fulfillment | …) over the shared warehouse."""
    name: str
    regime: str
    picker: PickerProfile
    # Batch-stream shape (BatchConfig needs the per-channel SKU count, supplied at run time).
    batch_mean_fraction: float = 0.20
    batch_std_fraction: float = 0.05
    # Offset added to seed_batches so each channel draws an INDEPENDENT batch stream
    # (store and fulfillment are unrelated order streams) yet stays deterministic.
    batch_seed_offset: int = 0

    def batch_config(self, inventory_size: int) -> BatchConfig:
        """Build this channel's BatchConfig for its SKU-subset size."""
        return BatchConfig(inventory_size=inventory_size,
                           mean_fraction=self.batch_mean_fraction,
                           std_fraction=self.batch_std_fraction)


def fulfillment_pick_config() -> PickConfig:
    """PLACEHOLDER human-walker pick-time regression for the fulfillment channel.

    Same functional FORM as the machine order-picker, different coefficients + speeds:
    faster walking travel, light handling (small items), a tote-swap penalty.  Height
    brackets are irrelevant (fulfillment bins all sit below the first bracket → M=1), so
    the default brackets are kept.  These numbers are a stand-in until real pick-time data
    calibrates the walker — see the plan's deferred "walker regression calibration".
    """
    return PickConfig(
        num_pickers      = 1,        # overridden by the PickerProfile pool size
        x_speed          = 4.5,      # ft/s — a person walking (vs a machine)
        y_speed          = 2.0,      # ft/s — reaching a ~6 ft shelf (small y anyway)
        pick_intercept   = 10.0,     # per-stop setup: locate + scan + grasp
        pick_weight_coef = 0.10,     # light items → weight nearly negligible
        pick_volume_coef = 0.50,
        pick_weight_fn   = 'log',
        pick_volume_fn   = 'log:2',
        cart_swap_coef   = 30.0,     # tote swap at the depot
        height_brackets  = DEFAULT_HEIGHT_BRACKETS,   # no-op for ff bins (all M=1)
    )


def build_channels(store_pick_cfg: PickConfig, store_num_pickers: int,
                   *, include_fulfillment: bool,
                   ff_pick_cfg: PickConfig | None = None,
                   ff_num_pickers: int = 20) -> list[Channel]:
    """Assemble the run's channel list.

    The STORE channel's cost is the run's own (swept) PickConfig, so store results are
    unchanged.  The FULFILLMENT channel is appended only when the inventory has fulfillment
    items (``include_fulfillment``); it uses the walker cost + its own picker pool.
    """
    channels = [
        Channel(
            name='store', regime=STORE, batch_seed_offset=0,
            picker=PickerProfile('store_machine', store_pick_cfg, store_num_pickers),
        )
    ]
    if include_fulfillment:
        channels.append(Channel(
            name='fulfillment', regime=FULFILLMENT,
            # A distinct seed offset → an independent batch stream from store.
            batch_seed_offset=1_000_000,
            picker=PickerProfile('fulfillment_walker',
                                 ff_pick_cfg or fulfillment_pick_config(),
                                 ff_num_pickers),
        ))
    return channels


def wp_by_regime(channels: list[Channel]) -> dict:
    """{regime: WorkloadParams} derived from each channel's picker cost — the map the
    placement/labor cost routing keys on (attached to the primary WorkloadParams as
    ``.by_regime`` so it rides through the assignment builders without signature churn)."""
    return {ch.regime: WorkloadParams.from_pick_config(ch.picker.cost) for ch in channels}
