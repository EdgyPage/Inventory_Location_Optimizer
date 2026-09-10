---
name: inbound-lead-is-not-in-the-coverage-record
description: the era's reorder point stamps lead 0 from the catalogue, so every inbound-on era run under-covers fulfillment by the trailer pipeline's ~1.8-day realized lead; the first yard-on era run read supply 0.148 with every crew in band, and the yard never binds under the derived crew
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

**How to apply:** an inbound-on era run is NOT comparable to the inbound-off reference on any
supply or missed-share number until the record declares the lead (department-calibration
ticket 36 decides how). Read the yard first: under the derived receiving crew (22 receivers on
the site day) the yard does not bind at 4 doors -- strict contention 0/40, door utilization
15% / 59% -- so `PHASE2_DOCK_DOORS = 4` and the pilot's lead regime are numbers nothing binds
on (inbound 25 decides the regime). Do not read a receiving-side failure into a supply
failure: check `recv` utilization and `shift_days.standing_dock` before blaming the dock.

Related: [[coverage-in-days-floors-the-store-section]], [[nothing-is-lost-under-the-era]],
[[receiving-is-its-own-crew]], [[config-knob-has-five-seams]], [[no-calibration-simulations]].
