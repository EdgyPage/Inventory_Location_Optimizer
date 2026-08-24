"""simdriver.cells — the what-if cell matrix: build/apply/inspect cells.

A cell = a fixed choice of aisle-split × zoning × scheduler over one frozen inventory.
_apply_cell mutates the SAME sim_config.CONFIG object (never rebound) so the change is
seen everywhere; _build_cells names cells k{k}[_l{loss}]_{zone}[_{sched}].

CONFIG is run STATE and the simconfig registry is the DECLARATION; _apply_cell writes to
the former only.  Cell.overrides() reports the same values without applying them."""
from __future__ import annotations

import os
from typing import NamedTuple

from Optimization.config.sim_config import CONFIG   # SAME object — _apply_cell mutates it in place


_SCHED_SHORT = {'round_robin': 'rr', 'lpt': 'lpt'}


class Cell(NamedTuple):
    """One point in the what-if matrix: a fixed choice on every swept axis.

    A NamedTuple rather than a dataclass, deliberately.  This was a bare 4-tuple unpacked
    positionally in five files — `cells.py`, `scenario.py`, `run_simulation.py`,
    `sim_manifest.py` and `run_whatif_delta.py` — so `c[1]`, `c[3]` and
    `(name, split, zoning, sched) = c` all had to keep working.  They do, which is why
    this change cannot break a consumer by being half-applied; named access is simply
    added beside them.

    Adding a FIFTH axis (an inbound sorter policy) is now one field and one line in
    `_build_cells` rather than five positional unpacks to find and renumber.  The names
    here are the same ones the run-tree descriptor records, so it and the producer
    cannot drift apart.
    """
    name: str
    split: dict | None          # {'k', 'capacity_loss'} or None for no aisle split
    zoning: dict                # velocity_zoning spec; {'enabled': False} is off
    scheduler: str              # 'round_robin' | 'lpt'

    @property
    def is_reference(self) -> bool:
        """The natural baseline: no split, no zoning, the legacy scheduler.

        This predicate was written out twice, verbatim, in `scenario.py` and
        `run_simulation.py` — and those two MUST agree, because one picks the cell the
        driver treats as the baseline and the other writes the reference name into
        the run-tree descriptor for every downstream what-if to diff against.  Two copies of a
        predicate that must agree is a bug with a delay on it.
        """
        return (self.split is None and not self.zoning.get('enabled')
                and self.scheduler == 'round_robin')

    def overrides(self) -> dict:
        """What this cell CHANGES about a channel, as a plain record.

        The same three values `_apply_cell` writes, named rather than positional, so a
        caller can see a cell's effect without applying it — which is what a resolution
        pass needs, and what makes the effect diffable between two cells.

        `scheduler` is a pick-config field (like `one_way`), so it lands on every one of a
        channel's pick-config dicts; the other two are channel-level.
        """
        return {'aisle_split': self.split,
                'velocity_zoning': dict(self.zoning),
                'scheduler': self.scheduler}


def reference_cell(cells, fallback: str | None = None) -> str:
    """The name of the reference cell, or `fallback`, or the first cell."""
    return next((c.name for c in cells if c.is_reference), fallback or cells[0].name)


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
                    cells.append(Cell(name, split, dict(zspec), sched))
    return cells


def _apply_cell(aisle_split, zoning, scheduler='round_robin') -> None:
    """Mutate CONFIG for one cell: same aisle_split + velocity_zoning + picker scheduler on every
    channel.  The scheduler is a pick-config field (like one_way), so set it on each channel's
    pick-config dicts.

    Writes into CONFIG and ONLY into CONFIG.  Those pick-config dicts used to be the very
    objects `PICK_CONFIG_BY_KEY[…].cfg` holds, so this loop permanently edited the registry:
    `scheduler` is absent from every config module as written, and after one cell ran the
    frozen spec carried whichever value that cell chose, for the life of the process.
    `sim_config` now copies the dicts when it assembles CONFIG, which is where that boundary
    lives — see the comment beside STORE_CONFIGS.  Do not reintroduce a path that hands the
    registry's own dict to a mutator.

    See `Cell.overrides()` for the same three values as a record, when you want to inspect a
    cell's effect rather than apply it."""
    for ch in CONFIG['channels']:
        CONFIG['channels'][ch]['sizing']['aisle_split'] = aisle_split
        CONFIG['channels'][ch]['velocity_zoning'] = dict(zoning)
        for cfg in CONFIG['channels'][ch]['configs']:
            cfg['scheduler'] = scheduler


def _tightest_split(cells):
    """The split with the largest capacity_loss (fewest bins).  Freezing the sampled
    inventory to it guarantees every roomier cell can hold it (the frozen inventory
    always fits)."""
    splits = [c.split for c in cells
              if c.split and c.split.get('capacity_loss', 0.0) > 0
              and int(c.split.get('k', 1)) > 1]
    return max(splits, key=lambda s: s.get('capacity_loss', 0.0), default=None)


def _cell_complete(scenario_base: str, pairs: list) -> bool:
    """A cell is done when it has ≥1 sim_meta.json per pair (all its configs finalized)."""
    import glob
    if not os.path.isdir(scenario_base):
        return False
    return all(glob.glob(os.path.join(scenario_base, label, '**', 'sim_meta.json'), recursive=True)
               for label, _i, _a in pairs)
