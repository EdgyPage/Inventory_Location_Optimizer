# Declare the demand and derive the crew from the joint first-time confidence

Type: task
Status: open

Graduated 2026-09-08 from
[Fit the store's window to its own steady state](27-fit-the-store-window-to-its-steady-state.md),
decisions 4-8. AFK build. Skills: `codebase-design`. Records ADR-0004 (already written:
`docs/adr/0004-demand-declared-crew-derived-first-time-confidence.md`).

## Question

Reverse the era's declared input and re-derive the picking crew from a closed-form first-time
guarantee. Today `Optimization/simconfig/staffing.py` takes pickers per channel as the declaration,
sizes the batch content from `K x S x rho_pick` through `expected_travel.solve_n`, and prices the
load on SERVED units (demand x fill rate). 27 showed that under the era nothing is lost -- every
missed or cut unit is re-offered next day and lead-0 top-ups land before the re-attempt -- so the
crew must pick all of demand, and that the day law is a declared Gaussian on the line count whose
quantile and partial expectation are exact enough to promise on.

**Build:**

1. **The declaration.** Demand per channel is the era's declared input, stored in the sampler's
   native unit (the fraction of the section's SKUs drawn per day; `settings.py` `STORE_BATCH_MEAN`
   / `FF_BATCH_MEAN` are the flag-off ancestors) so it scales to any catalogue. Its value on the
   reference pair is today's derived content -- 588.65 store / 2,903.19 fulfillment lines a day,
   i.e. the `mean_fraction` `batch_content` produced at the fixed point -- so the warehouse, the
   levels and the script family stay where they are. Two flags replace `--store-pickers` /
   `--ff-pickers`, which are REFUSED under the era exactly as the legacy crew flags are (a typed
   crew is a regime nobody derived). All five seams (memory `config-knob-has-five-seams`).
2. **The joint confidence** `first_time_confidence` (0.95, `declared`) replaces `rho_pick` for
   picking only; `rho_put` / `rho_recv` stay. Equal split: each side at `sqrt(c)`.
   - **Shelf side:** solve `floor_lines` (continuous; `coverage.py` `L_s = ceil(floor_lines x
     E[q_s])`) so the stamped first-pass fill clears `sqrt(c)` (~1.26-1.27 lines on the reference
     catalogue, fill 0.9733 at 1.25 / 0.994 at 1.5). Fill is a pure catalogue closed form while
     every SKU sits on the floor; state what happens when a section is NOT fully floored (the fill
     then depends on `coverage_days` too -- solve the same root, the formula already handles it).
     A user-typed `floor_lines` under the era is either refused or accepted only at or above the
     solved value and stamped `declared` -- pick one and say why.
   - **Crew side:** solve the smallest integer crew `K` per channel such that the expected cut
     share `E[(W - K S)^+] / E[W] <= 1 - sqrt(c)`, with `W` the day's expected work over the
     routing chain at the DECLARED demand, the Gaussian line count's cv from the declaration, and
     the partial expectation `sd x [phi(z) - z(1 - Phi(z))]` (or the 7-node Gauss-Hermite sum
     `expected_travel._nodes` already uses, since `expected_pick(n)` is concave in n). Load is
     DEMANDED units, never served. Stamp the expected cut share, the expected utilization (now
     derived, ~0.72 on the store), and `K`.
3. **The record** (`staffing.inputs` / `derived` / `calibration`): demand `declared`, pickers
   `derived`, `first_time_confidence` `declared`, `floor_lines` `derived` (solved), the day law
   (family, cv) stamped beside the sampled script's per-day cv, and the sampled script's actual
   per-day totals priced (27 decision 5) with the guarantee made against the declared law -- both
   stamped, named apart. Restore, resume and the `sim_result` stamp follow 03's contract; a resume
   that re-derives a different crew raises.
4. **The fixed point.** With demand declared, the levels no longer depend on the crew; check
   whether `era_coverage.fixed_point` still needs to iterate (travel depends on geometry, but only
   the crew reads travel now) and simplify if it collapses to one pass.
5. **Tests:** a sabotage test that pricing on served units fails the cut-share bound; the closed
   form's cut share against a Monte-Carlo of the declared law (seeded, tolerance); the refusal of
   the picker flags under the era; the record round-trip; byte-identical store-only path with the
   era off.

## Done when

- The era launches from a demand declaration and a joint confidence, derives both crews and the
  floor, and stamps them with provenance; the picker flags refuse under the era.
- On the reference pair the derived store crew and expected cut share are reported (expected:
  crew ~32, mean utilization ~0.72, cut share <= 0.0253), and the derived floor lands near 1.27
  lines with the stamped fill >= 0.9747.
- ADR-0004 matches what was built (amend it if the build found a reason to deviate, and say so on
  the map).
