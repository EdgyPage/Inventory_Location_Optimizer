"""
calltree_render.py — CLI: render out/*.json captures to the offline HTML viewer + PNGs.

Writes out/render/:
    viewer.html, viewer.js, style.css   copied from static/ (the tracked template)
    data.js                             window.CALLTREE_RUNS = [...] — data as a sibling
                                        script, NOT fetch(): the page works from file://
    sections.png                        stacked section walls across all captures
    top_self.png                        top-N traced self-time functions (newest capture)

Usage:
    python Tests/calltree/calltree_render.py [Tests/calltree/out] [--top 25]

Then open out/render/viewer.html directly in a browser (no server needed).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys

_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from calltree_tracer import SECTIONS   # noqa: E402  (sibling import; conftest for pytest)

_STATIC = os.path.join(_HERE, 'static')


def _load_captures(out_dir: str) -> list[dict]:
    docs = []
    for path in sorted(glob.glob(os.path.join(out_dir, '*.json'))
                       + glob.glob(os.path.join(out_dir, 'archive', '*.json'))):
        if path.endswith('.speedscope.json') or path.endswith('growth.json'):
            continue
        try:
            with open(path, encoding='utf-8') as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if doc.get('schema') != 'calltree-v1':
            continue
        doc['file'] = os.path.basename(path)
        docs.append(doc)
    return docs


def _save(fig, path: str) -> None:
    fig.savefig(path, dpi=150, bbox_inches='tight')   # diagnostic render, outside the chartkit contract
    plt.close(fig)


def _sections_png(docs: list[dict], path: str) -> None:
    labels = [f"{d['meta']['scenario']} s{d['meta']['seed']}\n{d['file']}" for d in docs]
    fig, ax = plt.subplots(figsize=(11, max(2.2, 0.6 * len(docs) + 1.2)))
    colors = plt.cm.tab10.colors
    left = [0.0] * len(docs)
    for si, sec in enumerate(SECTIONS):
        vals = []
        for d in docs:
            w = {s['name']: s['wall_s'] for s in d['sections']}
            vals.append(w.get(sec, 0.0))
        if not any(vals):
            continue
        ax.barh(labels, vals, left=left, color=colors[si % len(colors)], label=sec)
        left = [a + b for a, b in zip(left, vals)]
    ax.set_xlabel('untraced wall seconds')
    ax.set_title('section walls per capture (untraced pass)')
    ax.legend(loc='lower right', fontsize=8)
    ax.invert_yaxis()
    _save(fig, path)


def _top_self_png(doc: dict, path: str, top: int) -> None:
    rows: list[tuple[str, float, int]] = []

    def walk(node: dict) -> None:
        for c in node.get('children', []):
            if c.get('kind') != 'ext':
                rows.append((c['name'], c['self_s'], c['calls']))
            walk(c)

    walk(doc['tree'])
    # aggregate identical names across paths
    agg: dict[str, list] = {}
    for name, self_s, calls in rows:
        a = agg.setdefault(name, [0.0, 0])
        a[0] += self_s
        a[1] += calls
    best = sorted(agg.items(), key=lambda kv: -kv[1][0])[:top]
    if not best:
        return
    names  = [k for k, _ in best][::-1]
    selfs  = [v[0] for _, v in best][::-1]
    counts = [v[1] for _, v in best][::-1]
    fig, ax = plt.subplots(figsize=(11, max(3.0, 0.3 * len(best) + 1.0)))
    bars = ax.barh(names, selfs, color=plt.cm.tab10.colors[0])
    for b, c in zip(bars, counts):
        ax.text(b.get_width(), b.get_y() + b.get_height() / 2,
                f'  {c:,} calls', va='center', fontsize=7)
    ax.set_xlabel('traced self seconds (attribution only — includes tracer overhead)')
    ax.set_title(f"top self-time functions — {doc['meta']['scenario']} "
                 f"s{doc['meta']['seed']} ({doc['file']})")
    _save(fig, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='render calltree captures to HTML + PNG')
    ap.add_argument('out_dir', nargs='?', default=os.path.join(_HERE, 'out'))
    ap.add_argument('--top', type=int, default=25)
    args = ap.parse_args(argv)

    docs = _load_captures(args.out_dir)
    if not docs:
        print(f'no calltree-v1 captures under {args.out_dir}')
        return 2

    render_dir = os.path.join(args.out_dir, 'render')
    os.makedirs(render_dir, exist_ok=True)
    for f in ('viewer.html', 'viewer.js', 'style.css'):
        shutil.copyfile(os.path.join(_STATIC, f), os.path.join(render_dir, f))
    with open(os.path.join(render_dir, 'data.js'), 'w', encoding='utf-8',
              newline='\n') as fh:
        fh.write('window.CALLTREE_RUNS = ')
        json.dump(docs, fh)
        fh.write(';\n')

    _sections_png(docs, os.path.join(render_dir, 'sections.png'))
    _top_self_png(docs[-1], os.path.join(render_dir, 'top_self.png'), args.top)

    rel = os.path.relpath(render_dir, _REPO_ROOT)
    print(f'{len(docs)} capture(s) -> {rel}')
    print(f'open {os.path.join(rel, "viewer.html")} (file:// works)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
