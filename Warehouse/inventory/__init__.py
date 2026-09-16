"""Warehouse.inventory — The Inventory_Manager placement engine and its four mixins (planning, optimal, reorder, zoning) plus their shared leaf helpers.  Only inventory_reorder and inventory_zoning exist SOLELY for Inventory_Management; inventory_planning and inventory_optimal are also read from Optimization/ (see README.md for the two call sites), so a change to either is wider than the package boundary suggests.

See README.md for what does and does not belong here.
"""
