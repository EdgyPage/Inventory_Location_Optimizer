"""hook_check.py — memory-layer nags for SessionStart, Stop and PreCompact.

ALWAYS exits 0, like context/arch/hook_check.py and Optimization/runschema/hook_check.py.  These
are advisory: they must never block a turn, and must stay silent when optional deps are missing.
(The one hook in this repo that DOES block is context/guards/hook_check.py --pre-write.)

Modes, chosen by argv[1] so there is one file to maintain:

  --session-start  once, before the agent acts. Full integrity report: orphaned store, mirror
                   drift, stale anchors. A session that opens knowing an anchor is stale will not
                   cite it. Highest leverage, and it costs one run per session.
  --stop           every turn end. Cheap parity check ONLY -- no anchor scan, no git subprocess.
                   Three Stop hooks now run per turn and verify_architecture already dominates.
  --pre-compact    fires when the context is about to be discarded. The only moment where "write
                   anything durable NOW" has correct timing: afterwards the material to write it
                   from is gone. Output lands in the compacted context, so it is capped at 6 lines.

Run standalone:  python context/memory/hook_check.py --session-start
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


def _session_start() -> int:
    sync = _load(os.path.join(_HERE, 'sync.py'), 'memory_sync')
    st = sync.status()
    if st['orphaned']:
        print('[memory] the live memory store is MISSING but the mirror holds '
              f'{len(st["mirror_files"])} memor(ies) — this repo has moved. Restore them: '
              'python context/memory/sync.py --restore')
        return 0
    drift = len(st['added']) + len(st['changed']) + len(st['removed'])
    if drift:
        print(f'[memory] mirror is {drift} file(s) behind the live store '
              f'(+{len(st["added"])} ~{len(st["changed"])} -{len(st["removed"])}) — sync: '
              'python context/memory/sync.py --push')

    vm = _load(os.path.join(_HERE, 'verify_memory.py'), 'verify_memory')
    if vm.verify(repo_only=True, quiet=True) != 0:
        print('[memory] memory drift (stale anchors or shape) — run the memory-maintainer agent, '
              'or see: python context/memory/verify_memory.py')
    return 0


def _stop() -> int:
    sync = _load(os.path.join(_HERE, 'sync.py'), 'memory_sync')
    st = sync.status()
    if st['orphaned']:
        print('[memory] live store missing, mirror intact — restore: '
              'python context/memory/sync.py --restore')
        return 0
    drift = len(st['added']) + len(st['changed']) + len(st['removed'])
    if drift:
        print(f'[memory] mirror is {drift} file(s) behind — sync: '
              'python context/memory/sync.py --push')
    return 0


def _pre_compact() -> int:
    sync = _load(os.path.join(_HERE, 'sync.py'), 'memory_sync')
    names = sorted(n[:-3] for n in sync.status()['live_files'] if n != 'MEMORY.md')
    print('[memory] context is about to be compacted. If this session established anything '
          'durable — a decision and what it ruled out, a machine fact, a preference, a trap in '
          'the tooling — write it to memory NOW; after compaction the material is gone. '
          'Conventions and how-to-run go in CLAUDE.md instead, not memory.')
    if names:
        print('[memory] existing: ' + ', '.join(names[:8])
              + (f' (+{len(names) - 8} more)' if len(names) > 8 else '')
              + ' — prefer UPDATING one of these over creating a near-duplicate.')
    return 0


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else '--stop'
    try:
        if mode == '--session-start':
            return _session_start()
        if mode == '--pre-compact':
            return _pre_compact()
        return _stop()
    except SystemExit:          # optional dep missing — stay silent, never nag or block
        return 0
    except Exception:           # never let the hook error out a turn
        return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
