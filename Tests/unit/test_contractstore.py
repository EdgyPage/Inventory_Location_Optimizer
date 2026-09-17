"""test_contractstore.py — one store, one set of invariants, both adapters.

`runschema/contract.py` and `Schema/profile_tree.py` each kept a content-addressed document store:
immutable JSON named by the short form of its own sha256, plus a mutable `INDEX.json` carrying
`head`, `source_fingerprint` and a provenance chain. Eight operations, two copies, and **the only
one anything compared was `source_fingerprint`** -- nothing pinned `adopt`, `write`, `load_all` or
`verify_store`.

So the copy drifted toward weaker invariants, exactly where nothing was looking:
`contract.verify_store()` checked four properties and `profile_tree.verify_store()` checked two.
It did not check filenames, and it did not check the parent chain **although its own `adopt`
writes a `parent` field**.

That is not hypothetical. The first run of the four-check version against the profiles tree found
two documents DELETED from the store with their INDEX entries left behind -- see
`test_the_real_stores_are_sound`, and the ticket record for what deleted them.

This file is parameterised over a throwaway store and over BOTH real adapters, because those are
different questions: the first says the operations are correct, the second says the two modules
are really wired to them.

Run:  python -m pytest Tests/unit/test_contractstore.py -q
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from Optimization.runschema import contract as _rt
from Schema import profile_tree as _pt
from Schema.contractstore import ContractStore


def _doc(payload: dict) -> dict:
    """A document whose shape IS `payload` -- the smallest thing the store can hold."""
    return {'schema_id': None, 'prose': 'not hashed', 'shape': payload}


def _store(tmp_path, **over) -> ContractStore:
    kw = dict(tree_dir=str(tmp_path / 'store'), shape_of=lambda d: d['shape'],
              sources=(), repo_root=str(tmp_path), what='test-tree',
              refresh_cmd='python -m nothing --write')
    kw.update(over)
    return ContractStore(**kw)


def _built(store, payload) -> dict:
    d = _doc(payload)
    d['schema_id'] = store.schema_id(d)
    return d


# ── identity ──────────────────────────────────────────────────────────────────────

def test_the_id_is_the_shape_and_nothing_else(tmp_path):
    """Prose must never mint an id -- if it did, every docstring edit would fork the store and
    documents would stop being comparable."""
    s = _store(tmp_path)
    a, b = _doc({'x': 1}), _doc({'x': 1})
    b['prose'] = 'completely different words'
    assert s.schema_id(a) == s.schema_id(b)
    assert s.schema_id(_doc({'x': 2})) != s.schema_id(a), 'a shape change did not move the id'


def test_the_id_is_insensitive_to_key_ORDER_but_not_to_content(tmp_path):
    s = _store(tmp_path)
    assert s.schema_id(_doc({'a': 1, 'b': 2})) == s.schema_id(_doc({'b': 2, 'a': 1}))
    assert s.schema_id(_doc({'a': 1, 'b': 2})) != s.schema_id(_doc({'a': 2, 'b': 1}))


def test_the_filename_is_the_short_id_because_a_colon_is_illegal_on_windows(tmp_path):
    s = _store(tmp_path)
    sid = 'sha256:' + 'ab' * 32
    assert s.short_id(sid) == 'ab' * 6
    assert os.path.basename(s.path_for(sid)) == f'{"ab" * 6}.json'
    assert ':' not in os.path.basename(s.path_for(sid))


# ── write refuses rather than corrupts ────────────────────────────────────────────

def test_writing_a_document_whose_id_is_not_its_own_hash_is_refused(tmp_path):
    """Content addressing that does not hold is worse than none: every later `load` would return
    a document under an id it does not have."""
    s = _store(tmp_path)
    d = _built(s, {'x': 1})
    d['shape'] = {'x': 999}                       # the id now lies
    with pytest.raises(AssertionError):
        s.write(d)


def test_a_short_id_collision_is_refused_not_overwritten(tmp_path):
    """Astronomically unlikely at 48 bits, and unrecoverable if it ever happened -- a DIFFERENT
    schema would silently occupy this filename."""
    s = _store(tmp_path, short_len=1)             # forced, so the branch is reachable
    first = _built(s, {'x': 1})
    s.write(first)
    victim = None
    for n in range(2, 400):
        cand = _built(s, {'x': n})
        if s.short_id(cand['schema_id']) == s.short_id(first['schema_id']):
            victim = cand
            break
    assert victim is not None, 'no collision found; this test proved nothing'
    with pytest.raises(RuntimeError, match='collision'):
        s.write(victim)


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    s = _store(tmp_path)
    s.write(_built(s, {'x': 1}))
    leftovers = [f for f in os.listdir(s.tree_dir) if '.tmp.' in f]
    assert not leftovers, f'temp files survived: {leftovers}'


# ── the index and the chain ───────────────────────────────────────────────────────

def test_an_absent_index_reads_as_a_skeleton_not_a_crash(tmp_path):
    s = _store(tmp_path)
    assert s.read_index() == {'head': None, 'source_fingerprint': None, 'schemas': {}}
    assert s.head() is None


def test_adopt_sets_the_head_and_chains_the_parent(tmp_path):
    """The parent link is what restores the ordering hashes inherently lack."""
    s = _store(tmp_path)
    a, b, c = (_built(s, {'x': n}) for n in (1, 2, 3))
    for d in (a, b, c):
        s.adopt(d, commit='deadbeef', created='2026-01-01T00:00:00')
    idx = s.read_index()
    assert idx['head'] == c['schema_id']
    assert idx['schemas'][c['schema_id']]['parent'] == b['schema_id']
    assert idx['schemas'][b['schema_id']]['parent'] == a['schema_id']
    assert idx['schemas'][a['schema_id']]['parent'] is None, 'the first adopt must be the root'


def test_re_adopting_the_head_is_idempotent_and_does_not_reparent_it(tmp_path):
    """A refresh is the common case -- a source's text moved but the shape did not -- and it must
    not make a document its own ancestor."""
    s = _store(tmp_path)
    a, b = (_built(s, {'x': n}) for n in (1, 2))
    s.adopt(a, commit='x', created='2026-01-01T00:00:00')
    s.adopt(b, commit='x', created='2026-01-01T00:00:00')
    before = json.dumps(s.read_index(), sort_keys=True)
    s.adopt(b, commit='DIFFERENT', created='2099-09-09T00:00:00')
    after = s.read_index()
    assert after['schemas'][b['schema_id']]['parent'] == a['schema_id']
    assert after['schemas'][b['schema_id']]['commit'] == 'x', 'a re-adopt rewrote provenance'
    assert json.dumps(after, sort_keys=True) == before
    assert not s.verify_store()


def test_adopt_records_changes_only_when_the_store_keeps_a_change_log(tmp_path):
    """`contract` has a `diff_shape` and records `changes`; the profiles tree has neither. The
    difference is INJECTED, so neither module carries a branch for the other's behaviour."""
    plain = _store(tmp_path / 'a')
    logged = _store(tmp_path / 'b', diff=lambda old, new: ['a change'])
    for s in (plain, logged):
        d = _built(s, {'x': 1})
        s.adopt(d, commit='x', created='2026-01-01T00:00:00')
    assert 'changes' not in plain.read_index()['schemas'][next(iter(plain.read_index()['schemas']))]
    first = next(iter(logged.read_index()['schemas'].values()))
    assert first['changes'] == ['initial schema'], 'the first adopt has no parent to diff against'

    d2 = _built(logged, {'x': 2})
    logged.adopt(d2, commit='x', created='2026-01-01T00:00:00')
    assert logged.read_index()['schemas'][d2['schema_id']]['changes'] == ['a change']


def test_the_source_fingerprint_is_recorded_only_when_handed_in(tmp_path):
    """`contract` refreshes it explicitly; `profile_tree` always passes its own. Making it
    implicit would refresh the trigger on an adopt that never checked the sources."""
    s = _store(tmp_path)
    d = _built(s, {'x': 1})
    s.adopt(d, commit='x', created='2026-01-01T00:00:00')
    assert s.read_index()['source_fingerprint'] is None
    s.adopt(d, source_fp='sha256:abc', commit='x', created='2026-01-01T00:00:00')
    assert s.read_index()['source_fingerprint'] == 'sha256:abc'


# ── verify_store: all FOUR properties ─────────────────────────────────────────────

def _sound(tmp_path):
    s = _store(tmp_path)
    for n in (1, 2):
        s.adopt(_built(s, {'x': n}), commit='x', created='2026-01-01T00:00:00')
    assert not s.verify_store()
    return s


def test_a_tampered_document_is_caught(tmp_path):
    s = _sound(tmp_path)
    p = s.path_for(s.head())
    doc = json.loads(open(p, encoding='utf-8').read())
    doc['shape'] = {'x': 'tampered'}
    open(p, 'w', encoding='utf-8').write(json.dumps(doc))
    assert any('does not hash to its stated schema_id' in x for x in s.verify_store())


def test_a_renamed_document_is_caught(tmp_path):
    """The check the profiles-tree copy did NOT have. A document under the wrong filename is
    unreachable by id while still being listed by `load_all`."""
    s = _sound(tmp_path)
    p = s.path_for(s.head())
    os.rename(p, os.path.join(s.tree_dir, 'wrongname.json'))
    assert any('filename != short(schema_id)' in x for x in s.verify_store())


def test_a_deleted_document_with_a_surviving_index_entry_is_caught(tmp_path):
    """THE ONE THAT FOUND A REAL DEFECT. Two profiles-tree documents had been deleted with their
    INDEX entries left behind; the two-check copy could not see it, and the provenance chain had
    been broken for weeks."""
    s = _sound(tmp_path)
    os.remove(s.path_for(s.head()))
    problems = s.verify_store()
    assert any('has no stored document' in x for x in problems)
    assert any('but no document is stored' in x for x in problems)


def test_a_broken_parent_link_is_caught(tmp_path):
    """The other check the copy lacked -- and it lacked it while its own `adopt` wrote parents."""
    s = _sound(tmp_path)
    idx = s.read_index()
    idx['schemas'][idx['head']]['parent'] = 'sha256:' + 'ff' * 32
    s.write_index(idx)
    assert any('is not in the INDEX' in x for x in s.verify_store())


def test_two_roots_are_caught(tmp_path):
    """Exactly one root, or the chain is not a chain."""
    s = _sound(tmp_path)
    idx = s.read_index()
    idx['schemas'][idx['head']]['parent'] = None
    s.write_index(idx)
    assert any('root schema(s); expected exactly 1' in x for x in s.verify_store())


# ── stale_reasons ─────────────────────────────────────────────────────────────────

def test_stale_reasons_names_an_empty_store_and_its_remedy(tmp_path):
    s = _store(tmp_path)
    reasons = s.stale_reasons(lambda: _built(s, {'x': 1}))
    assert len(reasons) == 1 and 'empty' in reasons[0]
    assert s.refresh_cmd in reasons[0], 'a nag that does not name its remedy is a nag'


def test_stale_reasons_sees_a_moved_declaration_and_a_moved_source(tmp_path):
    s = _store(tmp_path)
    s.adopt(_built(s, {'x': 1}), source_fp=s.source_fingerprint(),
            commit='x', created='2026-01-01T00:00:00')
    assert not s.stale_reasons(lambda: _built(s, {'x': 1}))
    moved = s.stale_reasons(lambda: _built(s, {'x': 2}))
    assert any('now hashes to' in r for r in moved)


def test_stale_reasons_reports_store_integrity_too(tmp_path):
    s = _sound(tmp_path)
    os.remove(s.path_for(s.head()))
    assert any('store integrity' in r
               for r in s.stale_reasons(lambda: _built(s, {'x': 2})))


# ── both real adapters ────────────────────────────────────────────────────────────

ADAPTERS = [('run-tree', _rt), ('profile-tree', _pt)]


@pytest.mark.parametrize('name,mod', ADAPTERS, ids=[n for n, _m in ADAPTERS])
def test_the_real_stores_are_sound(name, mod):
    """THE FOUR INVARIANTS, ON THE COMMITTED STORES. This is what found the two deleted
    profiles-tree documents; before ticket 13 that store was only ever checked for two."""
    assert mod.verify_store() == [], f'{name} store integrity: {mod.verify_store()}'


@pytest.mark.parametrize('name,mod', ADAPTERS, ids=[n for n, _m in ADAPTERS])
def test_the_real_head_is_what_the_declaration_hashes_to(name, mod):
    assert mod.build()['schema_id'] == mod.head(), (
        f'{name}: the committed head is not what the declaration currently hashes to')


@pytest.mark.parametrize('name,mod', ADAPTERS, ids=[n for n, _m in ADAPTERS])
def test_the_real_parent_chain_resolves_end_to_end(name, mod):
    """Walkable from head to root, every hop loadable. A store whose ancestors cannot be loaded
    cannot answer "what changed" -- and cannot validate an archived artifact stamped with one."""
    idx = mod.read_index()
    seen, sid = [], idx['head']
    while sid is not None:
        assert sid not in seen, f'{name}: the parent chain has a cycle at {mod.short_id(sid)}'
        assert mod.load(sid) is not None, (
            f'{name}: {mod.short_id(sid)} is in the chain but has no document')
        seen.append(sid)
        sid = idx['schemas'][sid].get('parent')
    assert len(seen) == len(idx['schemas']), (
        f'{name}: the chain reaches {len(seen)} of {len(idx["schemas"])} indexed schemas')


@pytest.mark.parametrize('name,mod', ADAPTERS, ids=[n for n, _m in ADAPTERS])
def test_the_adapter_really_delegates(name, mod):
    """NON-VACUITY for everything above: a module could keep its own copy and still pass, because
    the copy is what the tests would then be exercising."""
    assert isinstance(mod._STORE, ContractStore)
    assert mod.verify_store.__module__ == mod.__name__, 'expected a thin wrapper, not an alias'
    import inspect
    assert '_STORE.verify_store()' in inspect.getsource(mod.verify_store)
    assert '_STORE.' in inspect.getsource(mod.load_all)


def test_the_two_adapters_are_separate_stores():
    """Sharing the machinery must not share the CONTENTS -- the declarations stay apart, which is
    the half of the layering objection that was right."""
    assert _rt._STORE.tree_dir != _pt._STORE.tree_dir
    assert _rt.head() != _pt.head()
    assert _rt._STORE.source_dirs and not _pt._STORE.source_dirs, (
        'only the run-tree store has an auto-discovered input; that asymmetry is the whole '
        'reason their fingerprints differ (architecture-drift/05)')
