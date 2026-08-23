"""Shared significance painters (not evaluations) — the three figure shapes every
significance eval renders through:

  * `merged_effect_panel` — ONE figure per metric replacing the retired four-file set
    (distribution boxes, p heatmap, effect heatmap, rank bars).  Left: the per-strategy
    distribution on a data-hugging axis in presentation units.  Right: the pairwise
    rank-biserial ladder, dot-with-CI, each row annotated via chartkit.effect_label with
    the Holm-corrected p baked into the stars — the label carries the significance, so
    the separate p-matrix and rank-bar figures are gone on purpose.
  * `effect_heatmap` — assignments × metrics, fill = % improvement (diverging colormap
    centred at 0, positive = better per chartkit.improvement_pct), non-significant
    cells greyed/hatched.
  * `forest_panel` — metrics as rows, % improvement with 95% CI whiskers.

Everything draws through chartkit (reserved geometry, no legend in the data region) and
saves with view='effect'; jitter is deterministic (np.random.default_rng(0)).
"""
import numpy as np
from matplotlib.patches import Rectangle

from Optimization.Performance_Evaluations.common import chartkit
from Optimization.Performance_Evaluations.common.stats_core import _rank_biserial_ci
from Optimization.Performance_Evaluations.common.style import _short


def _rb_ci(a, b):
    """95% CI for the matched-pairs rank-biserial of (a, b), resampling the PAIRS.

    Replaces a closed form that used the signed-rank NULL variance: that expression
    depends only on n, so every row of a panel got an identical whisker regardless of
    what the runs showed — widest, absurdly, where the effect was most decisive.  See
    stats_core._rank_biserial_ci.
    """
    return _rank_biserial_ci(a, b)


def merged_effect_panel(keys, colors, box_values, tests, out_path, *, title,
                        lower_is_better, conv=None, unit_label='', max_ladder=28,
                        paired=None):
    """One metric, one figure: distribution (left) + pairwise effect ladder (right).

    `box_values` are RAW per-key samples (what the boxes show); `conv` maps them into
    presentation units for the left panel only (effects are unitless).  `tests` is a
    stats_core._run_tests document over the same keys.  `paired` is the ALIGNED
    block × key matrix those tests were computed over — the ladder's resampled interval
    needs it, because a bootstrap of a PAIRED statistic has to resample pairs, and
    box_values are not aligned with each other.  Without it the dots still draw and the
    whiskers are simply omitted rather than invented.

    With more pairs than `max_ladder` (a 17-arm family gives 136), the ladder keeps only
    baseline-vs-each — every pairwise number still lives in the tests JSON beside this
    figure.  Returns the saved path, or None when there is nothing to draw.
    """
    vals = []
    for v in box_values:
        a = np.asarray(v, dtype=float)
        a = conv(a) if conv is not None else a
        vals.append(a[np.isfinite(a)])
    if not any(len(v) for v in vals):
        return None

    k = len(keys)
    pairs = [(i, j) for i in range(k) for j in range(i + 1, k)]
    if len(pairs) > max_ladder:
        pairs = [(0, j) for j in range(1, k)]

    panel_h = max(3.8, chartkit.height_for_categories(len(pairs), per=0.34, base=2.4))
    panel_w = max(4.6, chartkit.width_for_categories(k, per=0.5, base=3.4))
    ch = chartkit.make(panels=2, ncols=2, panel_w=panel_w, panel_h=panel_h,
                       legend='none')
    ax, ax2 = ch.axes

    # ── left: distribution, data-hugging y, deterministic strip jitter ──────────
    bp = ax.boxplot(vals, showmeans=True, patch_artist=True,
                    meanprops=dict(marker='D', markerfacecolor='black',
                                   markeredgecolor='black', markersize=4),
                    medianprops=dict(color='black'))
    for patch, c in zip(bp['boxes'], colors):
        patch.set_facecolor(c)
        patch.set_alpha(0.55)
    rng = np.random.default_rng(0)
    for i, v in enumerate(vals, start=1):
        if v.size:
            ax.scatter(rng.normal(i, 0.05, v.size), v, s=6, color=colors[i - 1],
                       alpha=0.35, edgecolors='none', zorder=3)
    ax.set_xticks(range(1, k + 1))
    ax.set_xticklabels([_short(x) for x in keys], rotation=40, ha='right', fontsize=8)
    if unit_label:
        ax.set_ylabel(unit_label)
    if any(len(v) for v in vals):
        chartkit.data_ylim(ax, np.concatenate([v for v in vals if len(v)]))

    # ── right: rank-biserial ladder, Holm-p in each row's label ─────────────────
    R = np.asarray(tests.get('rank_biserial'), dtype=float)
    P = np.asarray(tests.get('p_wilcoxon_holm'), dtype=float)
    MP = np.asarray(tests.get('median_pct'), dtype=float)
    sign = -1.0 if lower_is_better else 1.0
    drawn = 0
    for row, (i, j) in enumerate(pairs):
        y = len(pairs) - 1 - row
        r = sign * R[i, j]
        if not np.isfinite(r):
            continue
        lo, hi = (np.nan, np.nan)
        if paired is not None and getattr(paired, 'ndim', 0) == 2:
            lo, hi = _rb_ci(paired[:, i], paired[:, j])
            if sign < 0 and np.isfinite(lo):
                lo, hi = -hi, -lo            # the ladder plots the oriented statistic
        if np.isfinite(lo):
            ax2.errorbar([r], [y], xerr=[[r - lo], [hi - r]], fmt='none',
                         ecolor='#666666', elinewidth=1.2, capsize=2.5, zorder=2)
        ax2.scatter([r], [y], s=34, color=colors[i], edgecolors='black',
                    linewidths=0.5, zorder=3)
        pct = sign * MP[i, j] if np.isfinite(MP[i, j]) else None
        ax2.text(r, y + 0.24, chartkit.effect_label(P[i, j], r, kind='r', pct=pct),
                 ha='center', va='bottom', fontsize=7, color='#333333')
        drawn += 1
    if not drawn:
        ax2.text(0.5, 0.5, 'insufficient paired data', transform=ax2.transAxes,
                 ha='center', va='center', fontsize=9, color='#8a4a00')
    ax2.axvline(0, color='#888888', lw=1.0, ls='--')
    ax2.set_yticks(range(len(pairs)))
    ax2.set_yticklabels([f'{_short(keys[i])} vs {_short(keys[j])}'
                         for (i, j) in reversed(pairs)], fontsize=7)
    # ±1 is the statistic's support; the extra margin keeps the row annotations of
    # saturated effects (|r| ≈ 1, routine at paired n=75) inside the panel.
    ax2.set_xlim(-1.45, 1.45)
    ax2.set_xticks([-1.0, -0.5, 0.0, 0.5, 1.0])
    ax2.set_ylim(-0.7, len(pairs) - 1 + 0.9)
    ax2.set_xlabel('rank-biserial r  (+ = first named better)', fontsize=8)

    ch.title(title)
    return ch.save(out_path, view='effect')


def effect_heatmap(rows, cols, pct_matrix, effect_matrix, p_matrix, out_path, *,
                   title, cbar_label='% improvement (opt vs uni)'):
    """Assignments × metrics improvement heatmap.

    Fill = % improvement (positive = better, per chartkit.improvement_pct — callers
    orient before passing).  Each significant cell shows the % (1 decimal) with the
    oriented rank-biserial beneath; cells with p >= 0.05 (or no p) are greyed and
    hatched with 'ns'.  Returns the saved path, or None on an empty grid.
    """
    pct = np.asarray(pct_matrix, dtype=float)
    eff = np.asarray(effect_matrix, dtype=float)
    P = np.asarray(p_matrix, dtype=float)
    if pct.size == 0 or not np.any(np.isfinite(pct)):
        return None

    # Drop rows and columns with no measurement anywhere and SAY which: a metric that is
    # structurally undefined for this run (churn with reslotting off, put-away depth on a
    # store-only leaf) otherwise renders as a blank stripe the reader has to diagnose.
    keep_r = [i for i in range(pct.shape[0]) if np.any(np.isfinite(pct[i, :]))]
    keep_c = [j for j in range(pct.shape[1]) if np.any(np.isfinite(pct[:, j]))]
    dropped = [str(cols[j]) for j in range(pct.shape[1]) if j not in keep_c]
    if not keep_r or not keep_c:
        return None
    if len(keep_r) != pct.shape[0] or len(keep_c) != pct.shape[1]:
        pct = pct[np.ix_(keep_r, keep_c)]
        eff = eff[np.ix_(keep_r, keep_c)]
        P = P[np.ix_(keep_r, keep_c)]
        rows = [rows[i] for i in keep_r]
        cols = [cols[j] for j in keep_c]

    vmax = float(np.nanmax(np.abs(pct)))
    vmax = vmax if vmax > 0 else 1.0

    import matplotlib.pyplot as plt
    ch = chartkit.make(
        panels=1, legend='none',
        panel_w=chartkit.width_for_categories(len(cols), per=0.95, base=2.8),
        panel_h=chartkit.height_for_categories(len(rows), per=0.5, base=2.0))
    ax = ch.ax
    ax.grid(False)
    cmap = plt.cm.RdYlGn.copy()
    cmap.set_bad('#f2f2f2')
    im = ax.imshow(np.ma.masked_invalid(pct), cmap=cmap, vmin=-vmax, vmax=vmax,
                   aspect='auto')
    for i in range(len(rows)):
        for j in range(len(cols)):
            if not np.isfinite(pct[i, j]):
                continue
            sig = np.isfinite(P[i, j]) and P[i, j] < 0.05
            if sig:
                sub = f'r={eff[i, j]:+.2f}' if np.isfinite(eff[i, j]) else ''
                ax.text(j, i, f'{pct[i, j]:+.1f}' + (f'\n{sub}' if sub else ''),
                        ha='center', va='center', fontsize=7, color='black')
            else:
                ax.add_patch(Rectangle((j - 0.5, i - 0.5), 1, 1, facecolor='#e8e8e8',
                                       edgecolor='#bbbbbb', hatch='///', zorder=2))
                ax.text(j, i, f'{pct[i, j]:+.1f}\nns', ha='center', va='center',
                        fontsize=7, color='#777777', zorder=3)
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels([str(c) for c in cols], rotation=40, ha='right', fontsize=7)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([_short(str(r)) for r in rows], fontsize=7)
    cb = ch.fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cb.set_label(cbar_label, fontsize=8)
    n_sig = int(np.sum(np.isfinite(P) & (P < 0.05)))
    n_cell = int(np.sum(np.isfinite(pct)))
    note = f'{n_sig} of {n_cell} cells reach significance'
    if dropped:
        note += f' · not measured in this run: {", ".join(dropped)}'
    ch.title(title, note)
    return ch.save(out_path, view='effect')


def forest_panel(metric_rows, out_path, *, title,
                 xlabel='% improvement (opt vs uni)'):
    """Forest plot: one row per metric — % improvement dot, 95% CI whiskers, and an
    effect_label annotation (oriented rank-biserial + significance stars).

    `metric_rows` is a list of dicts with keys name / pct / lo / hi / p / effect
    (pct, lo, hi already improvement-oriented: positive = better).  Returns the saved
    path, or None when no row is drawable.
    """
    rows = [r for r in metric_rows if np.isfinite(r.get('pct', np.nan))]
    if not rows:
        return None
    ch = chartkit.make(
        panels=1, legend='none', panel_w=6.5,
        panel_h=chartkit.height_for_categories(len(rows), per=0.5, base=2.0))
    ax = ch.ax
    xs = [0.0]
    for row, r in enumerate(rows):
        y = len(rows) - 1 - row
        pct, lo, hi = r['pct'], r.get('lo', np.nan), r.get('hi', np.nan)
        if np.isfinite(lo) and np.isfinite(hi):
            ax.errorbar([pct], [y], xerr=[[pct - lo], [hi - pct]], fmt='none',
                        ecolor='#666666', elinewidth=1.2, capsize=3.0, zorder=2)
            xs += [lo, hi]
        ax.scatter([pct], [y], s=38, color='#2b6ca3', edgecolors='black',
                   linewidths=0.5, zorder=3)
        ax.text(pct, y + 0.26,
                chartkit.effect_label(r.get('p'), r.get('effect'), kind='r', pct=pct),
                ha='center', va='bottom', fontsize=7, color='#333333')
        xs.append(pct)
    ax.axvline(0, color='#888888', lw=1.0, ls='--')
    labels = [str(r['name']) for r in reversed(rows)]
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(labels, fontsize=8)
    # chartkit reserves a fixed left margin sized for numeric ticks; category rows
    # need room for their names, so widen this panel's own left edge to fit them.
    need = (0.35 + 0.062 * max(len(l) for l in labels)) / ch.fig.get_figwidth()
    pos = ax.get_position()
    dx = max(0.0, need - pos.x0)
    if dx:
        ax.set_position([pos.x0 + dx, pos.y0, pos.width - dx, pos.height])
    lo, hi = float(np.min(xs)), float(np.max(xs))
    span = (hi - lo) or 1.0
    ax.set_xlim(lo - 0.1 * span, hi + 0.1 * span)
    ax.set_ylim(-0.7, len(rows) - 1 + 0.9)
    tag = chartkit.pct_axis(ax, better='right', axis='x')
    ax.set_xlabel(f'{xlabel} {tag}', fontsize=8)
    ch.title(title)
    return ch.save(out_path, view='effect')
