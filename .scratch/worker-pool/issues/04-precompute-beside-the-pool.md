# 04 - Sibling-cell batch cache; precompute on the spare cores

Type: task
Status: resolved

ensure_batches copies a sibling cell's same-fingerprint file before computing; the transient precompute pool is sized to cpu_count minus the sim workers.

## Answer

Landed as develop 3ca743ca with Tests/unit/test_batch_sibling_cache.py. Chunk count changes no byte (Tests/e2e/test_batch_precompute.py pins serial == parallel).
