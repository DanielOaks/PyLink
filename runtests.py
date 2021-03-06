#!/usr/bin/env python3

import unittest
import glob
import os
import sys

runner = unittest.TextTestRunner(verbosity=2)
fails = []
suites = []

# Yay, import hacks!
sys.path.append(os.path.join(os.getcwd(), 'tests'))

query = sys.argv[1:] or glob.glob('tests/test_*.py')

for testfile in query:
    # Strip the tests/ and .py extension: tests/test_whatever.py => test_whatever
    module = testfile.replace('.py', '').replace('tests/', '')
    module = __import__(module)
    suites.append(unittest.defaultTestLoader.loadTestsFromModule(module))

testsuite = unittest.TestSuite(suites)
runner.run(testsuite)
