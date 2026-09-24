"""S01 -- read two `_toy_merge` roots' plan traces: yard depth T and the exact plan's wall.

    python .scratch/placement-sortmatch/assets/s01_trace_walls.py <base_root> <cand_root>
"""
import glob
import json
import os
import statistics as st
import sys


def read(root):
    rows = []
    for f in glob.glob(os.path.join(root, '*', '*', '_site', 'plan_trace_*.jsonl')):
        with open(f) as fh:
            for line in fh:
                r = json.loads(line)
                r['_file'] = os.path.relpath(f, root)
                rows.append(r)
    return rows


def main():
    base, cand = (read(r) for r in sys.argv[1:3])
    for name, rows in (('base', base), ('cand', cand)):
        T = [r['T'] for r in rows]
        w = [r["wall_s"]["exact"] for r in rows]
        deep = [r for r in rows if r['T'] > 1]
        print(f'{name}: {len(rows)} plans, T mean {st.mean(T):.2f} max {max(T)}, '
              f'plans with T>1: {len(deep)}, exact-plan wall sum {sum(w):.3f} s '
              f'(T>1 only: {sum(r["wall_s"]["exact"] for r in deep):.3f} s)')
    key = lambda r: (r['_file'], r.get('entry'), r.get('ranking'), r['T'], str(r.get('exact')))
    same = sorted(map(key, base)) == sorted(map(key, cand))
    print('exact orders identical across the two roots:', same)


if __name__ == '__main__':
    main()
