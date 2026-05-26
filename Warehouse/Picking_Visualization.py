from __future__ import annotations

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from Picking_Simulation import Task

_EMPTY   = '#e0e0e0'
_STOCKED = '#aec6cf'
_PICK    = '#ffad60'


def plot_task(task: Task) -> None:
    if not task.path:
        print(f'Task for aisle {task.aisle_id} has no path to plot.')
        return

    aisle = task.path[0].aisle
    task_skus = set(task.items)

    fig, ax = plt.subplots(figsize=(max(6, aisle.bayXPerAisle * 1.6),
                                    max(5, aisle.bayYPerAisle * 1.3)))

    for b in aisle.bins:
        if b.storage is None:
            fc = _EMPTY
        elif b.storage.carton.sku in task_skus:
            fc = _PICK
        else:
            fc = _STOCKED

        ax.add_patch(mpatches.FancyBboxPatch(
            (b.bayX - 0.42, b.bayY - 0.42), 0.84, 0.84,
            boxstyle='round,pad=0.04',
            facecolor=fc, edgecolor='#555', linewidth=0.7, zorder=2,
        ))

        if b.storage is not None and b.storage.carton.sku in task_skus:
            sku = b.storage.carton.sku
            qty = task.items[sku]
            ax.text(b.bayX, b.bayY + 0.13, f'SKU {sku}',
                    ha='center', va='center', fontsize=6.5, fontweight='bold', zorder=3)
            ax.text(b.bayX, b.bayY - 0.17, f'x{qty}',
                    ha='center', va='center', fontsize=6.5, color='#444', zorder=3)

    for i in range(len(task.path) - 1):
        b0, b1 = task.path[i], task.path[i + 1]
        ax.annotate(
            '', xy=(b1.bayX, b1.bayY), xytext=(b0.bayX, b0.bayY),
            arrowprops=dict(arrowstyle='->', color='#cc0000', lw=1.8, shrinkA=14, shrinkB=14),
            zorder=4,
        )

    for i, b in enumerate(task.path):
        ax.text(b.bayX + 0.32, b.bayY + 0.32, str(i + 1),
                fontsize=7, color='#cc0000', fontweight='bold',
                ha='center', va='center', zorder=5)

    s, e = task.path[0], task.path[-1]
    ax.plot(s.bayX, s.bayY, marker='^', color='green',   ms=11, zorder=6)
    ax.plot(e.bayX, e.bayY, marker='s', color='#cc0000', ms=10, zorder=6)

    ax.set_xlim(0.3, aisle.bayXPerAisle + 0.7)
    ax.set_ylim(0.3, aisle.bayYPerAisle + 0.7)
    ax.set_xticks(range(1, aisle.bayXPerAisle + 1))
    ax.set_yticks(range(1, aisle.bayYPerAisle + 1))
    ax.set_xticklabels([f'X={x}' for x in range(1, aisle.bayXPerAisle + 1)])
    ax.set_yticklabels([f'Y={y}' for y in range(1, aisle.bayYPerAisle + 1)])
    ax.set_xlabel('Bay Column (X)', fontsize=10)
    ax.set_ylabel('Bay Row (Y)', fontsize=10)
    ax.set_title(
        f'Aisle {task.aisle_id}  |  {aisle.storage_handling_type}  |  {len(task.items)} picks  |  {len(task.path)}-stop path',
        fontsize=12, fontweight='bold', pad=10,
    )
    ax.set_aspect('equal')

    ax.legend(
        handles=[
            mpatches.Patch(facecolor=_PICK,    edgecolor='#555', label='Pick target'),
            mpatches.Patch(facecolor=_STOCKED, edgecolor='#555', label='Stocked (not picked)'),
            mpatches.Patch(facecolor=_EMPTY,   edgecolor='#555', label='Empty bin'),
            plt.Line2D([0], [0], marker='^', color='w', markerfacecolor='green',   ms=9, label='Path start'),
            plt.Line2D([0], [0], marker='s', color='w', markerfacecolor='#cc0000', ms=8, label='Path end'),
        ],
        loc='upper right', fontsize=8, framealpha=0.9,
    )
    plt.tight_layout()
    plt.show()
