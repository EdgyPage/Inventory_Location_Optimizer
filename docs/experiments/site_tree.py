"""site_tree.py — the staged docs tree, declared as data.

The website's file layout under ``docs/experiments/<exp>/`` is a six-template contract with
one writer (ingest.py, which copies files IN) and one reader (docs/macros.py, which renders
paths OUT at mkdocs build time).  Both sides historically retyped the joins; this module is
the single declaration, rendered through the same ``Schema.pathtpl`` vocabulary as the run
tree and the profiles tree, so a site path can only move by editing a template here — where
a golden test and the committed snapshots will notice.

macros.py deliberately does NOT import this module: it runs inside mkdocs (mkdocs-macros
loads it standalone, and the site must build from a bare checkout with no guarantee about
sys.path).  Its f-string joins stay, PINNED to these templates by
``Tests/architecture/test_site_tree.py`` — the same literal-equals-render tie the ingest
destinations get by construction.

All paths are RELATIVE to the experiment dir (``docs/experiments/<exp>/``); ingest joins them
onto its ``exp_dir``, macros onto the site root.  ``{figure}``/``{plot}``/``{fname}`` are
basenames from the curated lists in ``experiment.yml``.
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
if _REPO_ROOT not in sys.path:                       # entry-script bootstrap (ingest imports us)
    sys.path.insert(0, _REPO_ROOT)

from Schema.pathtpl import render                    # noqa: E402

#: The staged-tree contract: every file ingest copies in and macros reads out.
#: <channel> is deliberately flattened (a store config only runs on the store channel), and
#: the docs "run" id IS the cell name — both invariants documented at their consumers.
TEMPLATES = {
    # per (cell, pair, config) leaf: the run's config snapshot + the curated figures
    'config_json':   'images/{run}/{inv}/{cfg}/config.json',
    'figure_png':    'images/{run}/{inv}/{cfg}/{figure}',
    # per pair: the catalogue's generation parameters
    'pair_params':   'data/{inv}/params.json',
    # run-root what-if outputs: JSON/CSV data into data/, PNGs flat into images/
    'whatif_data':   'data/{fname}',
    'whatif_delta':  'images/{fname}',
    # per-cell data artifacts (the channel rollup CSVs: the per-arm labor rows and the
    # per-channel best/saving summary the labor page's headline quotes) — added for
    # Experiment 8 after a reviewer traced the labor headline to a file the site never
    # staged.  Keyed by cell because the rollup is a cell-scope run-tree artifact.
    'cell_data':     'data/{run}/{fname}',
    # per-leaf data artifacts: the arm-vs-baseline table behind the headline figure's
    # effect sizes and intervals, so a reader can check the claim rather than read it
    # off a picture.  Keyed by leaf because it is a channel-run-scope artifact.
    'leaf_data':     'data/{run}/{inv}/{cfg}/{fname}',
    # catalogue distribution plots (the profiles tree's PNGs, flattened per experiment)
    'catalogue_png': 'images/{catalogue}/{plot}',
}


def path(name: str, exp_dir: str = '', **parts) -> str:
    """Render template `name` with `parts`, joined under `exp_dir` (native separators).
    A missing part raises KeyError by name — same contract as every other tree resolver."""
    rel = render(TEMPLATES[name], **parts)
    segs = rel.split('/')
    return os.path.join(exp_dir, *segs) if exp_dir else os.path.join(*segs)
