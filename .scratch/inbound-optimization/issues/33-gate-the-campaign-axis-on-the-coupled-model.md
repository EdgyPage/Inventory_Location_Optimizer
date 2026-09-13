# Gate the campaign axis on what a coupled run can do

Type: task
Status: open
Blocked by: 32

Surfaced 2026-09-12 by [Measure what a gain cell actually costs](31-measure-what-a-gain-cell-costs.md).

Blocked by [Decide the futuresight family's place](32-decide-the-futuresight-familys-place.md)
only because the gate would fail on today's axis -- which is the point of it, and also why it
cannot land until that decision has moved the axis or the composer.

## Question

Close the class of failure 31 found, not just the instance.

`phase2_inbound_axis()` declares ten cells. `Inbound/site_space.py` refuses two of them under
coupling. Both files are honest and both are tested; **nothing checks them against each other**,
so the axis has named two unrunnable cells since site-dock closed and the first thing that would
have noticed is a phase-2 launch paying its freeze and then failing 24 units.

The existing refusals do not help here, and it is worth being precise about why:
`validate_spec` fires before a directory exists and reads the spec's SHAPE (pairs, pin, arm
set); `_inbound_axis` refuses unknown keys, missing suffixes, duplicates and partial entries.
Neither knows what the coupled composer will decline at a drain, because that is a property of
the POLICY the cell names, not of the spec's shape.

So: what is the cheapest check that would have caught this, and where does it live?

Sketch, to be argued with rather than implemented as written -- a test that walks
`phase2_inbound_axis()` (and every registered campaign spec's inbound axis), and for each cell
asserts its named yard/dock policy can survive a two-leaf composition. The pieces are already
there: `compose_site_view` is a pure function over frozen data, and 31 exercised exactly this
in four lines against a hand-built `SpaceView` pair. Questions the build has to answer:

- **Test, or spec-time refusal?** A test fails in CI at zero cost to a launch; a refusal in
  `validate_spec` fails the launch itself, before the freeze. They are not exclusive and the
  cheap one may be enough.
- **What is the general predicate?** "Carries a futuresight window" is the instance. The class
  is "this cell's policy needs a per-leaf structure the composer does not compose". Decide
  whether that can be asked generically or whether the honest version is a small declared list
  the composer and the axis both read.
- **The same gap in the other direction**: `_gain_bundle_for` raises for any restock family
  outside `FAITHFUL_GAIN_FAMILIES`, and phase 2's arm set is phase 1's output, so the same
  shape of failure -- a declared campaign that cannot run -- is reachable from the ARM axis too.
  `run_restock_selection` already enforces the cap and backfills, so that side may be covered;
  check, and say so either way.
