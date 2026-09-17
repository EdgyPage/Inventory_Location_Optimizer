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

        # ONE CALL, not a fourth partial copy.  This block re-implemented `stale_reasons`
        # inline -- empty store, head mismatch, store integrity -- because `contract.py` had
        # none, which put a fourth copy one level above the three ticket 13 collapsed.  It
        # prints the FIRST reason and stops, as it always has: a nag hook that lists everything
        # is a nag hook people learn to scroll past.
        reasons = contract.stale_reasons()
        if reasons:
            print(f'[schema] {reasons[0]}'
                  + (f' ({len(reasons)} finding(s))' if len(reasons) > 1 else ''))
            return 0

        # NOT part of `stale_reasons`, and deliberately: every reason above is about the STORE
        # (is the committed contract the one the declaration hashes to). This is about the
        # OUTPUT TREE -- whether a source that decides the tree's SHAPE moved since the
        # fingerprint was recorded -- which is answered by running the canaries, not by reading
        # the store.
        changed, _old, _new = preflight.sources_changed()
        if changed:
            head = contract.head()
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
