"""test_profile_tree_golden.py — ProfileTree vs the legacy walk, pinned on synthetic catalogues.

The profiles-tree resolver (`Schema/profile_resolver.py`) serves descriptor-bearing runs from
`profile_layout.json` and pre-contract runs by a walk that must mirror the historical
`Optimization/runschema/runlayout.py` behaviour byte-for-byte — every existing catalogue is
pre-contract forever (descriptors are forward-only, per the no-backfill decision), so a drift in
the walk silently re-labels or drops real archives.  These goldens pin, on tmp_path fixtures
only (NO archive access, no `.env`, no real store writes):

  * `pairs()` on a pre-contract catalogue == the hardcoded `(label, inv_db, aff_db)` tuples the
    legacy walk produced, `f'{run}__{profile}'` labels included — that label IS the run tree's
    `<pair>` level and must never drift;
  * an incomplete profile (missing `affinity.db`) is excluded by BOTH routes, and
    `discover_db_pairs` / `find_latest_db_pairs` return lists IDENTICAL to ProfileTree's
    runs/pairs/latest on the same fixture;
  * the descriptor route answers exactly like the walk on a complete run (proved by removing the
    descriptor mid-test, with a non-vacuity guard that the descriptor route was really taken),
    and a descriptor's claims are EXISTENCE-CHECKED — deleting a DB excludes the pair while the
    descriptor still names it;
  * `binding_of` returns exactly the recorded catalogue-version binding, and honest `None` for
    pre-contract runs and malformed labels — absence of evidence, never fabricated evidence;
  * `latest()` stays lexicographic on descriptor-less trees (== the legacy choice), a NEWER
    descriptor `created` on a lexicographically-earlier name wins AND warns (caplog), and an
    empty root answers None;
  * `path()` renders the contract templates to exactly the golden strings the writers compose.

    python -m pytest Tests/integration/test_profile_tree_golden.py -q
"""
from __future__ import annotations

import logging
import os

import pytest

from Optimization.runschema import runlayout
from Schema import profile_tree
from Schema.profile_resolver import ProfileTree


# ── fixture builders (empty .db files — layout is under test, content never is) ──

def _touch(root, *parts) -> str:
    """An empty file at root/parts, parents created.  Empty on purpose: every question here is
    about WHERE files are, and a 0-byte `inventory.db` answers it as well as 40 GB would."""
    p = os.path.join(str(root), *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'wb').close()
    return p


def _legacy_catalogue(tmp_path) -> str:
    """A pre-contract profiles root: two runs x two profiles, one profile incomplete.

    No descriptor anywhere — this is every catalogue generated before the contract existed.
    Stray files at both levels exercise the walks' isdir filters (both routes must skip them).
    """
    root = tmp_path / 'profiles'
    for profile in ('mixed_realistic_lt0', 'mixed_realistic_lt4'):
        _touch(root, 'profile_20240101_120000', profile, 'inventory', 'inventory.db')
        _touch(root, 'profile_20240101_120000', profile, 'affinity', 'affinity.db')
    _touch(root, 'profile_20240202_120000', 'mixed_realistic_bell_lt2', 'inventory',
           'inventory.db')
    _touch(root, 'profile_20240202_120000', 'mixed_realistic_bell_lt2', 'affinity', 'affinity.db')
    # The incomplete profile: inventory generated, affinity never finished — a real crash shape.
    _touch(root, 'profile_20240202_120000', 'mixed_realistic_ltrand0-4', 'inventory',
           'inventory.db')
    _touch(root, 'notes.txt')                                   # stray file at the root level
    _touch(root, 'profile_20240202_120000', 'README.txt')       # stray file at the run level
    return str(root)


def _pair(root: str, run: str, profile: str) -> tuple:
    """The (label, inv, aff) tuple both routes must produce for one complete profile."""
    pdir = os.path.join(root, run, profile)
    return (f'{run}__{profile}',
            os.path.join(pdir, 'inventory', 'inventory.db'),
            os.path.join(pdir, 'affinity', 'affinity.db'))


def _descriptor_run(root, run: str, profiles: tuple, created: str) -> None:
    """A complete descriptor-bearing run: DB files on disk + a descriptor naming every profile."""
    for profile in profiles:
        _touch(root, run, profile, 'inventory', 'inventory.db')
        _touch(root, run, profile, 'affinity', 'affinity.db')
    profile_tree.write_profile_layout(
        os.path.join(str(root), run),
        {p: {'inventory': {'db': 'inventory/inventory.db', 'params_digest': f'sha256:inv_{p}'},
             'affinity': {'db': 'affinity/affinity.db', 'params_digest': f'sha256:aff_{p}'}}
         for p in profiles},
        generator='Tests/golden', created=created)


# ── the pre-contract walk: hardcoded goldens, both routes ───────────────────────


def test_pairs_on_a_pre_contract_catalogue_match_the_hardcoded_golden(tmp_path):
    """The walk fallback must reproduce the legacy answer literally — labels included.

    The expected tuples are spelled out rather than derived, so a drift in EITHER implementation
    (ProfileTree's fallback or runlayout's legacy loop) fails by name here instead of re-labeling
    archived runs far away.
    """
    root = _legacy_catalogue(tmp_path)
    tree = ProfileTree(root)

    golden_run1 = [
        ('profile_20240101_120000__mixed_realistic_lt0',) + _pair(
            root, 'profile_20240101_120000', 'mixed_realistic_lt0')[1:],
        ('profile_20240101_120000__mixed_realistic_lt4',) + _pair(
            root, 'profile_20240101_120000', 'mixed_realistic_lt4')[1:],
    ]
    golden_run2 = [
        ('profile_20240202_120000__mixed_realistic_bell_lt2',) + _pair(
            root, 'profile_20240202_120000', 'mixed_realistic_bell_lt2')[1:],
    ]

    # NON-VACUITY: the incomplete profile's inventory side really exists on disk, so its absence
    # from the answers below is the missing-affinity filter working, not a typo'd fixture.
    assert os.path.exists(os.path.join(root, 'profile_20240202_120000',
                                       'mixed_realistic_ltrand0-4', 'inventory', 'inventory.db'))

    assert tree.pairs('profile_20240101_120000') == golden_run1, (
        tree.pairs('profile_20240101_120000'))
    assert tree.pairs('profile_20240202_120000') == golden_run2, (
        tree.pairs('profile_20240202_120000'))

    for run, golden in (('profile_20240101_120000', golden_run1),
                        ('profile_20240202_120000', golden_run2)):
        legacy = runlayout._profile_pairs(os.path.join(root, run), run)
        assert legacy == golden, f'runlayout legacy walk drifted for {run}: {legacy}'

    all_labels = [p[0] for run in tree.runs() for p in tree.pairs(run)]
    assert 'profile_20240202_120000__mixed_realistic_ltrand0-4' not in all_labels, (
        f'the incomplete profile leaked into the pair list: {all_labels}')


def test_discover_and_find_latest_match_profiletree_end_to_end(tmp_path):
    """The runlayout entry points and ProfileTree must give IDENTICAL lists on the same tree.

    `discover_db_pairs` is every consumer's full-catalogue scan and `find_latest_db_pairs` is the
    default-catalogue pick; if either ever disagrees with ProfileTree's runs/pairs/latest, two
    halves of the same pipeline would simulate different catalogues without an error message.
    """
    root = _legacy_catalogue(tmp_path)
    os.makedirs(os.path.join(root, 'profile_20990909_000000'))     # later name, NO pairs
    tree = ProfileTree(root)

    assert tree.runs() == ['profile_20240101_120000', 'profile_20240202_120000',
                           'profile_20990909_000000'], tree.runs()

    flattened = [p for run in tree.runs() for p in tree.pairs(run)]
    assert len(flattened) == 3, f'fixture must yield exactly 3 complete pairs: {flattened}'
    assert runlayout.discover_db_pairs(root) == flattened, (
        f'discover_db_pairs disagrees with ProfileTree:\n  {runlayout.discover_db_pairs(root)}'
        f'\n  {flattened}')

    # The empty later-named run must be walked PAST by both routes, not returned empty-handed.
    assert tree.latest() == 'profile_20240202_120000', tree.latest()
    assert runlayout.find_latest_db_pairs(root) == tree.pairs('profile_20240202_120000'), (
        runlayout.find_latest_db_pairs(root))


# ── the descriptor route ────────────────────────────────────────────────────────


def test_descriptor_pairs_equal_the_walk_and_existence_check_the_claims(tmp_path):
    """A descriptor is a claim; the DB on disk is the fact.  Same answer as the walk, minus lies.

    Route equivalence is proved on ONE tree by removing the descriptor mid-test (the walk
    fallback then answers) — comparing against `runlayout` here would be circular, since its
    `_profile_pairs` now delegates descriptor-bearing runs to ProfileTree.  Then a DB file is
    deleted while the descriptor still names its profile: the pair must vanish, because a
    descriptor whose claims were not existence-checked would hand a simulation a missing file.
    """
    root = str(tmp_path / 'profiles')
    run = 'mixed_20260815_000000'
    _descriptor_run(tmp_path / 'profiles', run, ('p_alpha', 'p_beta'), '2026-08-15T00:00:00')
    tree = ProfileTree(root)

    # NON-VACUITY: the descriptor route is really the one answering (a silent fallback to the
    # walk would make every equality below trivially true).
    assert tree.layout_of(run) is not None, 'the descriptor did not land — route unproven'

    golden = [_pair(root, run, 'p_alpha'), _pair(root, run, 'p_beta')]
    assert tree.pairs(run) == golden, tree.pairs(run)

    # Same tree, descriptor removed -> the byte-for-byte legacy walk must answer identically.
    desc = os.path.join(root, run, profile_tree.DESCRIPTOR)
    os.remove(desc)
    assert tree.layout_of(run) is None, 'descriptor removal did not switch the route'
    assert tree.pairs(run) == golden, (
        f'walk fallback disagrees with the descriptor route on the same tree: {tree.pairs(run)}')

    # Restore the descriptor, then break one claim: p_beta's affinity DB disappears.
    _descriptor_run(tmp_path / 'profiles', run, ('p_alpha', 'p_beta'), '2026-08-15T00:00:00')
    os.remove(os.path.join(root, run, 'p_beta', 'affinity', 'affinity.db'))
    assert tree.pairs(run) == [_pair(root, run, 'p_alpha')], tree.pairs(run)
    layout = profile_tree.read_profile_layout(os.path.join(root, run))
    assert sorted(layout['profiles']) == ['p_alpha', 'p_beta'], (
        'the descriptor no longer names both profiles — the exclusion above proved nothing '
        'about the existence check')


def test_binding_of_returns_exactly_the_recorded_binding(tmp_path):
    """The catalogue-version binding a run records: WHICH catalogue, not merely where it was.

    Asserted as a whole dict, not field-by-field: the binding is written into `sim_meta` land by
    consumers, so an extra, missing, or renamed key is a schema change for every downstream
    reader and must fail here by value.
    """
    root = str(tmp_path / 'profiles')
    run = 'mixed_20260815_000000'
    _descriptor_run(tmp_path / 'profiles', run, ('p_alpha', 'p_beta'), '2026-08-15T00:00:00')
    tree = ProfileTree(root)

    layout = tree.layout_of(run)
    assert layout is not None and str(layout['schema_id']).startswith('sha256:'), (
        f'the descriptor must carry a real schema id: {layout and layout.get("schema_id")}')

    binding = tree.binding_of(f'{run}__p_alpha')
    assert binding == {
        'profile_run': run,
        'profile': 'p_alpha',
        'profile_schema_id': layout['schema_id'],
        'params_digest': {'inventory': 'sha256:inv_p_alpha', 'affinity': 'sha256:aff_p_alpha'},
    }, binding

    assert tree.binding_of(f'{run}__no_such_profile') is None, (
        'a profile the descriptor never named must bind to None, not to a fabricated entry')


def test_binding_is_none_for_pre_contract_runs_and_malformed_labels(tmp_path):
    """Absence of evidence is recorded as None — never fabricated.

    Descriptors are forward-only, so every pre-contract catalogue legitimately has no binding;
    inventing one (from mtimes, from a walk) would be evidence a generator never wrote.  A label
    without the `__` separator is not a pair label at all and answers None the same way.
    """
    root = _legacy_catalogue(tmp_path)
    tree = ProfileTree(root)

    label = 'profile_20240101_120000__mixed_realistic_lt0'
    # NON-VACUITY: the pair is real and discoverable — None below is about the missing
    # descriptor, not about a label that matches nothing.
    assert label in [p[0] for p in tree.pairs('profile_20240101_120000')]

    assert tree.binding_of(label) is None, 'a pre-contract run must bind to None'
    assert tree.binding_of('profile_20240101_120000') is None, (
        'a label with no __ separator is malformed and must bind to None')


# ── latest() ────────────────────────────────────────────────────────────────────


def test_a_newer_descriptor_created_beats_the_lexicographic_order_and_warns(tmp_path, caplog):
    """Descriptor timestamps outrank names — and the disagreement is SAID, not silent.

    A backdated `--name` is legal, so the merged order must prefer the descriptor's `created`;
    but a human renamed that run for a reason, so the resolver logs both answers.  Both the
    override and the warning are asserted, and `find_latest_db_pairs` (which now delegates) must
    follow the same pick.
    """
    root = str(tmp_path / 'profiles')
    _descriptor_run(tmp_path / 'profiles', 'a_backdated_name', ('p_alpha',),
                    '2099-01-01T00:00:00')
    for side in ('inventory', 'affinity'):
        _touch(root, 'profile_20240101_120000', 'mixed_realistic_lt0', side, f'{side}.db')
    tree = ProfileTree(root)

    # NON-VACUITY: the two orders genuinely disagree on this fixture.
    candidates = [r for r in tree.runs() if tree.pairs(r)]
    assert candidates[-1] == 'profile_20240101_120000', (
        f'the fixture must make the lexicographic pick differ from the descriptor pick: '
        f'{candidates}')

    with caplog.at_level(logging.WARNING, logger='Schema.profile_resolver'):
        assert tree.latest() == 'a_backdated_name', tree.latest()

    warnings = [r.getMessage() for r in caplog.records
                if r.name == 'Schema.profile_resolver' and r.levelno >= logging.WARNING]
    assert any('a_backdated_name' in m and 'profile_20240101_120000' in m for m in warnings), (
        f'the override must be logged with BOTH answers named: {warnings}')

    assert runlayout.find_latest_db_pairs(root) == tree.pairs('a_backdated_name'), (
        runlayout.find_latest_db_pairs(root))


def test_latest_is_lexicographic_and_silent_without_descriptors(tmp_path, caplog):
    """On a descriptor-less tree the historical behaviour survives byte-for-byte, with no noise.

    The timestamped run names make the lexicographic order correct, so no warning may fire —
    a resolver that warns on every legacy catalogue trains people to ignore the one warning
    that matters.
    """
    root = _legacy_catalogue(tmp_path)
    tree = ProfileTree(root)
    with caplog.at_level(logging.WARNING, logger='Schema.profile_resolver'):
        assert tree.latest() == 'profile_20240202_120000', tree.latest()
    assert [r for r in caplog.records if r.name == 'Schema.profile_resolver'] == [], (
        f'no descriptor, no disagreement, no warning: '
        f'{[r.getMessage() for r in caplog.records]}')


def test_latest_is_none_on_an_empty_root(tmp_path):
    """No catalogue is an answer (None), not an exception — for empty AND nonexistent roots."""
    empty = tmp_path / 'profiles_empty'
    empty.mkdir()
    assert ProfileTree(str(empty)).latest() is None
    assert ProfileTree(str(empty)).runs() == []
    assert runlayout.find_latest_db_pairs(str(empty)) == []

    missing = str(tmp_path / 'no_such_dir')
    assert ProfileTree(missing).latest() is None
    assert ProfileTree(missing).runs() == []
    assert runlayout.discover_db_pairs(missing) == []


# ── path() goldens ──────────────────────────────────────────────────────────────


def test_path_renders_the_contract_templates_exactly(tmp_path):
    """Every artifact template renders to the literal string its writer composes.

    Hardcoded byte-for-byte (not derived from the contract, which would be circular): a template
    edit that would relocate a generator's output fails here by name before anything is written
    to the wrong place.
    """
    root = str(tmp_path / 'profiles')
    tree = ProfileTree(root)
    run, prof = 'mixed_20260815_000000', 'mixed_realistic_bell_lt0'
    base = os.path.join(root, run)

    assert tree.path('inventory_db', run, profile=prof) == os.path.join(
        base, prof, 'inventory', 'inventory.db')
    assert tree.path('affinity_db', run, profile=prof) == os.path.join(
        base, prof, 'affinity', 'affinity.db')
    assert tree.path('inventory_params', run, profile=prof) == os.path.join(
        base, prof, 'inventory', 'params.json')
    assert tree.path('affinity_stats', run, profile=prof) == os.path.join(
        base, prof, 'affinity', 'stats.json')
    assert tree.path('profile_layout', run) == os.path.join(base, 'profile_layout.json')
    assert tree.path('legacy_suite_manifest', run) == os.path.join(base, 'profile_manifest.json')
    assert tree.path('cross_profile_dir', run) == os.path.join(base, 'cross_profile')


def test_path_with_a_missing_required_part_fails_loudly(tmp_path):
    """A typo'd or forgotten part must raise, not render a path with a literal brace in it."""
    tree = ProfileTree(str(tmp_path))
    with pytest.raises(KeyError, match='profile'):
        tree.path('inventory_db', 'mixed_20260815_000000')      # {profile} never supplied


if __name__ == '__main__':                                  # pragma: no cover
    import sys
    sys.exit(pytest.main([__file__, '-v']))
