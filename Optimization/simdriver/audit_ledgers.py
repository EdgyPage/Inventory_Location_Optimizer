"""audit_ledgers.py — the two run-long audits an arm keeps, and their two report-once rules.

WHAT IT REPLACES.  `strategy_runner._build_leaf` carried these as four closure variables:
`cons_picked`, `cons_breaks`, `cons_residual` (the conservation ledger) and `demand_breaks`
(the demand ledger).

# ── what each audit proves ────────────────────────────────────────────────────────

CONSERVATION is the runtime proof that the bin-mutation log is COMPLETE.  Over the arm,

    units placed - units evicted - units picked  ==  units sitting in bins right now

and the right-hand side is read from the WAREHOUSE, not from any counter the recorder keeps,
so a mutation that bypasses the log breaks it -- which is the entire point.  It is asserted
cumulatively rather than per-batch because the cumulative form needs no special case for the
first measured batch (a resumed arm re-stocks from empty) and localises a break just as well.

DEMAND is the proof that no pick exceeded the demand that asked for it.  Its docstring in the
runner records what it would have caught: both picker loops read the per-AISLE
`task.items[sku]` once per bin, so a SKU in several bins of one aisle was picked once per
bin, inflating every throughput figure by ~6.7% for as long as the model had existed.

NEITHER EVER RAISES.  A residual can move for a benign scheduling coincidence -- two pickers
contending for one bin drain slightly differently than the depletion applied -- and turning
that into a run-killer would be strictly worse than reporting it.

# ── the two report-once rules, which are NOT the same rule ────────────────────────

Both suppress repeats, because ~100 identical log lines is how a real defect gets scrolled
past.  They suppress them differently, and this object exists to make that difference
addressable:

    `observe()`           reports when the residual MOVES.  The ledger is cumulative, so one
                          bad batch leaves a residual forever; only a batch that moves it
                          INTRODUCED anything.  Movement in EITHER direction counts -- a
                          ledger that reported only growth would go quiet exactly when a
                          second defect cancelled the first.
    `note_demand_break()` reports only the FIRST.  There is no residual here, so "movement"
                          has no meaning: the first line is the diagnosis and the count is
                          the summary.

# ── what stays in the runner ──────────────────────────────────────────────────────

THE LOG WORDING.  This object decides WHETHER a batch should be reported and hands back the
drift; `strategy_runner` owns the messages, which name files and columns that a unit test
should not be pinned to.

Slotted and picklable, like the other `_build_leaf` state objects.
"""
from __future__ import annotations


class AuditLedgers:
    """The conservation ledger and the demand ledger, with their report-once rules.

        audit = AuditLedgers()
        audit.note_picked(sum(p.quantity for p in picks_b))
        broke = audit.observe(placed=..., evicted=..., occupancy=...)
        if broke is not None:
            drift, residual = broke
            log.error(...)
        ...
        if bs.total_items > bs.items_demanded and audit.note_demand_break():
            log.error(...)
        ...
        result.update(audit.totals())
    """

    __slots__ = ('picked', 'cons_breaks', 'residual', 'demand_breaks')

    def __init__(self) -> None:
        self.picked = 0          # units picked, cumulative over the arm
        self.cons_breaks = 0     # batches that INTRODUCED an unaccounted-for unit
        self.residual = 0        # last observed (ledger - occupancy)
        self.demand_breaks = 0   # batches that picked MORE than was demanded

    def __repr__(self) -> str:
        return (f'<AuditLedgers picked={self.picked:,} cons_breaks={self.cons_breaks} '
                f'residual={self.residual:+,} demand_breaks={self.demand_breaks}>')

    # ── the conservation ledger ───────────────────────────────────────────────────

    def note_picked(self, units: int) -> None:
        """Add this batch's picked units to the cumulative term."""
        self.picked += units

    def observe(self, *, placed: int, evicted: int, occupancy: int):
        """Test the ledger against the warehouse.  Returns `(drift, residual)` if this batch
        INTRODUCED a break, else None.

        `drift` is INCREMENTAL -- how far the residual moved this batch -- and is computed
        before the new residual is stored, so the caller can report "new drift X; cumulative
        Y" exactly as the runner always did.  A residual that has not moved returns None and
        does not count a second break.
        """
        residual = (placed - evicted - self.picked) - occupancy
        if residual == self.residual:
            return None
        drift = residual - self.residual
        self.cons_breaks += 1
        self.residual = residual
        return drift, residual

    # ── the demand ledger ─────────────────────────────────────────────────────────

    def note_demand_break(self) -> bool:
        """Count a batch that picked more than was demanded.  True only for the FIRST.

        Every break is counted; only the first is worth a line, because there is no residual
        whose movement could distinguish a later one.
        """
        self.demand_breaks += 1
        return self.demand_breaks == 1

    # ── the result payload ────────────────────────────────────────────────────────

    def totals(self) -> dict:
        """The audit half of the worker's result dict.

        Real zeroes on a clean arm, never None: `cons_breaks == 0` means MEASURED AND ZERO,
        and a NULL there would read as "not measured" -- which is the opposite claim.
        """
        return {'cons_breaks': self.cons_breaks,
                'cons_residual': self.residual,
                'demand_breaks': self.demand_breaks}
