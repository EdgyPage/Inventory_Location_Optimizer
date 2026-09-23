"""test_door_scarcity.py — fewer doors is an understaffed dock, and its cells always have a twin.

The door-scarcity specs (`.scratch/inbound-throughput/` 10) make the dock SHORT by lowering the
door count while the derived crews stay where the catalogue puts them.  The door-team cap
(`Tests/unit/test_door_team_cap.py`) is what makes a door count bind at all; this file pins the
three things the scarcity specs add on top of it:

  1. EVERY SCARCE CELL HAS ITS FOUR-DOOR TWIN, identical but for `dock_doors`, so a door count
     is read as a difference inside one matrix and never as a level across two runs.
  2. THE LEVELS ARE THE ONES THE ARITHMETIC NAMES: at the campaign's door team and derived
     crew, three doors still seat every receiver and two do not.  A level set that drifted to
     "3 and 4" would run a whole campaign in which nothing is short.
  3. NO CREW DERIVATION READS THE DOOR COUNT.  If one did, a scarce cell would re-derive a
     smaller crew and the matrix would compare two staffing levels, not two dock sizes -- and
     the staffing pin, a label digest, would not notice.

Run:  python -m pytest Tests/unit/test_door_scarcity.py -q
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from Optimization.config.whatif_config import (PHASE2_DOCK_DOORS, PHASE2_DOOR_LEVELS,
                                               PHASE2_DOOR_PROBE_LEVELS, PHASE2_DOOR_TEAM,
                                               SPECS, door_scarcity_axis)

_REPO = pathlib.Path(__file__).resolve().parents[2]


# ── 1. the twin ──────────────────────────────────────────────────────────────────────────────

def test_every_scarce_cell_has_its_four_door_twin():
    axis = dict(door_scarcity_axis(('fifo', 'gforecast'), (2, 1)))
    scarce = [n for n in axis if '_d' in n]
    assert sorted(scarce) == ['fifo_d1', 'fifo_d2', 'gforecast_d1', 'gforecast_d2']
    for name in scarce:
        base, d = name.rsplit('_d', 1)
        twin = axis[base]
        assert twin['dock_doors'] == PHASE2_DOCK_DOORS
        assert axis[name]['dock_doors'] == int(d)
        diff = {k for k in twin.keys() | axis[name].keys()
                if twin.get(k) != axis[name].get(k)}
        assert diff == {'dock_doors'}, f'{name} differs from its twin in {sorted(diff)}'


def test_without_the_reference_only_scarce_cells_are_built():
    axis = door_scarcity_axis(('fifo',), (2,), reference_doors=False)
    assert [n for n, _ in axis] == ['fifo_d2']


def test_the_door_team_rides_every_scarce_cell():
    """Without the cap a door count means nothing: the uncapped dock unloads at the crew's
    full rate through ONE door (the reason the cap exists)."""
    for name, over in door_scarcity_axis(('fifo', 'ggated_h050'), PHASE2_DOOR_PROBE_LEVELS):
        assert over['door_team'] == PHASE2_DOOR_TEAM, name


# ── 2. the levels ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('crew', [22, 23])
def test_the_priced_levels_are_short_and_the_campaign_door_count_is_not(crew):
    """Seats = door team x doors.  At the campaign's crew, four and three doors seat everyone
    and every priced scarcity level seats fewer -- the levels are physics, not a guess."""
    for doors in (PHASE2_DOCK_DOORS, 3):
        assert PHASE2_DOOR_TEAM * doors >= crew, f'{doors} doors should seat {crew}'
    for doors in PHASE2_DOOR_LEVELS:
        assert PHASE2_DOOR_TEAM * doors < crew, f'{doors} doors should be SHORT of {crew}'


def test_the_priced_levels_are_measured_by_the_depth_probe_first():
    assert set(PHASE2_DOOR_LEVELS) <= set(PHASE2_DOOR_PROBE_LEVELS)
    assert all(0 < d < PHASE2_DOCK_DOORS for d in PHASE2_DOOR_PROBE_LEVELS)


def test_the_specs_build_their_cells():
    from Optimization.simdriver.cells import _build_cells
    want = {
        '_toy_doors': {'k1_off_fifo': 4, 'k1_off_fifo_d1': 1},
        '_probe_door_depth': {'k1_off_fifo': 4, 'k1_off_fifo_d3': 3,
                              'k1_off_fifo_d2': 2, 'k1_off_fifo_d1': 1},
    }
    for spec, cells in want.items():
        got = {c.name: c.inbound.get('dock_doors') for c in _build_cells(SPECS[spec])}
        assert got == cells, spec
    scarce = {c.name: c.inbound.get('dock_doors') for c in _build_cells(SPECS['door_scarcity'])}
    assert SPECS['door_scarcity']['reference'] in scarce
    for name, doors in scarce.items():
        if name.endswith('_d2'):
            assert scarce[name[:-3]] == PHASE2_DOCK_DOORS and doors == 2


# ── 3. no crew derivation reads a door ───────────────────────────────────────────────────────

def test_no_staffing_derivation_reads_the_door_count():
    """An AST walk, not a grep: prose and comments may say "doors" freely.  What must not
    appear in the staffing package is a string KEY that names the door count."""
    keys = {'doors', 'dock_doors', 'inbound_dock_doors'}
    hits = []
    for path in sorted((_REPO / 'Optimization' / 'simconfig').glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in keys:
                hits.append(f'{path.name}:{node.lineno} {node.value!r}')
            elif isinstance(node, ast.Attribute) and node.attr in keys:
                hits.append(f'{path.name}:{node.lineno} .{node.attr}')
    assert not hits, ('a crew derivation reads the door count, so a scarce cell would '
                      're-derive its crew: ' + ', '.join(hits))
