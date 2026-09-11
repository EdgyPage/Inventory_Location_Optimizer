---
name: inbound-lead-is-not-in-the-coverage-record
description: the era's reorder point stamped lead 0 from the catalogue until 2026-09-10, so every inbound-on era run before department-calibration 37 under-covers fulfillment by the pipeline's ~1.8-day realized lead (the first yard-on run read supply 0.148 with every crew in band); the yard never binds under the derived crew
metadata:
  type: project
---

Measured 2026-09-10 on the first calibrated-era run with the standing yard on
(inbound-optimization ticket 23, `comparison_20260910_173151`, 40 site days, reference pair):
fulfillment failed the SUPPLY clause on every arm (level 0.148 vs a stamped 0.025, trending
up) while every crew of both leaves sat in band and the dock never stood a unit overnight.
The store passed.

**Why:** `Optimization/simconfig/coverage.py` sets `reorder_point = d_s * (lead_days +
safety_days)` with `lead_days` read from the catalogue's `lead_time_mean`, which is 0.0 on
every carton of the reference pair -- correct flag-off, where a reorder lands in the batch that
placed it. The trailer pipeline (lognormal transit, one drain per site day, next-fit loading)
realizes an order-to-shelf lead of ~1.8 days (Little's law: ~48,600 units in transit against
~27,300 ordered/day on fulfillment; the inbound-off reference has 0 in transit). Fulfillment's
line-sized levels cannot absorb it; the store's ~1,785-day implied coverage does not notice.

**Decided 2026-09-10 (department-calibration 36, user):** the lead is a SKU attribute (its
SUPPLIER lead, `lead_time_mean`, batches converted to days at the record) PLUS the trailer's
transit rounded UP to the day grid, `E[ceil(L/D)] = 1 + sum_k (1 - Phi(ln(kD/m)/sigma))` --
1.766 site days at the pilot regime, matching the run to 1%; the continuous mean (1.28) is the
wrong number. The floor is solved AT the lead, so inbound-on stock and warehouse move (a new
era); doors never enter the form (the yard's excess is the campaign's effect); a site-level lead
law on the era was REJECTED. Builds: dept-cal 37 (record) and inbound 27 (chain the supplier
lead before the trailer, which today discards it). Phase 1 of the funnel runs with the yard on.

**BUILT 2026-09-10 (department-calibration 37):** the record now declares AT the lead --
`coverage.transit_day_law` stamps `lead.transit_days` (1.766 at the pilot), every level and
pipeline is priced at `sku_lead_days`, the fill prices the position less the SKU's own lines
still in transit, and the floor is solved there (reference pair: fulfillment 1.2668 -> 1.4994
lines, store 1.2728 -> 1.3078). The audit reports the realized lead by Little's law beside the
stamp and the level it explains. Runs before that commit read under the lead-zero era: see
[[lead-aware-record-is-the-fifth-comparability-break]].

**How to apply:** an inbound-on era run recorded BEFORE 2026-09-10's build is NOT comparable
to the inbound-off reference on any supply or missed-share number (its record stamped lead 0);
one recorded after it carries `coverage.lead` and is judged at the lead. Read the yard first: under the derived receiving crew (22 receivers on
the site day) the yard does not bind at 4 doors -- strict contention 0/40, door utilization
15% / 59% -- so `PHASE2_DOCK_DOORS = 4` and the pilot's lead regime are numbers nothing binds
on (inbound 25 decides the regime). Do not read a receiving-side failure into a supply
failure: check `recv` utilization and `shift_days.standing_dock` before blaming the dock.

Related: [[coverage-in-days-floors-the-store-section]], [[nothing-is-lost-under-the-era]],
[[receiving-is-its-own-crew]], [[config-knob-has-five-seams]], [[no-calibration-simulations]].
