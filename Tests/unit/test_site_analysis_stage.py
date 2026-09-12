"""test_site_analysis_stage.py — the analysis half of the site scope: the context that reads
BOTH leaves, the rollup that refuses a coupled run, and the two declarations the site tree
is resolved through.

"Re-scope the analysis surfaces to the site" (site-dock 07) decided that site-ness lives in
WHAT THE KEYS POINT AT rather than in a second implementation of every frame broker: a site
"strategy" is an ARM PAIR whose `db_path` is the `_site/` inbound DB and which additionally
carries its two leaves.  That design has exactly one load-bearing override — `SiteContext
.batch_df` — and four declarations that have to stay tied to each other.  This file pins all
five, and each one is a different kind of silent failure:

  * **`batch_df` returns BOTH leaves' frames concatenated.**  `_arm_end_s` and both of
    `yard/scorecard`'s denominators read through it, so the censoring bound becomes the
    union of the two leaves' clocks and the receiver's busy share becomes both leaves'
    `recv_seconds` over the days they share.  A `batch_df` that quietly returned ONE leaf
    would still render every yard figure, with a shorter span and half the seconds — and
    nothing would look wrong.  So the fixture is built with two leaves whose clocks
    genuinely differ, and the test asserts the DIRECTION: the site span is strictly larger
    than either leaf's alone.
  * **`leaf_batch_df` is the per-channel SHARE, and it refuses an unknown channel.**  A
    right site total hides two wrong shares, so the split is what the pool's fairness rule
    is seen through; a channel that silently resolved to the wrong leaf would print one
    leaf's number under both names.
  * **`run_channel_rollup.rollup` raises a `ValueError` on a coupled run, and the TYPE is
    the decision.**  `analyze_run._step` catches `Exception`, and `SystemExit` is not one —
    so the house-style `SystemExit` refusal would abort `analyze_run` mid-run from inside
    its cell loop, killing every remaining cell's analysis and the cross-cell what-if stage.
    The type is asserted directly, and so is the ORDER: the refusal fires before a single
    series doc is read, proved by pointing it at a cell with no analyzed runs at all, where
    an uncoupled tree demonstrably returns `{}`.
  * **`RunTree.arm_pair_of` inverts the declared template.**  The arm pair is ONE capture —
    the two halves are joined by `__` inside the stem — so a consumer slicing fixed prefix
    and suffix lengths would survive a template rename and read the wrong key.
  * **`SITE_SCOPE_FAMILIES` and `_SITE_FIGURE_FAMILIES` are the same tuple.**  They are
    duplicated on purpose (`runschema/schema.py` imports nothing, so the contract's
    `schema_id` cannot depend on an analysis package), and this equality is the only thing
    that makes the duplication safe: a family added to one and not the other declares a
    path nothing produces, or produces output nothing declares — a preflight violation
    either way.

The DBs are real: `init_run_db` / `create_run` / `save_batch_stats` for the two leaves and
`save_site_inbound` for the `_site/` DB, all under `tmp_path`.  Nothing here is random and
nothing is timed; the fixtures are three batches per leaf, which is what keeps a file that
opens five SQLite databases a unit test.

Run:  python -m pytest Tests/unit/test_site_analysis_stage.py -q
"""
from __future__ import annotations

import json
import logging
import os

import pytest

from Optimization import run_channel_rollup
from Optimization.Performance_Evaluations.core import families as fam
from Optimization.Performance_Evaluations.core import requests as _requests
from Optimization.Performance_Evaluations.core.context import SiteContext
from Optimization.persistence import Picking_Data as pdata
from Optimization.runschema import contract as _contract
from Optimization.runschema import schema as _schema
from Optimization.runschema.resolver import RunTree

#: Floats (spans, seconds) compare with a tolerance, never `==`.
_TOL = 1e-6

#: The two leaves' batch records, as `(batch_start_time, duration, recv_seconds)` per day.
#: The CLOCKS DELIBERATELY DIFFER: store starts first and fulfillment ends last, so the
#: site's span is strictly wider than either leaf's and neither leaf alone can produce it.
_LEAVES = {
    'store':       [(0.0, 900.0, 100.0), (1000.0, 900.0, 200.0), (2000.0, 900.0, 300.0)],
    'fulfillment': [(500.0, 800.0, 50.0), (1500.0, 800.0, 60.0), (3500.0, 800.0, 70.0)],
}

#: One batch per working day, and the SAME days on both leaves — which is true by
#: construction on a real coupled run, because `staffing` refuses to sum channels that ran
#: different batch counts.
_WORK_DAYS = (0, 1, 2)

_ARM_PAIR = 'uni_fifo__opt_lpt'
_PAIR = 'pairA'
_CELL = 'cellA'


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures — two real leaf sim DBs, one real site DB, and a SiteContext over them
# ══════════════════════════════════════════════════════════════════════════════

def _batch(batch_id: int, start: float, duration: float, recv_s: float):
    """One `BatchStats`, with only the fields the site denominators read set to anything.

    `total_items` and the percentages are filled because `_bdf` divides by `duration` and
    would hand back NaN columns otherwise — NaN is a legitimate frame value here, but a
    fixture that produced only NaNs could not tell a working sum from a missing one.
    """
    return pdata.BatchStats(
        run_id=0, batch_id=batch_id, duration=duration, num_tasks=2,
        total_items=40, avg_concurrent_pickers=1.0, picking_pct=0.5, traveling_pct=0.5,
        task_makespan=duration, batch_start_time=start, batch_end_time=start + duration,
        recv_seconds=recv_s, work_day=_WORK_DAYS[batch_id])


def _leaf_db(tmp_path, channel: str) -> tuple[str, int]:
    """One channel leaf's sim DB, written through the real writers."""
    path = str(tmp_path / f'sim_{channel}.db')
    pdata.init_run_db(path)
    run_id = pdata.create_run(path, 'comparison', {})
    pdata.save_batch_stats(path, run_id, [
        _batch(i, start, dur, recv) for i, (start, dur, recv) in enumerate(_LEAVES[channel])
    ])
    return path, run_id


def _site_db(tmp_path) -> tuple[str, int]:
    """The `_site/` inbound DB: a sim DB carrying only the two yard tables.

    Real rows, through `save_site_inbound`, because the context VERIFIES this file's schema
    identity before a single frame is read — an empty file would fail there rather than
    where a reader would look.
    """
    path = str(tmp_path / f'inbound_{_ARM_PAIR}.db')
    pdata.init_run_db(path)
    run_id = pdata.create_run(path, 'comparison', {})
    pdata.save_site_inbound(
        path, run_id,
        yard_trailers=[(0, 0.0, 10.0, 900.0, 'done'), (1, 1000.0, 1010.0, None, 'standing')],
        yard_drains=[(0, 2, 4, 1, 12), (1, 1, 4, 0, 0), (2, 0, 4, 0, 0)])
    return path, run_id


def _site_ctx(tmp_path) -> SiteContext:
    """A `SiteContext` over one arm pair, in exactly the shape `run_analysis._site_jobs`
    builds: the pair's own `db_path`/`run_id` plus a `leaves` list carrying each channel's
    leaf DB.  `assignment` is the STORE half's rule, which is how the reference pair is
    selected (the diagonal is by rank and the reference pair is fifo/fifo)."""
    site_path, site_run = _site_db(tmp_path)
    leaves = []
    for channel in _LEAVES:
        db, run_id = _leaf_db(tmp_path, channel)
        leaves.append({'key': f'{channel}_arm', 'db_path': db, 'run_id': run_id,
                       'channel': channel})
    pairs = [{'key': _ARM_PAIR, 'label': _ARM_PAIR, 'assignment': 'fifo',
              'db_path': site_path, 'run_id': site_run, 'leaves': leaves}]
    log = logging.getLogger('test.site')
    log.setLevel(logging.ERROR)
    return SiteContext(pairs, str(tmp_path / 'out'), {'inventory': _PAIR}, log)


def _span(df) -> float:
    """A frame's CALENDAR span in seconds — the door-utilization denominator's numerator,
    `min(batch_start_time)` to `max(start + duration)`, the same formula
    `yard/scorecard._arm_span_days` divides into days."""
    return float((df['batch_start_time'] + df['duration']).max()
                 - df['batch_start_time'].min())


# ══════════════════════════════════════════════════════════════════════════════
# 1. SiteContext.batch_df — both leaves, and the direction the fix matters in
# ══════════════════════════════════════════════════════════════════════════════

def test_the_site_batch_frame_is_both_leaves_concatenated(tmp_path):
    """One concat, and it answers both denominator questions with no new arithmetic."""
    ctx = _site_ctx(tmp_path)
    site = ctx.batch_df(_ARM_PAIR)
    want = sum(len(v) for v in _LEAVES.values())
    assert len(site) == want, (
        f'the site frame carries {len(site)} row(s); two leaves of {len(_LEAVES["store"])} '
        f'batches each are {want}')
    assert site is _requests.site_batch_frame(ctx, _ARM_PAIR), (
        'batch_df did not route through site_batch_frame, so the memoisation and the '
        'denominators have two implementations')


def test_the_site_span_is_the_union_and_exceeds_either_leaf_alone(tmp_path):
    """THE DIRECTION THE FIX MATTERS IN.

    A span taken from ONE leaf is a real number that renders a real figure; it is simply
    the wrong one.  The fixture's two clocks start and end at different instants, so the
    union is strictly wider than both — which is what makes this assertion able to fail
    against a `batch_df` that returned either leaf.
    """
    ctx = _site_ctx(tmp_path)
    site = _span(ctx.batch_df(_ARM_PAIR))
    leaves = {ch: _span(ctx.leaf_batch_df(_ARM_PAIR, ch)) for ch in _LEAVES}
    assert site > max(leaves.values()) + _TOL, (
        f'the site span {site!r} is not wider than every leaf span {leaves}; a door is '
        f'occupied on the SITE\'s calendar whatever channel\'s packs sit behind it')
    starts = [min(s for s, _d, _r in rows) for rows in _LEAVES.values()]
    ends = [max(s + d for s, d, _r in rows) for rows in _LEAVES.values()]
    assert abs(site - (max(ends) - min(starts))) < _TOL, (
        f'the site span {site!r} is not min(start)..max(start+duration) over both leaves '
        f'({max(ends) - min(starts)!r})')


def test_the_censoring_bound_becomes_the_site_end(tmp_path):
    """`_arm_end_s` reads THROUGH `ctx.batch_df`, and that indirection is the whole of what
    makes detention and fee honest at site scope: both are built against a censoring bound,
    and under one shared yard that bound has to be the site's end."""
    ctx = _site_ctx(tmp_path)
    got = _requests._arm_end_s(ctx, _ARM_PAIR)
    ends = {ch: max(s + d for s, d, _r in rows) for ch, rows in _LEAVES.items()}
    assert abs(got - max(ends.values())) < _TOL, (
        f'the censoring bound is {got!r}, not the site end {max(ends.values())!r} '
        f'(per leaf: {ends})')
    assert got > min(ends.values()) + _TOL, (
        'the fixture\'s two leaves end at the same instant, so this could not have failed')


def test_recv_seconds_sum_and_the_work_days_are_the_shared_count(tmp_path):
    """The receiver-busy read-out: both leaves' seconds over DISTINCT `work_day`.

    Still work days and never the calendar span — a calendar denominator over-reads the
    share by the ratio of the two and printed 184% once.  The two leaves share days by
    construction, so the site's day count is the leaf's, not their sum.
    """
    ctx = _site_ctx(tmp_path)
    site = ctx.batch_df(_ARM_PAIR)
    want = sum(r for rows in _LEAVES.values() for _s, _d, r in rows)
    assert abs(float(site['recv_seconds'].sum()) - want) < _TOL, (
        f'the site frame holds {float(site["recv_seconds"].sum())!r} receiving seconds, '
        f'not both leaves\' {want!r}')
    assert int(site['work_day'].nunique()) == len(_WORK_DAYS), (
        f'the site counted {int(site["work_day"].nunique())} distinct work day(s); one '
        f'batch is one SITE day, so two leaves over {len(_WORK_DAYS)} days is still '
        f'{len(_WORK_DAYS)}')
    for ch in _LEAVES:
        leaf = ctx.leaf_batch_df(_ARM_PAIR, ch)
        assert float(leaf['recv_seconds'].sum()) < want - _TOL, (
            f'{ch} alone already accounts for the whole site total; the sum proves nothing')


# ══════════════════════════════════════════════════════════════════════════════
# 2. leaf_batch_df — the per-channel share, and the channel it refuses
# ══════════════════════════════════════════════════════════════════════════════

def test_leaf_batch_df_returns_that_leaf_alone(tmp_path):
    """Printed BESIDE the site number and never instead of it."""
    ctx = _site_ctx(tmp_path)
    for ch, rows in _LEAVES.items():
        leaf = ctx.leaf_batch_df(_ARM_PAIR, ch)
        assert len(leaf) == len(rows), (
            f'{ch}\'s frame carries {len(leaf)} row(s), expected {len(rows)}')
        want = sum(r for _s, _d, r in rows)
        assert abs(float(leaf['recv_seconds'].sum()) - want) < _TOL, (
            f'{ch}\'s frame holds {float(leaf["recv_seconds"].sum())!r} receiving seconds, '
            f'not its own {want!r}')


def test_leaf_batch_df_refuses_a_channel_the_pair_has_no_leaf_for(tmp_path):
    """A silent miss would print one leaf's number under another channel's name, which is
    the shape of `a-right-site-total-hides-two-wrong-shares` at a smaller scale."""
    ctx = _site_ctx(tmp_path)
    with pytest.raises(KeyError, match='no .nosuch. leaf'):
        ctx.leaf_batch_df(_ARM_PAIR, 'nosuch')


def test_the_context_reports_the_channels_it_serves(tmp_path):
    """The declared order the per-channel shares are printed in — taken off the first arm
    pair, because every pair of one site has the same two leaves."""
    ctx = _site_ctx(tmp_path)
    assert ctx.channels == tuple(_LEAVES), (
        f'the site serves {ctx.channels!r}, not {tuple(_LEAVES)!r}')


# ══════════════════════════════════════════════════════════════════════════════
# 3. The rollup's refusal — the TYPE, and the ORDER
# ══════════════════════════════════════════════════════════════════════════════

def _run_tree(tmp_path, *, coupled: bool) -> str:
    """A run root carrying a real `run_layout.json`, and ONE EMPTY CELL inside it.

    Empty on purpose: the cell has no analyzed channel runs at all, so an uncoupled tree
    demonstrably returns `{}` here.  That is what turns the coupled case into evidence
    about ORDER — the refusal cannot be coming from anything the rollup read, because
    there is nothing to read.
    """
    root = tmp_path / 'run'
    cell = root / _CELL
    cell.mkdir(parents=True, exist_ok=True)
    layout = {'schema_id': _contract.head(), 'cells': [_CELL], 'coupled': coupled}
    (root / 'run_layout.json').write_text(json.dumps(layout), encoding='utf-8')
    return str(cell)


def test_an_uncoupled_empty_cell_returns_nothing_rather_than_raising(tmp_path):
    """THE NON-VACUITY GUARD for the refusal below: the same empty cell, uncoupled, is a
    no-op that reports itself and returns `{}`."""
    cell = _run_tree(tmp_path, coupled=False)
    assert run_channel_rollup.rollup(cell, log=lambda *_a, **_k: None) == {}, (
        'an uncoupled empty cell did something; the coupled refusal below would then not '
        'be evidence that it fired before any series doc was read')


def test_the_rollup_refuses_a_coupled_cell_before_reading_anything(tmp_path):
    """The rollup's validity argument IS channel independence: it takes the best plan per
    channel and SUMS the savings.  Under one site dock a store plan changes what the
    fulfillment crew can do and back, so the sum is not any warehouse number at all."""
    cell = _run_tree(tmp_path, coupled=True)
    with pytest.raises(ValueError, match='COUPLED run'):
        run_channel_rollup.rollup(cell, log=lambda *_a, **_k: None)


def test_the_refusal_is_a_valueerror_and_specifically_not_a_systemexit(tmp_path):
    """THE TYPE IS THE DECISION (site-dock 07 section 4).

    `analyze_run._step` catches `Exception` and `SystemExit` is not one, so a `SystemExit`
    here — the house style for a CLI shape refusal — would abort `analyze_run` mid-run from
    inside its cell loop and take every remaining cell's analysis with it.  Both halves are
    asserted: it IS an `Exception` (so `_step` catches it) and it is NOT a `SystemExit`.
    """
    cell = _run_tree(tmp_path, coupled=True)
    with pytest.raises(Exception) as excinfo:           # noqa: PT011 - the type IS the claim
        run_channel_rollup.rollup(cell, log=lambda *_a, **_k: None)
    exc = excinfo.value
    assert type(exc) is ValueError, (
        f'the rollup refused with a {type(exc).__name__}; the declared type is ValueError')
    assert isinstance(exc, Exception), 'analyze_run._step catches Exception and would miss this'
    assert not isinstance(exc, SystemExit), (
        'a SystemExit escapes `except Exception` and aborts analyze_run mid-run')


# ══════════════════════════════════════════════════════════════════════════════
# 4. The run tree: the site DB's declared template, inverted
# ══════════════════════════════════════════════════════════════════════════════

def _head_tree(tmp_path) -> RunTree:
    """A resolver over the CURRENT declaration — `contract.build()` rather than a committed
    document, so a template edit in `schema.py` is seen here immediately."""
    return RunTree(str(tmp_path), _contract.build(), layout={})


def test_arm_pair_of_inverts_the_declared_site_db_template(tmp_path):
    """The arm pair is ONE capture, not two: the store and fulfillment halves are joined by
    `__` inside the stem, and the PAIR is what a site evaluation is keyed by."""
    rt = _head_tree(tmp_path)
    path = rt.path('site_inbound_db', cell=_CELL, pair=_PAIR, strategy=_ARM_PAIR)
    assert '__' in _ARM_PAIR, 'the fixture arm pair has no join, so "one capture" is untested'
    assert rt.arm_pair_of(path) == _ARM_PAIR, (
        f'arm_pair_of({os.path.basename(path)!r}) = {rt.arm_pair_of(path)!r}, '
        f'expected {_ARM_PAIR!r}')


def test_site_inbound_dbs_finds_the_declared_file_and_nothing_else(tmp_path):
    """Through `glob`, so the `_site` segment, the `inbound_` prefix and the extension all
    live in ONE template.  A consumer spelling them again would be a second declaration to
    keep in step."""
    rt = _head_tree(tmp_path)
    path = rt.path('site_inbound_db', cell=_CELL, pair=_PAIR, strategy=_ARM_PAIR)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'wb').close()
    found = rt.site_inbound_dbs(_CELL, _PAIR)
    assert found == [os.path.abspath(path)], (
        f'site_inbound_dbs found {found!r}, expected exactly {[os.path.abspath(path)]!r}')
    assert rt.arm_pair_of(found[0]) == _ARM_PAIR


def test_site_inbound_dbs_is_empty_where_there_is_no_site_directory(tmp_path):
    """`[]` on an uncoupled run — which is the whole archive.  An absence, not an error:
    the site stage emits no job rather than reporting zeros."""
    rt = _head_tree(tmp_path)
    os.makedirs(os.path.join(str(tmp_path), _CELL, 'pairB'), exist_ok=True)
    assert rt.site_inbound_dbs(_CELL, 'pairB') == [], (
        'a pair with no _site directory produced site DBs')
    assert rt.site_inbound_dbs('no_such_cell', _PAIR) == []


# ══════════════════════════════════════════════════════════════════════════════
# 5. The duplicated tuple, and the tie that makes it safe
# ══════════════════════════════════════════════════════════════════════════════

def test_the_site_scope_families_are_declared_and_real():
    """`yard` is the family that moves, and every name in the tuple has to BE a family —
    a typo would declare a glob for a folder no evaluation writes into."""
    assert fam.SITE_SCOPE_FAMILIES == ('yard',), (
        f'the site-scope families are {fam.SITE_SCOPE_FAMILIES!r}')
    assert set(fam.SITE_SCOPE_FAMILIES) <= set(fam.FAMILIES), (
        f'{sorted(set(fam.SITE_SCOPE_FAMILIES) - set(fam.FAMILIES))} is not a chart family')


def test_the_contract_and_the_registry_declare_the_same_site_families():
    """THE TIE THAT MAKES THE DUPLICATION SAFE.

    `runschema/schema.py` imports nothing on purpose — the contract's `schema_id` must not
    depend on an analysis package — so the tuple is spelled twice.  A family added to one
    and not the other declares a path nothing produces, or produces output nothing declares,
    and both are preflight violations that appear only on a coupled run.
    """
    assert _schema._SITE_FIGURE_FAMILIES == fam.SITE_SCOPE_FAMILIES, (
        f'the contract declares site figure families {_schema._SITE_FIGURE_FAMILIES!r} '
        f'while the registry declares {fam.SITE_SCOPE_FAMILIES!r}')
    assert set(_schema._SITE_FIGURE_FAMILIES) <= set(_schema._SITE_FAMILY_EVALUATIONS), (
        'a site figure family with no evaluations attributed to it renders nothing')


if __name__ == '__main__':                                        # pragma: no cover
    import sys
    sys.exit(pytest.main([__file__, '-v']))


# ══════════════════════════════════════════════════════════════════════════════
# 6. `_site_jobs` over a REAL run tree — the shape `sim_meta` actually has
# ══════════════════════════════════════════════════════════════════════════════
#
# The stage's job builder reads `sim_meta['strategies']`, which is a list of DICTS
# (`workunits._prepare_channel_run` builds `dict(key=…, label=…, db_path=…, run_id=…)`) --
# the same shape `EvalContext` indexes by `s['key']`.  A first draft tested membership on
# the dict itself, which matches NOTHING: every leaf resolved zero arms, every pair was
# skipped, and the stage emitted zero jobs while logging the line an UNCOUPLED run logs.
# `a-grant-is-not-an-output` exactly -- so the tree below is built in the real shape and
# the assertion is that a job comes out with BOTH leaves on it.

def _two_leaf_tree(tmp_path, *, coupled: bool = True):
    """A minimal two-leaf run tree: `<cell>/<pair>/<config>/<channel>/` with a sim DB, a
    sim_meta and a series doc per leaf, plus the pair's `_site/` inbound DB."""
    root = tmp_path / 'comparison_site'
    arms = {'store': 'uni_fifo', 'fulfillment': 'opt_lpt'}
    for channel, arm in arms.items():
        leaf = root / _CELL / _PAIR / f'cfg_{channel}' / channel
        leaf.mkdir(parents=True)
        db = str(leaf / f'sim_{arm}.db')
        pdata.init_run_db(db)
        run_id = pdata.create_run(db, 'comparison', {}, identity={'strategy_key': arm})
        pdata.save_batch_stats(db, run_id, [
            _batch(i, start, dur, recv)
            for i, (start, dur, recv) in enumerate(_LEAVES[channel])])
        (leaf / 'sim_meta.json').write_text(json.dumps({
            'name': f'{_PAIR}/{channel}', 'run_dir': str(leaf), 'inventory': _PAIR,
            'channel': channel,
            # THE REAL SHAPE: a list of dicts, not of keys.
            'strategies': [{'key': arm, 'label': arm, 'db_path': db, 'run_id': run_id}],
        }), encoding='utf-8')
        (leaf / 'series.json').write_text('{"strategies": []}', encoding='utf-8')
    site_dir = root / _CELL / _PAIR / '_site'
    site_dir.mkdir(parents=True)
    pair_stem = f"{arms['store']}__{arms['fulfillment']}"
    site_db = str(site_dir / f'inbound_{pair_stem}.db')
    pdata.init_run_db(site_db)
    site_run = pdata.create_run(site_db, 'site', {}, identity={'strategy_key': pair_stem})
    pdata.save_site_inbound(site_db, site_run,
                            yard_trailers=[(0, 0.0, 10.0, 900.0, 'done')],
                            yard_drains=[(0, 2, 4, 1, 12)])
    # `channels` is the DECLARED channel order and `write_run_layout` writes it on every
    # run: it is the order `workunits._site_db_path` builds the arm-pair stem in, and the
    # stage resolves the two halves positionally against it (site-dock 25). A fixture that
    # omitted it was describing a descriptor no run produces.
    (root / 'run_layout.json').write_text(json.dumps({
        'schema_id': _contract.head(), 'coupled': coupled,
        'channels': ['store', 'fulfillment'],
        'cells': [_CELL], 'axes': {}}), encoding='utf-8')
    return root, pair_stem, arms


def _jobs(root):
    from Optimization import run_analysis as ra
    from Optimization.runschema import resolver_for
    log = logging.getLogger('test.site.jobs'); log.setLevel(logging.ERROR)
    rt = resolver_for(str(root))
    return ra._site_jobs(str(root / _CELL), rt, _CELL, 'BY_INITIAL', 'config', {}, log)


def test_the_site_stage_emits_one_job_per_pair_with_both_leaves_on_it(tmp_path):
    """The whole stage in one assertion: a coupled pair produces a job, and the job carries
    BOTH leaves -- which is what every site denominator is built from."""
    root, pair_stem, arms = _two_leaf_tree(tmp_path)
    jobs = _jobs(root)
    assert len(jobs) == 1, f'the site stage emitted {len(jobs)} job(s) for one coupled pair'
    job = jobs[0]
    assert job['stage'] == 'site'
    assert job['out_dir'].endswith(os.path.join(_PAIR, '_site'))
    assert len(job['pairs']) == 1
    pair = job['pairs'][0]
    assert pair['key'] == pair_stem
    assert {lf['channel'] for lf in pair['leaves']} == {'store', 'fulfillment'}
    assert {lf['key'] for lf in pair['leaves']} == set(arms.values())
    for lf in pair['leaves']:
        assert os.path.exists(lf['db_path']) and lf['run_id'] is not None


def test_a_site_job_resolves_each_leafs_own_arm_and_not_the_other_half(tmp_path):
    """Each leaf must contribute the arm IT ran, on a pair whose halves are named
    DIFFERENTLY -- which is every pair on a real funnel run.

    Half k belongs to channel k of the layout's declared `channels`, which is the order the
    stem was built in; membership in the leaf's own recorded arm list then decides whether
    that arm was actually run.  Getting it the other way round -- letting membership decide
    WHICH channel -- is what site-dock 25 found: both channels draw restock rules from one
    vocabulary, so a leaf holds arms named like both halves and nothing resolves at all."""
    root, _stem, arms = _two_leaf_tree(tmp_path)
    pair = _jobs(root)[0]['pairs'][0]
    by_channel = {lf['channel']: lf['key'] for lf in pair['leaves']}
    assert by_channel == arms, by_channel


def test_a_leaf_that_also_ran_the_other_halfs_arm_still_resolves(tmp_path):
    """THE DEFECT SITE-DOCK 25 FOUND, planted: the store leaf ALSO ran the fulfillment
    half's rule, which is the normal case on a campaign where both channels draw from one
    17-rule vocabulary.  Membership alone matches the store leaf to both names and the pair
    is skipped with a warning; the whole site stage then runs on nothing."""
    root, _stem, arms = _two_leaf_tree(tmp_path)
    leaf = root / _CELL / _PAIR / 'cfg_store' / 'store'
    meta = json.loads((leaf / 'sim_meta.json').read_text(encoding='utf-8'))
    meta['strategies'].append({'key': arms['fulfillment'], 'label': arms['fulfillment'],
                               'db_path': '', 'run_id': 1})
    (leaf / 'sim_meta.json').write_text(json.dumps(meta), encoding='utf-8')
    pair = _jobs(root)[0]['pairs'][0]
    assert {lf['channel']: lf['key'] for lf in pair['leaves']} == arms


def test_a_layout_with_no_declared_channel_order_resolves_nothing(tmp_path):
    """`write_run_layout` writes `channels` on every run, so this is unreachable in
    production -- and it is a REFUSAL rather than a fallback for that reason. A fallback
    would have to guess which half is which channel, and a wrong guess reads every site
    denominator off the other channel's leaf with nothing to say so.

    THE ARMS ARE SWAPPED INTO THE ALPHABETICAL ORDER on purpose: each leaf is given the
    OTHER half's key, so a fallback that guessed alphabetically ('fulfillment' < 'store')
    would resolve this tree happily. Against a tree where the guess fails anyway, this test
    would pass over the fallback and prove nothing."""
    root, _stem, arms = _two_leaf_tree(tmp_path)
    for channel, other in (('store', 'fulfillment'), ('fulfillment', 'store')):
        leaf = root / _CELL / _PAIR / f'cfg_{channel}' / channel
        meta = json.loads((leaf / 'sim_meta.json').read_text(encoding='utf-8'))
        meta['strategies'] = [{**meta['strategies'][0], 'key': arms[other]}]
        (leaf / 'sim_meta.json').write_text(json.dumps(meta), encoding='utf-8')
    layout = json.loads((root / 'run_layout.json').read_text(encoding='utf-8'))
    del layout['channels']
    (root / 'run_layout.json').write_text(json.dumps(layout), encoding='utf-8')
    assert _jobs(root) == []


def test_a_pair_whose_fulfillment_half_was_never_run_emits_no_job(tmp_path):
    """BOTH LEAVES OR NEITHER. One half present is not half a site denominator, it is a
    denominator missing one channel's whole load — which renders as a utilisation figure
    that looks ordinary and is wrong by the size of the other channel."""
    root, _stem, _arms = _two_leaf_tree(tmp_path)
    leaf = root / _CELL / _PAIR / 'cfg_fulfillment' / 'fulfillment'
    meta = json.loads((leaf / 'sim_meta.json').read_text(encoding='utf-8'))
    meta['strategies'] = [{**meta['strategies'][0], 'key': 'some_other_arm'}]
    (leaf / 'sim_meta.json').write_text(json.dumps(meta), encoding='utf-8')
    assert _jobs(root) == []


def test_an_uncoupled_tree_with_no_site_directory_emits_no_job(tmp_path):
    """Non-vacuity for the assertion above, and the byte-identity half: the whole archive is
    uncoupled, and the stage must cost it nothing."""
    root, _stem, _arms = _two_leaf_tree(tmp_path, coupled=False)
    import shutil
    shutil.rmtree(root / _CELL / _PAIR / '_site')
    assert _jobs(root) == []

# ══════════════════════════════════════════════════════════════════════════════
# 7. the era gate — a SUBCLASS is not exempt from it
# ══════════════════════════════════════════════════════════════════════════════

def test_the_site_context_answers_the_capability_probe(tmp_path):
    """THE DEFECT THIS PINS was invisible to every other test here and to a code review, and
    was found only by running the stage end to end on a real coupled tree.

    `requests.era_shortfall` hasattr-checks `capabilities()` and skips the gate when there is
    none.  `SiteContext` SUBCLASSES `EvalContext`, so the method is inherited and the check
    finds it — while an earlier draft left its `_caps` cache uninitialised.  Every yard
    evaluation that declares a quantity (three of the four) then raised `AttributeError`
    inside the driver's swallow, rendered nothing, and left the `[access]` summary reporting
    a GRANT: `a-grant-is-not-an-output` in its purest form.

    The probe reads the SITE DB, which is the right file to ask: a site capability is what
    the site's own record carries.  `yard` is present there by construction — the file exists
    because trailers were unloaded — which is what makes this assertion non-vacuous.
    """
    ctx = _site_ctx(tmp_path)
    caps = ctx.capabilities()
    assert 'yard' in caps, (
        f'the site DB probed {sorted(caps)}; the yard tables ARE its whole content, so a '
        f'probe that cannot see them is reading the wrong file')
    assert ctx.capabilities() is caps, 'the probe is memoised, like every other context'
