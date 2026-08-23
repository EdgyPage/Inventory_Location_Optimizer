"""catalog.rules — the rule catalogue, as the RUN executed it.

`Optimization/config/objectives.py` is the registry; this emits it into the run, joined
against what the run actually swept.  The join is the point: a catalogue printed straight
from the registry describes the code at read time, and a page rendering it beside results
would silently list rules the sweep never ran, or omit that a rule ran on one channel only.
Every entry therefore carries `ran`, the arms that carried it, and the channels it appeared
on — read from the run's own per-arm rows.

Map-family entries additionally carry the measured solver split.  The published site says
the Map target comes from "the full linear assignment problem"; the exact solver has a size
gate, and at production catalogue scale the large BinKey classes are far past it.  Whatever
the split was, it is now a field rather than an adjective.
"""
import json
import os

from Optimization.Performance_Evaluations.core.registry import evaluation
from Optimization.Performance_Evaluations.common import io
from Optimization.config.objectives import FAMILIES, as_dicts


def _observed(rows) -> dict:
    """{rule: {'arms': [...], 'channels': [...], 'map_lap_pct': float|None}} from the run."""
    out: dict = {}
    for r in rows:
        rule = r.get('assignment')
        if not rule:
            continue
        e = out.setdefault(rule, {'arms': set(), 'channels': set(), 'lap': []})
        e['arms'].add(r.get('arm'))
        e['channels'].add(r.get('channel'))
        v = r.get('map_lap_pct')
        if v is not None:
            e['lap'].append(float(v))
    return {k: {'arms': sorted(x for x in v['arms'] if x),
                'channels': sorted(x for x in v['channels'] if x),
                'map_lap_pct': (sum(v['lap']) / len(v['lap'])) if v['lap'] else None}
            for k, v in out.items()}


def catalog(rows) -> dict:
    seen = _observed(rows)
    entries = []
    for e in as_dicts():
        obs = seen.get(e['rule'])
        pct = obs['map_lap_pct'] if obs else None
        notes = e['notes']
        if pct is not None:
            # Fold the measurement into the PERSISTED note, not just the rendered page.
            # A reader diffing this file against the site should not find the old, weaker
            # description here and the correction only in a macro.
            notes += (f' Measured on this run: the exact solver reaches '
                      f'{pct * 100:.2f} % of assigned units and a near-optimal greedy '
                      f'pass covers the rest, because the large bin classes exceed its size '
                      f'limit. The result the run produced is the greedy one.')
        entries.append({
            **e,
            'notes': notes,
            'ran': obs is not None,
            'arms': obs['arms'] if obs else [],
            'channels': obs['channels'] if obs else [],
            # None means "not measured", not "no exact solves" — the columns are nullable
            # for exactly this distinction, so the site can say which it is.
            'map_lap_pct': obs['map_lap_pct'] if obs else None,
            # The split is a property of the CATALOGUE — the map is solved once per
            # inventory pair over the whole warehouse, and which classes clear the exact
            # solver's size gate depends on bin geometry, not on a channel's pick
            # constants.  Every channel therefore reports the same number, and saying so
            # stops a reader reading the repetition as independent confirmation.
            'map_lap_scope': ('once per inventory pair, over the whole catalogue — the '
                              'same value on every channel' if pct is not None else None),
        })
    return {
        'families': FAMILIES,
        'n_rules': len(entries),
        'n_ran': sum(1 for e in entries if e['ran']),
        'note': ('The objective is transcribed once, in Optimization/config/objectives.py, '
                 'and tied by test to the symbol each builder actually calls. `ran` and '
                 '`arms` come from this run, not from the registry.'),
        'rules': entries,
    }


@evaluation(key='catalog.rules', label='What each placement rule is, and whether it ran',
            scope='run', needs=('runtime',), out_subdir='')
def render(ctx, params):
    doc = catalog(ctx.runtime_rows())
    path = os.path.join(io.out_dir(ctx), 'rule_catalog.json')
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=2)
    ctx.log.info(f"  wrote the rule catalogue ({doc['n_ran']}/{doc['n_rules']} rules ran)")
