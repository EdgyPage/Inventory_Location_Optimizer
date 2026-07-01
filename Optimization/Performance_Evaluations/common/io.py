"""Figure-save + fresh-directory helpers (deduped from the two retired monoliths).

`_save_close` stamps a process-global footer (set per context by the driver) onto every
figure, so all graphs carry the origin simulation folder + inventory provenance without
each graph having to opt in.
"""
import os
import shutil

import matplotlib.pyplot as plt
from matplotlib.legend import Legend

# Provenance footer stamped on every figure.  Set per-context by the driver
# (set_footer); None = no footer.  Process-global is safe: graphs run sequentially
# within a worker, and the pool parallelizes across processes (each with its own copy).
_FOOTER = None


def set_footer(text):
    """Set the provenance footer stamped on subsequently saved figures (None clears it)."""
    global _FOOTER
    _FOOTER = text


def _save_close(fig, path):
    if _FOOTER:
        fig.text(0.99, 0.004, _FOOTER, ha='right', va='bottom',
                 fontsize=6, color='#999999', style='italic')
    # Explicitly include every legend (incl. those placed OUTSIDE the axes on the right,
    # and second legends added via add_artist) so bbox_inches='tight' never clips them.
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
