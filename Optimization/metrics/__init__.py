"""Optimization.metrics — pick-cost and per-batch/per-task statistics over sim events.

Sits between the simulation and both consumers (the worker, which records stats, and the analysis
suite, which plots them) — which is why it is here and not under Performance_Evaluations.
"""
