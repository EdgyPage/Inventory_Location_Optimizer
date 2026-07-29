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
        from Optimization.runschema import contract, preflight

        head = contract.head()
        if head is None:
            print('[schema] the run-tree schema store is empty — mint it: '
                  'python -m Optimization.runschema.contract --write')
            return 0

        fresh = contract.build()
        if fresh['schema_id'] != head:
            n = len(contract.diff_shape(contract.load(head) or {}, fresh))
            print(f'[schema] runschema/schema.py now hashes to '
                  f'{contract.short_id(fresh["schema_id"])} but the head is '
                  f'{contract.short_id(head)} ({n} shape diff(s)) — adopt it: '
                  f'python -m Optimization.runschema.contract --write')
            return 0

        problems = contract.verify_store()
        if problems:
            print(f'[schema] run-tree schema store integrity: {problems[0]} '
                  f'({len(problems)} problem(s)) — see '
                  f'python -m Optimization.runschema.contract --check')
            return 0

        changed, _old, _new = preflight.sources_changed()
        if changed:
            print(f'[schema] shape-defining source changed since the fingerprint recorded for '
                  f'run-tree schema {contract.short_id(head)}. The output tree may have moved — '
                  f'validate BEFORE the next run: python -m Optimization.runschema.preflight')
    except SystemExit:          # an argparse/exit deep in an import — stay silent, never nag/block
        return 0
    except Exception:           # never let the hook error out a turn
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
