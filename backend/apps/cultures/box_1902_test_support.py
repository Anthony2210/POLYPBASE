"""Shared adapters for the real-role Box 1902 regression mixins.

The concrete tests live in test_box_1902_correction_v2_postgres. This module
contains no permission substitutes, approval shortcuts or SQL writer emulation.
"""

from functools import wraps


def runtime_test(test):
    """Run ordinary fixture/model operations as the genuine runtime login.

    Planning and correction helpers explicitly open the independent executor
    login. Fixture setup must be committed before crossing that boundary.
    """
    @wraps(test)
    def run(self, *args, **kwargs):
        with self.role("polypbase"):
            return test(self, *args, **kwargs)
    return run
