"""Experiment definition for `run_simulation --whatif`: an aisle-reconstruction × velocity-zoning ×
picker-scheduler matrix.

Editing + committing this file is how a what-if sweep is DEFINED — the same way the strategies sweep
lives in `strategies.CHANNEL_RESTOCKS` and the base warehouse knobs live in `sim_config.CONFIG`.
`run_simulation` consumes `WHATIF`; `--whatif` is just the flag that turns it on.  The matrix
simulates the SAME frozen inventory + SAME batch stream across every cell, so cells differ ONLY in
the swept knobs — an apples-to-apples comparison.

Cells are the combinatorial product schedulers × zoning × ks × losses (loss collapses to 0 when
k == 1), named `k{k}[_l{loss%}]_{zone}[_{sched}]`.  The scheduler suffix (`_rr` / `_lpt`) is added
only when more than one scheduler is swept; the (k=1, off, round_robin) cell is the reference.

CURRENT SCENARIO — picker-scheduler A/B: hold the layout fixed (no split, no zoning) and sweep the
task→picker scheduler `round_robin` vs `lpt` across the full assignment-function suite, to measure the
throughput (makespan) gain of load-balancing at unchanged total labor.  To sweep layout too, re-add
values to `ks`/`losses`/`zoning`.
"""

WHATIF = {
    'ks':     [1],               # aisle_split segment counts (1 = no split); fixed to isolate scheduler
    'losses': [0.0],             # capacity_loss fractions per cut, applied ONLY when k > 1
    'zoning': [                  # (cell-name-suffix, velocity_zoning spec); off to isolate scheduler
        ('off', {'enabled': False}),
    ],
    # Task→picker scheduler axis: 'round_robin' (legacy i%num_pickers) vs 'lpt' (load-balance
    # makespan).  A single value = no name suffix (byte-identical to no axis); >1 value sweeps it.
    'schedulers': ['round_robin', 'lpt'],
    # Assignment-function (restock) arms to sweep in every cell.  'all' = the full suite
    # (CHANNEL_RESTOCKS = None); a list/tuple of restock keys = a subset; None = leave
    # strategies.CHANNEL_RESTOCKS exactly as already committed (don't override it here).
    'arms': 'all',
    'reference': 'k1_off_rr',    # round-robin baseline; run_whatif_delta diffs k1_off_lpt against it
}


# ── spec registry ────────────────────────────────────────────────────────────────
# Every run is a CELL matrix; a plain run is the single-cell spec ``single`` (cell ``k1_off``).
# ``run_simulation --spec <name>`` selects one.  This lightweight dict registry is the Phase-1
# seam; a later phase promotes it to a self-registering package like Performance_Evaluations.
SPECS = {
    # Plain run: one cell, no split/zoning, round-robin scheduler.  arms=None ⇒ leave
    # strategies.CHANNEL_RESTOCKS exactly as committed (the normal per-channel subset), so a
    # `--spec single` run is the old flat run nested under a single `k1_off` cell.
    'single': {
        'ks': [1], 'losses': [0.0], 'zoning': [('off', {'enabled': False})],
        'schedulers': ['round_robin'], 'arms': None, 'reference': 'k1_off',
    },
    # The committed picker-scheduler A/B sweep (round_robin vs lpt over the full arm suite).
    'scheduler_ab': WHATIF,
}


def get_spec(name):
    """Return the cell-matrix spec dict for a registered name (KeyError if unknown)."""
    return SPECS[name]
