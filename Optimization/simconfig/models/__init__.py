"""models -- the composed closed-form models, one module per question.

Each module builds `Warehouse.kernel.closed_form.Model`s out of the kernel's expression tree and
the existing closed forms beside it (`coverage`, `staffing`, `expected_travel`, `fragmentation`),
which enter through `Call` nodes rather than being re-implemented.  Every equation that models a
simulator or record function is registered with a `Mirror` and held equal to it by
`Tests/unit/test_closed_form.py`.

  levels     where a SKU's equilibrium units come from (the declaration)
  reorders   when a reorder fires, how often, and what lands
  churn      how much picking an inbound decision reaches, how long a ranked rule
             keeps finding good bins, and what any unloading order could move
"""
