# Asset: the futuresight family cannot run coupled (inbound-opt 31)

Excerpt from the probe run that first hit this, 2026-09-12. Absolute paths redacted
(CLAUDE.md section 5). Spec: the throwaway `_probe_gaincost`, three cells, coupled
(`PHASE2_RUN_DEFAULTS`), 3,000 SKUs x 3 batches -- a SHAPE test, which is why it was
cheap enough to find this before the full-depth run.

The two cells before it (`k1_off_fifo`, `k1_off_gmyopic`) completed all four coupled
units each. Every one of `k1_off_fsight_wall`'s four failed, identically:

```
CELL k1_off_fsight_wall  (cell 3/3)  split=None  zoning=off  scheduler=lpt  inbound={'trailer_type': '53', 'dock_doors': 4, 'door_team': 10, 'lead_minutes': 480.0, 'lead_spread': 0.7, 'stand
22:11:01  comparison                [k1_off_fsight_wall/mixed_20260816_131535__mixed_realistic_bell_lt0/coupled/uni_fifo_norsl/uni_fifo_norsl] strategy FAILED: leaf/leaves ['store', 'fulfill
File "<repo>\Inbound\site_space.py", line 110, in compose_site_view
22:11:01  comparison                [k1_off_fsight_wall/mixed_20260816_131535__mixed_realistic_bell_lt0/coupled/opt_fifo_norsl/opt_fifo_norsl] strategy FAILED: leaf/leaves ['store', 'fulfill
File "<repo>\Inbound\site_space.py", line 110, in compose_site_view
22:11:01  comparison                [k1_off_fsight_wall/mixed_20260816_131535__mixed_realistic_bell_lt0/coupled/uni_tmin_norsl/uni_tmin_norsl] strategy FAILED: leaf/leaves ['store', 'fulfill
File "<repo>\Inbound\site_space.py", line 110, in compose_site_view
22:11:02  comparison                [k1_off_fsight_wall/mixed_20260816_131535__mixed_realistic_bell_lt0/coupled/opt_tmin_norsl/opt_tmin_norsl] strategy FAILED: leaf/leaves ['store', 'fulfill
File "<repo>\Inbound\site_space.py", line 110, in compose_site_view
```

The refusal is `Inbound/site_space.py:110`, and it is deliberate -- see the comment
above it for the lift rule, and
`Tests/unit/test_site_space_view.py::test_a_futuresight_window_is_refused_rather_than_zipped`
for the pin. It keys on the window being PRESENT, not on its width, which is why
`fsight_w5` is dead too; that half was proven by calling `compose_site_view` directly
with a w=5 window and a w=all window (both refused), against one leaf (composed) and
two windowless leaves (composed).
