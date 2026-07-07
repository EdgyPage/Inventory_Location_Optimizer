"""Warehouse — the domain engine as a package.

Physical model (aisles/bins/carts), inventory + demand, placement/assignment
functions, and the pick simulations.  Import style is package-absolute
(``from Warehouse.Order import Order``); the repo root must be on sys.path —
entry scripts self-bootstrap, pytest gets it from Tests/conftest.py.

Deliberately side-effect-free: no imports here, so ``import Warehouse`` never
drags the heavy modules in and spawn workers pay no extra import cost.
"""
