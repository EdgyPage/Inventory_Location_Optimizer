"""hook_check.py — Stop-hook backstop that nudges an architecture resync on drift.

Wired via .claude/settings.json (Stop hook).  Runs the architecture verifier quietly and,
if the spec/graph/catalog have drifted from the code, prints a one-line reminder to run the
architecture-maintainer.  ALWAYS exits 0 — this is advisory and must never block a turn or
fail when optional deps (pyyaml) are absent.

Run standalone:  python context/arch/hook_check.py
"""
from __future__ import annotations

import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    drift = False
    try:
        va = _load(os.path.join(_HERE, 'verify_architecture.py'), 'verify_architecture')
        drift = va.verify(quiet=True) != 0
        vs = _load(os.path.join(_HERE, 'verify_site.py'), 'verify_site')
        drift = drift or bool(vs.verify(fast=True))     # fast: no rebuild, just integrity
    except SystemExit:            # pyyaml missing etc. — stay silent, never nag/block
        return 0
    except Exception:             # never let the hook error out a turn
        return 0
    if drift:
        print('[architecture] spec/graph/site drift detected — run the architecture-maintainer '
              'agent to resync (or: python context/arch/extract.py --write && '
              'python context/arch/extract.py --catalog-merge && '
              'python context/arch/extract.py --write-nodes && '
              'python context/arch/render_html.py --build).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
