# Inventory Location Optimizer

A warehouse simulation study of **where to put stock**. When restock arrives, the choice of
which slot it goes into changes how far pickers walk, how long a batch takes, how well
co-picked items sit together, and how much the layout churns. This site asks a simple question
and measures the answer:

> **Does *where* you place restock matter — and by how much — versus doing nothing?**

Each experiment simulates a full inventory lifecycle (stock → pick → reorder → restock, over
many batches) under a suite of placement rules — from a do-nothing baseline to strategies that
optimise for travel, item affinity, or co-demand — and compares them on picker effort. A
recurring finding: not every "optimization" wins; some make total picking time *worse*.

## Experiments

Each experiment is self-contained — its own definitions, inventory, strategy catalogue,
results, and glossary — so a later sweep can change the setup without disturbing earlier ones.

- **[Experiment 1](experiments/experiment-1/index.md)** — the first sweep: the
  `mixed_realistic` catalogue (100,000 SKUs) across two replenishment lead-time variants and
  four pick-time calibrations. Start at its **Overview**, which links to the simulation
  lifecycle, inventory distributions, the assignment-function catalogue, the run write-ups, the
  full-suite results, and the glossary.
- **[Future experiment discussion](future-experiments.md)** — levers worth testing in later
  sweeps.

## What's inside an experiment

The **simulation lifecycle** page explains how a run works and defines every term; the
**assignment functions** page catalogues the placement rules compared; the **comparison
write-ups** give the headline findings; **full results** shows every strategy; and the
**glossary** defines the symbols. New readers should start from an experiment's Overview and
follow the links from there.
