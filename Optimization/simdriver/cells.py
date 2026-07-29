"""simdriver.cells — the what-if cell matrix: build/apply/inspect cells.

A cell = a fixed choice of aisle-split × zoning × scheduler over one frozen inventory.
_apply_cell mutates the SAME sim_config.CONFIG object (never rebound) so the change is
seen everywhere; _build_cells names cells k{k}[_l{loss}]_{zone}[_{sched}]."""
from __future__ import annotations

import os

from Optimization.config.sim_config import CONFIG   # SAME object — _apply_cell mutates it in place


_SCHED_SHORT = {'round_robin': 'rr', 'lpt': 'lpt'}


def _build_cells(spec):
    """Combinatorial (scheduler × zoning × k × capacity_loss) cells from a WHATIF spec.  k=1 = no
    split (loss collapses to 0).  Returns [(name, aisle_split|None, zoning_spec, scheduler), …];
    the (k=1, off, round_robin) cell is the natural reference.  ``schedulers`` defaults to
    ['round_robin'] (byte-identical, no name suffix); a multi-value list adds a `_rr`/`_lpt`
    suffix so the picker-scheduler becomes a sweep axis alongside aisle_split and zoning."""
    scheds = spec.get('schedulers', ['round_robin'])
    multi_sched = len(scheds) > 1
    cells, seen = [], set()
    for sched in scheds:
        for zname, zspec in spec['zoning']:
            for k in spec['ks']:
                for loss in (spec['losses'] if k > 1 else [0.0]):
                    split = None if k <= 1 else {'k': k, 'capacity_loss': loss}
                    name = f'k{k}' + ('' if k <= 1 else f'_l{int(round(loss * 100))}') + f'_{zname}'
                    if multi_sched:
                        name += f'_{_SCHED_SHORT.get(sched, sched)}'
                    if name in seen:
                        continue
                    seen.add(name)
                    cells.append((name, split, dict(zspec), sched))
    return cells


def _apply_cell(aisle_split, zoning, scheduler='round_robin') -> None:
    """Mutate CONFIG for one cell: same aisle_split + velocity_zoning + picker scheduler on every
    channel.  The scheduler is a pick-config field (like one_way), so set it on each channel's
    pick-config dicts."""
    for ch in CONFIG['channels']:
        CONFIG['channels'][ch]['sizing']['aisle_split'] = aisle_split
        CONFIG['channels'][ch]['velocity_zoning'] = dict(zoning)
        for cfg in CONFIG['channels'][ch]['configs']:
            cfg['scheduler'] = scheduler


def _tightest_split(cells):
    """The split with the largest capacity_loss (fewest bins).  Freezing the sampled
    inventory to it guarantees every roomier cell can hold it (the frozen inventory
    always fits)."""
    splits = [c[1] for c in cells
              if c[1] and c[1].get('capacity_loss', 0.0) > 0 and int(c[1].get('k', 1)) > 1]
    return max(splits, key=lambda s: s.get('capacity_loss', 0.0), default=None)


def _cell_complete(scenario_base: str, pairs: list) -> bool:
    """A cell is done when it has ≥1 sim_meta.json per pair (all its configs finalized)."""
    import glob
    if not os.path.isdir(scenario_base):
        return False
    return all(glob.glob(os.path.join(scenario_base, label, '**', 'sim_meta.json'), recursive=True)
               for label, _i, _a in pairs)
