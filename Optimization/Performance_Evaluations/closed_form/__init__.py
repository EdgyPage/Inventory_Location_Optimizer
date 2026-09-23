"""closed_form — pictures and pages of the closed-form models (`Warehouse/kernel/closed_form.py`,
`Optimization/simconfig/models/`).

The kernel module writes an equation once and gets numbers, LaTeX and a dependency graph from the
same tree.  This folder is where those turn into things a reader looks at, through `chartkit`
like every figure in the suite:

  render.model_graph            a model's dependency graph, inputs to outputs, laid out by depth
  render.sweep_chart            one or more outputs against a swept input
  render.predicted_vs_realised  a closed form's predictions against the simulator's measurements,
                                with the measurement's interval -- the verification picture
  render.write_page             a Markdown page (MathJax `$$...$$`) of several models, each
                                optionally with its numbers substituted in

Nothing here computes a model: every value comes from `Model.evaluate` / `Model.sweep`, and
every equation printed is `Equation.latex` of the tree that produced the value.
"""
