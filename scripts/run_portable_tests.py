"""Run the existing tests that need neither Cadence nor private native fixtures."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'src'))

# Explicit selection: full discovery includes historical local integration tests.
TARGETS = (
    'test_converter',
    'test_source_faults',
    'test_raw_graphic_values',
    'test_selftest_inputs',
    'test_process_runtime',
    'test_enclosure_exact.Enclosure',
    'test_enclosure_exact.TestBoardExtents',
)

if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromNames(TARGETS)
    print('LC2A public-package tests: no native Cadence integration.', flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    # A skipped selected test must not silently count as full success.
    sys.exit(0 if result.wasSuccessful() and not result.skipped else 1)
