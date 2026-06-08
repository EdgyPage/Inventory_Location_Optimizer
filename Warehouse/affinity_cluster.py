"""affinity_cluster.py — group SKUs into co-pick clusters via weighted label
propagation on the lift graph.

The affinity_cluster placement strategy stocks the members of a cluster into the
same aisle so that strongly co-picked SKUs are visited in one task (fewer aisle
visits, shorter within-aisle routes on correlated batches).

Clustering is restricted to within each (handling, category) class because aisles
are class-pure — two SKUs in different categories can never share an aisle, so
co-locating them is impossible regardless of lift.

Algorithm: weighted label propagation (LPA).  Each SKU starts as its own label and
repeatedly adopts the label with the greatest total lift among its in-class
partners.  O(edges) per iteration, no external dependency, deterministic given a
seed.  Cheap enough to run once per affinity object at scale.
"""
from __future__ import annotations

import random
from collections import defaultdict


def _class_of(carton) -> tuple:
    shc = carton.storage_handle_config
    return (shc.handling, shc.category)


def _adjacency(affinity, skus: set[int]) -> dict[int, list[tuple[int, float]]]:
    """sku -> [(partner_sku, lift), ...] restricted to partners within `skus`.

    Accepts an AffinityStore (CSR `_matrix`) or a dict AffMatrix {(i, j): lift}.
    """
    adj: dict[int, list[tuple[int, float]]] = defaultdict(list)
    m = getattr(affinity, '_matrix', None)
    if m is not None:                                   # AffinityStore (symmetric CSR)
        sku_to_idx = affinity._sku_to_idx
        idx_to_sku = {i: s for s, i in sku_to_idx.items()}
        indptr, indices, data = m.indptr, m.indices, m.data
        for s in skus:
            i = sku_to_idx.get(s)
            if i is None:
                continue
            for j in range(int(indptr[i]), int(indptr[i + 1])):
                p = idx_to_sku.get(int(indices[j]))
                if p is not None and p != s and p in skus:
                    adj[s].append((p, float(data[j])))
    elif isinstance(affinity, dict):
        for (si, sj), v in affinity.items():
            if si != sj and si in skus and sj in skus:
                adj[si].append((sj, v))
    return adj


def cluster_skus(affinity, cartons, max_iters: int = 20, seed: int = 0) -> dict[int, int]:
    """Return ``{sku: cluster_id}`` with globally-unique cluster ids.

    Weighted label propagation per (handling, category) class.  SKUs with no lift
    partners become singleton clusters.  Deterministic given ``seed``.
    """
    rng = random.Random(seed)
    by_class: dict[tuple, list[int]] = defaultdict(list)
    for c in cartons:
        by_class[_class_of(c)].append(c.sku)

    labels: dict[int, int] = {}
    next_label = 0

    for skus in by_class.values():
        sku_set = set(skus)
        adj = _adjacency(affinity, sku_set)
        local = {s: i for i, s in enumerate(skus)}      # each SKU its own label
        order = list(skus)

        for _ in range(max_iters):
            rng.shuffle(order)
            changed = False
            for s in order:
                nbrs = adj.get(s)
                if not nbrs:
                    continue
                weight: dict[int, float] = defaultdict(float)
                for p, lv in nbrs:
                    weight[local[p]] += lv
                best = max(weight.values())
                # deterministic tie-break; keep current label if it is among the best
                if weight.get(local[s], -1.0) < best:
                    local[s] = min(lbl for lbl, w in weight.items() if w == best)
                    changed = True
            if not changed:
                break

        # remap this class's local labels to globally-unique cluster ids
        remap: dict[int, int] = {}
        for s in skus:
            ll = local[s]
            cid = remap.get(ll)
            if cid is None:
                cid = next_label
                remap[ll] = cid
                next_label += 1
            labels[s] = cid

    return labels


def cluster_size_report(labels: dict[int, int]) -> dict:
    """Summary stats for logging / de-risking: number of clusters and the
    size distribution (max/mean and a coarse histogram)."""
    sizes: dict[int, int] = defaultdict(int)
    for cid in labels.values():
        sizes[cid] += 1
    counts = sorted(sizes.values(), reverse=True)
    n = len(counts)
    total = sum(counts)
    return {
        'n_clusters': n,
        'n_skus': total,
        'max_size': counts[0] if counts else 0,
        'mean_size': (total / n) if n else 0.0,
        'singletons': sum(1 for c in counts if c == 1),
        'top5': counts[:5],
    }
