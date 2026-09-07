# Stamp the line distribution on the SKU

Type: task
Status: open

Graduated from [Choose the coverage floor](15-choose-the-coverage-floor.md), decision 10 (the
user's amendment): every distribution-dependent number is read off a law stamped on the SKU,
never re-derived by a consumer. AFK build, flag-off byte-identical. Skills: `codebase-design`
(the object is a deep module with five readers); the `schema-maintainer` agent owns the DDL
change.

## Question

Land a first-class **line distribution** on every SKU -- the law one line's quantity is drawn
from -- and route every reader through it.

- **The object.** Family + parameters with `mean()`, `cdf(k)`, `quantile(p)`,
  `expected_min(S)` (`E[min(X, S)]`) and `sample(rng)`. The one family today is
  `max(1, Poisson(lambda))`; its sampler stays Knuth's (`Demand.poisson_sample`) so the draws
  are unchanged. Held on `Demand` beside the two scalars, which stay.
- **The inventory file.** Two columns on the `cartons` table, family and params JSON, following
  the `creation_plan` precedent -- a DDL change on the `inventory_db` family, so `--sync` before,
  `--accept` after, a per-vintage `dataset.override` for the pre-stamp shape. `generate_inventory`
  writes them; the loader reads them. A pre-stamp vintage (the reference pair) reconstructs
  Poisson(lambda) from `demand_qty_rate` at load, provenance `assumed`, and the staffing record
  names the reconstruction; regeneration is never forced.
- **The readers.** `Order.py`'s `max(1, poisson_sample(lam))`, `Demand.sample`,
  `expected_travel.accumulate` (lambda at ~275), `staffing.batch_content` (`max(1, lambda)` at
  ~129 -- a drift: the exact mean replaces it, the numeric change to era batch content stated
  in the record) and `coverage.line_units` all become calls on the object. No consumer names a
  family.
- **Provenance.** Every derived number that reads the law records the family it was read from.

Done when: the object exists with tests pinning the Poisson identities (`mean = lambda +
e^-lambda`, `expected_min` by hand); the columns ride the schema pipeline and a pre-stamp file
loads with the reconstruction stamped; every reader above is routed through it and a grep finds
no hand-coded line law outside the object; a flag-off run is byte-identical (a test through the
sampler proves the draws are unchanged); the staffing drift is corrected and stated.
