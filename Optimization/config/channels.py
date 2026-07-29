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

from Warehouse.picking.Pick import PickConfig
from Warehouse.layout.Storage_Primitive import FulfillmentCart
from Optimization.metrics.Workload import WorkloadParams
from Warehouse.picking.Workload_Builder import BatchConfig
from Warehouse.kernel.regime import STORE, FULFILLMENT


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
    # Restock-rule subset this channel runs (keys like 'fifo', 'rank_labor'); None ⇒ full
    # suite.  Lets one section run a curated subset while another runs everything.
    restocks: tuple[str, ...] | None = None

    def batch_config(self, inventory_size: int) -> BatchConfig:
        """Build this channel's BatchConfig for its SKU-subset size."""
        return BatchConfig(inventory_size=inventory_size,
                           mean_fraction=self.batch_mean_fraction,
                           std_fraction=self.batch_std_fraction)


def fulfillment_pick_config() -> PickConfig:
    """The fulfillment channel's default walker pick-time regression.

    A thin reader of ``sim_config.FULFILLMENT_CONFIGS[0]`` — the SINGLE source of
    truth for the walker numbers.  (This function used to carry its own copy, which
    silently diverged once the sweep entries were tuned; folding it onto CONFIG ended
    the two-sources-of-truth hazard.)  num_pickers stays 1 here — the PickerProfile
    pool size overrides it.  Imported lazily to avoid the sim_config → channels
    import cycle (FF_BATCH_SEED_OFFSET lives in this module).
    """
    from Optimization.config.sim_config import FULFILLMENT_CONFIGS, _build_pick_cfg
    return _build_pick_cfg(FULFILLMENT_CONFIGS[0], num_pickers=1,
                           default_cart=FulfillmentCart)


def make_channel(name: str, regime: str, pick_cfg: PickConfig, num_pickers: int,
                 *, restocks: tuple[str, ...] | None = None,
                 batch_seed_offset: int = 0,
                 batch_mean_fraction: float = 0.20,
                 batch_std_fraction: float = 0.05) -> Channel:
    """Build a single Channel from a picker cost + pool size.

    The one-channel primitive the runner uses to sweep each section's configs
    independently; build_channels() composes the standard store+fulfillment pair on
    top of it.  ``batch_seed_offset`` gives a channel an INDEPENDENT batch stream
    (store uses 0; fulfillment a large offset so its stream never overlaps store's).
    ``batch_mean_fraction``/``batch_std_fraction`` set the channel's batch-stream shape
    (each channel may differ; the runner sources these from CONFIG).
    """
    return Channel(
        name=name, regime=regime, batch_seed_offset=batch_seed_offset,
        picker=PickerProfile(f'{name}_picker', pick_cfg, num_pickers),
        restocks=restocks,
        batch_mean_fraction=batch_mean_fraction,
        batch_std_fraction=batch_std_fraction,
    )


# Seed offset that gives the fulfillment channel a batch stream independent of store's.
FF_BATCH_SEED_OFFSET = 1_000_000


def build_channels(store_pick_cfg: PickConfig, store_num_pickers: int,
                   *, include_fulfillment: bool,
                   ff_pick_cfg: PickConfig | None = None,
                   ff_num_pickers: int = 20,
                   store_restocks: tuple[str, ...] | None = None) -> list[Channel]:
    """Assemble the run's channel list.

    The STORE channel's cost is the run's own (swept) PickConfig, so store results are
    unchanged.  The FULFILLMENT channel is appended only when the inventory has fulfillment
    items (``include_fulfillment``); it uses the walker cost + its own picker pool.

    ``store_restocks`` restricts the store channel to a subset of restock rules (None ⇒ full
    suite); fulfillment always runs the full suite.
    """
    channels = [
        Channel(
            name='store', regime=STORE, batch_seed_offset=0,
            picker=PickerProfile('store_machine', store_pick_cfg, store_num_pickers),
            restocks=store_restocks,
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
