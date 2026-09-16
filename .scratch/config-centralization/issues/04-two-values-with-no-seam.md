# Two values that no seam can reach

Type: task
Status: ready-for-agent

`settings.py` documents a five-seam path from a declared name to a spawned worker, and
`Tests/unit/test_run_shaping_params.py` checks it per knob. Two real modelling parameters sit
outside it entirely -- they cannot be set, are not recorded in `run_spec.json`, and do not move
the run-tree source fingerprint.

## 1. `_OVERSTOCK_MIN_HEADROOM = 10` -- `Optimization/simdriver/strategy_runner.py:44`

Empty bins kept per bucket at overstock fill. A modelling choice with a real effect on placement
pressure, living as a module-level literal INSIDE the worker. Same shape as the `n_bands` fallback
`config-centralization` ticket 01 found one line away in the same file: a value the five seams
cannot see, in the one process nothing reads back from.

## 2. Aisle geometry `(50, 10)` -- `Optimization/config/sim_config.py:75-76`

    _AISLE_W = aisle_width_for(50)
    _AISLE_H = aisle_height_for(10)

Fifty columns and ten levels: the reference warehouse's shape, as two bare integers with no
`settings.py` name and no CLI flag. Every run ever published used them, and nothing records that
they were a choice.

## What landing this means

A `settings.py` name each, threaded through seams 2-5 (`CONFIG` entry + accessor, CLI flag,
`run_spec` write and BOTH restore sites, worker payload). `Tests/unit/test_run_shaping_params.py`
is the per-knob template; `test_config_reaches_the_worker.py` covers seam 5 generically and needs
no edit.

## The judgement call, stated rather than assumed

Threading a knob is a small FEATURE, not a refactor: it adds a way to vary something that today
cannot be varied. The argument for doing it anyway is that both values are already varying
silently in the sense that matters -- nothing records them, so a run's own spec cannot answer
"what aisle geometry was this?" without reading the source at that commit.

The argument against doing it NOW is that `--max-skus` / `--coverage-days` already exist as the
supported ways to resize a run, and a new knob is a new surface to support. Left open
deliberately: this is the user's call, not a cleanup.
