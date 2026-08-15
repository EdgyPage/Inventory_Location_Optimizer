"""hook_check.py — Stop-hook backstop that nudges a DB-shape store sync on drift.

Wired via .claude/settings.json (Stop hook), beside Optimization/runschema/hook_check.py — the
same bargain for the OTHER contract.  It compares the DDL source fingerprint recorded in
``Schema/shapes/INDEX.json`` against the working tree and stats the per-family committed
documents.  It NEVER imports the writers (that pulls ~1350 modules, matplotlib and pandas
included) and ALWAYS exits 0: advisory, never blocking, never slow.

The nag it prints is the whole point.  A DDL edit that ships without ``--sync`` opens the
window `Schema.compat.UncommittedShape` documents: the new shape is unrecoverable at the next
change, and the outgoing one silently orphans the archive.  Catching it at the END OF THE TURN
that edited the DDL costs one line; catching it at the next run's precheck costs a blocked run;
catching it at analysis time costs an unreadable archive.

Run standalone:  python Schema/hook_check.py
"""
from __future__ import annotations

import os
import sys

# Entry-script bootstrap: seed the repo root so the package-absolute imports below resolve when
# this file is executed by path (the hook runs `python Schema/hook_check.py`).
_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    try:
        from Schema import store_index

        for reason in store_index.stale_reasons():
            print(f'[schema-db] {reason}')
    except SystemExit:          # an argparse/exit deep in an import — stay silent, never block
        return 0
    except Exception:           # never let the hook error out a turn
        return 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
