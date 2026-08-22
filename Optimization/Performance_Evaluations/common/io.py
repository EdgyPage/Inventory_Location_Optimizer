"""Figure-save + fresh-directory helpers (deduped from the two retired monoliths).

`_save_close` stamps a process-global footer (set per context by the driver) onto every
figure, so all graphs carry the origin simulation folder + inventory provenance without
each graph having to opt in.  It also VALIDATES every save against the current evaluation's
declared `out_subdir` (set per render by the driver, same pattern as the footer): a figure
landing outside its owner's directory is logged as a warning — once per (eval, dir) — and
the e2e tests assert zero such warnings, so the declarations cannot rot into fiction.
"""
import logging
import os
import shutil

import matplotlib.pyplot as plt
from matplotlib.legend import Legend

# Provenance footer stamped on every figure.  Set per-context by the driver
# (set_footer); None = no footer.  Process-global is safe: graphs run sequentially
# within a worker, and the pool parallelizes across processes (each with its own copy).
_FOOTER = None

# The evaluation whose render is currently executing (driver._run_one sets/clears it —
# the set_footer pattern).  None outside a render: saves made by non-registry callers
# (Diagnostics, notebooks) are nobody's to police.
_CURRENT_EVAL = None

#: (eval_key, dirname) pairs already warned about — warn ONCE per pair, and expose the set so
#: the zero-warning test gate can assert emptiness without parsing log text.
_MAP_WARNINGS: set = set()


def set_footer(text):
    """Set the provenance footer stamped on subsequently saved figures (None clears it)."""
    global _FOOTER
    _FOOTER = text


def set_current_eval(key):
    """Name the evaluation whose render is executing (None clears it)."""
    global _CURRENT_EVAL
    _CURRENT_EVAL = key


def out_dir(ctx, pick=None, fresh=False):
    """The CURRENT evaluation's declared output dir under this context's root, created.

    THE way a render obtains its output directory: the subdir comes from the registration's
    `out_subdir` declaration (via artifact_map.figure_subdir), never a retyped literal — so a
    future directory change is a one-line declaration edit and no render body moves.

    root = ctx.run_dir (config scope) or ctx.out_dir (aggregate scope).  `pick` selects one
    member when the declaration is a tuple (agg.cross_profile) and must name a declared
    member; it is an error for a single-dir declaration.  '' (root-declaring evals, e.g.
    config.series) returns the root itself, uncreated-beyond-existing.  fresh=True routes
    through _fresh_dir — the wipe-own-leaf contract of the single-owner stats suites;
    otherwise os.makedirs(exist_ok=True), which also ends the old split where some renders
    made their dirs and others silently relied on the parent pre-pass.
    """
    if _CURRENT_EVAL is None:
        raise RuntimeError('io.out_dir called outside a render — non-registry callers have '
                           'no out_subdir declaration to derive from')
    from Optimization.Performance_Evaluations.core import artifact_map
    sub = artifact_map.figure_subdir(_CURRENT_EVAL)
    if isinstance(sub, tuple):
        if pick not in sub:
            raise RuntimeError(f'{_CURRENT_EVAL} declares {sub}; pick= must name one '
                               f'(got {pick!r})')
        sub = pick
    elif pick is not None:
        raise RuntimeError(f'{_CURRENT_EVAL} declares a single out_subdir {sub!r}; '
                           f'pick= is only for tuple declarations')
    root = getattr(ctx, 'run_dir', None) or getattr(ctx, 'out_dir', None)
    path = os.path.join(root, *sub.split('/')) if sub else root
    if fresh:
        _fresh_dir(path)
    elif sub:
        os.makedirs(path, exist_ok=True)
    return path


def _save_close(fig, path):
    if _CURRENT_EVAL is not None:
        save_dir = os.path.dirname(os.path.abspath(path))
        mark = (_CURRENT_EVAL, save_dir)
        if mark not in _MAP_WARNINGS:
            from Optimization.Performance_Evaluations.core import artifact_map
            if not artifact_map.save_in_bounds(_CURRENT_EVAL, save_dir):
                _MAP_WARNINGS.add(mark)
                logging.getLogger('analysis').warning(
                    f'[artifact-map] {_CURRENT_EVAL} saved a figure outside its declared '
                    f"out_subdir {artifact_map.figure_subdir(_CURRENT_EVAL)!r}: {path}")
    if _FOOTER:
        # chartkit figures reserve a footer band and publish its centre as fig._footer_y;
        # the stamp goes there, centred, where nothing else is allowed to draw.
        fig.text(0.5 if getattr(fig, '_chartkit', False) else 0.99,
                 getattr(fig, '_footer_y', 0.004), _FOOTER,
                 ha='center' if getattr(fig, '_chartkit', False) else 'right',
                 va='center' if getattr(fig, '_chartkit', False) else 'bottom',
                 fontsize=6, color='#999999', style='italic')
    if getattr(fig, '_chartkit', False):
        # Geometry is fully reserved up front — no tight-bbox rescue, so the canvas is
        # exactly the computed size and the footer band survives verbatim.
        fig.savefig(path, dpi=150)
    else:
        # Legacy path (pre-chartkit figures): include every legend (incl. those placed
        # OUTSIDE the axes on the right, and second legends added via add_artist) so
        # bbox_inches='tight' never clips them.
        extra = [a for parent in [fig, *fig.axes]
                 for a in parent.get_children() if isinstance(a, Legend)]
        fig.savefig(path, dpi=150, bbox_inches='tight',
                    bbox_extra_artists=extra or None)
    plt.close(fig)


def _fresh_dir(path):
    """Remove a stale output directory and recreate it empty, so a re-run can never
    leave mismatched plots (e.g. an old top5_* beside a new top3_by_initial_*) behind."""
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
