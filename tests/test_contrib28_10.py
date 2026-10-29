import tempfile
import unittest
from pathlib import Path
from leaseledger import Store, Worker


class Regression(unittest.TestCase):
    def test_poll_interval_rejects_non_numeric(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "queue.db")
            for v in (True, False, None, "0.5", [], float("nan"), float("inf")):
                with self.subTest(value=str(v)), self.assertRaises(ValueError):
                    Worker(store, lambda _: None, poll_seconds=v)
            self.assertEqual(Worker(store, lambda _: None, poll_seconds=0.01).poll_seconds, 0.01)


if __name__ == "__main__":
    unittest.main()
