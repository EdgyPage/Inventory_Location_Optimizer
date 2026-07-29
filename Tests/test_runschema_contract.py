"""test_runschema_contract.py

Locks the versioned run-tree contract (Optimization/runschema/):

  - the committed Optimization/schemas/run_tree.v<N>.json equals a fresh generation from
    runschema/v<N>.py (same "derived == regenerated" discipline as context/arch/verify_architecture);
  - context/artifacts.yml path patterns agree with the contract for every shared artifact id;
  - write_run_layout stamps schema_version, and resolver_for refuses a descriptor without one;
  - optional segments render correctly (a store-only run has NO <channel> level);
  - the POSITIONAL level walk survives the `<pair>/store/store/` collision, where the store config
    and the store channel share a name — the trap that makes name-based inference wrong.

Positive controls (mirroring Tests/test_architecture_sync.py) feed synthetic input to diff_shape
and validate so the checks cannot pass vacuously: an added level, a moved artifact, an undeclared
path, and a required artifact that only one canary produced must each be REPORTED.

Filesystem-only — no simulation.  The canary runs themselves are exercised by
`python -m Optimization.runschema.preflight`, which is far too slow for the ordinary suite.

Run:  python -m pytest Tests/test_runschema_contract.py -q
"""
from __future__ import annotations

import copy
import json
import os

import pytest

from Optimization import runschema
from Optimization.runschema import contract, preflight, v1
from Optimization.sim_manifest import write_run_layout, read_run_layout

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── the contract JSON is derived, not hand-maintained ───────────────────────────

def test_committed_contract_matches_module():
    ver = runschema.RUN_TREE_VERSION
    committed = contract.load(ver)
    assert committed is not None, f'run_tree.v{ver}.json is missing'
    fresh = contract.build(ver)
    assert committed['tree_fingerprint'] == fresh['tree_fingerprint'], (
        'run_tree.v%d.json is stale vs runschema/v%d.py:\n  %s' %
        (ver, ver, '\n  '.join(contract.diff_shape(committed, fresh))))


def test_contract_declares_every_axis_and_level():
    doc = contract.build()
    assert doc['axes'] == list(v1.AXES)
    names = [lv['name'] for lv in doc['levels']]
    assert names == ['cell', 'pair', 'config', 'channel']
    # channel MUST stay optional — a store-only run has no channel dir.  This is the invariant the
    # `len(rel) < 4` scanners violated.
    assert [lv['name'] for lv in doc['levels'] if lv['optional']] == ['channel']


def test_every_optional_artifact_states_its_condition():
    """An optional artifact with no `condition` is undocumented ambiguity — the exact gap that let
    store-only runs be silently dropped."""
    doc = contract.build()
    missing = [k for k, v in doc['artifacts'].items()
               if v.get('optional') and not v.get('condition')]
    assert not missing, f'optional artifacts missing a `condition`: {missing}'


# ── artifacts.yml agrees with the contract ──────────────────────────────────────

def test_artifacts_yml_patterns_match_contract():
    yaml = pytest.importorskip('yaml', reason='context verification needs pyyaml')
    with open(os.path.join(_ROOT, 'context', 'artifacts.yml'), encoding='utf-8') as f:
        arts = yaml.safe_load(f)['artifacts']
    doc = contract.build()
    mismatches = []
    for key, spec in doc['artifacts'].items():
        if key not in arts:
            continue                       # contract may cover artifacts the catalog doesn't list
        want = preflight._yaml_pattern(spec['path'])
        got = arts[key].get('path_pattern')
        if got != want:
            mismatches.append(f'{key}: artifacts.yml {got!r} != contract {want!r}')
    assert not mismatches, '\n'.join(mismatches)


def test_shared_artifact_ids_are_not_empty():
    """Non-vacuity: the cross-check above is meaningless if the id sets barely overlap."""
    yaml = pytest.importorskip('yaml', reason='context verification needs pyyaml')
    with open(os.path.join(_ROOT, 'context', 'artifacts.yml'), encoding='utf-8') as f:
        arts = yaml.safe_load(f)['artifacts']
    shared = set(arts) & set(contract.build()['artifacts'])
    assert len(shared) >= 10, sorted(shared)


# ── descriptor stamping + resolver selection ────────────────────────────────────

def _write_layout(base, **over):
    kw = dict(spec='_canary_single', reference='k1_off',
              cells=[('k1_off', None, {'enabled': False}, 'round_robin')],
              pairs=[('pairA', 'i.db', 'a.db')], store_cfgs=[{'name': 'store'}],
              ff_cfgs=[], channels=['store'], arms=None, created='2026-07-28T10:00:00')
    kw.update(over)
    write_run_layout(str(base), **kw)


def test_write_run_layout_stamps_schema_version(tmp_path):
    base = tmp_path / 'comparison_x'
    base.mkdir()
    _write_layout(base)
    lay = read_run_layout(str(base))
    assert lay['schema_version'] == runschema.RUN_TREE_VERSION
    # the template is FULL (from the run root) — the cell level is not implied
    assert lay['tree_template'].startswith('<cell>/')


def test_resolver_for_selects_the_declared_version(tmp_path):
    base = tmp_path / 'comparison_y'
    base.mkdir()
    _write_layout(base)
    rt = runschema.resolver_for(str(base))
    assert rt.version == runschema.RUN_TREE_VERSION
    assert isinstance(rt, v1.RunTreeV1)


def test_resolver_for_rejects_pre_v1_and_unknown(tmp_path):
    empty = tmp_path / 'legacy'
    empty.mkdir()
    with pytest.raises(runschema.UnsupportedRunTree):
        runschema.resolver_for(str(empty))          # no descriptor at all

    base = tmp_path / 'comparison_z'
    base.mkdir()
    _write_layout(base)
    path = base / 'run_layout.json'
    doc = json.loads(path.read_text())
    doc.pop('schema_version')
    path.write_text(json.dumps(doc))
    with pytest.raises(runschema.UnsupportedRunTree):
        runschema.resolver_for(str(base))           # pre-v1 descriptor

    doc['schema_version'] = 9999
    path.write_text(json.dumps(doc))
    with pytest.raises(runschema.UnsupportedRunTree):
        runschema.resolver_for(str(base))           # from the future


# ── optional-segment rendering ──────────────────────────────────────────────────

def test_render_drops_the_optional_channel_segment():
    tmpl = v1.ARTIFACTS['sim_db']['path']
    mixed = v1.render(tmpl, cell='k1_off', pair='p', config='store', channel='store',
                      strategy='uni_fifo_norsl')
    store_only = v1.render(tmpl, cell='k1_off', pair='p', config='store', channel=None,
                           strategy='uni_fifo_norsl')
    assert mixed == 'k1_off/p/store/store/sim_uni_fifo_norsl.db'
    assert store_only == 'k1_off/p/store/sim_uni_fifo_norsl.db'


def test_render_raises_on_a_missing_required_part():
    with pytest.raises(KeyError):
        v1.render(v1.ARTIFACTS['sim_db']['path'], cell='k1_off', pair='p')


# ── the positional walk survives the config/channel name collision ──────────────

_AXES = {'cell': {'k1_off'}, 'pair': {'pairA'}, 'config': {'store', 'ful_calibrated'},
         'channel': {'store', 'fulfillment'}}


@pytest.mark.parametrize('segments,expected', [
    # config 'store' AND channel 'store' — positional consumption keeps them apart
    (['k1_off', 'pairA', 'store', 'store', 'sim_uni_fifo_norsl.db'],
     '{cell}/{pair}/{config}/{channel}/sim_{strategy}.db'),
    (['k1_off', 'pairA', 'ful_calibrated', 'fulfillment', 'sim_opt_fifo_norsl.db'],
     '{cell}/{pair}/{config}/{channel}/sim_{strategy}.db'),
    # store-only: no channel level at all
    (['k1_off', 'pairA', 'store', 'sim_uni_fifo_norsl.db'],
     '{cell}/{pair}/{config}/sim_{strategy}.db'),
    (['k1_off', 'pairA', 'store', 'series.json'], '{cell}/{pair}/{config}/series.json'),
    # keyframes + checkpoints keep their strategy placeholder
    (['k1_off', 'pairA', 'store', 'sim_uni_fifo_norsl.keyframes.db'],
     '{cell}/{pair}/{config}/sim_{strategy}.keyframes.db'),
    # reserved subtrees
    (['_frozen', 'pairA', 'planned_inventory.db'], '_frozen/{pair}/planned_inventory.db'),
    (['k1_off', '_aggregate', 'store', 'fulfillment', 'stats', 'x.png'],
     '{cell}/_aggregate/{config}/{channel}/stats/*.png'),
    (['run_layout.json'], 'run_layout.json'),
])
def test_generalize_is_positional(segments, expected):
    assert preflight._generalize_file(list(segments), _AXES) == expected


# ── positive controls: the checks have teeth ────────────────────────────────────

def test_diff_shape_detects_an_added_level():
    a = contract.build()
    b = copy.deepcopy(a)
    b['levels'].append({'name': 'regime', 'optional': False})
    msgs = contract.diff_shape(a, b)
    assert any('level ADDED: regime' in m for m in msgs), msgs
    assert contract.tree_fingerprint(a) != contract.tree_fingerprint(b)


def test_diff_shape_detects_a_moved_artifact():
    a = contract.build()
    b = copy.deepcopy(a)
    b['artifacts']['sim_db']['path'] = '{cell}/{pair}/{config}/{channel?}/runs/sim_{strategy}.db'
    msgs = contract.diff_shape(a, b)
    assert any(m.startswith('artifact sim_db.path') for m in msgs), msgs


def test_diff_shape_ignores_prose_edits():
    """Editing a `note`/`condition` must NOT force a version bump — only shape does."""
    a = contract.build()
    b = copy.deepcopy(a)
    b['artifacts']['sim_db']['note'] = 'reworded documentation'
    b['levels'][0]['note'] = 'reworded too'
    assert contract.diff_shape(a, b) == []
    assert contract.tree_fingerprint(a) == contract.tree_fingerprint(b)


def _obs(templates):
    return {'templates': {t: 1 for t in templates}, 'levels_seen': [], 'layout': {}, 'base': ''}


def _canonical_observations():
    """The templates a healthy canary pair produces, derived FROM the contract itself."""
    doc = contract.build()
    a, b = [], []
    for spec in doc['artifacts'].values():
        p = spec['path']
        if '*' in p or '{group_key}' in p or '{per_strategy' in p:
            continue
        a.append(p.replace('/{channel?}', '/{channel}'))
        b.append(p.replace('/{channel?}', ''))
    return doc, _obs(a), _obs(b)


def test_validate_passes_on_the_declared_shape():
    doc, oa, ob = _canonical_observations()
    findings = preflight.validate(doc, oa, ob)
    assert not findings['undeclared'], findings['undeclared']


def test_validate_flags_an_undeclared_path():
    doc, oa, ob = _canonical_observations()
    oa['templates']['{cell}/{pair}/{config}/{channel}/surprise_export.parquet'] = 1
    findings = preflight.validate(doc, oa, ob)
    assert '{cell}/{pair}/{config}/{channel}/surprise_export.parquet' in findings['undeclared']
    assert not findings['ok']


def test_validate_flags_a_required_artifact_missing_from_one_canary():
    doc, oa, ob = _canonical_observations()
    required = 'run_layout.json'
    ob['templates'].pop(required, None)              # produced by A only now
    findings = preflight.validate(doc, oa, ob)
    assert any(m.startswith('run_layout:') for m in findings['optionality']), findings
    assert not findings['ok']


def test_validate_accepts_an_optional_artifact_in_one_canary_only():
    """`_frozen/` legitimately exists only on the multi-cell canary — that must NOT be a finding."""
    doc, oa, ob = _canonical_observations()
    frozen = doc['artifacts']['frozen_inventory_db']['path']
    ob['templates'].pop(frozen, None)
    findings = preflight.validate(doc, oa, ob)
    assert not any('frozen_inventory_db' in m for m in findings['optionality']), findings


# ── source fingerprint ──────────────────────────────────────────────────────────

def test_source_fingerprint_changes_when_a_shape_source_changes(tmp_path):
    """The cheap detector must actually react — otherwise the whole preflight never triggers."""
    import shutil
    fake = tmp_path / 'repo'
    for rel in contract.SHAPE_SOURCES:
        src = os.path.join(_ROOT, rel.replace('/', os.sep))
        dst = fake / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if os.path.exists(src):
            shutil.copyfile(src, dst)
    before = contract.source_fingerprint(str(fake))
    target = fake / contract.SHAPE_SOURCES[0]
    target.write_text(target.read_text(encoding='utf-8') + '\n# structural change\n',
                      encoding='utf-8')
    assert contract.source_fingerprint(str(fake)) != before


def test_source_fingerprint_ignores_line_ending_churn(tmp_path):
    """CRLF<->LF rewrites (a Windows checkout / stash-pop / autocrlf flip) must NOT read as a
    structural change — otherwise every such round-trip costs a needless canary pair."""
    import shutil
    fake = tmp_path / 'repo'
    for rel in contract.SHAPE_SOURCES:
        src = os.path.join(_ROOT, rel.replace('/', os.sep))
        dst = fake / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if os.path.exists(src):
            shutil.copyfile(src, dst)
    before = contract.source_fingerprint(str(fake))
    for rel in contract.SHAPE_SOURCES:                      # flip every file to CRLF
        p = fake / rel
        if p.exists():
            p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    assert contract.source_fingerprint(str(fake)) == before


def test_source_fingerprint_detects_a_deleted_shape_source(tmp_path):
    """A DELETED source must not silently hash the same as a present one."""
    import shutil
    fake = tmp_path / 'repo'
    for rel in contract.SHAPE_SOURCES:
        src = os.path.join(_ROOT, rel.replace('/', os.sep))
        dst = fake / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if os.path.exists(src):
            shutil.copyfile(src, dst)
    before = contract.source_fingerprint(str(fake))
    os.remove(fake / contract.SHAPE_SOURCES[0])
    assert contract.source_fingerprint(str(fake)) != before
