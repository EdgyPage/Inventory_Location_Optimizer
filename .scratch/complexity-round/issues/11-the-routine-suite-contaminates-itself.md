# The routine suite contaminates itself, and every tier passes alone

Type: research
Status: resolved
Label: needs-decision

Hit while validating the selection heap. **Not caused by it** -- the mechanism reproduces
identically on a `git archive` copy of HEAD -- but it cost two 25-minute suite runs to establish
that, and it makes `python -m pytest Tests/ -q -k "not gpu"` unable to answer the only question a
gate exists to answer.

## The symptom

| run | result |
|---|---|
| `Tests/unit` alone | 2,611 passed |
| `Tests/unit/{test_cli_surface,test_batch_sampler_v2,test_config_reaches_the_worker}.py` alone | 38 passed |
| `Tests/e2e` + those three files | **6 failed, 6 errors** |
| the full routine suite | 13 failed, 6 errors |

## The mechanism, reproduced in three lines

`run_analysis._apply_run_shape` restores a run's shape onto the LIVE `CONFIG`, and writes three
keys **unconditionally**:

    g['sampler']          = spec.get('sampler') or 'v1'
    g['work_day_seconds'] = spec.get('work_day_seconds')     # None when absent
    g['releases_per_day'] = spec.get('releases_per_day')     # None when absent

Each line is CORRECT for its purpose and says so in its own comment: a pre-field run_spec predates
the field, so its absence means that run's value, "never this checkout's default". The defect is
only that nothing puts `CONFIG` back.

    >>> json.dump({'n_batches': 3}, open(d + '/run_spec.json', 'w'))   # a pre-field spec
    >>> ra._apply_run_shape(d, log)
    sampler: 'v3' -> 'v1'
    >>> rs._build_parser()
    TypeError: unsupported format string passed to NoneType.__format__

Both symptom families in one shot: the sampler flip fails
`test_config_sampler_reaches_both_production_construction_sites` (which asserts `== 'v3'`), and
the Nones make a help-text f-string with a format spec raise, which is the six `test_cli_surface`
ERRORS. **Identical output on the control**, so it is HEAD's behaviour, not this round's.

## It was already known, in a place nobody reads

`Tests/calltree/calltree_inbound_ladder.py`'s docstring:

> This is not a theoretical worry. A full-suite run this week failed on exactly this shape:
> `run_analysis._apply_run_shape` writes `CONFIG['global']['sampler']` with no restore, so one
> e2e test broke a unit test twelve minutes later, and every tier passed when run on its own.
> A subprocess is the cheap, total fix -- the OS restores the global state for free.

That is the whole finding, written down, naming the function and the key -- inside a **hand-run
CLI that is in no gate**, as a justification for why that CLI shells out. Nothing connects it to
the suite it describes. Two suite runs were spent rediscovering it.

## Why it matters more than 13 red lines

CLAUDE.md calls `pytest Tests/ -q -k "not gpu"` **the routine suite**. It currently reports
failures that cannot be attributed without building a control tree, and a suite whose red is
unattributable trains the reader to skip it -- the same dynamic as the ALL-ZERO warning this
effort fixed two tickets ago, one level up.

## The fix, NOT taken here -- it needs a decision

The in-process equivalent of the subprocess fix is an autouse fixture in `Tests/conftest.py` that
snapshots `CONFIG['global']` and `CONFIG['channels']` and restores them after every test.
`Tests/unit/test_config_reaches_the_worker.py`'s own `pristine_config` is exactly that fixture,
already written, already correct -- it just applies to one file.

It is not taken here because it changes the environment of **every test in the repo**, and a test
that silently depends on a predecessor's mutation would start failing. Those failures would be
findings rather than regressions, but discovering them is its own piece of work and belongs to
whoever decides to spend it.

**Recorded, with the reproduction, so the next person to see 13 red lines does not spend two
suite runs on it.**
