"""test_context_sync.py

Locks the context/ flow specs to the code: every anchor (function@file, artifact
literal, CREATE TABLE, guard test) declared in context/flows/*.yml + artifacts.yml
must exist in the working tree.  Drift fails the ordinary suite; the maintainer
agent (context-maintainer) re-syncs the docs and re-runs the same verifier.

Run:  python -m pytest Tests/test_context_sync.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip('yaml', reason='context verification needs pyyaml (requirements-docs.txt)')

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_context_matches_code():
    r = subprocess.run(
        [sys.executable, os.path.join(_ROOT, 'context', 'verify_context.py'), '--quiet'],
        capture_output=True, text=True, cwd=_ROOT,
    )
    assert r.returncode == 0, 'context/ drift:\n' + r.stdout + r.stderr
