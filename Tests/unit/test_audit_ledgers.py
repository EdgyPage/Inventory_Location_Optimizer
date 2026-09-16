"""test_audit_ledgers.py — the two run-long audits and their two different report-once rules.

`_build_leaf` carried these as four closure variables: `cons_picked`, `cons_breaks`,
`cons_residual` (the conservation ledger) and `demand_breaks` (the demand ledger). Both
accumulate over an arm, both count breaks, both report to the log, and NEITHER ever raises --
a broken ledger is reported, not fatal, because turning a scheduling coincidence into a
run-killer would be strictly worse than reporting it.

# ── the two report-once rules, which are NOT the same rule ────────────────────────

Each ledger suppresses repeats, and they do it differently. Getting either wrong produces
~100 identical log lines, which is how a real defect gets scrolled past:

  CONSERVATION reports when the residual MOVES. The ledger is cumulative, so one bad batch
  leaves a residual that persists forever; reporting every batch after the first would be
  ~100 identical lines, while reporting only on movement names exactly the batches that
  INTRODUCED unaccounted units.

  DEMAND reports only the FIRST break. There is no cumulative residual to move, so
  "movement" has no meaning here -- the count is the summary and the first line is the
  diagnosis.

Those two rules were four lines of arithmetic inside a 600-line function and nothing could
address them. They are the reason this cluster is worth a seam at all: the counts reach the
result dict and the DB, so a wrong rule is not just noise -- it is a wrong `cons_breaks`.

# ── what stays in the runner ──────────────────────────────────────────────────────

The LOG MESSAGES. This object decides WHETHER a batch should be reported and what the drift
was; `strategy_runner` owns the wording, which names files and columns a test should not be
pinned to.
"""
#: NO sys.path bootstrap here: `Tests/conftest.py` puts the repo root on the path for
#: the whole suite, and CLAUDE.md names it and entry-script bootstraps as the only
#: legal `sys.path.insert` sites.
import pickle

import pytest

from Optimization.simdriver.audit_ledgers import AuditLedgers


# ── the conservation ledger ───────────────────────────────────────────────────────

def test_a_fresh_ledger_is_clean():
    a = AuditLedgers()
    assert a.picked == 0
    assert a.cons_breaks == 0
    assert a.residual == 0
    assert a.demand_breaks == 0


def test_a_balanced_batch_reports_nothing():
    """placed - evicted - picked == occupancy is the invariant; no report, no break."""
    a = AuditLedgers()
    a.note_picked(30)
    assert a.observe(placed=100, evicted=20, occupancy=50) is None
    assert a.cons_breaks == 0
    assert a.residual == 0


def test_the_first_unaccounted_unit_is_reported_with_its_drift():
    a = AuditLedgers()
    a.note_picked(30)
    got = a.observe(placed=100, evicted=20, occupancy=45)     # 5 units unaccounted for
    assert got is not None
    drift, residual = got
    assert residual == 5
    assert drift == 5, 'the first break drifts from zero'
    assert a.cons_breaks == 1
    assert a.residual == 5


def test_a_PERSISTING_residual_is_not_reported_again():
    """THE RULE. The ledger is cumulative, so a bad batch leaves a residual forever. Only a
    batch that MOVES it introduced anything, and only those are worth a line."""
    a = AuditLedgers()
    a.note_picked(30)
    assert a.observe(placed=100, evicted=20, occupancy=45) is not None   # break
    for _ in range(5):
        assert a.observe(placed=100, evicted=20, occupancy=45) is None, \
            'an unchanged residual must not report again'
    assert a.cons_breaks == 1, 'a persisting residual is ONE break, not six'


def test_a_residual_that_moves_again_is_a_second_break_with_the_incremental_drift():
    a = AuditLedgers()
    a.note_picked(30)
    a.observe(placed=100, evicted=20, occupancy=45)            # residual 5
    drift, residual = a.observe(placed=100, evicted=20, occupancy=42)   # residual 8
    assert residual == 8
    assert drift == 3, 'the drift is INCREMENTAL, not cumulative'
    assert a.cons_breaks == 2


def test_a_residual_that_moves_back_toward_zero_still_counts_as_a_break():
    """Movement in either direction introduced something. A ledger that only reported growth
    would go quiet exactly when a second defect cancelled the first."""
    a = AuditLedgers()
    a.note_picked(30)
    a.observe(placed=100, evicted=20, occupancy=45)            # residual 5
    got = a.observe(placed=100, evicted=20, occupancy=50)      # residual 0 again
    assert got is not None
    drift, residual = got
    assert residual == 0 and drift == -5
    assert a.cons_breaks == 2


def test_picked_accumulates_over_the_arm_and_feeds_the_residual():
    a = AuditLedgers()
    a.note_picked(10)
    a.note_picked(15)
    assert a.picked == 25
    # placed 100 - evicted 20 - picked 25 = 55; bins hold 55 -> balanced
    assert a.observe(placed=100, evicted=20, occupancy=55) is None


# ── the demand ledger ─────────────────────────────────────────────────────────────

def test_the_first_demand_break_reports_and_later_ones_do_not():
    """THE OTHER RULE, and deliberately not the conservation one: there is no residual to
    move here, so the first line is the diagnosis and the count is the summary."""
    a = AuditLedgers()
    assert a.note_demand_break() is True,  'the first break must report'
    for _ in range(4):
        assert a.note_demand_break() is False, 'later breaks must stay silent'
    assert a.demand_breaks == 5, 'every break is COUNTED even though only one reported'


def test_the_two_ledgers_are_independent():
    a = AuditLedgers()
    a.note_demand_break()
    a.note_picked(5)
    assert a.cons_breaks == 0, 'a demand break is not a conservation break'
    a.observe(placed=10, evicted=0, occupancy=0)               # residual 5 -> break
    assert a.cons_breaks == 1
    assert a.demand_breaks == 1


# ── the result payload ────────────────────────────────────────────────────────────

def test_totals_renders_the_result_dict_keys():
    """`_finish` builds its result dict from this, so the keys ARE the payload's keys and
    the DB columns downstream."""
    a = AuditLedgers()
    a.note_picked(7)
    a.observe(placed=10, evicted=0, occupancy=0)
    a.note_demand_break()
    out = a.totals()
    assert out == {'cons_breaks': 1, 'cons_residual': 3, 'demand_breaks': 1}


def test_the_totals_payload_is_a_plain_picklable_dict():
    a = AuditLedgers()
    out = a.totals()
    assert type(out) is dict
    assert all(type(v) is int for v in out.values())
    assert pickle.loads(pickle.dumps(out)) == out


def test_a_clean_arm_reports_zeroes_not_nulls():
    """A clean arm must write real zeroes: NULL would be read as 'not measured', and the
    whole point of `cons_breaks == 0` is that it was measured and was zero."""
    assert AuditLedgers().totals() == {'cons_breaks': 0, 'cons_residual': 0,
                                       'demand_breaks': 0}


# ── it never raises ───────────────────────────────────────────────────────────────

def test_a_broken_ledger_is_reported_and_never_raises():
    """The documented decision: a residual can move for a benign scheduling coincidence (two
    pickers contending for one bin), so this reports and counts -- it must not kill a run."""
    a = AuditLedgers()
    for occ in (0, -5, 10 ** 9):
        a.observe(placed=0, evicted=0, occupancy=occ)          # nothing raises
    assert a.cons_breaks >= 1


# ── the object itself ─────────────────────────────────────────────────────────────

def test_it_is_slotted_so_a_typo_cannot_shadow_a_counter():
    a = AuditLedgers()
    with pytest.raises(AttributeError):
        a.cons_break = 1


def test_it_survives_the_worker_process_boundary():
    a = AuditLedgers()
    a.note_picked(4)
    a.observe(placed=9, evicted=0, occupancy=0)
    a.note_demand_break()
    back = pickle.loads(pickle.dumps(a))
    assert back.totals() == a.totals()
    assert back.picked == 4
