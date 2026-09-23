"""score_plan_trace.py -- score the plan-trace probe's sidecars against ticket 03's gate.

Reads every `site_plan_trace` sidecar of a run root (resolved through the run-tree
contract, never joined) and reports, per gain entry and per reduction:

  * Kendall tau between the reduction's order and the exact plan's, per plan (median, p10);
  * top-1: the share of plans whose first trailer matches the exact plan's;
  * first divergence: the median position where the orders first differ;
  * cost: placement calls and wall seconds against the exact plan's, summed;

plus two diagnostics from the exact per-round gains:

  * gain monotonicity: the share of (candidate, consecutive round) pairs whose gain ROSE --
    a lazy plan on stale gains is exact only when gains never rise, so this bounds how far
    staleness can be trusted;
  * the exact winner's margin over the runner-up, relative -- near-ties are where any
    approximation flips the order, and where flipping it costs nothing.

and two readings of what a disagreement COSTS, added 2026-09-22 after the first partial read
(every reduction failed the tau gate, yet a drain only stages as many trailers as it has free
doors and replans the rest the next day, so disagreement deep in the order may cost nothing):

  * set agreement at k = 1, 2, 4: the share of the exact plan's first k trailers the reduction
    also puts in its first k -- the trailers a drain with k free doors would actually stage,
    whatever order it stages them in;
  * regret at the first wrong pick: at the first position the orders differ, the exact
    round's gain for the reduction's choice against the exact winner's, as a share of the
    winner's.  EXACT, not estimated: the orders agree up to that round, so both trailers are
    candidates of the same recorded round.  0 for a plan that never diverges.  It prices the
    first mistake only; the states after it differ and the trace holds no gains for them.

THE GATE (map decision Q9): a reduction qualifies when median tau >= 0.9 AND top-1 >= 0.8
over the pool-family plans (`pool: true`); plans with T <= 2 are counted separately, since
a one- or two-trailer order carries no ordering evidence.

    python .scratch/inbound-throughput/assets/score_plan_trace.py <run_root> [--json OUT]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

GATE_TAU = 0.9
GATE_TOP1 = 0.8


def kendall_tau(a: list, b: list) -> float | None:
    """Kendall tau-a between two orders of the same items; None when fewer than two."""
    if len(a) < 2 or sorted(a) != sorted(b):
        return None
    pos = {x: i for i, x in enumerate(b)}
    n = len(a)
    conc = disc = 0
    for i in range(n):
        for j in range(i + 1, n):
            d = pos[a[i]] - pos[a[j]]
            if d < 0:
                conc += 1
            elif d > 0:
                disc += 1
    return (conc - disc) / (n * (n - 1) / 2)


def first_divergence(a: list, b: list) -> int | None:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return None


SET_K = (1, 2, 4)


def set_agreement(a: list, b: list, k: int) -> float | None:
    """|first k of a  ∩  first k of b| / k, over orders of the same items; None if empty."""
    k = min(k, len(a), len(b))
    if k < 1:
        return None
    return len(set(a[:k]) & set(b[:k])) / k


def first_pick_regret(order: list, exact: list, rounds: list, prefix: int,
                      absolute: bool = False) -> float | None:
    """The first wrong pick's lost gain, as a share of the exact winner's (0 when the orders
    never differ).  Round `r` of the trace picks `exact[prefix + r]`, so at the first
    divergence `d` both `exact[d]` and `order[d]` are candidates of round `d - prefix`."""
    d = first_divergence(order, exact)
    if d is None:
        return 0.0
    r = d - prefix
    if r < 0 or r >= len(rounds):
        return None
    g = {seq: gain for seq, _n, _dfr, gain in rounds[r]['cands']}
    gw, ga = g.get(exact[d]), g.get(order[d])
    if gw is None or ga is None:
        return None
    if absolute:
        # In the gain's own units.  The share form is ill-conditioned wherever gains sit
        # near or below zero -- 81% of gain_forecast's candidates did on the first read.
        return gw - ga
    if gw == 0:
        return None
    return (gw - ga) / abs(gw)


def records(root: str) -> list:
    from Optimization.runschema import reader_for, resolver_for
    rt = resolver_for(root)
    # HEAD contract first: a run written before the artifact was declared has none.
    rd = reader_for(rt, 'site_plan_trace') or rt
    paths = rd.glob('site_plan_trace')
    out = []
    for p in paths:
        cell = os.path.relpath(p, root).replace('\\', '/').split('/')[0]
        with open(p, encoding='utf-8') as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    r = json.loads(ln)
                    r['cell'] = cell
                    out.append(r)
    return out


def _pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return xs[k]


def score(recs: list) -> dict:
    by = defaultdict(lambda: defaultdict(lambda: {'tau': [], 'top1': [], 'div': [],
                                                  'calls': 0, 'wall': 0.0, 'regret': [],
                                                  'regret_abs': [], 'spread': [],
                                                  **{f'set{k}': [] for k in SET_K}}))
    exact_cost = defaultdict(lambda: {'calls': 0, 'wall': 0.0, 'plans': 0, 'small': 0})
    mono = defaultdict(lambda: [0, 0])        # entry -> [rises, pairs]
    margins = defaultdict(list)
    for r in recs:
        if not r.get('pool'):
            continue
        entry = r['entry']
        exact = r['exact']
        prefix = len(r.get('prefix') or [])
        ec = exact_cost[entry]
        if r['T'] - prefix <= 2:
            ec['small'] += 1
            continue
        ec['plans'] += 1
        ec['calls'] += r['calls']['exact']
        ec['wall'] += r['wall_s']['exact']
        variants = {'merge': r['merge'], **{f'top{m}': o for m, o in r['topm'].items()}}
        for name, order in variants.items():
            s = by[entry][name]
            tau = kendall_tau(order[prefix:], exact[prefix:])
            if tau is not None:
                s['tau'].append(tau)
            s['top1'].append(order[prefix] == exact[prefix] if len(exact) > prefix else True)
            d = first_divergence(order, exact)
            s['div'].append(len(exact) if d is None else d)
            for k in SET_K:
                a = set_agreement(order[prefix:], exact[prefix:], k)
                if a is not None:
                    s[f'set{k}'].append(a)
            rg = first_pick_regret(order, exact, r['rounds'], prefix)
            if rg is not None:
                s['regret'].append(rg)
            ra = first_pick_regret(order, exact, r['rounds'], prefix, absolute=True)
            if ra is not None:
                s['regret_abs'].append(ra)
                # the first round's gain spread: what "a lot" means for this plan
                g0 = [c[3] for c in r['rounds'][0]['cands']] if r['rounds'] else []
                if g0:
                    s['spread'].append(max(g0) - min(g0))
            s['calls'] += r['calls'].get(name, 0)
            s['wall'] += r['wall_s'].get(name, 0.0)
        prev = None
        for rnd in r['rounds']:
            g = {seq: gain for seq, _n, _d, gain in rnd['cands']}
            if prev is not None:
                for seq, gv in g.items():
                    if seq in prev:
                        mono[entry][1] += 1
                        if gv > prev[seq] + 1e-9 * max(1.0, abs(prev[seq])):
                            mono[entry][0] += 1
            vals = sorted(g.values(), reverse=True)
            if len(vals) >= 2 and vals[0] != 0:
                margins[entry].append((vals[0] - vals[1]) / abs(vals[0]))
            prev = g
    out = {}
    for entry, variants in by.items():
        ec = exact_cost[entry]
        rows = {}
        for name, s in variants.items():
            med = statistics.median(s['tau']) if s['tau'] else None
            top1 = (sum(s['top1']) / len(s['top1'])) if s['top1'] else None
            rows[name] = {
                'plans': len(s['top1']), 'tau_median': med, 'tau_p10': _pct(s['tau'], 0.10),
                'top1': top1, 'first_divergence_median': (statistics.median(s['div'])
                                                          if s['div'] else None),
                'calls_ratio': (s['calls'] / ec['calls']) if ec['calls'] else None,
                'wall_ratio': (s['wall'] / ec['wall']) if ec['wall'] else None,
                **{f'set{k}_mean': (statistics.fmean(s[f'set{k}']) if s[f'set{k}'] else None)
                   for k in SET_K},
                'regret_median': statistics.median(s['regret']) if s['regret'] else None,
                'regret_p90': _pct(s['regret'], 0.90),
                'regret_abs_median': (statistics.median(s['regret_abs'])
                                      if s['regret_abs'] else None),
                'regret_of_spread_median': (statistics.median(
                    [a / sp for a, sp in zip(s['regret_abs'], s['spread']) if sp > 0])
                    if s['spread'] else None),
                'passes_gate': (med is not None and top1 is not None
                                and med >= GATE_TAU and top1 >= GATE_TOP1),
            }
        rises, pairs = mono[entry]
        out[entry] = {'plans': ec['plans'], 'plans_T_le_2_skipped': ec['small'],
                      'exact_wall_s': ec['wall'], 'variants': rows,
                      'gain_rise_share': (rises / pairs) if pairs else None,
                      'winner_margin_median': (statistics.median(margins[entry])
                                               if margins[entry] else None)}
    return out


def _f(x, fmt='{:.3f}'):
    return '--' if x is None else fmt.format(x)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('run_root')
    ap.add_argument('--json', default=None, metavar='OUT')
    a = ap.parse_args(argv)
    recs = records(a.run_root)
    if not recs:
        print(f'no plan-trace records under {a.run_root}')
        return 1
    res = score(recs)
    print(f'{len(recs)} plan records; gate: median tau >= {GATE_TAU}, top-1 >= {GATE_TOP1}')
    for entry, e in sorted(res.items()):
        print(f'\n{entry}: {e["plans"]} pool-family plans scored '
              f'({e["plans_T_le_2_skipped"]} with T<=2 skipped), exact wall '
              f'{e["exact_wall_s"]:.1f} s; gains rose on {_f(e["gain_rise_share"], "{:.1%}")} '
              f'of round-to-round pairs; median winner margin '
              f'{_f(e["winner_margin_median"], "{:.2%}")}')
        print(f'  {"variant":<8} {"tau med":>8} {"tau p10":>8} {"top-1":>7} {"1st div":>8} '
              f'{"calls":>7} {"wall":>7}  gate')
        for name, v in sorted(e['variants'].items()):
            print(f'  {name:<8} {_f(v["tau_median"]):>8} {_f(v["tau_p10"]):>8} '
                  f'{_f(v["top1"], "{:.0%}"):>7} {_f(v["first_divergence_median"], "{:.1f}"):>8} '
                  f'{_f(v["calls_ratio"], "{:.2f}x"):>7} {_f(v["wall_ratio"], "{:.2f}x"):>7}  '
                  f'{"PASS" if v["passes_gate"] else "fail"}')
        print(f'  {"variant":<8} ' + ' '.join(f'{"set@" + str(k):>7}' for k in SET_K)
              + f' {"regret med":>11} {"regret p90":>11} {"abs med":>10} {"of spread":>10}'
              + '   (what a disagreement costs)')
        for name, v in sorted(e['variants'].items()):
            print(f'  {name:<8} ' + ' '.join(f'{_f(v[f"set{k}_mean"], "{:.0%}"):>7}'
                                              for k in SET_K)
                  + f' {_f(v["regret_median"], "{:.2%}"):>11} {_f(v["regret_p90"], "{:.2%}"):>11}'
                  + f' {_f(v["regret_abs_median"], "{:,.0f}"):>10}'
                  + f' {_f(v["regret_of_spread_median"], "{:.1%}"):>10}')
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(res, f, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
