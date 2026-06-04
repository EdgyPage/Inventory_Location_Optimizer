"""
Compatibility shim — this script has been renamed to run_simulation.py.

For simulation: python run_simulation.py [args]
For graphs:     python run_analysis.py   <base_dir>
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_simulation import main  # noqa: F401

if __name__ == '__main__':
    main()
