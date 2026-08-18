"""
calltree_compare.py — diff two calltree-v1 captures; nonzero exit on drift.

What is exact and what is banded:
  COUNTS   per-project-function call counts are deterministic under fixed seeds, so any
           count delta at matched sizes is a code-shape change ("someone added a per-pick
           call") and fails loudly. 'ext'/'<c>' leaves are excluded — thread-pool
           bookkeeping counts vary with OS scheduling.
  WALLS    per-section UNTRACED walls compare within --time-tol (default 20%) — machine
           noise is real; only the untraced pass is ever compared.
  SELF     per-function traced self-times print informationally (never gate) — traced
           time carries tracer overhead and is for attribution, not regression.

Usage:
    python Tests/calltree/calltree_compare.py old.json new.json [--time-tol 0.20]

Exit codes: 0 = no drift; 1 = count drift or section wall outside tolerance;
2 = captures not comparable (different scenario/sizes/seed).
"""
from __future__ import annotations

import argparse
import json
import sys


def _flat(doc: dict, *, skip_ext: bool = True) -> dict[str, dict]:
    out: dict[str, dict] = {}

    def walk(node: dict, path: str) -> None:
        for c in node.get('children', []):
            if skip_ext and c.get('kind') == 'ext':
                continue
            p = f"{path}/{c['name']}"
            out[p] = c
            walk(c, p)

    walk(doc['tree'], '')
    return out


def _comparable(a: dict, b: dict) -> list[str]:
    """Reasons the two captures cannot be compared exactly (empty = comparable)."""
    reasons = []
    for key in ('scenario', 'seed'):
        if a['meta'].get(key) != b['meta'].get(key):
            reasons.append(f"meta.{key}: {a['meta'].get(key)!r} vs {b['meta'].get(key)!r}")
    sa, sb = a['meta'].get('sizes', {}), b['meta'].get('sizes', {})
    for key in sorted(set(sa) | set(sb)):
        if key in ('picks', 'placements', 'reorders'):
            continue                    # outputs, not inputs — count drift reports them
        if sa.get(key) != sb.get(key):
            reasons.append(f'sizes.{key}: {sa.get(key)!r} vs {sb.get(key)!r}')
    return reasons


def compare(a: dict, b: dict, time_tol: float) -> tuple[int, list[str]]:
    lines: list[str] = []
    status = 0

    mismatch = _comparable(a, b)
    if mismatch:
        lines.append('NOT COMPARABLE (exact-count gate skipped):')
        lines.extend(f'  {r}' for r in mismatch)
        return 2, lines

    if a['counts_fingerprint'] == b['counts_fingerprint']:
        lines.append('counts: IDENTICAL (fingerprint match)')
    else:
        fa, fb = _flat(a), _flat(b)
        drifted = [(k, (fa.get(k) or {}).get('calls', 0), (fb.get(k) or {}).get('calls', 0))
                   for k in sorted(set(fa) | set(fb))
                   if (fa.get(k) or {}).get('calls', 0) != (fb.get(k) or {}).get('calls', 0)]
        status = 1
        lines.append(f'counts: {len(drifted)} path(s) DRIFTED '
                     f'(exact under fixed seeds — a code-shape change):')
        for k, ca, cb in drifted[:40]:
            lines.append(f'  {cb - ca:+8d}  {ca:>9,} -> {cb:>9,}  {k}')
        if len(drifted) > 40:
            lines.append(f'  ... and {len(drifted) - 40} more')

    wa = {s['name']: s['wall_s'] for s in a['sections']}
    wb = {s['name']: s['wall_s'] for s in b['sections']}
    lines.append(f'section walls (untraced; tolerance ±{time_tol:.0%}):')
    for name in sorted(set(wa) | set(wb)):
        va, vb = wa.get(name, 0.0), wb.get(name, 0.0)
        base = max(va, 1e-4)
        delta = (vb - va) / base
        flag = ''
        if va >= 0.01 and abs(delta) > time_tol:     # ignore sub-10ms sections: pure noise
            flag = '  <-- OUTSIDE TOLERANCE'
            status = max(status, 1)
        lines.append(f'  {name:10s} {va:9.3f}s -> {vb:9.3f}s  ({delta:+7.1%}){flag}')

    # informational: top traced self-time movers
    fa, fb = _flat(a), _flat(b)
    movers = sorted(((k, (fa.get(k) or {}).get('self_s', 0.0),
                      (fb.get(k) or {}).get('self_s', 0.0))
                     for k in set(fa) | set(fb)),
                    key=lambda t: -abs(t[2] - t[1]))[:10]
    lines.append('top traced self-time movers (informational only):')
    for k, sa_, sb_ in movers:
        lines.append(f'  {sb_ - sa_:+8.3f}s  {k.rsplit("/", 1)[-1]}')
    return status, lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='diff two calltree captures')
    ap.add_argument('old')
    ap.add_argument('new')
    ap.add_argument('--time-tol', type=float, default=0.20)
    args = ap.parse_args(argv)

    with open(args.old, encoding='utf-8') as fh:
        a = json.load(fh)
    with open(args.new, encoding='utf-8') as fh:
        b = json.load(fh)
    status, lines = compare(a, b, args.time_tol)
    print('\n'.join(lines))
    print(f'\nresult: {"OK" if status == 0 else "DRIFT" if status == 1 else "INCOMPARABLE"}')
    return status


if __name__ == '__main__':
    raise SystemExit(main())
