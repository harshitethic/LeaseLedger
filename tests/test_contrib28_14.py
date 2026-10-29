import tempfile
import unittest
from pathlib import Path
from leaseledger import Store, Worker
from leaseledger.cli import _trusted_handler

class Regression(unittest.TestCase):
    def test_invalid_module_paths_return_value_error(self):
        for value in (None,42,".os:getenv","bad..module:handle","not valid:handle","json :dumps","json:dumps:extra"):
            with self.subTest(value=str(value)),self.assertRaises(ValueError): _trusted_handler(value)
        import json
        self.assertIs(_trusted_handler("json:dumps"),json.dumps)

if __name__=='__main__': unittest.main()
