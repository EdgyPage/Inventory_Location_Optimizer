"""The four significance-suite plot primitives (verbatim from the retired Stats_Analysis):
notched distribution boxes, the Holm-Wilcoxon p heatmap, the rank-biserial effect heatmap,
and the mean-Friedman-rank bar.  Called per-metric by the config and aggregate suites; not
graphs themselves."""
import os

import numpy as np
import matplotlib.pyplot as plt

from Performance_Evaluations.common.io import _save_close
from Performance_Evaluations.common.style import _short


def _stars(p: float) -> str:
    if p is None or not np.isfinite(p):
        return ''
    return '***' if p < 1e-3 else '**' if p < 1e-2 else '*' if p < 5e-2 else 'ns'


def _plot_dist(box_values, keys, colors, name, tests, out_dir):
    """Notched box plots of the per-block values across strategies (no violins)."""
    fig, ax = plt.subplots(figsize=(max(7, 1.1 * len(keys)), 5))
    bp = ax.boxplot(box_values, notch=True, showmeans=True, patch_artist=True,
                    meanprops=dict(marker='D', markerfacecolor='black',
                                   markeredgecolor='black', markersize=4),
                    medianprops=dict(color='black'))
    for patch, c in zip(bp['boxes'], colors):
        patch.set_facecolor(c); patch.set_alpha(0.55)
    rng = np.random.default_rng(0)
    for i, v in enumerate(box_values, start=1):
        v = np.asarray(v, float); v = v[np.isfinite(v)]
        if v.size:
            ax.scatter(rng.normal(i, 0.05, v.size), v, s=6, color=colors[i - 1],
                       alpha=0.35, edgecolors='none', zorder=3)
    ax.set_xticks(range(1, len(keys) + 1))
    ax.set_xticklabels([_short(k) for k in keys], rotation=40, ha='right', fontsize=8)
    ax.set_ylabel(name)
    ax.grid(axis='y', alpha=0.3)
    fr = tests.get('friedman', {})
    p = fr.get('p')
    sub = (f"Friedman χ²={fr.get('stat'):.1f}, p={p:.2e}"
           if p is not None and np.isfinite(p) else 'Friedman n/a')
    ax.set_title(f'{name} — steady-state distribution by strategy\n{sub}',
                 fontsize=11, fontweight='bold')
    _save_close(fig, os.path.join(out_dir, f'{name}_dist.png'))


def _plot_pmatrix(tests, keys, name, out_dir):
    """N×N Holm-corrected Wilcoxon p heatmap (−log10 scale, starred)."""
    P = np.asarray(tests['p_wilcoxon_holm'], float)
    k = len(keys)
    L = -np.log10(np.clip(P, 1e-12, 1.0))
    np.fill_diagonal(L, np.nan)
    fig, ax = plt.subplots(figsize=(0.8 * k + 3, 0.8 * k + 2))
    im = ax.imshow(L, cmap='viridis', vmin=0, vmax=4)
    for i in range(k):
        for j in range(k):
            if i != j and np.isfinite(P[i, j]):
                ax.text(j, i, _stars(P[i, j]), ha='center', va='center',
                        color='white', fontsize=8)
    ax.set_xticks(range(k)); ax.set_yticks(range(k))
    ax.set_xticklabels([_short(x) for x in keys], rotation=40, ha='right', fontsize=7)
    ax.set_yticklabels([_short(x) for x in keys], fontsize=7)
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label('−log10(Holm p)')
    ax.set_title(f'{name} — pairwise Wilcoxon (Holm)\n* p<.05  ** p<.01  *** p<.001',
                 fontsize=10, fontweight='bold')
    _save_close(fig, os.path.join(out_dir, f'{name}_pmatrix.png'))


def _plot_effect(tests, keys, name, out_dir):
    """N×N signed rank-biserial effect-size heatmap (row vs col)."""
    R = np.asarray(tests['rank_biserial'], float)
    k = len(keys)
    fig, ax = plt.subplots(figsize=(0.8 * k + 3, 0.8 * k + 2))
    im = ax.imshow(R, cmap='RdBu_r', vmin=-1, vmax=1)
    for i in range(k):
        for j in range(k):
            if i != j and np.isfinite(R[i, j]):
                ax.text(j, i, f'{R[i, j]:.2f}', ha='center', va='center',
                        color='black', fontsize=7)
    ax.set_xticks(range(k)); ax.set_yticks(range(k))
    ax.set_xticklabels([_short(x) for x in keys], rotation=40, ha='right', fontsize=7)
    ax.set_yticklabels([_short(x) for x in keys], fontsize=7)
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label('rank-biserial (row − col)')
    ax.set_title(f'{name} — pairwise effect size\n(+ ⇒ row larger than col)',
                 fontsize=10, fontweight='bold')
    _save_close(fig, os.path.join(out_dir, f'{name}_effect.png'))


def _plot_rank(tests, keys, colors, name, out_dir):
    """Mean Friedman rank per strategy (lower = better)."""
    mr = tests.get('mean_rank', {})
    items = [(k, mr.get(k)) for k in keys if mr.get(k) is not None and np.isfinite(mr.get(k))]
    if not items:
        return
    items.sort(key=lambda kv: kv[1])
    cmap = {k: c for k, c in zip(keys, colors)}
    labels = [_short(k) for k, _ in items]
    vals = [v for _, v in items]
    fig, ax = plt.subplots(figsize=(max(7, 1.1 * len(items)), 4.5))
    ax.bar(range(len(labels)), vals, color=[cmap[k] for k, _ in items], alpha=0.8)
    ax.set_ylabel('mean rank (1 = best)')
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=40, ha='right', fontsize=8)
    ax.grid(axis='y', alpha=0.3)
    ax.set_title(f'{name} — mean Friedman rank (lower is better)',
                 fontsize=11, fontweight='bold')
    _save_close(fig, os.path.join(out_dir, f'{name}_rank.png'))
