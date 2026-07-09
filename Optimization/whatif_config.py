"""Experiment definition for `run_simulation --whatif`: the aisle-reconstruction × velocity-zoning
matrix.

Editing + committing this file is how a what-if sweep is DEFINED — the same way the strategies sweep
lives in `strategies.CHANNEL_RESTOCKS` and the base warehouse knobs live in `sim_config.CONFIG`.
`run_simulation` consumes `WHATIF`; `--whatif` is just the flag that turns it on.  The matrix
simulates the SAME frozen inventory + SAME batch stream across every cell, so cells differ ONLY in
layout (aisle_split) and velocity zoning — an apples-to-apples layout comparison.

Cells are the combinatorial product ks × losses × zoning (loss collapses to 0 when k == 1), named
`k{k}[_l{loss%}]_{zone-name}`; the (k=1, off) cell `k1_off` is the natural reference.
"""

WHATIF = {
    'ks':     [1, 2],            # aisle_split segment counts (1 = no split, i.e. aisle_split=None)
    'losses': [0.0, 0.10],       # capacity_loss fractions per cut, applied ONLY when k > 1
    'zoning': [                  # (cell-name-suffix, velocity_zoning spec applied to every channel)
        ('off',  {'enabled': False}),
        ('abc2', {'enabled': True, 'mode': 'abc', 'n_bands': 2, 'abc': {'mass_thresholds': [0.6]}}),
        ('abc3', {'enabled': True, 'mode': 'abc', 'n_bands': 3, 'abc': {'mass_thresholds': [0.6, 0.9]}}),
    ],
    # Assignment-function (restock) arms to sweep in every cell.  'all' = the full suite
    # (CHANNEL_RESTOCKS = None); a list/tuple of restock keys = a subset; None = leave
    # strategies.CHANNEL_RESTOCKS exactly as already committed (don't override it here).
    'arms': 'all',
    'reference': 'k1_off',       # the cell run_whatif_delta diffs every other cell against
}
