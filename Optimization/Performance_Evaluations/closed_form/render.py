"""render — the closed-form models drawn and written (see the package docstring)."""
from __future__ import annotations

import os

import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch

from Optimization.Performance_Evaluations.common import chartkit as ck

_INPUT_FC, _EQ_FC, _EDGE = '#eef3f8', '#fdf1e2', '#6b7b8c'
_PRED, _REAL = '#c05a1f', '#1f5f99'


# ── the dependency graph ─────────────────────────────────────────────────────────────────────

def layers(model) -> dict:
    """`{name: depth}`: inputs at 0, each equation one past the deepest thing it reads."""
    depth = {n: 0 for n in model.inputs}
    for n in model._order:
        e = model.equation(n)
        depth[n] = 1 + max((depth[d] for d in e.inputs), default=0)
    return depth


def model_graph(model, path, *, title=None, subtitle=None) -> str:
    """Draw `model` as a left-to-right graph: inputs (square boxes) in the first column, each
    equation in the column after the deepest symbol it reads, an arrow per dependency."""
    depth = layers(model)
    cols: dict = {}
    for n, d in sorted(depth.items(), key=lambda kv: (kv[1], kv[0])):
        cols.setdefault(d, []).append(n)
    ncol, nrow = len(cols), max(len(v) for v in cols.values())
    chart = ck.make(panels=1, panel_w=max(4.8, 2.3 * ncol), panel_h=max(2.4, 0.62 * nrow),
                    legend='none')
    ax = chart.ax
    ax.grid(False)
    ax.set_axis_off()
    pos = {}
    for d, names in cols.items():
        for i, n in enumerate(names):
            pos[n] = (d, (len(names) - 1) / 2.0 - i)       # each column centred on 0
    for e in model.equations:
        x1, y1 = pos[e.name]
        for s in e.inputs:
            x0, y0 = pos[s]
            ax.annotate('', xy=(x1 - 0.33, y1), xytext=(x0 + 0.33, y0),
                        arrowprops=dict(arrowstyle='->', color=_EDGE, lw=0.8,
                                        shrinkA=0, shrinkB=0))
    for n, (x, y) in pos.items():
        is_in = n in model.inputs
        ax.add_patch(FancyBboxPatch((x - 0.32, y - 0.2), 0.64, 0.4,
                                    boxstyle='square,pad=0.02' if is_in else 'round,pad=0.02',
                                    fc=_INPUT_FC if is_in else _EQ_FC, ec=_EDGE, lw=0.8))
        unit = '' if is_in else (model.equation(n).unit or '')
        ax.text(x, y + (0.04 if unit else 0.0), n, ha='center', va='center', fontsize=8)
        if unit:
            ax.text(x, y - 0.12, unit, ha='center', va='center', fontsize=6, color='#666666')
    ax.set_xlim(-0.5, ncol - 0.5)
    ax.set_ylim(-(nrow / 2.0) - 0.2, nrow / 2.0 + 0.2)
    chart.title(title or f'{model.name}: dependency graph',
                subtitle or 'boxes are inputs, rounded boxes equations; arrows read a symbol')
    return chart.save(path)


# ── a sweep ──────────────────────────────────────────────────────────────────────────────────

def sweep_chart(model, name, values, inputs, outputs, path, *, xlabel=None, ylabel=None,
                title=None, subtitle=None, logx=False, scale=None) -> str:
    """`outputs` of `model` against input `name` swept over `values` (other inputs fixed).
    `scale` multiplies every output (100 for a share printed in %)."""
    rows = model.sweep(name, values, inputs, outputs)
    outs = list(outputs)
    chart = ck.make(panels=1, legend='gutter' if len(outs) > 1 else 'none',
                    legend_labels=outs)
    ax = chart.ax
    xs = [r[name] for r in rows]
    for i, o in enumerate(outs):
        ys = [r[o] * (scale or 1.0) for r in rows]
        ax.plot(xs, ys, marker='o', ms=3.5, lw=1.4, label=o, color=f'C{i}')
    if logx:
        ax.set_xscale('log')
    ax.set_xlabel(xlabel or name)
    ax.set_ylabel(ylabel or (outs[0] if len(outs) == 1 else 'value'))
    chart.title(title or f'{model.name}: {", ".join(outs)} over {name}', subtitle)
    if len(outs) > 1:
        chart.legend()
    return chart.save(path)


# ── predicted against realised ───────────────────────────────────────────────────────────────

def predicted_vs_realised(rows, path, *, title, subtitle=None, unit='%',
                          view=None) -> str:
    """One row per quantity: the closed form's prediction (hollow diamond) and the simulator's
    measurement (filled dot, with its interval when `lo`/`hi` are given).  `view` is the
    family-grammar view a registry render saves under (`<view>_*.png`); None outside one.
    `rows`: `[{'label', 'predicted', 'realised', 'lo'?, 'hi'?}, ...]`, top row first."""
    n = len(rows)
    chart = ck.make(panels=1, panel_h=ck.height_for_categories(n), legend='gutter',
                    legend_labels=['closed form', 'simulator (95% interval)'])
    ax = chart.ax
    ys = np.arange(n)[::-1]
    for y, r in zip(ys, rows):
        if r.get('lo') is not None and r.get('hi') is not None:
            ax.plot([r['lo'], r['hi']], [y, y], color=_REAL, lw=2.2, alpha=0.35,
                    solid_capstyle='butt')
        if r.get('realised') is not None:
            ax.plot(r['realised'], y, 'o', color=_REAL, ms=5.5)
        if r.get('predicted') is not None:
            ax.plot(r['predicted'], y, 'D', mfc='none', mec=_PRED, mew=1.4, ms=6.5)
    ax.set_yticks(ys)
    ax.set_yticklabels([r['label'] for r in rows], fontsize=7)
    ax.set_xlabel(unit)
    lim = [v for r in rows for v in (r.get('predicted'), r.get('realised'), r.get('lo'),
                                     r.get('hi')) if v is not None]
    if lim and min(lim) < 0 < max(lim):
        ax.axvline(0.0, color='#999999', lw=0.8)
    chart.title(title, subtitle)
    chart.legend([Line2D([], [], marker='D', ls='none', mfc='none', mec=_PRED, mew=1.4),
                  Line2D([], [], marker='o', ls='none', color=_REAL)],
                 ['closed form', 'simulator (95% interval)'])
    return chart.save(path, view=view)


# ── the page ─────────────────────────────────────────────────────────────────────────────────

def write_page(sections, path, *, title, intro='') -> str:
    """A Markdown page of several models: `sections` is `[(model, result_or_None), ...]` or
    `[(model, result_or_None, heading), ...]`.  Every equation is the model's own rendering."""
    out = [f'# {title}', '']
    if intro:
        out += [intro.strip(), '']
    for sec in sections:
        model, result = sec[0], sec[1]
        heading = sec[2] if len(sec) > 2 else None
        out.append(model.to_markdown(result, title=heading))
        out.append('')
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('\n'.join(out).rstrip() + '\n')
    return path
