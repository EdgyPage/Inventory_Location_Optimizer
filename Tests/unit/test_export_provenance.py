"""test_export_provenance.py -- an exported tree can still say what commit it is.

Every campaign in this repo runs from a `git archive` copy: a spawn-per-job run re-imports the
tree for every unit, so it must not see the working tree move under it (memory
`detached-runs-import-the-working-tree`).  An archive carries no `.git`, and until 2026-09-20
`repo_provenance` had no other source -- so the runs whose provenance matters MOST, the
multi-day campaigns nobody will remember the details of, were exactly the ones stamping
`repo_commit: unknown` into their own `run_spec.json`.  Caught on a phase-2 campaign launch.

The contract these pin:

  * a stamped export answers with its sha, and answers `repo_dirty` rather than shrugging,
    because an archive is an export of a COMMIT and is clean by construction;
  * a stamp may record `dirty` for an export someone has overlaid files onto (this session
    made several such probe snapshots), and that is believed;
  * an UNSTAMPED export still degrades to `unknown` rather than raising, which is the rule
    the whole module exists under;
  * git metadata WINS over a stamp, so a clone that happens to carry one is still read from
    git -- the working tree is the truth about what is running.

Run:  python -m pytest Tests/unit/test_export_provenance.py -q
"""
from __future__ import annotations

import os

from Schema.provenance import SOURCE_STAMP, repo_provenance, write_source_stamp

_SHA = '8f993a33ea87c0de'


def test_an_unstamped_export_degrades_to_unknown(tmp_path):
    got = repo_provenance(str(tmp_path))
    assert got == {'repo_commit': 'unknown', 'repo_dirty': None}, (
        'an export with neither git metadata nor a stamp must still ANSWER -- a producer '
        'that raised here would be strictly worse than one recording unknown')


def test_a_stamped_export_reports_its_commit_and_is_clean(tmp_path):
    write_source_stamp(str(tmp_path), _SHA)
    got = repo_provenance(str(tmp_path))
    assert got['repo_commit'] == _SHA[:12]
    assert got['repo_dirty'] is False, (
        'an archive is an export of a commit, so it is clean BY CONSTRUCTION; reporting '
        'None here would throw away an answer the export actually has')


def test_a_stamp_may_declare_the_export_dirty(tmp_path):
    write_source_stamp(str(tmp_path), _SHA, dirty=True)
    assert repo_provenance(str(tmp_path))['repo_dirty'] is True, (
        'a probe snapshot with files overlaid on top of a commit is not that commit, and '
        'the stamp is the only place that can say so')


def test_an_empty_or_junk_stamp_does_not_masquerade_as_an_answer(tmp_path):
    (tmp_path / SOURCE_STAMP).write_text('   \n', encoding='utf-8')
    assert repo_provenance(str(tmp_path)) == {'repo_commit': 'unknown', 'repo_dirty': None}, (
        'an empty stamp is not a commit; it must fall through to unknown rather than '
        'recording the empty string as provenance')


def test_git_metadata_wins_over_a_stamp(tmp_path):
    """A clone that also carries a stamp is a clone: what is RUNNING is the working tree,
    and the stamp is only ever a fallback for a tree that has no other answer."""
    git = tmp_path / '.git'
    git.mkdir()
    (git / 'HEAD').write_text('ref: refs/heads/develop\n', encoding='utf-8')
    refs = git / 'refs' / 'heads'
    refs.mkdir(parents=True)
    (refs / 'develop').write_text('abcdef0123456789\n', encoding='utf-8')
    write_source_stamp(str(tmp_path), _SHA)
    got = repo_provenance(str(tmp_path))
    assert got['repo_commit'] == 'abcdef012345', (
        f"the stamp was preferred over git metadata (got {got['repo_commit']!r}); a tree "
        f"with a .git is read from git, or a stale stamp would outrank the truth")


def test_the_repo_itself_still_answers_from_git():
    """Non-vacuity: the four cases above all run in a temp dir, so nothing there would
    notice if the git path stopped working entirely."""
    got = repo_provenance()
    assert got['repo_commit'] != 'unknown', (
        'the repo has git metadata and must resolve through it')
    assert len(got['repo_commit']) == 12
    assert got['repo_dirty'] in (True, False)
