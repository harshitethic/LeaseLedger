import tempfile
import unittest
from pathlib import Path
from leaseledger import Store, Worker


class Regression(unittest.TestCase):
    def test_lease_interval_rejects_non_numeric(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "queue.db")
            for v in (None, "30", [], {}, False, True, float("nan"), float("inf")):
                with self.subTest(value=str(v)), self.assertRaises(ValueError):
                    Worker(store, lambda _: None, lease_seconds=v)
            self.assertEqual(Worker(store, lambda _: None, lease_seconds=3.0).lease_seconds, 3.0)


if __name__ == "__main__":
    unittest.main()
