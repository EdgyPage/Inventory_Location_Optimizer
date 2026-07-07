"""Warehouse.generation -- data-generation CLIs (build the inventory/affinity/profile DBs).

Separate from the run-a-sim modules in Warehouse/.  Imports are package-absolute
(``from Warehouse.generation.generate_inventory import ...``); run as scripts they
self-bootstrap the repo root onto sys.path.
"""
