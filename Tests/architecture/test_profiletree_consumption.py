"""test_profiletree_consumption.py — the profiles-tree contract: ratchet, store, and honesty gates.

The profiles-tree contract (`Schema/profile_tree.py`) is the second contract to get the run-tree
treatment: a declaration hashed into a schema id, a committed content-addressed store, and a
descriptor (`profile_layout.json`) every generator stamps.  This file is the second RATCHET plus
the gates that keep the new layer honest:

  * hand-written profiles-tree path knowledge may only SHRINK — the forbidden tokens are DERIVED
    from `Schema.profile_tree.ARTIFACTS` at test time, every current occurrence is recorded in
    `_BASELINE`, and a new file mentioning a token fails immediately (the run-tree ratchet's
    skeleton, second instance);
  * the committed store at `Schema/schemas/profile_tree/` holds exactly the head document, the
    document hashes to its own filename, and `stale_reasons()` is empty against the working tree
    (if that last part goes red after a shape-source edit, the remedy is the one the message
    names: `python -m Schema.profile_tree --write`);
  * the HONESTY pin: `profile_tree.source_fingerprint` is a deliberate COPY of
    `runschema.contract.source_fingerprint` (schema→optimization imports are forbidden), so the
    two are held byte-for-byte against each other on shared input — CRLF-invariance and the
    MISSING marker included — and `schema_id` is pinned to ignore prose (note/writer/condition/
    description) while tracking every hashed shape field (path/format/scope/optional);
  * `write_profile_layout` / `read_profile_layout` round-trip atomically, and `params_digest`
    really is the sha256 of the file BYTES as written (no canonicalisation — the timestamp
    participating is the feature that makes in-place regeneration detectable).

Everything runs offline from the committed store and `tmp_path` fixtures: no archive, no `.env`,
no `COMPARISON_OUTPUT_DIR`, and the real store is never written.

    python -m pytest Tests/architecture/test_profiletree_consumption.py -q
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re

import pytest

# Importing the declaration, not the resolver: the ratchet tokens come straight from
# profile_tree's ARTIFACTS table so a renamed artifact re-derives its tokens with no edit here.
from Schema import profile_tree as _decl

# The run-tree implementation the honesty test pins against.  `profile_tree` COPIES its hashing
# rather than importing it (schema→optimization is forbidden), which is exactly why the two must
# be compared here — a copy that nothing compares is a copy that drifts.
from Optimization.runschema import contract as _runtree

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Directories swept.  Tests/ is excluded (golden tests hardcode the literals on purpose);
#: Schema/ is the contract's own home; Warehouse/generation/ holds the WRITERS (they legitimately
#: spell the layout they stamp); Optimization/runschema/ mirrors the legacy walk byte-for-byte.
_SWEPT = ('Optimization', 'Visualization', 'Diagnostics', 'docs', 'scripts', 'Warehouse')
_ALLOWED_DIRS = ('Schema', 'Warehouse/generation', 'Optimization/runschema')
_SKIP_DIRS = {'.git', '__pycache__', 'node_modules', 'site', '_build'}

_PLACEHOLDER = re.compile(r'\{\w+\??\}')

#: Declared by the contract but NOT policed: `params.json` / `stats.json` are too generic — the
#: docs tree and the run tree name files by exactly these basenames too, so matching them would
#: flag consumers of a DIFFERENT contract's artifacts.  The token test below asserts they really
#: are declared (so this exclusion cannot silently outlive the artifacts it exempts).
_TOO_GENERIC = ('params.json', 'stats.json')


def _contract_tokens() -> tuple:
    """Literal path fragments a consumer has no business spelling out.

    Same derivation as the run-tree ratchet (`test_runtree_consumption._contract_tokens`), over
    `Schema.profile_tree.ARTIFACTS`: fully-literal template basenames (`inventory.db`,
    `affinity.db`, `profile_layout.json`, `profile_manifest.json`), reserved `_`-prefixed
    directory segments (none today), and distinctive literal fragments >= 6 chars.  The final
    filter keeps only tokens with an extension and length >= 8 (or a leading underscore) so a
    match means THIS contract's file, not a coincidence — minus the `_TOO_GENERIC` names above.
    """
    tokens: set = set()
    for spec in _decl.ARTIFACTS.values():
        path = spec.get('path')
        if not path:
            continue
        for seg in path.split('/'):
            if seg in ('*', '**'):
                continue
            literal = _PLACEHOLDER.sub('', seg)
            if not literal or '*' in literal:
                continue                              # globbed segment — nothing distinctive left
            if literal == seg and '.' in seg:
                tokens.add(seg)                       # fully literal filename
            elif seg.startswith('_') and literal == seg:
                tokens.add(seg)                       # reserved directory (none declared today)
            elif len(literal) >= 6:
                tokens.add(literal)                   # distinctive fragment
    return tuple(sorted(t for t in tokens
                        if (('.' in t and len(t) >= 8) or t.startswith('_'))
                        and t not in _TOO_GENERIC))


def _count(path: str, tokens) -> dict:
    """{token: occurrences} in one source file (raw text — a literal in a comment still teaches
    the next reader to hand-join, so comments count too)."""
    try:
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return {}
    out = {}
    for t in tokens:
        n = text.count(t)
        if n:
            out[t] = n
    return out


def _sweep() -> dict:
    """{(relpath, token): count} over every swept Python source file."""
    tokens = _contract_tokens()
    found: dict = {}
    for top in _SWEPT:
        for dirpath, dirs, files in os.walk(os.path.join(_ROOT, top)):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            rel_dir = os.path.relpath(dirpath, _ROOT).replace(os.sep, '/')
            if any(rel_dir == a or rel_dir.startswith(a + '/') for a in _ALLOWED_DIRS):
                continue
            for fn in sorted(files):
                if not fn.endswith('.py'):
                    continue
                rel = f'{rel_dir}/{fn}'
                for tok, n in _count(os.path.join(dirpath, fn), tokens).items():
                    found[(rel, tok)] = n
    return found


#: The recorded debt: every (file, token) occurrence at the time the ratchet was armed
#: (2026-08-15).  The two `inventory.db` hits are SUBSTRING matches on the run tree's
#: `planned_inventory.db` — not profiles-tree debt at all, but the raw-text count cannot tell
#: them apart, so they are recorded rather than special-cased (shrinkage still works).
#: Regenerate (AFTER confirming the new state is a real migration, not a regression) with:
#:     python -c "from Tests.architecture import test_profiletree_consumption as t; \
#:                import pprint; pprint.pprint(t._sweep())"
#: and paste the result here.  Counts may only go DOWN.
_BASELINE: dict = {('Optimization/simdriver/scenario.py', 'inventory.db'): 1,
                   ('Optimization/simdriver/sim_assets.py', 'inventory.db'): 1,
                   ('Warehouse/catalog/Affinity_Store.py', 'affinity.db'): 5}


# ── the ratchet ─────────────────────────────────────────────────────────────────


def test_the_baseline_is_armed():
    """An empty baseline would make the ratchet a no-op exactly when it matters most.

    `_BASELINE` empty + a clean sweep means the migration finished AND the ratchet holds at
    zero — the ideal end state.  `_BASELINE` empty + a non-empty sweep means someone armed the
    ratchet without recording the debt: every existing occurrence would read as a regression,
    which is noise, not signal.  Fill the baseline.
    """
    current = _sweep()
    if not _BASELINE:
        leftovers = {k: v for k, v in current.items()}
        assert not leftovers or leftovers == {}, (
            'the ratchet has no baseline but the sweep still finds hand-written contract '
            'literals; record them (docstring above) or migrate them:\n  '
            + '\n  '.join(f'{f}: {t} x{n}' for (f, t), n in sorted(leftovers.items())))


def test_no_new_handwritten_profile_paths():
    """A consumer that spells a profiles-tree path re-creates the debt this contract removes.

    Every (file, token) count must be <= its baseline; a pair absent from the baseline fails
    outright.  The fix is never "grow the baseline": resolve through
    `Schema.profile_resolver.ProfileTree` (`path`/`pairs`/`latest`), or — for the contract's own
    layers — move the code under `Schema/`, `Warehouse/generation/`, or `Optimization/runschema/`.
    """
    current = _sweep()
    regressions = []
    for key, n in sorted(current.items()):
        base = _BASELINE.get(key, 0)
        if n > base:
            rel, tok = key
            regressions.append(f'{rel}: {tok!r} x{n} (baseline {base})')
    assert not regressions, (
        'hand-written profiles-tree path knowledge INCREASED — resolve through ProfileTree '
        'instead of growing the debt:\n  ' + '\n  '.join(regressions))


def test_the_tokens_are_still_derived_and_distinctive():
    """The token set itself must not silently collapse.

    If a declaration edit renamed every literal basename into globs, `_contract_tokens()` could
    return so few tokens the ratchet stops policing anything.  Pin a floor and the anchors that
    exist as long as the contract declares them — and pin that the `_TOO_GENERIC` exclusion is
    still excluding something the contract really declares, so it cannot rot into dead code.
    """
    tokens = _contract_tokens()
    assert len(tokens) >= 4, f'token set collapsed to {tokens}'
    for anchor in ('inventory.db', 'affinity.db', 'profile_layout.json', 'profile_manifest.json'):
        assert anchor in tokens, f'{anchor} fell out of the derived token set: {tokens}'

    declared_basenames = {spec['path'].rsplit('/', 1)[-1] for spec in _decl.ARTIFACTS.values()}
    for generic in _TOO_GENERIC:
        assert generic in declared_basenames, (
            f'{generic!r} is excluded as too generic but no artifact declares it any more — '
            f'delete the exclusion or re-derive it')
        assert generic not in tokens, f'{generic!r} must stay excluded from the token set'


# ── the committed store ─────────────────────────────────────────────────────────


def test_the_store_holds_exactly_the_head_document():
    """One document, and it is the head, and it hashes to its own filename.

    The store is content-addressed: the filename IS the short schema id, so a document that does
    not hash to its own name has been hand-edited or half-merged — and it would be believed
    silently by every generator that stamps `head()` into a descriptor.
    """
    head = _decl.head()
    assert head is not None, 'the profile-tree store has no head; run: python -m Schema.profile_tree --write'

    docs = _decl.load_all()
    assert set(docs) == {head}, (
        f'the store holds {sorted(_decl.short_id(s) for s in docs)} but the head is '
        f'{_decl.short_id(head)} — exactly the head document (its whole history is one schema '
        f'so far) should be committed')

    stored = sorted(fn for fn in os.listdir(_decl._TREE_DIR)
                    if fn.endswith('.json') and fn != 'INDEX.json')
    assert stored == [f'{_decl.short_id(head)}.json'], (
        f'store files {stored} != [{_decl.short_id(head)}.json]')
    assert _decl.schema_id(docs[head]) == head, (
        f'{_decl.short_id(head)}.json holds a document that hashes to '
        f'{_decl.short_id(_decl.schema_id(docs[head]))}, not to its own filename')


def test_the_committed_store_is_clean_and_current_now():
    """`verify_store()` and `stale_reasons()` both empty against the working tree.

    `stale_reasons()` is the Stop-hook gate: head == the hash of the current declaration, the
    recorded source fingerprint == the current shape sources, store integrity clean.  If this
    goes red after an edit to a `SHAPE_SOURCES` file, that is the gate WORKING — settle it with
    `python -m Schema.profile_tree --write` (the id only moves on a real shape change).
    """
    # NON-VACUITY: head exists and matches the declaration, so the empty-reasons assert below is
    # about freshness, not about an empty store answering nothing.
    head = _decl.head()
    fresh = _decl.build()['schema_id']
    assert head is not None and head == fresh, (
        f'head {head and _decl.short_id(head)} != declaration hash {_decl.short_id(fresh)}; '
        f'adopt it: python -m Schema.profile_tree --write')

    assert _decl.verify_store() == [], _decl.verify_store()
    reasons = _decl.stale_reasons()
    assert reasons == [], (
        'the committed profile-tree store is stale against the working tree — refresh it '
        '(python -m Schema.profile_tree --write):\n  ' + '\n  '.join(reasons))


def test_stale_reasons_flag_an_empty_store(tmp_path, monkeypatch):
    """An empty store must SAY so, not answer "clean" — on a throwaway store, never the real one.

    Both module paths are monkeypatched to an empty tmp dir; the real committed store is never
    read or written here.  The message must carry the mint command, because "the store is stale"
    without a remedy costs the next person the archaeology this module exists to end.
    """
    monkeypatch.setattr(_decl, '_TREE_DIR', str(tmp_path))
    monkeypatch.setattr(_decl, '_INDEX', os.path.join(str(tmp_path), 'INDEX.json'))

    # NON-VACUITY: the patch really redirected the store — the head is gone under it.
    assert _decl.head() is None, 'the monkeypatched store still resolves a head'

    reasons = _decl.stale_reasons()
    assert reasons, 'an empty store produced no staleness finding at all'
    assert 'empty' in reasons[0], reasons
    assert 'python -m Schema.profile_tree --write' in reasons[0], (
        f'the finding must name the remedy, not just the state: {reasons[0]}')


# ── the honesty pins: the COPY may not drift from the run-tree original ─────────


def test_source_fingerprint_matches_the_runtree_implementation(tmp_path, monkeypatch):
    """The deliberate copy of `runschema.contract.source_fingerprint`, held byte-for-byte.

    `Schema/` may not import `Optimization/`, so `profile_tree` COPIES the fingerprint algorithm
    — and a copy nothing compares is a copy that drifts (the `store_index.source_fingerprint`
    precedent).  Both implementations are pointed at the SAME tmp fixture files and must agree
    on every case that defines the algorithm: shared content, CRLF↔LF churn (invariant), a real
    content change (moves it), and a deleted source (the MISSING marker — moves it, still agree).
    """
    # NON-VACUITY: two distinct implementations from two distinct modules — if either ever
    # aliases the other, this comparison proves nothing (and an import boundary broke).
    assert _decl.source_fingerprint is not _runtree.source_fingerprint
    assert _decl.source_fingerprint.__module__ == 'Schema.profile_tree'
    assert _runtree.source_fingerprint.__module__ == 'Optimization.runschema.contract'

    sources = ('fp_a.py', 'sub/fp_b.py')
    root = str(tmp_path)
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'fp_a.py').write_bytes(b'alpha = 1\nbeta = 2\n')
    (tmp_path / 'sub' / 'fp_b.py').write_bytes(b'gamma = 3\n')
    monkeypatch.setattr(_decl, 'SHAPE_SOURCES', sources)
    monkeypatch.setattr(_runtree, 'SHAPE_SOURCES', sources)

    base_p, base_r = _decl.source_fingerprint(root), _runtree.source_fingerprint(root)
    assert base_p == base_r, (
        f'the two implementations disagree on identical input:\n  profile_tree: {base_p}\n  '
        f'runschema.contract: {base_r}')
    assert base_p.startswith('sha256:'), base_p

    # CRLF↔LF churn is a checkout artifact, not a shape change — both must be invariant.
    crlf = b'alpha = 1\r\nbeta = 2\r\n'
    assert crlf != (tmp_path / 'fp_a.py').read_bytes(), 'the CRLF variant must differ as bytes'
    (tmp_path / 'fp_a.py').write_bytes(crlf)
    assert _decl.source_fingerprint(root) == base_p, 'profile_tree fingerprint moved on CRLF churn'
    assert _runtree.source_fingerprint(root) == base_p, 'contract fingerprint moved on CRLF churn'

    # A real content change must move it — otherwise the invariance above is proving blindness.
    (tmp_path / 'fp_a.py').write_bytes(b'alpha = 99\n')
    changed = _decl.source_fingerprint(root)
    assert changed != base_p, 'a content change did not move the fingerprint'
    assert changed == _runtree.source_fingerprint(root), 'the two disagree after a content change'

    # A MISSING source contributes its path plus a marker rather than being skipped, so a
    # deleted shape source is detected instead of silently matching.
    (tmp_path / 'fp_a.py').write_bytes(b'alpha = 1\nbeta = 2\n')     # restore the baseline text
    os.remove(tmp_path / 'sub' / 'fp_b.py')
    missing = _decl.source_fingerprint(root)
    assert missing != base_p, 'deleting a shape source did not move the fingerprint'
    assert missing == _runtree.source_fingerprint(root), (
        'the two implementations disagree on the missing-file marker')


def test_schema_id_ignores_prose_but_tracks_every_shape_field():
    """Attribution and prose must never mint a schema; every hashed field must.

    `_shape_only` hashes exactly path/format/scope/optional per artifact (plus features,
    descriptor, axes, levels).  If prose leaked in, every doc edit would fork the id and
    descriptors would stop being comparable; if a shape field leaked OUT, a real layout change
    could ship under the old id and re-point consumers silently.  Both directions, one field at
    a time, on throwaway copies of the built document — the declaration itself is never touched.
    """
    base = _decl.build()
    base_id = base['schema_id']
    assert base_id == _decl.schema_id(base), 'build() must stamp its own hash'

    def mutated(fn):
        doc = copy.deepcopy(base)
        fn(doc)
        return _decl.schema_id(doc)

    # Prose and attribution: the id must NOT move.
    for label, fn in [
        ('artifact note', lambda d: d['artifacts']['inventory_db'].__setitem__('note', 'edited')),
        ('artifact writer', lambda d: d['artifacts']['inventory_db'].__setitem__('writer', 'x@y')),
        ('artifact condition',
         lambda d: d['artifacts']['legacy_suite_manifest'].__setitem__('condition', 'edited')),
        ('document description', lambda d: d.__setitem__('description', 'edited')),
        ('level note', lambda d: d['levels'][0].__setitem__('note', 'edited')),
        ('artifact family', lambda d: d['artifacts']['inventory_db'].__setitem__('family', 'z')),
    ]:
        got = mutated(fn)
        assert got == base_id, (
            f'editing {label} minted a NEW schema id ({_decl.short_id(got)} != '
            f'{_decl.short_id(base_id)}) — prose/attribution must never move the id')

    # Shape fields: the id MUST move.
    for label, fn in [
        ('artifact path',
         lambda d: d['artifacts']['inventory_db'].__setitem__('path', '{profile}/inv/i.db')),
        ('artifact format', lambda d: d['artifacts']['inventory_db'].__setitem__('format', 'csv')),
        ('artifact scope', lambda d: d['artifacts']['inventory_db'].__setitem__('scope', 'run')),
        ('artifact optional',
         lambda d: d['artifacts']['profile_layout'].__setitem__('optional', True)),
    ]:
        got = mutated(fn)
        assert got != base_id, (
            f'changing {label} did NOT move the schema id ({_decl.short_id(base_id)}) — a real '
            f'layout change would ship under the old identity')


# ── the descriptor writer ───────────────────────────────────────────────────────

_PROFILES = {
    'mixed_realistic_bell_lt0': {
        'inventory': {'db': 'inventory/inventory.db', 'params_digest': 'sha256:aa'},
        'affinity': {'db': 'affinity/affinity.db', 'params_digest': 'sha256:bb'},
    },
}


def test_write_profile_layout_roundtrips_and_replaces_atomically(tmp_path):
    """What a generator stamps is exactly what a consumer reads back — and nothing else is left.

    Atomicity is tmp + `os.replace`: a crashed write must never leave a half-written descriptor
    that `read_profile_layout` would half-believe, and the callers REWRITE after every completed
    profile — so the second write must land cleanly over the first with no `.tmp.*` residue.
    """
    run_dir = tmp_path / 'mixed_20260815_000000'
    run_dir.mkdir()
    path = _decl.write_profile_layout(str(run_dir), _PROFILES, generator='Tests/probe',
                                      argv=['--name', 'probe'], created='2026-08-15T00:00:00')
    assert os.path.basename(path) == _decl.DESCRIPTOR, path

    doc = _decl.read_profile_layout(str(run_dir))
    assert doc is not None, 'a freshly stamped descriptor must read back'
    assert doc['profiles'] == _PROFILES, doc['profiles']
    assert doc['version'] == 1, doc
    assert doc['created'] == '2026-08-15T00:00:00', doc['created']
    assert doc['generator'] == 'Tests/probe' and doc['argv'] == ['--name', 'probe'], doc
    assert doc['schema_id'] == _decl.head(), (
        f'a descriptor must stamp the store head, got {doc.get("schema_id")}')
    assert 'repo_commit' in doc and 'repo_dirty' in doc, (
        f'provenance must be stamped alongside the binding: {sorted(doc)}')

    left = sorted(os.listdir(run_dir))
    assert left == [_decl.DESCRIPTOR], (
        f'the write must be atomic — tmp residue (or anything else) survived: {left}')

    # The rewrite-after-every-profile path: a second write replaces the first, still atomically.
    _decl.write_profile_layout(str(run_dir), _PROFILES, generator='Tests/probe',
                               created='2026-08-15T00:00:01')
    doc2 = _decl.read_profile_layout(str(run_dir))
    assert doc2 is not None and doc2['created'] == '2026-08-15T00:00:01', doc2
    assert sorted(os.listdir(run_dir)) == [_decl.DESCRIPTOR], sorted(os.listdir(run_dir))


def test_read_profile_layout_is_none_for_absent_or_malformed(tmp_path):
    """A pre-contract catalogue is the NORMAL answer, never an error.

    Descriptors are forward-only (user decision): every catalogue generated before the contract
    lacks one, forever, and consumers fall back to the legacy walk on None.  Malformed bytes read
    as None too — a half-corrupt descriptor must degrade to the walk, not crash a run.
    """
    empty = tmp_path / 'profile_20240101_120000'
    empty.mkdir()
    assert _decl.read_profile_layout(str(empty)) is None, 'absent descriptor must read as None'
    assert _decl.read_profile_layout(str(tmp_path / 'no_such_run')) is None, (
        'a nonexistent run dir must read as None, not raise')

    bad = tmp_path / 'mixed_broken'
    bad.mkdir()
    (bad / _decl.DESCRIPTOR).write_bytes(b'{"version": 1, "profiles": {')     # truncated JSON
    assert _decl.read_profile_layout(str(bad)) is None, (
        'malformed descriptor bytes must read as None (fall back to the walk), not raise')


def test_params_digest_is_the_sha256_of_the_file_bytes(tmp_path):
    """The digest is over the BYTES AS WRITTEN — no canonicalisation, deliberately.

    The params timestamp participating in the digest is a FEATURE: a regeneration mints a new
    digest even under identical parameters, which is exactly what makes silent in-place
    re-pointing detectable from a run's recorded binding.  So the digest must equal a direct
    `hashlib.sha256` of the raw bytes (CRLF included verbatim), and any one-byte change must
    move it.
    """
    payload = b'{\r\n  "seed": 1,\r\n  "generated": "2026-08-15T00:00:00"\r\n}\r\n'
    p = tmp_path / 'params.json'
    p.write_bytes(payload)

    expected = 'sha256:' + hashlib.sha256(payload).hexdigest()
    got = _decl.params_digest(str(p))
    assert got == expected, (
        f'params_digest must hash the raw file bytes (CRLF and all):\n  got      {got}\n'
        f'  expected {expected}')

    p.write_bytes(payload.replace(b'"seed": 1', b'"seed": 2'))
    moved = _decl.params_digest(str(p))
    assert moved != expected, 'a one-byte content change did not move the digest'
    assert moved == 'sha256:' + hashlib.sha256(p.read_bytes()).hexdigest(), moved


if __name__ == '__main__':                                  # pragma: no cover
    import sys
    sys.exit(pytest.main([__file__, '-v']))
