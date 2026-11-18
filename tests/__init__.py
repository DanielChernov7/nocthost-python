import os
import sys

# Let `python3 -m unittest` run from a checkout without installing the package.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
