# 02 - cell_scope replaces the never-reset _apply_cell

Type: task
Status: resolved

Make a cell a scoped value: apply for a block, restore on exit by rebinding, copy CONFIG-sourced containers into the payload, drop the union refusal whose reason is gone.

## Answer

Landed as develop 8c156641 with Tests/unit/test_cell_scope.py. The design review caught that an in-place restore would rewrite a payload dict before the pool pickled it; restore rebinds and the payload copies.
