"""Figure-save + fresh-directory helpers (deduped from the two retired monoliths)."""
import os
import shutil

import matplotlib.pyplot as plt


def _save_close(fig, path):
    fig.savefig(path, dpi=150, bbox_inches='tight')
    plt.close(fig)


def _fresh_dir(path):
    """Remove a stale output directory and recreate it empty, so a re-run can never
    leave mismatched plots (e.g. an old top5_* beside a new top3_by_initial_*) behind."""
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
