"""simdriver.cells — the what-if cell matrix: build/apply/inspect cells.

A cell = a fixed choice of aisle-split × zoning × scheduler × inbound policy over one frozen
inventory.  _apply_cell mutates the SAME sim_config.CONFIG object (never rebound) so the
change is seen everywhere; _build_cells names cells
k{k}[_l{loss}]_{zone}[_{inbound}][_{sched}].

The inbound suffix sits BEFORE the scheduler suffix on purpose: `run_whatif_labor._scheduler_of`
recovers the swept scheduler as the cell name's LAST underscore token, and appending a fifth
segment after it would silently relabel every row of that module's CSV.

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

    The FIFTH axis (the inbound policy) is now one field and one line in `_build_cells`
    rather than five positional unpacks to find and renumber.  The names here are the same
    ones the run-tree descriptor records, so it and the producer cannot drift apart.
    """
    name: str
    split: dict | None          # {'k', 'capacity_loss'} or None for no aisle split
    zoning: dict                # velocity_zoning spec; {'enabled': False} is off
    scheduler: str              # 'round_robin' | 'lpt'
    # The inbound axis, as `inbound_*` CONFIG['global'] overrides WITHOUT the prefix —
    # {'standing_yard': True, 'yard_policy': 'gain_gated', ...}.  None (the default) means
    # this cell does not touch inbound at all, which is what every pre-phase-2 spec gets and
    # why they stay byte-identical.  Defaulted so the four-argument `Cell(...)` constructions
    # already in the tests and in a resumed legacy run keep working.
    inbound: dict | None = None

    @property
    def is_reference(self) -> bool:
        """The natural baseline: no split, no zoning, the legacy scheduler, inbound untouched.

        This predicate was written out twice, verbatim, in `scenario.py` and
        `run_simulation.py` — and those two MUST agree, because one picks the cell the
        driver treats as the baseline and the other writes the reference name into
        the run-tree descriptor for every downstream what-if to diff against.  Two copies of a
        predicate that must agree is a bug with a delay on it.

        A cell carrying an inbound record is never the NATURAL baseline: an inbound matrix has
        no order-blind default cell to fall into, so the spec must DECLARE its reference (and
        `_build_cells` refuses one that does not).  Without this clause every cell of a
        ten-policy matrix would answer True and `reference_cell` would silently take the first.
        """
        return (self.split is None and not self.zoning.get('enabled')
                and self.scheduler == 'round_robin' and self.inbound is None)

    def overrides(self) -> dict:
        """What this cell CHANGES, as a plain record.

        The same four values `_apply_cell` writes, named rather than positional, so a
        caller can see a cell's effect without applying it — which is what a resolution
        pass needs, and what makes the effect diffable between two cells.

        `scheduler` is a pick-config field (like `one_way`), so it lands on every one of a
        channel's pick-config dicts; `aisle_split` and `velocity_zoning` are channel-level.
        `inbound` is the odd one out and stays named as itself: the yard and dock policies are
        whole-RUN settings on `CONFIG['global']`, which is exactly why they had to become a
        cell axis instead of six separate runs.
        """
        return {'aisle_split': self.split,
                'velocity_zoning': dict(self.zoning),
                'scheduler': self.scheduler,
                'inbound': None if self.inbound is None else dict(self.inbound)}


def reference_cell(cells, fallback: str | None = None) -> str:
    """The name of the reference cell, or `fallback`, or the first cell."""
    return next((c.name for c in cells if c.is_reference), fallback or cells[0].name)


def _inbound_axis(spec):
    """The spec's inbound axis as [(name_suffix, overrides|None)], validated.

    Defaults to the single inert entry `[('', None)]`, which is what every spec written before
    the inbound axis existed resolves to: no suffix, no CONFIG write, byte-identical names and
    behaviour.  A real axis is a list of `(suffix, {inbound_key: value})` pairs — keys are
    `CONFIG['global']` `inbound_*` names with the prefix dropped.

    Four refusals, each closing a failure with no error message:

    * **An unknown key.**  A misspelt name creates a new `CONFIG['global']` entry that
      `inbound_spec()` never reads, so the cell runs the DEFAULT policy under the swept
      policy's name.  Checked on every axis, inert or not.
    * **A missing suffix on a real axis.**  `_build_cells` dedupes by name, so inbound-on and
      inbound-off collapse into ONE cell — the matrix comes out smaller than it was asked for
      and nothing says so.
    * **Duplicate suffixes.**  The same collapse by another route.
    * **A key one entry sets and another does not.**  `_apply_cell` mutates a process-wide
      CONFIG that is never reset between cells, so a key written by cell 3 and omitted by cell 4
      leaves cell 4 running cell 3's policy under its own name.  Requiring every entry to cover
      the union makes the carryover unreachable rather than merely unlikely.
    """
    axis = spec.get('inbound') or [('', None)]
    axis = [(str(n), (None if ov is None else dict(ov))) for n, ov in axis]
    # A misspelt key would create a NEW `CONFIG['global']` entry that `inbound_spec()` never
    # reads: the cell would run the default policy under the swept policy's name.
    unknown = sorted({k for _n, ov in axis for k in (ov or {})
                      if f'inbound_{k}' not in CONFIG['global']})
    if unknown:
        raise ValueError(f'inbound axis sets unknown key(s) {unknown}; a cell may only override '
                         f"CONFIG['global'] inbound_* settings, named here without the prefix")
    if len(axis) == 1:
        return axis
    names = [n for n, _ov in axis]
    # Empty-name FIRST: two unnamed entries are also duplicates, and the duplicate message would
    # report `['', '']` where the real fault is that neither was named at all.
    if any(not n for n in names):
        raise ValueError('every entry of a swept inbound axis needs a NAME suffix, or its cells '
                         'collapse into one directory (an unnamed inbound-off anchor is the '
                         'usual way in)')
    if len(set(names)) != len(names):
        raise ValueError(f'the inbound axis has duplicate name suffixes ({names}); cells dedupe '
                         f'by name, so two entries sharing one would collapse into a single cell')
    union = {k for _n, ov in axis for k in (ov or {})}
    for n, ov in axis:
        missing = sorted(union - set(ov or {}))
        if missing:
            raise ValueError(
                f'inbound axis entry {n!r} does not set {missing}, which another entry does. '
                f'CONFIG is mutated in place and never reset between cells, so this cell would '
                f'silently inherit the previous one\'s value for those keys — state every key '
                f'the axis touches in every entry, including the inbound-off anchor')
    return axis


def _build_cells(spec):
    """Combinatorial (scheduler × zoning × k × capacity_loss × inbound) cells from a WHATIF spec.
    k=1 = no split (loss collapses to 0).  Returns [(name, aisle_split|None, zoning_spec,
    scheduler, inbound|None), …]; the (k=1, off, round_robin, no inbound) cell is the natural
    reference.  ``schedulers`` defaults to ['round_robin'] (byte-identical, no name suffix); a
    multi-value list adds a `_rr`/`_lpt` suffix so the picker-scheduler becomes a sweep axis
    alongside aisle_split and zoning.  ``inbound`` defaults to one inert entry and behaves the
    same way — see `_inbound_axis` for its shape and the four collapses it refuses.

    A swept inbound axis must also DECLARE `reference`: `Cell.is_reference` answers False for
    every cell carrying an inbound record, so an undeclared reference would fall through to
    `cells[0]` — the same silent-arbitrary-baseline failure `run_channel_rollup._baseline_entry`
    was caught doing, one level up."""
    scheds = spec.get('schedulers', ['round_robin'])
    multi_sched = len(scheds) > 1
    inbounds = _inbound_axis(spec)
    if len(inbounds) > 1 and not spec.get('reference'):
        raise ValueError('a spec that sweeps inbound must declare `reference`: no cell of an '
                         'inbound matrix is the NATURAL baseline, so the reference would '
                         'silently become whichever cell was built first')
    cells, seen = [], set()
    for sched in scheds:
        for zname, zspec in spec['zoning']:
            for k in spec['ks']:
                for loss in (spec['losses'] if k > 1 else [0.0]):
                    for iname, iover in inbounds:
                        split = None if k <= 1 else {'k': k, 'capacity_loss': loss}
                        name = (f'k{k}' + ('' if k <= 1 else f'_l{int(round(loss * 100))}')
                                + f'_{zname}')
                        # Inbound BEFORE the scheduler: `run_whatif_labor._scheduler_of` reads
                        # the scheduler off the last underscore token of the cell name.
                        if iname:
                            name += f'_{iname}'
                        if multi_sched:
                            name += f'_{_SCHED_SHORT.get(sched, sched)}'
                        if name in seen:
                            continue
                        seen.add(name)
                        cells.append(Cell(name, split, dict(zspec), sched,
                                          None if iover is None else dict(iover)))
    return cells


def _apply_cell(aisle_split, zoning, scheduler='round_robin', inbound=None) -> None:
    """Mutate CONFIG for one cell: same aisle_split + velocity_zoning + picker scheduler on every
    channel, plus this cell's inbound policy on `CONFIG['global']`.  The scheduler is a
    pick-config field (like one_way), so set it on each channel's pick-config dicts.

    Writes into CONFIG and ONLY into CONFIG.  Those pick-config dicts used to be the very
    objects `PICK_CONFIG_BY_KEY[…].cfg` holds, so this loop permanently edited the registry:
    `scheduler` is absent from every config module as written, and after one cell ran the
    frozen spec carried whichever value that cell chose, for the life of the process.
    `sim_config` now copies the dicts when it assembles CONFIG, which is where that boundary
    lives — see the comment beside STORE_CONFIGS.  Do not reintroduce a path that hands the
    registry's own dict to a mutator.

    `inbound` is global rather than per-channel because the yard and the dock are one site's,
    not one channel's; `inbound_spec()` reads `CONFIG['global']` at CALL time inside
    `_prepare_channel_run`, so a value written here reaches every spawned worker's payload.
    None writes NOTHING — the inert default that keeps a pre-inbound spec byte-identical.

    See `Cell.overrides()` for the same four values as a record, when you want to inspect a
    cell's effect rather than apply it."""
    for ch in CONFIG['channels']:
        CONFIG['channels'][ch]['sizing']['aisle_split'] = aisle_split
        CONFIG['channels'][ch]['velocity_zoning'] = dict(zoning)
        for cfg in CONFIG['channels'][ch]['configs']:
            cfg['scheduler'] = scheduler
    for key, val in (inbound or {}).items():
        CONFIG['global'][f'inbound_{key}'] = val


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
