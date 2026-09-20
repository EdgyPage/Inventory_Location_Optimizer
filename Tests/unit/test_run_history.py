"""test_run_history.py — the record of how a run was constructed, and whether it finished.

Before this, nothing durable said a run had finished. `run_spec.json` is written once at
launch and never rewritten, so it carries no timestamp, no status, and no trace of the three
attempts a long campaign takes; answering "which of these forty roots were phase 2, and did
they complete?" meant opening forty files that could only answer the first half.

The decisions with a silent failure mode:

  * **One record per LAUNCH, including every resume.** Neither existing writer has that
    rule, and a resume is exactly the event a reader is trying to reconstruct.
  * **`attempt` and `resume_of` are DERIVED from what is on disk**, never passed in, so a
    caller cannot hand the chain a wrong link.
  * **`status: null` means the launch never reported back** — killed, crashed, machine down.
    A different fact from a launch that failed, and the two are never merged. A record that
    defaulted to `'failed'` would report a power cut as a simulation defect.
  * **The refusal path records too.** A run that stopped with unrecovered units is the one
    somebody comes back to days later; before this it raised without writing anything.
  * **Both ledgers are best-effort and never raise.** A write that sank a finished
    twelve-hour run would be worse than the gap it closes.

Run:  python -m pytest Tests/unit/test_run_history.py -q
"""
from __future__ import annotations

import json
import os

from Optimization.runschema import sim_manifest as sm


def _rec(spec='inbound_policies', phase=2, **kw):
    return {'spec': spec, 'phase': phase, 'argv': ['--spec', spec], **kw}


# ── the per-run history ──────────────────────────────────────────────────────────────

def test_a_first_launch_opens_attempt_one_with_no_parent(tmp_path):
    root = str(tmp_path / 'comparison_whatif_1')
    os.makedirs(root)
    n = sm.open_run_record(root, _rec())
    assert n == 1
    hist = sm.read_run_history(root)
    assert len(hist) == 1
    r = hist[0]
    assert r['attempt'] == 1 and r['resume_of'] is None
    assert r['spec'] == 'inbound_policies' and r['phase'] == 2
    assert r['started'] and r['repo_commit']
    assert r['status'] is None and r['ended'] is None, (
        'a launch that has not reported back must read as UNKNOWN, not as failed')


def test_each_resume_appends_and_chains_to_the_one_before(tmp_path):
    """The chain a reader reconstructs a campaign from: three launches, two of them
    resumes, each naming the start time of the attempt it followed."""
    root = str(tmp_path / 'comparison_whatif_2')
    os.makedirs(root)
    a = sm.open_run_record(root, _rec())
    sm.close_run_record(root, a, status='incomplete', unfinished={'k1_off': 4})
    b = sm.open_run_record(root, _rec(resume=True))
    sm.close_run_record(root, b, status='incomplete', unfinished={'k1_off': 1})
    c = sm.open_run_record(root, _rec(resume=True))
    sm.close_run_record(root, c, status='complete', unfinished={}, analysis_ran=True)

    hist = sm.read_run_history(root)
    assert [r['attempt'] for r in hist] == [1, 2, 3]
    assert hist[0]['resume_of'] is None
    assert hist[1]['resume_of'] == hist[0]['started']
    assert hist[2]['resume_of'] == hist[1]['started']
    assert [r['status'] for r in hist] == ['incomplete', 'incomplete', 'complete']
    assert hist[0]['unfinished'] == {'k1_off': 4}
    assert hist[2]['analysis_ran'] is True


def test_the_attempt_number_is_derived_not_taken_from_the_caller(tmp_path):
    """A caller that passed its own `attempt` could re-use one and overwrite an earlier
    record's outcome, which is how a torn campaign reads as a clean one."""
    root = str(tmp_path / 'comparison_whatif_3')
    os.makedirs(root)
    sm.open_run_record(root, _rec())
    assert sm.open_run_record(root, {**_rec(), 'attempt': 1, 'resume_of': 'lies'}) == 2
    hist = sm.read_run_history(root)
    assert [r['attempt'] for r in hist] == [1, 2]
    assert hist[1]['resume_of'] == hist[0]['started'], (
        'the caller supplied a `resume_of` and it must not have been believed')


def test_closing_an_attempt_that_does_not_exist_is_a_no_op(tmp_path):
    root = str(tmp_path / 'comparison_whatif_4')
    os.makedirs(root)
    sm.open_run_record(root, _rec())
    sm.close_run_record(root, 99, status='complete')
    assert [r['status'] for r in sm.read_run_history(root)] == [None]


def test_a_malformed_history_reads_as_none_rather_than_raising(tmp_path):
    """A ledger that raised would take a whole campaign's history with it, and it is read
    at the START of every launch -- the worst possible place to fail."""
    root = str(tmp_path / 'comparison_whatif_5')
    os.makedirs(root)
    with open(os.path.join(root, 'run_history.json'), 'w', encoding='utf-8') as f:
        f.write('{ not json at all')
    assert sm.read_run_history(root) == []
    assert sm.open_run_record(root, _rec()) == 1, 'a fresh list must start beside it'


def test_a_missing_root_never_raises_from_the_reader(tmp_path):
    assert sm.read_run_history(str(tmp_path / 'nope')) == []


# ── the cross-run ledger ─────────────────────────────────────────────────────────────

def test_the_ledger_lands_beside_the_run_roots_not_inside_one(tmp_path):
    """OUTSIDE every run root, and therefore outside the run-tree contract: no schema id
    moves to add it, and a root that is archived or deleted leaves its lines behind."""
    out = tmp_path / 'outputs'
    root = out / 'comparison_whatif_6'
    os.makedirs(root)
    sm.append_run_index(str(root), 'launched', {'attempt': 1, 'spec': 's', 'phase': 2})
    ledger = out / sm.RUN_INDEX_NAME
    assert ledger.exists(), f'the ledger must be at {ledger}, beside the roots'
    assert not (root / sm.RUN_INDEX_NAME).exists()

    lines = sm.read_run_index(str(out))
    assert len(lines) == 1
    assert lines[0]['event'] == 'launched' and lines[0]['run'] == 'comparison_whatif_6'
    assert lines[0]['phase'] == 2 and lines[0]['at']


def test_the_ledger_appends_and_skips_a_malformed_line(tmp_path):
    out = tmp_path / 'outputs'
    root = out / 'comparison_whatif_7'
    os.makedirs(root)
    sm.append_run_index(str(root), 'launched', {'attempt': 1})
    with open(out / sm.RUN_INDEX_NAME, 'a', encoding='utf-8') as f:
        f.write('{ truncated\n')
    sm.append_run_index(str(root), 'finished', {'attempt': 1, 'status': 'complete'})

    lines = sm.read_run_index(str(out))
    assert [ln['event'] for ln in lines] == ['launched', 'finished'], (
        'a malformed line must be skipped, not fatal, and must not eat the lines after it')


def test_an_unwritable_ledger_never_raises(tmp_path):
    """Best-effort by design: this runs at the front and the tail of every simulation."""
    sm.append_run_index(str(tmp_path / 'a' / 'b' / 'c' / 'nope'), 'launched', {'attempt': 1})
    assert sm.read_run_index(str(tmp_path / 'nowhere')) == []


# ── the reader ───────────────────────────────────────────────────────────────────────

def test_the_index_reader_folds_launches_and_outcomes_per_run(tmp_path):
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'scripts'))
    import run_index as ri

    out = tmp_path / 'outputs'
    for name in ('comparison_whatif_a', 'comparison_whatif_b'):
        os.makedirs(out / name)
    sm.append_run_index(str(out / 'comparison_whatif_a'), 'launched',
                        {'attempt': 1, 'spec': 'inbound_select', 'phase': 1})
    sm.append_run_index(str(out / 'comparison_whatif_a'), 'finished',
                        {'attempt': 1, 'status': 'complete'})
    sm.append_run_index(str(out / 'comparison_whatif_b'), 'launched',
                        {'attempt': 1, 'spec': 'inbound_policies', 'phase': 2})

    got = {r['run']: r for r in ri.rows(str(out))}
    assert got['comparison_whatif_a']['phase'] == 1
    assert got['comparison_whatif_a']['status'] == 'complete'
    assert got['comparison_whatif_b']['phase'] == 2
    assert got['comparison_whatif_b']['status'] is None, (
        'a run that launched and never reported back must read as unknown')


def test_a_new_launch_reopens_a_run_that_had_reported_complete(tmp_path):
    """The stale-outcome trap: a resume of a finished root makes the old `complete` stop
    describing what is on disk, and a reader that kept it would call a half-resumed run
    finished."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'scripts'))
    import run_index as ri

    out = tmp_path / 'outputs'
    root = out / 'comparison_whatif_c'
    os.makedirs(root)
    sm.append_run_index(str(root), 'launched', {'attempt': 1, 'spec': 's', 'phase': 2})
    sm.append_run_index(str(root), 'finished', {'attempt': 1, 'status': 'complete'})
    sm.append_run_index(str(root), 'launched', {'attempt': 2, 'spec': 's', 'phase': 2,
                                                'resume': True})

    row = ri.rows(str(out))[0]
    assert row['launches'] == 2
    assert row['status'] is None, f'the stale outcome survived a relaunch: {row}'


def test_a_root_that_predates_the_ledger_is_listed_as_unknown(tmp_path):
    """Every root before 2026-09-19 has no line and no history. Listing it as UNKNOWN is
    honest; omitting it would make the tool describe a smaller archive than the one on
    disk."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'scripts'))
    import run_index as ri

    out = tmp_path / 'outputs'
    os.makedirs(out / 'comparison_20260101_000000')
    rows = ri.rows(str(out))
    assert [r['run'] for r in rows] == ['comparison_20260101_000000']
    assert rows[0]['status'] is None and rows[0]['launches'] == 0


def test_deep_reads_the_run_s_own_history_as_the_authority(tmp_path):
    """The ledger is best-effort and can be incomplete; the per-run file is the authority,
    and `--deep` is what prefers it."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'scripts'))
    import run_index as ri

    out = tmp_path / 'outputs'
    root = out / 'comparison_whatif_d'
    os.makedirs(root)
    a = sm.open_run_record(str(root), _rec(spec='inbound_unload', phase=2))
    sm.close_run_record(str(root), a, status='incomplete', unfinished={'k1_off_fifo': 2})
    # NOTHING was written to the cross-run ledger for this run at all.
    shallow = ri.rows(str(out))[0]
    assert shallow['status'] is None and shallow['spec'] is None

    deep = ri.rows(str(out), deep=True)[0]
    assert deep['spec'] == 'inbound_unload' and deep['phase'] == 2
    assert deep['status'] == 'incomplete' and deep['unfinished'] == {'k1_off_fifo': 2}


# ── the declaration ──────────────────────────────────────────────────────────────────

def test_the_artifact_is_declared_by_the_run_tree_contract():
    from Optimization.runschema import schema
    art = schema.ARTIFACTS['run_history']
    assert art['scope'] == 'run' and art['path'] == 'run_history.json'
    assert art['writer'] == 'open_run_record@Optimization/runschema/sim_manifest.py'
    assert not art.get('optional'), (
        'every run writes it, so it is required -- an optional declaration would let a run '
        'that wrote none pass the tree verifier, which is the state this replaces')


def test_every_funnel_spec_declares_its_phase():
    """DECLARED, never inferred from the name: a spec called `inbound_unload` says nothing
    about which phase ranks what, and the phase is what the ledger filters on."""
    from Optimization.config import whatif_config as wc
    for name, want in (('inbound_select', 1), ('inbound_policies', 2),
                       ('inbound_unload', 2), ('inbound_unload_rider', 2)):
        assert wc.SPECS[name].get('phase') == want, (
            f'{name} declares phase {wc.SPECS[name].get("phase")!r}, expected {want}')


def test_the_rider_spec_needs_no_selection_artifact():
    """The whole point of it: phase 2 runnable BEFORE phase 1 answers. `inbound_policies`
    carries PHASE2_PAIRS, which is None until the selection artifact is read and refused
    rather than defaulted; the rider is pre-committed."""
    from Optimization.config import whatif_config as wc
    rider = wc.SPECS['inbound_unload_rider']
    assert rider['rule_pairs'] == (('fifo', 'fifo'),)
    assert rider['inbound'] == wc.SPECS['inbound_policies']['inbound'], (
        'the rider must sweep the SAME inbound axis, or it is not the same question')
    got = wc.get_spec('inbound_unload_rider')            # must validate without phase 1
    assert got['rule_pairs'] == (('fifo', 'fifo'),)
