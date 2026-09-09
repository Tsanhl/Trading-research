import json
from pathlib import Path
import unittest
from trading_hub.kx_context import integrated_context

class KXContextTests(unittest.TestCase):
    def test_shared_vector_and_no_entry_authority(self):
        value=json.loads(Path('tests_web/fixtures/kx-context-vector.json').read_text())
        result=integrated_context(value['bars'])
        self.assertEqual(result, value['expected'])
        self.assertFalse(result['entryAuthority'])
        self.assertFalse(result['sameBarEntryAllowed'])
        self.assertEqual(result['qualification'], 'KX_BLOCKED_9_GATES')
