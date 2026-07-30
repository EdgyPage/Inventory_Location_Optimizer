"""test_runschema_contract.py

Locks the CONTENT-ADDRESSED run-tree contract (Optimization/runschema/):

  - a schema's id is the sha256 of its own declared shape: deterministic, self-excluding, and
    invariant under prose edits but sensitive to every field a path resolver depends on;
  - every stored document re-hashes to its own id AND to its filename — the property that makes the
    scheme verifiable with no registry to maintain;
  - INDEX.json's head + parent chain resolve (hashes have no natural order, so the chain is what
    restores it);
  - compatibility is negotiated by FEATURE, not by comparing numbers;
  - context/artifacts.yml path patterns agree with the contract for every shared artifact id;
  - write_run_layout stamps schema_id, and resolver_for refuses a descriptor without one;
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
from Optimization.runschema import contract, preflight, resolver
from Optimization.runschema import schema as decl
from Optimization.runschema.sim_manifest import write_run_layout, read_run_layout

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── the contract JSON is derived, not hand-maintained ───────────────────────────

def test_head_matches_the_declaration():
    """The stored head must BE the hash of schema.py — that is the whole guarantee."""
    fresh = contract.build()
    head = contract.head()
    assert head is not None, 'schema store is empty; run: contract --write'
    committed = contract.load(head)
    assert committed is not None, f'no document stored for head {contract.short_id(head)}'
    assert head == fresh['schema_id'], (
        'schema store is stale vs runschema/schema.py:\n  '
        + '\n  '.join(contract.diff_shape(committed, fresh)))


def test_schema_id_is_deterministic():
    """Same declaration, same id — in-process and across a fresh interpreter."""
    import subprocess
    import sys as _sys
    a, b = contract.build()['schema_id'], contract.build()['schema_id']
    assert a == b
    r = subprocess.run(
        [_sys.executable, '-c',
         'from Optimization.runschema import contract; print(contract.build()["schema_id"])'],
        cwd=_ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == a, 'schema id differs across processes — hashing is not canonical'


def test_schema_id_excludes_itself():
    """An identity cannot be an input to its own hash."""
    assert 'schema_id' not in contract._shape_only(contract.build())


def test_store_is_self_consistent():
    """Every stored document re-hashes to its own id AND to its filename, and the INDEX chain
    resolves.  This is what replaces a registry: the store verifies itself."""
    assert contract.verify_store() == []


def test_stored_document_filename_is_its_short_id():
    docs = contract.load_all()
    assert docs, 'no schema documents stored'
    for sid in docs:
        assert os.path.basename(contract.contract_path(sid)) == f'{contract.short_id(sid)}.json'
        assert os.path.exists(contract.contract_path(sid))


def test_contract_declares_every_axis_and_level():
    doc = contract.build()
    assert doc['axes'] == list(decl.AXES)
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
        if not spec.get('path'):
            continue                       # alias artifact: no path of its own
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


def test_write_run_layout_stamps_the_schema_id(tmp_path):
    base = tmp_path / 'comparison_x'
    base.mkdir()
    _write_layout(base)
    lay = read_run_layout(str(base))
    assert lay['schema_id'] == contract.head()
    assert lay['schema_id'].startswith('sha256:') and len(lay['schema_id']) == len('sha256:') + 64
    # the template is FULL (from the run root) — the cell level is not implied
    assert lay['tree_template'].startswith('<cell>/')


def test_resolver_for_binds_to_the_runs_own_schema(tmp_path):
    base = tmp_path / 'comparison_y'
    base.mkdir()
    _write_layout(base)
    rt = runschema.resolver_for(str(base))
    assert isinstance(rt, resolver.RunTree)
    assert rt.schema_id == contract.head()
    assert rt.schema_short == contract.short_id(rt.schema_id)


def test_resolver_for_rejects_missing_and_unknown_schema(tmp_path):
    empty = tmp_path / 'legacy'
    empty.mkdir()
    with pytest.raises(runschema.UnsupportedRunTree):
        runschema.resolver_for(str(empty))          # no descriptor at all

    base = tmp_path / 'comparison_z'
    base.mkdir()
    _write_layout(base)
    path = base / 'run_layout.json'
    doc = json.loads(path.read_text())
    doc.pop('schema_id')
    path.write_text(json.dumps(doc))
    with pytest.raises(runschema.UnsupportedRunTree):
        runschema.resolver_for(str(base))           # descriptor without an id

    # A well-formed id this checkout has no document for — the content-addressed analogue of
    # "from the future".  Must raise, not ValueError on a coercion.
    doc['schema_id'] = 'sha256:' + 'de1e7e'.ljust(64, '0')
    path.write_text(json.dumps(doc))
    with pytest.raises(runschema.UnsupportedRunTree):
        runschema.resolver_for(str(base))


def test_resolver_refuses_a_contract_needing_an_unknown_feature(tmp_path):
    """Feature negotiation replaces version comparison — and must NAME what is missing."""
    base = tmp_path / 'comparison_f'
    base.mkdir()
    _write_layout(base)
    doc = copy.deepcopy(contract.build())
    doc['features'] = list(doc['features']) + ['time-travel']
    with pytest.raises(runschema.UnsupportedRunTree) as ei:
        runschema._resolver_for_contract(str(base), doc, None)
    assert 'time-travel' in str(ei.value)


def test_every_declared_feature_is_supported():
    """The committed declaration must be readable by this build."""
    missing = set(decl.FEATURES) - set(resolver.SUPPORTED_FEATURES)
    assert not missing, f'schema.py declares unsupported feature(s): {sorted(missing)}'


# ── optional-segment rendering ──────────────────────────────────────────────────

def test_render_drops_the_optional_channel_segment():
    tmpl = decl.ARTIFACTS['sim_db']['path']
    mixed = resolver.render(tmpl, cell='k1_off', pair='p', config='store', channel='store',
                      strategy='uni_fifo_norsl')
    store_only = resolver.render(tmpl, cell='k1_off', pair='p', config='store', channel=None,
                           strategy='uni_fifo_norsl')
    assert mixed == 'k1_off/p/store/store/sim_uni_fifo_norsl.db'
    assert store_only == 'k1_off/p/store/sim_uni_fifo_norsl.db'


def test_render_raises_on_a_missing_required_part():
    with pytest.raises(KeyError):
        resolver.render(decl.ARTIFACTS['sim_db']['path'], cell='k1_off', pair='p')


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
    assert contract.schema_id(a) != contract.schema_id(b)


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
    assert contract.schema_id(a) == contract.schema_id(b)


def _obs(templates):
    return {'templates': {t: 1 for t in templates}, 'levels_seen': [], 'layout': {}, 'base': ''}


def _canonical_observations():
    """The templates a healthy canary pair produces, derived FROM the contract itself."""
    doc = contract.build()
    a, b = [], []
    for spec in doc['artifacts'].values():
        p = spec.get('path')
        if not p or spec.get('format') == 'dir' or '*' in p or '{initial_group}' in p:
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


# ── INDEX: the provenance chain that restores the ordering hashes lack ──────────

def test_index_head_and_chain_resolve():
    index = contract.read_index()
    docs = contract.load_all()
    assert index['head'] in docs, 'head does not resolve to a stored document'
    roots = [sid for sid, m in index['schemas'].items() if m.get('parent') is None]
    assert len(roots) == 1, f'expected exactly one root schema, got {roots}'
    for sid, meta in index['schemas'].items():
        assert meta['short'] == contract.short_id(sid)
        if meta['parent'] is not None:
            assert meta['parent'] in index['schemas'], 'dangling parent link'


def test_adopt_is_idempotent_and_chains(tmp_path, monkeypatch):
    """Adopting the head twice must not fork the chain; adopting a CHANGED schema must record the
    previous head as its parent — that is what gives hashes an order."""
    store = tmp_path / 'run_tree'
    store.mkdir()
    monkeypatch.setattr(contract, '_TREE_DIR', str(store))
    monkeypatch.setattr(contract, '_INDEX', str(store / 'INDEX.json'))

    first = contract.build()
    contract.adopt(first, source_fp='sha256:aaa')
    contract.adopt(first, source_fp='sha256:bbb')           # idempotent
    idx = contract.read_index()
    assert list(idx['schemas']) == [first['schema_id']]
    assert idx['source_fingerprint'] == 'sha256:bbb'        # only the mutable field moved

    second = copy.deepcopy(first)
    second['artifacts']['sim_db']['path'] = '{cell}/{pair}/{config}/{channel?}/runs/sim_{strategy}.db'
    second['schema_id'] = contract.schema_id(second)
    contract.adopt(second, source_fp='sha256:ccc')
    idx = contract.read_index()
    assert idx['head'] == second['schema_id']
    assert idx['schemas'][second['schema_id']]['parent'] == first['schema_id']
    assert any('sim_db.path' in c for c in idx['schemas'][second['schema_id']]['changes'])


def test_write_refuses_a_document_whose_id_is_not_its_hash(tmp_path, monkeypatch):
    """The store's core invariant: a filename is a claim about content, and must be true."""
    store = tmp_path / 'run_tree'
    store.mkdir()
    monkeypatch.setattr(contract, '_TREE_DIR', str(store))
    doc = contract.build()
    doc['artifacts']['sim_db']['path'] = 'tampered/{strategy}.db'   # id no longer matches content
    with pytest.raises(AssertionError):
        contract.write(doc)


# ── the resolver is generic: behaviour comes from the contract, not from Python ──

def test_resolves_via_prefers_the_first_existing_candidate(tmp_path):
    """The frozen-vs-cell-local planned inventory fallback is DATA now, not an if-statement."""
    base = tmp_path / 'comparison_rv'
    (base / 'k1_off' / 'pairA').mkdir(parents=True)
    _write_layout(base)
    rt = runschema.resolver_for(str(base))

    local = base / 'k1_off' / 'pairA' / 'planned_inventory.db'
    local.write_text('')
    assert rt.planned_inventory_db('k1_off', 'pairA') == str(local)   # single-cell shape

    frozen = base / '_frozen' / 'pairA' / 'planned_inventory.db'
    frozen.parent.mkdir(parents=True)
    frozen.write_text('')
    assert rt.planned_inventory_db('k1_off', 'pairA') == str(frozen)  # frozen wins when present


def test_strategy_of_inverts_the_declared_template(tmp_path):
    """strategy_of is derived from the sim_db template, so renaming the pattern can't strand it."""
    base = tmp_path / 'comparison_inv'
    leaf = base / 'k1_off' / 'pairA' / 'store' / 'store'
    leaf.mkdir(parents=True)
    _write_layout(base)
    rt = runschema.resolver_for(str(base))
    assert rt.strategy_of(str(leaf / 'sim_opt_cluster_map_rank_norsl.db')) == 'opt_cluster_map_rank_norsl'
    store_only = base / 'k1_off' / 'pairA' / 'store'          # no channel level
    assert rt.strategy_of(str(store_only / 'sim_uni_fifo_norsl.db')) == 'uni_fifo_norsl'


def test_by_group_replaces_the_hardcoded_whatif_list(tmp_path):
    base = tmp_path / 'comparison_grp'
    base.mkdir()
    _write_layout(base)
    rt = runschema.resolver_for(str(base))
    assert set(rt.by_group('whatif')) == {
        'whatif_delta_csv', 'whatif_delta_json', 'whatif_delta_png',
        'whatif_labor_csv', 'whatif_labor_json', 'whatif_labor_pngs'}
    assert rt.by_group('nonexistent') == []


# ── positive controls for the new hashed fields ─────────────────────────────────

def test_id_changes_on_group_and_resolves_via_edits():
    """`group` and `resolves_via` steer real behaviour, so they MUST be hashed."""
    a = contract.build()
    for mutate in (lambda d: d['artifacts']['sim_db'].__setitem__('group', 'x'),
                   lambda d: d['artifacts']['planned_inventory'].__setitem__('resolves_via', [])):
        b = copy.deepcopy(a)
        mutate(b)
        assert contract.schema_id(a) != contract.schema_id(b)
        assert contract.diff_shape(a, b), 'a hashed change must also be EXPLAINED'


def test_axis_reordering_is_reported_not_just_hashed():
    """Regression: axes hash as an ordered list but were diffed as a set, so a reorder changed the
    id with an EMPTY explanation — under content addressing the diff is the only explanation."""
    a = contract.build()
    b = copy.deepcopy(a)
    b['axes'] = list(reversed(a['axes']))
    assert contract.schema_id(a) != contract.schema_id(b)
    assert any(m.startswith('axis ORDER') for m in contract.diff_shape(a, b))


def test_feature_edits_change_the_id():
    a = contract.build()
    b = copy.deepcopy(a)
    b['features'] = list(a['features']) + ['new-vocabulary']
    assert contract.schema_id(a) != contract.schema_id(b)
    assert any(m.startswith('features') for m in contract.diff_shape(a, b))
