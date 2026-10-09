"""Ambiguous or nonstandard configuration bytes cannot be hashed as valid input."""
from pathlib import Path
import tempfile
import unittest
from agenttime.plan import read_config


class StrictConfigurationTests(unittest.TestCase):
    def test_duplicate_keys_and_nonfinite_numbers_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for text in ('{"agent":"a","agent":"b"}', '{"settings":{"x":1,"x":2}}',
                         '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}', '{"x":1e999}'):
                (root / 'config.json').write_text(text)
                with self.subTest(text=text), self.assertRaises(ValueError):
                    read_config(root, 'config')

    def test_valid_unicode_json_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'config.json').write_text('{"agent":"測定","none":null,"integer":2}')
            self.assertEqual(read_config(root, 'config'), {'agent':'測定','none':None,'integer':2})
