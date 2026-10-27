"""Regression tests for trusted handler specification diagnostics."""

import unittest

from leaseledger.cli import _trusted_handler


class HandlerLookupTests(unittest.TestCase):
    def test_missing_handler_raises_validation_error(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            _trusted_handler("json:nonexistent_handler_name")

    def test_noncallable_handler_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not callable"):
            _trusted_handler("sys:version")

    def test_callable_handler_is_accepted(self):
        self.assertTrue(callable(_trusted_handler("json:loads")))


if __name__ == "__main__":
    unittest.main()
