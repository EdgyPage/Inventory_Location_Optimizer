# Fit the reference window to the replenishment cycle

Type: grilling
Status: open

HITL. Surfaced by [Take the reference run](09-take-the-reference-run.md), which ran the
procedure of [Choose the calibration procedure](02-choose-the-calibration-procedure.md) on the
production catalogue and could not produce a measured record: both windows failed all three
utilization bands and the fixed point was cut off at two passes. Skills: `grilling` +
`domain-modeling` (the glossary's *Equilibrium* / *Reference run* entries may need amending).

## Question

The window precondition of [Declare the equilibrium bands](04-declare-the-equilibrium-bands.md)
holds put-away and receiving to the same band as picking, and the band's expectation assumes
reorder flow equals pick flow. On this catalogue that steady state cannot exist inside a 40-day
run: stock coverage is authored as 10 GENERATION-TIME batches, an era day is 3.5-4.7x smaller
than that batch, and the first reorder wave lands at ~31 (fulfillment) / ~42 (store) days.
Inside days 20-39 reorders ran at 30% / 2% of picks. The same lag defeats the DRAINED clause
by a second route: once fulfillment picking was in band (third pass), pickers finished hours
early and the day still closed with standing carry -- 89% of it `unpicked_unstocked` in the
`carryover` ledger, demand on SKUs the wave has not refilled. So "every day drained" is
unreachable on this catalogue at 40 days even with a perfect pick constant. Meanwhile the
picking constant converges, but by a ~0.7 contraction per pass, so 02's cap of two passes
stops it 10-19% short.

Decide the amended procedure. The candidate levers, not exclusive:

1. **Coverage.** Regenerate (or warm-start) the profile with stock coverage authored so the first
   replenishment cycle completes before the window opens -- e.g. coverage of ~1 generation batch,
   or coverage in ERA DAYS. The circularity to settle: coverage in days depends on the calibrated
   demand this run measures. Also: does a re-generated catalogue break comparability with the
   pilot / archive, and does it matter (the per-item charge already broke it)?
2. **Horizon.** Keep the catalogue, lengthen the run past several coverage cycles (e.g. 200 days,
   window 120-199; ~4x the wall and disk per pass, both cheap here: ~20 min / ~4.5 GB per pass at
   40 days on 2 workers).
3. **Precondition scope.** Demote the put and receiving clauses to REPORT for the reference run
   (04 already keeps below-band picking a report on campaign arms) and size those crews from the
   derived flow alone -- accepting that the record then never verifies the put/receiving bands.
4. **Pass cap.** Replace "at most two passes" with "until derived daily demand moves < 5%, at
   most N". The continuation on 09 closed the picking fixed point at the FIFTH pass from the
   analytic seed (steps 13.6/85.6 -> 7.9/8.3 -> 4.3/3.2 %), with pick utilization then dead on
   expectation (0.891/0.890, 0.821/0.829). Seeding the re-take from the converged candidate
   should close it in one or two passes; N=6 is a safe cap from the analytic seed.
5. **Backlog.** A capped day is sticky under roll-over: a backlog built under a wrong seed does
   not clear in the window. Whether each pass should start from a clean day 0 (it does -- passes
   are separate runs) is settled; whether days 0-19 are long enough to clear a seed error is not.
6. **Line-to-unit gap.** Realized released units run ~3-6% above `daily_demand_units` because
   batch content is a line fraction. Inside the band, but it is headroom lost; decide whether the
   derivation should target units through the catalogue's units-per-line rather than lines.

The answer records the amended procedure precisely enough for
[Re-take the reference run](12-re-take-the-reference-run.md) to be AFK, and which of 02's and
04's decisions it supersedes (amend those tickets' answers in place with a dated note).
