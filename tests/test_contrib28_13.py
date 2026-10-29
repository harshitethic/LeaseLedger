import tempfile
import unittest
from pathlib import Path
from leaseledger import Store, Worker
from leaseledger.cli import _trusted_handler

class Regression(unittest.TestCase):
    def test_non_json_payloads_are_value_errors(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/"queue.db")
            cyclic=[]; cyclic.append(cyclic)
            for payload in ({"blob":{1}},cyclic,{"amount":float("nan")}):
                with self.assertRaisesRegex(ValueError,"JSON-serializable"): store.enqueue("topic",payload)
            self.assertEqual(store.stats()["queued"],0)
            self.assertTrue(store.enqueue("topic",{"items":[1,None,"ok"]}).created)

if __name__=='__main__': unittest.main()
