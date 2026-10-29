import tempfile
import unittest
from pathlib import Path
from leaseledger import Store


class Regression(unittest.TestCase):
    def test_invalid_clock_values(self):
        for value in ("100", True, False, None, [], float("nan"), float("inf")):
            with self.subTest(value=str(value)), tempfile.TemporaryDirectory() as d:
                store = Store(Path(d) / "queue.db", clock=lambda: value)
                with self.assertRaises(ValueError):
                    store.enqueue("topic", {})
                self.assertEqual(store.stats()["queued"], 0)


if __name__ == "__main__":
    unittest.main()
