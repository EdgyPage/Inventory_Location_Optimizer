"""hook_check.py — Stop-hook backstop that nudges a run-tree schema resync on drift.

Wired via .claude/settings.json (Stop hook), alongside context/arch/hook_check.py.  Runs ONLY the
cheap stage of the preflight — a source-fingerprint comparison plus a staleness check of the
committed contract.  It NEVER runs a canary simulation and ALWAYS exits 0: this is advisory and
must not block a turn, slow it down, or fail when something upstream is half-edited.

The nag it prints is the whole point.  Discovering that the output tree moved at the START of the
next simulation is cheap; discovering it after a multi-hour run, from broken graphs, is not.

Run standalone:  python Optimization/runschema/hook_check.py
"""
from __future__ import annotations

import os
import sys

# Entry-script bootstrap: seed the repo root so the package-absolute imports below resolve when
# this file is executed by path (the hook runs `python Optimization/runschema/hook_check.py`).
_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    try:
        from Optimization import runschema
        from Optimization.runschema import contract, preflight

        version = runschema.RUN_TREE_VERSION
        committed = contract.load(version)
        if committed is None:
            print(f'[schema] no committed run-tree contract for v{version} — generate it: '
                  f'python -m Optimization.runschema.contract --write')
            return 0

        fresh = contract.build(version)
        if committed.get('tree_fingerprint') != fresh['tree_fingerprint']:
            print(f'[schema] run_tree.v{version}.json is STALE vs runschema/v{version}.py '
                  f'({len(contract.diff_shape(committed, fresh))} shape diff(s)) — regenerate: '
                  f'python -m Optimization.runschema.contract --write')
            return 0

        changed, _old, _new = preflight.sources_changed(version)
        if changed:
            print(f'[schema] shape-defining source changed since the committed run-tree contract '
                  f'v{version}. The output tree may have moved — validate BEFORE the next run: '
                  f'python -m Optimization.runschema.preflight')
    except SystemExit:          # an argparse/exit deep in an import — stay silent, never nag/block
        return 0
    except Exception:           # never let the hook error out a turn
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
